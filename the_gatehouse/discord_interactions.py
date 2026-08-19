"""
HTTP Interactions endpoint for the Discord bot.

Discord POSTs every slash-command interaction here. Each request is signed
with Ed25519; we MUST verify the signature against our application's public
key before doing anything (Discord rejects the endpoint during setup
otherwise, and unsigned requests must get a 401).

Currently handles:
  PING (type 1)                        -> PONG (type 1)
  APPLICATION_COMMAND (type 2)         -> dispatches by command name (e.g.
                                          /faction, /clockwork, /map, /deck,
                                          /vagabond, /landmark, /hireling,
                                          /houserule, /stats, /upcoming,
                                          /schedule, /law, /help)
  APPLICATION_COMMAND_AUTOCOMPLETE (4) -> live option suggestions (type 8)
"""
import json
import logging
import random
import re
from datetime import datetime, timedelta, timezone as dt_timezone

from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError

from django.core.cache import cache
from django.db.models import Q, Exists, OuterRef
from django.http import JsonResponse, HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from the_keep.models import Faction, Map, Deck, Vagabond, Landmark, Hireling, Tweak, Law, Post, Card, CardTag
from the_warroom.models import Tournament, Match, CompetitionStatus, filtered_winrate, EloParticipant
from the_gatehouse.models import Profile, BotBlacklist, DiscordGuild, GuildLFGRole
from .tasks import (
    record_bot_usage_task, ensure_profile_from_discord_task, notify_lfg_task,
    create_lfg_thread_task, record_lfg_components_task, post_interaction_followup_task,
)
from .services.discordservice import (
    config, build_post_embed, build_post_image_embed, build_stats_embed,
    build_captain_embed, build_card_embed, build_law_embed, build_help_embed, build_upcoming_embed,
    faction_emoji_for, faction_emoji_object, vagabond_emoji_for, suit_emoji_for,
    roll_emoji_for, suit_static_image_url, embed_color, permissions_can_manage_guild,
    get_guild_roles,
)
from .services.discord_commands import (
    DRAFT_PLATFORM_TTS, DRAFT_PLATFORM_RD,
)
from .services.time_parsing import (
    NEED_TIMEZONE, parse_user_datetime, format_discord_timestamp,
    valid_timezone, search_timezones,
)
from .services.discord_components import (
    action_row, button, string_select, select_option,
    encode_custom_id, decode_custom_id, selected_values,
    RESPONSE_UPDATE_MESSAGE, STYLE_PRIMARY, STYLE_SUCCESS, STYLE_SECONDARY, STYLE_DANGER,
)

logger = logging.getLogger(__name__)

# Discord interaction request/response type constants
PING = 1
APPLICATION_COMMAND = 2
APPLICATION_COMMAND_AUTOCOMPLETE = 4
MESSAGE_COMPONENT = 3  # user interacted with a message component (select/button)

RESPONSE_PONG = 1
RESPONSE_CHANNEL_MESSAGE = 4
RESPONSE_AUTOCOMPLETE_RESULT = 8
# RESPONSE_UPDATE_MESSAGE (7) is imported from discord_components.

EPHEMERAL = 64  # message flag: only the invoking user sees it


def _interaction_user_id(payload):
    """The clicking/invoking user's Discord id (member.user in a guild, user in a
    DM), or None."""
    return ((payload.get("member") or {}).get("user", {}).get("id")
            or (payload.get("user") or {}).get("id"))


_BLACKLIST_TTL = 60  # seconds; short so an unblock takes effect within a minute


def _is_blacklisted(user_id, guild_id):
    """True if the user or guild is actively blocked. Cached ~60s per id in Redis
    to keep the interaction path fast (DB only on a cache miss); an admin unblock
    takes effect within the TTL (or immediately if the cache-bust signal fires)."""
    for kind, value in (("user", user_id), ("guild", guild_id)):
        if not value:
            continue
        key = f"botblacklist:{kind}:{value}"
        hit = cache.get(key)
        if hit is None:
            hit = BotBlacklist.objects.filter(kind=kind, discord_id=value, active=True).exists()
            cache.set(key, hit, _BLACKLIST_TTL)
        if hit:
            return True
    return False


def _interaction_author(payload):
    """The user who triggered an interaction, as an embed `author` dict
    ({name, icon_url}) or None. `member.user` in a guild, `user` in a DM. Uses
    the user's global (display) name when set, else their username, and builds
    the avatar CDN URL (falling back to Discord's default avatar)."""
    user = (payload.get("member") or {}).get("user") or payload.get("user")
    if not user:
        return None

    name = user.get("global_name") or user.get("username") or "Unknown"
    user_id = user.get("id")
    avatar = user.get("avatar")
    if user_id and avatar:
        ext = "gif" if avatar.startswith("a_") else "png"
        icon_url = f"https://cdn.discordapp.com/avatars/{user_id}/{avatar}.{ext}"
    elif user_id:
        # Default avatar: new usernames key off (id >> 22) % 6; legacy off
        # discriminator % 5. Fall back safely if the id isn't an int.
        try:
            index = (int(user_id) >> 22) % 6
        except (TypeError, ValueError):
            index = 0
        icon_url = f"https://cdn.discordapp.com/embed/avatars/{index}.png"
    else:
        icon_url = None

    author = {"name": name[:256]}
    if icon_url:
        author["icon_url"] = icon_url
    return author


# ── /draft ─────────────────────────────────────────────────────────────────
# Short platform keys ride in component custom_ids (100-char cap); the ban list
# never does (it's recovered from the message's own select state instead).
DRAFT_PLATFORM_KEYS = {"tts": DRAFT_PLATFORM_TTS, "rd": DRAFT_PLATFORM_RD}
DRAFT_PLATFORM_TO_KEY = {v: k for k, v in DRAFT_PLATFORM_KEYS.items()}
# If one of the pair is drafted, the other is removed from the remaining pool.
DRAFT_EXCLUSIONS = {
    "vagabond": "knaves-of-the-deepwood",
    "knaves-of-the-deepwood": "vagabond",
}

# Grace period for /upcoming: a match still counts as "upcoming" until this long
# after its scheduled start, so one that just kicked off isn't dropped mid-game.
UPCOMING_GRACE = timedelta(minutes=30)


def _verify_signature(request):
    """Return True if the request carries a valid Discord Ed25519 signature."""
    signature = request.headers.get("X-Signature-Ed25519")
    timestamp = request.headers.get("X-Signature-Timestamp")
    if not signature or not timestamp:
        return False

    verify_key = VerifyKey(bytes.fromhex(config["DISCORD_PUBLIC_KEY"]))
    message = timestamp.encode() + request.body
    try:
        verify_key.verify(message, bytes.fromhex(signature))
        return True
    except (BadSignatureError, ValueError):
        return False


def _ephemeral(content):
    return JsonResponse({
        "type": RESPONSE_CHANNEL_MESSAGE,
        "data": {"content": content, "flags": EPHEMERAL},
    })


def _get_option(data, name):
    """Pull a named option value out of an APPLICATION_COMMAND interaction."""
    for opt in data.get("options", []):
        if opt.get("name") == name:
            return opt.get("value")
    return None


def _lookup_post(queryset, name):
    """Prefer an exact title match; fall back to a substring search."""
    return (
        queryset.filter(status__lte=4, title__iexact=name).first()
        or queryset.filter(status__lte=4, title__icontains=name).first()
    )


def _lookup_embed(post, image_field=None):
    """A post's info card as a single embed with its large board/card image folded
    in (embed.image), so a lookup sends one complete embed rather than two. Image
    is omitted when the post has none. `image_field` overrides the default per-
    component image (e.g. "card_2_image" for a captain)."""
    embed = build_post_embed(post)
    image_url = _post_image_url(post, field=image_field)
    if image_url:
        embed["image"] = {"url": image_url}
    return embed


def _make_lookup_handler(label, queryset_factory):
    """Build a slash-command handler that looks up a Post by title and replies
    with one embed (info card + large image). `queryset_factory` returns the base
    queryset to search."""
    def handler(data):
        name = (_get_option(data, "name") or "").strip()
        if not name:
            return _ephemeral(f"Please provide a {label} name to search.")

        post = _lookup_post(queryset_factory(), name)
        if not post:
            return _ephemeral(f'No {label} found matching "{name}".')

        # If used inside an LFG thread, record the looked-up component (a selected
        # map/deck updates the LFGThread FK like a random roll).
        kind = _LFG_LOOKUP_KIND.get(data.get("name"))
        if kind:
            _capture_lfg_components(data.get("_channel_id"), [_lfg_item(kind, post)])

        return JsonResponse({
            "type": RESPONSE_CHANNEL_MESSAGE,
            "data": {"embeds": [_lookup_embed(post)]},
        })
    return handler


# Per-command base querysets, shared by the command handler and its autocomplete
# handler so there is a single source of truth. Faction and Clockwork share the
# Faction model, split by `component`.
LOOKUP_QUERYSETS = {
    "faction": lambda: Faction.objects.filter(component="Faction"),
    "clockwork": lambda: Faction.objects.filter(component="Clockwork"),
    "map": Map.objects.all,
    "deck": Deck.objects.all,
    "vagabond": Vagabond.objects.all,
    "landmark": Landmark.objects.all,
    "hireling": Hireling.objects.all,
    "houserule": Tweak.objects.all,
}

_LOOKUP_LABELS = {
    "faction": "faction",
    "clockwork": "clockwork faction",
    "map": "map",
    "deck": "deck",
    "vagabond": "vagabond",
    "landmark": "landmark",
    "hireling": "hireling",
    "houserule": "house rule",
}

# Lookup command name → the capitalized "kind" recorded on an LFGThread (matching the
# strings /random uses, so rolls entries are consistent across commands).
_LFG_LOOKUP_KIND = {
    "faction": "Faction",
    "clockwork": "Clockwork",
    "map": "Map",
    "deck": "Deck",
    "vagabond": "Vagabond",
    "landmark": "Landmark",
    "hireling": "Hireling",
    "houserule": "Tweak",
}


def _capture_lfg_components(channel_id, items):
    """Fire-and-forget: record components surfaced by a command into an LFG thread
    (no-op in the worker when the channel isn't a known LFG thread). `items` is a
    list of {"kind","slug","title"}. Safe to call with a falsy channel_id."""
    if channel_id and items:
        record_lfg_components_task.delay(channel_id, items)


def _lfg_item(kind, post):
    return {"kind": kind, "slug": getattr(post, "slug", None),
            "title": getattr(post, "title", None)}


def _handle_captain_command(data):
    """/captain: look up a captain-capable vagabond and show its captain
    (Advanced) profile — captain ability and captain starting items."""
    name = (_get_option(data, "name") or "").strip()
    if not name:
        return _ephemeral("Please provide a captain name to search.")

    vagabond = _lookup_post(Vagabond.objects.filter(captain=True), name)
    if not vagabond:
        return _ephemeral(f'No captain found matching "{name}".')

    # One embed: the captain (Advanced) profile with the flip-side card_2_image
    # folded in as the large image.
    embed = build_captain_embed(vagabond)
    image_url = _post_image_url(vagabond, field="card_2_image")
    if image_url:
        embed["image"] = {"url": image_url}

    # /captain is its own handler (not in the lookup loop); capture as "Captain".
    _capture_lfg_components(data.get("_channel_id"), [_lfg_item("Captain", vagabond)])

    return JsonResponse({
        "type": RESPONSE_CHANNEL_MESSAGE,
        "data": {"embeds": [embed]},
    })


def _handle_card_command(data):
    """/card: look up an individual card by name (dedup'd across decks), optionally
    filtered by the post it's from and/or its suit/tag. Because many cards share a
    name, pick the single best match: official posts first, then lower status."""
    name = (_get_option(data, "name") or "").strip()
    from_slug = _get_option(data, "from")
    tag = _get_option(data, "tag")

    if not (name or from_slug or tag):
        return _ephemeral("Please provide a card name (or a from/tag filter) to search.")

    base = Card.objects.select_related(
        "group", "group__post", "group__language",
    ).filter(group__post__status__lte=4)  # published posts only (site convention)
    if from_slug:
        base = base.filter(group__post__slug=from_slug)
    if tag:
        base = base.filter(tags__contains=[tag])  # JSONField list (Postgres)

    order = ("-group__post__official", "group__post__status", "name", "id")
    if name:
        # Exact match wins, else substring (same idea as _lookup_post).
        card = (base.filter(name__iexact=name).order_by(*order).first()
                or base.filter(name__icontains=name).order_by(*order).first())
    else:
        card = base.order_by(*order).first()

    if not card:
        return _ephemeral(f'No card found matching "{name or from_slug or tag}".')

    return JsonResponse({
        "type": RESPONSE_CHANNEL_MESSAGE,
        "data": {"embeds": [build_card_embed(card)]},
    })


def _handle_stats_command(data):
    """/stats: win rate filtered by player, faction, series, and/or platform."""
    player_slug = _get_option(data, "player")
    faction_slug = _get_option(data, "faction")
    series_slug = _get_option(data, "series")
    platform = _get_option(data, "platform")
    # Fan-made factions are hidden unless the user explicitly opts in, so both
    # "No" and an unset option (None) resolve to False.
    include_fan_content = bool(_get_option(data, "include_fan_content"))

    player = faction = tournament = None
    if player_slug:
        player = Profile.objects.filter(slug=player_slug).first()
        if not player:
            return _ephemeral("Couldn't find that player.")
    if faction_slug:
        faction = Faction.objects.filter(slug=faction_slug).first()
        if not faction:
            return _ephemeral("Couldn't find that faction.")
    if series_slug:
        tournament = Tournament.objects.filter(slug=series_slug).first()
        if not tournament:
            return _ephemeral("Couldn't find that series.")

    # When a player + series (with an Elo system) are both in focus and no faction
    # narrows the query, surface that player's standing in the system. Faction
    # filtering makes Elo irrelevant, so it's skipped there.
    elo_participant = None
    if player and tournament and not faction and tournament.elo_system_id:
        elo_participant = (
            EloParticipant.objects
            .select_related('elo_system')
            .filter(elo_system_id=tournament.elo_system_id, player=player)
            .first()
        )

    stats = filtered_winrate(
        player=player, faction=faction, tournament=tournament, platform=platform
    )
    if stats["total"] == 0:
        return JsonResponse({
            "type": RESPONSE_CHANNEL_MESSAGE,
            "data": {"content": "No games found for those filters."},
        })

    return JsonResponse({
        "type": RESPONSE_CHANNEL_MESSAGE,
        "data": {"embeds": [build_stats_embed(
            stats, player=player, faction=faction, tournament=tournament, platform=platform,
            include_fan_content=include_fan_content, elo_participant=elo_participant,
        )]},
    })


def _handle_upcoming_command(data):
    """/upcoming: the next scheduled match, optionally filtered to a series and/or
    a player. With no series, searches across all tournaments. Replies publicly
    with an embed linking to the matches page."""
    series_slug = _get_option(data, "series")
    player_slug = _get_option(data, "player")

    matches = Match.objects.filter(
        scheduled_time__isnull=False,
        scheduled_time__gte=timezone.now() - UPCOMING_GRACE,
    ).exclude(status=CompetitionStatus.COMPLETED)

    tournament = None
    if series_slug:
        tournament = Tournament.objects.filter(slug=series_slug).first()
        if not tournament:
            return _ephemeral("Couldn't find that series.")
        # A round links to its tournament directly (no-stage tournaments) or
        # through its stage, so match either path.
        matches = matches.filter(
            Q(round__stage__tournament=tournament) | Q(round__tournament=tournament),
        )

    player = None
    if player_slug:
        player = Profile.objects.filter(slug=player_slug).first()
        if not player:
            return _ephemeral("Couldn't find that player.")
        matches = matches.filter(
            series__matchseat__stage_participant__tournament_player__profile=player
        )

    match = (
        matches.select_related(
            "round", "round__stage", "round__stage__tournament",
            "round__tournament", "series__player_group",
        )
        .order_by("scheduled_time")
        .first()
    )
    if not match:
        return _ephemeral("No upcoming matches found.")

    return JsonResponse({
        "type": RESPONSE_CHANNEL_MESSAGE,
        "data": {"embeds": [build_upcoming_embed(match, series=tournament, player=player)]},
    })


# ── /schedule ────────────────────────────────────────────────────────────────
# Sets — or, with no `time` option, clears — Match.scheduled_time from inside the
# match's own Discord thread. The thread is identified by id
# (PlayerGroup.discord_thread holds its URL), falling back to the thread's title
# matched against the player group's name.
#
# Neither set nor clear writes straight away. A set echoes the parsed time back as
# a Discord <t:...> timestamp — which renders in the clicker's own timezone — so a
# misparse is visible before it's saved; a clear shows the time about to be
# removed. Both sit behind a button the invoker has to press.

# Discord thread types (channel.type) — used to tell "in a thread" from "in a channel".
_THREAD_CHANNEL_TYPES = {10, 11, 12}

# Collapse whitespace and strip leading decoration when comparing a thread title to
# a group name; thread names routinely pick up an emoji or separator prefix.
_TITLE_NOISE_RE = re.compile(r"^[^\w(]+|[^\w)]+$")
_WS_RE = re.compile(r"\s+")


def _normalize_title(text):
    return _WS_RE.sub(" ", _TITLE_NOISE_RE.sub("", (text or "").strip())).strip().lower()


def _schedulable_matches(guild_id):
    """Matches in this guild's tournaments that could still be scheduled: not
    completed and not yet linked to a recorded game.

    A round reaches its tournament directly (no-stage tournaments) or through its
    stage, so both paths must be matched — same as /upcoming."""
    return Match.objects.filter(
        Q(round__stage__tournament__guild__guild_id=guild_id)
        | Q(round__tournament__guild__guild_id=guild_id),
        game__isnull=True,
    ).exclude(status=CompetitionStatus.COMPLETED).select_related(
        "series", "series__player_group", "series__player_group__group_moderator",
        "round", "round__stage", "round__stage__tournament", "round__tournament",
    )


def _match_for_thread(channel_id, guild_id, channel_name=None, prefer="unscheduled"):
    """The Match to schedule for this thread. Returns (match, error) where `error`
    is a user-facing message.

    Primary key is the thread id inside PlayerGroup.discord_thread. Falls back to
    the thread's title matched against the player group's name (the name shown
    everywhere in the UI; MatchSeries.name is usually blank), for groups whose
    thread URL was never filled in.

    `prefer` picks which match of a multi-game series to act on: "unscheduled"
    (setting a time) takes the first game still missing one; "scheduled"
    (clearing) takes the LAST game that has one."""
    if not guild_id or not channel_id or not str(channel_id).isdigit():
        return None, "This command only works inside a server."

    base = _schedulable_matches(guild_id)

    # discord_thread is a URL ending in the thread id
    # (https://discord.com/channels/<guild>/<thread>). Anchor on the leading slash
    # so a thread id can't match part of the guild id.
    matches = list(base.filter(
        series__player_group__discord_thread__contains=f"/{channel_id}"
    ).order_by("match_number"))

    if not matches:
        title = _normalize_title(channel_name)
        if not title:
            return None, (
                "I couldn't find a tournament match linked to this thread. A "
                "moderator can link it on the series edit page (set the group's "
                "Discord thread), then try again."
            )
        # Title fallback. Compare in Python so normalization matches on both sides
        # (emoji prefixes, collapsed whitespace) rather than relying on __iexact.
        candidates = [
            m for m in base.filter(series__player_group__isnull=False)
            if _normalize_title(m.series.player_group.name) == title
        ]
        groups = {m.series.player_group_id for m in candidates}
        if len(groups) > 1:
            # Group names are unique per round, not per tournament, so a title can
            # legitimately match several groups. Don't guess.
            return None, (
                f'Several player groups are named "{channel_name}", so I can\'t tell '
                "which match this thread is for. A moderator can link this thread to "
                "the group on the series edit page."
            )
        matches = sorted(candidates, key=lambda m: (m.match_number is None, m.match_number))

    if not matches:
        return None, (
            "I couldn't find a tournament match linked to this thread. A moderator "
            "can link it on the series edit page (set the group's Discord thread), "
            "then try again."
        )

    # `matches` is ordered by match_number, so [0] is the earliest game of a
    # series and [-1] the latest.
    if prefer == "scheduled":
        # Clearing: the last game that actually has a time — most likely the one
        # just set by mistake. With none scheduled, hand back the earliest so the
        # caller can say "nothing to remove" naming a real match.
        scheduled = [m for m in matches if m.scheduled_time is not None]
        return (scheduled[-1] if scheduled else matches[0]), None

    # Setting: the first match with no time yet; if every match in the series is
    # already scheduled, target the earliest one and treat this as a reschedule.
    unscheduled = [m for m in matches if m.scheduled_time is None]
    return (unscheduled[0] if unscheduled else matches[0]), None


def _match_label(match):
    """How a match is named back to the user: the player group's name (what the UI
    shows everywhere), falling back to the derived match name."""
    group = match.series.player_group if match.series_id else None
    return (group.name if group and group.name else None) or match.name or "this match"


def _schedule_confirm_data(match, when, owner):
    """The ephemeral confirm prompt: the time as Discord renders it in the clicker's
    own timezone, plus Confirm / Cancel. The owner snowflake rides LAST in each
    custom_id so the dispatcher's owner-lock applies without extra checks."""
    label = _match_label(match)
    ts = int(when.timestamp())
    lines = [
        f"Schedule **{label}** for:",
        format_discord_timestamp(when),
    ]
    if match.scheduled_time:
        lines.append(
            f"\nThis replaces the current time of {format_discord_timestamp(match.scheduled_time)}."
        )
    lines.append("\nDoes that look right?")
    return {
        "content": "\n".join(lines),
        "flags": EPHEMERAL,
        "components": [action_row(
            button("Confirm", encode_custom_id("schedule_confirm", match.id, ts, owner),
                   style=STYLE_SUCCESS),
            button("Cancel", encode_custom_id("schedule_cancel", owner), style=STYLE_SECONDARY),
        )],
    }


def _schedule_clear_data(match, owner):
    """Confirm prompt for REMOVING a match's scheduled time. Destructive, so the
    action button is STYLE_DANGER (the ✖ vocabulary /lfg uses) rather than the
    STYLE_SUCCESS the set flow reserves for adding a time."""
    return {
        "content": "\n".join([
            f"Remove the scheduled time for **{_match_label(match)}**?",
            f"Currently {format_discord_timestamp(match.scheduled_time)}",
            # Clearing only affects where the match shows up, not who may record
            # it — recording is gated on seating, never on scheduled_time.
            "\nThe match stays in the bracket — it just won't show on the schedule "
            "until a new time is set.",
        ]),
        "flags": EPHEMERAL,
        "components": [action_row(
            button("Clear Time", encode_custom_id("schedule_clear_confirm", match.id, owner),
                   style=STYLE_DANGER),
            button("Cancel", encode_custom_id("schedule_cancel", owner), style=STYLE_SECONDARY),
        )],
    }


def _handle_schedule_command(data):
    """/schedule: set — or, with no `time`, clear — the scheduled time of the match
    belonging to this thread. Replies ephemerally with a confirm prompt; nothing is
    written until the user clicks."""
    guild_id = data.get("_guild_id")
    channel_id = data.get("_channel_id")
    author_id = data.get("_author_id")

    if not guild_id:
        return _ephemeral("This command only works inside a server.")
    if not author_id:
        return _ephemeral("I couldn't tell who you are — try again.")

    # Strict lookup: creating a profile here would only let the user fail the
    # permission check below in a more confusing way.
    profile = Profile.objects.filter(discord_id=str(author_id)).first()
    if not profile:
        site = config.get("SITE_URL") or ""
        suffix = f" at {site}" if site else ""
        return _ephemeral(
            f"I don't have a site account linked to your Discord yet. Log in{suffix} "
            "with Discord once, then try again."
        )

    # No time given = clear the existing one. That flips which match of a
    # multi-game series we want: the last one that HAS a time, not the first
    # one missing it.
    time_text = (_get_option(data, "time") or "").strip()
    clearing = not time_text

    match, error = _match_for_thread(
        channel_id, guild_id, data.get("_channel_name"),
        prefer="scheduled" if clearing else "unscheduled",
    )
    if error:
        # Discord tells us the channel type, so a command run in a normal channel
        # gets the actionable message rather than "no match linked to this thread".
        channel_type = data.get("_channel_type")
        if channel_type is not None and channel_type not in _THREAD_CHANNEL_TYPES:
            return _ephemeral(
                "Run this inside your match's thread — that's how I know which "
                "match you mean."
            )
        return _ephemeral(error)

    # Checked before the clear branch so an unauthorized user gets the permission
    # error rather than a prompt they can't act on.
    permission = match.can_schedule(profile)
    if not permission:
        return _ephemeral(
            "You can't set the time for this match: you need to be one of its "
            "players, the group's moderator, or a tournament moderator."
        )

    if clearing:
        if match.scheduled_time is None:
            return _ephemeral(
                f"**{_match_label(match)}** doesn't have a scheduled time to remove. "
                "Give me a `time` to set one."
            )
        # No timezone needed to clear, so this path works even for a profile that
        # has never set one.
        return JsonResponse({
            "type": RESPONSE_CHANNEL_MESSAGE,
            "data": _schedule_clear_data(match, author_id),
        })

    # Timezone: an explicit option (remembered for next time) beats the stored one.
    tz_option = (_get_option(data, "timezone") or "").strip()
    if tz_option:
        if not valid_timezone(tz_option):
            return _ephemeral(
                f'"{tz_option}" isn\'t a timezone I recognize. Pick one from the '
                "suggestions, e.g. `America/New_York`."
            )
        if profile.timezone != tz_option:
            profile.timezone = tz_option
            profile.save(update_fields=["timezone"])
    tz_name = tz_option or profile.timezone

    when, error = parse_user_datetime(time_text, tz_name)
    if error == NEED_TIMEZONE:
        return _ephemeral(
            "I don't know your timezone yet, so I can't tell what that time means. "
            "Run the command again with the `timezone` option (e.g. "
            "`America/New_York`) — I'll remember it after that."
        )
    if error:
        return _ephemeral(error)

    return JsonResponse({
        "type": RESPONSE_CHANNEL_MESSAGE,
        "data": _schedule_confirm_data(match, when, author_id),
    })


def _handle_schedule_confirm(payload):
    """Confirm button: write the scheduled time, then announce it in the thread."""
    _action, args = decode_custom_id(payload["data"]["custom_id"])  # [match_id, ts, owner]
    if len(args) < 3:
        return _ephemeral("That button is out of date — run /schedule again.")
    match_id, ts, owner = args[0], args[1], args[2]

    match = _schedulable_matches(payload.get("guild_id")).filter(pk=match_id).first()
    if not match:
        return _ephemeral(
            "That match can no longer be scheduled — it may have been played or removed."
        )

    # The button is a second request, so re-check permission rather than trusting
    # the check made when the prompt was built.
    profile = Profile.objects.filter(discord_id=str(owner)).first()
    if not profile or not match.can_schedule(profile):
        return _ephemeral("You can't set the time for this match.")

    try:
        when = datetime.fromtimestamp(int(ts), tz=dt_timezone.utc)
    except (ValueError, OSError, OverflowError):
        return _ephemeral("That time is no longer valid — run /schedule again.")

    match.scheduled_time = when
    # update_fields is required: a bare save() re-runs Match.save()'s name and
    # match_number derivation.
    match.save(update_fields=["scheduled_time"])

    # Announce publicly in the thread so the whole group sees it. The followup is
    # sequenced after this response's ACK (a followup before it 404s).
    token = payload.get("token")
    if token:
        try:
            embed = build_upcoming_embed(match)
        except Exception:
            logger.exception("Failed to build /schedule announcement embed")
            embed = None
        if embed:
            post_interaction_followup_task.apply_async(
                (token, {"embeds": [embed]}), countdown=2,
            )

    return JsonResponse({
        "type": RESPONSE_UPDATE_MESSAGE,
        "data": {
            "content": f"✔ Scheduled for {format_discord_timestamp(when)}.",
            "components": [],
        },
    })


def _handle_schedule_clear_confirm(payload):
    """Clear button: remove the match's scheduled time, then say so in the thread.
    Mirrors _handle_schedule_confirm's guards — the button is a second request, so
    the match is re-fetched and permission re-checked."""
    _action, args = decode_custom_id(payload["data"]["custom_id"])  # [match_id, owner]
    if len(args) < 2:
        return _ephemeral("That button is out of date — run /schedule again.")
    match_id, owner = args[0], args[1]

    match = _schedulable_matches(payload.get("guild_id")).filter(pk=match_id).first()
    if not match:
        return _ephemeral(
            "That match can no longer be changed — it may have been played or removed."
        )

    profile = Profile.objects.filter(discord_id=str(owner)).first()
    if not profile or not match.can_schedule(profile):
        return _ephemeral("You can't change the time for this match.")

    if match.scheduled_time is None:
        return _ephemeral("That match no longer has a scheduled time.")

    label = _match_label(match)
    match.scheduled_time = None
    # update_fields is required: a bare save() re-runs Match.save()'s name and
    # match_number derivation.
    match.save(update_fields=["scheduled_time"])

    # Supersede the announcement the set flow posted — otherwise the thread is left
    # showing a time that no longer exists. Plain text rather than
    # build_upcoming_embed, which omits its Scheduled field entirely when the time
    # is null and so would read as if nothing had changed.
    token = payload.get("token")
    if token:
        post_interaction_followup_task.apply_async(
            (token, {"content": f"🗓️ The scheduled time for **{label}** was removed."}),
            countdown=2,
        )

    return JsonResponse({
        "type": RESPONSE_UPDATE_MESSAGE,
        "data": {"content": "✔ Scheduled time removed.", "components": []},
    })


def _handle_schedule_cancel(payload):
    """Cancel button: drop the prompt without writing anything. Shared by the set
    and clear flows, so the copy stays neutral."""
    return JsonResponse({
        "type": RESPONSE_UPDATE_MESSAGE,
        "data": {"content": "Cancelled — nothing was changed.", "components": []},
    })


def _ac_schedule_timezone(query, _data):
    """Autocomplete for /schedule `timezone`: IANA zone names, common ones first."""
    return [{"name": z, "value": z} for z in search_timezones(query)]


def _public_laws(language_code="en"):
    """Laws that are publicly viewable and linkable, scoped to one language. A
    law needs a public group with a slug (for the URL) in the given language.
    Defaults to English; the /law command is English-only for now."""
    return Law.objects.filter(
        group__public=True, group__slug__isnull=False, language__code=language_code
    )


def _handle_law_command(data):
    """/law: find a public English law by the combined `law` (code/title), post,
    and/or text option (at least one), and reply with its embed."""
    law_value = (_get_option(data, "law") or "").strip()
    post_slug = (_get_option(data, "post") or "").strip()
    text = (_get_option(data, "text") or "").strip()

    if not (law_value or post_slug or text):
        return _ephemeral("Type a law code/title, post, or some text to search.")

    laws = _public_laws()

    if law_value:
        # Autocomplete sends the law's id as the value, so an all-digit value
        # that resolves to a public law pins the result to exactly that law.
        by_id = laws.filter(id=law_value) if law_value.isdigit() else laws.none()
        if by_id.exists():
            laws = by_id
        else:
            # Free-typed: prefer exact matches (code, then title) before any
            # substring match (code, then title). First tier that matches wins.
            title_exact = Q(plain_title__iexact=law_value) | Q(title__iexact=law_value)
            title_contains = Q(plain_title__icontains=law_value) | Q(title__icontains=law_value)
            for criterion in (
                Q(law_code__iexact=law_value),
                title_exact,
                Q(law_code__icontains=law_value),
                title_contains,
            ):
                matched = laws.filter(criterion)
                if matched.exists():
                    laws = matched
                    break
            else:
                laws = laws.none()
    if post_slug:
        post = Post.objects.filter(slug=post_slug).first()
        if not post:
            return _ephemeral("Couldn't find that post.")
        laws = laws.filter(Q(group__post=post) | Q(linked_post=post))
    if text:
        laws = laws.filter(
            Q(plain_description__icontains=text) | Q(description__icontains=text)
        )

    laws = laws.select_related("group", "group__post", "language")
    # Prefer a prime law when several match (e.g. a post's top-level law).
    law = laws.filter(prime_law=True).first() or laws.first()
    if not law:
        return _ephemeral("No matching law found.")

    return JsonResponse({
        "type": RESPONSE_CHANNEL_MESSAGE,
        "data": {"embeds": [build_law_embed(law)]},
    })


def _handle_help_command(data):
    """/help: list the commands available in this server, grouped by category. Ephemeral
    so the listing only shows to the invoking user and doesn't clutter the channel.

    In a guild, only the guild's enabled commands (plus /help) are listed; if the invoker
    can manage the server and some commands are still disabled, a link to enable more is
    appended. In a DM (no guild) the full command set is shown."""
    guild_id = data.get("_guild_id")
    enabled_names = None
    can_manage = False
    if guild_id:
        guild = DiscordGuild.objects.filter(guild_id=guild_id).first()
        # Absent guild row → treat as no commands enabled (only /help). It also can't be
        # managed from the site until it exists, so no link.
        enabled_names = list(guild.enabled_commands or []) if guild else []
        can_manage = permissions_can_manage_guild(data.get("_member_permissions"))
    embed = build_help_embed(enabled_names=enabled_names, guild_id=guild_id, can_manage=can_manage)
    return JsonResponse({
        "type": RESPONSE_CHANNEL_MESSAGE,
        "data": {"embeds": [embed], "flags": EPHEMERAL},
    })


# ── /draft ─────────────────────────────────────────────────────────────────
def _parse_draft_state(custom_id):
    """('draft_build', players:int(2..6), platform:str) from a draft custom_id;
    falls back to defaults for a malformed/short id.

    ONLY valid for `draft_select`/`draft_build` ids (args[0]=players, args[1]=platform).
    Do NOT call on `draft_cancel:{owner}` — its args[0] is the owner id, which would
    silently parse to the default players/platform."""
    action, args = decode_custom_id(custom_id)
    players, platform = 4, DRAFT_PLATFORM_TTS
    if len(args) >= 1:
        try:
            players = max(2, min(6, int(args[0])))
        except (ValueError, TypeError):
            players = 4
    if len(args) >= 2:
        platform = DRAFT_PLATFORM_KEYS.get(args[1], DRAFT_PLATFORM_TTS)
    return action, players, platform


def _draft_eligible_factions(platform, players):
    """Official, Stable, playable factions for the draft, as (slug, title, type)
    tuples ordered by title. Root Digital narrows to factions available there.
    2-player drafts are Militant-only — because the ban dropdown, the draftable
    pool, and the result all derive from this query, restricting here makes all
    three Militant-only at 2 players automatically."""
    qs = Faction.objects.filter(official=True, status=1, component="Faction")
    if platform == DRAFT_PLATFORM_RD:
        qs = qs.filter(in_root_digital=True)
    if players == 2:
        qs = qs.filter(type=Faction.TypeChoices.MILITANT)  # type='M'
    return list(qs.order_by("title").values_list("slug", "title", "type"))


def _draft_ui_data(players, platform, factions, banned_slugs, owner):
    """The public ban UI: a faction ban select (current bans pre-selected via
    default=True) plus Build/Cancel buttons. `factions` is a list of
    (slug, title, type); `banned_slugs` is a set. `owner` is the invoker's user id,
    appended to every custom_id so only they can operate the controls."""
    platform_key = DRAFT_PLATFORM_TO_KEY.get(platform, "tts")
    options = [
        select_option(title, slug, emoji=faction_emoji_object(slug), default=slug in banned_slugs)
        for slug, title, _type in factions
    ]
    select = string_select(
        encode_custom_id("draft_select", players, platform_key, owner),
        options,
        placeholder="Select factions to ban (optional)",
        min_values=0,
        max_values=len(options),
    )
    buttons = action_row(
        button("Build Draft", encode_custom_id("draft_build", players, platform_key, owner), style=STYLE_SUCCESS),
        button("Cancel", encode_custom_id("draft_cancel", owner), style=STYLE_SECONDARY),
    )
    return {
        "content": f"**{players} Player Draft** — pick factions to ban, then Build.",
        "components": [action_row(select), buttons],
    }


def _build_draft(factions, banned_slugs, players):
    """Return (drawn_slugs, error). Guarantee one Militant first, then draw
    `players` more random factions (so the draft holds `players + 1` total),
    enforcing the vagabond/knaves mutual exclusion and no duplicates. At 2 players
    the caller passes a Militant-only `factions` list, so the whole pool is
    Militant and the extra picks are Militants too."""
    total = players + 1  # 1 guaranteed Militant + `players` more
    pool = {slug: ftype for slug, _title, ftype in factions if slug not in banned_slugs}

    militants = [s for s, t in pool.items() if t == "M"]
    if not militants:
        return None, "No Militant faction available after bans — can't start a draft."
    if len(pool) < total:
        return None, f"Only {len(pool)} factions left after bans; need {total} for a {players}-player draft."

    drawn = []

    def take(slug):
        drawn.append(slug)
        pool.pop(slug, None)
        pool.pop(DRAFT_EXCLUSIONS.get(slug), None)  # enforce exclusion going forward

    take(random.choice(militants))
    while len(drawn) < total:
        if not pool:  # exclusions can starve the pool below the pre-checked count
            return None, (
                f"Not enough compatible factions for a {players}-player draft "
                f"(only {len(drawn)} of {total} could be drafted after exclusions)."
            )
        take(random.choice(list(pool)))

    return drawn, None


def _draft_result_embed(drawn, players, platform, banned_slugs, factions, author=None,
                        vagabond=None, captains=None):
    """The public draft embed: the invoking user's avatar/name as the author
    header, an `N Player Draft` title, the drafted faction emoji (title fallback
    when an emoji is missing) as the description, and platform + banned factions
    in the footer. When the Vagabond faction is drafted, `vagabond` is the rolled
    Vagabond object, shown as a "Vagabond: <emoji>" line below the faction row.
    When Knaves of the Deepwood is drafted, `captains` is the rolled list of
    Vagabonds, shown as a "Captains: <emoji> …" line."""
    titles = {slug: title for slug, title, _type in factions}
    icons = [faction_emoji_for(s) or titles.get(s, s) for s in drawn]

    description = " ".join(icons)
    if vagabond is not None:
        description += f"\nVagabond: {vagabond_emoji_for(vagabond) or vagabond.title}"
    if captains:
        marks = " ".join(vagabond_emoji_for(c) or c.title for c in captains)
        description += f"\nCaptains: {marks}"

    footer = f"Platform: {platform}"
    if banned_slugs:
        footer += " • Banned: " + ", ".join(sorted(titles.get(s, s) for s in banned_slugs))

    embed = {
        "title": f"{players} Player Draft",
        "description": description,
        "footer": {"text": footer[:2048]},
    }
    if author:
        embed["author"] = author
    return embed


def _handle_draft_command(data):
    """/draft: open the public, owner-locked ban UI for the chosen players/platform."""
    players = _get_option(data, "players") or 4
    players = max(2, min(6, int(players)))
    platform = _get_option(data, "platform") or DRAFT_PLATFORM_TTS

    factions = _draft_eligible_factions(platform, players)
    if len(factions) < players + 1:  # 1 Militant + `players` more
        return _ephemeral(
            f"Only {len(factions)} eligible factions for {platform}"
            f"{' (Militant-only for 2 players)' if players == 2 else ''}; "
            f"need {players + 1} for a {players}-player draft."
        )

    return JsonResponse({
        "type": RESPONSE_CHANNEL_MESSAGE,
        "data": _draft_ui_data(players, platform, factions, banned_slugs=set(),
                               owner=data.get("_author_id")),
    })


def _handle_draft_select(payload):
    """Ban select changed: re-render the public UI with the chosen bans marked
    default=True, so the selection persists in the message's component state. The
    owner rides in the incoming custom_id; re-emit it to keep the controls locked."""
    custom_id = payload["data"]["custom_id"]
    _action, players, platform = _parse_draft_state(custom_id)
    _, args = decode_custom_id(custom_id)
    owner = args[-1] if args else None
    banned_slugs = set(payload["data"].get("values", []))  # this select echoes its own values
    factions = _draft_eligible_factions(platform, players)
    return JsonResponse({
        "type": RESPONSE_UPDATE_MESSAGE,
        "data": _draft_ui_data(players, platform, factions, banned_slugs, owner=owner),
    })


def _random_draft_vagabond(platform):
    """A random official, Stable Vagabond for a draft that landed the Vagabond
    faction. Root Digital narrows to vagabonds available there. Returns None if
    none qualify (the draft still shows, just without a vagabond line)."""
    qs = Vagabond.objects.filter(official=True, status=1)
    if platform == DRAFT_PLATFORM_RD:
        qs = qs.filter(in_root_digital=True)
    return qs.order_by("?").first()


# Knaves of the Deepwood selects from 4 captains.
DRAFT_CAPTAIN_COUNT = 4


def _random_draft_captains(platform):
    """Up to `DRAFT_CAPTAIN_COUNT` random captain-capable Vagabonds for a draft
    that landed Knaves of the Deepwood — the same pool the game form and /captain
    use (captain=True). Root Digital narrows to those available there. Returns as
    many as exist when fewer than 4 qualify (possibly an empty list)."""
    qs = Vagabond.objects.filter(official=True, status=1, captain=True)
    if platform == DRAFT_PLATFORM_RD:
        qs = qs.filter(in_root_digital=True)
    return list(qs.order_by("?")[:DRAFT_CAPTAIN_COUNT])


def _handle_draft_build(payload):
    """Build button: recover bans from the message's select state, build the draft,
    and edit the public prompt message into the result embed in place."""
    _action, players, platform = _parse_draft_state(payload["data"]["custom_id"])
    # A button press doesn't echo the select's values, so recover them from the
    # message's persisted select state.
    banned_slugs = set(selected_values(payload, "draft_select"))
    factions = _draft_eligible_factions(platform, players)

    drawn, error = _build_draft(factions, banned_slugs, players)
    if error:
        # Public edit (the message is public): show the error, clear the buttons.
        return JsonResponse({
            "type": RESPONSE_UPDATE_MESSAGE,
            "data": {"content": error, "embeds": [], "components": []},
        })

    # If the Vagabond faction was drafted, roll a specific vagabond to play it;
    # if Knaves of the Deepwood was drafted, roll its 4 captains. (The two are
    # mutually exclusive in a draft, so at most one of these applies.)
    vagabond = _random_draft_vagabond(platform) if "vagabond" in drawn else None
    captains = _random_draft_captains(platform) if "knaves-of-the-deepwood" in drawn else None

    # If used inside an LFG thread, record the drafted factions plus the rolled
    # vagabond / captains onto the LFGThread.
    titles = {slug: title for slug, title, _ftype in factions}
    items = [{"kind": "Faction", "slug": slug, "title": titles.get(slug, slug)} for slug in drawn]
    if vagabond:
        items.append(_lfg_item("Vagabond", vagabond))
    if captains:
        items.extend(_lfg_item("Captain", c) for c in captains)
    _capture_lfg_components(payload.get("channel_id"), items)

    embed = _draft_result_embed(
        drawn, players, platform, banned_slugs, factions,
        author=_interaction_author(payload), vagabond=vagabond, captains=captains,
    )
    # Edit the public prompt into the result: content "" clears the prompt text,
    # components [] removes the buttons. (No follow-up — the message is already public.)
    return JsonResponse({
        "type": RESPONSE_UPDATE_MESSAGE,
        "data": {"embeds": [embed], "content": "", "components": []},
    })


def _handle_draft_cancel(payload):
    """Cancel button: edit the public prompt to a short notice, buttons removed.
    Carries only `draft_cancel:{owner}` — deliberately does NOT call
    _parse_draft_state (which would misread the owner id as players/platform)."""
    return JsonResponse({
        "type": RESPONSE_UPDATE_MESSAGE,
        "data": {"content": "Draft cancelled.", "embeds": [], "components": []},
    })


# ── /random ────────────────────────────────────────────────────────────────
# Base queryset per post-backed kind. Faction is filtered to component="Faction"
# like /draft; Captain to captain-capable vagabonds (as /captain).
RANDOM_POST_MODELS = {
    "Map": lambda: Map.objects.all(),
    "Faction": lambda: Faction.objects.filter(component="Faction"),
    "Clockwork": lambda: Faction.objects.filter(component="Clockwork"),
    "Deck": lambda: Deck.objects.all(),
    "Vagabond": lambda: Vagabond.objects.all(),
    "Captain": lambda: Vagabond.objects.filter(captain=True),
    "Hireling": lambda: Hireling.objects.all(),
    "Landmark": lambda: Landmark.objects.all(),
}
RANDOM_SUITS = ["Bird", "Mouse", "Fox", "Rabbit"]
RANDOM_CLEARINGS = ["Mouse", "Fox", "Rabbit"]
# Embed color per suit (int, as Discord wants), for /random Suit and Clearing.
# Derived from the single source of truth in the_keep.models (CardTag.hex_for), so
# /random and /card can never drift apart.
RANDOM_SUIT_COLORS = {
    s: int(CardTag.hex_for(s).lstrip("#"), 16) for s in RANDOM_SUITS
}
RANDOM_PLATFORM_KEYS = DRAFT_PLATFORM_KEYS  # reuse tts/rd keys from /draft


def _random_platform_prompt(kind, owner):
    """Public Tabletop Simulator / Root Digital buttons for a post-backed kind.
    `owner` (invoker's user id) rides in each custom_id so only they can click."""
    row = action_row(
        button("Tabletop Simulator", encode_custom_id("random_post", kind, "tts", owner)),
        button("Root Digital", encode_custom_id("random_post", kind, "rd", owner)),
    )
    return JsonResponse({
        "type": RESPONSE_CHANNEL_MESSAGE,
        "data": {"content": f"Random {kind} - choose platform:", "components": [row]},
    })


def _random_dice_prompt(owner):
    """Public 1 Die / Both Dice buttons for /random Roll. `owner` rides in each
    custom_id so only the invoker can click."""
    row = action_row(
        button("1 Die", encode_custom_id("random_roll", "1", owner)),
        button("2 Dice", encode_custom_id("random_roll", "2", owner)),
    )
    return JsonResponse({
        "type": RESPONSE_CHANNEL_MESSAGE,
        "data": {"content": "Random Roll — how many dice?", "components": [row]},
    })


def _random_hireling_side_row(platform_key, owner):
    """Promoted / Demoted / Either buttons for /random Hirelings, carrying the
    already-chosen platform key and the owner forward in each custom_id
    (random_hireling:<key>:<side>:<owner>)."""
    return action_row(
        button("Promoted", encode_custom_id("random_hireling", platform_key, "P", owner)),
        button("Demoted", encode_custom_id("random_hireling", platform_key, "D", owner)),
        button("Either", encode_custom_id("random_hireling", platform_key, "E", owner)),
    )


def _random_result_embed(kind, title, subtext="", author=None, url=None,
                         image_url=None, thumbnail_url=None, color=None):
    """The unified /random result embed: the invoking user as the author header, a
    `Random {kind}: {title}` title (linked to the post when `url` is given), an
    optional large board/card `image_url` (post kinds) or small `thumbnail_url`
    (suit/clearing), the post's `color` when set, and `subtext` in the description
    body. Used by every /random kind so results share one look.

    Subtext lives in the description (not a footer) because that's the only place
    custom emoji render — so faction/suit/clearing icons show as images rather than
    literal text."""
    embed = {"title": f"Random {kind}: {title}"[:256]}
    if subtext:
        embed["description"] = subtext[:4096]
    if url:
        embed["url"] = url
    if author:
        embed["author"] = author
    if image_url:
        embed["image"] = {"url": image_url}
    if thumbnail_url:
        embed["thumbnail"] = {"url": thumbnail_url}
    if color is not None:
        embed["color"] = color
    return embed


def _random_from_list(kind, options, variant, thumb_variant, author=None):
    """Public result for Suit/Clearing (no post). The title carries the chosen name
    (readable), a thumbnail shows the chosen suit's static art (`thumb_variant` is
    "tilt" for suits / "outline" for clearings), and the "Chosen from" emoji list
    (`variant` "card"/"icon", name fallback) goes in the description body where
    custom emoji actually render."""
    chosen = random.choice(options)
    marks = " ".join(suit_emoji_for(o, variant) or o for o in options)
    embed = _random_result_embed(
        kind, chosen, f"Chosen from: {marks}", author=author,
        thumbnail_url=suit_static_image_url(chosen, thumb_variant),
        color=RANDOM_SUIT_COLORS.get(chosen),
    )
    return JsonResponse({"type": RESPONSE_CHANNEL_MESSAGE, "data": {"embeds": [embed]}})


def _handle_random_command(data):
    """/random: route by the chosen kind. Most post-backed kinds show an ephemeral
    platform prompt first; Captain isn't platform-specific so it resolves straight
    to a public result; Roll shows a dice prompt; Suit/Clearing resolve immediately."""
    kind = _get_option(data, "kind")
    author = data.get("_author")  # invoking user (embed author), stashed by the dispatch
    owner = data.get("_author_id")  # invoking user id, to owner-lock the prompts
    if kind == "Captain":
        # Captains don't vary by platform, so skip the prompt. Passing TTS avoids
        # the Root Digital (in_root_digital) filter in _random_eligible. This is a
        # component-less public result (type 4), so it needs no owner lock.
        result, error = _random_post_result("Captain", DRAFT_PLATFORM_TTS, author=author,
                                            channel_id=data.get("_channel_id"))
        if error:
            return _ephemeral(error)
        return JsonResponse({"type": RESPONSE_CHANNEL_MESSAGE, "data": result})
    if kind == "Hireling":
        # Hirelings need platform AND side; ask platform first, then the side.
        return _random_platform_prompt("Hireling", owner)
    if kind in RANDOM_POST_MODELS:
        return _random_platform_prompt(kind, owner)
    if kind == "Roll":
        return _random_dice_prompt(owner)
    # Suit/Clearing resolve immediately to a component-less public result — no owner.
    if kind == "Suit":
        return _random_from_list("Suit", RANDOM_SUITS, "card", "card", author=author)
    if kind == "Clearing":
        return _random_from_list("Clearing", RANDOM_CLEARINGS, "icon", "outline", author=author)
    return _ephemeral(f"Unknown random kind: {kind}")


def _random_eligible(kind, platform, hireling_type=None):
    """Eligible official, Stable posts for a random kind. Root Digital narrows to
    factions/posts available there. `hireling_type` ('P'/'D') narrows Hirelings to
    one side; None (or 'E') leaves both."""
    qs = RANDOM_POST_MODELS[kind]().filter(official=True, status=1)
    if platform == DRAFT_PLATFORM_RD:
        qs = qs.filter(in_root_digital=True)
    if kind == "Hireling" and hireling_type in ("P", "D"):
        qs = qs.filter(type=hireling_type)
    return qs


def _random_chosen_from(kind, posts):
    """The 'Chosen from' body text for a post-kind result. Faction -> emoji icons
    (name fallback), which is why this renders in the description not a footer;
    Hireling -> a count (there are many); other kinds -> names if <=6, else a count."""
    if kind == "Faction":
        icons = [faction_emoji_for(p.slug) or p.title for p in posts]
        return "Chosen from: " + " ".join(icons)
    if len(posts) <= 6:
        return "Chosen from: " + ", ".join(p.title for p in posts)
    return f"Chosen from {len(posts)} options"


def _post_url(post):
    """Absolute URL to a post's page, or None when SITE_URL isn't configured."""
    site_url = config.get("SITE_URL", "").rstrip("/")
    return f"{site_url}{post.get_absolute_url()}" if site_url else None


def _post_image_url(post, field=None):
    """Absolute URL to a post's large board/card image, or None. Reuses
    build_post_image_embed's per-component field mapping (board_image for
    Faction/Map/Hireling, card_image for Vagabond/Deck/Landmark); `field` overrides
    it (e.g. "card_2_image" for a captain's flip side)."""
    image_embed = build_post_image_embed(post, field=field)
    return (image_embed or {}).get("image", {}).get("url")


def _random_post_image_url(kind, post):
    """The large image url for a /random post result: the captain flip side for
    Captain, else the component's default board/card image."""
    return _post_image_url(post, field="card_2_image" if kind == "Captain" else None)


def _random_post_result(kind, platform, hireling_type=None, author=None, channel_id=None):
    """Return (message_data, error) for a post-backed random kind as the unified
    /random embed (linked title + large board/card image). `hireling_type` ('P'/'D')
    narrows Hirelings to one side. When `channel_id` is a known LFG thread, the
    chosen component is recorded on the LFGThread."""
    posts = list(_random_eligible(kind, platform, hireling_type))
    if not posts:
        # Only Captain skips the platform prompt; every other post kind (incl.
        # Hireling) picks a platform, so its "none found" error should mention it.
        where = " for that platform" if kind in RANDOM_POST_MODELS and kind != "Captain" else ""
        return None, f"No eligible {kind} found{where}."
    chosen = random.choice(posts)
    _capture_lfg_components(channel_id, [_lfg_item(kind, chosen)])
    embed = _random_result_embed(
        kind, chosen.title, _random_chosen_from(kind, posts),
        author=author, url=_post_url(chosen), image_url=_random_post_image_url(kind, chosen),
        color=embed_color(chosen),
    )
    return {"embeds": [embed]}, None


def _random_roll_embed(dice, author=None):
    """The unified /random embed for a roll of `dice` 0-3 dice; two dice show the
    larger first. The die-face emoji appear in the body in the same order as the
    title. No post, so no link/image."""
    rolls = [random.randint(0, 3) for _ in range(dice)]
    if dice == 2:
        faces = sorted(rolls, reverse=True)  # larger first, matching the title
        value, sub = f"{faces[0]}-{faces[1]}", "Rolled 2 dice"
    else:
        faces = rolls
        value, sub = str(faces[0]), "Rolled 1 die"
    # Die-face emoji above the "Rolled N dice" subtext (only place emoji render).
    marks = " ".join(roll_emoji_for(f) for f in faces if roll_emoji_for(f))
    description = f"{marks}\n{sub}" if marks else sub
    return _random_result_embed("Roll", value, description, author=author)


def _random_result_edit(payload, message_data):
    """Edit the public prompt message into the /random result. `message_data` is
    `{"embeds": [embed]}`; we clear the prompt content and buttons so the message
    becomes the result in place (no separate follow-up — it's already public)."""
    return JsonResponse({
        "type": RESPONSE_UPDATE_MESSAGE,
        "data": {**message_data, "content": "", "components": []},
    })


def _random_error_edit(error):
    """Public edit showing a /random error, buttons and any embed cleared."""
    return JsonResponse({
        "type": RESPONSE_UPDATE_MESSAGE,
        "data": {"content": error, "embeds": [], "components": []},
    })


def _handle_random_post(payload):
    """Platform button: pick a random post for the kind and edit the prompt into
    the result. For Hirelings the platform choice edits to a second prompt (the
    side) rather than a result."""
    _action, args = decode_custom_id(payload["data"]["custom_id"])  # ["<Kind>", "tts|rd", owner]
    kind = args[0]
    platform_key = args[1] if len(args) > 1 else "tts"
    if kind == "Hireling":
        # Platform chosen; edit to the side prompt, carrying platform and owner forward.
        return JsonResponse({
            "type": RESPONSE_UPDATE_MESSAGE,
            "data": {"content": "Random Hireling — which side?",
                     "components": [_random_hireling_side_row(platform_key, args[-1])]},
        })
    platform = RANDOM_PLATFORM_KEYS.get(platform_key, DRAFT_PLATFORM_TTS)
    result, error = _random_post_result(kind, platform, author=_interaction_author(payload),
                                        channel_id=payload.get("channel_id"))
    if error:
        return _random_error_edit(error)
    return _random_result_edit(payload, result)


def _handle_random_hireling(payload):
    """Side button: pick a random hireling of the chosen platform and side
    (Promoted/Demoted/Either) and edit the prompt into the result."""
    _action, args = decode_custom_id(payload["data"]["custom_id"])  # ["tts|rd", "P"|"D"|"E", owner]
    platform = RANDOM_PLATFORM_KEYS.get(args[0] if args else "", DRAFT_PLATFORM_TTS)
    hireling_type = args[1] if len(args) > 1 else "E"  # default to Either
    result, error = _random_post_result(
        "Hireling", platform, hireling_type=hireling_type, author=_interaction_author(payload),
        channel_id=payload.get("channel_id"),
    )
    if error:
        return _random_error_edit(error)
    return _random_result_edit(payload, result)


def _handle_random_roll(payload):
    """Dice button: roll one or two dice and edit the prompt into the result."""
    _action, args = decode_custom_id(payload["data"]["custom_id"])  # ["1"|"2", owner]
    dice = 1 if args and args[0] == "1" else 2  # default to 2 (Two Dice) on anything else
    embed = _random_roll_embed(dice, author=_interaction_author(payload))
    return _random_result_edit(payload, {"embeds": [embed]})


# ── /lfg ─────────────────────────────────────────────────────────────────────
# A Looking-For-Game post. The message is stateless: the Players and Notify lists
# live in embed fields and are parsed back out on each interaction. Buttons:
#   Join / Notify  — clickable by ANYONE (custom_id ends in the non-snowflake "g",
#                    so the dispatcher owner-lock does not fire).
#   ❌ Cancel / ✅ Start — owner-only (owner snowflake is the last custom_id arg,
#                    which the dispatcher owner-lock enforces before the handler).
LFG_PLAYERS_FIELD = "Players"
LFG_NOTIFY_FIELD = "🔔 Notify"
LFG_DEFAULT_TITLE = "Looking for Game"
_LFG_MENTION_RE = re.compile(r"<@!?(\d+)>")
_LFG_ROLE_MENTION_RE = re.compile(r"<@&(\d+)>")
_LFG_PLAYER_LINE_RE = re.compile(r"^(.*) \(<@!?(\d+)>\)$")


def _lfg_field(embed, name):
    """The embed field dict with this name, or None."""
    for f in embed.get("fields", []):
        if f.get("name") == name:
            return f
    return None


def _lfg_ids_in_field(embed, name):
    """Set of user ids mentioned in the named field's value."""
    field = _lfg_field(embed, name)
    if not field:
        return set()
    return set(_LFG_MENTION_RE.findall(field.get("value", "")))


def _lfg_player_lines(embed):
    """[{"id","name"}] parsed from the Players field lines."""
    field = _lfg_field(embed, LFG_PLAYERS_FIELD)
    players = []
    if not field:
        return players
    for line in field.get("value", "").splitlines():
        m = _LFG_PLAYER_LINE_RE.match(line.strip())
        if m:
            players.append({"name": m.group(1), "id": m.group(2)})
    return players


def _lfg_player_line(name, uid):
    return f"{name} (<@{uid}>)"


def _lfg_member_display_name(payload):
    """The clicker's guild display name: member nick, then global_name, then username."""
    member = payload.get("member") or {}
    user = member.get("user") or payload.get("user") or {}
    return member.get("nick") or user.get("global_name") or user.get("username") or "Player"


def _lfg_set_notify_ids(embed, ids):
    """Write the Notify subscriber set into the embed. The Notify field is only
    present when it has subscribers: add it (right after Players) when the first
    person subscribes, and drop it entirely when the last one leaves."""
    fields = embed.setdefault("fields", [])
    idx = next((i for i, f in enumerate(fields) if f.get("name") == LFG_NOTIFY_FIELD), None)
    if not ids:
        if idx is not None:
            fields.pop(idx)
        return
    value = " ".join(f"<@{i}>" for i in ids)
    if idx is None:
        # Insert just after the Players field (or at the end if it's missing).
        p = next((i for i, f in enumerate(fields) if f.get("name") == LFG_PLAYERS_FIELD), len(fields) - 1)
        fields.insert(p + 1, {"name": LFG_NOTIFY_FIELD, "value": value, "inline": False})
    else:
        fields[idx]["value"] = value


def _lfg_message_data(author, owner, description, players_value,
                      content=None, title=LFG_DEFAULT_TITLE):
    """Build the full join-message payload (embed + button row). Used ONLY for the
    initial post and the picker→join transition — never to re-render on Join/Notify
    (that would wipe the other field; those handlers mutate the echoed embed).

    The Notify field is omitted until someone subscribes (added on first 🔔)."""
    embed = {
        "author": author,
        "title": title,
        "description": description,
        "fields": [
            {"name": LFG_PLAYERS_FIELD, "value": players_value, "inline": False},
        ],
    }
    # Join and 🔔 end in the non-snowflake "g" marker so the dispatcher owner-lock
    # does NOT fire — anyone may click them (they toggle: Join = join/leave, 🔔 =
    # subscribe/unsubscribe; the owner rides in a non-last arg so those handlers can
    # still identify the host). ✖ Cancel and ✔ Start end in the owner snowflake, so
    # the dispatcher owner-locks them — only the host can cancel or start.
    row = action_row(
        button("Join", encode_custom_id("lfg_join", owner, "g"), style=STYLE_PRIMARY),
        button("Notify", encode_custom_id("lfg_notify", owner, "g"),
               style=STYLE_SECONDARY, emoji={"name": "🔔"}),
        button("", encode_custom_id("lfg_cancel", owner), style=STYLE_DANGER, emoji={"name": "✖"}),
        button("", encode_custom_id("lfg_start", owner), style=STYLE_SUCCESS, emoji={"name": "✔"}),
    )
    data = {"embeds": [embed], "components": [row]}
    if content:
        data["content"] = content
        # Authorize the role ping. This is always a fresh channel message (the tag is
        # chosen up front in /lfg), so the mention notifies natively.
        data["allowed_mentions"] = {"parse": ["roles"]}
    return data


def _lfg_role_is_live(role, guild_id):
    """Whether the chosen tag's Discord role still exists, so we can ping it. A blank
    role_id has nothing to ping (mention() is a plain name) → treat as live. On a role
    fetch failure (None) we can't tell, so assume live rather than silently drop the
    ping during a transient Discord/network error. Only a definitive 'not in the live
    set' suppresses the ping — avoids rendering a broken @deleted-role mention."""
    if not role.role_id:
        return True
    live = get_guild_roles(guild_id)
    if live is None:
        return True
    return role.role_id in {r["id"] for r in live}


def _handle_lfg_command(data):
    """/lfg: post a Looking-For-Game call. The tag (if any) is chosen up front via the
    `type` option (present only when the guild has 2+ tags); with one tag it's used
    automatically, with none it's a plain post. When there are no tags and the invoker
    can manage the server, an ephemeral followup links them to add some."""
    description = (_get_option(data, "description") or "").strip()
    author = data.get("_author")
    owner = data.get("_author_id")
    if not owner:
        return _ephemeral("Couldn't identify you, try again.")

    # Onboard the invoker to the site (fire-and-forget).
    ensure_profile_from_discord_task.delay(owner, data.get("_author_username"),
                                           (author or {}).get("name"))

    guild_id = data.get("_guild_id")
    guild = DiscordGuild.objects.filter(guild_id=guild_id).first()
    roles = list(guild.lfg_roles.all()) if guild else []
    players_value = _lfg_player_line(_author_display_from_data(data), owner)

    def plain_post():
        return JsonResponse({
            "type": RESPONSE_CHANNEL_MESSAGE,
            "data": _lfg_message_data(author, owner, description, players_value),
        })

    # No tags configured. Post the plain call; if the invoker can manage the server,
    # nudge them (ephemerally) to add LFG tags so future calls can ping.
    if not roles:
        if permissions_can_manage_guild(data.get("_member_permissions")):
            site_url = config.get("SITE_URL", "").rstrip("/")
            token = data.get("_token")
            if site_url and guild_id and token:
                manage_url = f"{site_url}/guild/{guild_id}/edit/"
                # Sequence after the ACK (a followup before it 404s); the small countdown
                # lets the initial response reach Discord first.
                post_interaction_followup_task.apply_async(
                    (token, {
                        "content": f"Add LFG tags to ping members when someone posts a "
                                   f"game: {manage_url}",
                        "flags": EPHEMERAL,
                    }),
                    countdown=2,
                )
        return plain_post()

    # Resolve the chosen tag. `type` is only present in the MULTI variant (2+ tags);
    # its value is a GuildLFGRole pk. With one tag (SINGLE variant) there's no `type`
    # option, so fall back to the sole role.
    type_val = _get_option(data, "type")
    if type_val:
        role = GuildLFGRole.objects.filter(pk=type_val, guild=guild).first()
        # Stale choice: the tag was deleted between registration and use. Don't silently
        # ping a different tag — post plain.
        if role is None:
            return plain_post()
    else:
        role = roles[0]

    title = role.description or role.name or LFG_DEFAULT_TITLE
    # Ping only if the underlying Discord role still exists; otherwise post with the tag
    # name as the title but no mention (avoids a broken @deleted-role ping).
    content = role.mention() if _lfg_role_is_live(role, guild_id) else None
    return JsonResponse({
        "type": RESPONSE_CHANNEL_MESSAGE,
        "data": _lfg_message_data(author, owner, description, players_value,
                                  content=content, title=title),
    })


def _author_display_from_data(data):
    """The invoker's guild display name for the initial Players line. The command
    payload doesn't carry member.nick down to `data`, so use the author embed name
    (global_name/username) — good enough for the poster's own line."""
    return (data.get("_author") or {}).get("name") or "Player"


def _lfg_jump_url(payload):
    guild_id = payload.get("guild_id")
    channel_id = payload.get("channel_id")
    message_id = (payload.get("message") or {}).get("id")
    if channel_id and message_id:
        gid = guild_id or "@me"
        return f"https://discord.com/channels/{gid}/{channel_id}/{message_id}"
    return None


def _handle_lfg_join(payload):
    """Join (anyone, toggle): a player already in the game LEAVES; anyone else JOINS
    (added to Players, notify subscribers DM'd, clicker onboarded). The owner can't
    leave via Join — they're told to use ✖ to cancel. Mutates the echoed embed so the
    Notify field is preserved."""
    try:
        embed = payload["message"]["embeds"][0]
        clicker = _interaction_user_id(payload)
        field = _lfg_field(embed, LFG_PLAYERS_FIELD)
        if field is None:
            return _ephemeral("Couldn't update the game, try again.")

        # The game owner rides in the Join button's custom_id (lfg_join:{owner}:g).
        _action, args = decode_custom_id(payload["data"].get("custom_id", ""))
        owner_id = args[0] if args else None

        players = _lfg_player_lines(embed)
        joined = {p["id"] for p in players}

        # Already in the game → leave (unless owner, who cancels via ✖ instead).
        if clicker in joined:
            if clicker == owner_id:
                return _ephemeral("You're hosting this game — you can't leave it, "
                                  "but you can cancel it with ✖.")
            remaining = [p for p in players if p["id"] != clicker]
            field["value"] = "\n".join(_lfg_player_line(p["name"], p["id"]) for p in remaining) or "—"
            return JsonResponse({
                "type": RESPONSE_UPDATE_MESSAGE,
                "data": {"embeds": [embed]},
            })

        # Not in the game → join.
        display = _lfg_member_display_name(payload)
        existing = field.get("value", "").strip()
        existing = "" if existing == "—" else existing
        new_line = _lfg_player_line(display, clicker)
        new_value = (existing + "\n" if existing else "") + new_line
        # Discord caps an embed field value at 1024 chars; refuse the join rather
        # than send an edit Discord would reject (which would drop the whole update).
        if len(new_value) > 1024:
            return _ephemeral("This game already has too many players to add you.")
        field["value"] = new_value

        # Notify the subscribers (excluding the joiner), and onboard the joiner.
        # Pass the owner so the host gets a host-specific DM (they can start the game).
        notify_ids = list(_lfg_ids_in_field(embed, LFG_NOTIFY_FIELD) - {clicker})
        if notify_ids:
            notify_lfg_task.delay(notify_ids, display, embed.get("description", ""),
                                  _lfg_jump_url(payload), owner_id)
        user = (payload.get("member") or {}).get("user") or {}
        ensure_profile_from_discord_task.delay(clicker, user.get("username"), display)

        return JsonResponse({
            "type": RESPONSE_UPDATE_MESSAGE,
            "data": {"embeds": [embed]},
        })
    except (KeyError, IndexError, TypeError):
        logger.exception("Error handling lfg_join")
        return _ephemeral("Couldn't update the game, try again.")


def _handle_lfg_notify(payload):
    """Notify (anyone, toggle): subscribe the clicker to join DMs, or unsubscribe if
    they're already on the list. Does NOT add them to Players. Mutates the echoed
    embed (Notify field is hidden while empty). Preserves the Players field."""
    try:
        embed = payload["message"]["embeds"][0]
        clicker = _interaction_user_id(payload)
        # Preserve order: parse the current subscriber ids, then toggle the clicker.
        field = _lfg_field(embed, LFG_NOTIFY_FIELD)
        ids = _LFG_MENTION_RE.findall(field.get("value", "")) if field else []
        if clicker in ids:
            ids = [i for i in ids if i != clicker]  # unsubscribe
        else:
            ids.append(clicker)  # subscribe
            display = _lfg_member_display_name(payload)
            user = (payload.get("member") or {}).get("user") or {}
            ensure_profile_from_discord_task.delay(clicker, user.get("username"), display)
        _lfg_set_notify_ids(embed, ids)

        return JsonResponse({
            "type": RESPONSE_UPDATE_MESSAGE,
            "data": {"embeds": [embed]},
        })
    except (KeyError, IndexError, TypeError):
        logger.exception("Error handling lfg_notify")
        return _ephemeral("Couldn't update the game, try again.")


def _handle_lfg_cancel(payload):
    """✖ Cancel (owner-only, enforced by the dispatcher owner-lock): remove the
    buttons and note the game was cancelled. Players leave via the Join toggle."""
    embed = (payload.get("message", {}).get("embeds") or [{}])[0]
    # Status subtext goes in the embed footer (small text at the very bottom).
    # Use the monochrome ✖ to match the Cancel button glyph.
    embed["footer"] = {"text": "✖ Game was cancelled."}
    # The Notify list is only useful while recruiting; drop it once cancelled.
    _lfg_set_notify_ids(embed, [])
    return JsonResponse({
        "type": RESPONSE_UPDATE_MESSAGE,
        "data": {"embeds": [embed], "components": []},
    })


def _handle_lfg_start(payload):
    """Start (owner-only): remove the buttons, mark started, and offload thread
    creation + LFGThread persistence to Celery (keeps within the 3s ack window)."""
    message = payload.get("message", {})
    embed = (message.get("embeds") or [{}])[0]
    players = _lfg_player_lines(embed)
    description = embed.get("description", "")

    # Status subtext goes in the embed footer (small text at the very bottom).
    # Use the monochrome ✔ to match the Start button glyph.
    embed["footer"] = {"text": "✔ Game has started."}
    # The Notify list is only useful while recruiting; drop it once started.
    _lfg_set_notify_ids(embed, [])

    role_match = _LFG_ROLE_MENTION_RE.search(message.get("content", "") or "")
    role_id = role_match.group(1) if role_match else None

    # Pass the started embed so the task can re-edit it with the title linked to the
    # thread once the thread id is known (the thread is created in the task). The
    # interaction token lets the task send the owner an ephemeral notice if thread
    # creation fails (e.g. missing channel permissions).
    create_lfg_thread_task.delay(
        payload.get("channel_id"), message.get("id"), payload.get("guild_id"),
        role_id, description, players, embed, token=payload.get("token"),
    )
    return JsonResponse({
        "type": RESPONSE_UPDATE_MESSAGE,
        "data": {"embeds": [embed], "components": []},
    })


COMMAND_HANDLERS = {
    name: _make_lookup_handler(_LOOKUP_LABELS[name], qs)
    for name, qs in LOOKUP_QUERYSETS.items()
}
COMMAND_HANDLERS["stats"] = _handle_stats_command
COMMAND_HANDLERS["captain"] = _handle_captain_command
COMMAND_HANDLERS["card"] = _handle_card_command
COMMAND_HANDLERS["law"] = _handle_law_command
COMMAND_HANDLERS["help"] = _handle_help_command
COMMAND_HANDLERS["upcoming"] = _handle_upcoming_command
COMMAND_HANDLERS["schedule"] = _handle_schedule_command
COMMAND_HANDLERS["draft"] = _handle_draft_command
COMMAND_HANDLERS["random"] = _handle_random_command
COMMAND_HANDLERS["lfg"] = _handle_lfg_command


# Component (button/select) handlers, keyed by the custom_id's action prefix.
COMPONENT_HANDLERS = {
    "draft_select": _handle_draft_select,
    "draft_build": _handle_draft_build,
    "draft_cancel": _handle_draft_cancel,
    "random_post": _handle_random_post,
    "random_roll": _handle_random_roll,
    "random_hireling": _handle_random_hireling,
    "schedule_confirm": _handle_schedule_confirm,
    "schedule_clear_confirm": _handle_schedule_clear_confirm,
    "schedule_cancel": _handle_schedule_cancel,
    "lfg_join": _handle_lfg_join,
    "lfg_notify": _handle_lfg_notify,
    "lfg_cancel": _handle_lfg_cancel,
    "lfg_start": _handle_lfg_start,
}


# ── Autocomplete ──────────────────────────────────────────────────────────
# Every handler takes (query, data): `query` is the focused option's current
# value; `data` is the full interaction data, which carries the other options
# the user has already filled in (e.g. the chosen `language`).
def _title_ac(queryset_factory):
    """Autocomplete handler for a lookup command's `name` option: suggests
    matching titles. Value is the title itself (unique by convention)."""
    def ac(query, _data):
        qs = queryset_factory().filter(status__lte=4)
        if query:
            qs = qs.filter(title__icontains=query)
        # No explicit order_by: use the model's default Meta.ordering so results
        # match the site's listing order.
        titles = qs.values_list("title", flat=True)[:25]
        return [{"name": t, "value": t} for t in titles]
    return ac


def _ac_captains(query, _data):
    """Autocomplete for /captain: only published, captain-capable vagabonds."""
    qs = Vagabond.objects.filter(status__lte=4, captain=True)
    if query:
        qs = qs.filter(title__icontains=query)
    titles = qs.values_list("title", flat=True)[:25]
    return [{"name": t, "value": t} for t in titles]


def _ac_card_name(query, data):
    """Autocomplete for /card `name`: de-duplicated card names (many decks share a
    name). Respects an already-chosen `from` post. Deliberately does NOT apply the
    `tag` filter — JSONField `tags__contains` is Postgres-only and would error on
    every keystroke on the SQLite dev backend; the tag filter runs in the handler."""
    qs = Card.objects.filter(group__post__status__lte=4)
    if query:
        qs = qs.filter(name__icontains=query)
    from_slug = _get_option(data, "from")
    if from_slug:
        qs = qs.filter(group__post__slug=from_slug)
    names = (qs.exclude(name__isnull=True).exclude(name="")
               .order_by("name").values_list("name", flat=True).distinct()[:25])
    return [{"name": n, "value": n} for n in names]


def _ac_card_from(query, _data):
    """Autocomplete for /card `from`: only published posts that actually have cards.
    Value is the post slug. Uses Exists (no .distinct()) to avoid a Postgres
    DISTINCT+ORDER-BY conflict with Post's default Meta.ordering."""
    has_cards = Card.objects.filter(group__post=OuterRef("pk"))
    qs = Post.objects.filter(Exists(has_cards), status__lte=4).exclude(slug__isnull=True)
    if query:
        qs = qs.filter(title__icontains=query)
    rows = qs.values_list("title", "slug")[:25]
    return [{"name": title, "value": slug} for title, slug in rows]


def _ac_players(query, _data):
    qs = Profile.objects.exclude(slug__isnull=True)
    if query:
        qs = qs.filter(Q(display_name__icontains=query) | Q(discord__icontains=query))
    rows = qs.order_by("display_name").values_list("display_name", "discord", "slug")[:25]
    return [{"name": (dn or disc or slug), "value": slug} for dn, disc, slug in rows]


def _ac_upcoming_player(query, data):
    """Autocomplete for /upcoming `player`: only players who appear in at least
    one upcoming (future, not completed) scheduled match, so you can't pick a
    player with nothing scheduled. If a series is already selected, narrows to
    players with an upcoming match in that series. Mirrors the player path used
    by the /upcoming handler: seated players via series__matchseat, so only the
    players actually in a scheduled match surface (not the whole tournament roster)."""
    matches = Match.objects.filter(
        series__matchseat__stage_participant__tournament_player__profile=OuterRef("pk"),
        scheduled_time__gte=timezone.now() - UPCOMING_GRACE,
    ).exclude(status=CompetitionStatus.COMPLETED)

    series_slug = _get_option(data, "series")
    if series_slug:
        matches = matches.filter(
            Q(round__stage__tournament__slug=series_slug)
            | Q(round__tournament__slug=series_slug),
        )

    qs = Profile.objects.exclude(slug__isnull=True).filter(Exists(matches))
    if query:
        qs = qs.filter(Q(display_name__icontains=query) | Q(discord__icontains=query))
    rows = qs.order_by("display_name").values_list("display_name", "discord", "slug")[:25]
    return [{"name": (dn or disc or slug), "value": slug} for dn, disc, slug in rows]


def _ac_factions(query, _data):
    qs = Faction.objects.filter(status__lte=4).exclude(slug__isnull=True)
    if query:
        qs = qs.filter(title__icontains=query)
    rows = qs.values_list("title", "slug")[:25]
    return [{"name": title, "value": slug} for title, slug in rows]


def _ac_series(query, _data):
    qs = Tournament.objects.exclude(slug__isnull=True)
    if query:
        qs = qs.filter(name__icontains=query)
    rows = qs.order_by("name").values_list("name", "slug")[:25]
    return [{"name": name, "value": slug} for name, slug in rows]


def _ac_upcoming_series(query, _data):
    """Autocomplete for /upcoming `series`: only tournaments that have at least
    one upcoming (future, not completed) scheduled match — so you can't pick a
    series with nothing scheduled. A round links to its tournament directly or
    through its stage, so match either path."""
    upcoming = Match.objects.filter(
        Q(round__stage__tournament=OuterRef("pk")) | Q(round__tournament=OuterRef("pk")),
        scheduled_time__gte=timezone.now() - UPCOMING_GRACE,
    ).exclude(status=CompetitionStatus.COMPLETED)
    qs = Tournament.objects.exclude(slug__isnull=True).filter(Exists(upcoming))
    if query:
        qs = qs.filter(name__icontains=query)
    rows = qs.order_by("name").values_list("name", "slug")[:25]
    return [{"name": name, "value": slug} for name, slug in rows]


def _ac_law(query, _data):
    """Autocomplete for the combined /law `law` option: matches on code or title
    so typing either surfaces suggestions. Labels as "CODE - Title" and sends the
    law's id as the value, so picking a suggestion resolves to exactly that law
    (the code keeps rows unique, so no dedup is needed)."""
    qs = _public_laws()
    if query:
        qs = qs.filter(
            Q(law_code__icontains=query)
            | Q(plain_title__icontains=query)
            | Q(title__icontains=query)
        )
    rows = qs.values_list("id", "law_code", "plain_title", "title")[:25]
    choices = []
    for law_id, code, plain, title in rows:
        name = (plain or title or "").strip()
        if not name:
            continue
        label = (f"{code} - {name}" if code else name)[:100]
        choices.append({"name": label, "value": str(law_id)})
    return choices


def _ac_law_post(query, _data):
    qs = Post.objects.filter(
        Q(lawgroup__public=True, lawgroup__laws__language__code="en")
        | Q(linked_laws__group__public=True, linked_laws__language__code="en")
    ).distinct()
    if query:
        qs = qs.filter(title__icontains=query)
    rows = qs.exclude(slug__isnull=True).values_list("title", "slug")[:25]
    return [{"name": title, "value": slug} for title, slug in rows]


# Keyed by (command_name, focused_option_name) — the lookup commands all share
# an option literally named "name", so the option name alone isn't enough.
AUTOCOMPLETE_HANDLERS = {
    ("stats", "player"): _ac_players,
    ("stats", "faction"): _ac_factions,
    ("stats", "series"): _ac_series,
    ("captain", "name"): _ac_captains,
    ("card", "name"): _ac_card_name,
    ("card", "from"): _ac_card_from,
    ("upcoming", "series"): _ac_upcoming_series,
    ("upcoming", "player"): _ac_upcoming_player,
    ("schedule", "timezone"): _ac_schedule_timezone,
    ("law", "law"): _ac_law,
    ("law", "post"): _ac_law_post,
}
for _name, _qs in LOOKUP_QUERYSETS.items():
    AUTOCOMPLETE_HANDLERS[(_name, "name")] = _title_ac(_qs)


@csrf_exempt
@require_POST
def discord_interactions(request):
    if not _verify_signature(request):
        return HttpResponse("invalid request signature", status=401)

    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return HttpResponse("bad request", status=400)

    interaction_type = payload.get("type")

    if interaction_type == PING:
        return JsonResponse({"type": RESPONSE_PONG})

    user_id = _interaction_user_id(payload)
    guild_id = payload.get("guild_id")

    # Blacklist gate: refuse blocked users/guilds across every interaction type.
    # Autocomplete (type 4) must return a well-formed empty choices list, not an
    # ephemeral, or Discord rejects the response.
    if _is_blacklisted(user_id, guild_id):
        if interaction_type == APPLICATION_COMMAND_AUTOCOMPLETE:
            return JsonResponse({"type": RESPONSE_AUTOCOMPLETE_RESULT, "data": {"choices": []}})
        return _ephemeral("You're blocked from using this bot.")

    if interaction_type == APPLICATION_COMMAND:
        data = payload.get("data", {})
        command_name = data.get("name")
        handler = COMMAND_HANDLERS.get(command_name)
        if handler:
            # Record usage (per guild/user/command) asynchronously — fire-and-forget
            # so the DB write never delays the 3s response. Only known top-level
            # commands are counted (not buttons or autocomplete).
            record_bot_usage_task.delay(guild_id, user_id, command_name)
            try:
                # Stash the invoking user (from the top-level payload, not `data`)
                # so handlers can build author-attributed embeds (_author) and
                # owner-lock the prompts they post (_author_id). Also stash the guild
                # (for /lfg role lookup + invoker onboarding), the invoker's username
                # (onboarding), and the channel id (so /random Captain — resolved in
                # the command handler — can capture into an LFG thread).
                member_user = (payload.get("member") or {}).get("user") or payload.get("user") or {}
                data["_author"] = _interaction_author(payload)
                data["_author_id"] = _interaction_user_id(payload)
                data["_guild_id"] = guild_id
                data["_author_username"] = member_user.get("username")
                data["_channel_id"] = payload.get("channel_id")
                # Discord sends the full partial channel object on every
                # interaction; the name and type let /schedule match a thread by
                # its title (and tell a thread from a plain channel) without an
                # API round-trip. Types 10/11/12 are threads.
                channel = payload.get("channel") or {}
                data["_channel_name"] = channel.get("name")
                data["_channel_type"] = channel.get("type")
                # The invoker's computed permissions in this channel (Discord resolves
                # roles/owner/admin for us). Lets /help decide, without an API call,
                # whether to offer the "enable more commands" link.
                data["_member_permissions"] = (payload.get("member") or {}).get("permissions")
                # Interaction token, so a handler can send a followup after its ACK
                # (e.g. /lfg's ephemeral "add tags" nudge).
                data["_token"] = payload.get("token")
                return handler(data)
            except Exception:
                logger.exception("Error handling /%s interaction", command_name)
                return _ephemeral("Something went wrong handling that command.")
        return _ephemeral(f"Unknown command: {command_name}")

    if interaction_type == APPLICATION_COMMAND_AUTOCOMPLETE:
        data = payload.get("data", {})
        command_name = data.get("name")
        focused = next((o for o in data.get("options", []) if o.get("focused")), None)
        choices = []
        if focused:
            handler = AUTOCOMPLETE_HANDLERS.get((command_name, focused["name"]))
            if handler:
                try:
                    choices = handler(focused.get("value", ""), data)
                except Exception:
                    logger.exception(
                        "autocomplete error for /%s %s", command_name, focused.get("name")
                    )
        return JsonResponse({
            "type": RESPONSE_AUTOCOMPLETE_RESULT,
            "data": {"choices": choices},
        })

    if interaction_type == MESSAGE_COMPONENT:
        data = payload.get("data", {})
        custom_id = data.get("custom_id", "")
        action, args = decode_custom_id(custom_id)
        handler = COMPONENT_HANDLERS.get(action)
        if handler:
            # Every /draft and /random component custom_id carries the invoking
            # user's id (a 17-20 digit snowflake) as its LAST arg. Only that user
            # may operate the controls. We require the last arg to LOOK like a
            # snowflake so we don't mistake a state arg (tts/rd, P/D/E, 1/2) on a
            # stale pre-deploy custom_id for an owner — those fall through
            # permissively rather than locking the original user out.
            last = args[-1] if args else ""
            owner_id = last if (last.isdigit() and len(last) >= 17) else None
            clicker_id = _interaction_user_id(payload)
            if owner_id and clicker_id and clicker_id != owner_id:
                return _ephemeral("Only the commander can use this button.")
            try:
                return handler(payload)  # component handlers take the full payload
            except Exception:
                logger.exception("Error handling component %s", custom_id)
                return _ephemeral("Something went wrong handling that.")
        return _ephemeral(f"Unknown component: {custom_id}")

    # Unhandled interaction type
    return HttpResponse("unhandled interaction type", status=400)
