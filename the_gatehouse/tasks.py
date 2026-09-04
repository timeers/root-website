"""Site-level Celery tasks.

Two groups live here: the content sweeps (post status, daily users), and the
Discord tasks that are website functionality rather than bot functionality —
webhook site notifications and the OAuth login guild sync. The bot's own tasks
(DMs, channel posts, LFG threads, schedule polls) live in the_databot.tasks.
"""
import time
from enum import Enum

from celery import shared_task
from celery.exceptions import Retry
from dateutil.relativedelta import relativedelta
from django.core.cache import cache
from django.utils import timezone

from the_keep.models import StatusChoices, Faction, Vagabond, Deck, Map, Landmark, Hireling, Tweak
from the_warroom.models import Game, Effort

from .models import Profile
from .services.discord_oauth import update_discord_avatar
from .services.webhookservice import send_discord_message, send_rich_discord_message
from .services.context_service import get_daily_user_summary
from .utils import format_bulleted_list

import logging

logger = logging.getLogger(__name__)

@shared_task
def test_task(message=None):
    if message:
        send_discord_message(message, category="feedback")
    else:
        send_discord_message("This is a scheduled test.", category="feedback")

def update_status_fk_model(obj_queryset, related_model, related_field, inactive_period, development_count, development_list, inactive_count, inactive_list):
    for obj in obj_queryset:
        has_recent_related = related_model.objects.filter(
            **{related_field: obj},
            date_posted__gte=inactive_period
        ).exists()
        is_old = obj.date_updated < inactive_period

        if obj.status == StatusChoices.TESTING:
            if not has_recent_related:
                if is_old:
                    obj.status = StatusChoices.INACTIVE
                    inactive_count += 1
                    inactive_list.append(obj.title)
                else:
                    obj.status = StatusChoices.DEVELOPMENT
                    development_count += 1
                    development_list.append(obj.title)
                obj.save(update_fields=['status'])


        elif obj.status == StatusChoices.DEVELOPMENT:
            if not has_recent_related and is_old:
                obj.status = StatusChoices.INACTIVE
                inactive_count += 1
                inactive_list.append(obj.title)
                obj.save(update_fields=['status'])


    return development_count, development_list, inactive_count, inactive_list


def update_status_m2m_model(obj_queryset, related_name, inactive_period, development_count, development_list, inactive_count, inactive_list):
    for obj in obj_queryset:
        has_recent_games = getattr(obj, related_name).filter(date_posted__gte=inactive_period).exists()
        is_old = obj.date_updated < inactive_period

        if obj.status == StatusChoices.TESTING:
            if not has_recent_games:
                if is_old:
                    obj.status = StatusChoices.INACTIVE
                    inactive_count += 1
                    inactive_list.append(obj.title)
                else:
                    obj.status = StatusChoices.DEVELOPMENT
                    development_count += 1
                    development_list.append(obj.title)
                obj.save(update_fields=['status'])

        elif obj.status == StatusChoices.DEVELOPMENT:
            if not has_recent_games and is_old:
                obj.status = StatusChoices.INACTIVE
                inactive_count += 1
                inactive_list.append(obj.title)
                obj.save(update_fields=['status'])

    return development_count, development_list, inactive_count, inactive_list



@shared_task
def update_post_status():
    
    inactive_period = timezone.now() - relativedelta(months=6)

    development_count = 0
    development_list = []
    inactive_count = 0
    inactive_list = []


    development_count, development_list, inactive_count, inactive_list = update_status_fk_model(
        Faction.objects.filter(status__in=[StatusChoices.TESTING, StatusChoices.DEVELOPMENT]),
        Effort,
        'faction',
        inactive_period,
        development_count,
        development_list,
        inactive_count,
        inactive_list,
    )

    development_count, development_list, inactive_count, inactive_list = update_status_fk_model(
        Vagabond.objects.filter(status__in=[StatusChoices.TESTING, StatusChoices.DEVELOPMENT]),
        Effort,
        'vagabond',
        inactive_period,
        development_count,
        development_list,
        inactive_count,
        inactive_list
    )

    development_count, development_list, inactive_count, inactive_list = update_status_fk_model(
        Deck.objects.filter(status__in=[StatusChoices.TESTING, StatusChoices.DEVELOPMENT]),
        Game,
        'deck',
        inactive_period,
        development_count,
        development_list,
        inactive_count,
        inactive_list
    )

    development_count, development_list, inactive_count, inactive_list = update_status_fk_model(
        Map.objects.filter(status__in=[StatusChoices.TESTING, StatusChoices.DEVELOPMENT]),
        Game,
        'map',
        inactive_period,
        development_count,
        development_list,
        inactive_count,
        inactive_list
    )


    # Many-to-many fields (Landmark, Tweak, Hireling)
    development_count, development_list, inactive_count, inactive_list = update_status_m2m_model(
        Landmark.objects.filter(status__in=[StatusChoices.TESTING, StatusChoices.DEVELOPMENT]),
        'games',
        inactive_period,
        development_count,
        development_list,
        inactive_count,
        inactive_list
    )

    development_count, development_list, inactive_count, inactive_list = update_status_m2m_model(
        Tweak.objects.filter(status__in=[StatusChoices.TESTING, StatusChoices.DEVELOPMENT]),
        'games',
        inactive_period,
        development_count,
        development_list,
        inactive_count,
        inactive_list
    )

    development_count, development_list, inactive_count, inactive_list = update_status_m2m_model(
        Hireling.objects.filter(status__in=[StatusChoices.TESTING, StatusChoices.DEVELOPMENT]),
        'games',
        inactive_period,
        development_count,
        development_list,
        inactive_count,
        inactive_list
    )

    # Create cleanup message
    fields = []
    if development_count or inactive_count:
        if development_count:
            fields.append({
                'name': 'Development',
                'value': format_bulleted_list(development_list)
            })
        if inactive_count:
            fields.append({
                'name': 'Inactive', 
                'value': format_bulleted_list(inactive_list)
                })
        if not inactive_count:
            message = f'{development_count} Post(s) moved to Development.'
        elif not development_count:
            message = f'{inactive_count} Post(s) moved to Inactive.'
        else:
            message = f'{development_count} Post(s) moved to Development and {inactive_count} Post(s) moved to Inactive.'
    else:
        message = f'No Posts moved during cleanup.'

        
    # Call cleanup summary message to Discord
    send_rich_discord_message(
        message,
        author_name='RDB Admin',
        category='inactive-cleanup',
        title='Inactive Cleanup',
        fields=fields
    )


@shared_task
def daily_users():
    summary = get_daily_user_summary()

    send_rich_discord_message(
        summary['message'],
        author_name='RDB Admin',
        category='user-summary',
        title='Daily User Summary',
        fields=summary['fields']
    )




# ─────────────────────────────────────────────────────────────────────────────
# Discord webhook site notifications
# ─────────────────────────────────────────────────────────────────────────────

@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={'max_retries': 3, 'countdown': 30},
    retry_backoff=True,
)
def send_rich_discord_message_task(*args, **kwargs):
    try:
        send_rich_discord_message(*args, **kwargs)
    except Exception:
        logger.exception("Discord webhook failed")
        raise

@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={'max_retries': 3, 'countdown': 30},
    retry_backoff=True,
)
def send_discord_message_task(*args, **kwargs):
    try:
        send_discord_message(*args, **kwargs)
    except Exception:
        logger.exception("Discord webhook failed")
        raise



# ─────────────────────────────────────────────────────────────────────────────
# Discord OAuth: avatar sync and login guild refresh
# ─────────────────────────────────────────────────────────────────────────────

@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={'max_retries': 3, 'countdown': 30},
    retry_backoff=True,
)
def update_discord_avatar_task(user_id, force=False):
    # Deferred from user_logged_in_handler: the avatar download is a pure
    # side-effect (its result isn't used in the login flow), so it doesn't
    # need to block the request path on a Discord CDN call.
    from django.contrib.auth import get_user_model
    user = get_user_model().objects.filter(pk=user_id).first()
    if user is None:
        # Raise rather than return: a silent no-op here meant a lost avatar left no
        # trace at all, and autoretry_for never fired.
        raise RuntimeError(f"No user {user_id} for avatar sync")
    if not hasattr(user, 'profile'):
        raise RuntimeError(f"User {user_id} has no profile for avatar sync")
    update_discord_avatar(user, force=force)


class GuildSyncResult(Enum):
    """Why a guild refresh ended, so callers know whether retrying could ever help."""
    OK = 'ok'
    # Discord was slow, rate-limited or erroring, or the inline budget ran out.
    # A retry is worth doing.
    TRANSIENT = 'transient'
    # No usable Discord token (or no Discord account at all). Retrying re-discovers
    # the same thing, so terminate immediately instead of burning the retry ladder.
    NO_TOKEN = 'no_token'


def _report_unusable_token(user):
    """Surface a Discord account whose token no longer works.

    One report per user per day: a revoked token re-reports on every single login, and
    an alert that repeats is an alert that gets muted. cache.add is atomic and returns
    False when the key is already set, so concurrent logins send exactly once.
    """
    key = f'unusable-discord-token:{user.pk}'
    if cache.add(key, True, timeout=60 * 60 * 24):
        send_discord_message_task.delay(
            f"No usable Discord token for {user} - guild refresh skipped. "
            f"They should log out and back in via Discord.", category='report')


def _clear_guilds_refreshing(profile, synced):
    """Drop the refresh flag. `synced` stamps guilds_synced_at; a FAILED refresh must
    not, because guilds_synced_at feeds needs_sync_now in the login signal — claiming a
    sync we never achieved would suppress the inline retry on the user's next login."""
    fields = ['guilds_refreshing', 'guilds_refresh_started_at']
    profile.guilds_refreshing = False
    profile.guilds_refresh_started_at = None
    if synced:
        profile.guilds_synced_at = timezone.now()
        fields.append('guilds_synced_at')
    profile.save(update_fields=fields)


def refresh_user_guilds(user, budget=None):
    """Refresh a user's Discord guild membership, flags, group and display name.

    Shared by refresh_user_guilds_task (the async path, no budget) and the login signal
    (the inline path for a STALE profile, under a short budget). Returns a
    GuildSyncResult: OK means the refresh completed and the profile was saved with
    `guilds_refreshing` cleared. TRANSIENT and NO_TOKEN both mean NOTHING was written —
    the caller decides whether to retry (TRANSIENT) or give up now (NO_TOKEN).

    `budget` is a wall-clock allowance in seconds for the Discord calls. Enforced with a
    monotonic deadline: each call gets whatever time is left as its `timeout`. Note
    requests' timeout is per-socket-operation, not a hard wall-clock cap, so this bounds
    the work closely but not exactly — fine here, since the fallback path is safe.

    Retry lives in the TASK, not here: self.retry() needs the bound task context and is
    meaningless on a request thread.
    """
    from django.db.models import Q
    from the_keep.models import Post
    from .services.discord_oauth import (
        get_user_guilds, update_user_guilds, derive_guild_membership,
        get_discord_display_name, discord_refresh_capability,
    )

    profile = user.profile

    # Check before spending any time: without a usable token every call below fails the
    # same way, and the task would burn its whole retry ladder rediscovering that.
    capability = discord_refresh_capability(user)
    if capability != 'ok':
        if capability == 'no_token':
            # Has a Discord account but no usable token: a real fault, and invisible
            # until someone reports a stuck spinner. 'no_account' is the ordinary
            # admin/password login and is NOT a fault, so it stays silent.
            logger.warning("Discord token unusable for %s; skipping guild refresh", user)
            _report_unusable_token(user)
        return GuildSyncResult.NO_TOKEN

    deadline = None if budget is None else time.monotonic() + budget

    def _remaining():
        """Seconds left for the next call, or None for 'no budget' (historical 5s)."""
        if deadline is None:
            return None
        return deadline - time.monotonic()

    def _timeout_kwargs():
        left = _remaining()
        return {} if left is None else {'timeout': left}

    if _remaining() is not None and _remaining() <= 0:
        return GuildSyncResult.TRANSIENT

    guilds = get_user_guilds(user, **_timeout_kwargs())
    if guilds is None:
        # API failure — distinct from "really in no guilds" ([]). The token was usable
        # a moment ago (checked above), so treat this as transient: do NOT touch
        # flags/group (never demote on a transient failure).
        return GuildSyncResult.TRANSIENT

    update_user_guilds(user, guilds)
    in_ww, in_wr, in_fr = derive_guild_membership(guilds)

    updated = False

    # Promote a Woodland Warriors member who's still Outcast/Player (moved here from the
    # login signal so it runs against FRESH membership, incl. first login).
    if profile.group in ('O', 'P') and in_ww:
        has_posts = Post.objects.filter(
            Q(designer=profile) | Q(co_designers=profile) | Q(moderators=profile)
        ).exists()
        if has_posts:
            profile.group = 'E'
            updated = True
        elif profile.group == 'O':
            profile.group = 'P'
            updated = True

    # Feature flags are only ever turned ON (never demoted here) — matches prior behavior.
    if not profile.in_weird_root and in_wr:
        profile.in_weird_root = True
        profile.weird = True
        updated = True
    if not profile.in_french_root and in_fr:
        profile.in_french_root = True
        updated = True
    if not profile.in_woodland_warriors and in_ww:
        profile.in_woodland_warriors = True
        updated = True

    # The display name is cosmetic; the group promotion above is what gates access. If
    # the budget is spent, skip it and let the async task backfill it later.
    left = _remaining()
    if left is None or left > 0:
        display_name = get_discord_display_name(user, **_timeout_kwargs())
        if display_name and profile.display_name != display_name:
            profile.display_name = display_name
            updated = True

    profile.guilds_refreshing = False
    profile.guilds_refresh_started_at = None
    profile.guilds_synced_at = timezone.now()
    if updated:
        profile.save(update_fields=[
            'group', 'in_weird_root', 'weird', 'in_french_root', 'in_woodland_warriors',
            'display_name', 'guilds_refreshing', 'guilds_refresh_started_at',
            'guilds_synced_at',
        ])
    else:
        profile.save(update_fields=[
            'guilds_refreshing', 'guilds_refresh_started_at', 'guilds_synced_at',
        ])
    return GuildSyncResult.OK


@shared_task(bind=True, max_retries=3)
def refresh_user_guilds_task(self, user_id):
    """Refresh a user's Discord guild membership OFF the login request thread.

    Deferred from user_logged_in_handler so a slow/rate-limited Discord API call can
    never block (and eventually exhaust) the WSGI worker pool — the outage this fixes.
    This task is the AUTHORITY for guild flags + group promotion whenever login didn't
    already do it inline (see user_logged_in_handler).

    Retry is manual (NOT autoretry_for) so `guilds_refreshing` stays True across pending
    retries — clearing it per-failed-attempt would drop the header spinner while a retry
    is still queued. The flag clears on every TERMINAL path and only there: success,
    no usable token, retries exhausted, or an unexpected error. Anything that leaves
    here without clearing must be a retry that is genuinely still queued.
    """
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.filter(pk=user_id).first()
    if user is None or not hasattr(user, 'profile'):
        # Never a bare return while a profile could still hold the flag. Note that
        # Profile.user is on_delete=SET_NULL (models.py:1017), so a DELETED user detaches
        # its profile and this lookup finds nothing -- those rows are freed by the
        # staleness window instead. This covers the reachable case: the row still points
        # at a user we just failed to load.
        orphan = Profile.objects.filter(user_id=user_id).first()
        if orphan is not None:
            _clear_guilds_refreshing(orphan, synced=False)
        return

    try:
        result = refresh_user_guilds(user)
    except Retry:
        # Must precede the broad handler: Retry subclasses Exception, and swallowing it
        # would silently turn a queued retry into a terminal clear.
        raise
    except Exception:
        logger.exception("Guild refresh failed for user %s", user)
        _clear_guilds_refreshing(user.profile, synced=False)
        return

    if result is GuildSyncResult.OK:
        # refresh_user_guilds already saved the profile with the flag cleared.
        return

    if result is GuildSyncResult.NO_TOKEN:
        # Retrying cannot conjure a token. Stop now rather than holding the spinner up
        # for the full ~180s ladder. Not stamping guilds_synced_at means the next login
        # (which re-stores the token via allauth) syncs inline as if never synced.
        logger.warning("No usable Discord token for user %s; skipping guild refresh", user)
        _clear_guilds_refreshing(user.profile, synced=False)
        return

    # TRANSIENT: Discord was slow or erroring. Retry a few times, then give up and clear
    # the spinner so it can't get stuck on the header forever.
    if self.request.retries < self.max_retries:
        # Re-stamp so the staleness window measures time since the last sign of life,
        # not time since login. Without this the ladder (30+60+90s of backoff, plus
        # execution) races GUILDS_REFRESH_MAX_AGE and the spinner can vanish while a
        # retry is genuinely still queued -- breaking the contract in this docstring.
        profile = user.profile
        profile.guilds_refresh_started_at = timezone.now()
        profile.save(update_fields=['guilds_refresh_started_at'])
        raise self.retry(countdown=30 * (self.request.retries + 1))

    _clear_guilds_refreshing(user.profile, synced=False)


