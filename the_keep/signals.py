# the_keep/signals.py

from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver
from .models import (
    Map, Deck, Faction, Vagabond, Hireling, Landmark, Tweak,
    Expansion, LawGroup
)
from .utils import (
    slugify_post_title,
    slugify_expansion_title,
    slugify_law_group_title
)

# -- Pre-save signals --

@receiver(pre_save, sender=Map)
@receiver(pre_save, sender=Deck)
@receiver(pre_save, sender=Faction)
@receiver(pre_save, sender=Vagabond)
@receiver(pre_save, sender=Hireling)
@receiver(pre_save, sender=Landmark)
@receiver(pre_save, sender=Tweak)
def component_pre_save(sender, instance, **kwargs):
    if instance.slug is None:
        slugify_post_title(instance, save=False)

@receiver(pre_save, sender=Expansion)
def expansion_pre_save(sender, instance, **kwargs):
    if instance.slug is None:
        slugify_expansion_title(instance, save=False)

@receiver(pre_save, sender=LawGroup)
def law_group_pre_save(sender, instance, **kwargs):
    if instance.slug is None:
        slugify_law_group_title(instance, save=False)

# -- Post-save signals --

@receiver(post_save, sender=Map)
@receiver(post_save, sender=Deck)
@receiver(post_save, sender=Faction)
@receiver(post_save, sender=Vagabond)
@receiver(post_save, sender=Hireling)
@receiver(post_save, sender=Landmark)
@receiver(post_save, sender=Tweak)
def component_post_save(sender, instance, created, **kwargs):
    if created:
        slugify_post_title(instance, save=True)

@receiver(post_save, sender=Expansion)
def expansion_post_save(sender, instance, created, **kwargs):
    if created:
        slugify_expansion_title(instance, save=True)

@receiver(post_save, sender=LawGroup)
def law_group_post_save(sender, instance, created, **kwargs):
    if created:
        slugify_law_group_title(instance, save=True)
