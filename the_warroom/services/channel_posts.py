"""
Posting into a Tournament's configured Discord channels.

A tournament can name three channels in its linked guild (results_channel,
schedule_channel, game_threads_channel), set by a guild moderator from the Edit Guild
page. Every send goes through post_to_tournament_channel so the guild-ownership check
lives in exactly one audited place.

Also home to match_thread_id, which gives the same guarantee for a player group's
Discord THREAD: it lives here rather than in views.py because both the record-game
view and the reminder Celery task need it.
"""
import logging
import re

logger = logging.getLogger(__name__)

# Group thread URLs are https://discord.com/channels/<guild>/<thread>, optionally
# with a trailing message id. DISCORD_URL_PATTERN (used on the series edit page)
# only checks the host, so a moderator can paste an invite or a DM link -- anchor
# the full shape and capture the guild too, so we can prove the thread belongs to
# this tournament's server before posting into it.
DISCORD_THREAD_URL_RE = re.compile(
    r'^https://(?:discord\.com|discordapp\.com)/channels/(\d+)/(\d+)(?:/\d+)?/?$')


def match_thread_id(match, tournament=None):
    """The Discord thread id to post into for this match, or None to skip.

    Skips unless the player group's thread URL is a real channel link AND its guild
    is the tournament's guild -- a stale or mistyped URL would otherwise post a
    tournament's game link into an unrelated server. A tournament with no guild
    linked is never announced.

    `tournament` may be supplied by a caller that has already resolved it, which
    also PINS which tournament the guild is checked against. It defaults to
    match.round.get_tournament() -- note that consults the LEGACY Round.tournament
    FK, so a caller that must not use the legacy path should pass it explicitly
    (as remind_upcoming_matches does). Passing it also avoids touching
    round.tournament_id, which matters when the caller deferred that column.
    """
    group = getattr(match, 'player_group', None)
    url = (getattr(group, 'discord_thread', '') or '').strip()
    if not url:
        return None
    found = DISCORD_THREAD_URL_RE.match(url)
    if not found:
        return None
    url_guild, thread_id = found.group(1), found.group(2)

    if tournament is None:
        tournament = match.round.get_tournament() if match.round_id else None
    guild_snowflake = getattr(getattr(tournament, 'guild', None), 'guild_id', None)
    if not guild_snowflake or str(guild_snowflake) != url_guild:
        return None
    return thread_id


def match_reminder_thread_id(match, tournament):
    """The thread id to send this match's REMINDER into, or None to skip.

    Both gates must hold at SEND time, not at schedule time:
      1. the bot is still in the guild -- otherwise every post 403s,
      2. the thread URL's guild IS this tournament's guild (match_thread_id).

    `tournament` is REQUIRED here, not re-derived: the caller resolved it from
    round.stage.tournament, and passing it through is what keeps the legacy
    Round.tournament FK out of the reminder path entirely.
    """
    guild = getattr(tournament, 'guild', None)
    if not guild or not guild.bot_member:
        return None
    return match_thread_id(match, tournament=tournament)

# field name -> is it a forum channel? (game threads are forum posts; the other two are
# ordinary text channels). Used to pick which channel list the id is verified against,
# so a text channel can never satisfy the forum-only field or vice versa.
_CHANNEL_FIELDS = {
    'results_channel': False,
    'schedule_channel': False,
    'game_threads_channel': True,
}


def resolve_tournament_channel(tournament, field):
    """The channel id to post into for this tournament/field, or None to skip.

    SECURITY: returns None unless the stored id is CONFIRMED to belong to the
    tournament's CURRENT guild. The ids are bare snowflakes with no guild embedded, so
    after a tournament is re-pointed at a different guild a stale id would still be a
    valid channel -- in the wrong server. Tournament.save() clears the fields on a
    re-point; this is the second layer for rows that drifted before that existed, and
    the same guarantee _match_thread_id gives for group thread URLs.

    Fails CLOSED: an unreachable Discord means "unverified", so nothing is posted.
    """
    if field not in _CHANNEL_FIELDS:
        raise ValueError(f"unknown tournament channel field: {field}")
    if tournament is None:
        return None
    guild = getattr(tournament, 'guild', None)
    if guild is None:
        return None
    channel_id = (getattr(tournament, field, None) or '').strip()
    if not channel_id:
        return None

    from the_databot.services.discordservice import channel_belongs_to_guild
    if not channel_belongs_to_guild(guild, channel_id,
                                    forum=_CHANNEL_FIELDS[field]):
        logger.warning(
            "Refusing to post to %s=%s for tournament %s: not a confirmed channel of "
            "guild %s (stale after a guild change, or Discord unreachable)",
            field, channel_id, tournament.pk, guild.guild_id)
        return None
    return channel_id


def post_to_tournament_channel(tournament, field, content):
    """Queue `content` into one of a tournament's channels. Returns True if queued.

    Skips silently (returning False) whenever resolve_tournament_channel refuses -- no
    guild, unset field, or an unverified channel. Callers running inside a transaction
    must wrap this in transaction.on_commit: the Celery worker would otherwise be able
    to read -- or announce -- a row the transaction goes on to roll back.
    """
    channel_id = resolve_tournament_channel(tournament, field)
    if not channel_id:
        return False
    from the_databot.tasks import post_channel_message_task
    post_channel_message_task.delay(channel_id, content)
    return True
