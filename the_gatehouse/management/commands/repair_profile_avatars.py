from django.core.management.base import BaseCommand

from allauth.socialaccount.models import SocialAccount

from the_gatehouse.models import Profile, DEFAULT_PROFILE_IMAGE
from the_gatehouse.services.discord_oauth import update_discord_avatar


def _is_missing(profile):
    """True when the profile points at an image file that isn't in storage.

    Uses the storage API rather than os.path.exists so this keeps working if the
    media backend ever moves off the local filesystem (FieldFile.path raises on
    remote backends).
    """
    name = profile.image.name
    if not name or name == DEFAULT_PROFILE_IMAGE:
        return False
    try:
        return not profile.image.storage.exists(name)
    except (NotImplementedError, ValueError, OSError):
        return False


class Command(BaseCommand):
    help = (
        "Find profiles whose image points at a file that no longer exists and "
        "repair them: re-download the Discord avatar where possible, otherwise "
        "reset to the default image so the UI stops rendering a broken link."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report what would change without writing anything.',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=0,
            help='Repair at most this many profiles (0 = no limit, the default).',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        limit = options['limit']
        prefix = '[dry-run] ' if dry_run else ''

        scanned = 0
        missing = 0
        repaired = 0
        reset = 0
        errored = 0

        candidates = (
            Profile.objects
            .exclude(image=DEFAULT_PROFILE_IMAGE)
            .exclude(image='')
            .select_related('user')
            .order_by('pk')
        )

        for profile in candidates.iterator():
            scanned += 1
            if not _is_missing(profile):
                continue

            missing += 1
            broken = profile.image.name

            user = profile.user
            has_discord = bool(
                user and SocialAccount.objects
                .filter(user=user, provider='discord').exists()
            )

            if dry_run:
                action = 're-download' if has_discord else 'reset to default'
                self.stdout.write(f'{prefix}{profile} ({broken}) -> {action}')
                if has_discord:
                    repaired += 1
                else:
                    reset += 1
            else:
                result = None
                if has_discord:
                    try:
                        result = update_discord_avatar(user, force=True)
                    except Exception as exc:
                        errored += 1
                        self.stdout.write(self.style.WARNING(
                            f'{profile}: avatar re-download failed ({exc})'
                        ))

                if result:
                    repaired += 1
                else:
                    # Queryset update, not profile.save(): the file is already gone,
                    # and this skips the image-deletion/resize work in Profile.save().
                    Profile.objects.filter(pk=profile.pk).update(
                        image=DEFAULT_PROFILE_IMAGE
                    )
                    reset += 1

            if limit and (repaired + reset) >= limit:
                self.stdout.write(f'{prefix}Reached limit of {limit}.')
                break

        self.stdout.write(self.style.SUCCESS(
            f'{prefix}scanned {scanned} / missing {missing} / '
            f'repaired {repaired} / reset {reset} / errored {errored}'
        ))
