import requests
import json
import re
import emoji

from datetime import timedelta

from allauth.socialaccount.models import SocialAccount

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.files.base import ContentFile
from django.utils import timezone

from the_gatehouse.models import DiscordGuild, DiscordGuildJoinRequest

from django.urls import reverse
from django.templatetags.static import static
from django.utils.translation import gettext as _

import logging

logger = logging.getLogger(__name__)


DEFAULT_PROFILE_IMAGE = "default_images/default_user.png"

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
    if not force and config["DEBUG_VALUE"] == "True":
        return DM_BLOCKED  # mirror existing webhook guard; not a retryable error

    discord_id = get_discord_id(user)
    if not discord_id:
        logger.info("No Discord ID for user %s; cannot DM.", user)
        return DM_BLOCKED

    # 1) Open (or fetch) the DM channel with this user
    try:
        ch = requests.post(
            f"{DISCORD_API}/users/@me/channels",
            headers=_bot_headers(),
            json={"recipient_id": discord_id},
            timeout=10,
        )
        ch.raise_for_status()
        channel_id = ch.json()["id"]
    except requests.RequestException as e:
        if _is_terminal_http_error(e):
            logger.info("Cannot DM user %s (channel open 403, no shared server).", user)
            return DM_BLOCKED
        logger.error("Failed to open DM channel for user %s: %s", user, e)
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
            logger.info("Cannot DM user %s (message 403, DMs blocked).", user)
            return DM_BLOCKED
        logger.error("Failed to send DM to user %s: %s", user, e)
        return DM_ERROR


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


def get_ww_guild_nickname(user):
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

    access_token = get_valid_discord_token(user)
    if access_token is None:
        return None

    try:
        response = requests.get(
            f"{DISCORD_API}/users/@me/guilds/{guild_id}/member",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=5,
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


def get_discord_display_name(user):
    try:
        social = SocialAccount.objects.get(user=user, provider="discord")
        data = social.extra_data or {}

        # Prefer the user's Woodland Warriors server nickname; fall back to the
        # Discord global_name, then username, then the Django username.
        display_name = (
            get_ww_guild_nickname(user)
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
    discriminator = data.get("discriminator")

    if not discord_id:
        return None

    # If they have a custom avatar
    if avatar_hash:
        ext = "gif" if avatar_hash.startswith("a_") else "png"
        avatar_url = f"https://cdn.discordapp.com/avatars/{discord_id}/{avatar_hash}.{ext}?size=1024"
    else:
        return None

    # Download and save to Profile.image
    response = requests.get(avatar_url)
    if response.status_code == 200:
        filename = f"discord_{user.id}.png"
        profile.image.save(filename, ContentFile(response.content), save=True)
        return profile.image.url
    return None


def get_valid_discord_token(user):
    """Get a valid Discord access token, refreshing if expired."""
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
                timeout=10,
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


def get_user_guilds(user):
    access_token = get_valid_discord_token(user)
    if access_token is None:
        return None

    try:
        url = 'https://discord.com/api/v10/users/@me/guilds'
        headers = {'Authorization': f'Bearer {access_token}'}
        response = requests.get(url, headers=headers)

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


def check_user_guilds(user):
    guilds = get_user_guilds(user)
    in_ww = False
    in_wr = False
    in_fr = False

    update_user_guilds(user, guilds)

    if guilds:
        for guild in guilds:
            if guild['id'] == config['WW_GUILD_ID']:
                in_ww = True
            if guild['id'] == config['WR_GUILD_ID']:
                in_wr = True
            if guild['id'] == config['FR_GUILD_ID']:
                in_fr = True

    return in_ww, in_wr, in_fr


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
    elif category == "Forge":
        webhook_url = config['DISCORD_FORGE_URL']
        embed_title = "Forged Faction"
        embed_color = 0xffa500  # Orange color for Forge
    elif category == 'forge':
        webhook_url = config['DISCORD_FORGE_URL']
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
    response = requests.post(webhook_url, json=payload)
    
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
    """Faction/Clockwork-specific embed fields (Type, Reach, style ratings)."""
    fields = []
    if faction.type and faction.type != faction.TypeChoices.UNKNOWN:
        fields.append({"name": "Type", "value": faction.get_type_display(), "inline": True})
    if faction.reach:
        fields.append({"name": "Reach", "value": str(faction.reach), "inline": True})
    # Show the style ratings ("None" is a meaningful value), but skip them
    # entirely when all four are unset so an unconfigured faction isn't padded
    # with four redundant "None" fields.
    NONE = faction.StyleChoices.NONE
    style_values = (faction.complexity, faction.card_wealth, faction.aggression, faction.crafting_ability)
    if any(value and value != NONE for value in style_values):
        for label, display in (
            ("Complexity", faction.get_complexity_display()),
            ("Card Wealth", faction.get_card_wealth_display()),
            ("Aggression", faction.get_aggression_display()),
            ("Crafting Ability", faction.get_crafting_ability_display()),
        ):
            fields.append({"name": label, "value": display, "inline": True})
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
    # The flip side, labelled by its own type (e.g. "Demoted Side").
    if hireling.other_side:
        label = f"{hireling.other_side.get_type_display()} Side"
        fields.append({"name": label, "value": hireling.other_side.title, "inline": True})
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


def _embed_color(obj):
    """Discord embed color (int) from an object's "#RRGGBB" `color` string, or
    None when unset/malformed."""
    color = getattr(obj, "color", None)
    if not color:
        return None
    try:
        return int(color.lstrip("#"), 16)
    except (ValueError, AttributeError):
        return None


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

    # Post image as thumbnail (only resolvable on the public domain)
    if site_url and getattr(post, "picture", None):
        try:
            embed["thumbnail"] = {"url": f"{site_url}{post.picture.url}"}
        except ValueError:
            pass  # no file associated

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

    field = field or _STANDALONE_IMAGE_FIELD.get(getattr(post, "component", None))
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
    """Build the embed author line for a law: the group's prime-law title plus a
    breadcrumb of the selected law's two nearest ancestors (immediate parent and
    grandparent), top-down, e.g. "Vagabond ... Relationships - Improving
    Relationships". A " ... " separates the prime law from the ancestors when
    levels are skipped between them; shallow laws just show what exists.

    Titles use plain_title since the author line can't render markup/emoji.
    """
    def label(node):
        return ((node.plain_title or node.title) or "").strip()

    base = label(prime) if prime else (group.title or str(group)).strip()

    # Walk up from the selected law, collecting ancestors above it. Stop at (and
    # exclude) the prime law — it's already the base of the breadcrumb.
    ancestors = []
    node = law.parent
    while node is not None and not node.prime_law:
        ancestors.append(node)
        node = node.parent
    # `ancestors` is bottom-up (parent, grandparent, …); the two nearest are the
    # first two. Render them top-down.
    nearest = ancestors[:2]
    # Skipped levels exist when we trimmed ancestors, or the chain never reached
    # the prime law (so `base` sits outside this law's lineage).
    skipped = len(ancestors) > len(nearest) or node is None
    crumb_titles = [t for t in (label(a) for a in reversed(nearest)) if t]

    if not crumb_titles:
        return base
    sep = " ... " if (base and skipped) else (" - " if base else "")
    return f"{base}{sep}{' - '.join(crumb_titles)}"


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

    # Author: the prime law title of the group (in this language), followed by a
    # breadcrumb of the selected law's two nearest ancestors so e.g. 9.2.9.Ia
    # reads "Vagabond ... Relationships - Improving Relationships". A " ... "
    # marks any skipped levels between the prime law and the shown ancestors.
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

    if site_url and getattr(vagabond, "picture", None):
        try:
            embed["thumbnail"] = {"url": f"{site_url}{vagabond.picture.url}"}
        except ValueError:
            pass

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


def build_stats_embed(stats, *, player=None, faction=None, tournament=None, platform=None, include_fan_content=False):
    """Build a Discord embed dict for a /stats win-rate result.

    `stats` is the dict from filtered_winrate (total, games, win_points, win_rate).
    The remaining args are the resolved filter objects (or None) used to label
    the result and, when a single subject is in focus, link/thumbnail it.
    include_fan_content: when False (default), the faction board excludes
    unofficial (fan-made) factions.
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


def build_upcoming_embed(match, series=None, player=None):
    """Build a Discord embed for the next scheduled match.

    Links to the matches page that contains the match (via
    Match.get_matches_url, which adapts to the tournament's stage/round layout),
    lists the players in the match, and shows the platform only when the
    tournament requires one (tournament.platform is set).

    `series` and `player` are the optional filters the /upcoming result was
    narrowed by (each None when not supplied); a summary line names them, e.g.
    "The next scheduled Brand New Series game for MrMirz". They reflect the
    user's filters, not the match — so an unfiltered search omits the series
    even though the match belongs to a tournament.
    """
    site_url = config.get("SITE_URL", "").rstrip("/")
    round = match.round
    tournament = round.get_tournament()

    embed = {
        "title": match.name or "Upcoming Match",
        "url": f"{site_url}{match.get_matches_url()}" if site_url else None,
        "description": _upcoming_summary(series, player),
    }
    if tournament:
        embed["author"] = {"name": tournament.name}

    fields = []

    # Scheduled time as a Discord timestamp so each viewer sees it localized,
    # plus a relative "in X" hint.
    if match.scheduled_time:
        ts = int(match.scheduled_time.timestamp())
        fields.append({
            "name": "Scheduled",
            "value": f"<t:{ts}:F> (<t:{ts}:R>)",
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


def build_help_embed():
    """Build a Discord embed listing the bot's commands, grouped by category.

    Driven by the shared command definitions (the_gatehouse.services.
    discord_commands), so any command registered with Discord automatically
    appears here. Imported inside the function to avoid an import cycle
    (discord_commands imports models that pull in this package).
    """
    from the_gatehouse.services.discord_commands import grouped_commands

    site_url = config.get("SITE_URL", "").rstrip("/")

    fields = []
    for group_name, rows in grouped_commands():
        value = "\n".join(f"`/{name}` — {desc}" for name, desc in rows)
        fields.append({"name": group_name, "value": value, "inline": False})

    embed = {
        "title": "Bot Commands",
        "description": "Here are the commands you can use:",
        "fields": fields,
        "url": site_url or None,
    }
    return {k: v for k, v in embed.items() if v is not None}


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
