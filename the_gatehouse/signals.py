from django.db.models.signals import post_save
from django.contrib.auth.models import User
from django.dispatch import receiver
from .models import Profile

@receiver(post_save, sender=User)
def create_profile(sender, instance, created, **kwargs):
    if created:
        profile = Profile.objects.create(user=instance)
        profile.display_name = instance.username  # Set display_name to the username
        profile.save()

@receiver(post_save, sender=User)
def save_profile(sender, instance, **kwargs):
    instance.profile.save()