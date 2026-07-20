import re

from django.conf import settings
from django.core.management.base import BaseCommand

import requests

from the_gatehouse.models import Profile


def _clean_discord(value):
    """Normalize and validate a discord handle the same way
    PlayerCreateForm.clean_discord() does: lowercase, 2-32 chars, and only
    letters/numbers/underscores/periods. Returns the cleaned value, or None if
    it's invalid (e.g. contains spaces or special characters like '#')."""
    if not value:
        return None
    discord = value.lower().strip()
    if not (2 <= len(discord) <= 32):
        return None
    if not re.match(r'^[a-z0-9_.]+$', discord):
        return None
    return discord


PLAYER_API_URL = "https://rootleague.pliskin.dev/api/player/"
API_HEADERS = (
    {'Authorization': f'Token {settings.RDL_API_TOKEN}'}
    if getattr(settings, 'RDL_API_TOKEN', '') else {}
)


def _player_strings(in_game_name, in_game_id):
    """Return (full_player_string, standard_player_string) for an API player.

    full is the strict concatenation stored in rdl_cannonical_dwd
    ("MrMirz+45"); standard zero-pads the id to 4 digits and matches the
    Profile.dwd field ("MrMirz+0045"). Mirrors create_efforts_from_api().
    """
    number = str(in_game_id)
    full = f'{in_game_name}+{number}'
    standard = f'{in_game_name}+{number.zfill(4)}'
    return full, standard


class Command(BaseCommand):
    help = (
        "Backfill Profile.rdl_cannonical_dwd from the Root League player API, "
        "matching on the standardized dwd string. Also updates the discord "
        "field with the API discord_name for Outcast ('O') profiles when free."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Print what would change without saving.',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=100,
            help='Players fetched per API page (default: 100).',
        )
        parser.add_argument(
            '--max-pages',
            type=int,
            default=0,
            help='Stop after this many API pages (0 = all pages, the default). '
                 'Useful for testing on a single page.',
        )
        parser.add_argument(
            '--show-discordupdate',
            action='store_true',
            help="List each Outcast profile whose discord was updated (old -> new).",
        )

    def _fetch_players(self, page_size, max_pages=0):
        """Yield every player dict from the paginated player API, stopping
        after `max_pages` pages when that is nonzero."""
        url = f'{PLAYER_API_URL}?limit={page_size}&offset=0&format=json'
        pages = 0
        while url:
            response = requests.get(url, headers=API_HEADERS, timeout=30)
            response.raise_for_status()
            data = response.json()
            for player in data.get('results', []):
                yield player
            pages += 1
            if max_pages and pages >= max_pages:
                break
            url = data.get('next')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        page_size = options['limit']
        max_pages = options['max_pages']
        show_discord_updates = options['show_discordupdate']

        canonical_updated = 0
        dwd_standardized = 0
        discord_updated = 0
        discord_invalid = 0
        display_name_updated = 0
        unmatched = 0
        skipped = 0
        scanned = 0
        # (api discord_name, profile that wanted it, profile already using it)
        discord_conflicts = []
        # (profile, old_discord, new_discord) for --show-discordupdate
        discord_updates = []

        for api_player in self._fetch_players(page_size, max_pages):
            scanned += 1
            in_game_name = api_player.get('in_game_name')
            in_game_id = api_player.get('in_game_id')
            discord_name = api_player.get('discord_name')
            # Skip entries with no usable name/id — a blank in_game_name would
            # produce a garbage match string like "+4412".
            if not in_game_name or not str(in_game_name).strip() or in_game_id is None:
                skipped += 1
                continue

            full_string, standard_string = _player_strings(in_game_name, in_game_id)

            # Match the profile by its dwd string. Prefer the already-standardized
            # form, but fall back to the raw (non-zero-padded) form since ~10% of
            # profiles still store dwd as "name+953" rather than "name+0953".
            # Mirrors the lookup order in create_efforts_from_api().
            profile = (Profile.objects.filter(dwd__iexact=standard_string).first()
                       or Profile.objects.filter(dwd__iexact=full_string).first())
            if not profile:
                unmatched += 1
                continue

            changed_fields = []

            # Standardize a raw dwd ("name+953") to the zero-padded form
            # ("name+0953"). Unique, so only write when free.
            if profile.dwd != standard_string:
                taken = (Profile.objects
                         .filter(dwd__iexact=standard_string)
                         .exclude(pk=profile.pk).exists())
                if taken:
                    self.stdout.write(self.style.WARNING(
                        f'  dwd "{standard_string}" already owned by another '
                        f'profile; skipping for {profile} (pk={profile.pk})'
                    ))
                else:
                    profile.dwd = standard_string
                    changed_fields.append('dwd')
                    dwd_standardized += 1

            # Backfill the canonical value. It's unique, so only write when no
            # other profile already owns this exact string.
            if profile.rdl_cannonical_dwd != full_string:
                taken = (Profile.objects
                         .filter(rdl_cannonical_dwd__exact=full_string)
                         .exclude(pk=profile.pk).exists())
                if taken:
                    self.stdout.write(self.style.WARNING(
                        f'  canonical "{full_string}" already owned by another '
                        f'profile; skipping for {profile} (pk={profile.pk})'
                    ))
                else:
                    profile.rdl_cannonical_dwd = full_string
                    changed_fields.append('rdl_cannonical_dwd')
                    canonical_updated += 1

            # For Outcast profiles, adopt the API discord_name. Validate it the
            # same way registration does (lowercase, letters/numbers/_/. only) and
            # skip the update when it fails — a raw handle like "Chaotic Noodle"
            # or "Lusk#7562" must not be written to the discord field. Then skip
            # if another profile already uses the cleaned value (a conflict).
            if profile.group == Profile.GroupChoices.OUTCAST and discord_name:
                cleaned_discord = _clean_discord(discord_name)
                if not cleaned_discord:
                    discord_invalid += 1
                elif profile.discord != cleaned_discord:
                    other = (Profile.objects
                             .filter(discord__iexact=cleaned_discord)
                             .exclude(pk=profile.pk).first())
                    if other:
                        discord_conflicts.append((cleaned_discord, profile, other))
                    else:
                        discord_updates.append((profile, profile.discord, cleaned_discord))
                        profile.discord = cleaned_discord
                        changed_fields.append('discord')
                        discord_updated += 1

            # For Outcast profiles, set display_name to the API in_game_name.
            if (profile.group == Profile.GroupChoices.OUTCAST
                    and profile.display_name != in_game_name):
                profile.display_name = in_game_name
                changed_fields.append('display_name')
                display_name_updated += 1

            if changed_fields and not dry_run:
                profile.save(update_fields=changed_fields)
            if changed_fields and dry_run:
                changes = ', '.join(f'{f}="{getattr(profile, f)}"' for f in changed_fields)
                self.stdout.write(
                    f'  [dry-run] {profile} (pk={profile.pk}): {changes}'
                )

        prefix = '[dry-run] ' if dry_run else ''
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'{prefix}Scanned {scanned} API players. '
            f'canonical updated: {canonical_updated}, '
            f'dwd standardized: {dwd_standardized}, '
            f'discord updated: {discord_updated}, '
            f'discord invalid (skipped): {discord_invalid}, '
            f'display_name updated: {display_name_updated}, '
            f'unmatched (no dwd profile): {unmatched}, '
            f'skipped (blank name/id): {skipped}.'
        ))

        if show_discord_updates and discord_updates:
            self.stdout.write('')
            self.stdout.write(self.style.SUCCESS(
                f'{prefix}{len(discord_updates)} discord updates (Outcast profiles):'
            ))
            for profile, old_discord, new_discord in discord_updates:
                self.stdout.write(
                    f'  {profile} (pk={profile.pk}): '
                    f'"{old_discord}" -> "{new_discord}"'
                )

        if discord_conflicts:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING(
                f'{len(discord_conflicts)} discord conflicts '
                f'(discord_name already used by another profile):'
            ))
            for discord_name, wanted, owner in discord_conflicts:
                self.stdout.write(
                    f'  "{discord_name}" wanted by {wanted} (pk={wanted.pk}) '
                    f'but already used by {owner} (pk={owner.pk})'
                )
