import os
# import logging
import uuid
import requests
import json
from django.utils import timezone 

# from django.conf import settings
from django.contrib.auth.decorators import login_required
# from django.shortcuts import render
from django.core.exceptions import PermissionDenied


from allauth.socialaccount.models import SocialAccount

# logger = logging.getLogger(__name__)

with open('/etc/config.json') as config_file:
    config = json.load(config_file)


def get_discord_display_name(user):
    try:
        # Get the Discord social account
        social_account = SocialAccount.objects.get(user=user, provider='discord')
        # Extract the display name from the extra data
        display_name = social_account.extra_data.get('global_name', '')
        return display_name
    except SocialAccount.DoesNotExist:
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

    if guilds:
        for guild in guilds:
            if guild['id'] == config['WW_GUILD_ID']:
                in_ww = True
            if guild['id'] == config['WR_GUILD_ID']:
                in_wr = True

    return in_ww, in_wr


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



# class DiscordWebhookHandler(logging.Handler):
#     def __init__(self, webhook_url, username="RDB Logger", avatar_url=None):
#         super().__init__()
#         self.webhook_url = webhook_url
#         self.username = username
#         self.avatar_url = avatar_url  # Optional avatar URL (can be an image URL)
 
#     def emit(self, record):
#         # Format the log message
#         log_message = self.format(record)
       
#         # Define the embed structure
#         embed = {
#             "title": "Error Notification",
#             "description": log_message,  # Include the formatted log message in the embed
#             "color": 16711680  # Color code for red (for errors)
#         }
 
#         # Build the payload
#         payload = {
#             "username": self.username,  # Custom username for the webhook message
#             "avatar_url": self.avatar_url,  # Custom avatar URL (optional)
#             "embeds": [embed]  # Embed content for richer display
#         }
 
#         # Define the headers for the request
#         headers = {
#             "Content-Type": "application/json"
#         }
 
#         # Send the request to the Discord webhook URL
#         try:
#             response = requests.post(self.webhook_url, data=json.dumps(payload), headers=headers)
#             response.raise_for_status()  # Raise an error if the request failed
#         except requests.exceptions.RequestException as e:
#             print(f"Error sending log to Discord: {e}")
#             # logger.error(f"Error sending log to Discord: {e}")



def send_discord_message(message):
    # Check if DEBUG is False in the config
    if config["DEBUG_VALUE"] == "True":
        return  # Do nothing if DEBUG is True
    webhook_url = config['DISCORD_USER_EVENTS_WEBHOOK_URL']
    
    # Define the payload (message) to be sent
    payload = {
        'content': message,  # Message to be sent
    }

    # Send POST request to Discord webhook URL
    response = requests.post(webhook_url, json=payload)
    
    if response.status_code != 204:
        print(f"Failed to send message to Discord: {response.status_code}, {response.text}")