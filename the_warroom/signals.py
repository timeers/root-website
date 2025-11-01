from django.db.models.signals import pre_delete, pre_save, post_save
from django.dispatch import receiver
from .models import Effort, ScoreCard, Tournament, Round
from .services.slugify_titles import slugify_tournament_name, slugify_round_name

@receiver(pre_delete, sender=Effort)
def handle_effort_deletion(sender, instance, **kwargs):
    try:
        scorecard = instance.scorecard  # Reverse relation from Effort to ScoreCard
        if scorecard:
            scorecard.final = False
            scorecard.save()
    except ScoreCard.DoesNotExist:
        pass  # No related scorecard; nothing to do

@receiver(pre_save, sender=Tournament)
def tournament_pre_save(sender, instance, *args, **kwargs):
    if instance.slug is None:
        slugify_tournament_name(instance, save=False)

@receiver(post_save, sender=Tournament)
def tournament_post_save(sender, instance, created, *args, **kwargs):
    if created:
        slugify_tournament_name(instance, save=True)

@receiver(pre_save, sender=Round)
def round_pre_save(sender, instance, *args, **kwargs):
    if instance.slug is None:
        slugify_round_name(instance, save=False)

@receiver(post_save, sender=Round)
def round_post_save(sender, instance, created, *args, **kwargs):
    if created:
        slugify_round_name(instance, save=True)
