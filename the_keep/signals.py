# the_keep/signals.py

from django.db.models.signals import pre_save, post_save, post_delete
from django.dispatch import receiver
from .models import (
    Map, Deck, Faction, Vagabond, Hireling, Landmark, Tweak,
    Expansion, LawGroup, RulesFile
)
from .services.slugify_titles import (
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
        designers_list = instance.get_designers_list()
        instance.designers_list = designers_list
        instance.save(update_fields=["designers_list"])


@receiver(post_save, sender=Expansion)
def expansion_post_save(sender, instance, created, **kwargs):
    if created:
        slugify_expansion_title(instance, save=True)

@receiver(post_save, sender=LawGroup)
def law_group_post_save(sender, instance, created, **kwargs):
    if created:
        slugify_law_group_title(instance, save=True)



# Delete Yaml file on delete or update
@receiver(pre_save, sender=RulesFile)
def delete_old_file_on_change(sender, instance, **kwargs):
    if not instance.pk:
        return  # New object, no old file to delete
    try:
        old_file = sender.objects.get(pk=instance.pk).file
    except sender.DoesNotExist:
        return

    new_file = instance.file
    if old_file and old_file != new_file:
        old_file.delete(save=False)


@receiver(post_delete, sender=RulesFile)
def delete_rulesfile_file(sender, instance, **kwargs):
    if instance.file:
        instance.file.delete(save=False)
