import requests
import json
import emoji

from allauth.socialaccount.models import SocialAccount

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.files.base import ContentFile

from the_gatehouse.models import DiscordGuild, DiscordGuildJoinRequest

from django.urls import reverse
from django.utils.translation import gettext as _


DEFAULT_PROFILE_IMAGE = "default_images/default_user.png"

with open('/etc/config.json') as config_file:
    config = json.load(config_file)


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


def get_user_guilds(user):
    try:
        social_account = user.socialaccount_set.get(provider='discord')
        access_token = social_account.socialtoken_set.first()

        if access_token is None:
            print("No access token found.")
            return None  # Handle no token scenario

        url = 'https://discord.com/api/v10/users/@me/guilds'
        headers = {
            'Authorization': f'Bearer {access_token.token}',  # Use the token attribute
        }
        response = requests.get(url, headers=headers)

        if response.status_code == 200:
            return response.json()  # List of guilds
        else:
            print("Failed to fetch guilds:", response.status_code, response.text)
            return None  # Handle non-200 response appropriately
    except user.socialaccount_set.model.DoesNotExist:
        print("No Discord social account found for the user.")
        return None
    except Exception as e:
        print("An error occurred:", str(e))
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
                print('User is in guild')
                return True
    print("User is not in guild")
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
    elif category == 'Post Edited':
        webhook_url = config['DISCORD_NEW_EDIT_WEBHOOK_URL']
        embed_title = "Post Edited"
        embed_color = 0x00FF00  # Green color for new
        
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
        print(f"Failed to send message to Discord: {response.status_code}, {response.text}")


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
    response = requests.post(webhook_url, json=payload)
    
    if response.status_code != 204:
        print(f"Failed to send message to Discord: {response.status_code}, {response.text}")

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