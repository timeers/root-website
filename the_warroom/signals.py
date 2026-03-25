from django.db.models.signals import pre_delete, pre_save, post_save, post_delete
from django.dispatch import receiver
from .models import Effort, Game, ScoreCard, Tournament, Round, Stage, Match, CompetitionStatus
from .services.slugify_titles import slugify_tournament_name, slugify_round_name, slugify_stage_name
from .services.winrate_service import calculate_and_cache_winrate

@receiver(pre_save, sender=Effort)
def effort_pre_save_snapshot(sender, instance, **kwargs):
    """Snapshot old FK values so post_save can recalculate the old faction/vagabond/player if changed."""
    if instance.pk:
        try:
            old = Effort.objects.get(pk=instance.pk)
            instance._old_faction_id = old.faction_id
            instance._old_vagabond_id = old.vagabond_id
            instance._old_player_id = old.player_id
        except Effort.DoesNotExist:
            pass


@receiver(pre_delete, sender=Effort)
def handle_effort_deletion(sender, instance, **kwargs):
    try:
        scorecard = instance.scorecard  # Reverse relation from Effort to ScoreCard
        if scorecard:
            scorecard.final = False
            scorecard.save()
    except ScoreCard.DoesNotExist:
        pass  # No related scorecard; nothing to do


def _collect_winrate_objects(instance, include_old=False):
    """Return the set of (obj, id) pairs to recalculate winrates for from an Effort instance."""
    objects_to_update = []
    seen_ids = {'faction': set(), 'vagabond': set(), 'player': set()}

    def add(obj, key, pk):
        if pk and pk not in seen_ids[key]:
            seen_ids[key].add(pk)
            objects_to_update.append(obj)

    if instance.faction_id:
        add(instance.faction, 'faction', instance.faction_id)
    if instance.vagabond_id:
        add(instance.vagabond, 'vagabond', instance.vagabond_id)
    if instance.player_id:
        add(instance.player, 'player', instance.player_id)

    if include_old:
        old_faction_id = getattr(instance, '_old_faction_id', None)
        old_vagabond_id = getattr(instance, '_old_vagabond_id', None)
        old_player_id = getattr(instance, '_old_player_id', None)

        if old_faction_id and old_faction_id != instance.faction_id:
            from the_keep.models import Faction
            try:
                add(Faction.objects.get(pk=old_faction_id), 'faction', old_faction_id)
            except Faction.DoesNotExist:
                pass
        if old_vagabond_id and old_vagabond_id != instance.vagabond_id:
            from the_keep.models import Vagabond
            try:
                add(Vagabond.objects.get(pk=old_vagabond_id), 'vagabond', old_vagabond_id)
            except Vagabond.DoesNotExist:
                pass
        if old_player_id and old_player_id != instance.player_id:
            from the_gatehouse.models import Profile
            try:
                add(Profile.objects.get(pk=old_player_id), 'player', old_player_id)
            except Profile.DoesNotExist:
                pass

    return objects_to_update


@receiver(post_save, sender=Effort)
def handle_effort_save_update_winrates(sender, instance, **kwargs):
    for obj in _collect_winrate_objects(instance, include_old=True):
        calculate_and_cache_winrate(obj)


@receiver(post_delete, sender=Effort)
def handle_effort_delete_update_winrates(sender, instance, **kwargs):
    for obj in _collect_winrate_objects(instance, include_old=False):
        calculate_and_cache_winrate(obj)


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

@receiver(pre_save, sender=Game)
def game_pre_save_snapshot(sender, instance, **kwargs):
    """Snapshot final and test_match so post_save can detect changes."""
    if instance.pk:
        try:
            old = Game.objects.get(pk=instance.pk)
            instance._pre_save_final = old.final
            instance._pre_save_test_match = old.test_match
        except Game.DoesNotExist:
            pass


@receiver(pre_delete, sender=Game)
def game_pre_delete_reevaluate_match(sender, instance, **kwargs):
    """When a game linked to a match is deleted, reset match/series status."""
    try:
        match = Match.objects.get(game=instance)
    except Match.DoesNotExist:
        return

    series = match.series

    # Reset this match
    match.status = CompetitionStatus.PENDING
    match.save(update_fields=['status'])

    # Clear series winners and re-evaluate status
    series.winners.clear()

    other_completed = series.matches.filter(
        status=CompetitionStatus.COMPLETED
    ).exclude(pk=match.pk).exists()

    if other_completed:
        series.status = CompetitionStatus.ACTIVE
    else:
        series.status = CompetitionStatus.PENDING
    series.save(update_fields=['status'])


@receiver(post_save, sender=Game)
def game_post_save_check_match(sender, instance, **kwargs):
    """When a finalized game is linked to a match, trigger match completion logic."""
    if instance.final:
        from .models import Match
        try:
            match = Match.objects.get(game=instance)
        except Match.DoesNotExist:
            pass
        else:
            from .services.bracket import BracketService
            BracketService.on_game_complete(match)

    old_final = getattr(instance, '_pre_save_final', None)
    old_test_match = getattr(instance, '_pre_save_test_match', None)
    if old_final != instance.final or old_test_match != instance.test_match:
        seen = {'faction': set(), 'vagabond': set(), 'player': set()}
        for effort in instance.efforts.select_related('faction', 'vagabond', 'player'):
            if effort.faction_id and effort.faction_id not in seen['faction']:
                seen['faction'].add(effort.faction_id)
                calculate_and_cache_winrate(effort.faction)
            if effort.vagabond_id and effort.vagabond_id not in seen['vagabond']:
                seen['vagabond'].add(effort.vagabond_id)
                calculate_and_cache_winrate(effort.vagabond)
            if effort.player_id and effort.player_id not in seen['player']:
                seen['player'].add(effort.player_id)
                calculate_and_cache_winrate(effort.player)