from django.db.models.signals import pre_delete, pre_save, post_save, post_delete, m2m_changed
from django.dispatch import receiver
from .models import Effort, Game, ScoreCard, Tournament, Round, Stage, Match, CompetitionStatus
from .services.slugify_titles import slugify_tournament_name, slugify_round_name, slugify_stage_name


def _tournament_ids_for_game(game):
    """Set of tournament ids a game counts toward — via its primary round's
    stage.tournament and any extra_rounds' stage.tournament. Mirrors
    Game.objects.counting_for_tournament()."""
    ids = set()
    round_id = getattr(game, 'round_id', None)
    if round_id:
        tid = Round.objects.filter(pk=round_id).values_list('stage__tournament_id', flat=True).first()
        if tid:
            ids.add(tid)
    if game.pk:
        ids.update(
            Round.objects.filter(extra_games=game)
            .values_list('stage__tournament_id', flat=True)
        )
    ids.discard(None)
    return ids


def _tournament_ids_for_round(round_id):
    """Tournament id for a round id (via stage.tournament), or empty set."""
    if not round_id:
        return set()
    tid = Round.objects.filter(pk=round_id).values_list('stage__tournament_id', flat=True).first()
    return {tid} if tid else set()


def _enqueue_tournament_counts(ids):
    ids = {i for i in ids if i}
    if ids:
        from .tasks import update_tournament_counts
        update_tournament_counts.delay(list(ids))

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


def _obj_to_tuple(obj):
    return (obj._meta.app_label, obj._meta.model_name, obj.pk)


@receiver(post_save, sender=Effort)
def handle_effort_save_update_winrates(sender, instance, **kwargs):
    objects = _collect_winrate_objects(instance, include_old=True)
    if objects:
        from .tasks import update_cached_winrates
        update_cached_winrates.delay([_obj_to_tuple(obj) for obj in objects])


@receiver(post_delete, sender=Effort)
def handle_effort_delete_update_winrates(sender, instance, **kwargs):
    objects = _collect_winrate_objects(instance, include_old=False)
    if objects:
        from .tasks import update_cached_winrates
        update_cached_winrates.delay([_obj_to_tuple(obj) for obj in objects])


@receiver(post_save, sender=Effort)
@receiver(post_delete, sender=Effort)
def handle_effort_change_update_counts(sender, instance, **kwargs):
    """Player counts depend on efforts — refresh the game's tournament counts."""
    if instance.game_id:
        try:
            game = instance.game
        except Game.DoesNotExist:
            return
        _enqueue_tournament_counts(_tournament_ids_for_game(game))


def _slug_should_follow_name(instance, model_class, update_fields):
    """Return True if the slug should be regenerated because the name changed.

    Guards against partial (update_fields) saves that don't touch the name, so a
    re-slug only happens on full saves (e.g. the update forms) or saves that
    explicitly include 'name'. Regenerating the slug keeps the URL in sync with
    the new name; existing links to the old slug will no longer resolve.
    """
    if update_fields is not None and 'name' not in update_fields:
        return False
    try:
        old = model_class.objects.get(pk=instance.pk)
    except model_class.DoesNotExist:
        return False
    return (old.name or '') != (instance.name or '')


@receiver(pre_save, sender=Tournament)
def tournament_pre_save(sender, instance, update_fields=None, *args, **kwargs):
    if instance.slug is None:
        slugify_tournament_name(instance, save=False)
    elif instance.pk and _slug_should_follow_name(instance, Tournament, update_fields):
        slugify_tournament_name(instance, save=False)

@receiver(post_save, sender=Tournament)
def tournament_post_save(sender, instance, created, *args, **kwargs):
    if created:
        slugify_tournament_name(instance, save=True)


@receiver(pre_save, sender=Round)
def round_pre_save(sender, instance, update_fields=None, *args, **kwargs):
    if instance.slug is None:
        slugify_round_name(instance, save=False)
    elif instance.pk and _slug_should_follow_name(instance, Round, update_fields):
        slugify_round_name(instance, save=False)

@receiver(post_save, sender=Round)
def round_post_save(sender, instance, created, *args, **kwargs):
    if created:
        slugify_round_name(instance, save=True)


@receiver(pre_save, sender=Stage)
def stage_pre_save(sender, instance, update_fields=None, *args, **kwargs):
    if instance.slug is None:
        slugify_stage_name(instance, save=False)
    elif instance.pk and _slug_should_follow_name(instance, Stage, update_fields):
        slugify_stage_name(instance, save=False)

@receiver(post_save, sender=Stage)
def stage_post_save(sender, instance, created, *args, **kwargs):
    if created:
        slugify_stage_name(instance, save=True)

@receiver(pre_save, sender=Game)
def game_pre_save_snapshot(sender, instance, **kwargs):
    """Snapshot final, test_match and round so post_save can detect changes."""
    if instance.pk:
        try:
            old = Game.objects.get(pk=instance.pk)
            instance._pre_save_final = old.final
            instance._pre_save_test_match = old.test_match
            instance._pre_save_round_id = old.round_id
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
        objects_to_update = []
        for effort in instance.efforts.select_related('faction', 'vagabond', 'player'):
            if effort.faction_id and effort.faction_id not in seen['faction']:
                seen['faction'].add(effort.faction_id)
                objects_to_update.append(_obj_to_tuple(effort.faction))
            if effort.vagabond_id and effort.vagabond_id not in seen['vagabond']:
                seen['vagabond'].add(effort.vagabond_id)
                objects_to_update.append(_obj_to_tuple(effort.vagabond))
            if effort.player_id and effort.player_id not in seen['player']:
                seen['player'].add(effort.player_id)
                objects_to_update.append(_obj_to_tuple(effort.player))
        if objects_to_update:
            from .tasks import update_cached_winrates
            update_cached_winrates.delay(objects_to_update)


@receiver(post_save, sender=Game)
def game_post_save_update_counts(sender, instance, **kwargs):
    """Refresh cached tournament counts when a game's countable state changes.
    Includes the old round's tournament when the game moved rounds."""
    ids = _tournament_ids_for_game(instance)
    old_round_id = getattr(instance, '_pre_save_round_id', None)
    if old_round_id and old_round_id != instance.round_id:
        ids |= _tournament_ids_for_round(old_round_id)
    _enqueue_tournament_counts(ids)


@receiver(pre_delete, sender=Game)
def game_pre_delete_snapshot_counts(sender, instance, **kwargs):
    """Snapshot the tournaments this game counts toward before it's deleted so
    post_delete can refresh them (relations are gone after delete)."""
    instance._pre_delete_tournament_ids = _tournament_ids_for_game(instance)


@receiver(post_delete, sender=Game)
def game_post_delete_update_counts(sender, instance, **kwargs):
    _enqueue_tournament_counts(getattr(instance, '_pre_delete_tournament_ids', set()))


@receiver(m2m_changed, sender=Game.extra_rounds.through)
def game_extra_rounds_changed_update_counts(sender, instance, action, pk_set, **kwargs):
    """Refresh counts for tournaments gained/lost via the extra_rounds M2M.
    Snapshot before a clear (pk_set is empty on pre_clear)."""
    if action == 'pre_clear':
        instance._pre_clear_extra_tournament_ids = set(
            Round.objects.filter(extra_games=instance)
            .values_list('stage__tournament_id', flat=True)
        )
        return
    ids = set()
    if action in ('post_add', 'post_remove') and pk_set:
        ids = set(
            Round.objects.filter(pk__in=pk_set)
            .values_list('stage__tournament_id', flat=True)
        )
    elif action == 'post_clear':
        ids = getattr(instance, '_pre_clear_extra_tournament_ids', set())
    _enqueue_tournament_counts(ids)