import requests
import json
import emoji

from datetime import timedelta

from allauth.socialaccount.models import SocialAccount

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.files.base import ContentFile
from django.utils import timezone

from the_gatehouse.models import DiscordGuild, DiscordGuildJoinRequest

from django.urls import reverse
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


def get_discord_display_name(user):
    try:
        social = SocialAccount.objects.get(user=user, provider="discord")
        data = social.extra_data or {}

        # Prefer Discord global_name then username then fallback to Django username
        display_name = (
            data.get("global_name")
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

def send_rich_discord_message(message, category=None, author_name=None, author_icon_url=None, title=None, color=None, fields=None):
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
    if faction.type:
        fields.append({"name": "Type", "value": faction.get_type_display(), "inline": True})
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


def _vagabond_fields(vagabond):
    fields = []
    if vagabond.ability:
        fields.append({"name": "Special Ability", "value": vagabond.ability, "inline": True})
    if getattr(vagabond, "captain", False):
        fields.append({"name": "Captain", "value": "Yes", "inline": True})
    return fields


def _component_fields(post):
    """Return the subclass-specific embed fields for a Post, by component type."""
    component = getattr(post, "component", None)
    if component in ("Faction", "Clockwork"):
        return _faction_fields(post)
    if component == "Vagabond":
        return _vagabond_fields(post)
    if component == "Hireling" and post.type:
        return [{"name": "Type", "value": post.get_type_display(), "inline": True}]
    if component == "Landmark" and getattr(post, "card_text", None):
        return [{"name": "Card Text", "value": post.card_text, "inline": False}]
    return []


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
    }

    # color field is a "#RRGGBB" string; Discord wants an int
    if post.color:
        try:
            embed["color"] = int(post.color.lstrip("#"), 16)
        except (ValueError, AttributeError):
            pass

    # Post image as thumbnail (only resolvable on the public domain)
    if site_url and getattr(post, "picture", None):
        try:
            embed["thumbnail"] = {"url": f"{site_url}{post.picture.url}"}
        except ValueError:
            pass  # no file associated

    fields = []
    if post.designer:
        fields.append({"name": "Designer", "value": post.designer.display_name or "—", "inline": True})
    fields.extend(_component_fields(post))

    if fields:
        embed["fields"] = fields

    # Drop None values Discord would reject
    return {k: v for k, v in embed.items() if v is not None}


# Back-compat alias: the embed builder is now generic over all Post types.
build_faction_embed = build_post_embed


def build_stats_embed(stats, *, player=None, faction=None, tournament=None, platform=None):
    """Build a Discord embed dict for a /stats win-rate result.

    `stats` is the dict from filtered_winrate (total, win_points, win_rate).
    The remaining args are the resolved filter objects (or None) used to label
    the result and, when a single subject is in focus, link/thumbnail it.
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

    embed = {
        "title": "Win Rate",
        "description": description,
        "fields": [
            {"name": "Win Rate", "value": f"{stats['win_rate']:.1f}%", "inline": True},
            {"name": "Games", "value": str(stats['total']), "inline": True},
            {"name": "Win Points", "value": f"{stats['win_points']:g}", "inline": True},
        ],
    }

    # When exactly one of player/faction is the subject, link + thumbnail it.
    subject = player if (player and not faction) else (faction if (faction and not player) else None)
    if subject is not None:
        if subject is faction and faction.color:
            try:
                embed["color"] = int(faction.color.lstrip("#"), 16)
            except (ValueError, AttributeError):
                pass
        if site_url:
            try:
                embed["url"] = f"{site_url}{subject.get_absolute_url()}"
            except Exception:
                pass
            image = getattr(subject, "picture", None) or getattr(subject, "image", None)
            if image:
                try:
                    embed["thumbnail"] = {"url": f"{site_url}{image.url}"}
                except ValueError:
                    pass

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
