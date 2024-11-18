import os
import requests
# from django.conf import settings
from django.contrib.auth.decorators import login_required
# from django.shortcuts import render
from django.http import HttpResponseForbidden



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

def is_user_in_ww(user):
    guilds = get_user_guilds(user)
    if guilds:
        for guild in guilds:
            if guild['id'] == os.environ.get('WW_GUILD_ID'):
                print('User is in WW')
                return True
    print("User is not in WW")
    return False

def is_user_in_wr(user):
    guilds = get_user_guilds(user)
    if guilds:
        for guild in guilds:
            if guild['id'] == os.environ.get('WR_GUILD_ID'):
                print('User is in WR')
                return True
    print("User is not in WR")
    return False

# Decorator
def woodland_warriors_required():
    guild_id = os.environ.get('WW_GUILD_ID')
    def decorator(view_func):
        @login_required  # Ensure the user is authenticated
        def wrapper(request, *args, **kwargs):
            if is_user_in_guild(request.user, guild_id):
                return view_func(request, *args, **kwargs)  # Continue to the view
            else:
                return HttpResponseForbidden()  # 403 Forbidden
                # return render(request, 'the_gatehouse/not_verified.html')  # Redirect to home if not a member
        return wrapper
    return decorator