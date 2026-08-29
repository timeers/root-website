import requests
import json
import re
import emoji

from io import BytesIO
from PIL import Image

from datetime import timedelta

from allauth.socialaccount.models import SocialAccount

from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.core.files.base import ContentFile
from django.utils import timezone

from the_gatehouse.models import (DiscordGuild, DiscordGuildJoinRequest,
                                  DEFAULT_PROFILE_IMAGE as _DEFAULT_PROFILE_IMAGE)
# Safe at module level: time_parsing imports only stdlib + dateutil, nothing from
# this module or the ORM.
from .time_parsing import format_discord_timestamp

from django.urls import reverse
from django.templatetags.static import static
from django.utils.translation import gettext as _

import logging

logger = logging.getLogger(__name__)


# Re-exported from models (the canonical definition) so existing callers that
# import it from here keep working.
DEFAULT_PROFILE_IMAGE = _DEFAULT_PROFILE_IMAGE

DISCORD_API = "https://discord.com/api/v10"

with open('/etc/config.json') as config_file:
    config = json.load(config_file)


def _bot_headers():
    """Auth headers for Discord bot REST calls (DMs, command registration)."""
    return {
        "Authorization": f"Bot {config['DISCORD_BOT_TOKEN']}",
        "Content-Type": "application/json",
    }


# send_discord_dm result codes
DM_OK = "ok"            # delivered
DM_BLOCKED = "blocked"  # permanent: no shared server / DMs disabled / no Discord ID — do not retry
DM_ERROR = "error"      # transient: network error, 5xx, rate limit — safe to retry


def _is_terminal_http_error(exc):
    """A 403 means the bot can't DM this user (no shared server / DMs off): permanent."""
    response = getattr(exc, "response", None)
    return response is not None and response.status_code == 403


def send_discord_dm(user, content=None, embed=None, force=False):
    """
    Send a direct message to a user via the bot.

    Requires the bot and the user to share a server (Discord anti-spam rule).
    Never raises to the caller. Returns one of:
        DM_OK      — delivered
        DM_BLOCKED — permanent failure (no shared server, DMs disabled, no ID); do not retry
        DM_ERROR   — transient failure (network/5xx/rate limit); safe to retry

    force=True bypasses the DEBUG_VALUE guard. Use only for explicit manual
    testing (e.g. the test_dm command); the real event triggers never set it,
    so a dev/staging environment won't DM real users during normal testing.
    """
    discord_id = get_discord_id(user)
    if not discord_id:
        logger.info("No Discord ID for user %s; cannot DM.", user)
        return DM_BLOCKED
    return send_dm_by_id(discord_id, content=content, embed=embed, force=force)


def send_dm_by_id(discord_id, content=None, embed=None, force=False):
    """
    Send a direct message to a raw Discord user id via the bot — no Profile or
    SocialAccount required (unlike send_discord_dm, which resolves the id from a
    linked SocialAccount). Requires the bot and user to share a server. Never
    raises. Returns DM_OK / DM_BLOCKED / DM_ERROR.

    force=True bypasses the DEBUG_VALUE guard (manual testing only).
    """
    if not force and config["DEBUG_VALUE"] == "True":
        return DM_BLOCKED  # mirror existing webhook guard; not a retryable error

    if not discord_id:
        return DM_BLOCKED

    # 1) Open (or fetch) the DM channel with this user
    try:
        ch = requests.post(
            f"{DISCORD_API}/users/@me/channels",
            headers=_bot_headers(),
            json={"recipient_id": str(discord_id)},
            timeout=10,
        )
        ch.raise_for_status()
        channel_id = ch.json()["id"]
    except requests.RequestException as e:
        if _is_terminal_http_error(e):
            logger.info("Cannot DM %s (channel open 403, no shared server).", discord_id)
            return DM_BLOCKED
        logger.error("Failed to open DM channel for %s: %s", discord_id, e)
        return DM_ERROR

    # 2) Post the message into that channel
    payload = {}
    if content:
        payload["content"] = content
    if embed:
        payload["embeds"] = [embed]

    try:
        msg = requests.post(
            f"{DISCORD_API}/channels/{channel_id}/messages",
            headers=_bot_headers(),
            json=payload,
            timeout=10,
        )
        # 403 here usually means the bot and user share no server, or the
        # user has DMs from server members disabled.
        msg.raise_for_status()
        return DM_OK
    except requests.RequestException as e:
        if _is_terminal_http_error(e):
            logger.info("Cannot DM %s (message 403, DMs blocked).", discord_id)
            return DM_BLOCKED
        logger.error("Failed to send DM to %s: %s", discord_id, e)
        return DM_ERROR


# Thread helper result codes
THREAD_OK = "ok"
THREAD_BLOCKED = "blocked"  # permanent: message deleted, missing perms — do not retry
THREAD_ERROR = "error"      # transient: network error, 5xx, rate limit — safe to retry


def _is_terminal_edit_error(exc):
    """A 403 (missing perms) or 404 (message/channel gone) won't fix itself on retry.
    A 429/5xx/network error will, so those stay retryable."""
    response = getattr(exc, "response", None)
    return response is not None and response.status_code in (403, 404)


def create_message_thread(channel_id, message_id, name, auto_archive_duration=1440):
    """Create a thread hanging off an existing message. Returns the thread id
    (a snowflake string) on success, or None on failure. Never raises.

    No DEBUG_VALUE guard: this is a public, user-initiated action in the channel
    where the /lfg command was used (like the /lfg message and its button edits,
    which also post live), not an unsolicited DM."""
    name = (name or "Game")[:100]  # Discord thread name cap
    try:
        r = requests.post(
            f"{DISCORD_API}/channels/{channel_id}/messages/{message_id}/threads",
            headers=_bot_headers(),
            json={"name": name, "auto_archive_duration": auto_archive_duration},
            timeout=5,
        )
        r.raise_for_status()
        return r.json()["id"]
    except (requests.RequestException, KeyError, ValueError) as e:
        # Include Discord's HTTP status + error body so the reason is diagnosable
        # (e.g. 403 / 50013 Missing Permissions in a restricted channel). `response`
        # is only present on RequestException, and is None for a timeout/conn error.
        resp = getattr(e, "response", None)
        status = resp.status_code if resp is not None else "?"
        detail = resp.text if resp is not None else str(e)
        logger.error("Failed to create thread on message %s: %s %s", message_id, status, detail)
        return None


def create_forum_thread(forum_channel_id, name, content=None, embeds=None, tag_id=None):
    """Create a new thread (post) in a forum channel. Unlike a message thread, this
    posts a fresh starter message (content/embeds) rather than hanging off an
    existing one. `tag_id` optionally applies one forum tag to the post. Returns the
    thread id on success, or None on failure. Never raises.
    No DEBUG_VALUE guard — see create_message_thread."""
    name = (name or "Game")[:100]
    message = {}
    if content:
        message["content"] = content
    if embeds:
        message["embeds"] = embeds
    if not message:
        message["content"] = "​"  # Discord requires a non-empty starter message
    body = {"name": name, "auto_archive_duration": 1440, "message": message}
    if tag_id:
        body["applied_tags"] = [str(tag_id)]
    try:
        r = requests.post(
            f"{DISCORD_API}/channels/{forum_channel_id}/threads",
            headers=_bot_headers(),
            json=body,
            timeout=5,
        )
        r.raise_for_status()
        return r.json()["id"]
    except (requests.RequestException, KeyError, ValueError) as e:
        logger.error("Failed to create forum thread in %s: %s", forum_channel_id, e)
        return None


def post_channel_message(channel_id, content):
    """Post a message into a channel/thread. Returns THREAD_OK / THREAD_BLOCKED /
    THREAD_ERROR. Never raises. No DEBUG_VALUE guard — see create_message_thread."""
    try:
        r = requests.post(
            f"{DISCORD_API}/channels/{channel_id}/messages",
            headers=_bot_headers(),
            json={"content": content},
            timeout=5,
        )
        r.raise_for_status()
        return THREAD_OK
    except requests.RequestException as e:
        resp = getattr(e, "response", None)
        detail = resp.text if resp is not None else str(e)
        if _is_terminal_edit_error(e):
            # 403 (the bot can't post here) / 404 (the thread is gone) won't fix
            # itself, so don't let the task retry it three more times.
            logger.warning("Cannot post message in channel %s (permanent): %s",
                           channel_id, detail)
            return THREAD_BLOCKED
        logger.error("Failed to post message in channel %s: %s", channel_id, detail)
        return THREAD_ERROR


def post_channel_message_full(channel_id, content=None, embeds=None, components=None,
                              allowed_mentions=None):
    """Post a rich message into a channel/thread and return (result, message_id).

    Unlike post_channel_message (content-only, status-only), this exists so the
    caller can KEEP the new message's id: a /schedule proposal has to be edited
    later — from a DIFFERENT interaction, or from a Celery task — to strip its
    buttons once another proposal wins.

    `result` is THREAD_OK / THREAD_BLOCKED / THREAD_ERROR with the same meaning as
    edit_channel_message; `message_id` is None on any failure. Never raises.

    No DEBUG_VALUE guard — see create_message_thread."""
    body = {}
    if content is not None:
        body["content"] = content
    if embeds is not None:
        body["embeds"] = embeds
    if components is not None:
        body["components"] = components
    if allowed_mentions is not None:
        body["allowed_mentions"] = allowed_mentions
    try:
        r = requests.post(
            f"{DISCORD_API}/channels/{channel_id}/messages",
            headers=_bot_headers(),
            json=body,
            timeout=5,
        )
        r.raise_for_status()
        return THREAD_OK, str(r.json()["id"])
    except requests.RequestException as e:
        resp = getattr(e, "response", None)
        detail = resp.text if resp is not None else str(e)
        if _is_terminal_edit_error(e):
            logger.warning("Cannot post message in channel %s (permanent): %s",
                           channel_id, detail)
            return THREAD_BLOCKED, None
        logger.error("Failed to post message in channel %s: %s", channel_id, detail)
        return THREAD_ERROR, None
    except (KeyError, ValueError) as e:
        # 2xx with a body we can't read an id out of. The message may well have been
        # posted, so retrying would double-post: treat as permanent.
        logger.warning("Posted to channel %s but could not read the message id: %s",
                       channel_id, e)
        return THREAD_BLOCKED, None


def edit_channel_message(channel_id, message_id, embeds=None, components=None):
    """Edit an existing bot message (PATCH). Never raises. Returns one of:
        THREAD_OK      — edited
        THREAD_BLOCKED — permanent failure (403 missing perms / 404 gone); do not retry
        THREAD_ERROR   — transient failure (network/5xx/rate limit); safe to retry

    Only the parts you pass are sent, and an omitted key leaves that part of the
    message untouched. Note the `is not None` tests: `components=[]` is meaningful
    (it CLEARS the button row) and must not be confused with "leave components
    alone", so truthiness would be wrong here.

    No DEBUG_VALUE guard — see create_message_thread."""
    body = {}
    if embeds is not None:
        body["embeds"] = embeds
    if components is not None:
        body["components"] = components
    if not body:
        return THREAD_OK  # nothing to change; don't spend a request
    try:
        r = requests.patch(
            f"{DISCORD_API}/channels/{channel_id}/messages/{message_id}",
            headers=_bot_headers(),
            json=body,
            timeout=5,
        )
        r.raise_for_status()
        return THREAD_OK
    except requests.RequestException as e:
        # Include Discord's status + body so a rejected field (e.g. components on a
        # message we're not allowed to edit that way) is diagnosable rather than opaque.
        resp = getattr(e, "response", None)
        detail = resp.text if resp is not None else str(e)
        if _is_terminal_edit_error(e):
            logger.warning("Cannot edit message %s in channel %s (permanent): %s",
                           message_id, channel_id, detail)
            return THREAD_BLOCKED
        logger.error("Failed to edit message %s in channel %s: %s",
                     message_id, channel_id, detail)
        return THREAD_ERROR


def _retry_after_seconds(resp):
    """Seconds to wait from a 429 response, or None.

    Discord puts `retry_after` (a float) in the JSON body; the Retry-After header
    is the fallback. Never raises: an empty or non-JSON body must degrade to None
    rather than turning a rate-limit into an exception in the caller."""
    if resp is None:
        return None
    try:
        value = (resp.json() or {}).get("retry_after")
        if value is not None:
            return float(value)
    except (ValueError, AttributeError, TypeError):
        pass
    try:
        header = resp.headers.get("Retry-After")
        return float(header) if header else None
    except (ValueError, AttributeError, TypeError):
        return None


def rename_channel(channel_id, name):
    """Rename a channel or thread (PATCH). Never raises. Returns
    (result, retry_after) where result is THREAD_OK / THREAD_BLOCKED / THREAD_ERROR
    and retry_after is the seconds to wait on a rate limit, else None.

    The tuple exists because of this endpoint specifically: Discord caps thread
    renames at 2 per 10 minutes, so a 429 here carries a retry_after of HUNDREDS of
    seconds -- far too long to swallow behind a generic "try again". The caller
    tells the user how long to wait. (429 stays THREAD_ERROR per
    _is_terminal_edit_error; retry_after is what distinguishes it.)

    No DEBUG_VALUE guard — see create_message_thread."""
    name = (name or "")[:100]  # Discord channel name cap, as create_message_thread
    try:
        r = requests.patch(
            f"{DISCORD_API}/channels/{channel_id}",
            headers=_bot_headers(),
            json={"name": name},
            timeout=5,
        )
        r.raise_for_status()
        return THREAD_OK, None
    except requests.RequestException as e:
        resp = getattr(e, "response", None)
        detail = resp.text if resp is not None else str(e)
        if _is_terminal_edit_error(e):
            logger.warning("Cannot rename channel %s (permanent): %s", channel_id, detail)
            return THREAD_BLOCKED, None
        retry_after = _retry_after_seconds(resp)
        if retry_after is not None:
            logger.warning("Rate limited renaming channel %s; retry after %ss",
                           channel_id, retry_after)
        else:
            logger.error("Failed to rename channel %s: %s", channel_id, detail)
        return THREAD_ERROR, retry_after


def get_bot_guilds():
    """
    Return the list of guilds the bot is a member of (from Discord),
    or None on failure. Each item is a dict with at least 'id' and 'name'.
    """
    try:
        response = requests.get(
            f"{DISCORD_API}/users/@me/guilds",
            headers=_bot_headers(),
            timeout=10,
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error("Failed to fetch bot guilds: %s", e)
        return None


def bot_in_guild(guild_id):
    """Cheap check of whether the bot is a member of a single guild, or None if we
    can't tell (network/auth error). One GET /guilds/{id}: 200 → in, 403/404 → not.
    Used to self-correct a stale DiscordGuild.bot_member=False when a moderator opens
    the guild edit page (the daily sync_bot_guilds task keeps the flag current in bulk).
    Cached ~5 min so rapid reloads / HTMX round-trips don't re-probe."""
    if not guild_id:
        return None
    key = f"bot_in_guild:{guild_id}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    try:
        response = requests.get(
            f"{DISCORD_API}/guilds/{guild_id}",
            headers=_bot_headers(),
            timeout=10,
        )
        if response.status_code in (403, 404):
            cache.set(key, False, _DISCORD_LOOKUP_TTL)
            return False
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error("Failed to check bot membership for guild %s: %s", guild_id, e)
        return None
    cache.set(key, True, _DISCORD_LOOKUP_TTL)
    return True


def register_guild_commands(guild):
    """PUT this guild's enabled command set (always /help + whitelisted) to Discord's
    guild-scoped command endpoint. Returns True on success. Guild-scoped registration is
    ~instant (unlike global). Call on bot-add and whenever the whitelist changes."""
    from .discord_commands import (commands_for_guild, help_command_for_guild,
                                   lfg_command_for_roles)
    app_id = config["DISCORD_ID"]  # OAuth client ID doubles as the application ID
    url = f"{DISCORD_API}/applications/{app_id}/guilds/{guild.guild_id}/commands"
    # commands_for_guild returns references to the shared module-level command dicts, so
    # build a NEW list and substitute the per-guild variants of the two commands that have
    # them. Both helpers deep-copy, so the shared singletons are never mutated:
    #   * /help — BASE vs the LFG variant with the `category` dropdown (always present, so
    #     no membership check needed).
    #   * /lfg  — SINGLE vs MULTI, choices baked from this guild's tags. Only when it's
    #     actually in the body (i.e. whitelisted).
    # The second comprehension chains off `body`, not `base`, so it keeps the /help swap.
    enabled = guild.enabled_commands or []
    base = commands_for_guild(enabled)
    body = [help_command_for_guild(enabled) if c["name"] == "help" else c for c in base]
    if any(c["name"] == "lfg" for c in body):
        roles = list(guild.lfg_roles.all())
        body = [lfg_command_for_roles(roles) if c["name"] == "lfg" else c for c in body]
    try:
        resp = requests.put(url, headers=_bot_headers(), json=body, timeout=10)
        resp.raise_for_status()
        return True
    except requests.RequestException:
        logger.exception("Failed to register commands for guild %s", guild.guild_id)
        return False


# Cached (~5 min) reads of a guild's roles/forums/tags, used to build the LFG-role
# settings dropdowns. Each requires only that the bot is a member of the guild (no
# special permission bit); a 403/404 → None so the form can fall back to manual entry.
_DISCORD_LOOKUP_TTL = 300


def get_guild_roles(guild_id):
    """Assignable roles for a guild as [{"id","name"}], or None on failure. Excludes
    @everyone (its id == the guild id) and managed roles (bot/integration/booster roles
    nobody can be manually assigned). Cached ~5 min."""
    if not guild_id:
        return None
    key = f"guild_roles:{guild_id}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    try:
        response = requests.get(
            f"{DISCORD_API}/guilds/{guild_id}/roles",
            headers=_bot_headers(),
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        logger.error("Failed to fetch roles for guild %s: %s", guild_id, e)
        return None
    roles = [
        {"id": str(r["id"]), "name": r.get("name", "")}
        for r in sorted(data, key=lambda r: r.get("position", 0), reverse=True)
        if str(r["id"]) != str(guild_id) and not r.get("managed")
    ]
    cache.set(key, roles, _DISCORD_LOOKUP_TTL)
    return roles


def _get_guild_role_permissions(guild_id):
    """Map of role_id -> permissions bitfield (int) for every role in the guild,
    including @everyone, or None on failure. Cached ~5 min. Kept separate from
    get_guild_roles (which drops @everyone/managed roles and the permissions field)
    because permission math needs the raw, complete set."""
    if not guild_id:
        return None
    key = f"guild_role_perms:{guild_id}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    try:
        response = requests.get(
            f"{DISCORD_API}/guilds/{guild_id}/roles",
            headers=_bot_headers(),
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        logger.error("Failed to fetch role permissions for guild %s: %s", guild_id, e)
        return None
    perms = {str(r["id"]): int(r.get("permissions", 0)) for r in data}
    cache.set(key, perms, _DISCORD_LOOKUP_TTL)
    return perms


# Discord permission bits (https://discord.com/developers/docs/topics/permissions).
_PERM_ADMINISTRATOR = 1 << 3
_PERM_MANAGE_GUILD = 1 << 5


def permissions_can_manage_guild(permissions):
    """Whether a Discord `permissions` bitfield grants server management (Administrator
    or Manage Guild). `permissions` is the computed value Discord sends on a guild
    interaction (payload["member"]["permissions"]), a decimal string. Lets /help decide
    synchronously — from the payload, no API call — whether to offer the manage link."""
    try:
        bits = int(permissions or 0)
    except (TypeError, ValueError):
        return False
    return bool(bits & (_PERM_ADMINISTRATOR | _PERM_MANAGE_GUILD))


def user_can_manage_guild(user, guild_id):
    """Whether `user` has server-management permission in the guild per Discord, or
    None if we can't tell (not a member, missing OAuth scope, bot not in guild, or a
    network/auth error). "Manage" = the ADMINISTRATOR or MANAGE_GUILD permission bit,
    computed by OR-ing @everyone + the member's roles; the guild owner always qualifies.

    Uses the user's own OAuth token (scope guilds.members.read) for their member object
    and the bot token for the guild's roles/owner. One member fetch + cached roles, so
    it's cheap enough to run on a single deliberate action (e.g. adding a guild) — not
    for sweeping every guild a user belongs to."""
    if not guild_id:
        return None
    access_token = get_valid_discord_token(user)
    if access_token is None:
        return None

    # The user's member object in this guild: gives their role ids (but not a computed
    # permission bitfield, which Discord doesn't return here).
    try:
        member_resp = requests.get(
            f"{DISCORD_API}/users/@me/guilds/{guild_id}/member",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=5,
        )
    except requests.RequestException as e:
        logger.warning("Failed to fetch member for guild %s: %s", guild_id, e)
        return None
    # 404 not a member; 401/403 token lacks guilds.members.read — can't tell.
    if member_resp.status_code in (401, 403, 404):
        return None
    if member_resp.status_code != 200:
        logger.warning("Unexpected status fetching member for guild %s: %s",
                       guild_id, member_resp.status_code)
        return None
    member = member_resp.json() or {}
    member_role_ids = set(member.get("roles") or [])

    # The guild owner has every permission regardless of roles.
    guild = _get_guild(guild_id)
    if guild is not None and str(guild.get("owner_id")) == _member_user_id(member):
        return True

    role_perms = _get_guild_role_permissions(guild_id)
    if role_perms is None:
        return None

    # Effective permissions = @everyone (keyed by guild_id) OR each of the member's roles.
    effective = role_perms.get(str(guild_id), 0)
    for rid in member_role_ids:
        effective |= role_perms.get(str(rid), 0)

    if effective & _PERM_ADMINISTRATOR:  # admin implies everything
        return True
    return bool(effective & _PERM_MANAGE_GUILD)


def _member_user_id(member):
    """The user id from a guild member object (@me/guilds/{id}/member shape)."""
    return str((member.get("user") or {}).get("id") or "")


def _get_guild(guild_id):
    """Raw guild object from the bot token (for owner_id), or None. Cached ~5 min."""
    if not guild_id:
        return None
    key = f"guild_object:{guild_id}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    try:
        response = requests.get(
            f"{DISCORD_API}/guilds/{guild_id}",
            headers=_bot_headers(),
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        logger.error("Failed to fetch guild object %s: %s", guild_id, e)
        return None
    cache.set(key, data, _DISCORD_LOOKUP_TTL)
    return data


def get_guild_forum_channels(guild_id):
    """Forum/media channels for a guild as [{"id","name"}], or None on failure.
    Cached ~5 min."""
    if not guild_id:
        return None
    key = f"guild_forum_channels:{guild_id}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    try:
        response = requests.get(
            f"{DISCORD_API}/guilds/{guild_id}/channels",
            headers=_bot_headers(),
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        logger.error("Failed to fetch channels for guild %s: %s", guild_id, e)
        return None
    forums = [
        {"id": str(c["id"]), "name": c.get("name", "")}
        for c in data if c.get("type") in (15, 16)  # 15 GUILD_FORUM, 16 GUILD_MEDIA
    ]
    cache.set(key, forums, _DISCORD_LOOKUP_TTL)
    return forums


def get_guild_text_channels(guild_id):
    """Text/announcement channels for a guild as [{"id","name"}], or None on failure.
    Same endpoint and contract as get_guild_forum_channels, filtered to the types a
    plain message can be posted into. Ordered by Discord's own channel position so the
    dropdown reads like the server's sidebar. Cached ~5 min."""
    if not guild_id:
        return None
    key = f"guild_text_channels:{guild_id}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    try:
        response = requests.get(
            f"{DISCORD_API}/guilds/{guild_id}/channels",
            headers=_bot_headers(),
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        logger.error("Failed to fetch channels for guild %s: %s", guild_id, e)
        return None
    channels = [
        {"id": str(c["id"]), "name": c.get("name", "")}
        for c in sorted(data, key=lambda c: c.get("position", 0))
        if c.get("type") in (0, 5)  # 0 GUILD_TEXT, 5 GUILD_ANNOUNCEMENT
    ]
    cache.set(key, channels, _DISCORD_LOOKUP_TTL)
    return channels


def get_forum_channel_info(channel_id):
    """Forum channel details for the tag dropdown, or None on failure. Returns
    {"is_forum": bool, "requires_tag": bool, "tags": [{"id","name"}]} — a non-forum
    channel still returns a dict (is_forum=False) so callers distinguish that from a
    failed fetch (None). Cached ~5 min (successes only)."""
    if not channel_id:
        return None
    key = f"forum_channel_info:{channel_id}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    try:
        response = requests.get(
            f"{DISCORD_API}/channels/{channel_id}",
            headers=_bot_headers(),
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        logger.error("Failed to fetch forum channel %s: %s", channel_id, e)
        return None
    info = {
        "is_forum": data.get("type") in (15, 16),
        "requires_tag": bool((data.get("flags") or 0) & 16),  # REQUIRE_TAG = 1<<4
        "tags": [
            {"id": str(t["id"]), "name": t.get("name", "")}
            for t in (data.get("available_tags") or [])
        ],
    }
    cache.set(key, info, _DISCORD_LOOKUP_TTL)
    return info


def sync_bot_guilds():
    """
    Refresh DiscordGuild.bot_member to reflect which guilds the bot is in.

    Creates a DiscordGuild row for any guild the bot joined that we don't
    track yet, flags those bot_member=True, and clears the flag on the rest.
    Returns the number of guilds the bot is in, or None on API failure.
    """
    guilds = get_bot_guilds()
    if guilds is None:
        return None

    bot_guild_ids = [str(g["id"]) for g in guilds]

    # Ensure a DiscordGuild exists for each guild the bot is in.
    for g in guilds:
        DiscordGuild.objects.get_or_create(
            guild_id=str(g["id"]),
            defaults={"name": g.get("name", "")},
        )

    # Flag membership: True for the bot's guilds, False for all others.
    DiscordGuild.objects.filter(guild_id__in=bot_guild_ids).update(bot_member=True)
    DiscordGuild.objects.exclude(guild_id__in=bot_guild_ids).update(bot_member=False)

    logger.info("Synced bot guilds: bot is in %d guild(s).", len(bot_guild_ids))
    return len(bot_guild_ids)


def get_ww_guild_nickname(user, timeout=5):
    """Return the user's server nickname in the Woodland Warriors guild, or None.

    Uses the user's own OAuth token (scope ``guilds.members.read``) to read their
    member object in the WW guild; the ``nick`` field is the per-guild nickname and
    is null when unset. Returns None on any failure — not in the guild, no nickname,
    missing scope (older tokens), or API/network error — so callers fall back to the
    global display name.
    """
    guild_id = config.get("WW_GUILD_ID")
    if not guild_id:
        return None

    access_token = get_valid_discord_token(user, timeout=timeout)
    if access_token is None:
        return None

    try:
        response = requests.get(
            f"{DISCORD_API}/users/@me/guilds/{guild_id}/member",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=timeout,
        )
    except requests.RequestException as e:
        logger.warning("Failed to fetch WW nickname for user %s: %s", user, e)
        return None

    # 404: not a member of the WW guild. 401/403: token lacks the
    # guilds.members.read scope (e.g. hasn't re-consented yet). All expected —
    # fall back quietly rather than logging noise.
    if response.status_code in (401, 403, 404):
        return None
    if response.status_code != 200:
        logger.warning(
            "Unexpected status fetching WW nickname for user %s: %s %s",
            user, response.status_code, response.text,
        )
        return None

    nick = (response.json() or {}).get("nick")
    return nick.strip() if nick and nick.strip() else None


def get_discord_display_name(user, timeout=5):
    try:
        social = SocialAccount.objects.get(user=user, provider="discord")
        data = social.extra_data or {}

        # Prefer the user's Woodland Warriors server nickname; fall back to the
        # Discord global_name, then username, then the Django username.
        display_name = (
            get_ww_guild_nickname(user, timeout=timeout)
            or data.get("global_name")
            or data.get("username")
            or data.get("user", {}).get("username")
            or user.username   # fallback
        )

        # Emoji stripping (safe)
        try:
            display_name = emoji.replace_emoji(display_name, replace='').strip()
        except Exception:
            display_name = display_name.strip()

        return display_name

    except SocialAccount.DoesNotExist:
        # No Discord account then fallback to normal Django username
        return user.username

    
def get_discord_id(user):
    social_account = SocialAccount.objects.filter(user=user, provider='discord').first()
    return str(social_account.uid) if social_account else None



def discord_default_avatar_url(discord_id):
    """Discord's own fallback avatar for a user with no custom one.

    New-style usernames key off (id >> 22) % 6. Mirrors the same derivation in
    discord_interactions._interaction_author.
    """
    try:
        index = (int(discord_id) >> 22) % 6
    except (TypeError, ValueError):
        index = 0
    return f"https://cdn.discordapp.com/embed/avatars/{index}.png"


def update_discord_avatar(user, force=False):
    social_account = SocialAccount.objects.filter(user=user, provider='discord').first()
    if not social_account:
        return None

    profile = getattr(user, "profile", None)
    if not profile:
        return None

    # Skip if user already uploaded a custom profile picture
    if not force and profile.image and profile.image.name != DEFAULT_PROFILE_IMAGE:
        return None

    data = social_account.extra_data
    discord_id = data.get("id")
    avatar_hash = data.get("avatar")

    if not discord_id:
        return None

    if avatar_hash:
        ext = "gif" if avatar_hash.startswith("a_") else "png"
        avatar_url = f"https://cdn.discordapp.com/avatars/{discord_id}/{avatar_hash}.{ext}?size=1024"
    else:
        # No custom avatar: take Discord's default rather than returning early,
        # so the profile ends up with a real file instead of an unset image.
        avatar_url = discord_default_avatar_url(discord_id)

    # Network errors deliberately propagate: this runs inside
    # update_discord_avatar_task, whose autoretry_for=(Exception,) is what makes a
    # transient Discord/CDN blip recoverable. Swallowing them here disabled retries.
    response = requests.get(avatar_url, timeout=10)
    if response.status_code != 200:
        logger.warning("Discord avatar fetch for %s returned %s",
                       user, response.status_code)
        return None

    # The upload path always yields a .webp name (see avatar_upload_path), so encode
    # the bytes to WebP here rather than storing PNG/GIF bytes under a .webp file.
    try:
        img = Image.open(BytesIO(response.content))
        if img.mode not in ("RGB", "RGBA"):
            if img.mode in ("LA", "P") and "transparency" in img.info:
                img = img.convert("RGBA")
            else:
                img = img.convert("RGB")
        buffer = BytesIO()
        img.save(buffer, format="WEBP", quality=85, method=6)
        content = buffer.getvalue()
    except Exception:
        logger.exception("Could not decode Discord avatar for %s", user)
        return None

    profile.image.save(f"discord_{user.id}.webp", ContentFile(content), save=True)
    return profile.image.url


def get_valid_discord_token(user, timeout=5):
    """Get a valid Discord access token, refreshing if expired.

    `timeout` bounds the token-refresh POST. The login path passes the time left in
    its overall budget so a slow Discord can't hold a WSGI worker (see
    refresh_user_guilds); every other caller keeps the historical 5s.
    """
    try:
        social_account = user.socialaccount_set.get(provider='discord')
    except user.socialaccount_set.model.DoesNotExist:
        logger.warning("No Discord social account found for user %s", user)
        return None

    token_obj = social_account.socialtoken_set.first()
    if token_obj is None:
        logger.warning("No access token found for user %s", user)
        return None

    # Check if token is expired (with 60s buffer)
    if token_obj.expires_at and timezone.now() >= token_obj.expires_at - timedelta(seconds=60):
        if not token_obj.token_secret:
            logger.warning("Token expired and no refresh token available for user %s", user)
            return None

        try:
            response = requests.post(
                'https://discord.com/api/v10/oauth2/token',
                data={
                    'client_id': config['DISCORD_ID'],
                    'client_secret': config['DISCORD_SECRET'],
                    'grant_type': 'refresh_token',
                    'refresh_token': token_obj.token_secret,
                },
                # Reachable from a request thread (add-guild-from-invite view); keep short
                # so a slow Discord API can't hold a WSGI worker (defense in depth).
                timeout=timeout,
            )
            response.raise_for_status()
            data = response.json()

            token_obj.token = data['access_token']
            if 'refresh_token' in data:
                token_obj.token_secret = data['refresh_token']
            token_obj.expires_at = timezone.now() + timedelta(seconds=int(data.get('expires_in', 604800)))
            token_obj.save()
            logger.info("Refreshed Discord token for user %s", user)
        except requests.RequestException as e:
            logger.error("Failed to refresh Discord token for user %s: %s", user, e)
            return None

    return token_obj.token


def get_user_guilds(user, timeout=5):
    access_token = get_valid_discord_token(user, timeout=timeout)
    if access_token is None:
        return None

    try:
        url = 'https://discord.com/api/v10/users/@me/guilds'
        headers = {'Authorization': f'Bearer {access_token}'}
        # Reachable from a request thread (add-guild-from-invite view); keep short so a
        # slow Discord API can't hold a WSGI worker (defense in depth). The login path
        # calls this inline only for a stale profile, under a shrinking deadline.
        response = requests.get(url, headers=headers, timeout=timeout)

        if response.status_code == 200:
            return response.json()
        else:
            logger.warning("Failed to fetch guilds for user %s: %s %s", user, response.status_code, response.text)
            return None
    except Exception as e:
        logger.error("Error fetching guilds for user %s: %s", user, e)
        return None

def update_user_guilds(user, guilds):
    # guilds = get_user_guilds(user)
    if not guilds:
        return

    # Get existing guild IDs from the Discord API
    current_guild_ids = [g['id'] for g in guilds]

    # Clear and re-add only matching guilds that exist in DB
    # This will remove any guilds that were added via "mark_guild_invite_clicked"
    # if the user never actually joined the Discord server
    user.profile.guilds.clear()
    existing_guilds = DiscordGuild.objects.filter(guild_id__in=current_guild_ids)
    user.profile.guilds.add(*existing_guilds)

    # Mark approved invites as completed if user has actually joined the guild
    # Invites stay APPROVED if user clicked but never joined (so they can try again)
    from the_gatehouse.models import DiscordGuildJoinRequest
    approved_invites = DiscordGuildJoinRequest.objects.filter(
        profile=user.profile,
        status=DiscordGuildJoinRequest.Status.APPROVED,
        guild__in=existing_guilds
    )
    for invite in approved_invites:
        invite.complete()


def reconcile_tentative_membership(user, guild):
    """If the user has an APPROVED (not COMPLETED) invite for `guild` — i.e. they
    clicked 'Join Server' (optimistically granting access) but we haven't yet
    verified they really joined — re-check against Discord's real guild list and
    correct the record.

    Returns True if the user is in the guild after reconciliation, else False.
    No-op (returns None) when there's no pending APPROVED invite, so confirmed
    memberships incur no Discord API call.
    """
    from the_gatehouse.models import DiscordGuildJoinRequest

    if not user.is_authenticated:
        return None

    has_unverified_invite = DiscordGuildJoinRequest.objects.filter(
        profile=user.profile,
        guild=guild,
        status=DiscordGuildJoinRequest.Status.APPROVED,
    ).exists()
    if not has_unverified_invite:
        return None  # COMPLETED / none — trust cached profile.guilds, no API call

    guilds = get_user_guilds(user)
    if guilds is None:
        return None  # API failure — don't punish the user; leave as-is
    update_user_guilds(user, guilds)   # confirms (→COMPLETED) or removes phantom add
    return user.profile.guilds.filter(pk=guild.pk).exists()


def is_user_in_guild(user, guild_id):
    guilds = get_user_guilds(user)
    if guilds:
        for guild in guilds:
            if guild['id'] == guild_id:
                # print('User is in guild')
                return True
    # print("User is not in guild")
    return False


def derive_guild_membership(guilds):
    """Map an already-fetched Discord guild list to (in_ww, in_wr, in_fr).
    Pure/no network so callers that already have `guilds` (e.g. the async
    refresh task) don't hit the Discord API a second time."""
    in_ww = in_wr = in_fr = False
    if guilds:
        for guild in guilds:
            if guild['id'] == config['WW_GUILD_ID']:
                in_ww = True
            if guild['id'] == config['WR_GUILD_ID']:
                in_wr = True
            if guild['id'] == config['FR_GUILD_ID']:
                in_fr = True
    return in_ww, in_wr, in_fr


def check_user_guilds(user):
    guilds = get_user_guilds(user)
    update_user_guilds(user, guilds)
    return derive_guild_membership(guilds)


# Decorator
def woodland_warriors_required():
    guild_id = config['WW_GUILD_ID']
    def decorator(view_func):
        @login_required  # Ensure the user is authenticated
        def wrapper(request, *args, **kwargs):
            if is_user_in_guild(request.user, guild_id):
                return view_func(request, *args, **kwargs)  # Continue to the view
            else:
                raise PermissionDenied()   # 403 Forbidden
                # return render(request, 'the_gatehouse/not_verified.html')  # Redirect to home if not a member
        return wrapper
    return decorator




def apply_discord_category(category):

    webhook_url = ''
    embed_title = ''
    embed_color = ''
        # Set the webhook URL based on the category
    if category == 'feedback':
        webhook_url = config['DISCORD_FEEDBACK_WEBHOOK_URL']
        embed_title = "Feedback Received"
        embed_color = 0x00FF00  # Green color for feedback
    elif category == 'bug':
        webhook_url = config['DISCORD_FEEDBACK_WEBHOOK_URL']
        embed_title = "Bug Reported"
        embed_color = 0xFF0000  # Red color for report
    elif category == 'report':
        webhook_url = config['DISCORD_REPORTS_WEBHOOK_URL']
        embed_title = "Report Received"
        embed_color = 0xFF0000  # Red color for report
    elif category == 'request':
        webhook_url = config['DISCORD_FEEDBACK_WEBHOOK_URL']
        embed_title = "Request Received"
        embed_color = 0x0000FF  # Blue color for request
    elif category == 'weird-root' or category == 'french-root':
        webhook_url = config['DISCORD_REPORTS_WEBHOOK_URL']
        embed_title = "Invite Requested"
        embed_color = 0x9746c7  # Purple color for invite
    elif category == 'user_updates':
        webhook_url = config['DISCORD_NEW_USER_WEBHOOK_URL']
        embed_title = 'New User Registered'
        embed_color = 0xed3eed # Pink for new users
    elif category == 'New Post':
        webhook_url = config['DISCORD_NEW_POST_WEBHOOK_URL']
        embed_title = "Report Received"
        embed_color = 0x00FF00  # Green color for new
    elif category == 'New Game':
        webhook_url = config['DISCORD_NEW_GAME_WEBHOOK_URL']
        embed_title = "New Game Recorded"
        embed_color = 0xFF0000  # Red color for report
    elif category == 'FAQ Law':
        webhook_url = config['DISCORD_NEW_EDIT_WEBHOOK_URL']
        embed_color = 800080  # Red color for report
    elif category == 'Post Created':
        webhook_url = config['DISCORD_NEW_EDIT_WEBHOOK_URL']
        embed_title = "Post Created"
        embed_color = 0x00FF00  # Green color for new
    elif category == 'survey':
        webhook_url = config['DISCORD_NEW_EDIT_WEBHOOK_URL']
        embed_title = "Created"
        embed_color = 0xCF9FFF  # Light violet color for surveys
    elif category == 'Post Edited':
        webhook_url = config['DISCORD_NEW_EDIT_WEBHOOK_URL']
        embed_title = "Post Edited"
        embed_color = 0x00FF00  # Green color for new
    # Forge stuff
    elif category == 'forge-activity':
        webhook_url = config['DISCORD_FORGE_URL']
        embed_title = "Forged Faction"
        embed_color = 0xffa500  # Orange color for Forge
    elif category == 'forge-feedback':
        webhook_url = config['DISCORD_FEEDBACK_WEBHOOK_URL']
        embed_title = "Forge Feedback"
        embed_color = 0xffa500  # Orange, matches existing Forge category
    # Automations
    elif category == 'automation':
        webhook_url = config['DISCORD_AUTOMATIONS_WEBHOOK_URL']
        embed_title = "Automation"
        embed_color = 0x808080  # Grey color for unknown category
    elif category == 'rdl-import':
        webhook_url = config['DISCORD_AUTOMATIONS_WEBHOOK_URL']
        embed_title = "RDL Import"
        embed_color = 0xc7ef8e # Green
    elif category == 'rdl-update':
        webhook_url = config['DISCORD_AUTOMATIONS_WEBHOOK_URL']
        embed_title = "RDL Update"
        embed_color = 0xcbfbfd # Blue
    elif category == 'rdl-delete':
        webhook_url = config['DISCORD_AUTOMATIONS_WEBHOOK_URL']
        embed_title = "RDL Delete"
        embed_color = 0xf95965 # Red
    elif category == 'user-summary':
        webhook_url = config['DISCORD_AUTOMATIONS_WEBHOOK_URL']
        embed_title = "Daily User Summary"
        embed_color = 0xc29ce4 # Purple
    elif category == 'inactive-cleanup':
        webhook_url = config['DISCORD_AUTOMATIONS_WEBHOOK_URL']
        embed_title = "Inactive Cleanup"
        embed_color = 0xfd9651 # Orange

    # Other
    else:
        webhook_url = config['DISCORD_USER_EVENTS_WEBHOOK_URL']
        embed_title = "Activity"
        embed_color = 0x808080  # Grey color for unknown category

    return webhook_url, embed_title, embed_color


def post_interaction_followup(token, message_data):
    """POST a followup message to a Discord interaction's webhook. Must be called
    only AFTER the interaction's initial response (ACK) has reached Discord — a
    followup before the ACK returns 404 Unknown Webhook. The interaction token
    (not a bot token) authorizes this, so no auth header is needed. Raises
    requests.RequestException on network/HTTP failure so the task can retry.

    No DEBUG_VALUE guard: unlike the broadcast senders below, this is a live
    response to a user's interaction and must fire in every environment.

    UNUSED: its only caller was post_interaction_followup_task, which /draft and
    /random no longer use (they edit their public prompt into the result in place).
    Kept as the reusable raw webhook POST for any future flow that needs to send an
    additional message after an interaction's initial response.
    """
    response = requests.post(
        f"{DISCORD_API}/webhooks/{config['DISCORD_ID']}/{token}",
        json=message_data, timeout=10,
    )
    response.raise_for_status()


def send_discord_message(message, category=None):
    # Check if DEBUG is False in the config
    if config["DEBUG_VALUE"] == "True":
        return  # Do nothing if DEBUG is True

    webhook_url, _, _ = apply_discord_category(category=category)
    
    # Define the payload (message) to be sent
    payload = {
        'content': message,  # Message to be sent
    }

    # Send POST request to Discord webhook URL
    response = requests.post(webhook_url, json=payload, timeout=5)
    
    if response.status_code != 204:
        logger.error(
            "Discord webhook failed: status=%s body=%s url=%s",
            response.status_code, response.text[:200], webhook_url,
        )

def send_rich_discord_message(message, category=None, author_name=None, author_icon_url=None, title=None, color=None, fields=None, url=None):
    # Check if DEBUG is False in the config (uncomment this to test it)
    if config["DEBUG_VALUE"] == "True":
        return  # Do nothing if DEBUG is True
    
    webhook_url, embed_title, embed_color = apply_discord_category(category=category)

    # Base embed structure
    embed = {
        'description': message,
        'author': {
            'name': author_name,
            'icon_url': author_icon_url,
        },
        'title': embed_title,  # Title based on category
        'color': embed_color,  # Color based on category
    }

    # Add the title if provided
    if title:
        embed['title'] = title

    # Add a URL to make the title a clickable link (Discord renders the title
    # as a hyperlink only when both title and url are present)
    if url:
        embed['url'] = url

    # Add the color if provided (to override the default category color)
    if color:
        embed['color'] = color

    # Add fields if provided
    if fields:
        embed['fields'] = []
        for field in fields:
            embed['fields'].append({
                'name': field.get('name', 'Field Name'),
                'value': field.get('value', 'Field Value'),
                'inline': field.get('inline', False),  # Whether to display inline or not
            })

    # Payload to send to Discord
    payload = {
        # 'content': message,  # Removed because content is already in embed
        'embeds': [embed],  # Only one embed in this case
    }

    # Send POST request to Discord webhook URL
    response = requests.post(webhook_url, json=payload, timeout=5)
    
    if response.status_code != 204:
        logger.error(
            "Discord webhook failed",
            extra={
                'status_code': response.status_code,
                'response': response.text,
            }
        )



def _faction_fields(faction):
    """Faction/Clockwork-specific embed fields (Type)."""
    fields = []
    if faction.type and faction.type != faction.TypeChoices.UNKNOWN:
        fields.append({"name": "Type", "value": faction.get_type_display(), "inline": True})
    return fields


# Application-owned emoji for vagabond starting items, by item name. These
# render consistently in every server the app is installed in (unlike per-guild
# emoji). Keep names in sync with the `starting_<item>` fields on Vagabond.
VAGABOND_ITEM_EMOJI = {
    "torch": "<:torch:1518747305589604452>",
    "boots": "<:boots:1518747223708405770>",
    "coins": "<:coins:1518747264494080020>",
    "bag": "<:bag:1518747381947039874>",
    "tea": "<:tea:1518747327362371615>",
    "sword": "<:sword:1518747358547148800>",
    "hammer": "<:hammer:1518747399475036210>",
    "crossbow": "<:crossbow:1518747416763957298>",
}

# Cost item emoji, used for a vagabond's ability item. Keyed by
# the lowercased `ability_item` value. `other_flip` is the fallback for an
# ability not tied to a specific item.
VAGABOND_ABILITY_EMOJI = {
    "torch": "<:torch_flip:1518751223287644280>",
    "boots": "<:boots_flip:1518751288622448772>",
    "coins": "<:coins_flip:1518751176420626605>",
    "bag": "<:bag_flip:1518751193323802856>",
    "tea": "<:tea_flip:1518751209220214885>",
    "sword": "<:sword_flip:1518751239305822409>",
    "hammer": "<:hammer_flip:1518751254921084959>",
    "crossbow": "<:crossbow_flip:1518751270595199046>",
    "any": "<:any_flip:1518751311665954958>",
    "other": "<:other_flip:1518751160075550953>",
}
VAGABOND_ABILITY_OTHER_EMOJI = VAGABOND_ABILITY_EMOJI["other"]


# ── Law markup emoji ───────────────────────────────────────────────────────
# Law titles/descriptions embed icons as {{keyword}} (see the_keep.utils
# INLINE_ICON_MAP / text_filters.INLINE_IMAGES). We render each keyword as an
# application emoji fetched from Discord at runtime, so the ~30 emoji IDs don't
# have to be hardcoded. The emoji NAMES below must match the application emoji
# uploaded to the bot: items are prefixed `item` (itemtorch, itembag, …), while
# timing and faction icons use the bare keyword (cat, bird, daylight, …).
LAW_EMOJI_NAMES = {
    # items (note bag/sack and coin/coins share one emoji)
    "torch": "itemtorch",
    "tea": "itemtea",
    "sword": "itemsword",
    "bag": "itembag",
    "sack": "itembag",
    "hammer": "itemhammer",
    "crossbow": "itemcrossbow",
    "coins": "itemcoin",
    "coin": "itemcoin",
    "boot": "itemboot",
    # timing / triggers
    "hired": "hired",
    "ability": "ability",
    "daylight": "daylight",
    "birdsong": "birdsong",
    # factions (aliases point at one canonical emoji)
    "cat": "cat",
    "bird": "bird",
    "bunny": "bunny",
    "rabbit": "bunny",
    "mouse": "bunny",
    "rat": "rat",
    "raccoon": "raccoon",
    "vb": "raccoon",
    "otter": "otter",
    "mole": "mole",
    "lizard": "lizard",
    "crow": "crow",
    "frog": "frog",
    "bat": "bat",
    "skunk": "skunk",
}

# ── Official faction emoji ─────────────────────────────────────────────────
# The 13 official factions each have a bot application emoji whose name ends in
# "100". Keyed by faction slug (the stable identity — animal/status vary and are
# ambiguous). Used to prefix faction names in the /stats leaderboards. Emoji are
# rendered via faction_emoji_for(); a missing upload just yields no prefix.
FACTION_EMOJI_NAMES = {
    "keepers-in-iron": "badger100",
    "twilight-council": "bat100",
    "marquise-de-cat": "cat100",
    "corvid-conspiracy": "crow100",
    "underground-duchy": "duchy100",
    "eyrie-dynasties": "eyrie100",
    "lilypad-diaspora": "frog100",
    "knaves-of-the-deepwood": "knaves100",
    "lizard-cult": "lizard100",
    "lord-of-the-hundreds": "loth100",
    "riverfolk-company": "otter100",
    "vagabond": "vb100",
    "woodland-alliance": "wa100",
}

# Human-friendly label for a LawGroup.type, shown as a sub-header in the embed.
LAW_GROUP_TYPE_LABELS = {
    "Official": "Law of Root",
    "Bot": "Law of Rootbotics",
    "Fan": "Fan Content",
    "Appendix": "Law of Root",
}


_APP_EMOJI = None  # cache: {emoji_name: "<:name:id>"}, populated once per process


def _fetch_application_emoji():
    """Fetch the bot's application-owned emoji from Discord, returning a
    {name: "<:name:id>"} map (animated emoji use the "<a:name:id>" form).
    Returns {} on any failure (network, auth, unexpected shape)."""
    try:
        url = f"{DISCORD_API}/applications/{config['DISCORD_ID']}/emojis"
        response = requests.get(url, headers=_bot_headers(), timeout=10)
        response.raise_for_status()
        items = response.json().get("items", [])
    except (requests.RequestException, ValueError, KeyError):
        logger.exception("Failed to fetch application emoji")
        return {}

    emoji_map = {}
    for item in items:
        name = item.get("name")
        emoji_id = item.get("id")
        if not name or not emoji_id:
            continue
        prefix = "a" if item.get("animated") else ""
        emoji_map[name] = f"<{prefix}:{name}:{emoji_id}>"
    return emoji_map


def get_application_emoji():
    """Lazily fetch and cache the application emoji map for this process."""
    global _APP_EMOJI
    if _APP_EMOJI is None:
        _APP_EMOJI = _fetch_application_emoji()
    return _APP_EMOJI


def law_emoji_for(keyword):
    """Return the application-emoji string for a law {{keyword}}, or "" if the
    keyword is unknown or its emoji hasn't been uploaded (icon is then dropped)."""
    name = LAW_EMOJI_NAMES.get(keyword)
    if not name:
        return ""
    return get_application_emoji().get(name, "")


def faction_emoji_for(slug):
    """Return the application-emoji string for an official faction slug, or "" if
    the slug isn't one of the 13 official factions or its emoji hasn't been
    uploaded (the name is then shown without an icon prefix)."""
    name = FACTION_EMOJI_NAMES.get(slug)
    if not name:
        return ""
    return get_application_emoji().get(name, "")


_EMOJI_RE = re.compile(r"<a?:(?P<name>\w+):(?P<id>\d+)>")


def parse_emoji_object(emoji_str):
    """Turn a '<:name:id>' / '<a:name:id>' string into a Discord component emoji
    object {'id','name'[, 'animated']}, or None if empty/unparseable. Component
    emoji (select options, buttons) need this object form; message content uses
    the raw string."""
    if not emoji_str:
        return None
    m = _EMOJI_RE.match(emoji_str)
    if not m:
        return None
    obj = {"id": m.group("id"), "name": m.group("name")}
    if emoji_str.startswith("<a:"):
        obj["animated"] = True
    return obj


def faction_emoji_object(slug):
    """Component-emoji object for an official faction slug, or None."""
    return parse_emoji_object(faction_emoji_for(slug))


def vagabond_emoji_for(vagabond):
    """Return the application-emoji string for a Vagabond, or "" if its emoji
    hasn't been uploaded. The bot's vagabond meeple emoji are named "Meeple"
    followed by the vagabond's title with spaces removed (e.g. "MeepleThief")."""
    title = getattr(vagabond, "title", "") or ""
    name = "Meeple" + title.replace(" ", "")
    return get_application_emoji().get(name, "")


def suit_emoji_for(suit, variant):
    """Return the application-emoji string for a Root suit, or "" if not uploaded.
    `variant` is "card" or "icon"; emoji are named "{suit}_{variant}" lowercased
    (e.g. "fox_card", "mouse_icon"). Note only Mouse/Fox/Rabbit have an "icon"
    (clearing) form — there is no bird clearing."""
    name = f"{suit.lower()}_{variant}"
    return get_application_emoji().get(name, "")


def suit_static_image_url(suit, variant):
    """Absolute URL to a Root suit's static inline image, or None when SITE_URL
    isn't configured. `variant` is "tilt" (suit card art) or "outline" (clearing
    art); files live at static/pdf/inline/{suit}_{variant}.png (lowercased). Must
    be absolute for Discord, so we prefix SITE_URL like the other embed images."""
    site_url = config.get("SITE_URL", "").rstrip("/")
    if not site_url:
        return None
    return f"{site_url}{static(f'pdf/inline/{suit.lower()}_{variant}.png')}"


def roll_emoji_for(value):
    """Return the application-emoji string for a die face 0-3, or "" if not
    uploaded. Emoji are named "roll_{value}" (e.g. "roll_2")."""
    return get_application_emoji().get(f"roll_{value}", "")


def _item_emoji_value(vagabond, prefix):
    """Emoji string for a vagabond's item counts, repeating each emoji by its
    count. `prefix` is the field prefix, e.g. "starting" or "captain"."""
    parts = []
    for item, emoji_str in VAGABOND_ITEM_EMOJI.items():
        count = getattr(vagabond, f"{prefix}_{item}", 0) or 0
        parts.append(emoji_str * count)
    return "".join(parts)


def _vagabond_fields(vagabond):
    fields = []
    # Ability: the ability name is the field title; the value is the ability
    # item's (flipped) emoji, an arrow, then the description. Falls back to the
    # "other" emoji when the ability isn't tied to a specific item.

    items_value = _item_emoji_value(vagabond, "starting")
    if items_value:
        fields.append({"name": "Starting Items", "value": items_value, "inline": False})

    if vagabond.ability:
        emoji_str = VAGABOND_ABILITY_EMOJI.get(
            (vagabond.ability_item or "").lower(), VAGABOND_ABILITY_OTHER_EMOJI
        )
        value = f"{emoji_str} → {vagabond.ability_description}" if vagabond.ability_description else emoji_str
        fields.append({"name": vagabond.ability, "value": value, "inline": False})

    return fields


def _hireling_fields(hireling):
    fields = []
    if hireling.type:
        fields.append({"name": "Type", "value": hireling.get_type_display(), "inline": True})
    return fields


def _component_fields(post):
    """Return the subclass-specific embed fields for a Post, by component type."""
    component = getattr(post, "component", None)
    if component == "Faction":
        return _faction_fields(post)
    if component == "Vagabond":
        return _vagabond_fields(post)
    if component == "Hireling":
        return _hireling_fields(post)
    if component == "Deck":
        return [{"name": "Cards", "value": str(post.card_total), "inline": True}]
    if component == "Map":
        return [{"name": "Clearings", "value": str(post.clearings), "inline": True}]
    if component == "Landmark" and getattr(post, "card_text", None):
        return [{"name": "Card Text", "value": post.card_text, "inline": False}]
    return []


def embed_color(obj):
    """Discord embed color (int) from an object's "#RRGGBB" `color` string, or
    None when unset/malformed."""
    color = getattr(obj, "color", None)
    if not color:
        return None
    try:
        return int(color.lstrip("#"), 16)
    except (ValueError, AttributeError):
        return None


_embed_color = embed_color  # internal alias (kept for existing call sites)


# Discord embed limits — exceeding any of these makes the API reject the whole
# message with a 400, so we clamp user-controlled text (descriptions, field
# values from card_text/abilities/etc.) before sending.
_EMBED_TITLE_MAX = 256
_EMBED_DESC_MAX = 4096
_EMBED_FIELD_NAME_MAX = 256
_EMBED_FIELD_VALUE_MAX = 1024


def _truncate(text, limit):
    """Clamp `text` to `limit` chars, ending with an ellipsis when cut."""
    if text is None or len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _enforce_embed_limits(embed):
    """Clamp an embed dict's title/description/field text to Discord's per-field
    limits in place, so a long post (card_text, description, ability text) can't
    make Discord 400 the whole message. Returns the same dict for chaining."""
    if "title" in embed:
        embed["title"] = _truncate(embed["title"], _EMBED_TITLE_MAX)
    if "description" in embed:
        embed["description"] = _truncate(embed["description"], _EMBED_DESC_MAX)
    for field in embed.get("fields", []):
        field["name"] = _truncate(field.get("name", ""), _EMBED_FIELD_NAME_MAX)
        field["value"] = _truncate(field.get("value", ""), _EMBED_FIELD_VALUE_MAX)
    return embed


# Which image field to show as the small thumbnail, by component. A component
# absent here (Deck, Vagabond, Hireling) gets no thumbnail. Tweak is special-cased
# in _post_thumbnail_url (its thumbnail only applies when it also has a big board
# image). The field is resolved per-post; a component whose field is empty on a
# given post simply gets no thumbnail.
_THUMBNAIL_IMAGE_FIELD = {
    "Faction": "card_image",
    "Clockwork": "card_image",
    "Map": "card_image",
    "Landmark": "card_2_image",
}


def _resolve_image_url(post, field):
    """Absolute URL for one of a post's image fields, or None when the field is
    empty/unset or SITE_URL isn't configured."""
    site_url = config.get("SITE_URL", "").rstrip("/")
    if not site_url or not field:
        return None
    image_url = post.get_translated_image_url(field)
    return f"{site_url}{image_url}" if image_url else None


def _post_thumbnail_url(post):
    """The thumbnail image url for a post's embed, per component, or None.
    Tweak (house rule): only show a card_image thumbnail when the post ALSO has a
    board_image (which becomes the big image); a card-image-only Tweak shows that
    card as the big image and no thumbnail."""
    component = getattr(post, "component", None)
    if component == "Tweak":
        if _resolve_image_url(post, "board_image"):
            return _resolve_image_url(post, "card_image")
        return None
    return _resolve_image_url(post, _THUMBNAIL_IMAGE_FIELD.get(component))


def build_post_embed(post):
    """Build a Discord embed dict for any Post (faction, map, deck, etc.).

    Shared fields (title, link, description, color, thumbnail, designer) come
    from the base Post; subclass-specific fields are added per component type.
    """
    site_url = config.get("SITE_URL", "").rstrip("/")

    embed = {
        "title": post.title,
        "url": f"{site_url}{post.get_absolute_url()}" if site_url else None,
        "description": post.description or post.lore or "",
        "color": _embed_color(post),
    }

    # Small thumbnail: a per-component card image (Faction/Clockwork/Map ->
    # card_image, Landmark -> card_2_image, Tweak -> card_image when it also has a
    # board image; Deck/Vagabond/Hireling -> none). Omitted when the post's field
    # is empty.
    thumbnail_url = _post_thumbnail_url(post)
    if thumbnail_url:
        embed["thumbnail"] = {"url": thumbnail_url}

    fields = []
    if post.designer:
        fields.append({"name": "Designer", "value": post.designer.display_name or "—", "inline": True})
    if getattr(post, "based_on", None):
        fields.append({"name": "Based on", "value": post.based_on.title, "inline": True})
    fields.extend(_component_fields(post))

    if fields:
        embed["fields"] = fields

    # Drop None values Discord would reject, then clamp text to Discord's limits.
    return _enforce_embed_limits({k: v for k, v in embed.items() if v is not None})


# Back-compat alias: the embed builder is now generic over all Post types.
build_faction_embed = build_post_embed


# Which large image to show as a standalone follow-up embed, by component. Board
# components show their board; card components show their card. Anything absent
# here (e.g. Tweak) gets no standalone image.
_STANDALONE_IMAGE_FIELD = {
    "Faction": "board_image",
    "Clockwork": "board_image",
    "Map": "board_image",
    "Hireling": "board_image",
    "Vagabond": "card_image",
    "Landmark": "card_image",
    "Deck": "card_image",
}


def build_post_image_embed(post, language=None, field=None):
    """Build a second, image-only embed for a Post's board or card image, so it
    renders as a large standalone (click-to-enlarge) image after the main embed.

    `field` overrides the per-component image (e.g. "card_2_image" for a captain's
    flip side); otherwise it's chosen from the post's component. Returns None when
    there's no image for this post or the file is missing/unresolvable. The URL is
    only resolvable on the public domain.
    """
    site_url = config.get("SITE_URL", "").rstrip("/")
    if not site_url:
        return None

    component = getattr(post, "component", None)
    if not field and component == "Tweak":
        # House rule big image: prefer the board image, fall back to the card.
        field = "board_image" if post.get_translated_image_url("board_image", language) else "card_image"
    field = field or _STANDALONE_IMAGE_FIELD.get(component)
    if not field:
        return None

    image_url = post.get_translated_image_url(field, language)
    if not image_url:
        return None

    # An image-only embed renders as a large, click-to-enlarge standalone image.
    # We deliberately omit `url`: sharing the main embed's url would make Discord
    # merge the two into one gallery card instead of a separate image below. The
    # color matches the main embed so the pair reads as one unit.
    embed = {"image": {"url": f"{site_url}{image_url}"}}
    color = _embed_color(post)
    if color is not None:
        embed["color"] = color
    return embed


# ── Law embeds ─────────────────────────────────────────────────────────────
def format_law_for_discord(text):
    """Convert stored law markup into embed-safe text.

    Mirrors the site's `format_law_text` (the_keep/templatetags/text_filters.py)
    but targets Discord rather than HTML:
      {{keyword}}  -> application emoji (dropped if unavailable)
      **TEXT**     -> **UPPERCASE** (Discord has no small-caps; bold caps is closest)
      _text_       -> *text* (Discord italics)
      markdown tables -> flattened to plain text (Discord embeds can't render them)
    """
    if not text:
        return ""

    text = str(text)

    # {{keyword}} -> emoji (drop unknown/unuploaded)
    text = re.sub(
        r"\{\{\s*(\w+)\s*\}\}",
        lambda m: law_emoji_for(m.group(1)),
        text,
    )

    # **TEXT** (small-caps intent) -> bold uppercase
    text = re.sub(
        r"\*\*([^\*]+)\*\*",
        lambda m: f"**{m.group(1).upper()}**",
        text,
    )

    # _text_ -> *text* (italics)
    text = re.sub(r"_(.+?)_", lambda m: f"*{m.group(1)}*", text)

    # Flatten markdown tables: drop separator rows, turn pipes into spaces.
    lines = []
    for line in text.splitlines():
        if re.match(r"^\s*\|?\s*:?-{2,}", line) and set(line.strip()) <= set("|-: "):
            continue  # table separator row like |---|:--:|
        lines.append(line.replace("|", " ").strip())
    text = "\n".join(lines)

    # Drop backslash escapes before parentheses, like replace_special_references.
    text = text.replace(r"\(", "(").replace(r"\)", ")")

    return text.strip()


def format_law_title_for_discord(text):
    """Like `format_law_for_discord`, but for an embed *title*.

    Discord embed titles render custom emoji but NOT markdown, so {{keyword}}
    becomes an emoji while **bold**/_italics_ markup is stripped to plain text
    (leaving the asterisks/underscores would show literally in the title).
    """
    if not text:
        return ""

    text = str(text)

    # {{keyword}} -> emoji (drop unknown/unuploaded), same as the body.
    text = re.sub(
        r"\{\{\s*(\w+)\s*\}\}",
        lambda m: law_emoji_for(m.group(1)),
        text,
    )

    # **bold** / _italics_ -> plain text (titles can't render either).
    text = re.sub(r"\*\*([^\*]+)\*\*", lambda m: m.group(1), text)
    text = re.sub(r"_(.+?)_", lambda m: m.group(1), text)

    text = text.replace(r"\(", "(").replace(r"\)", ")")
    return text.strip()


def _law_author_breadcrumb(law, prime, group):
    """Build the embed author line for a law: the group's prime-law title, plus the
    selected law's immediate parent when that parent isn't the prime law itself,
    e.g. "Vagabond - Improving Relationships".

    Only the direct parent is named, at any depth. The law's own title is already
    the embed title, so a direct child of the prime law adds nothing and returns
    just the prime — which is also why no marker is needed for elided levels.

    Titles use plain_title since the author line can't render markup/emoji.
    """
    def label(node):
        return ((node.plain_title or node.title) or "").strip()

    base = label(prime) if prime else (group.title or str(group)).strip()

    parent = law.parent
    if parent is None or parent.prime_law:
        return base
    crumb = label(parent)
    if not crumb:
        return base
    return f"{base} - {crumb}" if base else crumb


def build_law_embed(law):
    """Build a Discord embed dict for a single Law.

    The embed links back to the law on the site, renders {{keyword}} icons as
    application emoji in the body, and shows the law group's prime-law title (in
    the law's language) as the author, with the group's post icon — or the static
    law icon when the group has no post.
    """
    site_url = config.get("SITE_URL", "").rstrip("/")
    group = law.group
    post = group.post

    # Use the raw title (not plain_title) so {{keyword}} markup survives to be
    # rendered as emoji in the embed title; bold/italics markup is stripped.
    raw_title = (law.title or law.plain_title or "").strip()
    title = f"{law.law_code} {raw_title}".strip() if law.law_code else raw_title
    title = format_law_title_for_discord(title)[:256]

    embed = {
        "title": title or "Law",
        "url": f"{site_url}{law.get_absolute_url()}" if site_url else None,
        "description": format_law_for_discord(law.description)[:4096] or None,
    }

    # Footer: the kind of law collection this belongs to (e.g. "Law of Root",
    # "Fan Content"), kept out of the body so it doesn't break up the content.
    type_label = LAW_GROUP_TYPE_LABELS.get(group.type)
    if type_label:
        embed["footer"] = {"text": type_label}

    # color from the group's post, if any
    if post and getattr(post, "color", None):
        try:
            embed["color"] = int(post.color.lstrip("#"), 16)
        except (ValueError, AttributeError):
            pass

    # Author: the prime law title of the group (in this language), followed by the
    # law's immediate parent when that isn't the prime law itself — so e.g.
    # 9.2.9.Ia reads "Vagabond - Improving Relationships".
    prime = group.get_prime_law(law.language)
    author_name = _law_author_breadcrumb(law, prime, group) or "Law"
    author = {"name": author_name[:256]}
    if site_url:
        icon_path = None
        if post and getattr(post, "small_icon", None):
            try:
                icon_path = post.small_icon.url
            except ValueError:
                icon_path = None
        if not icon_path:
            icon_path = static("images/law-icon-square.png")
        author["icon_url"] = f"{site_url}{icon_path}"
    embed["author"] = author

    return {k: v for k, v in embed.items() if v is not None}


def build_card_embed(card):
    """Build a Discord embed dict for a single Card (a row in a DeckGroup, not a
    Post). Title links to the card; body is the card text; the footer lists the
    published post(s) a card of this name appears in (up to two, then "+X more");
    color comes from the card's first tag, falling back to the source post's."""
    from the_keep.models import Post, CardTag

    site_url = config.get("SITE_URL", "").rstrip("/")
    post = card.group.post

    embed = {
        "title": card.name or "Card",
        "url": f"{site_url}{card.get_absolute_url()}" if site_url else None,
        "description": card.text or None,
    }

    # Footer: the published posts a card of this NAME appears in. Fetch the order
    # columns and sort/dedupe in Python — combining .distinct() with an order_by on
    # official/status while selecting only title raises a Postgres "SELECT DISTINCT
    # ... ORDER BY must appear in select list" error.
    if card.name:
        rows = (
            Post.objects.filter(decks__cards__name=card.name, status__lte=4)
            .values_list("title", "official", "status")
            .distinct()
        )
        # official desc (True first), then status asc ('1' before '2').
        rows = sorted(rows, key=lambda r: (not r[1], r[2]))
        seen, titles = set(), []
        for title, _official, _status in rows:
            if title not in seen:
                seen.add(title)
                titles.append(title)
    else:
        titles = [post.title] if post else []
    if titles:
        footer = ", ".join(titles[:2])
        if len(titles) > 2:
            footer += f" +{len(titles) - 2} more"
        embed["footer"] = {"text": footer}

    # Color: the card's first tag (via the site-wide CardTag.hex_for), else the
    # source post's color. tags can be None (JSONField null=True), so guard it.
    first_tag = (card.tags or [None])[0]
    tag_hex = CardTag.hex_for(first_tag)
    color = int(tag_hex.lstrip("#"), 16) if tag_hex else embed_color(post)
    if color is not None:
        embed["color"] = color

    # Card image (plain ImageField — not get_translated_image_url, a Post method).
    try:
        img = card.front_image.url
    except ValueError:
        img = None
    if site_url and img:
        embed["image"] = {"url": f"{site_url}{img}"}

    return _enforce_embed_limits({k: v for k, v in embed.items() if v is not None})


def build_captain_embed(vagabond):
    """Build a Discord embed for a vagabond's captain (Advanced) profile:
    captain ability and captain starting items, rather than the base ones."""
    site_url = config.get("SITE_URL", "").rstrip("/")

    embed = {
        "title": vagabond.title,
        "url": f"{site_url}{vagabond.get_absolute_url()}" if site_url else None,
        "description": vagabond.description or vagabond.lore or "",
        "color": _embed_color(vagabond),
    }
    # No thumbnail: captains (like vagabonds) intentionally show no thumbnail.

    fields = []
    if vagabond.designer:
        fields.append({"name": "Designer", "value": vagabond.designer.display_name or "—", "inline": True})
    if vagabond.captain_ability:
        fields.append({"name": "Captain Ability", "value": vagabond.captain_ability, "inline": False})

    items_value = _item_emoji_value(vagabond, "captain")
    if items_value:
        fields.append({"name": "Starting Items", "value": items_value, "inline": False})

    if fields:
        embed["fields"] = fields

    return _enforce_embed_limits({k: v for k, v in embed.items() if v is not None})


def build_stats_embed(stats, *, player=None, faction=None, tournament=None, platform=None, include_fan_content=False, elo_participant=None):
    """Build a Discord embed dict for a /stats win-rate result.

    `stats` is the dict from filtered_winrate (total, games, win_points, win_rate).
    The remaining args are the resolved filter objects (or None) used to label
    the result and, when a single subject is in focus, link/thumbnail it.
    include_fan_content: when False (default), the faction board excludes
    unofficial (fan-made) factions.
    elo_participant: the player's EloParticipant for the series' Elo system, when a
    player + series (with a system) are in focus and no faction is selected. When
    given, its rating/rank lead the fields and its icon_url/bg_color drive the
    thumbnail/color (mutually exclusive with the faction thumbnail/color).
    """
    site_url = config.get("SITE_URL", "").rstrip("/")

    # Human-readable filter summary
    parts = []
    if player:
        parts.append(f"Player: {player.display_name or player.discord}")
    if faction:
        parts.append(f"Faction: {faction.title}")
    if tournament:
        parts.append(f"Series: {tournament.name}")
    if platform:
        parts.append(f"Platform: {platform}")
    description = " · ".join(parts) if parts else "All games"

    # Win Rate / Wins are only meaningful when scoped to a player or faction;
    # for an unscoped query they'd be an aggregate over everything, so show only
    # the Games count (the leaderboards below carry the per-subject rates).
    fields = [{"name": "Games", "value": str(stats['games']), "inline": True}]
    if player or faction:
        fields.insert(0, {"name": "Win Rate", "value": f"{stats['win_rate']:.1f}%", "inline": True})
        fields.append({"name": "Wins", "value": f"{stats['win_points']:g}", "inline": True})

    # Elo standing leads the fields when a participant is in focus. Two insert(0)
    # calls reverse order, so add Rank first then Rating to keep Rating leftmost.
    # rank/icon_url/bg_color are only populated for RootELO systems; LOCAL systems
    # show the rating with an "Unranked" label and no thumbnail/color.
    if elo_participant:
        rank = elo_participant.rank
        rank_display = f"#{rank}" if rank else "Unranked"
        fields.insert(0, {"name": "Rank", "value": rank_display, "inline": True})
        fields.insert(0, {"name": elo_participant.elo_system.name,
                          "value": f"{round(elo_participant.rating)}", "inline": True})

    embed = {
        "title": "Win Rate",
        "description": description,
        "fields": fields,
    }

    def _absolute(subject):
        """Site-absolute URL for a subject, or None."""
        if not site_url:
            return None
        try:
            return f"{site_url}{subject.get_absolute_url()}"
        except Exception:
            return None

    def _image_url(subject):
        """Site-absolute URL of a subject's image, or None."""
        if not site_url:
            return None
        image = getattr(subject, "picture", None) or getattr(subject, "image", None)
        if not image:
            return None
        try:
            return f"{site_url}{image.url}"
        except ValueError:
            return None

    if faction and faction.color:
        try:
            embed["color"] = int(faction.color.lstrip("#"), 16)
        except (ValueError, AttributeError):
            pass

    # Elo only appears with no faction, so this never competes with the faction
    # color above. Mask to 24 bits since bg_color may carry an 8-digit #RRGGBBAA.
    if elo_participant and elo_participant.bg_color:
        try:
            embed["color"] = int(elo_participant.bg_color.lstrip("#"), 16) & 0xFFFFFF
        except (ValueError, AttributeError):
            pass

    # Player gets the author slot (icon + name + link); faction gets the
    # thumbnail. Either may be present alone, or both together.
    if player:
        author = {"name": player.display_name or player.discord or "Player"}
        player_url = _absolute(player)
        if player_url:
            author["url"] = player_url
        player_image = _image_url(player)
        if player_image:
            author["icon_url"] = player_image
        embed["author"] = author

    if faction:
        faction_image = _image_url(faction)
        if faction_image:
            embed["thumbnail"] = {"url": faction_image}

    # icon_url is a full external URL (from the RootELO feed), so it bypasses the
    # site-relative _image_url helper. Only reached when no faction is present.
    if elo_participant and elo_participant.icon_url:
        embed["thumbnail"] = {"url": elo_participant.icon_url}

    # When only one subject is in focus, also link the embed title to it.
    subject = player if (player and not faction) else (faction if (faction and not player) else None)
    if subject is not None:
        subject_url = _absolute(subject)
        if subject_url:
            embed["url"] = subject_url

    def _leaderboard_field(name, rows, *, with_emoji=False):
        """Append a numbered leaderboard field from {title, win_rate,
        total_efforts, url, slug} rows. No-op when there are no rows."""
        if not rows:
            return
        lines = []
        for i, r in enumerate(rows, 1):
            url = f"{site_url}{r['url']}" if (site_url and r.get("url")) else None
            label = f"[{r['title']}]({url})" if url else r["title"]
            emoji = faction_emoji_for(r.get("slug")) if with_emoji else ""
            prefix = f"{emoji} " if emoji else ""
            lines.append(f"{i}. {prefix}{label} — {r['win_rate']:.1f}% ({r['total_efforts']})")
        embed["fields"].append({"name": name, "value": "\n".join(lines), "inline": False})

    # Top factions / players over the same filtered efforts, each omitting the
    # board that a single-subject filter already narrows to. With no filters at
    # all — or only a platform — use the pre-computed cached global boards
    # (overall, or that platform's per-platform fields) instead of aggregating live.
    effort_qs = stats.get("qs")
    cached_only = not (player or faction or tournament)
    if effort_qs is not None:
        if cached_only:
            from the_warroom.services.winrate_service import (
                cached_top_factions, cached_top_players, cached_threshold,
            )
            _leaderboard_field(
                "Top Factions",
                cached_top_factions(limit=5, platform=platform, include_fan_content=include_fan_content),
                with_emoji=True)
            _leaderboard_field("Top Players", cached_top_players(limit=5, platform=platform))
            # Footer names the qualifying-plays cutoff the cached boards used.
            threshold = cached_threshold(platform)
            embed["footer"] = {"text": f"Leaderboard threshold of {threshold}"}
        else:
            # leaderboard() returns site-relative 'url's; a low threshold so
            # narrow filters still surface something.
            if not faction:
                from the_keep.models import Faction
                _leaderboard_field(
                    "Top Factions",
                    Faction.leaderboard(effort_qs, limit=5, game_threshold=2, as_json=True,
                                        include_fan_content=include_fan_content),
                    with_emoji=True,
                )
            if not player:
                from the_gatehouse.models import Profile
                _leaderboard_field(
                    "Top Players",
                    Profile.leaderboard(effort_qs, limit=5, game_threshold=2, as_json=True),
                )

    return {k: v for k, v in embed.items() if v is not None}


# Distinguishes "caller didn't pass a summary" (use the /upcoming wording) from
# an explicit summary=None (drop the description). A plain None default can't.
_UNSET = object()


def _upcoming_summary(series, player):
    """One-line summary naming the active /upcoming filters, e.g.
    "The next scheduled Brand New Series game for MrMirz". Drops whichever
    parts weren't filtered ("The next scheduled game" with neither).

    `series` and `player` are the filters the user actually supplied — not
    derived from the match — so an unfiltered search reads "The next scheduled
    game" even though the resulting match belongs to some tournament."""
    series_part = f" {series.name}" if series else ""
    player_name = (player.display_name or player.discord or player.slug) if player else None
    player_part = f" for {player_name}" if player_name else ""
    return f"The next scheduled{series_part} game{player_part}"


def build_upcoming_embed(match, series=None, player=None, summary=_UNSET):
    """Build a Discord embed for a scheduled match.

    Links to the matches page that contains the match (via
    Match.get_matches_url, which adapts to the tournament's stage/round layout),
    lists the players in the match, and shows the platform only when the
    tournament requires one (tournament.platform is set).

    `series` and `player` are the optional filters the /upcoming result was
    narrowed by (each None when not supplied); a summary line names them, e.g.
    "The next scheduled Brand New Series game for MrMirz". They reflect the
    user's filters, not the match — so an unfiltered search omits the series
    even though the match belongs to a tournament.

    `summary` overrides that description for callers that aren't /upcoming.
    /schedule announces the match it just wrote, which isn't necessarily the
    *next* one in the tournament, so it passes its own line (or None to drop
    the description entirely). Left unset, the /upcoming wording is used.
    """
    site_url = config.get("SITE_URL", "").rstrip("/")
    round = match.round
    tournament = round.get_tournament()

    embed = {
        "title": match.name or "Upcoming Match",
        "url": f"{site_url}{match.get_matches_url()}" if site_url else None,
        "description": (_upcoming_summary(series, player)
                        if summary is _UNSET else summary),
    }
    if tournament:
        embed["author"] = {"name": tournament.name}

    fields = []

    # Localized per viewer; see format_discord_timestamp.
    if match.scheduled_time:
        fields.append({
            "name": "Scheduled",
            "value": format_discord_timestamp(match.scheduled_time),
            "inline": False,
        })

    # Players in the match, from the seated participants (MatchSeat records).
    # Seats are the authoritative source once a game is scheduled; the player
    # group's M2M members isn't always populated for a specific series.
    from the_warroom.models import MatchSeat
    names = []
    if match.series_id:
        seats = MatchSeat.objects.filter(series_id=match.series_id).select_related(
            'stage_participant__tournament_player__profile'
        ).order_by('seat_number')
        names = [
            (p.display_name or p.discord or p.slug or "—")
            for p in (seat.stage_participant.tournament_player.profile for seat in seats)
        ]
    if names:
        fields.append({"name": "Players", "value": "\n".join(names), "inline": False})
    else:
        fields.append({"name": "Players", "value": "TBD", "inline": False})

    # Platform only when the tournament requires one. The stored value is already
    # the human-readable label (e.g. "In Person"), matching how /stats treats it.
    if tournament and tournament.platform:
        fields.append({
            "name": "Platform",
            "value": tournament.platform,
            "inline": True,
        })

    embed["fields"] = fields
    return {k: v for k, v in embed.items() if v is not None}


def build_help_embed(enabled_names=None, guild_id=None, can_manage=False):
    """Build a Discord embed listing the bot's commands, grouped by category.

    Driven by the shared command definitions (the_gatehouse.services.
    discord_commands), so any command registered with Discord automatically
    appears here. Imported inside the function to avoid an import cycle
    (discord_commands imports models that pull in this package).

    When `enabled_names` is given (a guild's whitelist), only the commands actually
    available in that server are listed: /help (always available) plus the enabled,
    whitelistable commands. Empty groups are dropped. Pass `enabled_names=None` (the
    default, e.g. in DMs) to list every command unfiltered.

    A single useful link is always appended: a server manager (`can_manage`, in a guild)
    gets a link to that guild's command settings; everyone else (including in DMs) gets a
    link to add the bot to their own server.
    """
    from the_gatehouse.services.discord_commands import grouped_commands

    site_url = config.get("SITE_URL", "").rstrip("/")

    # None → list everything (no guild context). Otherwise a command is shown only if it's
    # /help or in the guild's whitelist.
    if enabled_names is None:
        def is_available(name):
            return True
    else:
        enabled = set(enabled_names)
        def is_available(name):
            return name == "help" or name in enabled

    fields = []
    for group_name, rows in grouped_commands():
        shown = [(name, desc) for name, desc in rows if is_available(name)]
        if not shown:
            continue
        value = "\n".join(f"`/{name}` — {desc}" for name, desc in shown)
        fields.append({"name": group_name, "value": value, "inline": False})

    embed = {
        "title": "Bot Commands",
        "description": "Here are the commands you can use:",
        "fields": fields,
        # Title links to the bot's own page rather than the site homepage — someone
        # reading /help wants to know about the bot.
        "url": f"{site_url}/databot/" if site_url else None,
    }

    # Always offer one useful link. A server manager gets a link to this guild's command
    # settings; anyone else (including in DMs, where there's no guild/manage context) gets
    # a link to add the bot to their own server.
    if site_url:
        if enabled_names is not None and can_manage and guild_id:
            manage_url = f"{site_url}/guild/{guild_id}/edit/"
            embed["fields"].append({
                "name": "Manage this server",
                "value": f"[Manage commands for this server]({manage_url})",
                "inline": False,
            })
        else:
            databot_url = f"{site_url}/databot/"
            embed["fields"].append({
                "name": "Add the Databot",
                "value": f"[Add the Databot to your server]({databot_url})",
                "inline": False,
            })

    return {k: v for k, v in embed.items() if v is not None}


# [label](url-name) in the shared LFG copy. The target is a Django URL name, never a
# path, so the copy never hardcodes a URL.
_LFG_LINK_RE = re.compile(r"\[([^\]]+)\]\(([a-z0-9-]+)\)")


def _expand_lfg_markdown(text, site_url):
    """Turn [label](url-name) into a real markdown link. Backticks and *italics* pass
    through untouched — Discord renders both natively. Without a site_url the link
    degrades to its plain label rather than emitting a broken relative link, matching how
    build_help_embed drops its link fields in that case."""
    def sub(match):
        label, url_name = match.group(1), match.group(2)
        if not site_url:
            return label
        return f"[{label}]({site_url}{reverse(url_name)})"
    return _LFG_LINK_RE.sub(sub, text)


def build_lfg_help_embed():
    """Embed explaining how /lfg works, built from the same LFG_HELP_STEPS the Databot
    page renders — edit the copy in discord_commands and both update.

    Imported inside the function to avoid an import cycle (discord_commands imports
    models that pull in this package), the same as build_help_embed.
    """
    from the_gatehouse.services.discord_commands import LFG_HELP_INTRO, LFG_HELP_STEPS

    site_url = config.get("SITE_URL", "").rstrip("/")

    fields = []
    for i, step in enumerate(LFG_HELP_STEPS, 1):
        value = _expand_lfg_markdown(step["body"], site_url)
        # The chips follow the body in the same field: step 3's body ends in a colon
        # introducing them.
        if step.get("commands"):
            value += "\n" + "\n".join(
                f"`/{name}` — {blurb}" for name, blurb in step["commands"]
            )
        fields.append({"name": f"{i}. {step['title']}", "value": value, "inline": False})

    embed = {
        "title": "How to Use LFG",
        "description": _expand_lfg_markdown(LFG_HELP_INTRO, site_url),
        "fields": fields,
        "url": f"{site_url}/databot/" if site_url else None,
    }
    embed = {k: v for k, v in embed.items() if v is not None}
    # Current copy sits well inside every limit; this guards a future copy edit.
    return _enforce_embed_limits(embed)


def get_discord_invite_info(invite_code):
    """Fetch Discord server info from invite code"""
    try:
        response = requests.get(
            f'https://discord.com/api/v10/invites/{invite_code}',
            params={'with_counts': 'true', 'with_expiration': 'true'},
            timeout=5
        )
        
        if response.status_code == 200:
            data = response.json()
            guild_data = data.get('guild', {})
            
            icon = guild_data.get('icon')
            banner = guild_data.get('banner')
            splash = guild_data.get('splash')
            
            # Generate default banner color if no banner/splash
            guild_id = guild_data.get('id')
            banner_color = None
            profile_data = data.get('profile', {})    
    
            if not banner and not splash:
                # Try to get the badge colors from profile
                primary_color = profile_data.get('badge_color_primary')
                secondary_color = profile_data.get('badge_color_secondary')
                
                if primary_color and secondary_color and not (primary_color == '#ff0000' and secondary_color == '#800000'):
                    # Use Discord's actual server colors
                    banner_color = f'linear-gradient(135deg, {primary_color} 0%, {secondary_color} 100%)'
                elif guild_id:
                    # Fallback to generated color
                    banner_color = generate_guild_color(guild_id)
            

            return {
                'success': True,
                'guild_id': guild_data.get('id'),
                'name': guild_data.get('name'),
                'description': guild_data.get('description'),
                'icon_hash': icon,  
                'banner_hash': banner,
                'splash_hash': splash,
                'banner_color': banner_color,
                'member_count': data.get('approximate_member_count', 0),
                'online_count': data.get('approximate_presence_count', 0),
                'vanity_url': guild_data.get('vanity_url_code'),
                'features': guild_data.get('features', []),
                'invite_code': invite_code,
            }
        else:
            return {'success': False, 'error': 'Invalid or expired invite'}
            
    except requests.RequestException as e:
        return {'success': False, 'error': str(e)}
    
def generate_guild_color(guild_id):
    """Generate a default gradient color based on guild ID"""
    # Discord's default gradient colors
    gradients = [
        ('linear-gradient(135deg, #5865F2 0%, #7289DA 100%)', 'blue'),
        ('linear-gradient(135deg, #57F287 0%, #3BA55D 100%)', 'green'),
        ('linear-gradient(135deg, #FEE75C 0%, #F0B232 100%)', 'yellow'),
        ('linear-gradient(135deg, #EB459E 0%, #C558E8 100%)', 'fuchsia'),
        ('linear-gradient(135deg, #ED4245 0%, #C9302C 100%)', 'red'),
        ('linear-gradient(135deg, #FF7A00 0%, #E67E22 100%)', 'orange'),
        ('linear-gradient(135deg, #00D9FF 0%, #00B8D4 100%)', 'cyan'),
        ('linear-gradient(135deg, #9B59B6 0%, #8E44AD 100%)', 'purple'),
    ]
    
    # Use guild ID to consistently pick a color
    index = int(guild_id) % len(gradients)
    return gradients[index][0]


def get_guild_link_config(request, guild_id, object_link):
    """
    Generate configuration for Discord guild-gated links.

    Args:
        request: Django request object
        guild_id: Discord guild ID (e.g., config['WR_GUILD_ID'])
        object_link: The protected link to display (e.g., obj.wr_link)

    Returns:
        Dict with 'type', 'url', and 'text' keys, or None if no link
    """


    if not object_link:
        return None

    discord_guild = DiscordGuild.objects.filter(guild_id=guild_id).first()
    if not discord_guild:
        return None

    if not request.user.is_authenticated:
        next_url = request.get_full_path()
        login_url = reverse('discord_login')
        return {
            'type': 'login',
            'url': f"{login_url}?next={next_url}",
            'text': _(f'{discord_guild.name} Thread')
        }

    is_member = request.user.profile.guilds.filter(guild_id=discord_guild.guild_id).exists()

    if is_member:
        return {
            'type': 'direct_link',
            'url': object_link,
            'text': _(f'{discord_guild.name} Thread')
        }

    if not request.user.profile.player:
        return {
            'type': 'discord_join',
            'text': _('Join on Discord for Link')
        }

    # User is a player but not a member - check for existing invite
    guild_invite = DiscordGuildJoinRequest.objects.filter(
        guild=discord_guild,
        profile=request.user.profile
    ).first()

    if guild_invite:
        if guild_invite.status == DiscordGuildJoinRequest.Status.PENDING:
            link_text = _('Invite Pending')
        elif guild_invite.status == DiscordGuildJoinRequest.Status.APPROVED:
            link_text = _(f'Join {discord_guild.name}')
        else:
            link_text = _(f'Request Invite to {discord_guild.name}')
    else:
        link_text = _(f'Request Invite to {discord_guild.name}')

    next_url = request.get_full_path()
    url = f"{reverse('guild-invite', kwargs={'guild_id': discord_guild.guild_id})}?next={next_url}"

    return {
        'type': 'invite_request',
        'url': url,
        'text': link_text
    }

def send_new_survey_notification(*, profile, survey, type):
    if not profile or not survey:
        logger.warning("Missing profile or survey for survey notification")
        return False

    fields = []

    try:
        # Core info
        if survey.pk:
            fields.append({'name': 'Questions:', 'value': survey.question_count()})

        if survey.post_id:
            fields.append({'name': 'Post:', 'value': survey.post.title})

        if survey.series_id:
            fields.append({'name': 'Series:', 'value': survey.series.name})

        if survey.stage_id:
            fields.append({'name': 'Stage:', 'value': survey.stage.name})

        if not survey.is_public:
            if survey.guild_id:
                fields.append({'name': 'Guild:', 'value': survey.guild.name})

            if survey.invited_players.exists():
                fields.append({
                    'name': 'Invited Players:',
                    'value': survey.invited_players.count()
                })

        author = profile.discord or profile.user.username if profile.user else "Unknown"

        from the_gatehouse.tasks import send_rich_discord_message_task

        send_rich_discord_message_task.delay(
            message=survey.title,
            author_name=author,
            category='survey',
            title=f'{type} Survey',
            fields=fields,
        )

        return True

    except Exception:
        logger.exception(
            "Failed to queue survey notification",
            extra={
                'survey_id': survey.pk,
                'profile_id': profile.pk if profile else None,
            }
        )
        return False
