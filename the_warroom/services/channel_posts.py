"""
Posting into a Tournament's configured Discord channels.

A tournament can name three channels in its linked guild (results_channel,
schedule_channel, game_threads_channel), set by a guild moderator from the Edit Guild
page. Every send goes through post_to_tournament_channel so the guild-ownership check
lives in exactly one audited place.
"""
import logging

logger = logging.getLogger(__name__)

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
