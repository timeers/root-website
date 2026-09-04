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
                                          /schedule, /law, /card, /captain,
                                          /draft, /random, /lfg, /help)
  MESSAGE_COMPONENT (type 3)           -> button/select clicks, dispatched by the
                                          custom_id's leading action
  APPLICATION_COMMAND_AUTOCOMPLETE (4) -> live option suggestions (type 8)
"""
import json
import logging
import math
import random
import re
from datetime import datetime, timedelta, timezone as dt_timezone

import requests
from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError

from django.core.cache import cache
from django.db import transaction
from django.db.models import Q, Exists, OuterRef
from django.http import JsonResponse, HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from the_keep.models import Faction, Map, Deck, Vagabond, Landmark, Hireling, Tweak, Law, Post, Card, CardTag
from the_warroom.models import (
    Tournament, Match, CompetitionStatus, filtered_winrate, EloParticipant, Effort,
)
from the_gatehouse.models import Profile, DiscordGuild
from the_databot.models import (
    BotBlacklist, GuildLFGRole, LFGThread, ScheduleProposal,
    LFGSeat, LFGRoll, LFGDraft,
)
from .tasks import (
    record_bot_usage_task, ensure_profile_from_discord_task,
    ensure_profile_from_discord, notify_lfg_task, notify_lfg_cancelled_task,
    notify_schedule_poll_task,
    create_lfg_thread_task, record_lfg_components_task, post_interaction_followup_task,
    post_channel_message_task, post_schedule_proposal_task,
    strip_schedule_proposal_messages_task,
)
from .services.discordservice import (
    config, build_post_embed, build_post_image_embed, build_stats_embed,
    build_captain_embed, build_card_embed, build_law_embed, build_help_embed,
    build_lfg_help_embed, build_upcoming_embed,
    faction_emoji_for, faction_emoji_object, vagabond_emoji_for, suit_emoji_for,
    parse_emoji_object,
    roll_emoji_for, suit_static_image_url, embed_color, permissions_can_manage_guild,
    get_guild_roles, rename_channel, THREAD_OK, THREAD_BLOCKED,
    edit_channel_message,
)
from the_databot.services.discord_commands import (
    DRAFT_PLATFORM_TTS, DRAFT_PLATFORM_RD, HELP_CATEGORY_LFG,
)
from the_databot.services.time_parsing import (
    NEED_TIMEZONE, parse_user_datetime, format_discord_timestamp,
    valid_timezone, search_timezones,
    timezone_regions, zones_for_region, region_for_timezone,
    describe_timezone, format_utc_offset,
)
from the_databot.services.discord_components import (
    action_row, button, string_select, select_option,
    encode_custom_id, decode_custom_id, selected_values,
    RESPONSE_UPDATE_MESSAGE, STYLE_PRIMARY, STYLE_SUCCESS, STYLE_SECONDARY, STYLE_DANGER,
)
from the_databot.services.lfg_game import (
    player_group_for_channel, link_group_thread, normalize_title,
    group_roster, group_series_id, undrafted_pick,
    roster_name, name_list_value, FIELD_VALUE_MAX, match_label,
    schedule_closed_embed, name_join,
    POLL_YES_FIELD, POLL_NO_FIELD, POLL_PENDING_FIELD, POLL_NOTIFY_FIELD,
    poll_count_label, poll_response_fields,
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


def _get_attachment(data, name):
    """The resolved attachment dict for an ATTACHMENT (type 11) option, or None.

    An attachment option's VALUE is the attachment's id; the metadata that
    matters -- filename, size, url -- lives in data["resolved"]["attachments"],
    keyed by that id. The dispatcher stashes its own "_" keys into `data` but
    never touches "resolved", so it arrives here intact.
    """
    attachment_id = _get_option(data, name)
    if not attachment_id:
        return None
    attachments = (data.get("resolved") or {}).get("attachments") or {}
    return attachments.get(str(attachment_id))


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
            _capture_lfg_components(data.get("_channel_id"), [_lfg_item(kind, post)],
                                    source="lookup")

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


def _capture_lfg_components(channel_id, items, source="", draft=None):
    """Fire-and-forget: record components surfaced by a command into an LFG thread
    (no-op in the worker when the channel isn't a known LFG thread). `items` is a
    list of {"kind","slug","title"}. Safe to call with a falsy channel_id.

    `source` tags the originating command (random / lookup / draft). `draft`
    carries a full draft to replace the thread's current one. Both must be
    JSON-serializable -- slugs and ids only, never model instances.

    Deliberately fire-and-forget: a capture failure must never damage a draft or
    lookup that already succeeded.
    """
    if channel_id and (items or draft):
        record_lfg_components_task.delay(channel_id, items, source=source, draft=draft)


def _lfg_item(kind, post):
    return {"kind": kind, "slug": getattr(post, "slug", None),
            "title": getattr(post, "title", None)}


def _lfg_thread_for_channel(channel_id):
    """The LFGThread for this channel, or None when it isn't a game thread.
    An LFGThread's `thread_id` IS the channel id inside the thread."""
    if not channel_id:
        return None
    return LFGThread.objects.filter(thread_id=channel_id).first()


def _guild_allows(guild_id, command_name):
    """Whether `command_name` is enabled in this guild.

    Absent guild row -> False, matching /help: a guild the bot has no row for has
    nothing enabled but /help. No guild_id (a DM) -> True: there is no whitelist to
    consult, so there's no reason to withhold behaviour.

    NOTE this reads the guild's stated intent, which is what register_guild_commands
    PUTs per guild. A deployment that has also registered commands GLOBALLY (the
    register_discord_commands management command) can make a command usable in a
    guild whose enabled_commands omits it -- so a guild where the command works may
    still answer False here."""
    if not guild_id:
        return True
    enabled = (DiscordGuild.objects
               .filter(guild_id=str(guild_id))
               .values_list("enabled_commands", flat=True)
               .first())
    return command_name in (enabled or [])


def _record_url(path):
    """Absolute record-game URL, or None when SITE_URL isn't configured."""
    site = (config.get("SITE_URL") or "").rstrip("/")
    return f"{site}{path}" if site else None


def _handle_record_command(data):
    """/record: hand back a link to record this game's result, picking the form's
    mode from the channel the command was used in.

    LFG thread -> lfg_mode (?lfg=), scheduled match thread -> match_mode
    (?match=), anything else -> the plain standalone form. Resolution mirrors
    /schedule, which finds its match from the same thread signals."""
    guild_id = data.get("_guild_id")
    channel_id = data.get("_channel_id")
    channel_name = data.get("_channel_name")

    if not guild_id:
        return _ephemeral("This command only works inside a server.")

    # 1) An LFG thread is the most specific signal: its thread_id IS the channel id.
    #    A SERIES-linked thread is skipped: it's a tournament group thread that
    #    captures rolls into an LFGThread, but recording it must stay in match
    #    mode (bracket advancement, seat validation) -- so fall through to (2).
    thread = _lfg_thread_for_channel(channel_id)
    if thread and not thread.series_id:
        if thread.game_id:
            url = _record_url(f"/game/{thread.game_id}/edit/v2/")
            lead = "This game is already recorded — edit it here:"
        else:
            url = _record_url(f"/record/game/v2/?lfg={thread.id}")
            lead = "Record this game:"
        if not url:
            return _ephemeral("The site URL isn't configured, so I can't build a link.")

        lines = [lead, url, ""]
        players = list(thread.players.all())
        # Materialize once (select_related: this runs in the 3-second budget).
        seats = list(thread.seats.select_related("profile"))
        if seats:
            order = ", ".join(
                f"{s.seat_number}. "
                f"{s.profile.name if s.profile_id else '(removed player)'}"
                for s in seats)
            lines.append(f"**Seating:** {order}")
        elif players:
            lines.append(f"**Players:** {', '.join(p.name for p in players)}")
        if thread.map or thread.deck:
            bits = [str(x) for x in (thread.map, thread.deck) if x]
            lines.append(f"**Map/Deck:** {' · '.join(bits)}")
        tournament = getattr(thread.lfg_role, "tournament", None)
        if tournament:
            lines.append(f"**Series:** {tournament}")
        return _ephemeral("\n".join(lines))

    # 2) Otherwise fall back to a scheduled match for this thread, the same way
    #    /schedule resolves one. `prefer="unscheduled"` matches recording intent:
    #    the first game of a series that still needs a result.
    match, _err = _match_for_thread(channel_id, guild_id, channel_name)
    if match:
        if match.game_id:
            url = _record_url(f"/game/{match.game_id}/edit/v2/")
            lead = "This match already has a game — edit it here:"
        else:
            url = _record_url(f"/record/game/v2/?match={match.id}")
            lead = "Record this game:"
        if not url:
            return _ephemeral("The site URL isn't configured, so I can't build a link.")
        return _ephemeral(f"{lead}\n{url}\n\n**Match:** {match}\n**Round:** {match.round}")

    # 3) Neither: hand over the standalone form rather than erroring — the user
    #    can still record a game, just without any prefill.
    url = _record_url("/record/game/v2/")
    if not url:
        return _ephemeral("The site URL isn't configured, so I can't build a link.")
    return _ephemeral(
        "Record a game on the Root Database:\n{url}")


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
    _capture_lfg_components(data.get("_channel_id"), [_lfg_item("Captain", vagabond)],
                            source="lookup")

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

# Stands in for a match id in a /schedule custom_id when the time isn't linked to
# any Match (a plain channel, or a thread with no tournament match). Every schedule
# prompt encodes the match id as its first arg and re-fetches by it, so a sentinel
# keeps all four custom_id shapes intact instead of forking them.
#
# "0" specifically: `Match.objects.filter(pk="0")` is an empty queryset, whereas a
# non-numeric sentinel raises ValueError ("Field 'id' expected a number") inside the
# handler, which the dispatcher would surface as "Something went wrong".
SCHEDULE_NO_MATCH = "0"


def _is_no_match(match_id):
    return str(match_id) == SCHEDULE_NO_MATCH


# Shown on EVERY unlinked message, ephemeral and public. The whole risk this flow
# has to avoid is someone believing they scheduled a game on the site when they only
# suggested a time in a Discord thread, so this is deliberately not softened.
SCHEDULE_UNLINKED_NOTE = (
    "-# This isn't linked to a specific game.")


def _schedule_profile(discord_id, username=None, author=None):
    """The Profile for a /schedule user, created on first use so their timezone can
    always be remembered.

    Passes the username through like every other caller: ensure_profile_from_discord
    matches the verified discord_id first and only claims an UNLINKED profile by
    username, so a handle can't be used to take over someone else's account.

    `username` is absent on the component paths (a custom_id carries only the
    snowflake); that's fine — the id match is the one that matters there."""
    if not discord_id:
        return None
    return ensure_profile_from_discord(
        discord_id, username, (author or {}).get("name"))

# Title normalization now lives in services.lfg_game so this module and
# player_group_for_channel can't drift apart about which threads match a group.
# Kept as a module-level alias: the name is used throughout this file.
_normalize_title = normalize_title


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
    everywhere in the UI; MatchSeries.name is usually blank), but ONLY for groups
    with no thread URL saved — a linked group is reachable by its id alone, so a
    same-named thread can't hijack it.

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
        # Title fallback, for groups whose thread URL was never filled in. A group
        # that IS linked is excluded even when its URL points somewhere else: the id
        # lookup above is the authority for those, and matching them on title alone
        # would let a second thread with the same name schedule the wrong group's
        # match. discord_thread is blank=True without null=True, so "unlinked" means
        # empty string — an isnull check would match nothing.
        # Compare titles in Python so normalization applies to both sides (emoji
        # prefixes, collapsed whitespace) rather than relying on __iexact.
        candidates = [
            m for m in base.filter(series__player_group__isnull=False,
                                   series__player_group__discord_thread="")
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
        # Exactly one group matched by title: remember the thread, so every later
        # lookup (this command, /seating, /pick, the Celery tasks) resolves by id
        # instead of re-running the guess. Only reached on the title branch, and
        # only for groups with no thread saved.
        if matches:
            link_group_thread(matches[0].series.player_group, guild_id, channel_id)

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


# In services.lfg_game so the Celery strip task can label a closed proposal the
# same way an interaction does. Aliased so every call site here is unchanged.
_match_label = match_label


# ── /schedule roster + clicker resolution ────────────────────────────────────
# The consensus flow polls the match's ROSTER, and every Confirm/Reject click has to
# be attributed to one of those players. Both live here.

def _match_roster(match):
    """Every Profile on this match's roster, in a stable, deduped order.

    PlayerGroup.tournament_players is an M2M to TournamentPlayer, not to Profile, so
    this hops through .profile (skipping any row missing one).

    Delegates to group_roster, which /seating and /pick also use, so the consensus
    flow and the seating commands can't disagree about who is in a group. It
    prefers tournament_players and falls back to the series' MatchSeats — see
    that function for why."""
    group = match.series.player_group if match.series_id else None
    return group_roster(group, match.series_id)


# Outcomes of resolving a button-clicker against a proposal's roster.
CLICKER_MATCHED = "matched"    # verified: discord_id links them to a roster Profile
CLICKER_UNLINKED = "unlinked"  # username looks like a roster player, but unverified
CLICKER_UNKNOWN = "unknown"    # nobody on the roster corresponds to this user


def _clicker_username(payload):
    """The clicking user's raw Discord *username* — the only field comparable to
    Profile.discord. Uses .get() chains (never subscripts) because `member` is
    absent outside a guild."""
    user = (payload.get("member") or {}).get("user") or payload.get("user") or {}
    return user.get("username")


def _resolve_clicker(roster, discord_id, username):
    """Resolve a clicking Discord user against a roster: (profile, status).

      CLICKER_MATCHED  — a roster Profile's discord_id IS this snowflake. The only
                         outcome permitted to act on a proposal.
      CLICKER_UNLINKED — a roster Profile with a NULL discord_id has a matching
                         Discord username. Almost certainly this player, but a
                         username is user-controlled and so is NOT proof of
                         identity. The Profile comes back only so the caller can
                         tell them to log in; the click does not count.
      CLICKER_UNKNOWN  — nobody on the roster corresponds to this user.

    This deliberately does NOT write Profile.discord_id. That field is how every bot
    handler answers "who is this user" before running a permission check, and its
    only other writer is the verified OAuth login (signals.py), which refuses to
    reassign an id another Profile already holds. Backfilling it from a renameable
    username would let anyone bind their snowflake to a roster player's Profile —
    permanently, since the login path won't reclaim a taken id. So the username
    comparison decides only WHICH MESSAGE the clicker sees."""
    from the_warroom.services.root_league_api import sanitize_discord

    discord_id = str(discord_id) if discord_id else None
    if discord_id:
        for profile in roster:
            if profile.discord_id and str(profile.discord_id) == discord_id:
                return profile, CLICKER_MATCHED

    handle = sanitize_discord(username)
    if handle:
        for profile in roster:
            # Only ever consider UNLINKED profiles, so a handle collision can never
            # shadow a Profile that is already linked to a different account.
            if profile.discord_id:
                continue
            if sanitize_discord(profile.discord) == handle:
                return profile, CLICKER_UNLINKED
    return None, CLICKER_UNKNOWN


def _login_hint():
    """'log in with Discord' copy, with the site URL when one is configured.
    Mirrors the phrasing _handle_schedule_command uses for an unlinked invoker."""
    site = (config.get("SITE_URL") or "").rstrip("/")
    return f" at {site}/accounts/login/" if site else ""


# ── /schedule timezone prompt ────────────────────────────────────────────────
# When we don't know a user's timezone we ask for it with two selects (region,
# then city) before showing the schedule confirmation. Those are separate
# interactions, so the time the user typed has to survive between them — and it
# can't ride in a custom_id (100-char cap, and the codec is ':'-delimited, which
# a time like "20:00" would corrupt).
#
# So it rides in the prompt's own content, the way /lfg keeps its roster in embed
# fields: every step edits the SAME ephemeral message (type 7), so whatever we
# render is handed back to us on the next click. Discord's "-#" subtext renders
# small and grey, which makes the carrier double as a useful echo of what the
# user typed.
_SCHEDULE_INPUT_PREFIX = "-# From your input: "
_SCHEDULE_INPUT_RE = re.compile(r"^-# From your input: `(.+)`$", re.MULTILINE)
_SCHEDULE_INPUT_MAX = 200


def _schedule_input_line(time_text):
    """The subtext line carrying the user's raw time text to the next interaction.

    Backticks are stripped (an unescaped one would truncate the value on the way
    back) and newlines collapsed — the latter also defeats a forged marker line,
    since flattening it leaves the greedy pattern below matching the whole thing
    rather than the forgery."""
    text = (time_text or "").replace("`", "").replace("\n", " ").strip()
    return f"{_SCHEDULE_INPUT_PREFIX}`{text[:_SCHEDULE_INPUT_MAX]}`"


def _schedule_input_text(payload):
    """Recover the raw time text from the prompt this component belongs to, or ""
    when it's missing (a pre-deploy message, or one we didn't render)."""
    content = (payload.get("message") or {}).get("content") or ""
    match = _SCHEDULE_INPUT_RE.search(content)
    return match.group(1) if match else ""


def _tz_region_data(match_id, time_text, owner, current_tz=None):
    """Step 1 of the timezone prompt: pick a broad region.

    `current_tz` pre-selects the matching region (and switches the copy to the
    "changing it" wording), so the Change-timezone button lands somewhere useful.

    Takes a match ID rather than a Match: this prompt only ever needed it for the
    custom_id, so passing SCHEDULE_NO_MATCH makes the whole picker work for a time
    that isn't linked to a match."""
    if current_tz:
        intro = f"Your timezone is set to **{describe_timezone(current_tz)}**."
    else:
        intro = ("I don't know your timezone yet, so I can't tell what that time "
                 "means.")
    current_region = region_for_timezone(current_tz)
    options = [
        select_option(region["label"], region["key"], emoji={"name": region["emoji"]},
                      default=region["key"] == current_region)
        for region in timezone_regions()
    ]
    return {
        "content": "\n".join([
            intro,
            "Which part of the world are you in? I'll remember it for next time.",
            # The picker is a curated list, so point at the escape hatch here —
            # nothing else in this flow mentions it.
            "-# Not listed? Re-run with the `timezone` option instead.",
            _schedule_input_line(time_text),
        ]),
        "flags": EPHEMERAL,
        "allowed_mentions": {"parse": []},
        "components": [
            action_row(string_select(
                encode_custom_id("schedule_tz_region", match_id, owner), options,
                placeholder="Pick your region", min_values=1, max_values=1,
            )),
            action_row(button("Cancel", encode_custom_id("schedule_cancel", owner),
                              style=STYLE_SECONDARY)),
        ],
    }


def _tz_zone_data(match_id, region_key, time_text, owner, current_tz=None):
    """Step 2 of the timezone prompt: pick a city within the chosen region.

    Offsets are appended to each label, which is what makes the list readable. They
    are computed at the time the user is scheduling where we can parse it, not at
    "now" — the two differ across a DST boundary (Sydney is UTC+10 in August and
    UTC+11 in January), and it's the scheduled instant the user cares about."""
    zones = zones_for_region(region_key)
    region = next((r for r in timezone_regions() if r["key"] == region_key), None)
    options = []
    for zone, label in zones:
        # Parse against each candidate so the offset shown is the one that would
        # actually apply; an unparseable time just falls back to now.
        when, _error = parse_user_datetime(time_text, zone)
        options.append(select_option(
            f"{label} — {format_utc_offset(zone, when)}", zone,
            default=zone == current_tz,
        ))
    return {
        "content": "\n".join([
            f"Pick the closest city in **{region['label'] if region else region_key}**.",
            _schedule_input_line(time_text),
        ]),
        "flags": EPHEMERAL,
        "allowed_mentions": {"parse": []},
        "components": [
            action_row(string_select(
                encode_custom_id("schedule_tz_zone", match_id, region_key, owner), options,
                placeholder="Pick your timezone", min_values=1, max_values=1,
            )),
            action_row(
                button("◀ Regions", encode_custom_id("schedule_tz_back", match_id, owner),
                       style=STYLE_SECONDARY),
                button("Cancel", encode_custom_id("schedule_cancel", owner),
                       style=STYLE_SECONDARY),
            ),
        ],
    }


def _schedule_confirm_data(match, when, owner, tz_name=None, time_text="", note=None,
                           pending_confirmers=0, already_proposed=False,
                           unlinked_kind="bare"):
    """The ephemeral confirm prompt: the time as Discord renders it in the clicker's
    own timezone, plus Confirm / Change timezone / Cancel. The owner snowflake rides
    LAST in each custom_id so the dispatcher's owner-lock applies without extra
    checks — correct in both modes, since only the invoker may act on their own
    prompt.

    `pending_confirmers` (>0) switches the copy to the consensus flow: the button
    becomes "Propose Time" and the prompt says who still has to agree.
    `already_proposed` adds the warning that another proposal is open.

    `tz_name` is falsy for an epoch/`<t:…>` input, which is absolute: there's no
    timezone to show and re-interpreting it in another one would be a no-op, so the
    line and the button are both dropped. `note` acknowledges a just-changed
    timezone — Discord renders `<t:…>` in the VIEWER's zone, so without it the
    re-shown prompt can look unchanged even though it now means a different
    instant.

    `match` is None for a time that isn't linked to any Match. Then the copy says
    so plainly and Confirm becomes `sched_free`, because nothing will be written to
    the site — the whole point is that this can't be mistaken for a real schedule.
    `unlinked_kind` is "lfg" (the thread's players get asked to confirm) or "bare"."""
    unlinked = match is None
    match_id = SCHEDULE_NO_MATCH if unlinked else match.id
    ts = int(when.timestamp())
    lines = []
    if note:
        lines.append(note)
    if unlinked:
        # "this thread" only when there actually is one -- the bare case covers a
        # plain channel too.
        lines.append("Suggest this time for the game in this thread:" if unlinked_kind == "lfg"
                     else "Suggest this time:")
    else:
        lines.append(
            f"{'Propose' if pending_confirmers else 'Schedule'} "
            f"**{_match_label(match)}** for:")
    lines.append(format_discord_timestamp(when))
    if tz_name:
        lines.append(f"Interpreted in **{describe_timezone(tz_name, at=when)}**.")
    if not unlinked and match.scheduled_time:
        lines.append(
            f"\nThis replaces the current time of {format_discord_timestamp(match.scheduled_time)}."
        )
    if unlinked:
        lines.append(f"\n{SCHEDULE_UNLINKED_NOTE}")
        if unlinked_kind == "lfg":
            lines.append("I'll ask the other players in this thread to confirm.")
    if pending_confirmers:
        others = "the other player" if pending_confirmers == 1 else f"the other {pending_confirmers} players"
        lines.append(f"\nI'll ask {others} in this game to confirm before it's set.")
    if already_proposed:
        lines.append("\n⚠️ A time has already been proposed for this match. Proposing "
                     "another is fine — the first one everyone confirms wins.")
    lines.append("\nDoes that look right?")
    if time_text:
        lines.append(_schedule_input_line(time_text))
    # Two buttons in EVERY mode: poll the group, or put the time out directly.
    # Which the second one is depends on whether this time can actually be
    # written -- see the mode table below.
    #
    # Poll carries the match id in match mode so the open handler can re-resolve
    # the match at click time; the sentinel keeps the arg count identical
    # elsewhere, so one decode shape reads both.
    poll_kind = "match" if not unlinked else unlinked_kind
    poll_id = encode_custom_id("sched_poll_open", poll_kind,
                               match_id if not unlinked else SCHEDULE_NO_MATCH,
                               ts, owner)
    buttons = [button("Poll", poll_id, style=STYLE_SUCCESS)]

    if unlinked:
        # NOT schedule_confirm: that handler looks the match up by id and would
        # answer "That match can no longer be scheduled". This is the path a user
        # lands on after setting their timezone through the picker, so it has to be
        # decided here, in the builder both flows share.
        buttons.append(button(
            "Suggest", encode_custom_id("sched_free", unlinked_kind, ts, owner),
            style=STYLE_SECONDARY))
    elif pending_confirmers:
        # The tournament requires participant confirmation, so a direct write is
        # not on offer -- only a confirmed poll may schedule. Suggest still lets a
        # moderator float a time; it just writes nothing.
        buttons.append(button(
            "Suggest", encode_custom_id("sched_free", "match", ts, owner),
            style=STYLE_SECONDARY))
    else:
        buttons.append(button(
            "Set Time", encode_custom_id("schedule_confirm", match_id, ts, owner),
            style=STYLE_SECONDARY))
    if tz_name:
        buttons.append(button(
            "Change timezone", encode_custom_id("schedule_tz_change", match_id, owner),
            style=STYLE_SECONDARY))
    buttons.append(button("Cancel", encode_custom_id("schedule_cancel", owner),
                          style=STYLE_SECONDARY))
    return {
        "content": "\n".join(lines),
        "flags": EPHEMERAL,
        "allowed_mentions": {"parse": []},
        "components": [action_row(*buttons)],
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


# ── /schedule consensus proposals ────────────────────────────────────────────
# When the tournament opts in, a time isn't written on the invoker's say-so: it
# becomes a ScheduleProposal that every roster player must confirm.

def _consensus_required(match):
    """(required, roster) — whether this match's time needs every player's
    confirmation, plus the roster so callers don't re-query it.

    Two conditions, both required:
      * tournament.requires_schedule_confirmation() — the per-tournament opt-in flag
        AND players actually being permitted to schedule. Under MODERATORS-only
        recording_access no player may set a time, so there is nobody to poll and
        the moderator schedules directly, exactly as before this feature.
      * a non-empty roster. A player group with no tournament_players has nobody to
        ask; consensus would be vacuous at best and a deadlock at worst.

    A match with no tournament falls back to the old behavior — there's no setting
    to opt in with."""
    tournament = match.round.get_tournament() if match.round_id else None
    if not tournament or not tournament.requires_schedule_confirmation():
        return False, []
    roster = _match_roster(match)
    return bool(roster), roster


# These live in services.lfg_game so the Celery strip task can render the same
# closed-proposal embed (tasks.py cannot import this module -- see the note there
# on EPHEMERAL). Aliased to their original private names so every call site here,
# and the tests that reach for them, keep working unchanged.
_roster_name = roster_name
_FIELD_VALUE_MAX = FIELD_VALUE_MAX
_name_list_value = name_list_value


# Whether a new proposal pings the players it is waiting on.
#
# OFF deliberately. A series thread carries a proposal per game, so pinging the
# whole roster each time is more noise than it's worth; the embed names everyone
# who is waiting, and anyone who needs chasing can be @-mentioned by hand.
#
# Turning this True is enough to enable it -- but read the two hazards in
# _proposal_ping_content first.
SCHEDULE_PROPOSAL_PINGS = False

# Discord's cap on message content. Well above any real roster, but an over-long
# content makes Discord reject the POST outright, losing the whole message.
_CONTENT_MAX = 2000


def _proposal_ping_content(pending):
    """The mention line for a proposal's pending players, or None.

    Built from discord_id directly rather than _roster_name: that helper appends
    "(not linked — log in with Discord once)", which explains a stuck proposal in
    the embed but is noise in a ping. An unlinked player cannot be pinged at all,
    so they are simply omitted here -- the embed still names them.

    ⚠️ Two things to know before enabling SCHEDULE_PROPOSAL_PINGS:
      * Omitting `content` on a later EDIT does not remove it: both
        edit_channel_message and a type-7 interaction response only replace the
        keys they send, so this line persists above the embed for the life of the
        message unless an edit passes content="" explicitly. (Harmless: Discord
        pings on the initial post, never on edits.)
      * _name_list_value can't be reused: it hard-codes the 1024-char embed FIELD
        cap, not the 2000-char content cap."""
    mentions = [f"<@{p.discord_id}>" for p in pending if p.discord_id]
    if not mentions:
        return None
    line = " ".join(mentions)
    if len(line) > _CONTENT_MAX:
        # Trim whole mentions rather than slicing mid-snowflake, which would
        # render as literal text.
        out, used = [], 0
        for m in mentions:
            if used + len(m) + 1 > _CONTENT_MAX:
                break
            out.append(m)
            used += len(m) + 1
        line = " ".join(out)
    return line or None


def _proposal_entries(profiles):
    """Profiles as the poll renderer's [{"id","name"}] shape.

    A player with no linked Discord has no snowflake to key on, so they get their
    pk as a stable stand-in -- it never matches a real clicker id, which is
    correct: they cannot click until they link, and _resolve_clicker tells them
    so."""
    return [{"id": str(p.discord_id or f"profile-{p.pk}"),
             "name": p.display_name or p.discord or p.slug or "—"}
            for p in profiles]


def _schedule_proposal_data(proposal, match=None, mention=False, author=None,
                            notify_ids=()):
    """The public proposal message, rendered as a poll.

    Same visual design as an embed-mode poll -- Yes / No / Pending columns and the
    four buttons -- but every response is read from and written to the
    ScheduleProposal row, so it survives the message being deleted and can drive
    the actual schedule write.

    Every custom_id ends in the non-snowflake "g" marker so the dispatcher's
    owner-lock does NOT fire; every roster player must be able to click, and each
    handler authorizes for itself.

    `notify_ids` are carried in the EMBED even here, because a subscriber need not
    have a Profile -- so callers re-read them off the echoed message and pass them
    back in. Dropping this argument silently unsubscribes everyone on the next
    render.

    `mention` marks the FIRST post, where a ping would belong. It is currently
    INERT: pinging is off (see SCHEDULE_PROPOSAL_PINGS), and mentions inside an
    embed never notify anyone regardless -- Discord only pings from message
    `content`."""
    match = match or proposal.match
    pending = list(proposal.pending_profiles())
    data = _schedule_poll_data(
        proposal.proposed_time,
        proposal.proposed_by.discord_id if proposal.proposed_by_id else None,
        yes=_proposal_entries(proposal.confirmed_by.all()),
        no=_proposal_entries(proposal.rejected_by.all()),
        notify_ids=notify_ids,
        pending=[_roster_name(p) for p in pending],
        label=_match_label(match),
        author=author,
        proposal_pk=proposal.pk,
        kind="match",
    )
    if ScheduleProposal.objects.filter(
        match_id=proposal.match_id,
        status__in=ScheduleProposal.LIVE_STATUSES,
    ).exclude(pk=proposal.pk).exists():
        data["embeds"][0]["description"] += (
            "\n-# Another time is also proposed for this match — "
            "whichever is confirmed first wins.")
    ping = (_proposal_ping_content(pending)
            if mention and SCHEDULE_PROPOSAL_PINGS else None)
    if ping:
        data["content"] = ping
        data["allowed_mentions"] = {"parse": ["users"]}
    return data


def _schedule_rejected_data(proposal, match=None, author=None):
    """The closed view for a poll somebody couldn't make. Keeps the history --
    who suggested the time, what it was, who agreed and who declined -- so the
    thread retains a record of what fell through instead of just that something
    did.

    Names the people who declined, so the group can see why the time fell through
    rather than having to ask. Safe to render as mentions: Discord never notifies
    from inside an embed."""
    embed = schedule_closed_embed(
        proposal, "🗓 Time not scheduled", "rejected",
        label=_match_label(match) if match else None,
        author=author)
    embed["description"] += "\n-# Run `/schedule` to propose another time."
    return {
        "embeds": [embed],
        "components": [],
        "allowed_mentions": {"parse": []},
    }


def _schedule_finalized_data(proposal, match):
    """The 'game has been scheduled' view: the standard announcement embed plus the
    roster who agreed to it. build_upcoming_embed is treated as fallible here for
    the same reason the legacy confirm path does.

    summary=None drops that builder's "The next scheduled game" line: it's
    /upcoming's wording, and the match just scheduled here isn't necessarily the
    next one in the tournament. The title and the Confirmed-by field already say
    what happened."""
    try:
        embed = build_upcoming_embed(match, summary=None)
    except Exception:
        logger.exception("Failed to build /schedule announcement embed")
        embed = None
    # `is None` rather than a falsy check: the builder strips None values, and with
    # summary=None an embed can legitimately come back without a description. A
    # bare `not embed` would treat such a sparse embed as a failure and fall
    # through to the fallback, whose title would then get double-prefixed below.
    if embed is None:
        embed = {
            "title": _match_label(match),
            "description": format_discord_timestamp(proposal.proposed_time),
        }
    embed = dict(embed)
    embed["title"] = f"🗓️ {embed.get('title') or _match_label(match)} scheduled"
    # The same closing note an embed-mode poll gets, so both modes say how the
    # poll ended rather than leaving the match one to be inferred from the title.
    # Appended to whatever description build_upcoming_embed produced (which may
    # be absent entirely -- summary=None strips it).
    note = "-# Scheduled — everyone confirmed."
    existing = embed.get("description")
    embed["description"] = f"{existing}\n\n{note}" if existing else note
    fields = list(embed.get("fields") or [])
    fields.append({
        "name": "✅ Confirmed by",
        "value": _name_list_value(list(proposal.confirmed_by.all())),
        "inline": False,
    })
    embed["fields"] = fields
    return {"embeds": [embed], "components": [],
            "allowed_mentions": {"parse": []}}


def _schedule_agreed_data(proposal, match=None):
    """Everyone confirmed, but nobody who did could write the time. Shows the
    agreed time and hands a moderator a Set Time button.

    The time is deliberately NOT written yet: under MODERATORS-only
    recording_access the tournament has said players don't set times, and a
    unanimous roster shouldn't quietly override that. What it DOES establish is
    that everyone is free then, which is the part players are entitled to decide.

    Like the other proposal buttons, the custom_id ends in "g" so the dispatcher's
    owner-lock stays off -- the moderator who presses this is usually not whoever
    ran /schedule. _handle_schedule_proposal_set authorizes instead."""
    match = match or proposal.match
    return {
        "embeds": [{
            "title": "✅ Everyone agreed",
            "description": "\n".join([
                f"**{_match_label(match)}**",
                format_discord_timestamp(proposal.proposed_time),
                "",
                "-# Every player is free then. A moderator can set it on the site "
                "with the button below.",
            ]),
            "fields": [{
                "name": "✅ Confirmed",
                "value": _name_list_value(list(proposal.confirmed_by.all())),
                "inline": False,
            }],
        }],
        "components": [action_row(
            button("Set Time", encode_custom_id("sched_prop_set", proposal.pk, "g"),
                   style=STYLE_SUCCESS),
            button("Reject", encode_custom_id("sched_prop_no", proposal.pk, "g"),
                   style=STYLE_DANGER),
        )],
        "allowed_mentions": {"parse": []},
    }


def _schedule_closed_data(title, description=None, proposal=None, reason=None,
                          actor=None):
    """A retired proposal: explain, drop the buttons.

    With a `proposal`, keeps its history -- proposer, time, who had agreed -- so
    the record isn't lost the moment it closes, and `reason` (plus optional
    `actor`) supplies the closing line. Passing the reason as a KEY rather than
    prose is what lets an expired proposal name nobody: expiry is a clock, not
    someone's decision.

    Without one, falls back to the bare title+description it always rendered --
    the finalize-failure paths describe a match-level problem rather than the
    proposal's own history, and carry their own specific wording."""
    if proposal is None:
        embed = {"title": title, "description": description or ""}
    else:
        embed = schedule_closed_embed(
            proposal, title, reason or "cancelled", actor=actor)
    return {
        "embeds": [embed],
        "components": [],
        "allowed_mentions": {"parse": []},
    }


def _schedule_retire_response(proposal, reason, actor=None):
    """Retire a proposal a button can no longer act on, and clear its buttons.

    `reason` is a key (see PROPOSAL_RETIRED_TEXT), not prose, so the rendered
    message keeps the proposal's history and names an actor only when a person
    actually decided it -- an expired proposal was closed by the clock and must
    not read as somebody's doing.

    Refusing alone left the row LIVE and the public message fully buttoned, with
    nothing able to dismiss it until cleanup_stale_schedule_proposals happened to
    run -- and that is beat-scheduled from the admin (DatabaseScheduler, no
    beat_schedule in code), so nothing guarantees when. The click itself is the
    reliable moment to retire it.

    CANCELLED matches how every other non-rejection retirement is recorded (see
    _cancel_open_proposals and the cleanup task), so nothing downstream has to
    learn a new state.

    LIVE-guarded: a concurrent finalize may have just confirmed this row, and that
    result must win -- the update is a no-op then, and the message this renders is
    replaced by whatever the winner wrote."""
    ScheduleProposal.objects.filter(
        pk=proposal.pk, status__in=ScheduleProposal.LIVE_STATUSES,
    ).update(status=ScheduleProposal.Status.CANCELLED,
             resolved_at=timezone.now())
    return JsonResponse({
        "type": RESPONSE_UPDATE_MESSAGE,
        "data": _schedule_closed_data(
            "Proposal closed", proposal=proposal, reason=reason, actor=actor),
    })


def _cancel_open_proposals(match, reason, exclude_pk=None):
    """Retire every OPEN proposal for this match and strip its buttons.

    Called from EVERY path that writes or clears Match.scheduled_time, not just
    finalize: a stale proposal is a live button that can overwrite a time someone
    else just set. Returns the ids retired."""
    qs = ScheduleProposal.objects.filter(
        match_id=match.pk, status__in=ScheduleProposal.LIVE_STATUSES)
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    ids = list(qs.values_list("pk", flat=True))
    if not ids:
        return []
    status = (ScheduleProposal.Status.SUPERSEDED if reason == "superseded"
              else ScheduleProposal.Status.CANCELLED)
    ScheduleProposal.objects.filter(pk__in=ids).update(
        status=status, resolved_at=timezone.now())
    # on_commit: the task must never observe a status this transaction rolls back.
    # Each edit is a blocking HTTP call, so it belongs off the request path.
    transaction.on_commit(
        lambda: strip_schedule_proposal_messages_task.delay(ids, reason))
    return ids


def _announce_schedule_to_channel(match, old_time, new_time):
    """Announce a newly written match time in the tournament's schedule_channel.

    Call with the time read BEFORE the write: the verb depends on it, and an unchanged
    time is not announced at all (re-confirming the same slot isn't news).

    Safe to call from inside an atomic block -- it defers the post itself, and
    post_to_tournament_channel refuses any channel it can't confirm belongs to the
    tournament's current guild.
    """
    if new_time is None or old_time == new_time:
        return
    tournament = match.round.get_tournament() if match.round_id else None
    if tournament is None:
        return
    verb = "rescheduled" if old_time is not None else "scheduled"
    content = (f"{_match_label(match)} is {verb} for "
               f"{format_discord_timestamp(new_time)}")
    from the_warroom.services.channel_posts import post_to_tournament_channel
    # on_commit: callers run inside transaction.atomic(), and the worker must never
    # announce a time this transaction goes on to roll back.
    transaction.on_commit(
        lambda: post_to_tournament_channel(tournament, 'schedule_channel', content))


def _finalize_proposal(proposal, actor=None):
    """Write the agreed time and retire every other proposal for this match.
    Returns (ok, error).

    `actor` is whoever is exercising the authority to schedule. Default (None)
    means the PROPOSER, which is the confirm path: the roster consented, and the
    right to write comes from whoever proposed it. The Set Time button passes the
    clicking MODERATOR instead -- there the proposer is a player who deliberately
    cannot schedule, so checking them would refuse every time.

    The ordering is load-bearing, and `status` is written EXACTLY ONCE:

      1. Authority first, while the row is still live. Confirmations express
         CONSENT; the authority to schedule comes from `actor`, whose permission
         can be revoked while a proposal sits open. A refusal has to be able to
         land CANCELLED, which is impossible once the row reads CONFIRMED.
      2. A compare-and-swap live -> CONFIRMED claims the exclusive right to write.
         This is the ONLY writer of CONFIRMED. It's a single conditional UPDATE
         rather than a read-then-write because select_for_update is a no-op on
         SQLite, so a lock-only guard would be untested in the suite.
      3. Only then the scheduled_time write and the supersede sweep."""
    with transaction.atomic():
        # Re-fetch under lock rather than trusting the (possibly minutes-stale)
        # object carried in from the interaction. Plain Match.objects: applying
        # select_for_update to _schedulable_matches would join across nullable FKs,
        # which Postgres rejects for FOR UPDATE.
        match = Match.objects.select_for_update().filter(pk=proposal.match_id).first()
        if not match:
            return False, "that match no longer exists"

        authority = actor if actor is not None else proposal.proposed_by
        if authority is None:
            ScheduleProposal.objects.filter(
                pk=proposal.pk, status__in=ScheduleProposal.LIVE_STATUSES,
            ).update(status=ScheduleProposal.Status.CANCELLED, resolved_at=timezone.now())
            return False, "the player who proposed this time no longer has an account"
        if not match.can_schedule(authority):
            ScheduleProposal.objects.filter(
                pk=proposal.pk, status__in=ScheduleProposal.LIVE_STATUSES,
            ).update(status=ScheduleProposal.Status.CANCELLED, resolved_at=timezone.now())
            return False, ("you no longer have permission to schedule it" if actor
                           else "whoever proposed it no longer has permission to "
                                "schedule it")

        won = ScheduleProposal.objects.filter(
            pk=proposal.pk, status__in=ScheduleProposal.LIVE_STATUSES,
        ).update(status=ScheduleProposal.Status.CONFIRMED, resolved_at=timezone.now())
        if not won:
            return False, "another time was confirmed for this match first"

        # Read before the write: the announcement's verb (scheduled vs rescheduled)
        # depends on whether this match already had a time.
        previous_time = match.scheduled_time
        match.scheduled_time = proposal.proposed_time
        # update_fields is required: a bare save() re-runs Match.save()'s name and
        # match_number derivation.
        match.save(update_fields=["scheduled_time"])
        _announce_schedule_to_channel(match, previous_time, proposal.proposed_time)

        # exclude_pk is REQUIRED, not incidental: don't rely on the CAS above having
        # already moved this row out of OPEN. If these are ever reordered, an
        # unguarded sweep would supersede the winner and strip its own buttons.
        _cancel_open_proposals(match, "superseded", exclude_pk=proposal.pk)

    proposal.refresh_from_db()
    return True, None


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
        return _ephemeral("User not found, try again.")

    # Get-or-create rather than a strict lookup: every path here wants to remember
    # the user's timezone, including someone suggesting a time in a thread that
    # isn't linked to anything. A brand-new Profile simply fails can_schedule below,
    # which is the honest answer anyway.
    profile = _schedule_profile(author_id, data.get("_author_username"),
                                data.get("_author"))
    if not profile:
        return _ephemeral("User not found, try again.")

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
        # No match: instead of the old dead end, suggest a time that is explicitly
        # NOT linked to anything on the site.
        return _handle_schedule_unlinked(data, profile, time_text, clearing)

    # Checked before the clear branch so an unauthorized user gets the permission
    # error rather than a prompt they can't act on.
    permission = match.can_schedule(profile)
    if not permission:
        return _ephemeral(
            "You're not able to schedule this game. If you think you should be, "
            "contact the series admin."
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
    # It's rarely needed now that we ask, but it's the only route to a zone the
    # region/city picker doesn't curate.
    tz_option = (_get_option(data, "timezone") or "").strip()
    if tz_option and not valid_timezone(tz_option):
        return _ephemeral(
            f'"{tz_option}" isn\'t a timezone I recognize. Pick one from the '
            "suggestions, e.g. `America/New_York` — or leave it blank and I'll ask."
        )
    tz_name = tz_option or profile.timezone or None

    when, error = parse_user_datetime(time_text, tz_name)
    if error == NEED_TIMEZONE:
        # Ask, rather than erroring out and making them re-type the command. An
        # epoch/`<t:…>` paste never lands here — it parses with no zone at all.
        return JsonResponse({
            "type": RESPONSE_CHANNEL_MESSAGE,
            "data": _tz_region_data(match.id, time_text, author_id, current_tz=tz_name),
        })
    if error:
        return _ephemeral(error)

    # Persisted only once the whole command has succeeded, so a good timezone
    # paired with an unreadable time doesn't get saved on the way out.
    if tz_option and profile.timezone != tz_option:
        profile.timezone = tz_option
        profile.save(update_fields=["timezone"])

    # The preview is ephemeral in BOTH modes — it's what catches a misparse, and
    # it's where the Change-timezone flow lands. Only the copy and what Confirm
    # does differ.
    consensus, roster = _consensus_required(match)
    pending = 0
    already_proposed = False
    if consensus:
        # The proposer doesn't confirm their own time, so they don't count toward
        # the "others must agree" tally.
        pending = sum(1 for p in roster if str(p.discord_id or "") != str(author_id))
        already_proposed = ScheduleProposal.objects.filter(
            match=match,
            status__in=ScheduleProposal.LIVE_STATUSES).exists()

    return JsonResponse({
        "type": RESPONSE_CHANNEL_MESSAGE,
        "data": _schedule_confirm_data(match, when, author_id, tz_name, time_text,
                                       pending_confirmers=pending,
                                       already_proposed=already_proposed),
    })


def _handle_schedule_unlinked(data, profile, time_text, clearing):
    """/schedule where no tournament match could be resolved — a plain channel, or a
    thread that isn't linked to one.

    Suggests a time that is NOT written anywhere: the reply says so, and confirming
    posts a public message that says so too. In an LFG thread the thread's players
    are also asked to confirm, but that confirmation lives in the message alone --
    LFGThread has no time field and ScheduleProposal requires a Match."""
    channel_id = data.get("_channel_id")
    author_id = data.get("_author_id")

    if clearing:
        # There is no stored time to remove, so "clear" has nothing to act on. Say
        # what to do instead rather than reporting a missing match.
        return _ephemeral(
            "This thread isn't linked to a match, so there's no scheduled time to "
            "clear. Give me a `time` and I'll suggest one for this thread instead."
        )

    thread = _lfg_thread_for_channel(channel_id)
    kind = "lfg" if thread else "bare"

    tz_option = (_get_option(data, "timezone") or "").strip()
    if tz_option and not valid_timezone(tz_option):
        return _ephemeral(
            f'"{tz_option}" isn\'t a timezone I recognize. Pick one from the '
            "suggestions, e.g. `America/New_York` — or leave it blank and I'll ask."
        )
    tz_name = tz_option or profile.timezone or None

    when, error = parse_user_datetime(time_text, tz_name)
    if error == NEED_TIMEZONE:
        # The picker carries the sentinel, so the whole region/city flow works here
        # and the timezone it saves is reused everywhere afterwards.
        return JsonResponse({
            "type": RESPONSE_CHANNEL_MESSAGE,
            "data": _tz_region_data(SCHEDULE_NO_MATCH, time_text, author_id,
                                    current_tz=tz_name),
        })
    if error:
        return _ephemeral(error)

    if tz_option and profile.timezone != tz_option:
        profile.timezone = tz_option
        profile.save(update_fields=["timezone"])

    return JsonResponse({
        "type": RESPONSE_CHANNEL_MESSAGE,
        "data": _schedule_confirm_data(None, when, author_id, tz_name, time_text,
                                       unlinked_kind=kind),
    })


def _handle_schedule_confirm(payload):
    """Confirm button. Either writes the scheduled time outright (the original
    behavior, kept for tournaments that haven't opted in) or — when the tournament
    requires player confirmation — opens a ScheduleProposal for the roster to
    confirm, writing nothing yet."""
    _action, args = decode_custom_id(payload["data"]["custom_id"])  # [match_id, ts, owner]
    if len(args) < 3:
        return _ephemeral("That button is out of date — run /schedule again.")
    match_id, ts, owner = args[0], args[1], args[2]

    match = _schedulable_matches(payload.get("guild_id")).filter(pk=match_id).first()
    if not match:
        return _ephemeral(
            "That match can no longer be scheduled: it may have been played or removed."
        )

    # The button is a second request, so re-check permission rather than trusting
    # the check made when the prompt was built.
    profile = Profile.objects.filter(discord_id=str(owner)).first()
    if not profile or not match.can_schedule(profile):
        return _ephemeral("You can't set the time for this match.")

    try:
        when = datetime.fromtimestamp(int(ts), tz=dt_timezone.utc)
    except (ValueError, OSError, OverflowError):
        return _ephemeral("That time is no longer valid: run /schedule again.")

    # Re-read the gate rather than trusting a value baked into the custom_id, the
    # same way the match and permission are re-checked above: a moderator may have
    # flipped the tournament setting since the prompt was built.
    consensus, roster = _consensus_required(match)
    if consensus:
        return _open_schedule_proposal(payload, match, when, profile, roster)

    # Read before the write, for the announcement's scheduled/rescheduled verb.
    previous_time = match.scheduled_time
    match.scheduled_time = when
    # update_fields is required: a bare save() re-runs Match.save()'s name and
    # match_number derivation.
    match.save(update_fields=["scheduled_time"])
    # Additional to the thread embed below: that tells the players in the thread, this
    # tells the tournament's schedule channel.
    _announce_schedule_to_channel(match, previous_time, when)

    # A direct write supersedes anything still awaiting confirmation — otherwise a
    # stale Confirm could overwrite the time just set here.
    _cancel_open_proposals(match, "cancelled")

    # Announce publicly in the thread so the whole group sees it. The followup is
    # sequenced after this response's ACK (a followup before it 404s).
    token = payload.get("token")
    if token:
        try:
            # Not /upcoming's "The next scheduled game" line — this announces the
            # match just written, which needn't be the tournament's next one. No
            # roster confirmed it on this path, so name who set the time. A plain
            # display name, not _roster_name: that renders a mention, and this
            # followup sets no allowed_mentions, so it would ping the very person
            # who just clicked Confirm.
            who = profile.display_name or profile.discord or profile.slug
            embed = build_upcoming_embed(
                match, summary=f"Scheduled by {who}" if who else None)
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


def _open_schedule_proposal(payload, match, when, profile, roster, author=None):
    """Create a ScheduleProposal and post it publicly for the roster to confirm.

    The proposer is seeded into confirmed_by — they picked the time, so asking them
    again is noise. But only when they're actually ON the roster: a group moderator
    or organizer scheduling for a game they don't play in confirms nobody, and every
    player still has to agree."""
    proposal = ScheduleProposal.objects.create(
        match=match,
        proposed_time=when,
        proposed_by=profile,
        channel_id=str(payload.get("channel_id") or ""),
        guild_id=str(payload.get("guild_id") or ""),
    )
    proposal.roster.set(roster)
    if any(p.pk == profile.pk for p in roster):
        proposal.confirmed_by.add(profile)

    others = ScheduleProposal.objects.filter(
        match=match, status__in=ScheduleProposal.LIVE_STATUSES,
    ).exclude(pk=proposal.pk).exists()

    # countdown=2 sequences the post after this response's ACK, matching the
    # followup convention elsewhere in this module.
    post_schedule_proposal_task.apply_async(
        (proposal.pk, _schedule_proposal_data(proposal, match, mention=True,
                                              author=author)),
        countdown=2,
    )

    content = (f"✔ Proposed {format_discord_timestamp(when)}. "
               "The other players will need to confirm.")
    if others:
        content += ("\n-# Another time is also awaiting confirmation - whichever is "
                    "confirmed first wins.")
    return JsonResponse({
        "type": RESPONSE_UPDATE_MESSAGE,
        "data": {"content": content, "components": []},
    })


def _proposal_for_click(payload, allow_agreed=False, allow_passed=False):
    """Shared guards for the proposal buttons: (proposal, match, error).

    These custom_ids end in the non-snowflake "g" marker precisely so the
    dispatcher's owner-lock does NOT fire — any roster player must be able to click
    — so every check the lock would have made has to happen here instead.

    `allow_agreed` admits a proposal whose roster has already fully confirmed but
    whose time nobody present could write. Only Set Time passes it: Confirm and
    Reject act on consent that is already complete there, so for them an AGREED
    row is correctly "no longer active".

    `allow_passed` admits a proposal whose time has already gone by. Only REJECT
    passes it: the passed-time check exists to keep a past time out of
    Match.scheduled_time, and rejecting writes no time at all -- so for Reject the
    check was not merely unnecessary, it was the thing stopping the one button
    whose job is clearing the message.

    The two branches that refuse a LIVE proposal below RETIRE it rather than only
    saying no. A bare refusal left the row live and the message fully buttoned,
    with nothing able to dismiss it until the cleanup sweep happened to run."""
    _action, args = decode_custom_id(payload["data"]["custom_id"])  # [proposal_id, "g"]
    if not args:
        return None, None, _ephemeral("That button is out of date — run /schedule again.")

    proposal = ScheduleProposal.objects.filter(pk=args[0]).first()
    if not proposal:
        return None, None, _ephemeral(
            "That proposal is no longer available — run /schedule again.")
    acceptable = proposal.is_live if allow_agreed else proposal.is_open
    if not acceptable:
        return None, None, _ephemeral({
            ScheduleProposal.Status.AGREED:
                "Everyone has already agreed to this time — a moderator just needs "
                "to press Set Time.",
            ScheduleProposal.Status.CONFIRMED: "That time has already been confirmed.",
            ScheduleProposal.Status.REJECTED: "That proposed time was rejected.",
            ScheduleProposal.Status.SUPERSEDED:
                "A different time was confirmed for this match.",
        }.get(proposal.status, "That proposed time is no longer active."))

    # Guild scope + still-schedulable, exactly as the other schedule buttons do.
    match = _schedulable_matches(payload.get("guild_id")).filter(
        pk=proposal.match_id).first()
    if not match:
        # This filter fails for two very different reasons, and only one of them
        # may retire the row. A match that was played or removed is a dead proposal
        # -- and it is the COMMONEST way one goes stale, so refusing without
        # retiring is what leaves a buttoned message nobody can dismiss.
        #
        # But the same filter is also the GUILD SCOPE check, and a cross-guild
        # click must never be able to cancel a proposal it isn't allowed to even
        # see. So retire only what belongs to this guild, and refuse the rest
        # exactly as before.
        if proposal.guild_id and str(proposal.guild_id) == str(
                payload.get("guild_id") or ""):
            return None, None, _schedule_retire_response(
                proposal, "unschedulable")
        return None, None, _ephemeral(
            "That match can no longer be scheduled: it may have been played or removed.")

    # No actor: the time simply arrived. Naming whoever happened to click would
    # attribute a decision nobody made.
    if not allow_passed and proposal.proposed_time <= timezone.now():
        return None, None, _schedule_retire_response(proposal, "expired")
    return proposal, match, None


def _already_confirmed_text(proposal, me):
    """Why a repeat Confirm click changed nothing.

    The proposer is seeded into confirmed_by at creation and never pressed
    anything, so a bare "you already confirmed" reads to them as a bug -- and on a
    roster of three or more theirs is the commonest click to land here. Naming
    who's still pending answers the question actually behind the second click: not
    "did mine register" but "why hasn't this resolved".

    Plain display names, NOT _roster_name: that renders a mention, and this text is
    message content rather than an embed field, so every repeat click would ping
    players who did nothing. Same reasoning as the followup in _finalize_proposal.
    """
    if proposal.proposed_by_id == me.pk:
        lead = "You proposed this time, so you're already counted as confirmed."
    else:
        lead = "You've already confirmed this time."
    pending = list(proposal.pending_profiles())
    if pending:
        names = ", ".join(
            p.display_name or p.discord or p.slug or "—" for p in pending)
        return f"{lead}\nStill waiting on: {names}"
    return lead


def _handle_schedule_proposal_confirm(payload):
    """Confirm on a public proposal. Only a roster player whose Discord is linked
    may act; everyone else is told why they can't."""
    proposal, match, error = _proposal_for_click(payload)
    if error:
        return error

    roster = list(proposal.roster.all())
    me, status = _resolve_clicker(
        roster, _interaction_user_id(payload), _clicker_username(payload))
    if status == CLICKER_UNLINKED:
        return _ephemeral(
            "You're on this game's roster, but your Discord isn't linked to your "
            f"site account yet. Log in{_login_hint()} with Discord once, then click "
            "Confirm again."
        )
    if status != CLICKER_MATCHED:
        return _ephemeral(
            "You cannot confirm or reject this schedule — you're not one of this "
            "game's players."
        )

    # Captured BEFORE the write: add() is idempotent, so afterwards there is no
    # way to tell a repeat click from a first one.
    already = proposal.confirmed_by.filter(pk=me.pk).exists()
    proposal.confirmed_by.add(me)

    # Silence here reads as a broken button, so say the consent already landed --
    # but only when the roster is still incomplete. A repeat click can also be the
    # click that FINISHES: once everyone else has confirmed, the seeded proposer
    # pressing Confirm is both already-confirmed and the last confirmation owed,
    # and returning early on `already` alone would strand the proposal unscheduled.
    if already and not proposal.all_responded():
        return _ephemeral(_already_confirmed_text(proposal, me))

    proposal.rejected_by.remove(me)   # answering moves you between the columns
    return _resolve_match_poll(payload, proposal, match)


def _resolve_match_poll(payload, proposal, match):
    """Re-render a match poll after a vote, closing it if that was the last one.

    The single place the poll's outcome is decided, shared by Yes and No so the
    two can't drift. Three outcomes:

      * still waiting      -> re-render with the updated columns
      * everyone said yes  -> write the time (or park at AGREED, see below)
      * somebody said no   -> close REJECTED, writing nothing

    all_responded is the CLOSE condition and all_confirmed the WRITE condition;
    they differ exactly when someone declined, which is the whole point of a poll
    that no longer dies on the first rejection."""
    notify_ids = _poll_notify_ids_from_payload(payload)

    if not proposal.all_responded():
        return JsonResponse({
            "type": RESPONSE_UPDATE_MESSAGE,
            "data": _schedule_proposal_data(
                proposal, match, author=_poll_author_from_payload(payload),
                notify_ids=notify_ids),
        })

    declined = list(proposal.rejected_by.all())
    if declined:
        # Everyone answered and somebody can't make it: no time is written, and
        # the message says who. LIVE-guarded so a proposal resolved another way
        # in the meantime is not overwritten.
        ScheduleProposal.objects.filter(
            pk=proposal.pk, status__in=ScheduleProposal.LIVE_STATUSES,
        ).update(status=ScheduleProposal.Status.REJECTED,
                 resolved_at=timezone.now())
        proposal.refresh_from_db()
        if notify_ids:
            # Excluding whoever's vote completed the roster -- they just clicked.
            _notify_poll_closed(notify_ids, proposal.proposed_time,
                                _proposal_entries(declined), scheduled=False,
                                closed_by=str(_interaction_user_id(payload)),
                                jump_url=_lfg_jump_url(payload))
        return JsonResponse({
            "type": RESPONSE_UPDATE_MESSAGE,
            "data": _schedule_rejected_data(
                proposal, match, author=_poll_author_from_payload(payload)),
        })

    # Everyone agreed -- but agreement is CONSENT, not authority. When the
    # proposer may not write the time (the normal case under MODERATORS-only
    # recording_access, where players can still say when they're free), park the
    # proposal as AGREED and hand a moderator the Set Time button instead of
    # cancelling it for a permission the roster was never expected to have.
    if not match.can_schedule(proposal.proposed_by):
        claimed = ScheduleProposal.objects.filter(
            pk=proposal.pk, status=ScheduleProposal.Status.OPEN,
        ).update(status=ScheduleProposal.Status.AGREED)
        proposal.refresh_from_db()
        if not claimed and proposal.status != ScheduleProposal.Status.AGREED:
            # Something else resolved it between the confirm and here.
            return JsonResponse({
                "type": RESPONSE_UPDATE_MESSAGE,
                "data": _schedule_closed_data(
                    "Proposal closed", "That proposed time is no longer active."),
            })
        return JsonResponse({
            "type": RESPONSE_UPDATE_MESSAGE,
            "data": _schedule_agreed_data(proposal, match),
        })

    ok, failure = _finalize_proposal(proposal)
    if not ok:
        return JsonResponse({
            "type": RESPONSE_UPDATE_MESSAGE,
            "data": _schedule_closed_data(
                "Proposal closed",
                f"The time can no longer be set for this match — {failure}."),
        })
    match.refresh_from_db()
    if notify_ids:
        # Excluding whoever's confirmation completed the roster -- they just clicked.
        _notify_poll_closed(notify_ids, proposal.proposed_time, [],
                            scheduled=True,
                            closed_by=str(_interaction_user_id(payload)),
                            jump_url=_lfg_jump_url(payload))
    return JsonResponse({
        "type": RESPONSE_UPDATE_MESSAGE,
        "data": _schedule_finalized_data(proposal, match),
    })


def _poll_notify_ids_from_payload(payload):
    """The 🔔 subscribers, read back off the echoed message.

    Match-mode polls keep this list in the EMBED, not the row -- a subscriber
    need not have a Profile, so an M2M could not hold them. Every match-mode
    re-render rebuilds its embed from the row, so without carrying this across
    the subscribers would vanish the moment anyone voted."""
    embed = (payload.get("message", {}).get("embeds") or [{}])[0]
    field = _poll_field_lookup(embed, POLL_NOTIFY_FIELD)
    return _LFG_MENTION_RE.findall(field.get("value", "")) if field else []


def _poll_author_from_payload(payload):
    """The embed author block carried on the message being edited.

    Not rebuilt from the clicker: the author is whoever PROPOSED the time, and
    every re-render happens under someone else's interaction."""
    embed = (payload.get("message", {}).get("embeds") or [{}])[0]
    return embed.get("author")


def _handle_schedule_proposal_reject(payload):
    """Reject on a public proposal: retire it and drop the buttons.

    Accepts a WIDER set than Confirm. A roster player may reject — including one who
    already confirmed, since plans change and their earlier consent shouldn't trap
    the group. So may anyone who passes can_schedule (a group moderator, organizer
    or admin), who is often not on the roster at all: that's what lets a moderator
    clear a proposal stuck behind an unresponsive player.

    allow_agreed: this button also rides the "Everyone agreed" message, so a
    moderator can throw out a time the roster settled on rather than being forced
    to set it.

    But an AGREED proposal NARROWS to can_schedule only. Rejecting a time still
    being negotiated is ordinary; destroying a completed agreement on one late
    click is not, and it would undo work every other player already did. A player
    who can no longer make it says so in the thread and a moderator clears it --
    the same person the Set Time button is waiting on either way.

    allow_passed: rejecting writes no time, so the passed-time guard has nothing to
    protect here -- and it was the reason a proposal whose time had gone by could
    not be cleared by anyone at all."""
    proposal, match, error = _proposal_for_click(
        payload, allow_agreed=True, allow_passed=True)
    if error:
        return error

    roster = list(proposal.roster.all())
    me, status = _resolve_clicker(
        roster, _interaction_user_id(payload), _clicker_username(payload))

    if proposal.status == ScheduleProposal.Status.AGREED:
        clicker = Profile.objects.filter(
            discord_id=str(_interaction_user_id(payload) or "")).first()
        if not clicker or not match.can_schedule(clicker):
            return _ephemeral(
                "Everyone already agreed to this time. Ask a moderator to reject "
                "it if it no longer works.")
        me = clicker
    elif status != CLICKER_MATCHED:
        # Not a linked roster player — but a moderator/organizer may still reject.
        clicker = Profile.objects.filter(
            discord_id=str(_interaction_user_id(payload) or "")).first()
        if clicker and match.can_schedule(clicker):
            me = clicker
        elif status == CLICKER_UNLINKED:
            return _ephemeral(
                "You're on this game's roster, but your Discord isn't linked to your "
                f"site account yet. Log in{_login_hint()} with Discord once, then "
                "click Reject again."
            )
        else:
            return _ephemeral(
                "You cannot confirm or reject this schedule — you're not one of this "
                "game's players."
            )

    # A "No" is a VOTE, not a termination: record it and leave the poll open so
    # everyone else still gets a say. The poll closes when the last roster player
    # answers -- see _resolve_match_poll.
    proposal.rejected_by.add(me)
    proposal.confirmed_by.remove(me)   # answering moves you between the columns
    return _resolve_match_poll(payload, proposal, match)


def _handle_schedule_proposal_set(payload):
    """Set Time on an AGREED proposal: write the time the roster settled on.

    Only for someone who may actually schedule this match — the same can_schedule
    check /schedule itself makes, so group moderators, organizers and admins
    qualify and a player does not. This is the authority half of the split: the
    roster supplied consent, this supplies the right to write it.

    The clicker, not the proposer, is passed to _finalize_proposal as the actor:
    the proposer is typically a player who deliberately cannot schedule, so
    checking them would refuse every time."""
    proposal, match, error = _proposal_for_click(payload, allow_agreed=True)
    if error:
        return error

    clicker = Profile.objects.filter(
        discord_id=str(_interaction_user_id(payload) or "")).first()
    if not clicker or not match.can_schedule(clicker):
        return _ephemeral(
            "Only a moderator or organizer can set this time. Everyone has agreed "
            "to it — ask one of them to press Set Time.")

    ok, failure = _finalize_proposal(proposal, actor=clicker)
    if not ok:
        return JsonResponse({
            "type": RESPONSE_UPDATE_MESSAGE,
            "data": _schedule_closed_data(
                "Proposal closed",
                f"The time can no longer be set for this match — {failure}."),
        })
    match.refresh_from_db()
    return JsonResponse({
        "type": RESPONSE_UPDATE_MESSAGE,
        "data": _schedule_finalized_data(proposal, match),
    })


def _handle_schedule_clear_confirm(payload):
    """Clear button: remove the match's scheduled time, then say so in the thread.
    Mirrors _handle_schedule_confirm's guards — the button is a second request, so
    the match is re-fetched and permission re-checked."""
    _action, args = decode_custom_id(payload["data"]["custom_id"])  # [match_id, owner]
    if len(args) < 2:
        return _ephemeral("That button is out of date: run /schedule again.")
    match_id, owner = args[0], args[1]

    match = _schedulable_matches(payload.get("guild_id")).filter(pk=match_id).first()
    if not match:
        return _ephemeral(
            "That match can no longer be changed: it may have been played or removed."
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

    # Retire anything still awaiting confirmation: a stale Confirm would otherwise
    # re-write the very time that was just cleared.
    _cancel_open_proposals(match, "cancelled")

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


# The response field names. These are PARSED back out of the embed on every click
# in embed mode, so their values are a wire format: a poll posted before a rename
# stops being readable. Defined in services.lfg_game so the Celery strip task
# renders the same labels; re-exported here under the names the call sites use.
SCHEDULE_FREE_CONFIRMED_FIELD = POLL_YES_FIELD
SCHEDULE_FREE_UNAVAILABLE_FIELD = POLL_NO_FIELD

# How many Yes / No responses a poll with no roster accepts. Not a correctness
# guard -- name_list_value's 1024-char truncation is that -- but a poll where
# ninety people click Yes stops being readable long before Discord complains.
POLL_FREE_RESPONSE_MAX = 12


def _schedule_free_public_data(when, proposer_id, kind, author=None):
    """The PUBLIC suggested-time message: who suggested it, and when.

    Deliberately minimal -- this is the "just put a time out there" half of the
    picker, so it carries no lists and no buttons. Anything that collects
    responses is a poll (see _schedule_poll_data).

    Deliberately unlike a real schedule too: a different title and the unlinked
    note, so nobody reads this as a game scheduled on the site."""
    embed = {
        "title": "🕐 Suggested time",
        "description": "\n".join([
            format_discord_timestamp(when),
            "",
            f"Suggested by <@{proposer_id}>.",
            SCHEDULE_UNLINKED_NOTE,
        ]),
    }
    if author:
        embed["author"] = author
    return {"embeds": [embed], "allowed_mentions": {"parse": []}}


def _poll_entry_lines(entries):
    """"Name (<@id>)" per entry, as the embed field value. Mirrors /lfg's player
    lines so _LFG_PLAYER_LINE_RE parses them straight back."""
    return "\n".join(_lfg_player_line(e["name"], e["id"]) for e in entries) or "—"


def _schedule_poll_data(when, proposer_id, *, yes, no, notify_ids=(),
                        pending=None, label=None, closed=False, closed_reason=None,
                        closed_by=None, scheduled=False, author=None,
                        proposal_pk=None, kind="bare"):
    """The poll message, in every mode.

    ONE renderer for all four situations, fed a normalized shape so the two
    backends converge: match mode passes values read from the ScheduleProposal
    row, embed mode passes values parsed out of the echoed embed. This function
    knows about neither store.

    `yes` / `no` are [{"id", "name"}]. `pending` is a list of names for a poll
    with a roster, or None for one without -- and None is NOT the same as empty:
    empty means everyone answered (the poll closes), None means nobody knows who
    should answer (a bare channel, where the column is absent entirely).

    `notify_ids` are raw snowflakes. They live in the embed even in match mode,
    because a subscriber need not have a Profile at all.

    Mentions render but never notify: Discord only pings from message `content`,
    which this never sets."""
    lines = []
    if label:
        lines.append(f"**{label}**")
    lines.append(format_discord_timestamp(when))
    lines.append(f"Suggested by <@{proposer_id}>.")
    if kind != "match":
        lines.append(SCHEDULE_UNLINKED_NOTE)

    if closed:
        if scheduled:
            title = "✅ Time confirmed"
        elif closed_reason == "closed":
            title = "🗓 Poll closed"
        else:
            title = "🗓 Time not scheduled"
    else:
        title = "🗓 Proposed time"

    embed = {"title": title, "description": "\n".join(lines)}
    if author:
        embed["author"] = author

    # Closed polls stack rather than column: the alignment columns protect only
    # matters while votes are arriving, and empty lists are dropped instead of
    # holding their slot with a "—".
    if closed:
        fields = poll_response_fields(
            _poll_entry_lines(yes) if yes else None,
            _poll_entry_lines(no) if no else None,
            None, columns=False)
    else:
        fields = poll_response_fields(
            _poll_entry_lines(yes), _poll_entry_lines(no),
            # None (no roster) drops the column; a roster with nobody left simply
            # renders empty until the close lands.
            None if pending is None else ("\n".join(pending) or "—"))
    for field in fields:
        field["name"] = poll_count_label(
            field["name"],
            {POLL_YES_FIELD: yes, POLL_NO_FIELD: no}.get(field["name"],
                                                         pending or []))

    # Written HERE rather than via _lfg_set_notify_ids: that helper positions the
    # field after /lfg's "Players" field, which a poll has no equivalent of, so it
    # would land BETWEEN the response columns and break the inline row.
    if notify_ids and not closed:
        fields.append({"name": POLL_NOTIFY_FIELD,
                       "value": " ".join(f"<@{i}>" for i in notify_ids),
                       "inline": False})
    if fields:
        embed["fields"] = fields

    if closed:
        note = _poll_closed_note(closed_reason, closed_by, no, scheduled, kind)
        if note:
            embed["description"] += f"\n\n{note}"

    data = {"embeds": [embed], "allowed_mentions": {"parse": []}}
    if not closed:
        data["components"] = [_poll_buttons(proposal_pk, kind, proposer_id)]
    else:
        data["components"] = []
    return data


def _poll_closed_note(reason, closed_by, no_entries, scheduled, kind):
    """The `-#` subtext explaining how a poll ended."""
    if reason == "closed":
        who = f" by <@{closed_by}>" if closed_by else ""
        return f"-# Closed{who} before everyone responded."
    if scheduled:
        return "-# Scheduled — everyone confirmed."
    if no_entries:
        names = name_join([f"<@{e['id']}>" for e in no_entries])
        tail = ("\n-# Run `/schedule` to propose another time."
                if kind == "match" else "")
        return f"-# Not scheduled — {names} couldn't make it.{tail}"
    if kind != "match":
        return "-# Everyone confirmed."
    return None


def _poll_buttons(proposal_pk, kind, proposer_id):
    """Yes / No / 🔔 Notify / Close.

    Every custom_id ends in the non-snowflake "g" marker so the dispatcher's
    owner-lock does NOT fire -- anyone may be allowed to click, and each handler
    authorizes for itself. That includes Close: it is host-gated, but the lock
    admits exactly one snowflake and Close must also admit moderators.

    Match-mode ids carry the proposal pk (the store); embed-mode ids carry the
    kind (the voter gate) and the proposer (for Close). The proposer rides
    NON-last so the lock stays off."""
    if proposal_pk is not None:
        args = (proposal_pk,)
    else:
        args = (kind, proposer_id)
    return action_row(
        button("Yes", encode_custom_id("sched_poll_ok", *args, "g"),
               style=STYLE_SUCCESS),
        button("No", encode_custom_id("sched_poll_no", *args, "g"),
               style=STYLE_DANGER),
        button("", encode_custom_id("sched_poll_notify", *args, "g"),
               style=STYLE_SECONDARY, emoji={"name": "🔔"}),
        button("Close", encode_custom_id("sched_poll_close", *args, "g"),
               style=STYLE_SECONDARY),
    )


def _handle_schedule_free(payload):
    """Suggest: post the unlinked suggestion publicly. Nothing is written."""
    _action, args = decode_custom_id(payload["data"]["custom_id"])  # [kind, ts, owner]
    if len(args) < 3:
        return _ephemeral("That button is out of date — run /schedule again.")
    kind, ts, owner = args[0], args[1], args[2]
    try:
        when = datetime.fromtimestamp(int(ts), tz=dt_timezone.utc)
    except (ValueError, OSError, OverflowError):
        return _ephemeral("That time is no longer valid: run /schedule again.")

    token = payload.get("token")
    if not token:
        return _ephemeral("Couldn't post that — run /schedule again.")

    # A followup WITHOUT the ephemeral flag is public. countdown=2 lets this
    # response's ACK land first (a followup that races ahead 404s). Swallowed on a
    # broker outage: losing the post shouldn't replace the user's confirmation with
    # an error.
    try:
        post_interaction_followup_task.apply_async(
            (token, _schedule_free_public_data(
                when, owner, kind, author=_interaction_author(payload))),
            countdown=2)
    except Exception:
        logger.exception("Could not enqueue the suggested-time post")
        return _ephemeral("Couldn't post that just now — try again in a moment.")

    return JsonResponse({
        "type": RESPONSE_UPDATE_MESSAGE,
        "data": {"content": "Posted your suggested time.", "components": [],
                 "embeds": []},
    })


def _poll_proposer_seed(payload, owner, roster, kind):
    """The opening `yes` list: the proposer already counted, or [].

    They chose the time, so making them click Yes on their own poll is noise --
    and on a two-player LFG thread it is the difference between one answer closing
    the poll and two.

    Only when they can actually vote in it. A bare channel's poll is open to
    everyone present, so the proposer always counts; an LFG poll belongs to its
    thread's players, so someone polling a thread they aren't in seeds nobody and
    every player still has to answer -- the same rule _open_schedule_proposal
    applies to a match roster.

    The entry carries the raw snowflake as `id`, which is what _poll_state parses
    back out of the rendered line and what _poll_pending_names matches against
    Profile.discord_id, so the seeded Yes behaves exactly like a clicked one.

    Not seeded when the proposer is the ONLY person a roster poll is waiting on.
    That poll would open with nobody pending, and an embed poll only ever closes
    on a click -- so it would sit open and complete forever with no answer left to
    give. Leaving them unseeded keeps their own Yes as the click that closes it."""
    if not owner:
        return []
    name = _lfg_member_display_name(payload)
    if kind == "lfg" and roster:
        me, status = _resolve_clicker(
            roster, owner, _clicker_username(payload))
        if status != CLICKER_MATCHED:
            return []
        if len(roster) == 1:
            return []
        name = me.display_name or name
    return [{"id": str(owner), "name": name}]


def _handle_schedule_poll_open(payload):
    """Poll: post the public time poll.

    Routes on `kind`: a match poll becomes a ScheduleProposal (durable, drives the
    actual schedule write); every other poll lives in its own embed."""
    # [kind, match_id|SCHEDULE_NO_MATCH, ts, owner]
    _action, args = decode_custom_id(payload["data"]["custom_id"])
    if len(args) < 4:
        return _ephemeral("That button is out of date — run /schedule again.")
    kind, ts, owner = args[0], args[2], args[3]
    try:
        when = datetime.fromtimestamp(int(ts), tz=dt_timezone.utc)
    except (ValueError, OSError, OverflowError):
        return _ephemeral("That time is no longer valid: run /schedule again.")

    author = _interaction_author(payload)

    if kind == "match":
        return _open_match_poll(payload, when, owner, author)

    token = payload.get("token")
    if not token:
        return _ephemeral("Couldn't post that — run /schedule again.")

    roster, _thread = _poll_lfg_roster(payload) if kind == "lfg" else ([], None)

    # The proposer picked this time, so they are counted as a Yes without having
    # to click their own poll -- matching what _open_schedule_proposal does for a
    # match poll. Seeded BEFORE pending is computed so they don't appear in both
    # columns, and so an LFG poll they're the last member of can still close.
    yes = _poll_proposer_seed(payload, owner, roster, kind)
    pending = _poll_pending_names(roster, yes, []) if roster else None

    data = _schedule_poll_data(when, owner, yes=yes, no=[], notify_ids=[],
                               pending=pending, author=author, kind=kind)
    try:
        post_interaction_followup_task.apply_async((token, data), countdown=2)
    except Exception:
        logger.exception("Could not enqueue the time poll")
        return _ephemeral("Couldn't post that just now — try again in a moment.")

    return JsonResponse({
        "type": RESPONSE_UPDATE_MESSAGE,
        "data": {"content": "Posted your time poll.", "components": [],
                 "embeds": []},
    })


def _open_match_poll(payload, when, owner, author):
    """A poll on a real tournament match: durable, and able to write the time.

    Re-resolves the match and permission at click time rather than trusting the
    prompt, exactly as _handle_schedule_confirm does -- the button is a second
    request and the world may have moved."""
    match_id = _poll_match_id_from_message(payload)
    match = (_schedulable_matches(payload.get("guild_id"))
             .filter(pk=match_id).first() if match_id else None)
    if not match:
        return _ephemeral(
            "That match can no longer be scheduled: it may have been played or "
            "removed.")

    profile = Profile.objects.filter(discord_id=str(owner)).first()
    if not profile or not match.can_schedule(profile):
        return _ephemeral("You can't set the time for this match.")

    roster = _match_roster(match)
    if not roster:
        return _ephemeral(
            "This game has no players on its roster yet, so there's nobody to "
            "poll. A moderator can set the time directly instead.")

    return _open_schedule_proposal(payload, match, when, profile, roster,
                                   author=author)


def _poll_match_id_from_message(payload):
    """The match id out of a Poll custom_id: sched_poll_open:{kind}:{id}:{ts}:{owner}.

    SCHEDULE_NO_MATCH in every non-match mode, so the arg count is identical
    across all four and one decode shape reads them all."""
    _action, args = decode_custom_id((payload.get("data") or {}).get("custom_id") or "")
    if len(args) < 4:
        return None
    return None if _is_no_match(args[1]) else args[1]


def _handle_schedule_poll_dispatch(payload):
    """Route a poll button to the store that backs it.

    Match-mode ids carry the numeric proposal pk as their first arg; embed-mode
    ids carry the kind ("lfg"/"bare"). Telling them apart on `isdigit` keeps one
    handler name per button in the dispatch table while the two backends stay
    completely separate underneath."""
    action, args = decode_custom_id((payload.get("data") or {}).get("custom_id") or "")
    first = args[0] if args else ""
    if first.isdigit():
        return _handle_match_poll_click(payload, action)
    if action == "sched_poll_close":
        return _handle_schedule_poll_close(payload)
    return _handle_schedule_poll_respond(payload)


def _handle_match_poll_click(payload, action):
    """Yes / No / 🔔 / Close on a match poll — the ScheduleProposal-backed one."""
    if action == "sched_poll_ok":
        return _handle_schedule_proposal_confirm(payload)
    if action == "sched_poll_no":
        return _handle_schedule_proposal_reject(payload)
    if action == "sched_poll_notify":
        return _handle_match_poll_notify(payload)
    return _handle_match_poll_close(payload)


def _handle_match_poll_notify(payload):
    """🔔 on a match poll. The subscriber list lives in the embed even here, so
    this toggles the field and re-renders from the row + that list."""
    proposal, match, error = _proposal_for_click(payload, allow_agreed=True)
    if error:
        return error
    clicker_id = str(_interaction_user_id(payload))
    notify_ids = _poll_notify_ids_from_payload(payload)
    if clicker_id in notify_ids:
        notify_ids = [i for i in notify_ids if i != clicker_id]
    else:
        notify_ids.append(clicker_id)
    return JsonResponse({
        "type": RESPONSE_UPDATE_MESSAGE,
        "data": _schedule_proposal_data(
            proposal, match, author=_poll_author_from_payload(payload),
            notify_ids=notify_ids),
    })


def _handle_match_poll_close(payload):
    """Close on a match poll: the proposer or a moderator ends it early.

    Retires the row as CANCELLED rather than REJECTED -- nobody declined the
    time, the poll simply stopped -- which also keeps it out of the way of a
    later proposal for the same match."""
    proposal, match, error = _proposal_for_click(payload, allow_agreed=True)
    if error:
        return error

    clicker = Profile.objects.filter(
        discord_id=str(_interaction_user_id(payload) or "")).first()
    is_proposer = clicker and clicker.pk == proposal.proposed_by_id
    if not is_proposer and not (clicker and match.can_schedule(clicker)):
        return _ephemeral(
            "Only the person who started this poll, or a moderator, can close it.")

    notify_ids = _poll_notify_ids_from_payload(payload)
    ScheduleProposal.objects.filter(
        pk=proposal.pk, status__in=ScheduleProposal.LIVE_STATUSES,
    ).update(status=ScheduleProposal.Status.CANCELLED,
             resolved_at=timezone.now())
    proposal.refresh_from_db()
    if notify_ids:
        _notify_poll_closed(notify_ids, proposal.proposed_time, [],
                            scheduled=False,
                            closed_by=str(_interaction_user_id(payload)),
                            jump_url=_lfg_jump_url(payload))
    embed = schedule_closed_embed(
        proposal, "🗓 Poll closed", "closed", actor=clicker,
        label=_match_label(match), author=_poll_author_from_payload(payload))
    return JsonResponse({
        "type": RESPONSE_UPDATE_MESSAGE,
        "data": {"embeds": [embed], "components": [],
                 "allowed_mentions": {"parse": []}},
    })


def _poll_entries(field):
    """[{"id","name"}] parsed out of one response column's field.

    The lines are /lfg's "Name (<@id>)" shape, so _LFG_PLAYER_LINE_RE reads them
    straight back. A line that doesn't match is DROPPED rather than guessed at --
    the id is the identity, and an entry without one can't be moved or deduped."""
    entries = []
    for line in (field or {}).get("value", "").splitlines():
        m = _LFG_PLAYER_LINE_RE.match(line.strip())
        if m:
            entries.append({"name": m.group(1), "id": m.group(2)})
    return entries


def _poll_field_lookup(embed, base_name):
    """Find a response field whose name STARTS with the base label.

    The rendered names carry a count -- "✅ Yes (2)" -- so an exact match would
    miss every field that has anyone in it. Matching on the prefix keeps the
    count purely presentational, which is what lets it change on every click
    without breaking the parse."""
    for field in embed.get("fields", []):
        name = field.get("name", "")
        if name == base_name or name.startswith(f"{base_name} ("):
            return field
    return None


def _poll_state(embed):
    """(yes, no, notify_ids) read out of a poll embed."""
    def entries(base):
        return _poll_entries(_poll_field_lookup(embed, base))

    notify_field = _poll_field_lookup(embed, POLL_NOTIFY_FIELD)
    notify_ids = (_LFG_MENTION_RE.findall(notify_field.get("value", ""))
                  if notify_field else [])
    return entries(POLL_YES_FIELD), entries(POLL_NO_FIELD), notify_ids


def _poll_embed_meta(embed):
    """(when, proposer_id, label, author) recovered from a rendered poll embed.

    The poll is stateless in embed mode, so everything needed to re-render comes
    back off the message. The timestamp is parsed from the `<t:unix:F>` the
    description opens with rather than carried in the custom_id, which is capped
    at 100 chars and ':'-delimited."""
    description = embed.get("description", "")
    ts_match = re.search(r"<t:(\d+):", description)
    when = None
    if ts_match:
        try:
            when = datetime.fromtimestamp(int(ts_match.group(1)), tz=dt_timezone.utc)
        except (ValueError, OSError, OverflowError):
            when = None
    proposer = re.search(r"Suggested by <@!?(\d+)>", description)
    label_match = re.match(r"\*\*(.+?)\*\*", description)
    return (when, proposer.group(1) if proposer else None,
            label_match.group(1) if label_match else None,
            embed.get("author"))


def _poll_lfg_roster(payload):
    """(roster, thread) for an LFG-thread poll. Roster is [] when the thread is
    gone or has no players -- which makes the poll behave like a bare one rather
    than becoming unusable."""
    thread = _lfg_thread_for_channel(payload.get("channel_id"))
    if not thread:
        return [], None
    return list(thread.players.all()), thread


def _handle_schedule_poll_respond(payload):
    """Yes / No / 🔔 Notify on an embed-mode poll (LFG thread or plain channel).

    All state lives in the message's own embed fields — there is no row. A Yes or
    No MOVES the clicker between the two columns rather than only adding: they are
    removed from both first, then appended to whichever they chose, so neither
    column can hold the same person twice and switching answers works.

    Identity is the snowflake throughout, never the display name, so a rename
    can't double-count someone."""
    action, args = decode_custom_id(payload["data"]["custom_id"])
    kind = args[0] if args else "bare"
    proposer_id = args[1] if len(args) > 1 else None

    embed = dict((payload.get("message", {}).get("embeds") or [{}])[0])
    when, embed_proposer, label, author = _poll_embed_meta(embed)
    if when is None:
        return _ephemeral("That poll is out of date — run /schedule again.")
    proposer_id = embed_proposer or proposer_id

    clicker_id = str(_interaction_user_id(payload))
    display = _lfg_member_display_name(payload)

    # Who may vote. An LFG thread's poll belongs to its players; a bare channel's
    # is open to anyone there.
    roster, _thread = _poll_lfg_roster(payload) if kind == "lfg" else ([], None)
    if kind == "lfg" and roster:
        me, status = _resolve_clicker(
            roster, _interaction_user_id(payload), _clicker_username(payload))
        if status == CLICKER_UNLINKED:
            return _ephemeral(
                "You're one of this game's players, but your Discord isn't linked "
                f"to your site account yet. Log in{_login_hint()} with Discord "
                "once, then click again.")
        if status != CLICKER_MATCHED:
            return _ephemeral("Only the players in this thread can respond to that.")
        display = me.display_name or display

    yes, no, notify_ids = _poll_state(embed)

    if action == "sched_poll_notify":
        if clicker_id in notify_ids:
            notify_ids = [i for i in notify_ids if i != clicker_id]
        else:
            notify_ids.append(clicker_id)
    else:
        joining_yes = action == "sched_poll_ok"
        target = yes if joining_yes else no
        already_here = any(e["id"] == clicker_id for e in target)
        # The cap applies only to a NEW entry on a full column. Someone already on
        # it may always switch or be counted again, or a full poll would trap them.
        if (not already_here and roster == [] and kind != "match"
                and len(target) >= POLL_FREE_RESPONSE_MAX):
            return _ephemeral(
                f"This poll already has {POLL_FREE_RESPONSE_MAX} "
                f"{'Yes' if joining_yes else 'No'} responses.")
        yes = [e for e in yes if e["id"] != clicker_id]
        no = [e for e in no if e["id"] != clicker_id]
        (yes if joining_yes else no).append({"id": clicker_id, "name": display})

        if joining_yes and notify_ids:
            _notify_poll_yes(notify_ids, clicker_id, display, when, len(yes),
                             len(roster) or None, _lfg_jump_url(payload))

    # Everyone on the roster has answered -> close. A poll with no roster has no
    # completion condition and closes only via the Close button.
    pending = _poll_pending_names(roster, yes, no) if roster else None
    if roster and not pending:
        # An auto-close is still SOMEBODY's click -- the last answer owed -- so
        # exclude them from the result DM the same way a manual Close excludes
        # whoever pressed it. `closed_by` is only passed as the exclusion here;
        # the rendered note stays the everyone-answered one, not "closed by".
        return _poll_close_response(
            when, proposer_id, yes, no, notify_ids, label, author, kind,
            reason=None, closed_by=clicker_id, jump_url=_lfg_jump_url(payload))

    return JsonResponse({
        "type": RESPONSE_UPDATE_MESSAGE,
        "data": _schedule_poll_data(
            when, proposer_id, yes=yes, no=no, notify_ids=notify_ids,
            pending=pending, label=label, author=author, kind=kind),
    })


def _poll_pending_names(roster, yes, no):
    """Roster members who haven't answered, as rendered names."""
    answered = {e["id"] for e in yes} | {e["id"] for e in no}
    return [_roster_name(p) for p in roster
            if str(p.discord_id or "") not in answered]


def _handle_schedule_poll_close(payload):
    """Close (embed mode): the proposer ends the poll early.

    NOT owner-locked by the dispatcher — the lock admits exactly one snowflake,
    and this must also admit guild moderators. So the id rides non-last and the
    check happens here."""
    _action, args = decode_custom_id(payload["data"]["custom_id"])
    kind = args[0] if args else "bare"
    proposer_id = args[1] if len(args) > 1 else None

    embed = dict((payload.get("message", {}).get("embeds") or [{}])[0])
    when, embed_proposer, label, author = _poll_embed_meta(embed)
    if when is None:
        return _ephemeral("That poll is out of date — run /schedule again.")
    proposer_id = embed_proposer or proposer_id

    clicker_id = str(_interaction_user_id(payload))
    if proposer_id and clicker_id != proposer_id and not _poll_closer_is_staff(payload):
        return _ephemeral("Only the person who started this poll can close it.")

    yes, no, notify_ids = _poll_state(embed)
    return _poll_close_response(
        when, proposer_id, yes, no, notify_ids, label, author, kind,
        reason="closed", closed_by=clicker_id,
        jump_url=_lfg_jump_url(payload))


def _poll_closer_is_staff(payload):
    """Whether a non-proposer may close: a guild moderator or site admin.

    Embed-mode polls have no Match, so there is no can_schedule to consult --
    guild moderation is the only staff signal available here."""
    from .views import can_moderate_guild

    guild_id = payload.get("guild_id")
    if not guild_id:
        return False
    profile = Profile.objects.filter(
        discord_id=str(_interaction_user_id(payload))).first()
    if not profile:
        return False
    guild = DiscordGuild.objects.filter(guild_id=str(guild_id)).first()
    return bool(guild and can_moderate_guild(profile, guild))


def _poll_close_response(when, proposer_id, yes, no, notify_ids, label, author,
                         kind, *, reason, closed_by, jump_url=None):
    """Render the closed poll and DM the subscribers. Embed modes write nothing.

    `closed_by` always names whoever's click ended the poll, so they are excluded
    from the DM. It only reaches the RENDERER for an early close (reason
    "closed"), where "Closed by X" is the right note -- on an auto-close the last
    voter did not close anything, the roster simply finished."""
    if notify_ids:
        _notify_poll_closed(notify_ids, when, no, scheduled=False,
                            closed_by=closed_by, jump_url=jump_url)
    return JsonResponse({
        "type": RESPONSE_UPDATE_MESSAGE,
        "data": _schedule_poll_data(
            when, proposer_id, yes=yes, no=no, notify_ids=[], pending=None,
            label=label, author=author, kind=kind, closed=True,
            closed_reason=reason,
            closed_by=closed_by if reason == "closed" else None,
            scheduled=False),
    })


def _notify_poll_yes(notify_ids, actor_id, actor_name, when, yes_count, total,
                     jump_url):
    """DM the subscribers that someone confirmed. The actor is excluded — they
    just clicked, so telling them is noise."""
    targets = [i for i in notify_ids if str(i) != str(actor_id)]
    if not targets:
        return
    notify_schedule_poll_task.delay(
        targets, "yes", int(when.timestamp()), actor_name=actor_name,
        yes_count=yes_count, total=total, jump_url=jump_url)


def _notify_poll_closed(notify_ids, when, no_entries, *, scheduled, closed_by=None,
                        jump_url=None):
    """DM the subscribers the final result.

    `closed_by` is whoever's click ENDED the poll -- the person who pressed Close,
    or, on an auto-close, whoever cast the answer that completed the roster. They
    are excluded: they just did it, so telling them is noise.

    Deliberately not the host. A moderator may close a poll they did not start,
    and an auto-close is triggered by whichever player happens to answer last --
    so excluding the proposer would both spam the closer and silently drop the
    host from a result they are still subscribed to."""
    targets = [i for i in notify_ids if str(i) != str(closed_by or "")]
    if not targets:
        return
    notify_schedule_poll_task.delay(
        targets, "closed", int(when.timestamp()),
        declined=[e["name"] for e in no_entries], scheduled=scheduled,
        jump_url=jump_url)


def _handle_schedule_cancel(payload):
    """Cancel button: drop the prompt without writing anything. Shared by the set
    and clear flows, so the copy stays neutral."""
    return JsonResponse({
        "type": RESPONSE_UPDATE_MESSAGE,
        "data": {"content": "Cancelled — nothing was changed.", "components": []},
    })


def _schedule_tz_context(payload, args):
    """Shared guards for the timezone-prompt components: (match, profile, error).

    The dispatcher's owner-lock already proved WHO clicked; this re-checks that the
    match is still schedulable and they may still schedule it, exactly as the
    confirm handlers do — the prompt may have been sitting there a while.

    Returns `match=None` with NO error for the no-match sentinel: there is nothing
    to look up and nothing to authorize, and the callers only wanted the id for the
    next custom_id anyway. Checked FIRST — falling through would look the sentinel
    up, find nothing, and answer "That match can no longer be scheduled", which is
    a lie on a path that never had a match."""
    if len(args) < 2:
        return None, None, _ephemeral("That prompt is out of date — run /schedule again.")
    match_id, owner = args[0], args[-1]
    if _is_no_match(match_id):
        profile = _schedule_profile(owner)
        if not profile:
            return None, None, _ephemeral("User not found, try again.")
        return None, profile, None
    match = _schedulable_matches(payload.get("guild_id")).filter(pk=match_id).first()
    if not match:
        return None, None, _ephemeral(
            "That match can no longer be scheduled: it may have been played or removed."
        )
    profile = Profile.objects.filter(discord_id=str(owner)).first()
    if not profile or not match.can_schedule(profile):
        return None, None, _ephemeral("You can't set the time for this match.")
    return match, profile, None


def _handle_schedule_tz_region(payload):
    """Region select: show that region's cities."""
    _action, args = decode_custom_id(payload["data"]["custom_id"])  # [match_id, owner]
    match, profile, error = _schedule_tz_context(payload, args)
    if error:
        return error
    region_key = (payload["data"].get("values") or [None])[0]
    if not zones_for_region(region_key):
        return _ephemeral("I don't know that region — run /schedule again.")
    return JsonResponse({
        "type": RESPONSE_UPDATE_MESSAGE,
        "data": _tz_zone_data(args[0], region_key, _schedule_input_text(payload),
                              args[-1], current_tz=profile.timezone),
    })


def _handle_schedule_tz_zone(payload):
    """City select: save the timezone, then re-read the user's original time in it.

    Re-parsing the TEXT (not reusing the instant) is the point: "Mar 15 8pm" means
    8pm wherever they actually are."""
    _action, args = decode_custom_id(payload["data"]["custom_id"])  # [match_id, region, owner]
    match, profile, error = _schedule_tz_context(payload, args)
    if error:
        return error
    owner = args[-1]

    # Never trust a value echoed back by the client.
    tz_name = (payload["data"].get("values") or [None])[0]
    if not valid_timezone(tz_name):
        return _ephemeral("That isn't a timezone I recognize — run /schedule again.")
    if profile.timezone != tz_name:
        profile.timezone = tz_name
        # update_fields is required: a bare save() re-derives display_name and can
        # delete the profile's existing avatar.
        profile.save(update_fields=["timezone"])

    saved = f"✔ Saved your timezone as **{describe_timezone(tz_name)}**."
    time_text = _schedule_input_text(payload)
    if not time_text:
        # The carrier line is gone (a pre-deploy prompt, say). The timezone is
        # still worth keeping — they just have to ask for the time again.
        return JsonResponse({
            "type": RESPONSE_UPDATE_MESSAGE,
            "data": {"content": f"{saved}\nRun /schedule again to set a time.",
                     "components": []},
        })

    when, error = parse_user_datetime(time_text, tz_name)
    if error:
        # NEED_TIMEZONE can't happen — we just validated and stored a zone — so
        # `error` is always user-facing copy here. Report the save too: the
        # timezone is a real result even though the time didn't work out.
        return JsonResponse({
            "type": RESPONSE_UPDATE_MESSAGE,
            "data": {"content": f"{saved}\n{error}", "components": []},
        })

    return JsonResponse({
        "type": RESPONSE_UPDATE_MESSAGE,
        # match is None on the no-match sentinel; re-derive the unlinked kind from
        # the channel the same way the command did, since the tz custom_ids don't
        # carry it.
        "data": _schedule_confirm_data(
            match, when, owner, tz_name, time_text, note=f"{saved}\n",
            unlinked_kind=("lfg" if _lfg_thread_for_channel(payload.get("channel_id"))
                           else "bare")),
    })


def _handle_schedule_tz_back(payload):
    """Back to the region list. Also serves the confirmation's Change timezone
    button — the two want the same prompt, and _tz_region_data already varies its
    copy on whether a timezone is stored."""
    _action, args = decode_custom_id(payload["data"]["custom_id"])  # [match_id, owner]
    match, profile, error = _schedule_tz_context(payload, args)
    if error:
        return error
    return JsonResponse({
        "type": RESPONSE_UPDATE_MESSAGE,
        "data": _tz_region_data(args[0], _schedule_input_text(payload), args[-1],
                                current_tz=profile.timezone),
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
    appended. In a DM (no guild) the full command set is shown.

    Guilds with /lfg enabled get a `category` option (see help_command_for_guild);
    category:LFG returns the LFG walkthrough instead of the command list, trimmed to the
    commands that guild actually has enabled."""
    # _get_option returns None when the option is absent, which is the bare-/help case.
    if _get_option(data, "category") == HELP_CATEGORY_LFG:
        # One indexed lookup on the unique guild_id, narrower than the full-row fetch the
        # command-list branch below already runs -- worth it so the walkthrough never
        # tells a server to use a command Discord won't offer it. str() to match
        # _guild_allows; the value arrives from Discord as a string either way.
        guild_id = data.get("_guild_id")
        enabled_names = None
        if guild_id:
            enabled_names = (DiscordGuild.objects
                             .filter(guild_id=str(guild_id))
                             .values_list("enabled_commands", flat=True)
                             .first()) or []
        # A stale category:LFG from an old registration still renders rather than
        # erroring. In a guild with no row that now means the trimmed-to-nothing-enabled
        # walkthrough, matching how the branch below treats an absent row.
        return JsonResponse({
            "type": RESPONSE_CHANNEL_MESSAGE,
            "data": {"embeds": [build_lfg_help_embed(enabled_names)], "flags": EPHEMERAL},
        })

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
# "argument not supplied", for optional params whose None is a real value.
_UNSET = object()


def _parse_draft_state(custom_id):
    """('draft_build', players:int(2..6), platform:str) from a draft custom_id;
    falls back to defaults for a malformed/short id.

    ONLY valid for `draft_select`/`draft_build` ids (args[0]=players, args[1]=platform).
    Do NOT call on `draft_cancel:{owner}` or `draft_clear:{owner}` — their args[0] is
    the owner id, which would silently parse to the default players/platform.

    Unaffected by the `has_draft` flag those two ids carry at args[2]: it sits after
    the fields read here and before the owner, so both ends stay in place."""
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


def _draft_ui_data(players, platform, factions, banned_slugs, owner, has_draft=False):
    """The public ban UI: a faction ban select (current bans pre-selected via
    default=True) plus Build/Cancel buttons. `factions` is a list of
    (slug, title, type); `banned_slugs` is a set. `owner` is the invoker's user id,
    appended to every custom_id so only they can operate the controls.

    `has_draft` -- the thread already holds a draft, which building would REPLACE
    (LFGDraft is a OneToOne; see _replace_lfg_draft). That case says so in the copy,
    renames Build to "Recreate Draft", and offers a third button that clears the
    existing draft without building a new one -- previously only reachable through
    the Django admin, leaving a table that drafted by mistake stuck with a pool
    /pick would keep using. With no draft the layout is untouched: same copy, same
    "Build Draft", same two buttons.

    The flag rides in the select/build custom_ids (args[2]) rather than being
    re-derived, because _handle_draft_select re-renders this whole message on every
    ban change and a component handler has no cheap path back to the thread. It goes
    BEFORE the owner: the dispatcher's owner-lock keys on the LAST arg looking like a
    snowflake."""
    platform_key = DRAFT_PLATFORM_TO_KEY.get(platform, "tts")
    draft_key = "d" if has_draft else "n"
    options = [
        select_option(title, slug, emoji=faction_emoji_object(slug), default=slug in banned_slugs)
        for slug, title, _type in factions
    ]
    select = string_select(
        encode_custom_id("draft_select", players, platform_key, draft_key, owner),
        options,
        placeholder="Select factions to ban (optional)",
        min_values=0,
        max_values=len(options),
    )
    build_label = "Recreate Draft" if has_draft else "Build Draft"
    row = [
        button(build_label,
               encode_custom_id("draft_build", players, platform_key, draft_key, owner),
               style=STYLE_SUCCESS),
    ]
    if has_draft:
        # Destructive, so STYLE_DANGER -- the vocabulary /schedule's Clear Time and
        # the seating Overwrite button already use.
        row.append(button("Clear Draft", encode_custom_id("draft_clear", owner),
                          style=STYLE_DANGER))
    row.append(button("Cancel", encode_custom_id("draft_cancel", owner),
                      style=STYLE_SECONDARY))

    content = f"**{players} Player Draft** — pick factions to ban, then Build."
    if has_draft:
        content += "\nThis thread already has a draft — recreating **replaces** it."
    return {
        "content": content,
        "components": [action_row(select), action_row(*row)],
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


def _thread_roster_size(data, thread=_UNSET):
    """How many players this thread holds, for defaulting /draft's count. 0 when
    the channel is neither kind of thread.

    Mirrors _pick_roster's branch: a tournament group thread's roster lives on the
    GROUP (its LFGThread has an empty `players`), an LFG thread's on the thread.

    `thread` is the already-resolved LFGThread from the caller, so its draft lookup
    and this share one query. The default is a sentinel, NOT None: None is the
    legitimate "this channel has no LFGThread" answer, and treating it as "not
    supplied" would re-run the query the caller passed it to avoid."""
    channel_id = data.get("_channel_id")
    if thread is _UNSET:
        thread = _lfg_thread_for_channel(channel_id)
    if thread and not thread.series_id:
        return thread.players.count()

    group = player_group_for_channel(
        channel_id, data.get("_channel_name"), data.get("_guild_id"))
    if group:
        return len(group_roster(group, group_series_id(group)))
    return thread.players.count() if thread else 0


def _handle_draft_command(data):
    """/draft: open the public, owner-locked ban UI for the chosen players/platform.

    With no `players` option the count defaults to the thread's own roster size
    (clamped to 2..6 like any other value); anywhere else falls back to 4.

    Both kinds of thread count: an LFG game thread uses its `players`, and a
    tournament group thread uses the GROUP's roster. A group thread's LFGThread is
    created with only `series` set -- players.set() only ever runs on the /lfg
    path -- so reading thread.players there always found 0 and silently opened a
    4-player draft for a table of 3.

    When the thread already holds a draft the prompt says so and offers Clear
    alongside Recreate; see _draft_ui_data."""
    # Resolved once, UNCONDITIONALLY: _thread_roster_size runs only when `players`
    # was omitted, so relying on its lookup would miss the existing draft whenever
    # someone passed an explicit count. One indexed hit on a unique column.
    thread = _lfg_thread_for_channel(data.get("_channel_id"))
    # getattr, NOT thread.draft: LFGDraft.thread is a OneToOne, so the reverse
    # accessor RAISES when there's no draft.
    has_draft = getattr(thread, "draft", None) is not None if thread else False

    players = _get_option(data, "players")
    if players is None:
        players = _thread_roster_size(data, thread) or 4
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
                               owner=data.get("_author_id"), has_draft=has_draft),
    })


def _handle_draft_select(payload):
    """Ban select changed: re-render the public UI with the chosen bans marked
    default=True, so the selection persists in the message's component state. The
    owner rides in the incoming custom_id; re-emit it to keep the controls locked.

    The has_draft flag rides along the same way. It has to: this rebuilds the WHOLE
    message, so re-deriving it as False would drop the Clear button and revert the
    copy the moment anyone touched a ban. Reading it from the id also keeps this
    handler query-free -- a button click carries no channel name, and the flag was
    already resolved when the prompt was built."""
    custom_id = payload["data"]["custom_id"]
    _action, players, platform = _parse_draft_state(custom_id)
    _, args = decode_custom_id(custom_id)
    owner = args[-1] if args else None
    # args[2] on the current id shape; absent on one built before the flag existed,
    # which reads as "no draft" and keeps the old two-button layout.
    has_draft = len(args) >= 3 and args[2] == "d"
    banned_slugs = set(payload["data"].get("values", []))  # this select echoes its own values
    factions = _draft_eligible_factions(platform, players)
    return JsonResponse({
        "type": RESPONSE_UPDATE_MESSAGE,
        "data": _draft_ui_data(players, platform, factions, banned_slugs, owner=owner,
                               has_draft=has_draft),
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


def _captain_pool(platform=None):
    """Every captain-capable Vagabond — the same pool the game form and /captain
    use (captain=True). Root Digital narrows to those available there.

    Ordered by title, not "?": callers that want a random subset shuffle
    themselves, and a stable order is what a full picker should show."""
    qs = Vagabond.objects.filter(official=True, status=1, captain=True)
    if platform == DRAFT_PLATFORM_RD:
        qs = qs.filter(in_root_digital=True)
    return list(qs.order_by("title"))


def _random_draft_captains(platform):
    """Up to `DRAFT_CAPTAIN_COUNT` random captain-capable Vagabonds for a draft
    that landed Knaves of the Deepwood. Returns as many as exist when fewer than
    4 qualify (possibly an empty list)."""
    pool = _captain_pool(platform)
    random.shuffle(pool)
    return pool[:DRAFT_CAPTAIN_COUNT]


def _draft_build_result(factions, banned_slugs, players, platform, owner):
    """Draw a draft and assemble everything needed to record it.

    Returns (drawn, vagabond, captains, items, draft_payload, error); on error
    every other member is None and the caller renders `error` however suits its
    message.

    Shared by /draft and /adset, which differ only in how they PRESENT the
    result: /draft edits its prompt into an embed and offers seating, /adset
    edits into its next phase. Everything up to that point -- the draw, the two
    conditional rolls, the exclusion rules, the roll-log items and the
    Celery-safe payload -- is identical, and duplicating it is how the two would
    drift apart.

    `items` and `draft_payload` hold only slugs and ids: the payload is
    JSON-serialized by Celery, so a model instance would raise EncodeError."""
    drawn, error = _build_draft(factions, banned_slugs, players)
    if error:
        return None, None, None, None, None, error

    # If the Vagabond faction was drafted, roll a specific vagabond to play it;
    # if Knaves of the Deepwood was drafted, roll its 4 captains. (The two are
    # mutually exclusive in a draft, so at most one of these applies.)
    vagabond = _random_draft_vagabond(platform) if "vagabond" in drawn else None
    captains = _random_draft_captains(platform) if "knaves-of-the-deepwood" in drawn else None

    titles = {slug: title for slug, title, _ftype in factions}
    items = [{"kind": "Faction", "slug": slug, "title": titles.get(slug, slug)} for slug in drawn]
    if vagabond:
        items.append(_lfg_item("Vagabond", vagabond))
    if captains:
        items.extend(_lfg_item("Captain", c) for c in captains)

    # The vagabond attaches to the pick that drew "vagabond", and the captains to
    # the one that drew "knaves-of-the-deepwood" -- never to picks[0]. The two are
    # mutually exclusive (DRAFT_EXCLUSIONS), so at most one pick carries either.
    draft_payload = {
        "players": players,
        "platform": platform,
        "drafted_by": owner,
        "picks": [
            {"faction": slug,
             "vagabond": vagabond.slug if (slug == "vagabond" and vagabond) else None,
             "captains": ([c.slug for c in captains]
                          if (slug == "knaves-of-the-deepwood" and captains) else []),
             "order": i}
            for i, slug in enumerate(drawn, 1)
        ],
    }
    return drawn, vagabond, captains, items, draft_payload, None


def _handle_draft_build(payload):
    """Build button: recover bans from the message's select state, build the draft,
    and edit the public prompt message into the result embed in place."""
    _action, players, platform = _parse_draft_state(payload["data"]["custom_id"])
    # A button press doesn't echo the select's values, so recover them from the
    # message's persisted select state.
    banned_slugs = set(selected_values(payload, "draft_select"))
    factions = _draft_eligible_factions(platform, players)

    # The drafter's Discord id (LAST custom_id arg -- the has_draft flag sits before
    # it), resolved to a Profile in the worker so no extra query lands in this
    # 3-second interaction budget.
    _action_id, id_args = decode_custom_id(payload["data"]["custom_id"])
    owner = id_args[-1] if id_args else ""

    drawn, vagabond, captains, items, draft_payload, error = _draft_build_result(
        factions, banned_slugs, players, platform, owner)
    if error:
        # Public edit (the message is public): show the error, clear the buttons.
        return JsonResponse({
            "type": RESPONSE_UPDATE_MESSAGE,
            "data": {"content": error, "embeds": [], "components": []},
        })

    # If used inside an LFG thread, record the drafted factions plus the rolled
    # vagabond / captains onto the LFGThread. NOT wrapped in try/except: a failure
    # here would otherwise replace an already-delivered draft with an error.
    _capture_lfg_components(payload.get("channel_id"), items,
                            source="draft", draft=draft_payload)

    embed = _draft_result_embed(
        drawn, players, platform, banned_slugs, factions,
        author=_interaction_author(payload), vagabond=vagabond, captains=captains,
    )
    # Inside an LFG game thread, offer to seat the players (ephemeral, so only the
    # drafter sees the prompt). Enqueued as a followup rather than returned, because
    # this response is the public result edit.
    _offer_lfg_seating(payload)
    # Edit the public prompt into the result: content "" clears the prompt text,
    # components [] removes the buttons. (The result itself is already public; the
    # only follow-up is the ephemeral seating prompt above.)
    return JsonResponse({
        "type": RESPONSE_UPDATE_MESSAGE,
        "data": {"embeds": [embed], "content": "", "components": []},
    })


def _lfg_seating_prompt_data(thread, owner):
    """The ephemeral Yes/No seating prompt for `thread`, as interaction response
    data.

    When the thread already has a seating, the copy warns that confirming REPLACES
    it (a thread holds one current seating) and the confirm button turns into a red
    "Overwrite".

    Split from _offer_lfg_seating so /seating can return this prompt as its own
    response while /draft still ships it as a followup. The seating_set branch is
    reachable only from /seating: _offer_lfg_seating skips the offer entirely on an
    already-seated thread, so /draft never proposes an overwrite unprompted.

    Keys on seating_set, NOT seats.exists(): /pick can assign factions without a
    seating, leaving seat rows with filler numbers -- warning about overwriting a
    seating that was never set would be a lie."""
    if thread.seating_set:
        content = ("This game already has a seating order — seating again will "
                   "**overwrite it**. Seat the players again?")
        confirm, style = "Overwrite", STYLE_DANGER
    else:
        content = "Seat the players for this game?"
        confirm, style = "Yes", STYLE_SUCCESS
    return {
        "content": content,
        "flags": EPHEMERAL,
        "components": [action_row(
            button(confirm, encode_custom_id("draft_seat", owner), style=style),
            button("No", encode_custom_id("draft_seat_no", owner),
                   style=STYLE_SECONDARY),
        )],
    }


def _offer_lfg_seating(payload):
    """After a draft inside an LFG thread, send the drafter an ephemeral Yes/No
    prompt offering to seat the thread's players. No-op outside an LFG thread, when
    the roster is too small to seat, when the thread is already seated, or when the
    guild hasn't enabled /seating -- confirming would otherwise run seating the
    guild deliberately turned off."""
    if not _guild_allows(payload.get("guild_id"), "seating"):
        return
    # thread.players deliberately, NOT the group roster: this offer leads to
    # _handle_draft_seat, which seats the THREAD's own roster. A tournament group
    # thread has an empty `players` (only /lfg fills it), so it no-ops here and
    # reaches seating through /seating instead -- which resolves the group roster
    # itself. Using the group roster here would seat it from the wrong handler.
    thread = _lfg_thread_for_channel(payload.get("channel_id"))
    if not thread or thread.players.count() < 2:
        return
    # Already seated: /draft must not assume a reroll means "reseat too". The
    # prompt's confirm button REPLACES the current order, and nobody asked for
    # that -- rerolling a draft mid-setup is routine. Reseating stays available
    # through /seating, which was typed deliberately and keeps its Overwrite
    # warning. Keys on seating_set, not seats.exists(): /pick can leave filler
    # seat rows behind on a table that was never actually seated.
    if thread.seating_set:
        return
    token = payload.get("token")
    if not token:  # can't send a followup without the interaction token
        return

    _action, args = decode_custom_id(payload["data"]["custom_id"])
    owner = args[-1] if args else ""
    # The countdown lets the initial interaction response reach Discord first — a
    # followup that races ahead of the ACK 404s.
    #
    # The prompt is an optional extra on top of a draft that already succeeded, so a
    # broker outage must cost only the prompt: swallow it rather than let the
    # dispatcher's catch-all replace the finished draft with an error message.
    try:
        post_interaction_followup_task.apply_async(
            (token, _lfg_seating_prompt_data(thread, owner)),
            countdown=2,
        )
    except Exception:
        logger.exception("Could not enqueue the LFG seating prompt for thread %s",
                         thread.thread_id)


def _handle_draft_cancel(payload):
    """Cancel button: edit the public prompt to a short notice, buttons removed.
    Carries only `draft_cancel:{owner}` — deliberately does NOT call
    _parse_draft_state (which would misread the owner id as players/platform)."""
    return JsonResponse({
        "type": RESPONSE_UPDATE_MESSAGE,
        "data": {"content": "Draft cancelled.", "embeds": [], "components": []},
    })


def _draft_clear(thread):
    """Drop the thread's draft and the rolls that recorded it. Returns whether
    there was a draft to clear.

    Only source="draft" rolls: /random, /pick and the lookups share the log and
    their history isn't ours to drop -- the same scoping _pick_clear documents.
    Leaving them behind would keep the record form (which narrows its component
    choices from the roll log) offering a draft that no longer exists.

    The SEATING and any picks survive. /seating and /pick own those; a table that
    wants to redo a draft hasn't asked to lose the order it already agreed on.
    """
    with transaction.atomic():
        deleted, _ = LFGDraft.objects.filter(thread=thread).delete()
        # LFGDraftPick cascades off LFGDraft, so `deleted` counts picks too --
        # hence the boolean rather than a row count.
        LFGRoll.objects.filter(thread=thread, source="draft").delete()
        # Both deletes touch CHILDREN only, so nothing above saved the thread.
        # Without this bump an actively-used thread ages toward cleanup; save()
        # supplies last_activity for the empty update_fields.
        thread.save(update_fields=[])
    return bool(deleted)


def _handle_draft_clear(payload):
    """Clear Draft button: delete the thread's existing draft without building a
    new one, and edit the public prompt into a short notice.

    Only offered when a draft exists (see _draft_ui_data), and owner-locked for
    free -- `draft_clear:{owner}` ends in the invoker's snowflake, so the
    dispatcher blocks everyone else before this runs.

    Re-resolves and re-checks rather than trusting the flag baked into the button:
    a click is a SECOND request, and the draft can have gone since the prompt was
    rendered (a concurrent clear, the thread recorded). Same reason
    _handle_schedule_clear_confirm re-fetches its match.

    Carries only `draft_clear:{owner}` — like Cancel, deliberately NOT passed to
    _parse_draft_state, which would misread the owner id as players/platform."""
    thread = _lfg_thread_for_channel(payload.get("channel_id"))
    if not thread:
        return JsonResponse({
            "type": RESPONSE_UPDATE_MESSAGE,
            "data": {"content": "This isn't a game thread anymore.",
                     "embeds": [], "components": []},
        })

    if not _draft_clear(thread):
        return JsonResponse({
            "type": RESPONSE_UPDATE_MESSAGE,
            "data": {"content": "This thread has no draft to clear.",
                     "embeds": [], "components": []},
        })

    # The pool consequence is the part worth telling: _pick_pool treats a draft as
    # /pick's ENTIRE pool and silently widens back to every official Stable faction
    # once it's gone.
    return JsonResponse({
        "type": RESPONSE_UPDATE_MESSAGE,
        "data": {"content": "✔ Draft cleared — `/pick` is back to the full faction pool.",
                 "embeds": [], "components": []},
    })


def _draft_seating_message(seats, reseated=False):
    """The public seating message: numbered seats, then who picks first. The LAST
    seat has first pick (Root drafts factions in reverse seat order).

    `reseated` prefixes a banner marking this as a replacement — the superseded
    seating message stays in the thread history, so without it two numbered lists
    sit there with nothing saying which is current.

    Plain names, not mentions: everyone in the thread already sees this message,
    and mentioning every player would ping the whole table."""
    lines = []
    if reseated:
        lines += ["**Re-seated** - this replaces the previous seating order.", ""]
    else:
        lines += ["**Seating**", ""]       
    lines += [f"{s.seat_number}. {s.profile.name}" for s in seats]
    lines += ["", f"{seats[-1].profile.name} has first pick of the faction draft"]
    return "\n".join(lines)


def _persist_seating(thread, profiles, shuffle=True):
    """Shuffle `profiles` into seats 1..N on `thread`, REPLACING any current order.
    Returns (seats, reseated).

    The shared write behind /seating and the draft's Seat button. A thread holds
    ONE current seating, so this replaces rather than appends (unlike the roll
    log); select_for_update serializes two concurrent reseats, which would
    otherwise collide on uniq_lfg_seat_per_thread.

    `reseated` keys on seating_set, NOT seats.exists(): /pick can leave seat rows
    behind with filler numbers and no real order, and those must not be reported
    as a previous seating this replaces.

    `shuffle=False` keeps the given order, for a caller that already knows the
    seating (/boxscore reads it from an uploaded game). `profiles` may contain
    None in that case -- LFGSeat.profile is nullable, and a blank seat still
    holds its position so the record form renders a row for it."""
    ordered = list(profiles)
    if shuffle:
        random.shuffle(ordered)
    with transaction.atomic():
        locked = LFGThread.objects.select_for_update().filter(pk=thread.pk).first() or thread
        reseated = locked.seating_set
        locked.seats.all().delete()
        # Build the instances ourselves rather than reusing bulk_create's return:
        # these already hold their Profile, so the message renderer reads
        # seat.profile.name with no extra query.
        seats = [LFGSeat(thread=locked, profile=p, seat_number=i)
                 for i, p in enumerate(ordered, 1)]
        LFGSeat.objects.bulk_create(seats)
        # Same transaction as the rows it describes: the flag and the seats must
        # never be separately visible. Saved unconditionally -- the seats above
        # were just rewritten, so a re-seat of an already-seated thread must still
        # bump last_activity (save() supplies it) even though the flag is already
        # True.
        if not locked.seating_set:
            locked.seating_set = True
        locked.save(update_fields=["seating_set"])
        thread.seating_set = True  # keep the caller's instance in step
    return seats, reseated


def _handle_seating_command(data):
    """/seating: seat this thread's players without needing a draft.

    Two kinds of thread, in priority order:

    * An LFG game thread — the seating step /draft offers, reachable on its own.
      Confirmed through the shared draft_seat handler and SAVED, so the record
      form can place effort rows by seat.
    * A tournament player group's thread — seats the group's roster, posts it
      straight away, and SAVES it, so /pick reuses this exact order instead of
      shuffling a second, contradictory one and announcing that.

      This used to be display-only, on the reasoning that a group thread spans a
      whole series so a stored order goes stale by the next game. But /pick has to
      persist seats to attach factions to, so "never store" was never actually
      achievable -- it just meant the stored order was the one nobody was shown.
      Re-running /seating reshuffles and says it replaced the previous order.

    Unlike _offer_lfg_seating (an optional extra after a draft, silent when it
    doesn't apply), this was typed deliberately: say why nothing happened."""
    channel_id = data.get("_channel_id")

    # A tournament group thread also gets an LFGThread (it captures rolls the same
    # way), so this must tell the two apart. `series_id` alone isn't enough: a
    # group with no MatchSeries yet leaves it NULL, and once /seating has created
    # the row that thread would look exactly like an LFG one and get answered
    # "not enough players". An LFG thread is the one with its OWN players -- that
    # roster is what this branch goes on to seat, and only /lfg ever fills it.
    thread = _lfg_thread_for_channel(channel_id)
    if thread and not thread.series_id and thread.players.exists():
        if thread.players.count() < 2:
            return _ephemeral("Not enough players in this thread to seat.")
        # Returned as this command's own response rather than a followup: there's
        # no earlier message to sequence after, so no Celery hop or countdown.
        return JsonResponse({
            "type": RESPONSE_CHANNEL_MESSAGE,
            "data": _lfg_seating_prompt_data(thread, data.get("_author_id")),
        })

    group = player_group_for_channel(
        channel_id, data.get("_channel_name"), data.get("_guild_id"))
    if group:
        # group_roster, not group.tournament_players: a group whose M2M was never
        # populated still has MatchSeats, and those are what the site shows. Reading
        # the M2M alone told people with 3 players on screen they had none.
        profiles = group_roster(group, group_series_id(group))
        if len(profiles) < 2:
            return _ephemeral(
                "This group doesn't have enough players to seat yet.")

        # Needs a row to hang the seats on. A group thread's LFGThread is created
        # on first use by whichever command gets there first -- same get-or-create
        # _pick_thread_for_channel and the capture task both do.
        # series may be None (a group not yet tied to one) -- the FK is nullable,
        # and seating doesn't depend on it. It's set when known so this row is
        # recognisable as a group thread, the way the LFG branch above tells them
        # apart.
        if not thread:
            thread, _created = LFGThread.objects.get_or_create(
                thread_id=channel_id,
                defaults={"series_id": group_series_id(group)})

        seats, reseated = _persist_seating(thread, profiles)
        # Public: the whole group should see the order, same as an LFG seating.
        # No confirmation step -- /seating in a group thread was typed
        # deliberately, and the message says when it replaced an earlier order.
        return JsonResponse({
            "type": RESPONSE_CHANNEL_MESSAGE,
            "data": {"content": _draft_seating_message(seats, reseated),
                     "allowed_mentions": {"parse": []}},
        })

    return _ephemeral(
        "Use this in a game thread or a player group's thread to seat its players.")


def _handle_draft_seat_no(payload):
    """No: dismiss the ephemeral prompt. Nothing is posted or persisted."""
    return JsonResponse({
        "type": RESPONSE_UPDATE_MESSAGE,
        "data": {"content": "No seating assigned.", "components": []},
    })


def _handle_draft_seat(payload):
    """Seat button: randomly assign seats 1..N to the LFG thread's roster, post the
    seating publicly in the thread, and persist it on the LFGThread (replacing any
    previous order)."""
    thread = _lfg_thread_for_channel(payload.get("channel_id"))
    if not thread:
        # Type 7, not an ephemeral: this prompt carries Yes/No buttons, and an
        # ephemeral cannot edit the message it came from -- the buttons would stay
        # live and keep erroring with no way to dismiss them.
        return _pick_retire_panel("This isn't a game thread anymore.")
    profiles = list(thread.players.all())
    if len(profiles) < 2:
        return _ephemeral("Not enough players in this thread to seat.")

    seats, reseated = _persist_seating(thread, profiles)

    post_channel_message_task.delay(
        thread.thread_id, _draft_seating_message(seats, reseated))
    return JsonResponse({
        "type": RESPONSE_UPDATE_MESSAGE,
        "data": {"content": "Seating posted.", "components": []},
    })


# ── /pick ──────────────────────────────────────────────────────────────────
# Who may operate the faction select. Assign mode locks it to the invoker;
# players mode hands each turn to the seated player whose turn it is.
PICK_MODE_ASSIGN = "a"
PICK_MODE_PLAYERS = "p"

# Every /pick component custom_id ends with this marker instead of a snowflake,
# so the dispatcher's owner-lock (which reads args[-1]) stays OFF and players
# other than the invoker can click. Authorization happens in the handlers --
# same escape hatch /lfg's Join and the public /schedule proposal buttons use.
PICK_OPEN = "g"


def _pick_thread_for_channel(channel_id, channel_name=None, guild_id=None):
    """The LFGThread for this channel, creating one for a tournament group thread
    on first use. None when the channel is neither.

    Mirrors record_lfg_components_task's get-or-create so /pick works in a group
    thread that has never captured a roll. `getattr`, NOT `group.series`:
    MatchSeries.player_group is a OneToOne, so the reverse accessor RAISES
    RelatedObjectDoesNotExist when the group has no series (a third of them
    don't).

    channel_name/guild_id are optional and only enable the group TITLE fallback;
    the button paths have no channel name and stay id-only, as before."""
    thread = _lfg_thread_for_channel(channel_id)
    if thread:
        return thread
    group = player_group_for_channel(channel_id, channel_name, guild_id)
    series = getattr(group, "series", None) if group else None
    if not series:
        return None
    thread, _ = LFGThread.objects.get_or_create(
        thread_id=channel_id, defaults={"series": series})
    return thread


def _pick_roster(thread, channel_id, channel_name=None, guild_id=None):
    """The players /pick should seat: a tournament group's roster in a group
    thread, else the LFG thread's own players. [] when too small to seat."""
    if thread.series_id:
        group = player_group_for_channel(channel_id, channel_name, guild_id)
        if not group:
            return []
        # Same roster resolver /seating and the consensus flow use: reading
        # tournament_players alone misses groups populated by seats only.
        profiles = group_roster(group, thread.series_id)
    else:
        profiles = list(thread.players.all())
    return profiles if len(profiles) >= 2 else []


# Commands that write to the thread they're used in, so a non-player must not run
# them where a roster exists. Everything else stays open: /help, /stats,
# /upcoming, /law, /card and /captain only read; /lfg CREATES a game (there is no
# roster to belong to yet); and /record only hands back a link the site re-checks
# permissions on, so gating it would block a moderator fixing a mis-recorded game
# without actually protecting anything. /rename keeps its own host-only rule.
ROSTER_GUARDED_COMMANDS = {
    "pick", "seating", "draft", "adset", "schedule", "random", "boxscore",
    *LOOKUP_QUERYSETS,   # faction, clockwork, map, deck, vagabond, landmark,
                         # hireling, houserule -- all capture into the roll log
}


def _thread_roster(thread, channel_id, channel_name=None, guild_id=None):
    """(roster, group) for this channel's game. Roster is [] when there is none.

    Unlike _pick_roster this does NOT impose a minimum: a one-player thread still
    HAS a roster, and treating it as empty would leave it unguarded.

    The group is handed back so the caller can reuse it for the staff check
    without resolving it a second time."""
    if thread is not None and not thread.series_id:
        return list(thread.players.all()), None
    group = player_group_for_channel(channel_id, channel_name, guild_id)
    if not group:
        return [], None
    return group_roster(group, group_series_id(group)), group


def _thread_actor_error(data):
    """Ephemeral refusal when this channel's game has a roster and the invoker is
    not on it, its host, or staff. None when the command may proceed.

    NO ROSTER, NO RESTRICTION. A plain channel, or a thread whose game has no
    players yet, is unguarded -- there is nothing to protect and these commands
    are how a table gets set up in the first place. That also means a tournament
    group thread whose group has neither tournament_players NOR MatchSeats is
    open; correct for a group that hasn't been populated, and it closes the moment
    anyone is added.

    Ordering is deliberate (Discord allows 3 seconds for the whole interaction):
    the cheap indexed thread lookup runs first, and the profile resolution LAST --
    ensure_profile_from_discord can WRITE (it claims or creates a Profile), so it
    must never run for a command in a channel with no roster at all."""
    channel_id = data.get("_channel_id")
    if not channel_id:
        return None

    thread = _lfg_thread_for_channel(channel_id)
    guild_id = data.get("_guild_id")
    # NOTE this can LINK the thread to a matching group as a side effect of the
    # title fallback (see player_group_for_channel). Deliberate: the link is
    # correct whoever typed the command, and the alternative is re-guessing it on
    # every later lookup.
    roster, group = _thread_roster(
        thread, channel_id, data.get("_channel_name"), guild_id)
    if not roster:
        return None

    profile = ensure_profile_from_discord(
        data.get("_author_id"), data.get("_author_username"),
        (data.get("_author") or {}).get("name"))
    if not profile:
        return _ephemeral("I couldn't identify you, so I can't tell if you're in "
                          "this game. Try again.")

    if any(p.pk == profile.pk for p in roster):
        return None
    if thread is not None and thread.host_id == profile.pk:
        return None
    if _thread_staff_override(profile, group, guild_id):
        return None

    return _ephemeral(
        "Only the players in this game can use that here. Ask one of them, or a "
        "moderator, to run it for you.")


def _thread_staff_override(profile, group, guild_id):
    """Whether a non-player may act anyway: a guild moderator or site admin, or —
    in a tournament group thread — anyone who could schedule that match (group
    moderator, organizer, admin). Lets staff unstick a table they aren't in.

    Takes the GROUP, not the LFGThread. A group thread has no LFGThread until the
    first command creates one, so keying the can_schedule branch off the thread
    would refuse a group moderator's very first command -- the same trap the
    roster lookup avoids by resolving the group directly."""
    from .views import can_moderate_guild

    if guild_id:
        guild = DiscordGuild.objects.filter(guild_id=str(guild_id)).first()
        if guild and can_moderate_guild(profile, guild):
            return True

    # can_schedule lives on Match, so this only applies to a tournament group.
    series_id = group_series_id(group) if group else None
    if series_id:
        match = Match.objects.filter(series_id=series_id).order_by(
            "match_number").first()
        if match and match.can_schedule(profile):
            return True
    return False


def _pick_seat_roster(thread, channel_id, ordered=True, announce=True):
    """Give `thread` seats so /pick has something to attach factions to.

    Two very different callers:

    * `ordered=True` — a real seating. The roster is SHUFFLED and seating_set is
      set. Reached from /pick's "Seat Players" button, where the invoker
      explicitly asked for a random order.
    * `ordered=False` — /pick's "Skip Seating". The roster keeps its own order and
      NOTHING is posted: seat_number is non-nullable, so the rows still need 1..N,
      but those numbers are filler and must not be presented as an order.
      Shuffling here would be worse than useless -- with no explicit seat_order on
      the record form, Effort.seat falls back to row order, so a shuffle would
      silently invent a turn order nobody agreed to.

    `announce` posts the order to the thread; only meaningful with ordered=True.
    /pick passes False: the panel it opens next already lists the seats NUMBERED
    (seating_set is set), so a separate seating message would just say the same
    thing twice.

    Returns the seats, or [] when the roster is too small.
    """
    profiles = _pick_roster(thread, channel_id)
    if not profiles:
        return []

    if ordered:
        random.shuffle(profiles)
    created = False
    with transaction.atomic():
        locked = LFGThread.objects.select_for_update().filter(pk=thread.pk).first() or thread
        if locked.seats.exists():  # another click won the race
            seats = list(locked.seats.select_related("profile"))
        else:
            seats = [LFGSeat(thread=locked, profile=p, seat_number=i)
                     for i, p in enumerate(profiles, 1)]
            LFGSeat.objects.bulk_create(seats)
            created = True
        # Same transaction as the rows it describes, so the flag and the seats are
        # never separately visible.
        if ordered and not locked.seating_set:
            locked.seating_set = True
            locked.save(update_fields=["seating_set"])
            thread.seating_set = True  # keep the caller's instance in step
        elif created:
            # Seats were written but the flag didn't change, so nothing above
            # saved the thread. Bump it so /pick-created seating counts as
            # activity. The race loser (created False) changed nothing.
            locked.save(update_fields=[])

    # Announce only the seating WE created -- the race loser must not post a
    # second, contradictory order. Outside the lock: a broker hiccup shouldn't
    # hold the row, and the seats are already committed either way. Nothing is
    # posted for an unordered assignment: there is no order to announce.
    if created and ordered and announce:
        post_channel_message_task.delay(
            thread.thread_id, _draft_seating_message(seats))
    return seats


def _pick_pool(thread):
    """The factions this thread may pick from, as [(slug, title, vagabond_slug)].

    With a draft, the draft IS the pool -- and each pick carries the vagabond
    rolled for it, so choosing Vagabond attaches the right one automatically.
    Without a draft, every official Stable faction is fair game.

    `getattr(thread, "draft", None)`: LFGDraft.thread is a OneToOne, so the
    reverse accessor RAISES when the thread has no draft."""
    draft = getattr(thread, "draft", None)
    if draft:
        return [
            (p.faction.slug, p.faction.title,
             p.vagabond.slug if p.vagabond_id else None)
            for p in draft.picks.select_related("faction", "vagabond")
        ]
    # Same filter as _draft_eligible_factions. `status=1` is an int against a
    # CharField of string choices -- Django coerces on the lookup, and this
    # matches every other call site.
    return [
        (slug, title, None)
        for slug, title in Faction.objects.filter(
            official=True, status=1, component="Faction",
        ).order_by("title").values_list("slug", "title")
    ]


def _pick_free(thread, mode):
    """Whether picks are OPEN to any pending player rather than taken in turn.

    True only when the players are picking for themselves AND there is no seating:
    with no agreed order, making them wait on an arbitrary roster position is
    friction with no purpose. A seating (or Assign mode) keeps the strict turn
    order -- there the order is the point.

    The single source of truth for this branch, so the condition can't drift
    between the panel, the turn resolver and the follow-ups. Takes `mode` because
    mode lives only in the custom_id, never on the thread."""
    return mode == PICK_MODE_PLAYERS and not thread.seating_set


def _pick_pending_seats(seats):
    """Every seat still owed a faction, in seat_number order.

    Seats whose Profile was deleted are SKIPPED: no clicker could ever match them,
    so waiting on one would stall the table forever. Filters on `profile_id`, NOT
    on discord_id -- an unlinked player is someone the table is genuinely still
    waiting on, and hiding them would silently shorten the board.

    Ascending, because this list is DISPLAYED (the board reads top-to-bottom);
    _pick_next_seat takes the last of it for the reverse-order turn."""
    return [s for s in sorted(seats, key=lambda s: s.seat_number)
            if not s.faction_id and s.profile_id]


def _pick_next_seat(seats):
    """The seat whose turn it is: the highest-numbered seat with no faction yet.

    The LAST seat picks first, then descending -- Root drafts factions in reverse
    seat order.

    Also serves as the board-complete test (None == every seat is filled), which
    is correct in free mode too: "no highest pending seat" is exactly "no pending
    seats at all"."""
    pending = _pick_pending_seats(seats)
    return pending[-1] if pending else None


def _pick_seat_for_clicker(seats, clicker):
    """The clicker's OWN unfilled seat, or None -- free mode's answer to "whose
    turn is it", where the answer is "whoever just clicked, if they're owed one".

    None covers three cases the caller phrases apart: not in the game, already
    picked, and on the roster but unlinked.

    Matched on discord_id ONLY. Profile.discord_id is nullable and seats are built
    straight from roster Profiles, so an unlinked player has a real seat carrying a
    NULL id -- and `str(None)` is the string "None", which would compare against a
    snowflake rather than being skipped. The truthiness guard is what makes that
    unreachable instead of accidentally-correct. (A username is user-controlled and
    is never proof of identity; see _resolve_clicker.)"""
    if not clicker:
        return None
    return next((s for s in _pick_pending_seats(seats)
                 if s.profile.discord_id
                 and str(s.profile.discord_id) == str(clicker)), None)


# Enough names that a real table never truncates (Root seats 6), while still
# bounding the line -- a large tournament roster must not push the panel past
# Discord's 2000-char content limit.
PICK_PENDING_NAMES_MAX = 8


def _pick_pending_line(seats):
    """"Alice, Bob & Carol pick." -- everyone still owed a faction.

    Free mode's replacement for the single-name turn line: with no order there is
    no "next", so the panel names everyone who may act.

    Plain names, not mentions. The board directly above renders every seat as
    `profile.name`, and re-pinging a group on each edit would spam the table --
    the same reason the ordered turn line's mention is neutered by
    allowed_mentions. Singular "picks" for one name: free mode reaches that state
    on the last player."""
    names = [s.profile.name for s in _pick_pending_seats(seats)]
    if not names:
        return ""
    if len(names) == 1:
        return f"{names[0]} picks."
    if len(names) > PICK_PENDING_NAMES_MAX:
        shown = ", ".join(names[:PICK_PENDING_NAMES_MAX])
        return f"{shown} & {len(names) - PICK_PENDING_NAMES_MAX} more pick."
    return f"{', '.join(names[:-1])} & {names[-1]} pick."


# Stands in for a faction that has been taken, in the Factions row. Discord
# cannot dim or grey a custom emoji (they are fixed images, and ~~strikethrough~~
# draws a line across the picture), so a neutral placeholder is the only way to
# mark one as spent -- and unlike simply dropping it, this keeps the row's LENGTH
# and ORDER stable across the dozen in-place edits a pick session makes.
PICK_TAKEN_MARK = "⭘"


def _pick_faction_row(thread, pool, taken, force=False):
    """The 'Factions' row: every DRAFTED faction in draft order, taken ones shown
    as PICK_TAKEN_MARK. "" when the thread has no draft.

    Draft-only on purpose. With a draft the pool is a short, deliberate set the
    table chose, and showing it turns the panel into the whole picture. Without
    one the pool is every official Stable faction, and a 13-emoji row would be
    noise that pushes the board itself off the first screen.

    Falls back to the title when a faction's emoji was never uploaded, matching
    _draft_result_embed.

    `force` renders the row for a pool the CALLER knows is a draft even though the
    thread doesn't show one yet: /adset records its draft through Celery, so the
    row would otherwise be blank on the very message that announces the draw.

    getattr, NOT thread.draft: LFGDraft.thread is a OneToOne, so the reverse
    accessor RAISES when there's no draft."""
    if not force and getattr(thread, "draft", None) is None:
        return ""
    marks = [PICK_TAKEN_MARK if slug in taken else (faction_emoji_for(slug) or title)
             for slug, title, _vb in pool]
    return "Factions\n" + " ".join(marks)


def _pick_seat_lines(thread, seats, pool=None, header=None, force_row=False):
    """The seat board both /pick messages show: one line per seat, with each taken
    seat's faction as `<emoji> <name>`.

    Shared so the panel and its follow-up can't drift: the follow-up REPLACES the
    panel message, so any difference here would show up as the board silently
    changing shape mid-pick.

    `header` overrides the default "**Faction Picks**" / "**Faction Assignments**"
    title, so /adset can keep one stable name across every phase of its single
    message.

    `pool` enables the Factions row above the board (see _pick_faction_row); it
    renders only when the thread has a draft. Passed in rather than re-derived so
    callers that already hold the pool don't pay for a second query. The row lives
    HERE rather than at each call site because both consumers need it: the panel,
    and the turn-order follow-up that REPLACES the panel -- omit it there and the
    row would vanish mid-pick and reappear after, which reads as a bug.

    The emoji is a prefix, not a replacement -- faction_emoji_for returns "" for
    fan factions and for official ones whose emoji was never uploaded, and a name
    alone still reads correctly.

    A seat's second choice trails the faction name: the Vagabond character it
    took, or Knaves of the Deepwood's captains. Both are what distinguishes two
    seats that otherwise read identically -- all 12 vagabond variants share one
    Faction row -- so the board is ambiguous without them.

    Captains are shown only once the seat has COMMITTED. `captains` holds the
    OFFERED set while the follow-up prompt is open (the commit overwrites it with
    the chosen 3), so rendering it then would show the table an offer as though it
    were a decision. The FACTION is the marker: it too is written only by the
    commit, which is why the prompt parks captains on a seat whose faction is
    still None. (Not discarded_captain -- that is set only when exactly one
    captain was left over, which a full-pool offer never produces.)"""
    ordered = thread.seating_set
    lines = [header or ("**Faction Picks**" if ordered else "**Faction Assignments**"), ""]
    if pool is not None:
        row = _pick_faction_row(
            thread, pool, {s.faction.slug for s in seats if s.faction_id},
            force=force_row)
        if row:
            lines += [row, ""]
    for seat in sorted(seats, key=lambda s: s.seat_number):
        who = seat.profile.name if seat.profile_id else "(removed player)"
        prefix = f"{seat.seat_number}. " if ordered else "• "
        if seat.faction_id:
            emoji = faction_emoji_for(seat.faction.slug)
            mark = f"{emoji} {seat.faction.title}" if emoji else seat.faction.title
            mark += _pick_seat_detail(seat)
            lines.append(f"{prefix}{who} - {mark}")
        else:
            lines.append(f"{prefix}{who}")
    return lines


def _pick_seat_detail(seat):
    """The trailing " <emoji> <name>" for a seat's vagabond, or " <emoji>…" for its
    captains. "" when the seat has neither.

    Emoji-with-name for the single vagabond (it names one character, so the name
    carries), emoji-only for the three captains -- three names would crowd the
    line, and the emoji are the quick read. Each falls back to the title when its
    emoji isn't uploaded, so nothing silently disappears.

    Takes an LFGSeat OR an LFGDraftPick: both carry `vagabond` and `captains`
    under those exact names, and nothing here touches anything else. The
    undrafted row below renders through it so the leftover faction reads
    identically to a taken seat."""
    if seat.vagabond_id:
        emoji = vagabond_emoji_for(seat.vagabond)
        return f" ({emoji} {seat.vagabond.title})" if emoji else f" ({seat.vagabond.title})"
    # Callers only reach here for a seat that HAS a faction, and the faction is
    # written by the same commit that narrows the parked offer to the chosen
    # captains -- so these are a decision, never an open offer. (A draft pick's
    # captains are likewise final: the draft rolls them once and never revises.)
    marks = [vagabond_emoji_for(c) or c.title for c in seat.captains.all()]
    if marks:
        return f" ({' '.join(marks)})"
    return ""


def _pick_undrafted_line(thread):
    """The board row for the one drafted faction nobody took, or "".

    Only ever one line: undrafted_pick returns the leftover ONLY when exactly one
    is left, so this is silent without a draft, mid-pick, and on any draft/seat
    desync -- which is also why the no-draft case can't dump the whole faction
    list here.

    Rendered exactly like a taken seat's faction (emoji prefix, _pick_seat_detail
    suffix) so the leftover reads as part of the board, but with no seat prefix
    and a trailing "Undrafted" instead of a name -- it belongs to no player, and
    a leading number or bullet would sit it in the seat column and read as one."""
    pick = undrafted_pick(thread)
    if pick is None:
        return ""
    emoji = faction_emoji_for(pick.faction.slug)
    mark = f"{emoji} {pick.faction.title}" if emoji else pick.faction.title
    mark += _pick_seat_detail(pick)
    return f"{mark} Undrafted"


def _pick_panel_data(thread, seats, mode, owner, pool=None, notice=None, header=None):
    """The public pick panel, rebuilt from the DB on every interaction so the
    bot stays stateless and a stale message can never drive a write.

    `header` overrides the board title so /adset keeps one name across its whole
    single-message flow; it rides in the custom_id (see _pick_header) because the
    downstream follow-up handlers rebuild this panel too. The Factions row needs
    no such flag -- it derives from the thread's own draft.

    The seat whose turn it is is derived here, not carried in a custom_id -- that
    is what makes a double-click land on the same seat and be rejected as already
    taken, rather than consuming two picks.

    `notice` is an optional subtext line explaining why the panel just changed
    under the reader (today: the draft was re-rolled). Rendered as Discord subtext
    so it reads as an aside rather than competing with the board."""
    pool = pool if pool is not None else _pick_pool(thread)
    taken = {s.faction.slug for s in seats if s.faction_id}

    # Read from the thread, not a custom_id arg: the panel is rebuilt from the DB
    # every time, so there is one source of truth and a stale message can never
    # claim an ordering the DB disagrees with. When the seats are filler, the
    # numbers must not be shown as an order the players never agreed to.
    ordered = thread.seating_set

    lines = _pick_seat_lines(thread, seats, pool=pool, header=header)

    nxt = _pick_next_seat(seats)
    if nxt is None:
        # Only on the FINAL edit: mid-pick there is more than one faction left,
        # and naming them would read as a suggestion to the seat still choosing.
        leftover = _pick_undrafted_line(thread)
        if leftover:
            lines.append(leftover)
        lines += ["", "Draft Complete" if ordered else "All factions assigned."]
        return {"content": "\n".join(lines), "components": [],
                "allowed_mentions": {"parse": []}}

    free = _pick_free(thread, mode)
    seat_label = f" (seat {nxt.seat_number})" if ordered else ""
    if mode == PICK_MODE_ASSIGN:
        lines += ["", f"Assigning for **{nxt.profile.name}**{seat_label}."]
    elif free:
        # No order, so no "next" to name -- everyone still owed a faction may act.
        lines += ["", _pick_pending_line(seats)]
    else:
        lines += ["", f"<@{nxt.profile.discord_id}> picks{seat_label}."]

    if notice:
        lines.append(f"-# {notice}")

    options = [
        select_option(title, slug, emoji=faction_emoji_object(slug))
        for slug, title, _vb in pool if slug not in taken
    ]

    stop_row = action_row(
        button("Stop", encode_custom_id("pick_cancel", owner, PICK_OPEN),
               style=STYLE_SECONDARY))

    # Seats still owed a faction but nothing left to offer them. Discord rejects a
    # select with zero options, so sending one would 400 and leave the panel frozen
    # on whatever it last showed. _pick_pool_error only guards the pool at /pick
    # START, so a /draft re-run to a smaller pool can strip the last options
    # mid-session and reach here. Keep Stop: it's the only way out.
    if not options:
        lines += ["", "No factions left to choose. Use Stop to start over."]
        return {"content": "\n".join(lines), "components": [stop_row],
                "allowed_mentions": {"parse": []}}

    if ordered:
        placeholder = f"Faction for seat {nxt.seat_number}"
    elif free:
        # Addresses whoever is reading: several people may act on this panel, and
        # naming one of them would read as a turn indicator.
        placeholder = "Choose your faction"
    else:
        placeholder = f"Faction for {nxt.profile.name}"[:100]

    select = string_select(
        encode_custom_id("pick_faction", mode, owner, PICK_OPEN),
        options, placeholder=placeholder, min_values=1, max_values=1,
    )
    return {
        "content": "\n".join(lines),
        "components": [action_row(select), stop_row],
        # The panel is edited on every pick; re-pinging each time would spam the
        # table, so the mention above renders as plain text.
        "allowed_mentions": {"parse": []},
    }


def _pick_vagabond_pool():
    """The Vagabonds a seat may take when it picks the Vagabond faction.

    `status=1` is an int against a CharField of string choices -- Django coerces
    on the lookup, and this matches every other call site. Slugless rows are
    excluded because the seat is matched back by slug."""
    return list(Vagabond.objects.filter(official=True, status=1)
                .exclude(slug__isnull=True).order_by("title"))


def _pick_followup_data(thread, seats, mode, owner, faction_slug, options,
                        placeholder, action, min_values=1, max_values=1,
                        panel_id="", pool=None, header=None):
    """A follow-up select for a faction that needs a second choice before the seat
    can be written (Vagabond, Knaves).

    `faction_slug` rides in the custom_id because the seat has no faction yet:
    the write is deferred until this select resolves, so an abandoned prompt
    leaves the seat untouched rather than stranding it half-recorded.

    `panel_id` likewise: in free mode this is shown as an EPHEMERAL beside the
    shared panel, and the interaction that resolves it carries the EPHEMERAL's
    message id, not the panel's -- so the panel's id has to be handed forward to
    be refreshed afterwards. Empty in turn-order modes, where the follow-up IS the
    panel. It sits before PICK_OPEN, which must stay last or the dispatcher
    owner-locks the component.

    That same flag decides the SHAPE of this prompt, because the two cases are
    doing different jobs:

    * Turn-order (panel_id ""): the prompt REPLACES the panel, so it carries the
      whole board (dropping the seat lines would blank it mid-pick) and Stop,
      which is then the table's only remaining control.
    * Free mode (panel_id set): the panel is still on screen right above this,
      so repeating the board here is a duplicate that goes stale the moment
      anyone else picks -- and Stop would let one player clear the WHOLE table's
      picks from a message nobody else can see. Ask the question, nothing more.
      Abandoning it is already a no-op: the seat isn't written until this
      resolves, so the player can ignore it and pick again from the panel."""
    free = bool(panel_id)
    # `pool`/`header` only matter on the turn-order branch, which carries the whole
    # board: free mode renders no board at all, so passing them there is harmless.
    lines = [] if free else _pick_seat_lines(thread, seats, pool=pool, header=header)
    lines += ["", placeholder] if lines else [placeholder]

    select = string_select(
        encode_custom_id(action, mode, owner, faction_slug, panel_id, PICK_OPEN),
        options, placeholder=placeholder[:100],
        min_values=min_values, max_values=max_values,
    )
    components = [action_row(select)]
    if not free:
        components.append(action_row(
            button("Stop", encode_custom_id("pick_cancel", owner, PICK_OPEN),
                   style=STYLE_SECONDARY)))
    return {
        "content": "\n".join(lines),
        "components": components,
        "allowed_mentions": {"parse": []},
    }


def _pick_vagabond_panel_data(thread, seats, mode, owner, faction_slug,
                              panel_id="", pool=None, header=None):
    """The "which Vagabond?" follow-up. All 12 vagabond variants share one Faction
    row, so without this the seat records Vagabond with no identity and Ranger and
    Thief collapse into the same record."""
    options = [
        select_option(vb.title, vb.slug,
                      emoji=parse_emoji_object(vagabond_emoji_for(vb)))
        for vb in _pick_vagabond_pool()
    ]
    return _pick_followup_data(
        thread, seats, mode, owner, faction_slug, options,
        "Which Vagabond?", "pick_vagabond", panel_id=panel_id,
        pool=pool, header=header)


PICK_CAPTAIN_CHOICES = 3


def _pick_captains_panel_data(thread, seats, mode, owner, faction_slug, captains,
                              panel_id="", pool=None, header=None):
    """The "pick 3 of 4" follow-up for Knaves of the Deepwood.

    `captains` is the already-rolled offer, not the whole captain-capable pool:
    the rule is pick 3 of 4 ROLLED, and the pool is larger than 4 (6 today), so
    offering all of it would let a seat take captains that were never offered."""
    options = [
        select_option(c.title, c.slug,
                      emoji=parse_emoji_object(vagabond_emoji_for(c)))
        for c in captains
    ]
    return _pick_followup_data(
        thread, seats, mode, owner, faction_slug, options,
        f"Choose {PICK_CAPTAIN_CHOICES} captains", "pick_captains",
        min_values=PICK_CAPTAIN_CHOICES, max_values=PICK_CAPTAIN_CHOICES,
        panel_id=panel_id, pool=pool, header=header)


def _pick_followup_response(data, free):
    """How a Vagabond/Knaves follow-up is delivered.

    Turn-order modes REPLACE the panel: only one seat can act, so nothing is taken
    away from anyone.

    Free mode can't do that -- several players may be choosing at once, and
    replacing the shared panel with one player's prompt would take the board away
    from the rest until they resolved it (or forever, if they wandered off; the
    only reset is Stop, which discards everyone's picks). So the prompt goes to
    that player as an EPHEMERAL and the panel is left alone. Type 4 is valid as the
    INITIAL response to a component interaction, so this needs no deferral or
    webhook."""
    if free:
        return JsonResponse({
            "type": RESPONSE_CHANNEL_MESSAGE,
            "data": {**data, "flags": EPHEMERAL},
        })
    return JsonResponse({"type": RESPONSE_UPDATE_MESSAGE, "data": data})


def _pick_refresh_panel(payload, panel_id, data):
    """PATCH the shared panel with freshly rendered `data`, for a free-mode
    follow-up that was answered in an ephemeral.

    Only the ephemeral is updated by the interaction response itself, so without
    this the panel would sit one pick stale until somebody else picked.

    Cosmetic, and deliberately best-effort: edit_channel_message never raises and
    a failure (panel deleted, perms lost) leaves a stale-but-harmless panel that
    the next pick rebuilds anyway -- a completed write must never be reported as
    an error because its redraw didn't land."""
    if not panel_id:
        return
    result = edit_channel_message(
        payload.get("channel_id"), panel_id,
        content=data.get("content"), components=data.get("components"))
    if result != THREAD_OK:
        logger.warning("Could not refresh /pick panel %s in channel %s (%s).",
                       panel_id, payload.get("channel_id"), result)


def _pick_panel_id(args):
    """The shared panel's message id carried by a free-mode follow-up, or "".

    args[3] is the panel slot ONLY when the custom_id actually has one. A
    turn-order prompt encodes `action:mode:owner:faction:g`, so args[3] is
    PICK_OPEN -- and a follow-up message posted before that slot existed looks
    exactly the same. Reading either as an id would make a turn-order prompt
    behave as free mode: it would try to PATCH a message called "g" and dismiss
    the very panel it is supposed to be."""
    if len(args) > 3 and args[3] != PICK_OPEN:
        return args[3]
    return ""


# Marks a pick custom_id as belonging to an /adset flow, so the panel keeps its
# own title instead of reverting to "**Faction Picks**" the first time a pick
# handler rebuilds it. A single character: custom_ids cap at 100 and the
# follow-up ids already carry mode, owner, faction slug and panel id.
PICK_ADSET_FLAG = "x"
ADSET_TITLE = "**Adset Draft**"


def _pick_retire_panel(
        text="This game's setup has expired — start again with `/pick`."):
    """Retire a panel whose thread is gone: say why and REMOVE the controls.

    Deliberately NOT _ephemeral. An ephemeral is a separate private message and
    never edits the one it came from, so the panel would keep live buttons that
    can only ever error again -- and because cleanup_stale_lfg_threads deletes
    threads on a 30/180-day timer while Discord keeps message components forever,
    that state is permanent and undismissable. Type 7 is the only response that
    can actually take the buttons away; _handle_draft_clear does the same for its
    own missing-thread case.

    Only for "this message can never work again". Authorization refusals ("it's
    someone else's turn", "only the players can stop this") stay ephemeral: those
    address one clicker and must leave the shared panel intact for everyone else.
    """
    return JsonResponse({
        "type": RESPONSE_UPDATE_MESSAGE,
        "data": {"content": text, "embeds": [], "components": []},
    })


def _pick_close_panel(thread, channel_id, actor, panel_id=None, skip_id=None):
    """Edit a superseded pick panel into a closing notice, best-effort.

    `panel_id` overrides thread.pick_panel_id, because the caller usually has to
    read it BEFORE _pick_clear (which nulls the field, so reading it here would
    find nothing left to close).

    `skip_id` is the id of the message the caller is ALREADY answering. Skipping
    it is not an optimisation: the takeover button usually lives on the panel
    itself (_handle_pick_mode turns the current message into the panel and
    records that id), so PATCHing it here while the interaction response also
    edits it would be two concurrent writes on one message with no defined
    winner -- the race link_lfg_message_task documents.

    Best-effort like _pick_refresh_panel: edit_channel_message never raises, and
    the picks are already cleared by the time this runs, so a failed redraw must
    never fail the takeover."""
    panel_id = panel_id or thread.pick_panel_id
    if not panel_id or (skip_id and str(panel_id) == str(skip_id)):
        return
    who = f" by {actor}" if actor else ""
    result = edit_channel_message(
        channel_id, panel_id,
        content=f"Picks closed and restarted{who}.", components=[])
    if result != THREAD_OK:
        logger.warning("Could not close superseded pick panel %s in channel %s (%s).",
                       panel_id, channel_id, result)


def _pick_header(args):
    """The board title carried by a pick custom_id, or None for plain /pick.

    Read from a trailing FLAG rather than from the action prefix, because the
    downstream follow-up handlers (pick_vagabond / pick_captains) only ever see
    `pick_*` ids no matter which command opened the panel -- a prefix test would
    be right at the mode handler and wrong everywhere after it.

    Scanned rather than read positionally: pick ids vary in length (the follow-ups
    carry a faction slug and panel id that the panel's own select does not), and
    the flag must not disturb _pick_panel_id's args[3] slot."""
    return ADSET_TITLE if PICK_ADSET_FLAG in args else None


def _pick_dismiss_ephemeral(text):
    """Collapse a resolved free-mode prompt to one line with no controls.

    Discord has no API to DELETE an ephemeral, so this is how one is dismissed --
    the same thing _handle_draft_seat does with "Seating posted."

    Returning the board here instead (which is what a shared-panel handler does)
    would hand the player a private duplicate of the panel: a faction select that
    can only ever refuse them, since they now hold a faction, and a Stop that
    would clear the WHOLE table's picks from a message nobody else can see.

    embeds is cleared alongside components because a type-7 response replaces
    only the keys it sends -- these prompts are content-only today, so this just
    keeps a future embed-bearing one from leaving a stale embed behind."""
    return JsonResponse({
        "type": RESPONSE_UPDATE_MESSAGE,
        "data": {"content": text, "components": [], "embeds": []},
    })


def _pick_setup_reminder(thread):
    """A subtext nudge listing the setup this thread is still missing, or "".

    Names only what's ACTUALLY absent -- a table that already chose a map doesn't
    need to be told to choose one -- and stays silent when both are set, so the
    prompt isn't carrying a permanent notice nobody reads.

    Deliberately names NO command. /map and /deck are per-guild (enabled_commands,
    see _guild_allows), so naming them can point at commands this guild doesn't
    have -- the same reason the too-few-players message avoids mentioning
    /seating. It also isn't a requirement: a table can settle its map and deck
    anywhere, and this only records what the bot happens to know.

    `-#` is Discord's subtext: it renders small and grey, which is what keeps this
    a reminder rather than an error competing with the question above it."""
    missing = [name for name, value in (("map", thread.map_id),
                                        ("deck", thread.deck_id)) if not value]
    if not missing:
        return ""
    return f"\n-# Remember to choose a {' and '.join(missing)} for this game."


def _pick_mode_prompt_data(thread, owner):
    """The Players-pick / Assign-all prompt. Owner-locked: it decides how the whole
    table proceeds.

    The ONLY prompt /pick opens, and its three buttons are the same either way --
    a seating changes what order the seats are filled in, never what the invoker
    is asked. Players-pick needs no seating: each pick is authorized against that
    seat's own profile, so it simply follows the roster order instead.

    Only the wording tracks that: "in seat order" would overstate a roster order
    nobody agreed to, so it becomes "in turn" when there is no seating."""
    turn = "in seat order" if thread.seating_set else "in turn"
    return {
        "content": (f"**Faction Picks** — assign every faction yourself, or let "
                    f"each player pick {turn}?"
                    + _pick_setup_reminder(thread)),
        "components": [action_row(
            button("Players pick",
                   encode_custom_id("pick_mode", PICK_MODE_PLAYERS, owner),
                   style=STYLE_SUCCESS),
            button("Assign all",
                   encode_custom_id("pick_mode", PICK_MODE_ASSIGN, owner),
                   style=STYLE_PRIMARY),
            button("Cancel", encode_custom_id("pick_cancel", owner, PICK_OPEN),
                   style=STYLE_SECONDARY),
        )],
    }


def _pick_unseated_prompt_data(owner):
    """The first /pick prompt when the thread has no seating: assign a random
    order, or keep the players in their current order.

    Neither button posts a seating message -- the panel that follows lists the
    seats itself (numbered after Seat Players, bulleted after Skip Seating), so
    announcing it separately would say the same thing twice.

    Takes only `owner`, no thread: nothing is written until a button is pressed,
    so an abandoned prompt leaves no seats behind.

    Deliberately carries NO _pick_setup_reminder -- both buttons lead straight to
    the mode prompt, which already has it, and repeating it one click apart would
    just be noise."""
    return {
        "content": ("**Faction Picks** — these players haven't been seated yet. "
                    "Assign a random seating order, or use the current player "
                    "order?"),
        "components": [action_row(
            button("Seat Players", encode_custom_id("pick_seat", owner),
                   style=STYLE_SUCCESS),
            button("Skip Seating",
                   encode_custom_id("pick_noseat", owner), style=STYLE_PRIMARY),
            button("Cancel", encode_custom_id("pick_cancel", owner, PICK_OPEN),
                   style=STYLE_SECONDARY),
        )],
    }


def _pick_in_progress(thread):
    """Whether a pick session is already underway.

    Inferred from the seats rather than a flag on the thread: the panel is
    already rebuilt from them on every interaction, so this stays the single
    source of truth. A flag would be a second one that desyncs -- a deleted panel
    or a restart would strand the thread as "picking" with nothing able to clear
    it.

    A seat mid-follow-up still has no faction, so an abandoned Vagabond/Knaves
    prompt does NOT lock the thread. That is deliberate: the alternative traps
    the table behind a prompt nobody can resolve."""
    return LFGSeat.objects.filter(thread=thread, faction__isnull=False).exists()


def _pick_clear(thread):
    """Undo a pick session: drop every seat's faction/vagabond/captains and the
    rolls that recorded them. Returns how many seats had a faction.

    The SEATING survives -- the rows and seating_set stay. /seating owns that,
    the table agreed to it, and on an ordered thread it was posted publicly, so
    re-picking must not silently reshuffle or contradict it.

    No select_for_update: .update() is atomic per statement and takes its own row
    locks, and LFGSeat.profile/.faction are nullable -- a locking query with a
    join across them is the Postgres FOR UPDATE trap documented in _pick_commit.
    """
    cleared = LFGSeat.objects.filter(thread=thread, faction__isnull=False).count()
    # .update() writes ONLY the columns named here, so every nullable pick field
    # must be listed -- one left out survives Stop and prefills onto the next game.
    LFGSeat.objects.filter(thread=thread).update(
        faction=None, vagabond=None, discarded_captain=None)
    for seat in LFGSeat.objects.filter(thread=thread):
        seat.captains.clear()

    # Only this command's rolls: /draft, /random and the lookups share the log,
    # and their history isn't ours to drop. Leaving pick rolls behind would keep
    # the record form narrowed to factions no seat holds any more.
    LFGRoll.objects.filter(thread=thread, source="pick").delete()

    # The session is over, so its panel id must not outlive it -- a stale id
    # would let the next takeover "close" an unrelated message.
    if thread.pick_panel_id:
        thread.pick_panel_id = None
        thread.save(update_fields=["pick_panel_id"])
    return cleared


def _pick_pool_error(thread, count):
    """The "not enough factions" refusal, or None when the pool is big enough."""
    pool = _pick_pool(thread)
    if len(pool) < count:
        return _ephemeral(
            f"Only {len(pool)} factions available for {count} players — "
            "run `/draft` first, or add more factions.")
    return None


def _handle_pick_command(data):
    """/pick: choose factions for this game.

    With a seating already established, picks run in seat order (last seat first)
    and this goes straight to the mode prompt -- re-seating there would overwrite
    an order players were just shown.

    Without one, it first asks whether to assign a random order (Seat Players) or
    keep the roster order (Skip Seating); either way the mode prompt follows.

    Works in an LFG game thread and in a tournament group thread (whose roster
    comes from the player group). The pool is the thread's draft when it has one,
    otherwise every official Stable faction."""
    channel_id = data.get("_channel_id")
    channel_name = data.get("_channel_name")
    guild_id = data.get("_guild_id")
    thread = _pick_thread_for_channel(channel_id, channel_name, guild_id)
    if not thread:
        return _ephemeral(
            "Use this in a game thread or a player group's thread to pick factions.")

    owner = data.get("_author_id")
    # One session at a time: a second panel would write to the same seats, so two
    # tables could pick into one game. Rather than refuse and send the table off to
    # find a panel that may have scrolled away, offer to close it and start over --
    # the same confirmation /adset shows, so both commands share one path back.
    if _pick_in_progress(thread):
        underway = list(thread.seats.select_related(
            "profile", "faction", "vagabond").prefetch_related("captains"))
        return JsonResponse({
            "type": RESPONSE_CHANNEL_MESSAGE,
            "data": _adset_takeover_data(thread, underway, owner, header=None),
        })

    seats = list(thread.seats.select_related(
        "profile", "faction", "vagabond",
    ).prefetch_related("captains"))

    # Roster size is checked against the ROSTER, not the seat rows: an unseated
    # thread has no seats yet and must still reach the prompt below rather than
    # being told it has too few players.
    roster = _pick_roster(thread, channel_id, channel_name, guild_id)
    if not roster and not seats:
        return _ephemeral("This game doesn't have enough players yet.")

    if thread.seating_set:
        error = _pick_pool_error(thread, len([s for s in seats if s.profile_id]))
        return error or JsonResponse({
            "type": RESPONSE_CHANNEL_MESSAGE,
            "data": _pick_mode_prompt_data(thread, owner),
        })

    error = _pick_pool_error(thread, len(roster) or len(seats))
    if error:
        return error

    # No seating yet: ask whether to assign a random order first. NO seats are
    # written here -- both buttons create them, so an abandoned prompt leaves
    # nothing behind. The roster and pool guards above have already run, so a
    # table too small to pick never reaches this.
    return JsonResponse({
        "type": RESPONSE_CHANNEL_MESSAGE,
        "data": _pick_unseated_prompt_data(owner),
    })


def _handle_pick_mode(payload):
    """Mode chosen: open the first turn panel. The mode buttons end in the
    invoker's snowflake, so the dispatcher has already locked them to them.

    Serves BOTH `pick_mode` and `adset_mode`: the two differ only in the board
    title, which rides in the custom_id as PICK_ADSET_FLAG (before the owner, so
    the dispatcher's owner-lock still keys on the last arg). Registering one
    function under two actions follows random_opt_* / _handle_random_option.

    This is also where a panel is BORN -- both /pick prompts and /adset's phase 2
    converge here, and the response edits this very message into the panel -- so
    it is the one place that can record pick_panel_id. A slash command cannot: an
    interaction response never reveals the id of the message it creates."""
    _action, args = decode_custom_id(payload["data"]["custom_id"])
    mode = args[0] if args else PICK_MODE_PLAYERS
    owner = args[-1] if args else ""
    header = _pick_header(args)

    thread = _pick_thread_for_channel(payload.get("channel_id"))
    if not thread:
        return _pick_retire_panel()
    seats = list(thread.seats.select_related(
        "profile", "faction", "vagabond",
    ).prefetch_related("captains"))
    if len(seats) < 2:
        return _ephemeral("Not enough players in this thread to pick factions.")

    # Best-effort: the panel is already rendered below either way, and a failed
    # write here only costs a later takeover its tidy-up of the superseded message.
    message_id = (payload.get("message") or {}).get("id")
    if message_id and thread.pick_panel_id != message_id:
        thread.pick_panel_id = message_id
        thread.save(update_fields=["pick_panel_id"])

    return JsonResponse({
        "type": RESPONSE_UPDATE_MESSAGE,
        "data": _pick_panel_data(thread, seats, mode, owner,
                                 pool=_pick_pool(thread), header=header),
    })


# ── legacy /pick seat-or-skip buttons ───────────────────────────────────────
def _handle_pick_seat(payload):
    """"Seat Players": shuffle the roster into a real seating, then ask how the
    table wants to pick. Owner-locked by the dispatcher.

    announce=False: the mode prompt and the panel behind it already list the seats
    NUMBERED (seating_set is now set), so posting a separate seating message would
    repeat what the invoker is about to see. /seating remains the way to announce
    an order to the thread."""
    _action, args = decode_custom_id(payload["data"]["custom_id"])
    owner = args[-1] if args else ""
    thread = _pick_thread_for_channel(payload.get("channel_id"))
    if not thread:
        return _pick_retire_panel("This isn't a game thread anymore.")

    seats = _pick_seat_roster(thread, payload.get("channel_id"),
                              ordered=True, announce=False)
    if len(seats) < 2:
        return _ephemeral("This game doesn't have enough players yet.")
    return JsonResponse({
        "type": RESPONSE_UPDATE_MESSAGE,
        "data": _pick_mode_prompt_data(thread, owner),
    })


def _handle_pick_noseat(payload):
    """"Skip Seating": give the thread filler seats (unshuffled, nothing posted,
    seating_set left False), then ask how to fill them. The panel lists these
    bulleted rather than numbered -- there is no order to present."""
    _action, args = decode_custom_id(payload["data"]["custom_id"])
    owner = args[-1] if args else ""
    thread = _pick_thread_for_channel(payload.get("channel_id"))
    if not thread:
        return _pick_retire_panel("This isn't a game thread anymore.")

    seats = _pick_seat_roster(thread, payload.get("channel_id"), ordered=False)
    if len(seats) < 2:
        return _ephemeral("This game doesn't have enough players yet.")
    return JsonResponse({
        "type": RESPONSE_UPDATE_MESSAGE,
        "data": _pick_mode_prompt_data(thread, owner),
    })


def _pick_free_refusal(thread, seats, clicker, payload):
    """Why this clicker may not pick in free mode, phrased for which of them they
    are: already picked, on the roster but unlinked, or not in the game.

    The unlinked case has to be told apart from the rest. Seats are built from
    roster Profiles, so an unlinked player sees their own name on the board but
    can never click it -- a flat "you can't pick" would be a dead end for the one
    person who has an action to take. Same resolution and same copy Stop already
    uses (_handle_pick_cancel), so the two agree.

    Only reached on refusal, so the extra roster lookup never touches the happy
    path."""
    if any(s.profile_id and s.profile.discord_id
           and str(s.profile.discord_id) == str(clicker) for s in seats):
        return _ephemeral("You've already chosen a faction in this game.")

    roster = _pick_roster(thread, payload.get("channel_id"))
    _me, status = _resolve_clicker(
        roster, clicker, _clicker_username(payload))
    if status == CLICKER_UNLINKED:
        return _ephemeral(
            "You're one of this game's players, but your Discord isn't linked to "
            f"your site account yet. Log in{_login_hint()} with Discord once, then "
            "try again.")
    return _ephemeral("You're not picking a faction in this game.")


def _pick_turn(payload, mode, owner, panel_id="", header=None):
    """Resolve the thread and the seat whose turn it is, and authorize the
    clicker against it. Returns (thread, seats, seat, pool) or a JsonResponse to
    return as-is.

    Shared by the faction select and every follow-up: each click is its own
    request, so the turn is re-derived and re-authorized every time rather than
    trusting the check made when the message was built. Authorizing BEFORE the
    lock keeps a rejected click from holding a row lock while its response is
    built.

    In FREE mode the question changes from "whose turn is it" to "is this clicker
    owed a faction", so the seat is resolved from the clicker instead of the
    order. That also keeps the Knaves follow-up honest: the captains it validates
    against are parked on the seat resolved here, so resolving by clicker is what
    stops one player reading another's offer."""
    thread = _pick_thread_for_channel(payload.get("channel_id"))
    if not thread:
        return _pick_retire_panel("This isn't a game thread anymore.")

    pool = _pick_pool(thread)
    seats = list(thread.seats.select_related(
        "profile", "faction", "vagabond",
    ).prefetch_related("captains"))
    clicker = _interaction_user_id(payload)

    if _pick_free(thread, mode):
        # Completeness BEFORE authorization, matching the ordered path below: the
        # last picker's double-click must land on the finished board everyone else
        # sees, not on "you've already chosen".
        if _pick_next_seat(seats) is None:
            data = _pick_panel_data(thread, seats, mode, owner, pool=pool,
                                    header=header)
            # From a free-mode ephemeral (the table finished while this prompt sat
            # open): the finished board goes to the shared panel, and the prompt is
            # dismissed rather than becoming a private copy of it.
            if panel_id:
                _pick_refresh_panel(payload, panel_id, data)
                return _pick_dismiss_ephemeral(
                    "Every faction was taken while this was open.")
            return JsonResponse({
                "type": RESPONSE_UPDATE_MESSAGE, "data": data,
            })
        seat = _pick_seat_for_clicker(seats, clicker)
        if seat is None:
            return _pick_free_refusal(thread, seats, clicker, payload)
        return thread, seats, seat, pool

    seat = _pick_next_seat(seats)
    if seat is None:
        return JsonResponse({
            "type": RESPONSE_UPDATE_MESSAGE,
            "data": _pick_panel_data(thread, seats, mode, owner, pool=pool,
                                     header=header),
        })

    if mode == PICK_MODE_ASSIGN:
        if clicker != owner:
            return _ephemeral("Only the player who ran `/pick` can assign factions.")
    elif clicker != (seat.profile.discord_id if seat.profile_id else None):
        who = seat.profile.name if seat.profile_id else "someone else"
        return _ephemeral(f"It's {who}'s turn to pick a faction.")

    return thread, seats, seat, pool


def _pick_stale_pool_response(thread, seats, mode, owner, pool, payload=None,
                              panel_id="", header=None):
    """A click landed on a faction the pool no longer contains: re-render the panel
    from the CURRENT pool instead of leaving stale options on screen.

    /draft re-run replaces the thread's picks, so an open panel keeps offering the
    old factions. Refusing the click and stopping there left no way back except
    Stop, which discards every pick the table had already made -- one re-draft
    locked the session out entirely.

    No extra filtering needed: _pick_panel_data already drops options whose slug is
    taken by a seat, and a re-draft doesn't touch LFGSeat, so factions chosen before
    it keep their seats and simply don't reappear as choices.

    Imperfect by design -- a faction committed BEFORE the re-draft can be one the
    new draft doesn't contain, and that seat keeps it. Clearing seats the table
    already agreed on would be worse than the inconsistency, and the codebase
    already tolerates this desync elsewhere (see undrafted_pick, which returns None
    rather than guessing).

    `panel_id` marks a click that came from a free-mode EPHEMERAL rather than the
    panel itself. The rebuilt board then belongs on the shared panel, and this
    response only dismisses the prompt -- painting the board into the ephemeral
    would duplicate it privately, the same trap _pick_dismiss_ephemeral exists to
    avoid."""
    data = _pick_panel_data(
        thread, seats, mode, owner, pool=pool, header=header,
        notice="The draft changed — these are the current factions.")
    if panel_id:
        _pick_refresh_panel(payload, panel_id, data)
        return _pick_dismiss_ephemeral(
            "The draft changed — pick again from the board above.")
    return JsonResponse({"type": RESPONSE_UPDATE_MESSAGE, "data": data})


def _handle_pick_faction(payload):
    """A faction was chosen: authorize the clicker, write it to the seat, and
    advance the panel in place.

    The select echoes its own values, so the chosen slug is read straight off the
    payload -- selected_values is only for recovering state on a BUTTON press."""
    _action, args = decode_custom_id(payload["data"]["custom_id"])
    mode = args[0] if args else PICK_MODE_PLAYERS
    owner = args[1] if len(args) > 1 else ""
    header = _pick_header(args)

    turn = _pick_turn(payload, mode, owner, header=header)
    if isinstance(turn, JsonResponse):
        return turn
    thread, seats, seat, pool = turn

    values = payload["data"].get("values") or []
    if not values:
        return _ephemeral("No faction selected.")
    slug = values[0]

    # Checked AFTER _pick_turn, so an unauthorized clicker gets their own refusal
    # first -- a spectator must not be able to redraw the table's panel.
    entry = next((e for e in pool if e[0] == slug), None)
    if entry is None:
        return _pick_stale_pool_response(thread, seats, mode, owner, pool,
                                         header=header)
    _slug, _title, vagabond_slug = entry

    faction = Faction.objects.filter(slug=slug).first()
    if not faction:
        return _ephemeral("That faction couldn't be found anymore.")
    vagabond = (Vagabond.objects.filter(slug=vagabond_slug).first()
                if vagabond_slug else None)

    # In free mode a follow-up is an ephemeral shown BESIDE the shared panel, so
    # the panel's id has to be carried forward to refresh it after the commit.
    # This payload came from the panel itself, so its message id IS the panel's.
    free = _pick_free(thread, mode)
    panel_id = (payload.get("message") or {}).get("id") or "" if free else ""

    # Vagabond with no draft needs an identity before the seat can be written:
    # all 12 variants share one Faction row. The draft path already carries one
    # (vagabond_slug), so it skips straight to the write.
    #
    # The faction is NOT written here -- deferring it to the follow-up is what
    # makes an abandoned prompt a no-op instead of a seat stranded with a faction
    # and no vagabond, which is the bug this exists to close.
    if faction.slug == "vagabond" and vagabond is None:
        return _pick_followup_response(
            _pick_vagabond_panel_data(thread, seats, mode, owner, faction.slug,
                                      panel_id=panel_id, pool=pool,
                                      header=header), free)

    # Knaves takes 3 of 4 ROLLED captains. The roll happens here and is parked on
    # the seat, because the bot is stateless and a custom_id can't carry a list --
    # the follow-up validates the chosen 3 against it. `platform` lives on
    # LFGDraft, so a no-draft pick has none and the Root Digital narrowing simply
    # doesn't apply.
    #
    # Skipped when fewer than PICK_CAPTAIN_CHOICES qualify: string_select clamps
    # max_values but NOT min_values, so a shorter offer would be an invalid
    # payload. Recording the faction with no captains beats blocking a player's
    # turn on a data gap they can't fix.
    if faction.slug == "knaves-of-the-deepwood":
        draft = getattr(thread, "draft", None)
        # With a draft, the rule is pick 3 of 4 ROLLED, so the offer is a random 4
        # and taking anything else would break the draft. WITHOUT a draft there is
        # nothing to be faithful to -- the table is choosing freely, so offer every
        # captain rather than an arbitrary 4 of them.
        rolled = (_random_draft_captains(draft.platform) if draft
                  else _captain_pool())
        if len(rolled) < PICK_CAPTAIN_CHOICES:
            logger.warning(
                "Knaves picked in thread %s but only %d captain-capable "
                "vagabonds qualify; recording without captains.",
                thread.thread_id, len(rolled))
        else:
            seat.captains.set(rolled)
            return _pick_followup_response(
                _pick_captains_panel_data(thread, seats, mode, owner,
                                          faction.slug, rolled,
                                          panel_id=panel_id, pool=pool,
                                          header=header), free)

    return _pick_commit(payload, thread, seat, mode, owner, pool, faction,
                        vagabond=vagabond, header=header)


def _pick_commit(payload, thread, seat, mode, owner, pool, faction,
                 vagabond=None, captains=None, discarded_captain=None,
                 panel_id="", header=None):
    """Write the seat and advance the panel. Shared by the plain faction path and
    by every follow-up, so the lock, the race checks and the roll capture have one
    implementation.

    `seat` is advisory -- everything before this is, since the row is re-resolved
    under the lock and only the write below decides. Two clicks racing the same
    seat both pass the earlier checks; the second finds it already filled here.

    No select_related on the lock query: profile and faction are both nullable, so
    it would LEFT OUTER JOIN, and Postgres rejects FOR UPDATE against the nullable
    side of an outer join (same trap as the Match lock in _finalize_proposal).
    Nothing below reads those relations -- `locked` only takes the write, and the
    panel is rebuilt from its own query afterwards.

    `panel_id` is set only when a free-mode follow-up was answered in an ephemeral:
    the response below updates that ephemeral, so the shared panel is PATCHed with
    the SAME rendered data rather than being left stale or rendered twice."""
    with transaction.atomic():
        locked = (LFGSeat.objects.select_for_update()
                  .filter(pk=seat.pk, faction__isnull=True).first())
        if locked is None:
            return _ephemeral("That seat was just picked — check the updated list.")
        if LFGSeat.objects.filter(thread=thread, faction=faction).exists():
            return _ephemeral("That faction is already taken.")
        locked.faction = faction
        locked.vagabond = vagabond
        # Assigned even when None, and NAMED in update_fields: a field left out
        # of that list is silently not written, which would strand a discarded
        # captain from an abandoned Knaves prompt on whatever faction won.
        locked.discarded_captain = discarded_captain
        locked.save(update_fields=["faction", "vagabond", "discarded_captain"])
        # `locked` is the SEAT, so that save doesn't touch the thread. Bump the
        # parent explicitly (save() supplies last_activity) so picking factions
        # keeps a thread out of the cleanup task's reach.
        thread.save(update_fields=[])

    # M2M outside the lock: .set() writes a separate join table, so it neither
    # needs the row lock nor can run before the row exists.
    #
    # Set unconditionally, INCLUDING to empty: a seat that was offered captains
    # and then took a different faction still carries the parked roll, and
    # leaving it would record captains for a faction that has none.
    locked.captains.set(captains or [])

    seats = list(thread.seats.select_related(
        "profile", "faction", "vagabond",
    ).prefetch_related("captains"))

    # Record the pick in the roll log so the record form's narrowing still offers
    # it -- lfg_option_querysets narrows factions to what the log contains, so a
    # picked faction missing from it would be silently dropped at prefill.
    #
    # Skipped on a series-linked thread: match mode narrows from the same log, so
    # a roll here would shrink that tournament match's allowed factions. Match
    # mode's prefill reads LFGSeat directly and needs no roll.
    if not thread.series_id:
        items = [{"kind": "Faction", "slug": faction.slug, "title": faction.title}]
        if vagabond:
            items.append(_lfg_item("Vagabond", vagabond))
        items.extend(_lfg_item("Captain", c) for c in (captains or []))
        _capture_lfg_components(payload.get("channel_id"), items, source="pick")

    # Board finished: the panel has no controls left, so there is nothing for a
    # later takeover to close. Dropping the id here keeps it from pointing at a
    # completed board that a restart would then "close" misleadingly.
    if _pick_next_seat(seats) is None and thread.pick_panel_id:
        thread.pick_panel_id = None
        thread.save(update_fields=["pick_panel_id"])

    data = _pick_panel_data(thread, seats, mode, owner, pool=pool, header=header)
    if panel_id:
        # Free mode: this interaction came from the player's own EPHEMERAL, so the
        # board goes to the shared panel and the prompt is merely dismissed. The
        # response edits the ephemeral, so returning the board here would leave
        # the player a private second copy of it -- see _pick_dismiss_ephemeral.
        _pick_refresh_panel(payload, panel_id, data)
        return _pick_dismiss_ephemeral("Pick recorded.")
    return JsonResponse({"type": RESPONSE_UPDATE_MESSAGE, "data": data})


def _handle_pick_vagabond(payload):
    """A Vagabond was chosen for a seat that picked the Vagabond faction. Writes
    the faction AND the vagabond together -- the faction was deliberately not
    written when the prompt was shown.

    The faction is re-validated here, not trusted from the custom_id: Stop may
    have cleared the board, or another seat may have taken it, between the two
    clicks."""
    _action, args = decode_custom_id(payload["data"]["custom_id"])
    mode = args[0] if args else PICK_MODE_PLAYERS
    owner = args[1] if len(args) > 1 else ""
    faction_slug = args[2] if len(args) > 2 else ""
    # Set only by a free-mode prompt, which was answered in an ephemeral -- the
    # shared panel it belongs to is a different message and needs refreshing.
    panel_id = _pick_panel_id(args)
    header = _pick_header(args)

    turn = _pick_turn(payload, mode, owner, panel_id=panel_id, header=header)
    if isinstance(turn, JsonResponse):
        return turn
    thread, seats, seat, pool = turn

    values = payload["data"].get("values") or []
    if not values:
        return _ephemeral("No vagabond selected.")

    if not any(e[0] == faction_slug for e in pool):
        return _pick_stale_pool_response(thread, seats, mode, owner, pool,
                                         payload=payload, panel_id=panel_id,
                                         header=header)
    faction = Faction.objects.filter(slug=faction_slug).first()
    if not faction:
        return _ephemeral("That faction couldn't be found anymore.")

    vagabond = Vagabond.objects.filter(slug=values[0]).first()
    if not vagabond:
        return _ephemeral("That vagabond couldn't be found anymore.")

    return _pick_commit(payload, thread, seat, mode, owner, pool, faction,
                        vagabond=vagabond, panel_id=panel_id, header=header)


def _handle_pick_captains(payload):
    """The 3 captains were chosen for a seat taking Knaves of the Deepwood.

    The chosen 3 are validated against the 4 parked on the seat when the prompt
    was shown -- a select echoes whatever values it was sent, so without this a
    forged payload could take captains that were never rolled."""
    _action, args = decode_custom_id(payload["data"]["custom_id"])
    mode = args[0] if args else PICK_MODE_PLAYERS
    owner = args[1] if len(args) > 1 else ""
    faction_slug = args[2] if len(args) > 2 else ""
    # Set only by a free-mode prompt, which was answered in an ephemeral -- the
    # shared panel it belongs to is a different message and needs refreshing.
    panel_id = _pick_panel_id(args)
    header = _pick_header(args)

    turn = _pick_turn(payload, mode, owner, panel_id=panel_id, header=header)
    if isinstance(turn, JsonResponse):
        return turn
    thread, seats, seat, pool = turn

    values = payload["data"].get("values") or []
    if len(values) != PICK_CAPTAIN_CHOICES:
        return _ephemeral(f"Choose exactly {PICK_CAPTAIN_CHOICES} captains.")

    if not any(e[0] == faction_slug for e in pool):
        return _pick_stale_pool_response(thread, seats, mode, owner, pool,
                                         payload=payload, panel_id=panel_id,
                                         header=header)
    faction = Faction.objects.filter(slug=faction_slug).first()
    if not faction:
        return _ephemeral("That faction couldn't be found anymore.")

    # The offer, as parked by the faction click. Stop clears it, so an empty set
    # here means the board was reset while this prompt sat open.
    offered = {c.slug: c for c in seat.captains.all()}
    if not offered:
        return _ephemeral("That pick was reset — choose a faction again.")
    if not set(values) <= set(offered):
        return _ephemeral("Those captains weren't the ones offered.")

    # The one offered but not taken. This is the only moment both sets are known:
    # the commit below replaces the parked 4 with the chosen 3. Guarded with next()
    # rather than indexed -- a short offer (fewer than 4 rolled) leaves nothing
    # discarded, and that is not an error.
    leftover = [c for s, c in offered.items() if s not in set(values)]
    discarded = leftover[0] if len(leftover) == 1 else None

    return _pick_commit(payload, thread, seat, mode, owner, pool, faction,
                        captains=[offered[v] for v in values],
                        discarded_captain=discarded, panel_id=panel_id,
                        header=header)


def _pick_actor_name(payload):
    """A display name for whoever clicked, for public /pick copy. "" when nothing
    identifies them.

    Prefers the linked Profile's name so it matches how the seat lines and the
    rest of the site render people, then falls back to the Discord username the
    interaction already carries -- a clicker with no account still has one, and
    naming them is better than an anonymous "Picking stopped".

    Returns a PLAIN name, never a <@id> mention: callers post this in public
    content without allowed_mentions, where a mention would ping."""
    discord_id = _interaction_user_id(payload)
    if discord_id:
        profile = Profile.objects.filter(discord_id=str(discord_id)).first()
        if profile:
            name = profile.display_name or profile.discord or profile.slug
            if name:
                return name
    return _clicker_username(payload) or ""


def _handle_pick_cancel(payload):
    """Stop picking and clear the session, so /pick can run again from scratch.

    Also serves Cancel on the mode and unseated prompts, which fire BEFORE any
    faction exists. Clearing is idempotent, so this doesn't branch on which
    button sent it -- but the message reports what was actually cleared, so
    cancelling a prompt doesn't claim to have discarded picks that never
    happened.

    Restricted to the table. These buttons carry PICK_OPEN so the dispatcher's
    owner-lock stays off -- any of the PLAYERS may stop a session, not just
    whoever ran /pick -- but that also let anyone in the channel discard the
    table's picks, so the roster check below is the lock's replacement.

    Same rule as ROSTER_GUARDED_COMMANDS, applied at the other entry point: that
    guard runs in the command dispatcher and never sees a button click, so this
    check has to exist separately.

    Names whoever pressed it, since it replaces the panel for everyone."""
    channel_id = payload.get("channel_id")
    thread = _pick_thread_for_channel(channel_id)
    if not thread:
        return _pick_retire_panel("This isn't a game thread anymore.")

    # The roster resolves by thread id alone: a button payload carries no channel
    # name, and /pick already linked a group thread to its player group before any
    # of these buttons existed.
    roster = _pick_roster(thread, channel_id)
    _me, status = _resolve_clicker(
        roster, _interaction_user_id(payload), _clicker_username(payload))
    if status == CLICKER_UNLINKED:
        return _ephemeral(
            "You're one of this game's players, but your Discord isn't linked to "
            f"your site account yet. Log in{_login_hint()} with Discord once, then "
            "try again.")
    if status != CLICKER_MATCHED:
        return _ephemeral(
            "Only the players in this game can stop the faction picks.")

    cleared = _pick_clear(thread)

    # Who stopped it: this replaces the panel for the WHOLE table, and any of the
    # players can press Stop -- not just whoever ran /pick -- so without a name
    # the table can't tell who discarded their picks.
    #
    # A plain display name, NOT a <@id> mention: this response sets no
    # allowed_mentions, so a mention would ping the person who just clicked --
    # the same reason /schedule's announcement names the scheduler in plain text.
    who = _pick_actor_name(payload)
    stopped = f"Picking stopped by {who}" if who else "Picking stopped"
    content = (f"{stopped} and factions cleared. Run `/pick` to start over."
               if cleared else f"{stopped}.")
    return JsonResponse({
        "type": RESPONSE_UPDATE_MESSAGE,
        "data": {"content": content, "components": [],
                 "allowed_mentions": {"parse": []}},
    })


# ── /adset ─────────────────────────────────────────────────────────────────
# /seating + /draft + /pick as ONE message, edited in place through every phase.
# Nothing here re-implements those commands: the writes (_persist_seating,
# _pick_seat_roster, _draft_build_result, _pick_commit) and the board renderer
# (_pick_panel_data) are the same functions, reached from a different front end.
#
# Phase 0 join gate -> 1 bans -> 2 mode -> 3 picking (handed to /pick's engine).

# Phase 0's roster lives in an embed field, exactly as /lfg's does, and is written
# to thread.players only on Start. Join is a TOGGLE, so per-click writes would
# delete-and-reinsert the M2M on every leave and strand a half-built roster on an
# abandoned setup.
ADSET_PLAYERS_FIELD = "Players"


def _adset_thread(payload_or_data, key="_channel_id"):
    """The LFGThread for this interaction, or None outside a game/group thread."""
    return _pick_thread_for_channel(payload_or_data.get(key))


def _role_valid_in(role, parent_id):
    """Whether an LFG tag can legitimately apply to a thread under `parent_id`.

    A tag with a forum_channel_id belongs to ONE forum, so offering it in a thread
    somewhere else would attach a tag whose whole purpose is a home this thread
    doesn't have. A tag without one is unbound and always valid.

    Fails CLOSED when parent_id is unknown, unlike /lfg's equivalent check, which
    fails open. /lfg is deciding whether to block a game the user asked for, so a
    wrong guess costs them the game; here the only cost is a tag not being
    offered, and wrongly attaching a forum-bound tag is the worse outcome."""
    if not role.forum_channel_id:
        return True
    if not parent_id:
        return False
    return str(parent_id) == str(role.forum_channel_id)


def _adset_roles(guild_id, parent_id, thread):
    """The LFG tags worth offering in phase 0: none when the thread already has a
    role (it keeps the one it has), else the guild's tags filtered to those valid
    in this channel.

    Returned as a list so the caller can test emptiness and skip the select
    entirely -- Discord rejects a zero-option select."""
    if thread is not None and thread.lfg_role_id:
        return []
    guild = DiscordGuild.objects.filter(guild_id=guild_id).first() if guild_id else None
    if not guild:
        return []
    return [r for r in guild.lfg_roles.all() if _role_valid_in(r, parent_id)]


def _adset_cancel_data(text="Adset draft cancelled."):
    """Retire the /adset message: a short notice, no controls, no embed."""
    return {"content": text, "embeds": [], "components": []}


def _adset_join_data(owner, players=None, roles=None, role_pk=None):
    """Phase 0: the join gate.

    The roster rides in an embed FIELD (like /lfg) rather than in the content,
    because the content is reserved for the board in later phases and an embed
    field parses back reliably via _lfg_player_lines.

    `roles` is the ALREADY-FILTERED tag list (see _adset_roles); this builder makes
    no channel decisions. The select is omitted entirely when it's empty, and is
    optional (min_values=0) even with a single tag -- unlike /lfg's SINGLE variant,
    which silently uses the sole tag with no way to decline."""
    players = players or []
    value = "\n".join(_lfg_player_line(p["name"], p["id"]) for p in players) or "—"
    embed = {
        "title": ADSET_TITLE.strip("*"),
        "fields": [{"name": ADSET_PLAYERS_FIELD, "value": value, "inline": False}],
    }
    rows = []
    if roles:
        rows.append(action_row(string_select(
            encode_custom_id("adset_role", owner),
            [select_option(r.name, str(r.pk), default=(str(r.pk) == str(role_pk)))
             for r in roles[:25]],
            placeholder="LFG tag (optional)", min_values=0, max_values=1)))
    # Join is open to anyone (trailing "g" keeps the dispatcher's owner-lock off);
    # Start and Cancel end in the host's snowflake, so they are locked to them.
    rows.append(action_row(
        button("Join", encode_custom_id("adset_join", owner, "g"), style=STYLE_PRIMARY),
        button("Start", encode_custom_id("adset_start", owner), style=STYLE_SUCCESS,
               emoji={"name": "✔"}),
        button("Cancel", encode_custom_id("adset_cancel", owner), style=STYLE_SECONDARY,
               emoji={"name": "✖"}),
    ))
    return {"content": "", "embeds": [embed], "components": rows,
            "allowed_mentions": {"parse": []}}


def _adset_ban_data(thread, seats, factions, banned_slugs, owner, has_draft=False,
                    preseated=False):
    """Phase 1: the seat list, a faction-ban select, and Build.

    Mirrors _draft_ui_data's custom_id layout (players, platform, has_draft, owner)
    so _parse_draft_state reads them unchanged; platform is always TTS here.

    `preseated` is whether the seating EXISTED BEFORE this /adset run, which is not
    the same as thread.seating_set: /adset seats the table itself on entry, so by
    the time this renders the flag is always True. Only a pre-existing order is
    worth remarking on -- announcing "already has a seating order" about one we
    just created a moment ago would be nonsense. Reseat, by contrast, keys on
    seating_set: it is offered whenever there is an order to replace."""
    players = len([s for s in seats if s.profile_id])
    options = [
        select_option(title, slug, emoji=faction_emoji_object(slug),
                      default=slug in banned_slugs)
        for slug, title, _type in factions
    ]
    lines = [ADSET_TITLE, ""]
    lines += [f"{s.seat_number}. "
              f"{s.profile.name if s.profile_id else '(removed player)'}"
              for s in sorted(seats, key=lambda s: s.seat_number)]
    if preseated:
        lines.append("")
        lines.append("-# This game already had a seating order — Reseat to reshuffle.")
    if has_draft:
        lines.append("-# This thread already has a draft — building **replaces** it.")

    row = [button("Build Draft",
                  encode_custom_id("adset_build", players, "tts",
                                   "d" if has_draft else "n", owner),
                  style=STYLE_SUCCESS)]
    if thread.seating_set:
        row.append(button("Reseat", encode_custom_id("adset_reseat", owner),
                          style=STYLE_DANGER))
    if has_draft:
        row.append(button("Clear Draft", encode_custom_id("adset_clear", owner),
                          style=STYLE_DANGER))
    row.append(button("Cancel", encode_custom_id("adset_cancel", owner),
                      style=STYLE_SECONDARY))

    select = string_select(
        encode_custom_id("adset_select", players, "tts",
                         "d" if has_draft else "n", owner),
        options, placeholder="Select factions to ban (optional)",
        min_values=0, max_values=len(options))
    return {"content": "\n".join(lines), "embeds": [],
            "components": [action_row(select), action_row(*row)],
            "allowed_mentions": {"parse": []}}


def _adset_mode_data(thread, seats, pool, owner, force_row=False):
    """Phase 2: the drafted factions, the seats, and how to fill them.

    The board comes from _pick_seat_lines so the Factions row and seat rendering
    are identical to the panel that follows -- this message becomes that panel."""
    lines = _pick_seat_lines(thread, seats, pool=pool, header=ADSET_TITLE,
                             force_row=force_row)
    turn = "in seat order" if thread.seating_set else "in turn"
    lines += ["", f"Assign every faction yourself, or let each player pick {turn}?"]
    reminder = _pick_setup_reminder(thread)
    if reminder:
        lines.append(reminder.lstrip("\n"))
    return {
        "content": "\n".join(lines), "embeds": [],
        "components": [action_row(
            # PICK_ADSET_FLAG sits BEFORE the owner so the dispatcher's owner-lock
            # still keys on the last arg; _pick_header reads it back downstream.
            button("Players pick",
                   encode_custom_id("adset_mode", PICK_MODE_PLAYERS,
                                    PICK_ADSET_FLAG, owner),
                   style=STYLE_SUCCESS),
            button("Assign all",
                   encode_custom_id("adset_mode", PICK_MODE_ASSIGN,
                                    PICK_ADSET_FLAG, owner),
                   style=STYLE_PRIMARY),
            button("Redraft", encode_custom_id("adset_redraft", owner),
                   style=STYLE_DANGER),
            button("Cancel", encode_custom_id("adset_cancel", owner),
                   style=STYLE_SECONDARY),
        )],
        "allowed_mentions": {"parse": []},
    }


def _adset_takeover_data(thread, seats, owner, header=ADSET_TITLE):
    """The confirmation shown when picks are already underway.

    Renders the live board so the clicker sees exactly which picks Continue would
    discard -- they are being asked to throw away named work, not an abstraction.

    Both buttons end in PICK_OPEN rather than a snowflake, so the dispatcher's
    owner-lock stays off and the handlers apply _handle_pick_cancel's roster gate
    instead: a pick session can outlive whoever started it, and any player at the
    table may restart it."""
    lines = _pick_seat_lines(thread, seats, header=header)
    lines += ["", "Continuing will clear these picks and start over. "
                  "The seating is kept."]
    return {
        "content": "\n".join(lines), "embeds": [],
        "components": [action_row(
            button("Continue",
                   encode_custom_id("adset_takeover", owner,
                                    PICK_ADSET_FLAG if header else "p",
                                    PICK_OPEN),
                   style=STYLE_DANGER),
            button("Cancel", encode_custom_id("adset_cancel", owner, PICK_OPEN),
                   style=STYLE_SECONDARY),
        )],
        "allowed_mentions": {"parse": []},
    }


def _adset_entry(thread, data, owner):
    """Response DATA for the first /adset phase whose work isn't already done, or
    an (error_response, None) pair when the flow can't start.

    Returns (data, error) -- never a JsonResponse -- because the caller decides the
    response TYPE: /adset itself posts (type 4) while every button that re-enters
    here edits in place (type 7). Baking in type 4 would make each of those post a
    SECOND message and break the single-message premise outright.

    Deliberately does NOT check _pick_in_progress: its callers do. Putting that
    check here would mean _handle_adset_takeover -- which calls _pick_clear and
    then this -- depends on its own write already being visible to avoid being
    handed back the prompt it just answered.

    Seating is created only when absent: reshuffling an order the table already
    agreed on is exactly what _offer_lfg_seating refuses to do after a draft."""
    channel_id = data.get("_channel_id")
    if not thread.players.exists() and not thread.series_id:
        roles = _adset_roles(data.get("_guild_id"),
                             data.get("_channel_parent_id"), thread)
        host = {"id": owner, "name": _author_display_from_data(data)}
        return _adset_join_data(owner, players=[host], roles=roles), None

    # Captured BEFORE seating: /adset seats the table itself when there is no
    # order yet, so afterwards seating_set is always True and can no longer tell
    # "the table already had an order" from "we just made one".
    preseated = thread.seating_set
    seats = list(thread.seats.select_related("profile", "faction", "vagabond")
                 .prefetch_related("captains"))
    if not thread.seating_set:
        seats = _pick_seat_roster(thread, channel_id, ordered=True, announce=False)
    if len([s for s in seats if s.profile_id]) < 2:
        return None, _ephemeral("This game doesn't have enough players yet.")

    if getattr(thread, "draft", None) is not None:
        return _adset_mode_data(thread, seats, _pick_pool(thread), owner), None

    players = len([s for s in seats if s.profile_id])
    factions = _draft_eligible_factions(DRAFT_PLATFORM_TTS, players)
    if len(factions) < players + 1:
        return None, _ephemeral(
            f"Only {len(factions)} eligible factions; need {players + 1} for a "
            f"{players}-player draft.")
    return _adset_ban_data(thread, seats, factions, set(), owner,
                           preseated=preseated), None


def _handle_adset_command(data):
    """/adset: seat the players and draft factions, all in one message."""
    thread = _adset_thread(data)
    if not thread:
        return _ephemeral(
            "Use this in a game thread or a player group's thread to set up a game.")

    owner = data.get("_author_id")
    seats = list(thread.seats.select_related("profile", "faction", "vagabond")
                 .prefetch_related("captains"))
    if _pick_in_progress(thread):
        return JsonResponse({
            "type": RESPONSE_CHANNEL_MESSAGE,
            "data": _adset_takeover_data(thread, seats, owner),
        })

    payload_data, error = _adset_entry(thread, data, owner)
    if error:
        return error
    return JsonResponse({"type": RESPONSE_CHANNEL_MESSAGE, "data": payload_data})


def _adset_component_data(payload):
    """The `data`-shaped dict a component handler needs to re-enter _adset_entry.

    The dispatcher only stashes _channel_* for SLASH commands, so a button payload
    has to assemble the same keys itself."""
    channel = payload.get("channel") or {}
    return {
        "_channel_id": payload.get("channel_id"),
        "_channel_parent_id": channel.get("parent_id"),
        "_guild_id": payload.get("guild_id"),
        "_author": _interaction_author(payload),
    }


def _adset_resume(payload, thread, owner):
    """Re-enter the flow from a button: same phases, but editing in place."""
    payload_data, error = _adset_entry(thread, _adset_component_data(payload), owner)
    if error:
        return error
    return JsonResponse({"type": RESPONSE_UPDATE_MESSAGE, "data": payload_data})


def _handle_adset_cancel(payload):
    """Cancel: retire the message without touching anything already written.

    Serves phases 0-2. Reached with two custom_id shapes -- `adset_cancel:{owner}`
    on the setup phases (owner-locked by the dispatcher) and
    `adset_cancel:{owner}:g` on the takeover prompt (open, matching the Continue
    button beside it) -- so nothing here reads the owner positionally.

    NOT an undo: seating written on entry and a draft built in phase 1 both
    survive, the same way _draft_clear and _pick_clear each preserve what they
    don't own. Clear Draft and Reseat are the deliberate ways to undo those."""
    return JsonResponse({"type": RESPONSE_UPDATE_MESSAGE, "data": _adset_cancel_data()})


def _handle_adset_join(payload):
    """Join (anyone, toggle): add or remove the clicker from the pending roster.

    Mirrors _handle_lfg_join exactly, including its 1024-char field cap and the
    host-can't-leave rule, and writes NOTHING to thread.players -- the roster is
    committed only by Start."""
    try:
        embed = payload["message"]["embeds"][0]
        clicker = _interaction_user_id(payload)
        field = _lfg_field(embed, ADSET_PLAYERS_FIELD)
        if field is None:
            return _ephemeral("Couldn't update the game, try again.")

        _action, args = decode_custom_id(payload["data"].get("custom_id", ""))
        owner = args[0] if args else None

        players = _lfg_player_lines(embed)
        if clicker in {p["id"] for p in players}:
            if clicker == owner:
                return _ephemeral(
                    "You're hosting this game and can't leave it, but you can "
                    "cancel it with ✖.")
            players = [p for p in players if p["id"] != clicker]
        else:
            display = _lfg_member_display_name(payload)
            candidate = players + [{"id": clicker, "name": display}]
            value = "\n".join(_lfg_player_line(p["name"], p["id"]) for p in candidate)
            # Discord caps an embed field at 1024 chars; refuse rather than send an
            # edit it would reject, which would drop the whole update.
            if len(value) > 1024:
                return _ephemeral("This game already has too many players to add you.")
            players = candidate
            user = (payload.get("member") or {}).get("user") or {}
            ensure_profile_from_discord_task.delay(
                clicker, user.get("username"), display)

        # Re-emit the tag select WITH its current selection: this rebuilds the whole
        # message, so a dropped default=True would silently discard the host's
        # choice (the failure _handle_draft_select documents for bans).
        thread = _adset_thread(payload, "channel_id")
        roles = _adset_roles(payload.get("guild_id"),
                             (payload.get("channel") or {}).get("parent_id"), thread)
        return JsonResponse({
            "type": RESPONSE_UPDATE_MESSAGE,
            "data": _adset_join_data(
                owner, players=players, roles=roles,
                role_pk=(selected_values(payload, "adset_role") or [None])[0]),
        })
    except (KeyError, IndexError, TypeError):
        logger.exception("Error handling adset_join")
        return _ephemeral("Couldn't update the game, try again.")


def _handle_adset_role(payload):
    """Tag select changed: re-render phase 0 with the choice marked default=True.

    Writes nothing -- like the roster, the choice lives in the message until Start.
    Owner-locked by its trailing snowflake."""
    _action, args = decode_custom_id(payload["data"]["custom_id"])
    owner = args[-1] if args else ""
    try:
        embed = payload["message"]["embeds"][0]
        players = _lfg_player_lines(embed)
    except (KeyError, IndexError, TypeError):
        players = []
    thread = _adset_thread(payload, "channel_id")
    roles = _adset_roles(payload.get("guild_id"),
                         (payload.get("channel") or {}).get("parent_id"), thread)
    chosen = (payload["data"].get("values") or [None])[0]
    return JsonResponse({
        "type": RESPONSE_UPDATE_MESSAGE,
        "data": _adset_join_data(owner, players=players, roles=roles, role_pk=chosen),
    })


def _handle_adset_start(payload):
    """Start (host only): commit the pending roster, then open phase 1.

    The roster is resolved to Profiles and written HERE rather than in the task,
    because the very next message lists the seats and must be rendered
    synchronously -- deferring would leave Start clickable and let a second press
    enqueue a duplicate.

    create_lfg_thread_task still runs, for the parts only it does: adopting the
    thread, recording the host, and applying a forum tag. It is passed
    kickoff=False (everyone is already here and just clicked Join) and role_pk
    rather than role_id (a display-only tag has no snowflake)."""
    _action, args = decode_custom_id(payload["data"]["custom_id"])
    owner = args[-1] if args else ""
    channel_id = payload.get("channel_id")

    try:
        embed = payload["message"]["embeds"][0]
        players = _lfg_player_lines(embed)
    except (KeyError, IndexError, TypeError):
        logger.exception("Error reading the adset roster")
        return _pick_retire_panel("Couldn't read this game's players — run `/adset` again.")

    if len(players) < 2:
        # Before any mutation, so the message stays joinable.
        return _ephemeral(
            "You can't start with only one player — wait for someone to press Join.")

    thread = _adset_thread(payload, "channel_id")
    if not thread:
        thread, _created = LFGThread.objects.get_or_create(thread_id=channel_id)

    # Re-validate the tag server-side: a select echoes whatever it is sent, so the
    # filter that built it is a UI affordance, not a boundary. Any failure degrades
    # to no tag rather than erroring, matching /lfg's plain_post() fallback.
    role_pk = (selected_values(payload, "adset_role") or [None])[0]
    role = None
    if role_pk:
        guild = DiscordGuild.objects.filter(
            guild_id=payload.get("guild_id")).first()
        role = GuildLFGRole.objects.filter(pk=role_pk, guild=guild).first() if guild else None
        if role and not _role_valid_in(
                role, (payload.get("channel") or {}).get("parent_id")):
            role = None

    profiles = [ensure_profile_from_discord(p["id"], None, p.get("name"))
                for p in players]
    resolved = [p for p in profiles if p]
    if len(resolved) < 2:
        logger.error("adset start in thread %s resolved %d/%d profiles",
                     channel_id, len(resolved), len(players))
        return _ephemeral("Couldn't look up these players — try again in a moment.")
    thread.players.set(resolved)
    if role and not thread.lfg_role_id:
        thread.lfg_role = role
        thread.save(update_fields=["lfg_role"])

    try:
        create_lfg_thread_task.delay(
            channel_id, (payload.get("message") or {}).get("id"),
            payload.get("guild_id"), None, thread.description or "", players,
            host_id=owner, in_thread=True, send_kickoff=False,
            role_pk=str(role.pk) if role else None,
        )
    except TypeError:
        # A signature mismatch is OUR bug, not a broker outage, and swallowing it
        # would mean adoption silently never runs while the flow still looks fine.
        raise
    except Exception:
        # The roster is already written and the flow can continue; only the host
        # attribution and forum tag are lost, so a broker outage must not cost the
        # user the game they just started.
        logger.exception("Could not enqueue adset thread adoption for %s", channel_id)

    return _adset_resume(payload, thread, owner)


def _handle_adset_select(payload):
    """Ban select changed: re-render phase 1 with the bans marked default=True."""
    custom_id = payload["data"]["custom_id"]
    _action, args = decode_custom_id(custom_id)
    owner = args[-1] if args else ""
    has_draft = len(args) >= 3 and args[2] == "d"
    thread = _adset_thread(payload, "channel_id")
    if not thread:
        return _pick_retire_panel("This isn't a game thread anymore.")
    seats = list(thread.seats.select_related("profile"))
    players = max(2, min(6, len([s for s in seats if s.profile_id]) or 4))
    factions = _draft_eligible_factions(DRAFT_PLATFORM_TTS, players)
    return JsonResponse({
        "type": RESPONSE_UPDATE_MESSAGE,
        "data": _adset_ban_data(
            thread, seats, factions,
            set(payload["data"].get("values", [])),  # this select echoes its values
            owner, has_draft=has_draft),
    })


def _handle_adset_build(payload):
    """Build: draw the draft, record it, and edit into phase 2.

    Shares _draft_build_result with /draft -- the draw, the vagabond/captains
    rolls and the Celery-safe payload are identical. Only the presentation
    differs: /draft renders an embed and offers seating, this edits into the mode
    prompt. No followup is sent; that would break the single-message flow."""
    _action, players, platform = _parse_draft_state(payload["data"]["custom_id"])
    _action_id, args = decode_custom_id(payload["data"]["custom_id"])
    owner = args[-1] if args else ""

    thread = _adset_thread(payload, "channel_id")
    if not thread:
        return _pick_retire_panel("This isn't a game thread anymore.")

    banned_slugs = set(selected_values(payload, "adset_select"))
    factions = _draft_eligible_factions(platform, players)
    # `_captains` is unused here: unlike /draft, which names them in its result
    # embed, this phase shows only the faction row -- the captains surface later,
    # on the seat that takes Knaves.
    drawn, vagabond, _captains, items, draft_payload, error = _draft_build_result(
        factions, banned_slugs, players, platform, owner)
    if error:
        return JsonResponse({
            "type": RESPONSE_UPDATE_MESSAGE,
            "data": {"content": error, "embeds": [], "components": []},
        })

    _capture_lfg_components(payload.get("channel_id"), items,
                            source="draft", draft=draft_payload)

    # The draft is recorded through Celery, so thread.draft isn't visible yet.
    # Render phase 2 from the pool we just drew rather than re-reading it.
    titles = {slug: title for slug, title, _t in factions}
    pool = [(slug,
             titles.get(slug, slug),
             vagabond.slug if (slug == "vagabond" and vagabond) else None)
            for slug in drawn]
    seats = list(thread.seats.select_related("profile", "faction", "vagabond")
                 .prefetch_related("captains"))
    # force_row: the draft was just handed to Celery, so thread.draft isn't
    # visible yet and the row would otherwise be blank on the very message that
    # announces the draw.
    data = _adset_mode_data(thread, seats, pool, owner, force_row=True)
    return JsonResponse({"type": RESPONSE_UPDATE_MESSAGE, "data": data})


def _handle_adset_reseat(payload):
    """Reseat: shuffle the seating again, then re-render phase 1.

    Refused once any faction is committed -- reshuffling seats that hold factions
    would scramble a table mid-pick."""
    _action, args = decode_custom_id(payload["data"]["custom_id"])
    owner = args[-1] if args else ""
    thread = _adset_thread(payload, "channel_id")
    if not thread:
        return _pick_retire_panel("This isn't a game thread anymore.")
    if _pick_in_progress(thread):
        return _ephemeral(
            "Factions have already been picked — press Stop on the pick panel "
            "before reseating.")
    profiles = _pick_roster(thread, payload.get("channel_id"))
    if len(profiles) < 2:
        return _ephemeral("This game doesn't have enough players yet.")
    _persist_seating(thread, profiles)
    return _adset_resume(payload, thread, owner)


def _handle_adset_redraft(payload):
    """Redraft: back to the ban UI, with the existing draft still in place.

    Writes nothing -- the replacement happens on Build, so backing out here costs
    the table nothing."""
    _action, args = decode_custom_id(payload["data"]["custom_id"])
    owner = args[-1] if args else ""
    thread = _adset_thread(payload, "channel_id")
    if not thread:
        return _pick_retire_panel("This isn't a game thread anymore.")
    seats = list(thread.seats.select_related("profile"))
    players = max(2, min(6, len([s for s in seats if s.profile_id]) or 4))
    factions = _draft_eligible_factions(DRAFT_PLATFORM_TTS, players)
    return JsonResponse({
        "type": RESPONSE_UPDATE_MESSAGE,
        "data": _adset_ban_data(thread, seats, factions, set(), owner,
                                has_draft=True),
    })


def _handle_adset_clear(payload):
    """Clear Draft: drop the draft without building a new one, then re-render."""
    _action, args = decode_custom_id(payload["data"]["custom_id"])
    owner = args[-1] if args else ""
    thread = _adset_thread(payload, "channel_id")
    if not thread:
        return _pick_retire_panel("This isn't a game thread anymore.")
    _draft_clear(thread)
    return _adset_resume(payload, thread, owner)


def _handle_adset_takeover(payload):
    """Continue: clear the in-progress picks and restart the flow.

    Authorized against the ROSTER, not the host: this button carries PICK_OPEN so
    the dispatcher's owner-lock is off, and _handle_pick_cancel already
    establishes that any player may end a session (it can outlive whoever started
    it). Same gate, same three outcomes, same copy."""
    channel_id = payload.get("channel_id")
    thread = _adset_thread(payload, "channel_id")
    if not thread:
        return _pick_retire_panel("This isn't a game thread anymore.")

    roster = _pick_roster(thread, channel_id)
    _me, status = _resolve_clicker(
        roster, _interaction_user_id(payload), _clicker_username(payload))
    if status == CLICKER_UNLINKED:
        return _ephemeral(
            "You're one of this game's players, but your Discord isn't linked to "
            f"your site account yet. Log in{_login_hint()} with Discord once, then "
            "try again.")
    if status != CLICKER_MATCHED:
        return _ephemeral("Only the players in this game can restart the picks.")

    _action, args = decode_custom_id(payload["data"]["custom_id"])
    owner = args[0] if args else ""
    # args[1] records which command opened the prompt, so Continue returns to that
    # flow rather than always landing in /adset's phases.
    from_adset = len(args) > 1 and args[1] == PICK_ADSET_FLAG
    actor = _pick_actor_name(payload)
    # Read the panel id BEFORE clearing: _pick_clear nulls it (so a stale id can't
    # outlive its session), which would leave nothing to close here.
    panel_id = thread.pick_panel_id
    _pick_clear(thread)
    # Skip the message we're about to edit: the panel is often THIS message, and
    # PATCHing it while the interaction response also edits it is two writes
    # racing on one message with no defined winner.
    _pick_close_panel(thread, channel_id, actor, panel_id=panel_id,
                      skip_id=(payload.get("message") or {}).get("id"))
    if from_adset:
        return _adset_resume(payload, thread, owner)
    # /pick's own restart: back to its mode prompt, not into /adset's phases. The
    # seating and any draft survive _pick_clear, so this is exactly where a fresh
    # /pick would land anyway.
    return JsonResponse({
        "type": RESPONSE_UPDATE_MESSAGE,
        "data": _pick_mode_prompt_data(thread, owner),
    })


# ── /rename ────────────────────────────────────────────────────────────────
def _rename_wait_text(retry_after):
    """"about 8 minutes" / "about 30 seconds" for a rate-limit retry_after."""
    seconds = int(math.ceil(retry_after))
    if seconds < 60:
        return f"about {seconds} second{'s' if seconds != 1 else ''}"
    minutes = int(math.ceil(seconds / 60))
    return f"about {minutes} minute{'s' if minutes != 1 else ''}"


def _handle_rename_command(data):
    """/rename: retitle this game's thread. Host only, ephemeral either way.

    Also writes the thread's `nickname`, so the recorded Game inherits the name
    the players actually used."""
    title = (_get_option(data, "title") or "").strip()
    if not title:
        return _ephemeral("Give the thread a title.")

    channel_id = data.get("_channel_id")
    thread = _lfg_thread_for_channel(channel_id)
    if not thread:
        channel_type = data.get("_channel_type")
        if channel_type is not None and channel_type not in _THREAD_CHANNEL_TYPES:
            return _ephemeral("Run this inside your game's thread to rename it.")
        return _ephemeral("This isn't a game thread I know about.")

    # A tournament group thread spans a whole series and has no host, so it isn't
    # any one player's to retitle. Same series_id guard /seating uses.
    if thread.series_id:
        return _ephemeral("This is a tournament group thread, so I can't rename it.")

    if not thread.host_id:
        return _ephemeral(
            "I don't know who started this game, so I can't tell who may rename it.")

    profile = Profile.objects.filter(discord_id=str(data.get("_author_id"))).first()
    if not profile or profile.pk != thread.host_id:
        return _ephemeral("Only the player who started this game can rename its thread.")

    result, retry_after = rename_channel(channel_id, title)
    if result != THREAD_OK:
        if retry_after is not None:
            return _ephemeral(
                "Discord is limiting renames on this thread — try again in "
                f"{_rename_wait_text(retry_after)}.")
        if result == THREAD_BLOCKED:
            return _ephemeral("I don't have permission to rename this thread.")
        return _ephemeral("Couldn't rename the thread — try again in a moment.")

    # Only after Discord confirms: the model must never claim a name the thread
    # doesn't have. Truncated to nickname's max_length -- production is Postgres,
    # which raises on overflow rather than truncating.
    thread.nickname = title[:50]
    thread.save(update_fields=["nickname"])
    return _ephemeral(f"Renamed this thread to **{title[:100]}**.")


# ── /boxscore ──────────────────────────────────────────────────────────────
# Seeds a thread from an uploaded game JSON so /record opens a pre-filled form.
#
# The file is DECOMPOSED rather than stored whole: map/deck go through the roll
# capture (the record form narrows its dropdowns from the roll log, so a map set
# without a roll would be dropped from its own options), players become seats,
# and only the box score / dominance / brazen demagogue -- the three things an
# LFGThread has no other field for -- land in turns_data.

# Two orders of magnitude above a real box score (a maximal 6-seat, 12-turn game
# is ~10KB), so this only ever catches a wrong file.
_BOXSCORE_MAX_BYTES = 256 * 1024
# Tighter than the timeout=5 used elsewhere in discordservice: this download runs
# inside Discord's 3-second interaction budget, and there is no deferred-response
# path in this codebase to fall back on.
_BOXSCORE_TIMEOUT = 2

# Effort.DominanceChoices values, resolved once rather than per participant.
_BOXSCORE_DOMINANCE_VALUES = frozenset(c.value for c in Effort.DominanceChoices)


def _boxscore_size_text(size):
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{max(1, round(size / 1024))} KB"


def _handle_boxscore_command(data):
    """/boxscore: attach a game JSON to this thread's game.

    Writes three places, none of which overwrite work the thread already has a
    better source for: the roll log + map/deck, the seating (ONLY when there
    isn't one -- a reseat would cascade away /pick's factions), and turns_data.
    """
    from the_warroom.services.box_score_import import (
        BoxScoreImportError, normalize_turns, parse_box_score_json,
        validate_participants,
    )

    channel_id = data.get("_channel_id")
    thread = _pick_thread_for_channel(
        channel_id, data.get("_channel_name"), data.get("_guild_id"))
    if not thread:
        channel_type = data.get("_channel_type")
        if channel_type is not None and channel_type not in _THREAD_CHANNEL_TYPES:
            return _ephemeral("Run this inside your game's thread to add a box score.")
        return _ephemeral("This isn't a game thread I know about.")

    attachment = _get_attachment(data, "file")
    if not attachment:
        return _ephemeral("Attach a JSON file with your box score.")

    # The FILENAME decides, not content_type: Discord sets the latter by sniffing,
    # so accepting either would let a mislabeled .png through on a JSON-ish sniff.
    filename = attachment.get("filename") or "your file"
    if not filename.lower().endswith(".json"):
        return _ephemeral(f"That needs to be a `.json` file — got `{filename}`.")

    size = attachment.get("size") or 0
    if size > _BOXSCORE_MAX_BYTES:
        return _ephemeral(
            f"That file is too big to be a box score ({_boxscore_size_text(size)}).")

    url = attachment.get("url")
    if not url:
        return _ephemeral("Discord didn't give me a link to that file — try again.")
    try:
        response = requests.get(url, timeout=_BOXSCORE_TIMEOUT)
        response.raise_for_status()
        raw = response.content
    except requests.RequestException:
        # Distinct from the dispatcher's generic catch-all, which would otherwise
        # tell the user "Something went wrong" for a plain network blip.
        logger.warning("boxscore download failed for thread %s", channel_id)
        return _ephemeral("Couldn't download that file — try again.")

    try:
        payload = parse_box_score_json(raw)
    except BoxScoreImportError as exc:
        return _ephemeral(f"That box score couldn't be read: {exc}")

    participants = payload.get("participants") or []
    if not participants:
        return _ephemeral("That file has no participants.")

    notes = []
    collapsed_seats = []

    # ── Trim to what only turns_data can hold, normalizing turns on the way in
    # so the stored shape is canonical however the file was written.
    entries = []
    for index, participant in enumerate(participants):
        seat_no = participant.get("turn_order", participant.get("seat")) or index + 1
        label = f"Seat {seat_no}"
        entry = {"turn_order": seat_no}

        dominance = participant.get("dominance")
        if dominance:
            if dominance in _BOXSCORE_DOMINANCE_VALUES:
                entry["dominance"] = dominance
            else:
                notes.append(f"{label}: ignored an unknown dominance “{dominance}”.")
                dominance = None
        if participant.get("brazen_demagogue"):
            if entry.get("dominance"):
                entry["brazen_demagogue"] = True
            else:
                notes.append(f"{label}: Brazen Demagogue needs a dominance, so I left it off.")

        try:
            cells, turn_notes = normalize_turns(participant.get("turns"), label=label)
        except BoxScoreImportError as exc:
            return _ephemeral(f"That box score couldn't be read: {exc}")
        if cells:
            entry["turns"] = [
                {"turn": c["turn"], "score": c["value"],
                 **({"dominance": True} if c["dominance"] else {})}
                for c in cells
            ]
        # normalize_turns' own notes are phrased for the record form ("enter it
        # from the Game detail page"), which reads as a non-sequitur in Discord.
        # Say the same thing in terms of what happened to the upload.
        if turn_notes:
            collapsed_seats.append(label)

        if len(entry) > 1:
            entries.append(entry)

    if collapsed_seats:
        notes.append(
            "Kept turn totals only for " + ", ".join(collapsed_seats)
            + " — per-category points can be added on the game's page.")

    # ── Map / deck, resolved before writing so an unknown slug is reported.
    # Titles are kept for the reply: the capture below is a Celery enqueue, so
    # thread.map/.deck are NOT set yet by the time this message is built.
    items = []
    component_titles = []
    for key, kind, model in (("board_map", "Map", Map), ("deck", "Deck", Deck)):
        slug = payload.get(key)
        if not slug:
            continue
        obj = model.objects.filter(slug=slug).first()
        if obj:
            items.append({"kind": kind, "slug": slug})
            component_titles.append(obj.title)
        else:
            notes.append(f"I didn't recognise the {kind.lower()} `{slug}`.")

    if not entries and not items:
        return _ephemeral("There was nothing in that file I can use.")

    # ── Seating: only when there isn't one. _persist_seating deletes every seat,
    # and that cascade takes /pick's factions and captains with it.
    seating_line = None
    already_seated = thread.seating_set or thread.seats.exists()
    if already_seated:
        seating_line = f"Seating: left as it was ({thread.seats.count()} seats)."
    else:
        profiles, unmatched = [], []
        for index, participant in enumerate(participants):
            slug = participant.get("player")
            profile = Profile.objects.filter(slug=slug).first() if slug else None
            if slug and not profile:
                unmatched.append(slug)
            profiles.append(profile)

        # Seat by POSITION, not by raw turn_order: a stray "7" in a 2-player file
        # would otherwise create seven seats.
        raw_seats = [p.get("turn_order", p.get("seat")) for p in participants]
        if [s for s in raw_seats if s is not None] != list(
                range(1, len([s for s in raw_seats if s is not None]) + 1)):
            notes.append("Seat numbers weren't 1-N, so I used the order they appear in.")

        seats, _reseated = _persist_seating(thread, profiles, shuffle=False)
        order = "  ".join(
            f"{s.seat_number}. {s.profile.name if s.profile_id else '(blank)'}"
            for s in seats)
        seating_line = f"Seating: {order}"
        if unmatched:
            notes.append(
                "Couldn't match players: " + ", ".join(f"`{s}`" for s in unmatched)
                + " — pick them on the form.")

    if items:
        _capture_lfg_components(channel_id, items, source="boxscore")

    if entries:
        # clean() is not run by save(), and this model's own docstring says a
        # caller writing turns_data directly should validate first.
        try:
            validate_participants(entries)
        except BoxScoreImportError as exc:
            logger.error("boxscore built an invalid turns_data: %s", exc)
            return _ephemeral("That box score couldn't be saved — check the file and try again.")
        thread.turns_data = entries
        thread.save(update_fields=["turns_data"])

    turn_count = max(
        (len(e.get("turns") or []) for e in entries), default=0)
    lines = [
        f"Box score added from `{filename}` — {len(entries)} seats, {turn_count} turns."
        if entries else f"Read `{filename}`."
    ]
    if seating_line:
        lines.append(seating_line)
    if component_titles:
        lines.append("Map/Deck: " + " · ".join(component_titles))
    lines.extend(notes)

    return JsonResponse({
        "type": RESPONSE_CHANNEL_MESSAGE,
        "data": {
            "content": "\n".join(line for line in lines if line),
            # Naming a player must not ping them, same as /seating.
            "allowed_mentions": {"parse": []},
        },
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


# Options-panel select choices, as (label, value). Values are short and stable:
# they're echoed back in the message's component state and read by selected_values.
RANDOM_PLATFORM_CHOICES = [("Tabletop Simulator", "tts"), ("Root Digital", "rd")]
RANDOM_FAN_CHOICES = [("Official only", "0"), ("Include fan content", "1")]
RANDOM_SIDE_CHOICES = [("Either", "E"), ("Promoted", "P"), ("Demoted", "D")]


def _random_options_data(kind, owner, platform_key="tts", fan="0", side="E"):
    """The public, owner-locked options panel for a post-backed /random kind:
    platform and fan-content selects (plus a side select for Hirelings), then Roll.

    The current choices ride as default=True on the select options rather than in
    the custom_ids, so the Roll button can recover them via `selected_values` — the
    same mechanism /draft uses for its bans. That leaves only `kind` and `owner` to
    encode, which keeps the owner LAST in every id (the dispatcher's owner-lock
    reads args[-1] and only honours it when it looks like a snowflake)."""
    def options(choices, chosen):
        return [select_option(label, value, default=value == chosen)
                for label, value in choices]

    rows = [
        action_row(string_select(
            encode_custom_id("random_opt_platform", kind, owner),
            options(RANDOM_PLATFORM_CHOICES, platform_key),
            placeholder="Platform", min_values=1, max_values=1,
        )),
        action_row(string_select(
            encode_custom_id("random_opt_fan", kind, owner),
            options(RANDOM_FAN_CHOICES, fan),
            placeholder="Fan content", min_values=1, max_values=1,
        )),
    ]
    if kind == "Hireling":
        rows.append(action_row(string_select(
            encode_custom_id("random_opt_side", kind, owner),
            options(RANDOM_SIDE_CHOICES, side),
            placeholder="Side", min_values=1, max_values=1,
        )))
    rows.append(action_row(
        button("Roll", encode_custom_id("random_roll_post", kind, owner), style=STYLE_SUCCESS),
        button("Cancel", encode_custom_id("random_cancel", owner), style=STYLE_SECONDARY),
    ))
    return {"content": f"**Random {kind}** — set options, then Roll.", "components": rows}


def _random_panel_state(payload):
    """Recover (platform, hireling_type, include_fan_content) from the panel's own
    select state. A button press doesn't echo the selects' values, so read back the
    options rendered default=True. Falls back to the historical defaults (TTS /
    Either / official-only) when a select is absent or nothing is marked.

    NOTE: `selected_values` matches by custom_id PREFIX, so the three select action
    names must never prefix one another (see RANDOM_OPTION_ACTIONS)."""
    platform_key = (selected_values(payload, "random_opt_platform") or ["tts"])[0]
    fan = (selected_values(payload, "random_opt_fan") or ["0"])[0]
    side = (selected_values(payload, "random_opt_side") or ["E"])[0]
    return (RANDOM_PLATFORM_KEYS.get(platform_key, DRAFT_PLATFORM_TTS),
            side, fan == "1")


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


# The three panel selects, mapped to the piece of state each one sets. Their names
# must stay mutually non-prefixing — `selected_values` matches by startswith, so a
# shared stem would make the first select shadow the others.
RANDOM_OPTION_ACTIONS = {
    "random_opt_platform": "platform",
    "random_opt_fan": "fan",
    "random_opt_side": "side",
}


def _handle_random_option(payload):
    """A panel select changed: re-render the panel with the new choice persisted as
    default=True and the other selects left as they were.

    The message state reflects what Discord rendered BEFORE this interaction, so the
    select that fired still reads its OLD value there — it must be overridden with
    the echoed `values`, or the change is dropped and the panel looks frozen."""
    action, args = decode_custom_id(payload["data"]["custom_id"])
    kind = args[0] if args else "Faction"
    owner = args[-1] if args else None

    platform, side, fan = _random_panel_state(payload)
    state = {
        "platform": DRAFT_PLATFORM_TO_KEY.get(platform, "tts"),
        "fan": "1" if fan else "0",
        "side": side,
    }
    chosen = (payload["data"].get("values") or [None])[0]
    if chosen is not None and action in RANDOM_OPTION_ACTIONS:
        state[RANDOM_OPTION_ACTIONS[action]] = chosen

    return JsonResponse({
        "type": RESPONSE_UPDATE_MESSAGE,
        "data": _random_options_data(kind, owner, state["platform"], state["fan"],
                                     state["side"]),
    })


def _handle_random_roll_post(payload):
    """Roll button: read the chosen options off the panel, pick a random post and
    edit the panel into the result in place."""
    _action, args = decode_custom_id(payload["data"]["custom_id"])  # ["<Kind>", owner]
    kind = args[0] if args else None
    if kind not in RANDOM_POST_MODELS:
        return _random_error_edit(f"Unknown random kind: {kind}.")

    platform, side, include_fan_content = _random_panel_state(payload)
    result, error = _random_post_result(
        kind, platform, hireling_type=side if kind == "Hireling" else None,
        author=_interaction_author(payload), channel_id=payload.get("channel_id"),
        include_fan_content=include_fan_content,
    )
    if error:
        return _random_error_edit(error)
    return _random_result_edit(payload, result)


def _handle_random_cancel(payload):
    """Cancel button: edit the panel to a short notice, controls removed. Carries
    only `random_cancel:{owner}`, so it deliberately parses no state from the id."""
    return JsonResponse({
        "type": RESPONSE_UPDATE_MESSAGE,
        "data": {"content": "Random cancelled.", "embeds": [], "components": []},
    })


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
    if kind in RANDOM_POST_MODELS:
        # Every post-backed kind (Hirelings and Captains included) opens the one
        # options panel; it gathers platform, fan content and — for Hirelings — the
        # side in a single step, then Roll resolves it.
        return JsonResponse({
            "type": RESPONSE_CHANNEL_MESSAGE,
            "data": _random_options_data(kind, owner),
        })
    if kind == "Roll":
        return _random_dice_prompt(owner)
    # Suit/Clearing resolve immediately to a component-less public result — no owner.
    if kind == "Suit":
        return _random_from_list("Suit", RANDOM_SUITS, "card", "card", author=author)
    if kind == "Clearing":
        return _random_from_list("Clearing", RANDOM_CLEARINGS, "icon", "outline", author=author)
    return _ephemeral(f"Unknown random kind: {kind}")


def _random_eligible(kind, platform, hireling_type=None, include_fan_content=False):
    """Eligible Stable posts for a random kind, official-only unless
    `include_fan_content`. Root Digital narrows to factions/posts available there.
    `hireling_type` ('P'/'D') narrows Hirelings to one side; None (or 'E') leaves
    both."""
    qs = RANDOM_POST_MODELS[kind]().filter(status=1)
    if not include_fan_content:
        qs = qs.filter(official=True)
    if platform == DRAFT_PLATFORM_RD:
        qs = qs.filter(in_root_digital=True)
    if kind == "Hireling" and hireling_type in ("P", "D"):
        qs = qs.filter(type=hireling_type)
    return qs


def _random_chosen_from(kind, posts, include_fan_content=False):
    """The 'Chosen from' body text for a post-kind result. Faction -> emoji icons
    (name fallback), which is why this renders in the description not a footer;
    Hireling -> a count (there are many); other kinds -> names if <=6, else a count.

    The Faction emoji strip only holds for the official pool: fan factions have no
    emoji, so a fan-inclusive roll would fall back to dozens of bare titles. Those
    use the count form instead. The suffix marks the wider pool on the result, since
    the panel's own text is cleared when it becomes the result."""
    suffix = " (incl. fan content)" if include_fan_content else ""
    if kind == "Faction" and not include_fan_content:
        icons = [faction_emoji_for(p.slug) or p.title for p in posts]
        return "Chosen from: " + " ".join(icons) + suffix
    if len(posts) <= 6:
        return "Chosen from: " + ", ".join(p.title for p in posts) + suffix
    return f"Chosen from {len(posts)} options{suffix}"


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


def _random_post_result(kind, platform, hireling_type=None, author=None, channel_id=None,
                        include_fan_content=False):
    """Return (message_data, error) for a post-backed random kind as the unified
    /random embed (linked title + large board/card image). `hireling_type` ('P'/'D')
    narrows Hirelings to one side; `include_fan_content` widens the pool past
    official posts. When `channel_id` is a known LFG thread, the chosen component is
    recorded on the LFGThread."""
    posts = list(_random_eligible(kind, platform, hireling_type,
                                  include_fan_content=include_fan_content))
    if not posts:
        # Every post kind picks a platform on the options panel, so an empty pool
        # should say so.
        where = " for that platform" if kind in RANDOM_POST_MODELS else ""
        return None, f"No eligible {kind} found{where}."
    chosen = random.choice(posts)
    _capture_lfg_components(channel_id, [_lfg_item(kind, chosen)], source="random")
    embed = _random_result_embed(
        kind, chosen.title, _random_chosen_from(kind, posts, include_fan_content),
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
                      content=None, title=LFG_DEFAULT_TITLE, ping_role=True):
    """Build the full join-message payload (embed + button row). Used ONLY for the
    initial post and the picker→join transition — never to re-render on Join/Notify
    (that would wipe the other field; those handlers mutate the echoed embed).

    The Notify field is omitted until someone subscribes (added on first 🔔).

    `ping_role=False` renders the role mention WITHOUT notifying anyone — used
    inside a thread, where the ping is noise but the mention must still be in the
    content for ✔ Start to recover the tag from (see _handle_lfg_start)."""
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
        #
        # In a thread we still SEND the mention but authorize nothing: it renders as
        # a role chip that pings no one. The mention has to stay in the content
        # because it's the only place the tag survives to ✔ Start, which regexes the
        # role id back out of it.
        data["allowed_mentions"] = {"parse": ["roles"] if ping_role else []}
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

    # In a thread, THIS thread becomes the game thread (Discord can't nest one), so
    # a thread that already has a game is not available for another. Checked first,
    # before any work: it needs no role and refuses outright.
    #
    # This is load-bearing beyond UX -- ✔ Start adopts the thread with a
    # get_or_create, and without this gate a second /lfg here would overwrite the
    # first game's roster. See create_lfg_thread_task.
    in_thread = data.get("_channel_type") in _THREAD_CHANNEL_TYPES
    if in_thread:
        existing = _lfg_thread_for_channel(data.get("_channel_id"))
        if existing is not None:
            if existing.series_id:
                return _ephemeral("This is a tournament group thread, so you can't "
                                  "start an LFG game in it.")
            return _ephemeral("This thread is already linked to an LFG game, so you "
                              "can't use `/lfg` here.")

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

    # This tag's games live in a specific forum, so a thread elsewhere is the wrong
    # home for one. Refuse before posting anything rather than adopting a thread in
    # the wrong place. Only when parent_id is known: absent (older payload, or a
    # shape we didn't expect) means we can't tell, and guessing wrong would block a
    # valid /lfg -- so fail open, matching _lfg_role_is_live's posture.
    if in_thread and role.forum_channel_id:
        parent_id = data.get("_channel_parent_id")
        if parent_id and str(parent_id) != str(role.forum_channel_id):
            return _ephemeral(
                f"{role.name} games belong in <#{role.forum_channel_id}> - run "
                "`/lfg` in the appropriate LFG channel to automatically create a "
                "thread there.")

    title = role.description or role.name or LFG_DEFAULT_TITLE
    # Ping only if the underlying Discord role still exists; otherwise post with the tag
    # name as the title but no mention (avoids a broken @deleted-role ping).
    content = role.mention() if _lfg_role_is_live(role, guild_id) else None
    return JsonResponse({
        "type": RESPONSE_CHANNEL_MESSAGE,
        "data": _lfg_message_data(author, owner, description, players_value,
                                  content=content, title=title,
                                  # In a thread the mention renders but notifies
                                  # nobody -- the people here are already here.
                                  ping_role=not in_thread),
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
                return _ephemeral("You're hosting this game and can't join or leave it, "
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
    buttons, note the game was cancelled, and DM everyone who subscribed to 🔔 so
    they don't keep waiting on a game that isn't happening. Players leave via the
    Join toggle."""
    embed = (payload.get("message", {}).get("embeds") or [{}])[0]

    # The host, read off this button's own custom_id (`lfg_cancel:{owner}`), the
    # same way ✔ Start does. The clicker only equals the host because the
    # dispatcher owner-locks this button, so the custom_id is what actually means
    # "host". Defensive .get chain with an `or ""`, not payload["data"]: a missing
    # OR null custom_id must cost only the host exclusion, never the cancel the
    # host just asked for.
    _action, id_args = decode_custom_id(
        (payload.get("data") or {}).get("custom_id") or "")
    host_id = id_args[-1] if id_args else None

    # Read the subscribers BEFORE _lfg_set_notify_ids wipes them below. The host is
    # excluded -- they're the one who just cancelled.
    notify_ids = list(_lfg_ids_in_field(embed, LFG_NOTIFY_FIELD) - {host_id})
    if notify_ids:
        notify_lfg_cancelled_task.delay(
            notify_ids, _lfg_member_display_name(payload),
            embed.get("description", ""), _lfg_jump_url(payload))

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

    # Nothing parsed out of the Players field: starting would create a thread that
    # pings nobody (kickoff collapses to "your game can start!") and an LFGThread
    # with no players. The host can't leave, so this shouldn't happen in the normal
    # flow — it guards a malformed/edited embed. Bail before mutating or enqueuing.
    if not players:
        return _ephemeral("This game has no players yet.")

    # A table of one is the host alone -- the kickoff would ping only them and the
    # LFGThread would hold a single player. Refused BEFORE the mutations below so
    # the message stays fully joinable (an ephemeral never edits the source
    # message, so the buttons survive untouched). The host cannot leave via Join,
    # so the only way to be here is that nobody has joined yet.
    if len(players) < 2:
        return _ephemeral("You can't start a game with only one player — "
                          "wait for someone to press Join.")

    # Status subtext goes in the embed footer (small text at the very bottom).
    # Use the monochrome ✔ to match the Start button glyph.
    embed["footer"] = {"text": "✔ Game has started."}
    # The Notify list is only useful while recruiting; drop it once started.
    _lfg_set_notify_ids(embed, [])

    role_match = _LFG_ROLE_MENTION_RE.search(message.get("content", "") or "")
    role_id = role_match.group(1) if role_match else None

    # The host, read off this button's own custom_id (`lfg_start:{owner}`). This is
    # the LAST moment the host is knowable: the response below strips the buttons,
    # and nothing else records who started the game. Deliberately not
    # _interaction_user_id -- the clicker only equals the host because the
    # dispatcher owner-locks this button, so the custom_id is what actually means
    # "host".
    # .get chain with an `or ""`, not payload["data"]: a missing or null custom_id
    # must cost only the host attribution, never the thread the player asked for.
    _action, id_args = decode_custom_id(
        (payload.get("data") or {}).get("custom_id") or "")
    host_id = id_args[-1] if id_args else None

    # Whether /lfg was run inside a thread. Read off THIS payload rather than
    # carried through the embed or custom_id: Discord sends the partial channel
    # object on component clicks too, and the custom_id can't take it (the owner
    # snowflake must stay last for the dispatcher owner-lock). The dispatcher only
    # stashes _channel_* for slash commands, so read it here.
    in_thread = (payload.get("channel") or {}).get("type") in _THREAD_CHANNEL_TYPES

    # Pass the started embed so the task can re-edit it with the title linked to the
    # thread once the thread id is known (the thread is created in the task). The
    # interaction token lets the task send the owner an ephemeral notice if thread
    # creation fails (e.g. missing channel permissions).
    create_lfg_thread_task.delay(
        payload.get("channel_id"), message.get("id"), payload.get("guild_id"),
        role_id, description, players, embed, token=payload.get("token"),
        host_id=host_id, in_thread=in_thread,
    )
    # Answer synchronously (type 7) rather than deferring (type 6) and letting the
    # task be the sole writer. Deferring would leave the buttons LIVE until the
    # worker gets to it, so with Celery/Redis down the game still looks joinable and
    # a second ✔ Start would enqueue a duplicate thread. Answering here degrades to
    # "buttons gone, started footer, title just isn't linked".
    #
    # Note this edit and link_lfg_message_task's PATCH both write this message; the
    # embed serialized above predates `url`, so the task's edit re-sends the whole
    # embed plus components:[] to win on content whichever order they land in.
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
COMMAND_HANDLERS["record"] = _handle_record_command
COMMAND_HANDLERS["draft"] = _handle_draft_command
COMMAND_HANDLERS["seating"] = _handle_seating_command
COMMAND_HANDLERS["pick"] = _handle_pick_command
COMMAND_HANDLERS["adset"] = _handle_adset_command
COMMAND_HANDLERS["rename"] = _handle_rename_command
COMMAND_HANDLERS["boxscore"] = _handle_boxscore_command
COMMAND_HANDLERS["random"] = _handle_random_command
COMMAND_HANDLERS["lfg"] = _handle_lfg_command


# Component (button/select) handlers, keyed by the custom_id's action prefix.
COMPONENT_HANDLERS = {
    "draft_select": _handle_draft_select,
    "draft_build": _handle_draft_build,
    "draft_cancel": _handle_draft_cancel,
    # Only rendered when the thread already has a draft; owner-locked by its
    # trailing snowflake like the rest of the prompt's controls.
    "draft_clear": _handle_draft_clear,
    "draft_seat": _handle_draft_seat,
    "draft_seat_no": _handle_draft_seat_no,
    # `pick_mode` ends in the invoker's snowflake, so the dispatcher locks it to
    # them. `pick_faction`/`pick_cancel` end in PICK_OPEN so any seated player can
    # click; those handlers authorize per turn themselves.
    "pick_mode": _handle_pick_mode,
    # Both end in the invoker's snowflake, so the dispatcher owner-locks them:
    # they decide how the whole table proceeds, so they're the invoker's call.
    "pick_seat": _handle_pick_seat,
    "pick_noseat": _handle_pick_noseat,
    "pick_faction": _handle_pick_faction,
    # Follow-up selects for factions that need a second choice before the seat
    # can be written. Same PICK_OPEN convention as pick_faction: the dispatcher
    # lock stays off and the handler authorizes the turn itself.
    "pick_vagabond": _handle_pick_vagabond,
    "pick_captains": _handle_pick_captains,
    "pick_cancel": _handle_pick_cancel,
    # /adset. `adset_mode` shares _handle_pick_mode with /pick -- the two differ
    # only in the board title, which rides in the custom_id as PICK_ADSET_FLAG.
    "adset_join": _handle_adset_join,
    "adset_role": _handle_adset_role,
    "adset_start": _handle_adset_start,
    "adset_select": _handle_adset_select,
    "adset_build": _handle_adset_build,
    "adset_reseat": _handle_adset_reseat,
    "adset_redraft": _handle_adset_redraft,
    "adset_clear": _handle_adset_clear,
    # PICK_OPEN-terminated, so the dispatcher's owner-lock stays off and the
    # handler applies /pick's roster gate instead.
    "adset_takeover": _handle_adset_takeover,
    "adset_mode": _handle_pick_mode,
    "adset_cancel": _handle_adset_cancel,
    # The three options-panel selects share one handler; `random_roll` is the
    # (unrelated) dice prompt, hence `random_roll_post` for the panel's Roll button.
    "random_opt_platform": _handle_random_option,
    "random_opt_fan": _handle_random_option,
    "random_opt_side": _handle_random_option,
    "random_roll_post": _handle_random_roll_post,
    "random_cancel": _handle_random_cancel,
    "random_roll": _handle_random_roll,
    "schedule_confirm": _handle_schedule_confirm,
    "schedule_clear_confirm": _handle_schedule_clear_confirm,
    "schedule_cancel": _handle_schedule_cancel,
    # Public proposal buttons. Their custom_ids end in "g" (not a snowflake) so the
    # dispatcher's owner-lock stays off and any roster player can click; the
    # handlers do their own authorization.
    "sched_prop_ok": _handle_schedule_proposal_confirm,
    "sched_prop_no": _handle_schedule_proposal_reject,
    # Rides the "Everyone agreed" message: the roster consented, this writes it.
    "sched_prop_set": _handle_schedule_proposal_set,
    # Suggestions that write nothing. Owner-locked (the invoker's own prompt).
    "sched_free": _handle_schedule_free,
    # The time poll. `sched_poll_open` is owner-locked on the ephemeral prompt;
    # every button on the PUBLIC poll ends in "g" so the lock stays off and the
    # handlers authorize for themselves -- including Close, which is host-gated
    # but must also admit moderators, whom a single-snowflake lock cannot express.
    "sched_poll_open": _handle_schedule_poll_open,
    "sched_poll_ok": _handle_schedule_poll_dispatch,
    "sched_poll_no": _handle_schedule_poll_dispatch,
    "sched_poll_notify": _handle_schedule_poll_dispatch,
    "sched_poll_close": _handle_schedule_poll_dispatch,
    "schedule_tz_region": _handle_schedule_tz_region,
    "schedule_tz_zone": _handle_schedule_tz_zone,
    "schedule_tz_back": _handle_schedule_tz_back,
    # Same prompt as Back; the copy differs on whether a timezone is already set.
    "schedule_tz_change": _handle_schedule_tz_back,
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
                # A thread's parent channel (the forum, or the channel it hangs
                # off). Lets /lfg check a forum post is in the tag's OWN forum
                # without an API round-trip. Absent on a plain channel.
                data["_channel_parent_id"] = channel.get("parent_id")
                # The invoker's computed permissions in this channel (Discord resolves
                # roles/owner/admin for us). Lets /help decide, without an API call,
                # whether to offer the "enable more commands" link.
                data["_member_permissions"] = (payload.get("member") or {}).get("permissions")
                # Interaction token, so a handler can send a followup after its ACK
                # (e.g. /lfg's ephemeral "add tags" nudge).
                data["_token"] = payload.get("token")
                # A thread with a roster belongs to its players: commands that
                # write to it (seating, drafts, the roll log, proposals) are
                # refused for anyone else. Enforced here rather than per handler
                # so a new command can't quietly miss it -- and AFTER the stash
                # above, which is where the helper's inputs come from.
                if command_name in ROSTER_GUARDED_COMMANDS:
                    refusal = _thread_actor_error(data)
                    if refusal is not None:
                        return refusal
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
                return _ephemeral("Only the host can use this button.")
            try:
                return handler(payload)  # component handlers take the full payload
            except Exception:
                logger.exception("Error handling component %s", custom_id)
                return _ephemeral("Something went wrong handling that.")
        return _ephemeral(f"Unknown component: {custom_id}")

    # Unhandled interaction type
    return HttpResponse("unhandled interaction type", status=400)
