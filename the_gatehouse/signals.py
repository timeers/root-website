from django.db.models.signals import post_save
from django.contrib.auth.signals import user_logged_in
from django.shortcuts import redirect
from django.dispatch import receiver
from django.contrib.auth.models import User
from .models import Profile  # Adjust the import based on your project structure
from .discordservice import get_discord_display_name, check_user_guilds
from django.contrib.auth.models import Group


@receiver(post_save, sender=User)
def manage_profile(sender, instance, created, **kwargs):
    if created:
        profile, _ = Profile.objects.get_or_create(discord=instance.username, defaults={'user': instance})
        print(f'Profile created/linked: {profile.discord}')
    else:
        try:
            profile = instance.profile
        except Profile.DoesNotExist:
            profile, _ = Profile.objects.get_or_create(discord=instance.username)
            profile.user = instance
            profile.save()


# @receiver(post_save, sender=User)
# def manage_profile(sender, instance, created, **kwargs):
#     # display_name = get_discord_display_name(instance)
#     # print(f'Display Name: {display_name}')
#     if created:
#         try:
#             # Find or create profile
#             print('Trying to get or create profile')
#             profile, _ = Profile.objects.get_or_create(discord=instance.username)
#             print(profile.discord)
#             # if display_name:
#             #     profile.display_name = display_name # Set display_name to discord username
#             # else:
#             #     profile.display_name = instance.username  # Set display_name to the username
#             profile.user = instance # Link profile to user
#             print(f'Linked profile to user: {instance}')
#             profile.save()
#         except Profile.DoesNotExist:
#             # Create a new Profile if it does not exist
#             print('Creating new profile')
#             profile = Profile.objects.create(user=instance, discord=instance.username)  # Ensure discord is set
#             # if display_name:
#             #     profile.display_name = display_name # Set display_name to discord username
#             # else:
#             #     profile.display_name = instance.username  # Set display_name to the username
#             profile.save()
#     else:
#         # This block runs when the user is updated
#         print("Updating User Profile")
#         if hasattr(instance, 'profile'):
#             # print("Profile saved")
#             instance.profile.save()
#         else:
#             print('No profile associated')
#             profile, _ = Profile.objects.get_or_create(discord=instance.username)
#             instance.profile = profile
#             # if display_name:
#             #     profile.display_name = display_name # Set display_name to discord username
#             # else:
#             #     profile.display_name = instance.username  # Set display_name to the username
#             profile.save()
    

# This is to put users in the correct groups when created or updated
@receiver(user_logged_in)
def user_logged_in_handler(request, user, **kwargs):
    print(f'{user} logged in')
    if not hasattr(user, 'profile'):
        user.save()

    
    profile = user.profile
    profile_updated = False
    current_group = profile.group
    in_ww, in_wr = check_user_guilds(user)
    display_name = get_discord_display_name(user)


    if display_name and profile.display_name != display_name:
        profile.display_name = display_name
        profile_updated = True

    # If user is a member of WW but in group O (add to group P)
    if current_group == 'O' and in_ww:
        profile.group = 'P'
        profile_updated = True

    # If user is a member of WR but does not have the weird view (add view)
    if not profile.in_weird_root and in_wr:
        profile.in_weird_root = True
        profile.weird = True
        profile_updated = True

    if profile.designer and not profile.in_weird_root:
        profile.in_weird_root = True
        profile.weird = True
        profile_updated = True

    if profile_updated:
        profile.save()

    group_name = 'admin'  
    group_exists = user.groups.filter(name=group_name).exists()
    
    # If user is in group A but not in the Admin group (add to group)
    if not group_exists and current_group == "A":
        # Get the group object
        group, created = Group.objects.get_or_create(name=group_name)
        # Add the user to the group
        user.groups.add(group)
        user.is_staff = True
        print(f'User {user} added to {group_name}')
        user.save()

    # If user is not in group A but is in the Admin group (remove from group)
    elif group_exists and current_group != "A":
        # Get the group object
        group, created = Group.objects.get_or_create(name=group_name)
        # Add the user to the group
        user.groups.remove(group)
        user.is_staff = False
        print(f'User {user} removed from {group_name}')
        user.save()

    if user.profile.player_onboard == False and user.profile.player:
        onboard_for_player = True
    else:
        onboard_for_player = False

    if user.profile.designer_onboard == False and user.profile.designer:
        onboard_for_designer = True
    else:
        onboard_for_designer = False

    if user.profile.admin_onboard == False and user.profile.admin:
        onboard_for_admin = True
    else:
        onboard_for_admin = False

    print(f'Player onboard:{onboard_for_player}, Designer onboard:{onboard_for_designer}, Admin Onboard:{onboard_for_admin}')
    # If any of the onboard flags are True, store them in the session
    if onboard_for_admin or onboard_for_designer or onboard_for_player:
        request.session['onboard_data'] = {
            'admin_onboard': onboard_for_admin,
            'player_onboard': onboard_for_player,
            'designer_onboard': onboard_for_designer,
        }
        




# @receiver(post_save, sender=User)
# def create_profile(sender, instance, created, **kwargs):
#     if created:
#         print("Created User")
#         profile, _ = Profile.objects.get_or_create(discord=instance.username)
#         print(profile)
#         # profile = Profile.objects.create(user=instance)
#         profile.display_name = instance.username  # Set display_name to the username
#         profile.save()

# @receiver(post_save, sender=User)
# def save_profile(sender, instance, **kwargs):
#     if not instance.profile is None:
#         print("User has profile")
#         instance.profile.save()
#     else:
#         print("Finding or creating profile")
#         profile, _ = Profile.objects.get_or_create(discord=instance.username)
#         instance.profile = profile
#         print("Profile set")
#         instance.profile.save()
#         print("Profile saved")