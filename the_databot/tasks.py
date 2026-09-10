"""Celery tasks owned by the Discord bot.

Everything here acts as the bot: DMs, channel and thread posts, LFG threads,
schedule polls, command registration. The webhook site notifications and the
OAuth login guild sync are website concerns and live in the_gatehouse.tasks.

Import direction is one-way -- this module reads the_gatehouse models and
services, but the_gatehouse never imports discord_interactions.
"""
import time
from datetime import datetime, timedelta, timezone as dt_timezone

from celery import shared_task
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone

from the_keep.models import Post, Faction, Vagabond, Deck, Map
from the_warroom.models import Game
from the_gatehouse.models import DiscordGuild, Profile, UserNotification, MessageChoices
from .models import BotUsage, GuildLFGRole, LFGThread, LFGRoll, LFGDraft, LFGDraftPick

from .services.discordservice import (send_discord_dm, sync_bot_guilds,
                                      post_interaction_followup,
                                      register_guild_commands, DM_ERROR)
# lfg_game imports no models at module level (it defers them inside functions for
# the circular-import reason documented there), so this is safe at import time.
from .services.lfg_game import (
    schedule_closed_embed, PROPOSAL_RETIRED_TEXT, name_join,
)
# Lives in discord_commands, not discord_interactions: that module imports THIS one,
# so importing it back would cycle.
from .services.discord_commands import LFG_DEFAULT_TITLE

import logging

logger = logging.getLogger(__name__)

# Discord message flag: only the invoking user sees the message. Mirrors EPHEMERAL in
# discord_interactions.py — defined locally to avoid a circular import (that module
# imports from this one).
EPHEMERAL = 64

# Pacing for bulk forum-thread creation (create_match_threads_task). Discord's thread
# limits aren't published as a fixed number and vary by channel and guild, so this is a
# deliberate go-slow rather than a computed rate: a round of 40 matches takes ~40s of
# wall clock in a worker, which costs nothing and keeps a normal batch under the limit
# entirely. _THREAD_CREATE_MAX_BACKOFF caps how long a single 429 can park the worker --
# beyond that the item is reported as failed and the user retries.
_THREAD_CREATE_INTERVAL = 1.0      # seconds between creations
_THREAD_CREATE_MAX_BACKOFF = 30.0  # seconds; longest we honour a retry_after inline


@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={'max_retries': 3, 'countdown': 30},
    retry_backoff=True,
)
def post_channel_message_task(channel_id, content, allowed_mentions=None):
    """Post a message into a channel/thread off the request path (the underlying
    call blocks for up to 5s, which an interaction response can't afford).
    Neither underlying call raises, so surface a transient failure as one to
    trigger the retry.

    allowed_mentions routes through post_channel_message_full, which accepts it;
    calling that service function directly from the request path instead would
    lose both this retry and the off-request-path posting.
    """
    from the_databot.services.discordservice import (
        post_channel_message, post_channel_message_full, THREAD_ERROR,
    )
    if allowed_mentions is not None:
        result, _message_id = post_channel_message_full(
            channel_id, content=content, allowed_mentions=allowed_mentions)
    else:
        result = post_channel_message(channel_id, content)
    if result == THREAD_ERROR:
        raise RuntimeError(f"Transient failure posting message in channel {channel_id}")


@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={'max_retries': 3, 'countdown': 30},
    retry_backoff=True,
)
def send_discord_dm_task(user_id, content=None, embed=None):
    from django.contrib.auth import get_user_model
    user = get_user_model().objects.get(pk=user_id)
    result = send_discord_dm(user, content=content, embed=embed)
    # Only retry transient failures. A blocked DM (no shared server / DMs off)
    # is permanent — return quietly instead of retrying 3x per recipient.
    if result == DM_ERROR:
        raise RuntimeError(f"Transient failure sending DM to user {user_id}")


@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={'max_retries': 3, 'countdown': 30},
    retry_backoff=True,
)
def sync_bot_guilds_task():
    if sync_bot_guilds() is None:
        raise RuntimeError("Failed to sync bot guilds from Discord")


@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={'max_retries': 3, 'countdown': 30},
    retry_backoff=True,
)
def post_interaction_followup_task(token, message_data):
    # Sends an ADDITIONAL message after an interaction's initial response, on the
    # interaction token. Used by /lfg's "add LFG tags" nudge and by /draft's
    # ephemeral seating prompt. Retries heal Discord's transient 404s when a
    # followup briefly races ahead of the initial ACK (callers also pass a small
    # countdown to let the initial response land first).
    try:
        post_interaction_followup(token, message_data)
    except Exception:
        logger.exception("Discord interaction followup failed")
        raise

@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={'max_retries': 3, 'countdown': 30},
    retry_backoff=True,
)
def register_guild_commands_task(guild_id):
    """Re-register a guild's slash commands off the request path. Enqueued (debounced
    with a short countdown) after LFG-role changes so /lfg swaps SINGLE↔MULTI and its
    tag choices stay current. register_guild_commands swallows errors and returns False;
    re-raise so Celery retries with backoff on a transient failure / 429."""
    guild = DiscordGuild.objects.filter(pk=guild_id).first()
    if guild and not register_guild_commands(guild):
        raise RuntimeError(f"guild command registration failed for {guild.guild_id}")


@shared_task
def record_bot_usage_task(guild_id, user_id, command):
    # Best-effort per-(guild, user, command) usage count for the Discord bot.
    # Fire-and-forget from the interaction dispatch, so it never delays the 3s
    # response; a lost count is harmless. get_or_create + F() increment is atomic
    # across workers (no read-modify-write race).
    if not user_id:
        return
    try:
        obj, _ = BotUsage.objects.get_or_create(
            guild_id=guild_id or None, user_id=user_id, command=command,
        )
        BotUsage.objects.filter(pk=obj.pk).update(
            count=F("count") + 1, last_used=timezone.now(),
        )
    except Exception:
        logger.exception("Failed to record bot usage")


# ── /lfg ─────────────────────────────────────────────────────────────────────

def ensure_profile_from_discord(discord_id, username, display_name):
    """Lookup-or-create a Profile for a Discord user. Plain function (callable both
    from a task and inline). Returns the Profile.

    Match order, mirroring what the site login does in signals.py:

      1. `discord_id` — an identity Discord actually verified. It beats any name.
      2. An UNLINKED profile whose `discord` handle matches (case-insensitive), which
         is then CLAIMED by writing the id. Most profiles here were created manually
         and carry no discord_id; this is how their owner takes ownership.
      3. Otherwise create one.

    The `discord_id__isnull=True` filter on step 2 is load-bearing, not a
    micro-optimisation. Without it a username match can return a profile that is
    ALREADY linked to a different Discord account — the caller then silently acts as
    that person, and on a permission-bearing path (e.g. /schedule) inherits their
    rights. Only an unclaimed profile is claimable.

    Order matters for the same reason: checking the username first would hand a user
    who already HAS a profile somebody else's row.

    `username` may be None (e.g. when only a display name + id are known, as at
    Start) — then match by id only and, on create, derive the handle from the id."""
    from the_warroom.services.root_league_api import sanitize_discord
    if not discord_id:
        return None
    discord_id = str(discord_id)
    cleaned = sanitize_discord(username) if username else None

    # 1) by discord id — the verified identity wins.
    profile = Profile.objects.filter(discord_id=discord_id).first()
    if profile:
        return profile

    # 2) by username, but ONLY an unclaimed profile (see the docstring).
    if cleaned:
        profile = Profile.objects.filter(
            discord__iexact=cleaned, discord_id__isnull=True).first()
        if profile:
            # The exists() guard mirrors signals.py: discord_id is unique=True, so
            # if step 1 and this raced, writing it again would raise IntegrityError.
            if not Profile.objects.filter(discord_id=discord_id).exists():
                profile.discord_id = discord_id
                # update_fields is required: a bare save() re-derives display_name
                # and can delete the profile's existing avatar.
                profile.save(update_fields=["discord_id"])
                logger.info("Discord %s claimed profile %s (%s) by username",
                            discord_id, profile.pk, profile.discord)
            return profile
    # 3) create — `discord` must be unique; fall back to id-suffixed handle on clash.
    discord_val = cleaned
    if not discord_val or Profile.objects.filter(discord__iexact=discord_val).exists():
        discord_val = sanitize_discord(f"{username or ''}{discord_id}") or discord_id
    try:
        return Profile.objects.create(
            discord=discord_val, discord_id=discord_id, display_name=display_name,
        )
    except IntegrityError:
        # Lost a create race (unique discord/discord_id): fall back to the now-existing row.
        # Only IntegrityError means "raced" — anything else is a real fault and is
        # re-raised below rather than being mislabelled and swallowed.
        # Same unlinked-only rule as step 2: without it this fallback could hand
        # back a profile already linked to somebody else.
        profile = (Profile.objects.filter(discord_id=discord_id).first()
                   or Profile.objects.filter(discord__iexact=discord_val,
                                             discord_id__isnull=True).first())
        if profile is None:
            # Neither the id nor the handle resolves: we return None and the caller
            # silently drops this player. Log it — this is the one path that can
            # quietly shrink an LFGThread's player list.
            logger.warning("Profile lookup failed after IntegrityError for discord_id "
                           "%s (discord=%s)", discord_id, discord_val)
        return profile


@shared_task
def ensure_profile_from_discord_task(discord_id, username, display_name):
    """Fire-and-forget wrapper for Join/Notify/lfg onboarding."""
    ensure_profile_from_discord(discord_id, username, display_name)


@shared_task
def notify_lfg_task(notify_ids, joiner_name, description, jump_url, owner_id=None):
    """DM every notify subscriber that a new player joined. The game host (owner_id)
    gets a host-specific line (they can start the game); everyone else is told they'll
    be pinged in the thread. Raw-id DMs (subscribers may have no Profile/SocialAccount);
    Discord's 403 is swallowed per id."""
    from the_databot.services.discordservice import send_dm_by_id
    game = f"*{description}*" if description else "your game"
    link = f" {jump_url}" if jump_url else ""
    for uid in notify_ids:
        if owner_id and str(uid) == str(owner_id):
            content = (f"**{joiner_name}** joined {game}.{link}\n"
                       "When it's full, press ✅ to start the thread and ping each player.")
        else:
            content = (f"**{joiner_name}** joined {game}.{link}\n"
                       "You'll be pinged in the game thread when it starts.")
        send_dm_by_id(uid, content=content)


@shared_task
def notify_lfg_cancelled_task(notify_ids, host_name, description, jump_url=None):
    """DM every notify subscriber that the host cancelled the game, so they stop
    waiting on it. The host is excluded by the CALLER -- they're the one who
    cancelled. Raw-id DMs (subscribers may have no Profile/SocialAccount);
    Discord's 403 is swallowed per id. Mirrors notify_lfg_task."""
    from the_databot.services.discordservice import send_dm_by_id
    game = f"*{description}*" if description else "the game"
    link = f" {jump_url}" if jump_url else ""
    # Fall back to "The host" rather than emitting an empty bold "** cancelled".
    host = f"**{host_name}**" if host_name else "The host"
    for uid in notify_ids:
        send_dm_by_id(uid, content=(f"{host} cancelled {game}.{link}\n"
                                    "Use `/lfg` to start a new game."))


# How many names a closed-poll DM lists before summarizing. A poll with no roster
# has no upper bound on responders, and a DM naming thirty people is unreadable.
_DM_NAME_MAX = 4


def _summarize_names(names, limit=_DM_NAME_MAX):
    """"A, B and C" — or "A, B, C, D + 3 more" once past the cap.

    Bolded per name, matching the declined line these sit beside."""
    names = [f"**{n}**" for n in names]
    if len(names) <= limit:
        return name_join(names)
    return f"{', '.join(names[:limit])} + {len(names) - limit} more"


@shared_task
def notify_schedule_poll_task(notify_ids, event, when_ts, actor_name=None,
                              yes_count=0, total=None, declined=None,
                              scheduled=False, jump_url=None, confirmed=None):
    """DM the 🔔 subscribers of a /schedule poll.

    `event` is "yes" (someone just confirmed, with a running count) or "closed"
    (the final result). The actor is excluded by the CALLER, as in the lfg notify
    tasks. Raw-id DMs -- a subscriber need not have a Profile at all, which is
    also why the notify list lives in the embed rather than an M2M.

    The time is re-rendered as a Discord timestamp from the epoch so each
    recipient reads it in their OWN timezone; a preformatted string would show
    the sender's."""
    from the_databot.services.discordservice import send_dm_by_id
    from the_databot.services.time_parsing import format_discord_timestamp

    when = format_discord_timestamp(
        datetime.fromtimestamp(int(when_ts), tz=dt_timezone.utc))
    link = f"\n{jump_url}" if jump_url else ""

    if event == "yes":
        who = f"**{actor_name}**" if actor_name else "Someone"
        tally = (f" — {yes_count} of {total} players confirmed." if total
                 else f" — {yes_count} confirmed so far.")
        content = f"{who} confirmed for {when}.{tally}{link}"
    else:
        if scheduled:
            content = f"The poll for {when} closed — everyone confirmed. ✅{link}"
        elif declined and total is not None:
            # A poll with a roster: every player had to agree, so one decline
            # means no time could be set. `total is not None` IS that test --
            # the same tri-state the "yes" branch above reads.
            names = ", ".join(f"**{n}**" for n in declined)
            content = (f"The poll for {when} closed — {names} couldn't make it, so "
                       f"no time was scheduled. Run `/schedule set` to propose "
                       f"another.{link}")
        elif declined and confirmed:
            # No roster, so nothing was ever going to be booked and a decline
            # vetoes nothing. Who CAN make it is the actual result.
            content = (f"The poll for {when} closed — "
                       f"{_summarize_names(confirmed)} can make it.{link}")
        elif declined:
            content = f"The poll for {when} closed — nobody could make it.{link}"
        else:
            # The count replaces "before everyone responded" -- it says the same
            # thing precisely, and reads correctly whether or not a roster
            # exists. total=None means there was nobody to compare against, the
            # same convention the "yes" branch above uses.
            tally = (f"{yes_count} of {total} confirmed" if total
                     else f"{yes_count} confirmed")
            content = f"The poll for {when} closed with {tally}.{link}"

    for uid in notify_ids:
        send_dm_by_id(uid, content=content)


@shared_task
def create_match_threads_task(round_id, profile_id, tournament_id):
    """Create one forum thread per un-threaded MatchSeries in a round, ping its players,
    and link the thread back to the PlayerGroup. Reports the outcome as a
    UserNotification pointing at the matches page.

    Async because a round can hold many series and each thread is a blocking Discord
    call. Permission was checked by the enqueueing view; this re-reads the tournament's
    channel through post-time verification rather than trusting an id passed in.
    """
    from the_warroom.models import Round, MatchSeries, Tournament
    from the_warroom.services.channel_posts import resolve_tournament_channel
    from the_databot.services.discordservice import create_forum_thread_result
    from the_databot.services.lfg_game import group_roster, link_group_thread

    round = Round.objects.filter(pk=round_id).select_related('stage').first()
    tournament = Tournament.objects.filter(pk=tournament_id).select_related('guild').first()
    profile = Profile.objects.filter(pk=profile_id).first()
    if not (round and tournament and profile):
        logger.warning("create_match_threads_task: round/tournament/profile missing "
                       "(%s/%s/%s)", round_id, tournament_id, profile_id)
        return

    # Re-verify at send time: the tournament may have been re-pointed at another guild
    # between the click and this task running, and a stale channel id would create
    # threads in the wrong server.
    forum_id = resolve_tournament_channel(tournament, 'game_threads_channel')
    if not forum_id:
        UserNotification.create_notification(
            profile,
            "Couldn't create game threads: this series has no valid game-threads "
            "forum channel for its Discord server.",
            message_type=MessageChoices.WARNING,
            related_url=round.get_matches_url())
        return

    guild_snowflake = tournament.guild.guild_id
    # Forums with Discord's "require tag" flag reject every post without applied_tags,
    # so the tag is not decoration -- omitting it fails the whole batch with a 400.
    tag_id = tournament.game_threads_tag
    created = skipped = failed = 0
    # discord_thread is blank=True WITHOUT null=True -- unlinked is "", never NULL.
    # An __isnull=True filter here would match nothing at all.
    series_qs = (MatchSeries.objects
                 .filter(round=round, player_group__isnull=False,
                         player_group__discord_thread='')
                 .select_related('player_group')
                 .order_by('id'))

    rate_limited = False
    bad_request = False
    for index, series in enumerate(series_qs):
        group = series.player_group
        # Re-check under the current row: the page's count may be minutes stale, and
        # another run of this task may have linked it since the queryset was built.
        group.refresh_from_db(fields=['discord_thread'])
        if group.discord_thread:
            skipped += 1
            continue

        roster = group_roster(group, series_id=series.id)
        # discord_id is the snowflake; Profile.discord is a legacy username and must
        # never be mentioned with. A player who never linked Discord can't be pinged,
        # but must still be NAMED -- dropping them made the roster look short and left
        # them wondering whether they were in the match. str(profile) is the same
        # "Display Name (discord)" the record-game player dropdowns use, collapsing to
        # one name when the two match.
        mentions = [f"<@{p.discord_id}>" if p.discord_id else str(p) for p in roster]
        pings = " ".join(mentions)
        title = group.name or f"Group {group.group_number}"
        content = (f"{pings} your match is ready!".strip() if pings
                   else "Your match is ready!")

        # Space the requests out. A big round is dozens of thread creations, and
        # firing them back to back is what trips Discord's limit in the first place.
        # Skipped groups cost nothing, so only pause before an actual API call, and
        # never before the first one.
        if index:
            time.sleep(_THREAD_CREATE_INTERVAL)

        thread_id, retry_after, status = create_forum_thread_result(
            forum_id, title, content=content, tag_id=tag_id)

        # A 429 is not a failure -- the request was refused for pacing, and the same
        # call will succeed after the wait. Without this the remaining groups would
        # each burn one request against the same limit and be reported as "failed".
        if thread_id is None and retry_after is not None:
            rate_limited = True
            wait = min(retry_after, _THREAD_CREATE_MAX_BACKOFF)
            logger.warning("create_match_threads_task: rate limited, waiting %ss", wait)
            time.sleep(wait)
            # The retry must carry the tag too -- without it a rate-limited item would
            # fail again for an entirely unrelated reason.
            thread_id, retry_after, status = create_forum_thread_result(
                forum_id, title, content=content, tag_id=tag_id)

        if not thread_id:
            failed += 1
            if status == 400:
                bad_request = True
            continue
        # Compare-and-swap on discord_thread="" -- never clobbers a link written
        # concurrently, and builds the exact URL shape match_thread_id parses back.
        if link_group_thread(group, guild_snowflake, thread_id):
            created += 1
        else:
            skipped += 1

    parts = [f"{created} game thread{'' if created == 1 else 's'} created"]
    if skipped:
        parts.append(f"{skipped} skipped (already had a thread)")
    if failed:
        parts.append(f"{failed} failed")
    message = f"{round.name}: " + ", ".join(parts) + "."
    if failed and rate_limited:
        # Tell the user WHY, so a retry looks worthwhile rather than pointless.
        message += (" Discord rate limited the batch — press Create Game Threads "
                    "again to finish the rest.")
    elif failed and bad_request and not tag_id:
        # The overwhelmingly common 400 here: the forum has "require tag when posting"
        # set and we sent no applied_tags. Retrying is pointless until a tag is chosen,
        # so name the fix instead of inviting another identical failure.
        message += (" Discord rejected the posts — this forum may require a tag. "
                    "Set a game threads tag for this series in Edit Guild, then try "
                    "again.")
    UserNotification.create_notification(
        profile, message,
        message_type=(MessageChoices.WARNING if failed else MessageChoices.SUCCESS),
        related_url=round.get_matches_url())


@shared_task
def create_lfg_thread_task(channel_id, message_id, guild_id, role_id, description,
                           players, embed=None, token=None, host_id=None,
                           in_thread=False, send_kickoff=True, role_pk=None,
                           role_name=""):
    """Create the game thread, ping the players, link the original message's title
    to the thread, and persist the LFGThread row. `players` = [{"id","name"}] parsed
    from the Players field lines, so this task resolves-or-creates every Profile
    itself (no dependency on Join-time onboarding).

    `host_id` is the Discord snowflake of whoever ran /lfg, recorded on the thread
    so /rename can tell who may retitle it. Keyword-defaulted so a task enqueued
    before this argument existed still deserializes.

    If the game's LFG role has a `forum_channel_id`, the thread is created as a post
    in that forum channel; otherwise it hangs off the LFG message. A role's optional
    `thread_message` is appended to the kickoff ping.

    `in_thread` means /lfg was run inside a thread, so no thread is created at all:
    that thread IS the game thread and is adopted as-is. Keyword-defaulted like
    `host_id`, so a task enqueued before this argument existed still deserializes.

    `role_name` is the tag name recovered from the LFG message's own content, sent
    ONLY when the message carried no role mention -- i.e. a display-only tag, which
    cannot be resolved from `role_id` here. It exists so the nickname discriminator
    below can still recognise a tag-name title as a fallback rather than something
    the host typed. Keyword-defaulted for the same deserialization reason."""
    from the_databot.services.discordservice import (
        create_message_thread, create_forum_thread, post_channel_message,
        apply_thread_tag,
    )
    guild = DiscordGuild.objects.filter(guild_id=guild_id).first() if guild_id else None
    # `role_pk` is preferred over `role_id` when supplied: role_id is the DISCORD
    # snowflake and is nullable ("leave blank if you only want the display tag"),
    # so a display-only tag can't be found by it -- and those are exactly the tags
    # most likely to exist purely to carry a `tournament`, which is what /record
    # reads to resolve a series.
    if role_pk and guild:
        role = GuildLFGRole.objects.filter(pk=role_pk, guild=guild).first()
    else:
        role = (GuildLFGRole.objects.filter(guild=guild, role_id=role_id).first()
                if role_id and guild else None)

    pings = " ".join(f"<@{p['id']}>" for p in players)
    kickoff = f"{pings} your game can start!".strip()
    if role and role.thread_message:
        kickoff = f"{kickoff} {role.thread_message}".strip()

    # The LFG message's own title, which /lfg already resolved as
    # `title option -> tag name -> "Looking for Game"`. `embed` is the started
    # message's embed, so the thread is named exactly what players already saw.
    #
    # The free-text `description` deliberately does NOT name the thread: it answers
    # "what kind of game", which makes a poor title, and the `title` option now
    # exists for naming.
    embed_title = (embed or {}).get("title") or ""
    thread_name = (embed_title or "Game")[:100]

    # Did the host actually NAME this game, or is the embed title just a fallback?
    # The title is always populated, so writing it to `nickname` unconditionally
    # would give every game a name like "Looking for Game" -- and since the record
    # form seeds from `nickname`, that would shadow a real name with boilerplate.
    #
    # Everything /lfg could have defaulted to is collected here and treated as "not
    # a name". role.description is included so tasks queued before the title option
    # existed (their embeds were titled from the blurb) still resolve to blank.
    fallback_titles = {"", LFG_DEFAULT_TITLE}
    if role:
        fallback_titles |= {role.name, role.description}
    elif role_name:
        # Display-only tag: unresolvable here, so the caller sent its name instead.
        fallback_titles.add(role_name)
    typed_title = "" if embed_title in fallback_titles else embed_title

    if in_thread:
        # /lfg was run inside a thread: that thread IS the game thread. Discord
        # cannot nest a thread on a message already inside one -- attempting it is
        # what used to fail here -- and the host asked for the game right here.
        # A thread's channel id IS its own id, so channel_id already names it.
        #
        # This mirrors what a freshly created thread receives: the same kickoff,
        # posted into the thread everyone is already reading.
        thread_id = channel_id
        # /adset passes send_kickoff=False: everyone is already in the thread and
        # just clicked Join, so the ping is noise -- and it would ping the table a
        # second time moments after the join gate.
        if send_kickoff:
            post_channel_message(thread_id, kickoff)
        # Adopting an existing forum post means its tag wasn't set at creation, so
        # apply it now. Best-effort: a tag is decoration and must not cost the game
        # its thread (apply_thread_tag logs and swallows its own failures).
        if role and role.forum_tag_id:
            apply_thread_tag(thread_id, role.forum_tag_id)
    elif role and role.forum_channel_id:
        # Forum post: the starter message carries the kickoff ping (+ the game embed
        # for context). No parent message to hang off of.
        forum_embed = None
        if embed is not None:
            forum_embed = dict(embed)
            forum_embed["url"] = _lfg_message_jump_url(guild_id, channel_id, message_id)
        thread_id = create_forum_thread(role.forum_channel_id, thread_name, content=kickoff,
                                        embeds=[forum_embed] if forum_embed else None,
                                        tag_id=role.forum_tag_id)
    else:
        thread_id = create_message_thread(channel_id, message_id, thread_name)
        if thread_id:
            post_channel_message(thread_id, kickoff)

    if not thread_id:
        # Thread creation failed (already logged with the Discord error). The message
        # was optimistically flipped to "started" in the synchronous response, so tell
        # the owner privately why no thread appeared. Ephemeral follow-up needs the
        # interaction token; skip if we weren't given one.
        if token:
            notice = "Couldn't create the game thread — check my permissions in this channel."
            if not (role and role.forum_channel_id):
                notice += " I likely need **Create Public Threads** and **Send Messages in Threads** here."
            post_interaction_followup_task.delay(token, {"content": notice, "flags": EPHEMERAL})
        return  # no thread → nothing to persist

    # Link the original message's title to the new thread (embed titles support a url).
    # Dispatched with a countdown to settle the race with ✔ Start's own interaction
    # edit — see link_lfg_message_task. Enqueued BEFORE the DB write below so a
    # persistence failure can't also cost the user the title link.
    if embed is not None:
        gid = guild_id or "@me"
        link_lfg_message_task.apply_async(
            (channel_id, message_id, embed,
             f"https://discord.com/channels/{gid}/{thread_id}"),
            countdown=2,
        )

    thread, created = LFGThread.objects.get_or_create(
        thread_id=thread_id,
        # nickname seeds the recorded game's name. In `defaults` (create-only) for
        # the same reason description is: a task retry must not clobber a /rename
        # the host has since run. Truncated because nickname is max_length=50 and
        # Postgres raises on overflow rather than truncating.
        defaults={"guild": guild, "lfg_role": role,
                  "description": description or "",
                  "nickname": typed_title[:50]},
    )
    # An adopted thread that ALREADY had a row is not ours to write to: players.set
    # below would replace the existing game's roster wholesale. /lfg refuses in a
    # linked thread, so the only way here is a race -- two /lfg in one thread, both
    # started before either persisted. Bail, keeping the first game intact; the
    # kickoff already posted above is harmless.
    #
    # Deliberately NOT applied to the other paths: there, created=False means a task
    # retry on a thread this same game created, where re-running these writes is the
    # correct idempotent behaviour.
    if in_thread and not created:
        logger.warning("LFG thread %s already linked; refusing to adopt (race with "
                       "another /lfg in this thread)", thread_id)
        return
    # Resolve-or-create each player's Profile synchronously (display name is in the
    # embed line), so players.set attaches everyone — no reliance on Join-time tasks.
    if not players:
        logger.warning("LFG thread %s: no players parsed from the embed", thread_id)
    profiles = [ensure_profile_from_discord(p["id"], None, p.get("name")) for p in players]
    resolved = [p for p in profiles if p]
    if len(resolved) != len(players):
        unresolved = [p["id"] for p, prof in zip(players, profiles) if not prof]
        logger.error("LFG thread %s: resolved %d/%d player profiles; unresolved ids=%s",
                     thread_id, len(resolved), len(players), unresolved)
    thread.players.set(resolved)
    logger.info("LFG thread %s: attached %d players", thread_id, len(resolved))

    # AFTER the players loop, not before: the host is one of the players, and
    # ensure_profile_from_discord above is what CREATES a Profile for a first-time
    # user. Looking up earlier would miss exactly that case and leave host NULL.
    #
    # Set outside `defaults`, which only applies on CREATE -- this get_or_create
    # exists because the row may already be there (a retried task, or a thread that
    # captured a roll first), and on that path defaults are ignored entirely. The
    # host_id guard keeps a retry from reassigning a host already recorded.
    if host_id and not thread.host_id:
        host = Profile.objects.filter(discord_id=str(host_id)).first()
        if host:
            thread.host = host
            thread.save(update_fields=["host"])
        else:
            logger.warning("LFG thread %s: could not resolve host %s", thread_id, host_id)


def _lfg_message_jump_url(guild_id, channel_id, message_id):
    gid = guild_id or "@me"
    return f"https://discord.com/channels/{gid}/{channel_id}/{message_id}"


@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={'max_retries': 3, 'countdown': 5},
)
def link_lfg_message_task(channel_id, message_id, embed, thread_url):
    """Point the started LFG message's embed title at the new game thread.

    Split out of create_lfg_thread_task and dispatched with a small countdown to
    settle a race: ✔ Start answers the interaction with RESPONSE_UPDATE_MESSAGE,
    but the embed Celery serialized at .delay() time predates `url`, so whichever
    write Discord applies LAST wins. The countdown lets the interaction's own edit
    land first, and this PATCH re-sends `components: []` so it's a strict superset
    of that edit and wins on content regardless of arrival order.

    Unlike thread creation this edit is idempotent, so retrying is safe — but only
    for transient failures; a 403/404 is permanent and must not burn retries."""
    from the_databot.services.discordservice import (
        edit_channel_message, THREAD_OK, THREAD_BLOCKED, THREAD_ERROR,
    )
    embed = dict(embed or {})
    embed["url"] = thread_url
    result = edit_channel_message(channel_id, message_id, embeds=[embed], components=[])
    if result == THREAD_BLOCKED:
        # Permanent (message deleted, or we may not edit components this way).
        # Fall back to an embeds-only edit so we still land the title link rather
        # than leaving the message worse off than before this task existed.
        if edit_channel_message(channel_id, message_id, embeds=[embed]) == THREAD_OK:
            logger.warning("LFG link: components edit rejected for message %s; "
                           "fell back to embeds-only", message_id)
        return
    if result == THREAD_ERROR:
        raise RuntimeError(f"transient failure linking LFG message {message_id}")


# kinds whose result is one of the Game's direct FKs (latest selection/roll wins)
_LFG_FK_KINDS = {"Map": "map", "Deck": "deck"}


@shared_task
def record_lfg_components_task(channel_id, items, source="", draft=None,
                               undrafted=None):
    """Record components surfaced inside an LFG thread (from /random, /map, /deck,
    other lookups, /draft). No-op when the channel isn't a known LFG thread.

    `source` tags where the items came from (random / lookup / draft). `draft`,
    when given, REPLACES the thread's current draft: {"players", "platform",
    "drafted_by": <discord id>, "picks": [{"faction","vagabond","captains",
    "order"}]}. Everything on the wire is slugs and ids — Celery serializes as
    JSON, so model instances would raise EncodeError in the caller.

    `source` and `draft` are keyword-defaulted so the other capture call sites
    keep working and so tasks enqueued by older code still deserialize.
    """
    if not channel_id or not (items or draft):
        return
    # select_for_update still earns its place: the map/deck update below is a
    # read-modify-write, and the draft replacement must not interleave with a
    # concurrent one. (The roll rows themselves are plain inserts and don't race.)
    with transaction.atomic():
        thread = LFGThread.objects.select_for_update().filter(thread_id=channel_id).first()
        if not thread:
            # A tournament series' group thread captures exactly the same way once
            # it has a row, so create one on first use. Only for a channel that
            # resolves to a player group with a series -- every /random in every
            # channel reaches this task and most are neither.
            #
            # getattr, NOT `group.series`: MatchSeries.player_group is a OneToOne,
            # so the reverse accessor RAISES RelatedObjectDoesNotExist when the
            # group has no series (a third of them don't). This task has no
            # autoretry, so a raise here would silently lose the capture.
            # Id-only on purpose: this task's payload carries no channel NAME, so
            # the title fallback isn't available here, and adding a parameter would
            # change the wire format for tasks already queued by older code. It
            # doesn't need one -- /schedule, /seating and /pick all link the thread
            # to the group on first use, so by the time captures matter the group
            # resolves by id anyway.
            from the_databot.services.lfg_game import player_group_for_channel
            group = player_group_for_channel(channel_id)
            series = getattr(group, "series", None) if group else None
            if not series:
                return  # not a capturing thread (the common case) — no-op
            thread, _ = LFGThread.objects.get_or_create(
                thread_id=channel_id, defaults={"series": series})
            # Re-fetch under the lock the rest of this block assumes.
            thread = LFGThread.objects.select_for_update().get(pk=thread.pk)

        items = items or []
        # One query for every rolled component. NOTE this dict holds base Post
        # instances and must NOT be reused for the typed FKs below (map/deck/
        # faction): MTI forbids assigning a parent instance to a child FK.
        slugs = [it.get("slug") for it in items if it.get("slug")]
        posts_by_slug = {}
        if slugs:
            posts_by_slug = {p.slug: p for p in Post.objects.filter(slug__in=slugs)}

        rolls = []
        for it in items:
            kind, slug = it.get("kind"), it.get("slug")
            # created_at is deliberately not passed: letting the field default
            # fire per row keeps draw order unambiguous.
            rolls.append(LFGRoll(thread=thread, kind=kind or "", slug=slug or "",
                                 post=posts_by_slug.get(slug), source=source or ""))
        if rolls:
            LFGRoll.objects.bulk_create(rolls)

        # map/deck keep their own typed lookup -- posts_by_slug holds Post rows,
        # and `thread.map = <Post>` raises ValueError under multi-table inheritance.
        touched = []
        for it in items:
            field = _LFG_FK_KINDS.get(it.get("kind"))
            slug = it.get("slug")
            if field and slug:
                model = {"map": Map, "deck": Deck}[field]
                obj = model.objects.filter(slug=slug).first()
                if obj:
                    setattr(thread, field, obj)
                    touched.append(field)
        # The undrafted assets get their own columns rather than riding
        # _LFG_FK_KINDS: that maps kind -> field, and an undrafted faction shares
        # kind "Faction" with every seated one, so nothing there could tell them
        # apart. Slugs are pre-validated by the caller.
        captain_slugs = None
        if undrafted:
            from the_keep.models import Faction, Vagabond
            if undrafted.get("faction"):
                obj = Faction.objects.filter(slug=undrafted["faction"]).first()
                if obj:
                    thread.undrafted_faction = obj
                    touched.append("undrafted_faction")
            if undrafted.get("vagabond"):
                obj = Vagabond.objects.filter(slug=undrafted["vagabond"]).first()
                if obj:
                    thread.undrafted_vagabond = obj
                    touched.append("undrafted_vagabond")
            captain_slugs = undrafted.get("captains")

        # Saved even when nothing was touched: the rolls above are children, so
        # without this a /random faction roll would leave last_activity stale and
        # age an actively-used thread toward cleanup. save() supplies the field.
        thread.save(update_fields=sorted(set(touched)))

        # AFTER the save, and never via update_fields: an M2M is not a column on
        # this row, and cannot be assigned before it exists.
        if captain_slugs:
            from the_keep.models import Vagabond
            thread.undrafted_captains.set(
                Vagabond.objects.filter(slug__in=captain_slugs))

        if draft:
            _replace_lfg_draft(thread, draft)


def _replace_lfg_draft(thread, draft):
    """Replace the thread's current draft with `draft` (a JSON-safe dict).

    A thread holds ONE draft: re-running /draft supersedes the previous one
    rather than accumulating. Runs inside record_lfg_components_task's locked
    transaction. Unresolvable slugs are skipped and logged rather than aborting
    the batch -- a bad slug must never cost the user their delivered draft.
    """
    picks = draft.get("picks") or []
    faction_slugs = [p.get("faction") for p in picks if p.get("faction")]
    # Typed querysets, NOT the Post dict from the caller (see the MTI note above).
    factions = {f.slug: f for f in Faction.objects.filter(slug__in=faction_slugs)}

    vb_slugs = set()
    for p in picks:
        if p.get("vagabond"):
            vb_slugs.add(p["vagabond"])
        vb_slugs.update(p.get("captains") or [])
    vagabonds = {v.slug: v for v in Vagabond.objects.filter(slug__in=vb_slugs)} if vb_slugs else {}

    drafted_by = None
    if draft.get("drafted_by"):
        drafted_by = Profile.objects.filter(discord_id=str(draft["drafted_by"])).first()

    obj, _ = LFGDraft.objects.update_or_create(
        thread=thread,
        defaults={"players": draft.get("players"),
                  "platform": draft.get("platform") or "",
                  "drafted_by": drafted_by},
    )
    obj.picks.all().delete()

    for p in picks:
        faction = factions.get(p.get("faction"))
        if not faction:
            logger.warning("LFG draft: unknown faction slug %r for thread %s",
                           p.get("faction"), thread.thread_id)
            continue
        pick = LFGDraftPick.objects.create(
            draft=obj, faction=faction, order=p.get("order") or 0,
            vagabond=vagabonds.get(p.get("vagabond")) if p.get("vagabond") else None,
        )
        caps = [vagabonds[s] for s in (p.get("captains") or []) if s in vagabonds]
        if caps:
            pick.captains.set(caps)


# ── /schedule proposals ──────────────────────────────────────────────────────
# A proposal's public message is posted by the BOT (not as an interaction followup)
# so it stays editable indefinitely: an interaction token expires after 15 minutes
# and proposals routinely outlive that.

# Copy for a proposal retired without anyone rejecting it, keyed by the `reason`
# the caller passes. Defined in services.lfg_game alongside the embed renderer that
# consumes it (imported at the top of this module), so the interaction path and
# this task cannot drift apart on what a closed proposal says. The alias keeps the
# old private name working for existing importers.
_PROPOSAL_RETIRED_TEXT = PROPOSAL_RETIRED_TEXT


@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={'max_retries': 3, 'countdown': 5},
)
def post_schedule_proposal_task(proposal_id, message_data):
    """Post a schedule proposal's public message and record its id on the row.

    The id is the whole point: without it the proposal can't be edited later
    (superseded/cancelled) from outside its own interaction.

    Bails out when the proposal stopped being LIVE while the task sat queued — e.g.
    another proposal finalized first. Posting live buttons for a dead proposal would
    hand someone a control that could overwrite a confirmed time.

    is_live, NOT is_open: a fast roster can confirm everything before this task
    runs, leaving the row AGREED and awaiting a moderator. Bailing on that would
    never post the message at all, stranding the proposal with no message_id --
    so nothing could later edit its buttons or sweep it."""
    from the_databot.models import ScheduleProposal
    from the_databot.services.discordservice import (
        post_channel_message_full, THREAD_OK, THREAD_ERROR,
    )

    proposal = ScheduleProposal.objects.filter(pk=proposal_id).first()
    if not proposal or not proposal.is_live or not proposal.channel_id:
        return

    result, message_id = post_channel_message_full(
        proposal.channel_id, **(message_data or {}))
    if result == THREAD_ERROR:
        # Transient: let autoretry have another go.
        raise RuntimeError(f"transient failure posting proposal {proposal_id}")
    if result == THREAD_OK and message_id:
        # .update() rather than .save(): never clobber a status another request
        # changed while this task was in flight.
        ScheduleProposal.objects.filter(pk=proposal_id).update(message_id=message_id)


@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={'max_retries': 3, 'countdown': 10},
)
def strip_schedule_proposal_messages_task(proposal_ids, reason):
    """Remove the buttons from retired proposal messages and replace the embed with
    a short explanation.

    Purely cosmetic: the DB status is what actually stops a retired proposal being
    confirmed, so a PERMANENT failure (message deleted, missing perms) is logged and
    skipped rather than retried. Only transient failures re-raise, and only after
    every id has been attempted — one dead message must not block the rest."""
    from the_databot.models import ScheduleProposal
    from the_databot.services.discordservice import (
        edit_channel_message, THREAD_ERROR,
    )

    transient = []
    # select_related/prefetch_related: the embed now reads the proposer and the
    # confirmations for every id in the batch, which would otherwise be two extra
    # queries per proposal.
    proposals = (ScheduleProposal.objects
                 .filter(pk__in=list(proposal_ids or []))
                 .select_related("proposed_by", "match",
                                 "match__series__player_group")
                 # rejected_by joined the embed when a "No" became a vote rather
                 # than a termination. It is an M2M, so without it here the
                 # closed-poll render costs an extra query per proposal.
                 .prefetch_related("confirmed_by", "rejected_by"))
    for proposal in proposals:
        if not proposal.channel_id or not proposal.message_id:
            continue  # never posted (or the id never landed) — nothing to strip
        # No actor: every reason reaching this task (superseded, website, expired,
        # cancelled) is a consequence rather than someone's decision about THIS
        # proposal, so none of them may name a person.
        result = edit_channel_message(
            proposal.channel_id, proposal.message_id,
            embeds=[schedule_closed_embed(
                proposal, "Proposal closed", reason)],
            components=[],
        )
        if result == THREAD_ERROR:
            transient.append(proposal.pk)
    if transient:
        raise RuntimeError(f"transient failure stripping proposals {transient}")


@shared_task
def cleanup_stale_schedule_proposals(max_age_days=14):
    """Retire OPEN proposals that can never complete: ones whose proposed time has
    passed, and ones left open longer than `max_age_days`.

    Runs on a schedule created in Django admin (django_celery_beat) — this project
    uses DatabaseScheduler, so there is no beat_schedule in code to register it."""
    from the_databot.models import ScheduleProposal

    now = timezone.now()
    stale = ScheduleProposal.objects.filter(
        status__in=ScheduleProposal.LIVE_STATUSES,
    ).filter(
        Q(proposed_time__lt=now)
        | Q(created_at__lt=now - timedelta(days=max_age_days))
    )
    ids = list(stale.values_list("pk", flat=True))
    if not ids:
        return 0
    ScheduleProposal.objects.filter(pk__in=ids).update(
        status=ScheduleProposal.Status.CANCELLED, resolved_at=now)
    strip_schedule_proposal_messages_task.delay(ids, "expired")
    logger.info("Retired %d stale schedule proposals", len(ids))
    return len(ids)


# Deletion chunk size. Each LFGThread drags four cascade tables (roll_log,
# draft -> picks, seats), so the rows deleted are a multiple of this.
_LFG_DELETE_CHUNK = 200


@shared_task
def cleanup_stale_lfg_threads(recorded_after_days=30, stale_after_days=180,
                              limit=None, dry_run=False):
    """Delete LFGThread rows that no longer have a job to do, and their captured
    roll/draft/seat history with them (all CASCADE).

    Two independently-tunable windows, because "done" and "abandoned" are
    different risks:

      recorded_after_days -- the thread's game is recorded and final. NOT deleted
        immediately: /record's only duplicate guard is `if thread.game_id`
        (_handle_record_command), so removing the row the day a game is recorded
        means the next /record in that Discord thread offers a blank form and
        invites a duplicate Game.

      stale_after_days -- an abandoned thread. Keyed on `last_activity`, not
        `created_at`: a long-running game is not a dead one.

    Keyed on STATUS, not game_id, and that distinction is load-bearing: a
    save-progress draft game sets `game` but leaves status OPEN
    (the_warroom/views.py, the lfg_mode block), so keying on game_id would delete
    a thread whose game is still being written. CANCELLED rides along with
    RECORDED -- nothing writes it today, but if something starts to, the short
    window is the behaviour it should inherit.

    Tournament group threads (`series` set) get no exemption: they follow the
    same idle rule as everything else.

    The recorded Game itself SURVIVES: LFGThread.game is a OneToOne with
    SET_NULL, so only the scaffolding that fed the game form is discarded.

    dry_run returns the count without deleting; limit caps a single run,
    oldest-first, for a cautious first pass.

    Runs on a schedule created in Django admin (django_celery_beat) -- this
    project uses DatabaseScheduler, so there is no beat_schedule in code.
    Returns the number of threads deleted (or that would be)."""
    now = timezone.now()

    done = Q(
        status__in=[LFGThread.Status.RECORDED, LFGThread.Status.CANCELLED],
        last_activity__lt=now - timedelta(days=recorded_after_days),
    )
    abandoned = Q(last_activity__lt=now - timedelta(days=stale_after_days))

    ids = list(LFGThread.objects.filter(done | abandoned)
               .order_by("last_activity").values_list("pk", flat=True))
    if limit:
        ids = ids[:int(limit)]
    if not ids:
        return 0

    if dry_run:
        logger.info("cleanup_stale_lfg_threads DRY RUN: would delete %d threads "
                    "(recorded_after_days=%s stale_after_days=%s)",
                    len(ids), recorded_after_days, stale_after_days)
        return len(ids)

    deleted = 0
    # Chunked, one transaction each: a large run cascades into four tables, and a
    # partial success is fine for an idempotent cleanup that reruns weekly --
    # holding those locks across the whole set is not.
    for start in range(0, len(ids), _LFG_DELETE_CHUNK):
        chunk = ids[start:start + _LFG_DELETE_CHUNK]
        with transaction.atomic():
            count, _by_model = LFGThread.objects.filter(pk__in=chunk).delete()
        deleted += len(chunk)
        logger.debug("Deleted LFG thread chunk of %d (%d rows incl. children)",
                     len(chunk), count)

    logger.info("Deleted %d stale LFG threads (recorded_after_days=%s "
                "stale_after_days=%s limit=%s)",
                deleted, recorded_after_days, stale_after_days, limit)
    return deleted


# ── Box score uploads from Tabletop Simulator ───────────────────────────────

@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={'max_retries': 3, 'countdown': 10},
)
def post_boxscore_prompt_task(token_pk, message_data):
    """Post an upload's Confirm/Cancel prompt into its game thread, recording the
    message id on the row.

    The id is the whole point: without it the sweep task cannot later strip the
    buttons off a prompt nobody answered, leaving a dead control in the thread.

    Bails out when the upload stopped being PENDING while this sat queued -- a
    fast roster can confirm from another prompt, and posting live buttons for a
    resolved upload would hand someone a control that could overwrite it.
    """
    from the_databot.models import BoxScoreUploadToken
    from the_databot.services.discordservice import (
        post_channel_message_full, THREAD_OK, THREAD_ERROR,
    )

    token = BoxScoreUploadToken.objects.filter(pk=token_pk).first()
    if not token or token.status != BoxScoreUploadToken.Status.PENDING:
        return
    if not token.channel_id:
        return

    result, message_id = post_channel_message_full(
        token.channel_id, **(message_data or {}))
    if result == THREAD_ERROR:
        raise RuntimeError(f"transient failure posting boxscore prompt {token_pk}")
    if result == THREAD_OK and message_id:
        # .update() rather than .save(): never clobber a status another request
        # changed while this task was in flight.
        BoxScoreUploadToken.objects.filter(pk=token_pk).update(message_id=message_id)


@shared_task
def sweep_boxscore_upload_tokens(remind_within_minutes=60, prune_after_days=None):
    """Remind, expire and prune box-score upload tokens.

    Runs on a schedule created in Django admin (django_celery_beat) -- this
    project uses DatabaseScheduler, so there is no beat_schedule in code to
    register it. Every 10-15 minutes is about right.

    Three passes:
      1. REMIND the person who minted the token, once, so the prompt isn't
         missed. Only they (plus host/mods) can answer it.
      2. EXPIRE a lapsed prompt AND strip its buttons, so no dead control is
         left in the thread inviting a click that can't work.
      3. PRUNE resolved rows, and tokens minted but never used.

    prune_after_days defaults to None rather than a literal so it can resolve
    from BoxScoreUploadToken.PAYLOAD_RETENTION_DAYS below -- a default argument
    would evaluate at import time, forcing a module-level models import this
    file deliberately avoids.
    """
    from the_databot.models import BoxScoreUploadToken
    from the_databot.services.discordservice import (
        edit_channel_message, post_channel_message_full, THREAD_ERROR,
    )

    if prune_after_days is None:
        prune_after_days = BoxScoreUploadToken.PAYLOAD_RETENTION_DAYS

    now = timezone.now()
    reminded = expired = 0

    # 1) Remind.
    soon = now + timedelta(minutes=remind_within_minutes)
    for token in BoxScoreUploadToken.objects.filter(
            status=BoxScoreUploadToken.Status.PENDING,
            reminded_at__isnull=True,
            prompt_expires_at__lte=soon,
            prompt_expires_at__gt=now).select_related("thread", "issued_by"):
        # Ping the issuer, not the roster: the prompt is locked to them plus
        # host/mods, so a roster ping would notify people who would be refused.
        # issued_by is SET_NULL and discord_id is nullable AND blankable, so fall
        # back to the roster rather than sending an unaddressed reminder -- a
        # game with an untraceable uploader still needs finishing.
        issuer_id = token.issued_by.discord_id if token.issued_by else None
        if issuer_id:
            who = f"<@{issuer_id}>"
        else:
            mentions = [f"<@{p.discord_id}>"
                        for p in token.thread.players.all() if p.discord_id]
            who = " ".join(mentions) if mentions else "Players"
        post_channel_message_full(
            token.channel_id,
            content=(f"{who} — a box score from Tabletop Simulator is still "
                     "waiting to be confirmed above. It expires soon."),
            # parse: ["users"] so this actually pings, unlike the box-score
            # summaries, which deliberately name players without notifying them.
            allowed_mentions={"parse": ["users"]},
        )
        BoxScoreUploadToken.objects.filter(pk=token.pk).update(reminded_at=now)
        reminded += 1

    # 2) Expire, and take the buttons with it.
    for token in BoxScoreUploadToken.objects.filter(
            status=BoxScoreUploadToken.Status.PENDING,
            prompt_expires_at__lte=now):
        if token.channel_id and token.message_id:
            result = edit_channel_message(
                token.channel_id, token.message_id,
                content=("That box score expired before anyone confirmed it.\n"
                         f"-# Kept for {BoxScoreUploadToken.PAYLOAD_RETENTION_DAYS} "
                         "days — run `/boxscore token` to restore it."),
                components=[])
            if result == THREAD_ERROR:
                continue          # transient: try again on the next sweep
        # Keep the payload: this is the path where NOBODY chose to discard, so
        # it is the one most worth restoring. The message above promises it.
        BoxScoreUploadToken.objects.filter(pk=token.pk).update(
            status=BoxScoreUploadToken.Status.EXPIRED)
        expired += 1

    # 3) Prune.
    cutoff = now - timedelta(days=prune_after_days)
    done, _ = BoxScoreUploadToken.objects.filter(
        status__in=[BoxScoreUploadToken.Status.APPLIED,
                    BoxScoreUploadToken.Status.CANCELLED,
                    BoxScoreUploadToken.Status.EXPIRED],
        created_at__lt=cutoff).delete()
    stale, _ = BoxScoreUploadToken.objects.filter(
        status=BoxScoreUploadToken.Status.ISSUED,
        expires_at__lt=cutoff).delete()

    logger.info("sweep_boxscore_upload_tokens: reminded=%d expired=%d pruned=%d",
                reminded, expired, done + stale)
    return {"reminded": reminded, "expired": expired, "pruned": done + stale}


@shared_task
def remind_upcoming_matches():
    """Ping each upcoming match's players in their group's Discord thread, once,
    `Tournament.match_reminder_minutes` before it starts.

    Runs on a schedule created in Django admin (django_celery_beat) -- this project
    uses DatabaseScheduler, so there is no beat_schedule in code to register it.
    Every 5-10 minutes is about right; the sweep interval is also the worst-case
    LATENESS of a reminder, which is why the message carries a <t:...> timestamp
    rather than a hardcoded "in N minutes".

    THE WINDOW IS PER-TOURNAMENT, so one `scheduled_time__lte` bound can't select
    the rows. Rather than express `scheduled_time - interval(minutes)` in SQL --
    which compiles differently on Postgres (prod) and SQLite (dev), the same
    portability trap group_roster avoids for NULL ordering -- take the MAX
    configured lead as a coarse outer bound in one cheap query, then apply each
    match's own window in Python.
    """
    from django.db.models import Max, Prefetch
    from the_warroom.models import (Match, Tournament, TournamentPlayer,
                                    CompetitionStatus)
    from the_warroom.services.channel_posts import match_reminder_thread_id
    from the_databot.services.lfg_game import group_roster
    from the_databot.services.time_parsing import format_discord_timestamp

    now = timezone.now()

    # Same gates as the match query below -- a tournament with a long lead but no
    # bot in its guild must not widen the candidate window for everyone else.
    # Deliberately NOT narrowed to tournaments that have stages: this only needs
    # to be an upper bound, and a too-wide bound is merely wasteful, never wrong.
    max_window = Tournament.objects.filter(
        match_reminder_minutes__isnull=False,
        guild__bot_member=True,
    ).aggregate(longest=Max('match_reminder_minutes'))['longest']
    if not max_window:
        return {"sent": 0, "skipped": 0}

    # Round.tournament is a LEGACY link and is deliberately not consulted: a
    # match's tournament is round.stage.tournament. Older code (_schedulable_
    # matches, /upcoming) ORs both paths; doing that here would be actively wrong,
    # because the OR could select a row via one tournament while the lead time
    # below is read from another.
    candidates = (Match.objects
                  .filter(round__stage__tournament__match_reminder_minutes__isnull=False,
                          round__stage__tournament__guild__bot_member=True,
                          reminder_sent_at__isnull=True,
                          game__isnull=True,
                          scheduled_time__gt=now,
                          scheduled_time__lte=now + timedelta(minutes=max_window))
                  .exclude(status=CompetitionStatus.COMPLETED)
                  # A group with no thread can never be reminded, so drop those in
                  # SQL rather than loading them to throw away. discord_thread is
                  # blank=True WITHOUT null=True -- unlinked is "", so an __isnull
                  # filter would match NOTHING. (No matching exclude() is needed
                  # for a NULL player_group: filtering through the relation makes
                  # it an INNER JOIN, which already drops those rows.)
                  .exclude(series__player_group__discord_thread='')
                  # Only the columns the loop actually reads. Without .only() this
                  # drags every field of six tables per row -- including
                  # Tournament.rules/description, Stage.config and PlayerGroup's
                  # availability JSON, none of which this task touches.
                  .select_related('series', 'series__player_group', 'round',
                                  'round__stage', 'round__stage__tournament',
                                  'round__stage__tournament__guild',
                                  # The group's moderator is named in the reminder
                                  # when one is set. Joined here rather than read
                                  # lazily: a nullable FK costs a query per match
                                  # otherwise, which is the whole thing this
                                  # queryset is built to avoid.
                                  'series__player_group__group_moderator')
                  .only('scheduled_time', 'reminder_sent_at', 'status',
                        'match_number', 'series_id', 'round_id',
                        'series__number_of_games', 'series__player_group_id',
                        'series__player_group__discord_thread',
                        'series__player_group__group_moderator_id',
                        # Only what a mention needs: the snowflake, plus the two
                        # fields Profile.__str__ reads (via .name) when the
                        # moderator never linked Discord and can't be pinged.
                        'series__player_group__group_moderator__discord_id',
                        'series__player_group__group_moderator__display_name',
                        'series__player_group__group_moderator__discord',
                        'round__stage_id',
                        'round__stage__tournament_id',
                        'round__stage__tournament__match_reminder_minutes',
                        'round__stage__tournament__guild_id',
                        'round__stage__tournament__guild__guild_id',
                        'round__stage__tournament__guild__bot_member')
                  # Two N+1s to kill, both invisible in the generated SQL:
                  #   series__matches -- series_position walks series.matches
                  #   the roster      -- group_roster reads an M2M (which
                  #                      select_related CANNOT cover) and falls
                  #                      back to MatchSeat, so 1-2 queries PER
                  #                      MATCH without these.
                  #
                  # The roster prefetch is an explicit Prefetch carrying
                  # select_related('profile') so the CACHED TournamentPlayer rows
                  # already have their profiles attached -- group_roster reads
                  # tp.profile per row, which would otherwise be a query each.
                  # group_roster cooperates by using .all() when a prefetch cache
                  # is present (a .select_related() call there builds a new
                  # queryset that ignores the cache entirely).
                  .prefetch_related(
                      'series__matches',
                      Prefetch(
                          'series__player_group__tournament_players',
                          queryset=TournamentPlayer.objects.select_related('profile')),
                      'series__matchseat_set__stage_participant'
                      '__tournament_player__profile')
                  .order_by('scheduled_time'))

    sent = skipped = 0
    for match in candidates:
        # Read the tournament off the STAGE, matching the filter exactly. NOT
        # get_tournament(): that falls back to the legacy Round.tournament FK, so
        # on a round carrying both FKs it could hand back a different tournament
        # than the one the query selected on -- and then the lead time, the guild
        # and the bot_member check would all belong to the wrong series.
        tournament = match.round.stage.tournament
        lead = tournament.match_reminder_minutes
        if not lead:
            continue
        # This match's OWN window -- max_window above is the loosest possible
        # bound; this is the exact test.
        if match.scheduled_time > now + timedelta(minutes=lead):
            continue

        thread_id = match_reminder_thread_id(match, tournament)
        if not thread_id:
            # An unparseable URL, a thread in ANOTHER guild, or the bot is no
            # longer in this one. (Unlinked groups were already excluded in SQL.)
            # Leave the row UNCLAIMED: fixing the link should still earn a
            # reminder while there is still time.
            skipped += 1
            continue

        # Claim BEFORE sending: at-most-once. A duplicate ping to a whole roster
        # is worse than a missed one, and the site still shows the schedule.
        # Compare-and-swap rather than select_for_update -- the same one-statement
        # claim link_group_thread and the token sweep use, with no transaction.
        claimed = Match.objects.filter(
            pk=match.pk, reminder_sent_at__isnull=True).update(reminder_sent_at=now)
        if not claimed:
            continue                      # another worker got here first

        # Hand the prefetched seats in: group_roster's fallback would otherwise
        # query MatchSeat.objects directly, which cannot see a prefetch cache and
        # costs a query per match on the seat-only path (a group whose M2M was
        # never populated -- common enough that the fallback exists at all).
        roster = group_roster(match.player_group, series_id=match.series_id,
                              seats=match.series.matchseat_set.all())
        # discord_id is the snowflake; Profile.discord is a legacy username and
        # must never be mentioned with. A player who never linked Discord can't be
        # pinged but must still be NAMED -- dropping them makes the roster look
        # short. Same rule as create_match_threads_task.
        mentions = [f"<@{p.discord_id}>" if p.discord_id else str(p) for p in roster]
        pings = " ".join(mentions)

        # The group's moderator, when it has one, named after the label so the
        # players read who is running their game. Mentioned by the SAME rule as a
        # player -- snowflake when linked, plain name when not -- and folded into
        # the same allowed_mentions, so a linked moderator is notified too.
        #
        # Deliberately NOT added to `mentions`: they are not on the roster, and
        # leading with them would read as though they were playing.
        moderator = match.player_group.group_moderator if match.player_group else None
        moderating = ""
        if moderator is not None:
            who = f"<@{moderator.discord_id}>" if moderator.discord_id else str(moderator)
            moderating = f" with {who} moderating"

        when = format_discord_timestamp(match.scheduled_time)
        position = match.series_position
        label = (f"game {position} of {match.series.number_of_games}"
                 if position else "your match")
        content = (f"{pings} {label} starts soon{moderating} — {when}".strip() if pings
                   else f"{label.capitalize()} starts soon{moderating} — {when}")

        # parse: ["users"] so this actually notifies. Without it the mentions
        # render as blue text and ping nobody, which is the entire feature.
        post_channel_message_task.delay(
            thread_id, content, allowed_mentions={"parse": ["users"]})
        sent += 1

    logger.info("remind_upcoming_matches: sent=%d skipped=%d", sent, skipped)
    return {"sent": sent, "skipped": skipped}
