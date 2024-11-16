from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import User
from .models import Profile  # Adjust the import based on your project structure

@receiver(post_save, sender=User)
def manage_profile(sender, instance, created, **kwargs):
    if created:
        # Find or create profile
        profile, _ = Profile.objects.get_or_create(discord=instance.username)
        profile.display_name = instance.username  # Set display_name to the username
        profile.user = instance # Link profile to user
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