"""Discord OAuth identity and per-user guild membership.

NOT bot code: everything here authenticates as the *user*, via the Discord
OAuth token on their allauth SocialAccount — never the bot token. This is the
login path (guild sync, avatars, display names, membership checks) plus the
guild-invite pages, all of which are website features that happen to be keyed
on Discord IDs.

The bot's own API client lives in the_databot.services.discordservice, which
imports get_discord_id / get_valid_discord_token from here. Imports flow one
way: the_databot -> the_gatehouse.
"""
import json
import logging
from datetime import timedelta
from io import BytesIO

import emoji
import requests
from PIL import Image

from allauth.socialaccount.models import SocialAccount
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.files.base import ContentFile
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from the_gatehouse.models import (DiscordGuild, DiscordGuildJoinRequest,
                                  DEFAULT_PROFILE_IMAGE)

logger = logging.getLogger(__name__)

DISCORD_API = "https://discord.com/api/v10"

with open('/etc/config.json') as config_file:
    config = json.load(config_file)


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


def discord_refresh_capability(user):
    """Can a guild refresh for this user plausibly succeed? Network-free.

    Mirrors the three give-up conditions in get_valid_discord_token below, but without
    the HTTP call, so callers can tell a RETRYABLE failure (Discord slow/erroring) from
    one where no amount of retrying helps. Returns:

      'ok'          — a Discord account with a usable (or refreshable) token
      'no_account'  — no Discord social account at all. Normal for a username/password
                      login, e.g. Django admin: not a fault, don't report it.
      'no_token'    — has a Discord account but no usable token. A real fault worth
                      surfacing; allauth re-stores the token on their next Discord login.
    """
    try:
        social_account = user.socialaccount_set.get(provider='discord')
    except user.socialaccount_set.model.DoesNotExist:
        return 'no_account'

    token_obj = social_account.socialtoken_set.first()
    if token_obj is None:
        return 'no_token'

    # Expired (same 60s buffer as get_valid_discord_token) with nothing to refresh from.
    if (token_obj.expires_at
            and timezone.now() >= token_obj.expires_at - timedelta(seconds=60)
            and not token_obj.token_secret):
        return 'no_token'

    return 'ok'


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

