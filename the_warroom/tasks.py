import logging
import re
import requests
import time

from celery import shared_task
from dateutil import parser
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from the_gatehouse.models import Profile
from the_gatehouse.utils import format_bulleted_list
from the_gatehouse.tasks import send_rich_discord_message_task, send_discord_message_task

from .models import Game, Tournament, Stage, Round, CompetitionStatus, EloSystem, EloRating, EloParticipant
from .services.root_league_api import create_game_from_api, create_efforts_from_api, update_game_from_api
from .services.winrate_service import calculate_and_cache_winrate
from .services.draft_service import refresh_game_draft_options

logger = logging.getLogger(__name__)


@shared_task
def update_cached_winrates(objects_to_update):
    """
    Recalculate cached winrates for a list of (app_label, model_name, pk) tuples.
    Called asynchronously from signals after Effort/Game saves.
    """
    from django.apps import apps
    for app_label, model_name, pk in objects_to_update:
        try:
            model = apps.get_model(app_label, model_name)
            obj = model.objects.get(pk=pk)
            calculate_and_cache_winrate(obj)
        except Exception:
            pass


@shared_task
def update_tournament_counts(tournament_ids):
    """
    Refresh the denormalized game/player counts for a list of Tournament ids.
    Called asynchronously from signals after Game/Effort changes.
    """
    for pk in tournament_ids:
        try:
            Tournament.objects.get(pk=pk).refresh_cached_counts()
        except Exception:
            pass


@shared_task
def update_game_player_count(game_id):
    """
    Refresh a Game's denormalized cached_player_count from its efforts.
    Called asynchronously from signals after Effort changes.
    """
    game = Game.objects.filter(pk=game_id).first()
    if game:
        game.refresh_cached_player_count()


@shared_task
def update_game_draft_options(game_id):
    """Refresh the cached per-seat draft pools for a game.

    Logs failures rather than swallowing them like the neighbouring tasks — a derivation
    bug here would otherwise leave silently wrong offer-rate denominators.
    """
    game = Game.objects.filter(pk=game_id).first()
    if game:
        try:
            refresh_game_draft_options(game)
        except Exception:
            logger.exception('draft options refresh failed for game %s', game_id)


@shared_task
def recompute_dirty_local_elo():
    """Replay every dirty LOCAL EloSystem from its recompute_from watermark, then clear it.

    Scheduled (~every 30 min via django_celery_beat, configured in admin). Mark-dirty
    signals set recompute_from; this task does all the heavy replay.
    """
    from .services.elo_service import recompute_system_from
    systems = EloSystem.objects.filter(
        calculation_type=EloSystem.CalculationType.LOCAL,
        recompute_from__isnull=False,
    )
    processed = 0
    for system in systems:
        cutoff = system.recompute_from
        try:
            recompute_system_from(system, cutoff)
            # Clear ONLY if no newer (earlier) mark arrived mid-run — otherwise leave it so
            # the next run picks up the earlier cutoff. Safe: no mark is ever lost.
            EloSystem.objects.filter(pk=system.pk, recompute_from=cutoff).update(recompute_from=None)
            processed += 1
        except Exception:
            pass  # leave recompute_from set so the next run retries this system
    # Cheap housekeeping: drop rating rows orphaned by games deleted outside a replay window.
    EloRating.objects.filter(game__isnull=True).delete()
    return f'Recomputed {processed} elo system(s)'


@shared_task
def update_competition_statuses():
    """Update statuses for tournaments, stages, and rounds based on dates. Cascades completion to children."""
    now = timezone.now().date()
    updated = 0

    # --- Tournaments ---
    # Pending → Active
    updated += Tournament.objects.filter(
        is_active=True,
        status=CompetitionStatus.PENDING,
    ).filter(
        Q(start_date__isnull=True) | Q(start_date__lte=now)
    ).filter(
        Q(end_date__isnull=True) | Q(end_date__gt=now)
    ).update(status=CompetitionStatus.ACTIVE)

    # Completed by end_date (cascade to children)
    completed_tournaments = Tournament.objects.filter(
        is_active=True,
        status__in=[CompetitionStatus.PENDING, CompetitionStatus.ACTIVE],
        end_date__lt=now,
    )
    for t in completed_tournaments:
        t.status = CompetitionStatus.COMPLETED
        t.save(update_fields=['status'])
        t.stages.exclude(status=CompetitionStatus.COMPLETED).update(status=CompetitionStatus.COMPLETED)
        Round.objects.filter(stage__tournament=t).exclude(
            status=CompetitionStatus.COMPLETED
        ).update(status=CompetitionStatus.COMPLETED)
        updated += 1

    # --- Stages ---
    # Pending → Active
    updated += Stage.objects.filter(
        is_active=True,
        status=CompetitionStatus.PENDING,
    ).filter(
        Q(start_date__isnull=True) | Q(start_date__lte=now)
    ).filter(
        Q(end_date__isnull=True) | Q(end_date__gt=now)
    ).update(status=CompetitionStatus.ACTIVE)

    # Completed by end_date (cascade to child rounds)
    completed_stages = Stage.objects.filter(
        is_active=True,
        status__in=[CompetitionStatus.PENDING, CompetitionStatus.ACTIVE],
        end_date__lt=now,
    )
    for s in completed_stages:
        s.status = CompetitionStatus.COMPLETED
        s.save(update_fields=['status'])
        s.rounds.exclude(status=CompetitionStatus.COMPLETED).update(status=CompetitionStatus.COMPLETED)
        updated += 1

    # --- Rounds ---
    # Pending → Active
    updated += Round.objects.filter(
        is_active=True,
        status=CompetitionStatus.PENDING,
    ).filter(
        Q(start_date__isnull=True) | Q(start_date__lte=now)
    ).filter(
        Q(end_date__isnull=True) | Q(end_date__gt=now)
    ).update(status=CompetitionStatus.ACTIVE)

    # Completed by end_date
    updated += Round.objects.filter(
        is_active=True,
        status__in=[CompetitionStatus.PENDING, CompetitionStatus.ACTIVE],
        end_date__lt=now,
    ).update(status=CompetitionStatus.COMPLETED)

    return f"Updated {updated} competition(s)"


# Import League Games from Pliskin.dev REST API

BASE_URL = "https://rootleague.pliskin.dev/api/match/"
API_HEADERS = {'Authorization': f'Token {settings.RDL_API_TOKEN}'} if getattr(settings, 'RDL_API_TOKEN', '') else {}

# Imports all games from the last 1 day
@shared_task
def import_league_games(limit=25, tournament_name="", days_back=1, date_from=None, date_to=None):
    """
    Import games from the Root League API.
    
    Args:
        limit: Number of games to fetch per request
        tournament_name: String included in the tournament's name
        days_back: Number of days back to import (default 1, ignored if date_from is provided)
        date_from: Optional - specific start date (datetime or ISO string)
        date_to: Optional - specific end date (datetime or ISO string)
    """
    
    imported_count = 0
    skipped_count = 0
    error_count = 0

    imported_list = []
    skipped_list = []
    error_list = []
    
    offset = 0
    has_more = True

    # Determine date range
    if date_from:
        # If date_from is a string, parse it
        if isinstance(date_from, str):
            start_date = parser.parse(date_from)
        else:
            start_date = date_from
    else:
        # Use days_back as default
        start_date = timezone.now() - timedelta(days=days_back)
    
    # Parse date_to if provided
    end_date = None
    if date_to:
        if isinstance(date_to, str):
            end_date = parser.parse(date_to)
        else:
            end_date = date_to
    
    while has_more:
        # Fetch data from API
        params = {
            'tournament__name': tournament_name,
            'limit': limit,
            'offset': offset,
            'date_closed__gte': start_date.isoformat()
        }
        
        # Only add date_closed__lte if end_date is provided
        if end_date:
            params['date_closed__lte'] = end_date.isoformat()
        
        try:
            response = requests.get(BASE_URL, params=params, headers=API_HEADERS, timeout=30)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as e:
            print(f"Error fetching API data: {e}")
            error_count += 1
            break

        results = data.get('results', [])

        if not results:
            has_more = False
            break

        for match_data in results:
            try:
                # Check if game already exists by league_id
                league_id = str(match_data['id'])
                if Game.objects.filter(league_id=league_id).exists():
                    # print(f"Game {league_id} already exists, skipping")
                    skipped_count += 1
                    skipped_list.append(league_id)
                    continue

                # Atomic transaction - if anything fails, nothing gets saved to prevent incomplete game submission
                with transaction.atomic():
                    # Create the Game
                    game = create_game_from_api(match_data)
                    
                    # Create the Efforts (participants)
                    create_efforts_from_api(game, match_data['participants'])
                
                imported_count += 1
                imported_list.append(league_id)
                print(f"Successfully imported game {league_id}")
                
            except Exception as e:
                print(f"Error importing game {str(match_data.get('id'))}: {e}")
                error_count += 1
                error_list.append(league_id)
                continue
        
        # Check if there are more results
        has_more = data.get('next') is not None
        # Add delay before next API call
        if has_more:
            time.sleep(0.5)  # 500ms delay between API requests
        offset += limit
    
    summary = f"Import complete: {imported_count} imported, {skipped_count} skipped, {error_count} errors"
    message = f"Import complete: {imported_count} imported and {error_count} errors"

    # Create import message
    fields = []
    if imported_list:
        fields.append({
            'name': 'Imported',
            'value': format_bulleted_list(imported_list),
        })
    # if skipped_list:
    #     fields.append({
    #         'name': 'Skipped',
    #         'value': format_bulleted_list(skipped_list),
    #     })
    if error_list:
        if error_list:
            error_field = {
                'name': 'Errors',
                'value': format_bulleted_list(error_list),
            }
            fields.append(error_field)

            # Send error message
            send_rich_discord_message_task.delay(
                message,
                author_name='RDB Admin',
                category='report',
                title='Import Errors',
                fields=[error_field]
            )

    return summary


# Checks all the games modified in the last 2 days and updates them
@shared_task
def update_league_games(limit=50, days_back=2, days_cutoff=1, date_from=None, date_to=None):
    """
    Check for games that were modified after initial submission and update them.
    
    Args:
        limit: Number of games to fetch per request
        days_back: Number of days back to update (default 2, ignored if date_from is provided)
        days_cutoff: How many days from now to stop checking (default 1 = yesterday)
                     Example: days_back=2, days_cutoff=1 checks games from 2-1 days ago
        date_from: Optional - specific start date (datetime or ISO string)
        date_to: Optional - specific end date (datetime or ISO string)
    """
    
    updated_count = 0
    error_count = 0
    skipped_count = 0
    updated_list = []
    error_list = []
    
    offset = 0
    has_more = True
    
    # Determine date range
    if date_from:
        # If date_from is a string, parse it
        if isinstance(date_from, str):
            start_date = parser.parse(date_from)
        else:
            start_date = date_from
    else:
        # Use days_back as default
        start_date = timezone.now() - timedelta(days=days_back)
    
    # Parse date_to if provided
    end_date = None
    if date_to:
        if isinstance(date_to, str):
            end_date = parser.parse(date_to)
        else:
            end_date = date_to
    else:
        end_date = timezone.now() - timedelta(days=days_cutoff)

    while has_more:
        # Fetch data from API
        params = {
            'limit': limit,
            'offset': offset,
            'date_modified__gte': start_date.isoformat()
        }
        
        # Only add date_modified__lte if end_date is provided
        if end_date:
            params['date_modified__lte'] = end_date.isoformat()
        
        try:
            response = requests.get(BASE_URL, params=params, headers=API_HEADERS, timeout=30)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as e:
            print(f"Error fetching API data: {e}")
            error_count += 1
            break

        results = data.get('results', [])

        if not results:
            has_more = False
            break

        for match_data in results:
            try:

                # Parse dates
                date_closed = parser.parse(match_data.get('date_closed'))
                date_modified = parser.parse(match_data.get('date_modified'))
                
                # Check if modified at least 5 seconds after closing (to account for API timing)
                time_diff = (date_modified - date_closed).total_seconds()
                
                # Skip if not actually modified (less than 60 seconds difference)
                if time_diff < 60:
                    skipped_count += 1
                    continue
                
                league_id = str(match_data['id'])
                
                # Check if game exists in Root Database
                try:
                    game = Game.objects.get(league_id=league_id)
                except Game.DoesNotExist:
                    # Game doesn't exist, skip
                    continue
                
                print(f"Game {league_id} was modified {time_diff:.0f} seconds after closing. Updating...")
                
                # Use atomic transaction to update
                with transaction.atomic():
                    # Delete existing efforts
                    game.efforts.all().delete()
                    
                    # Update game data
                    update_game_from_api(game, match_data)
                    
                    # Create efforts with new data
                    create_efforts_from_api(game, match_data['participants'])
                
                updated_count += 1
                updated_list.append(league_id)
                print(f"Successfully updated game {league_id}")
                
            except Exception as e:
                print(f"Error updating game {str(match_data.get('id'))}: {e}")
                import traceback
                traceback.print_exc()
                error_count += 1
                error_list.append(str(match_data.get('id')))
                continue
        
        # Check if there are more results
        has_more = data.get('next') is not None
        # Add delay before next API call
        if has_more:
            time.sleep(0.5)  # 500ms delay between API requests
        offset += limit

        summary = f"Update check complete: {updated_count} updated, {skipped_count} skipped, {error_count} errors"

        # Build fields for summary message
        fields = []

        if updated_list:
            fields.append({
                'name': 'Updated',
                'value': format_bulleted_list(updated_list),
            })

        if error_list:
            error_field = {
                'name': 'Errors',
                'value': format_bulleted_list(error_list),
            }
            fields.append(error_field)

            # Send error message
            send_rich_discord_message_task.delay(
                summary,
                author_name='RDB Admin',
                category='report',
                title='Update Errors',
                fields=[error_field]
            )

        # Send main update summary (only if anything happened)
        if updated_count > 0 or error_count > 0:
            send_rich_discord_message_task.delay(
                summary,
                author_name='RDB Admin',
                category='rdl-update',
                title='RDL Update Check',
                fields=fields
            )

    print(summary)
    return summary

@shared_task(bind=True, autoretry_for=(requests.RequestException,), retry_backoff=True, max_retries=3)
def check_all_league_rounds(delete=False, list_games=True):
    tournament, _ = Tournament.objects.get_or_create(name='Root Digital League')
    results = {}
    total_missing_count = 0
    for round_obj in Round.objects.filter(
        Q(tournament=tournament, stage__isnull=True) | Q(stage__tournament=tournament)
    ):
        site_count, api_count, missing_count = compare_league_game_count(round_obj)

        if missing_count is None:
            print(f"Skipping '{round_obj.name}': API unreachable")
            continue

        if missing_count > 0:
            results[round_obj.name] = {
                'site_count': site_count,
                'api_count': api_count,
                'missing_count': missing_count,
                }
            total_missing_count += missing_count
            print(f"Round '{round_obj.name}' is out of sync! Missing {missing_count} games.")

            # Only list games if explicitly requested
            if list_games:
                # deleted_ids = find_deleted_games(round_obj)
                api_result = find_deleted_games(round_obj)

                if api_result.get("error"):
                    print("API issue:", api_result["error"])
                    deleted_ids = None
                else:
                    deleted_ids = api_result["deleted_ids"]

                print(f'Count from API: {api_result['api_count']}')
                
                if deleted_ids:
                    results[round_obj.name]["deleted_game_ids"] = list(deleted_ids)


                    # Only delete if explicitly requested
                    if delete == True:
                        # Safety: refuse to delete more than half a round's games
                        if len(deleted_ids) > site_count * 0.5:
                            print(f"SAFETY: Refusing to delete {len(deleted_ids)}/{site_count} games from '{round_obj.name}' (>50%)")
                            send_discord_message_task.delay(
                                f"SAFETY: Refusing to delete {len(deleted_ids)}/{site_count} games from '{round_obj.name}' (>50%)",
                                'report'
                            )
                        else:
                            count, _ = Game.objects.filter(league_id__in=deleted_ids, round=round_obj).delete()
                            print(f"Deleted {count} games from '{round_obj.name}'")
    if not results:
        print("All Root Digital League rounds are up to date.")
    else:
        fields = []
        for key, value in results.items():
            if value.get('deleted_game_ids'):
                fields.append({
                    'name': key,
                    'value': format_bulleted_list(value['deleted_game_ids'])
                })
            elif value['missing_count']:
                fields.append({
                    'name': key,
                    'value': f"{value['missing_count']} games missing"
                })

        send_rich_discord_message_task.delay(
            f'{total_missing_count} games missing from RDL',
            author_name='RDB Admin',
            category='rdl-delete',
            title='Deleted Games',
            fields=fields
        )

    return results



def find_deleted_games(league_round, limit=200):
    """
    Compare local games in a round with API data and return a list of missing (deleted) game IDs.

    Args:
        league_round: The Round object to check
        limit: Number of games per page to fetch from the API
    Returns:
        set of league_ids that exist locally but not in the API
    """
    tournament_name = league_round.name

    params = {
        'limit': limit,
        'tournament__name': tournament_name
    }
    api_game_ids = set()
    url = BASE_URL

    # Fetch all pages from the API
    while url:
        try:
            response = requests.get(url, params=params, headers=API_HEADERS, timeout=30)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as e:
            print(f'Error fetching data for "{tournament_name}": {e}')
            raise

        results = data.get("results", [])
        api_game_ids.update(str(g["id"]) for g in results if "id" in g)

        # Move to the next page
        url = data.get("next")
        params = None  # only send params once

    # Get all local game IDs for this round
    site_game_ids = set(
        Game.objects.filter(round=league_round)
        .values_list("league_id", flat=True)
        .iterator()
    )

    # If API returned nothing at all, treat that as an error case
    if not api_game_ids:
        return {
            "error": f"API returned zero IDs for {tournament_name}",
            "api_count": 0,
            "site_count": len(site_game_ids),
            "deleted_count": None,
            "deleted_ids": None,
        }

    # Find missing games
    deleted_ids = site_game_ids - api_game_ids

    return {
        "api_count": len(api_game_ids),
        "site_count": len(site_game_ids),
        "deleted_count": len(deleted_ids),
        "deleted_ids": deleted_ids,
    }


def compare_league_game_count(league_round):
    """
    Compare the count of league games on the site to the API count

    Args:
        league_round: A round object from Root Digital League

    """

    tournament_name = league_round.name

    site_count = Game.objects.filter(round=league_round).count()

    api_count = count_games_from_api(tournament_name=tournament_name)

    if api_count is None:
        return site_count, None, None

    missing_count = site_count - api_count


    return site_count, api_count, missing_count

def count_games_from_api(tournament_name):
    """
    Count games in tournament from the Root League API.
    
    Args:
        tournament_name: String included in the tournament's name
    """

    # Fetch data from API
    params = {
        'tournament__name': tournament_name,
        'limit': 1,
        }
    
    try:
        response = requests.get(BASE_URL, params=params, headers=API_HEADERS, timeout=30)
        response.raise_for_status()
        data = response.json()
        return data.get('count', 0)
    except requests.RequestException as e:
        print(f"Error fetching API data: {e}")
        return None


def _apikey_to_canonical(api_key):
    """'led_slash-3579' -> 'led_slash+3579'. Split on the LAST dash so names with
    dashes/underscores survive. Returns None if there's no dash."""
    name, sep, num = api_key.rpartition('-')
    return f'{name}+{num}' if sep else None


def _feed_key_lookup(feed_key, source):
    """Normalize a feed player key to the lowercased value used to look up a
    Profile, per the system's url_key_source. Returns None when the key can't
    produce a lookup value for that source."""
    UrlKeySource = EloSystem.UrlKeySource
    if source == UrlKeySource.CANONICAL:
        canonical = _apikey_to_canonical(feed_key)
        return canonical.lower() if canonical else None
    # SLUG, DISCORD, PK: the feed key IS the reference (used verbatim, lowercased).
    return feed_key.lower() if feed_key else None


def _build_profile_lookup(source):
    """Return {lowercased reference value -> Profile} for matching a feed against
    profiles, per the system's url_key_source. First writer wins on a collision."""
    UrlKeySource = EloSystem.UrlKeySource
    lookup = {}
    if source == UrlKeySource.CANONICAL:
        # Prefer rdl_cannonical_dwd; fall back to dwd when it's missing.
        qs = Profile.objects.filter(
            Q(rdl_cannonical_dwd__isnull=False, rdl_cannonical_dwd__gt='')
            | Q(dwd__isnull=False, dwd__gt=''))
        for p in qs:
            value = p.rdl_cannonical_dwd or p.dwd
            if value:
                lookup.setdefault(value.lower(), p)
    elif source == UrlKeySource.PK:
        for p in Profile.objects.all():
            lookup.setdefault(str(p.pk), p)
    else:  # SLUG or DISCORD — the field name equals the source value
        field = source  # 'slug' or 'discord'
        qs = Profile.objects.filter(**{f'{field}__isnull': False}).exclude(**{field: ''})
        for p in qs:
            value = getattr(p, field, None)
            if value:
                lookup.setdefault(value.lower(), p)
    return lookup


def _normalize_icon_url(url):
    """Collapse accidental duplicate path segments in a feed icon_url. The feed
    sometimes emits '.../rootelo//rootelo/assets/...' (a doubled base); reduce any
    run of repeated '/rootelo/' back to a single one, and clean stray '//' in the
    path. Returns None unchanged."""
    if not url:
        return url
    # Fix the specific doubled base the feed produces, then any generic '//' in the
    # path portion (but not the '://' after the scheme).
    url = url.replace('/rootelo//rootelo/', '/rootelo/')
    scheme, sep, rest = url.partition('://')
    if sep:
        rest = re.sub(r'/{2,}', '/', rest)
        url = f'{scheme}://{rest}'
    return url


def _refresh_one_rootelo_system(system, dry_run=False):
    """Fetch one ROOTELO system's feed and upsert its EloParticipants. Returns a
    per-system summary dict. On fetch/parse error, returns without writing so a
    bad feed never wipes existing participant rows."""
    try:
        resp = requests.get(system.api_url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        logger.warning("refresh_rootelo: fetch failed for %s (%s)", system, system.api_url,
                       exc_info=True)
        return {'system': system.name, 'matched': 0, 'error': True}

    players = data.get('players', {}) or {}

    # How feed keys map to profiles is configured per system by url_key_source
    # (the same setting drives trends_url). Build a {lowercased value -> Profile}
    # lookup for that source, then normalize each feed key the same way.
    source = system.url_key_source
    profiles = _build_profile_lookup(source)
    by_lookup = {}
    for feed_key, entry in players.items():
        key = _feed_key_lookup(feed_key, source)
        if key:
            by_lookup[key] = (feed_key, entry)

    # Existing participants for this system, keyed by player_id (single query).
    existing = {ep.player_id: ep for ep in system.participants.all()}

    to_create, to_update, matched = [], [], 0
    for key, (feed_key, entry) in by_lookup.items():
        profile = profiles.get(key)
        if not profile:
            continue
        matched += 1
        rating = entry.get('elo')
        if rating is None:
            rating = system.initial_rating
        fields = dict(
            rating=rating,
            games_played=entry.get('games') or 0,
            wins=entry.get('wins') or 0,
            rank=(str(entry['rank']) if entry.get('rank') is not None else None),
            tier=entry.get('tier'),
            bg_color=entry.get('bg_color'),
            icon_url=_normalize_icon_url(entry.get('icon_url')),
            win_rate=entry.get('win_rate'),
            feed_key=feed_key,
        )
        ep = existing.get(profile.pk)
        if ep:
            for k, v in fields.items():
                setattr(ep, k, v)
            to_update.append(ep)
        else:
            to_create.append(EloParticipant(elo_system=system, player=profile, **fields))

    if not dry_run:
        with transaction.atomic():
            if to_create:
                EloParticipant.objects.bulk_create(to_create, batch_size=500)
            if to_update:
                EloParticipant.objects.bulk_update(
                    to_update,
                    ['rating', 'games_played', 'wins', 'rank', 'tier', 'bg_color',
                     'icon_url', 'win_rate', 'feed_key'],
                    batch_size=500,
                )
    return {'system': system.name, 'matched': matched,
            'created': len(to_create), 'updated': len(to_update)}


def refresh_rootelo_ranks_impl(dry_run=False):
    """Refresh every ROOTELO EloSystem that has an api_url. Returns a list of
    per-system summary dicts."""
    systems = (EloSystem.objects
               .filter(calculation_type=EloSystem.CalculationType.ROOTELO)
               .exclude(api_url__isnull=True)
               .exclude(api_url__exact=''))
    results = []
    for system in systems:
        results.append(_refresh_one_rootelo_system(system, dry_run=dry_run))
        time.sleep(0.5)  # be polite between feeds (matches import_league_games cadence)
    return results


@shared_task
def refresh_rootelo_ranks():
    """Fetch every ROOTELO EloSystem's feed and upsert its EloParticipants.
    Scheduled (~once/day via django_celery_beat, configured in admin)."""
    return refresh_rootelo_ranks_impl()