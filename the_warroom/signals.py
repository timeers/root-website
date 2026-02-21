from django.db.models.signals import pre_delete, pre_save, post_save
from django.dispatch import receiver
from .models import Effort, Game, ScoreCard, Tournament, Round, Stage
from .services.slugify_titles import slugify_tournament_name, slugify_round_name, slugify_stage_name

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


@receiver(pre_save, sender=Stage)
def stage_pre_save(sender, instance, *args, **kwargs):
    if instance.slug is None:
        slugify_stage_name(instance, save=False)

@receiver(post_save, sender=Stage)
def stage_post_save(sender, instance, created, *args, **kwargs):
    if created:
        slugify_stage_name(instance, save=True)

@receiver(post_save, sender=Game)
def game_post_save_check_match(sender, instance, **kwargs):
    """When a finalized game is linked to a match, trigger match completion logic."""
    if not instance.final:
        return
    from .models import Match
    try:
        match = Match.objects.get(game=instance)
    except Match.DoesNotExist:
        return
    from .services.bracket import BracketService
    BracketService.on_game_complete(match)