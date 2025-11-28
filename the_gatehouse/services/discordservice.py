import requests
import json
import emoji

from allauth.socialaccount.models import SocialAccount

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.files.base import ContentFile

from the_gatehouse.models import DiscordGuild


DEFAULT_PROFILE_IMAGE = "default_images/default_user.png"

with open('/etc/config.json') as config_file:
    config = json.load(config_file)


def get_discord_display_name(user):
    try:
        # Get the Discord social account
        social_account = SocialAccount.objects.get(user=user, provider='discord')
        # Extract the display name from the extra data
        display_name = social_account.extra_data.get('global_name', '')

        # Remove all emojis
        display_name = emoji.replace_emoji(display_name, '').strip()

        return display_name
    except SocialAccount.DoesNotExist:
        return None
    
def get_discord_id(user):
    try:
        return SocialAccount.objects.get(user=user, provider='discord').uid
    except SocialAccount.DoesNotExist:
        return None



def update_discord_avatar(user, force=False):
    try:
        social_account = SocialAccount.objects.get(user=user, provider='discord')
    except SocialAccount.DoesNotExist:
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
    user.profile.guilds.clear()
    existing_guilds = DiscordGuild.objects.filter(guild_id__in=current_guild_ids)
    user.profile.guilds.add(*existing_guilds)



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
    # Check if DEBUG is False in the config (uncomment this if you want to use it)
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
