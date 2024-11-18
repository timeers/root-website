from django.db.models.signals import post_save
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from django.contrib.auth.models import User
from .models import Profile  # Adjust the import based on your project structure
from .discordservice import is_user_in_ww, is_user_in_wr
from django.contrib.auth.models import Group


@receiver(post_save, sender=User)
def manage_profile(sender, instance, created, **kwargs):
    if created:
        try:
            # Find or create profile
            profile, _ = Profile.objects.get_or_create(discord=instance.username)
            profile.display_name = instance.username  # Set display_name to the username
            profile.user = instance # Link profile to user
            profile.save()
        except Profile.DoesNotExist:
            # Create a new Profile if it does not exist
            print('Creating new profile')
            profile = Profile.objects.create(user=instance, discord=instance.username)  # Ensure discord is set
            profile.display_name = instance.username
            profile.save()
    else:
        # This block runs when the user is updated
        print("Updating User Profile")
        if hasattr(instance, 'profile'):
            instance.profile.save()
        else:
            profile, _ = Profile.objects.get_or_create(discord=instance.username)
            instance.profile = profile
            profile.display_name = instance.username  # Ensure display_name is set
            profile.save()
    

# This is to put new users into the Player group so that they have access.
@receiver(user_logged_in)
def user_logged_in_handler(request, user, **kwargs):
    profile = user.profile  # To avoid multiple lookups
    profile_updated = False

    if profile.group == 'O' and is_user_in_ww(user):
        profile.group = 'P'
        profile_updated = True

    if not profile.weird and is_user_in_wr(user):
        profile.weird = True
        profile_updated = True

    group_name = 'admin'  
    if not user.groups.filter(name=group_name).exists() and profile.group == "A":
        # Get the group object
        group, created = Group.objects.get_or_create(name=group_name)
        # Add the user to the group
        user.groups.add(group)
        user.is_staff = True
        print(f'User {user} added to {group_name}')
        user.save()

    if profile_updated:
        profile.save()







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