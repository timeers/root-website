from django.db.models.signals import pre_delete, pre_save, post_save, post_delete, m2m_changed
from django.dispatch import receiver
from django.db.models import Min
from django.utils import timezone
from .models import Effort, Game, ScoreCard, Tournament, Round, Stage, Match, CompetitionStatus, EloSystem, EloSeason
from .services.slugify_titles import slugify_tournament_name, slugify_round_name, slugify_stage_name, slugify_elo_system_name


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


def _mark_local_systems_dirty(system_ids, dt):
    """Lower the recompute_from watermark on the given LOCAL EloSystems. Signals-only
    (no calculation) — the scheduled recompute_dirty_local_elo task does the replay."""
    ids = {i for i in system_ids if i}
    if not ids:
        return
    for s in EloSystem.objects.filter(
        pk__in=ids, calculation_type=EloSystem.CalculationType.LOCAL
    ):
        s.mark_dirty_from(dt)

@receiver(pre_save, sender=Effort)
def effort_pre_save_snapshot(sender, instance, **kwargs):
    """Snapshot old FK values so post_save can recalculate the old faction/vagabond/player if changed."""
    if instance.pk:
        try:
            old = Effort.objects.get(pk=instance.pk)
            instance._old_faction_id = old.faction_id
            instance._old_vagabond_id = old.vagabond_id
            instance._old_player_id = old.player_id
            instance._old_win = old.win  # for elo dirty-marking (win change affects result)
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


@receiver(post_save, sender=Effort)
@receiver(post_delete, sender=Effort)
def handle_effort_change_update_game_count(sender, instance, **kwargs):
    """Refresh the game's denormalized cached_player_count. Async (like tournament counts)
    to avoid writing the game once per effort when several are added/removed together."""
    if instance.game_id:
        from .tasks import update_game_player_count
        update_game_player_count.delay(instance.game_id)


@receiver(post_save, sender=Effort)
def effort_saved_mark_elo_dirty(sender, instance, created, **kwargs):
    """Mark local elo systems dirty only when who-played or who-won changed. Edits to
    score/faction/vagabond/dominance/etc. don't affect a winner-vs-field result."""
    if not (created
            or getattr(instance, '_old_player_id', instance.player_id) != instance.player_id
            or getattr(instance, '_old_win', instance.win) != instance.win):
        return
    try:
        game = instance.game
    except Game.DoesNotExist:
        return
    if game.final and not game.test_match:
        from .services.elo_service import affected_local_system_ids
        _mark_local_systems_dirty(affected_local_system_ids(game), game.date_posted)


@receiver(post_delete, sender=Effort)
def effort_deleted_mark_elo_dirty(sender, instance, **kwargs):
    """Removing a seat changes the game's result — mark its local elo systems dirty."""
    game = Game.objects.filter(pk=instance.game_id).first()
    if game and game.final and not game.test_match:
        from .services.elo_service import affected_local_system_ids
        _mark_local_systems_dirty(affected_local_system_ids(game), game.date_posted)


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


@receiver(pre_save, sender=EloSystem)
def elo_system_pre_save(sender, instance, update_fields=None, *args, **kwargs):
    if instance.slug is None:
        slugify_elo_system_name(instance, save=False)
    elif instance.pk and _slug_should_follow_name(instance, EloSystem, update_fields):
        slugify_elo_system_name(instance, save=False)

@receiver(post_save, sender=EloSystem)
def elo_system_post_save(sender, instance, created, *args, **kwargs):
    if created:
        slugify_elo_system_name(instance, save=True)


def _earliest_eligible_game_date(elo_system):
    """Earliest date_posted of a game currently eligible for this system, or None."""
    from .services.elo_service import _eligible_games_for_system
    return _eligible_games_for_system(elo_system).aggregate(m=Min('date_posted'))['m']


@receiver(pre_save, sender=Tournament)
def tournament_pre_save_snapshot_elo(sender, instance, **kwargs):
    """Snapshot the old elo_system so post_save can detect an assignment change."""
    if instance.pk:
        instance._pre_save_elo_system_id = (
            Tournament.objects.filter(pk=instance.pk)
            .values_list('elo_system_id', flat=True).first()
        )
    else:
        instance._pre_save_elo_system_id = None


@receiver(post_save, sender=Tournament)
def tournament_post_save_mark_elo_dirty(sender, instance, **kwargs):
    """When a tournament gains or loses an elo_system, dirty BOTH the old and new LOCAL
    system from their earliest eligible game (or now, if none yet)."""
    old_id = getattr(instance, '_pre_save_elo_system_id', None)
    new_id = instance.elo_system_id
    if old_id == new_id:
        return
    for sid in {old_id, new_id}:
        if not sid:
            continue
        system = EloSystem.objects.filter(
            pk=sid, calculation_type=EloSystem.CalculationType.LOCAL).first()
        if system:
            cutoff = _earliest_eligible_game_date(system) or timezone.now()
            system.mark_dirty_from(cutoff)


@receiver(pre_save, sender=EloSystem)
def elo_system_pre_save_mark_dirty(sender, instance, update_fields=None, **kwargs):
    """Dirty a LOCAL system when an eligibility or math field changes. Sets recompute_from
    directly on the instance (persists with this same save; no recursion — the sweeper
    clears it via .update() which bypasses signals)."""
    if not instance.pk:
        return  # new system: no history to recompute
    # Skip partial saves that can't touch a recompute field (e.g. clearing recompute_from).
    if update_fields is not None and not (set(EloSystem.RECOMPUTE_FIELDS) & set(update_fields)):
        return
    try:
        old = EloSystem.objects.get(pk=instance.pk)
    except EloSystem.DoesNotExist:
        return
    was_local = old.calculation_type == EloSystem.CalculationType.LOCAL
    is_local = instance.calculation_type == EloSystem.CalculationType.LOCAL
    if not (was_local or is_local):
        return  # rootelo <-> rootelo edits never affect local ratings
    if not any(getattr(old, f) != getattr(instance, f) for f in EloSystem.RECOMPUTE_FIELDS):
        return
    from .services.elo_service import elo_config_change_cutoff
    cutoff = elo_config_change_cutoff(old, instance)
    if cutoff is not None:
        instance.recompute_from = min(cutoff, instance.recompute_from or cutoff)


# --- EloSeason: adding/moving/retyping a season boundary changes stored ratings from that
#     date forward, so dirty the (LOCAL) system so the scheduled recompute re-derives resets.
#     A NONE boundary that neither moves nor changes type only affects the live-computed
#     MatchAPI season label (no stored rating), so it can be skipped. ---

def _season_affects_ratings(reset_mode):
    return reset_mode in (EloSeason.ResetMode.HARD, EloSeason.ResetMode.SOFT)


@receiver(pre_save, sender=EloSeason)
def elo_season_pre_save_snapshot(sender, instance, **kwargs):
    """Snapshot the old start_date/reset_mode so post_save can tell what changed."""
    if instance.pk:
        old = EloSeason.objects.filter(pk=instance.pk).first()
        instance._old_start_date = old.start_date if old else None
        instance._old_reset_mode = old.reset_mode if old else None
    else:
        instance._old_start_date = None
        instance._old_reset_mode = None


@receiver(post_save, sender=EloSeason)
def elo_season_saved_mark_dirty(sender, instance, created, **kwargs):
    """Mark the system dirty from the earliest boundary date the change affects."""
    old_start = getattr(instance, '_old_start_date', None)
    old_mode = getattr(instance, '_old_reset_mode', None)

    if created:
        if _season_affects_ratings(instance.reset_mode):
            _mark_local_systems_dirty([instance.elo_system_id], instance.start_date)
        return

    # Update. Dirty when the boundary moved, or the reset behavior changed, or it is
    # (still) a resetting season being edited. Skip a pure NONE->NONE no-move edit.
    moved = old_start is not None and old_start != instance.start_date
    mode_changed = old_mode != instance.reset_mode
    if not (moved or mode_changed
            or _season_affects_ratings(instance.reset_mode)):
        return
    dt = min(instance.start_date, old_start) if moved else instance.start_date
    _mark_local_systems_dirty([instance.elo_system_id], dt)


@receiver(post_delete, sender=EloSeason)
def elo_season_deleted_mark_dirty(sender, instance, **kwargs):
    """Removing a boundary merges its games into the previous season — dirty from its start."""
    if _season_affects_ratings(instance.reset_mode):
        _mark_local_systems_dirty([instance.elo_system_id], instance.start_date)


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
    """Snapshot final, test_match, round, date_posted and elo systems so post_save can
    detect elo-relevant changes (a game that leaves a system must still dirty it)."""
    if instance.pk:
        try:
            old = Game.objects.get(pk=instance.pk)
            instance._pre_save_final = old.final
            instance._pre_save_test_match = old.test_match
            instance._pre_save_round_id = old.round_id
            instance._pre_save_date_posted = old.date_posted
            from .services.elo_service import affected_local_system_ids
            instance._pre_save_elo_system_ids = affected_local_system_ids(old)
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


@receiver(post_save, sender=Game)
def game_post_save_mark_elo_dirty(sender, instance, created, **kwargs):
    """Mark local elo systems dirty when an elo-relevant field changed (final, test_match,
    date_posted) or a game was created as final. Effort/round changes are handled by their
    own signals, so we don't dirty on every save of a final game (e.g. a notes edit)."""
    old_final = getattr(instance, '_pre_save_final', None)
    old_test_match = getattr(instance, '_pre_save_test_match', None)
    old_date_posted = getattr(instance, '_pre_save_date_posted', instance.date_posted)
    relevant_change = (
        created
        or old_final != instance.final
        or old_test_match != instance.test_match
        or old_date_posted != instance.date_posted
    )
    if not relevant_change:
        return
    from .services.elo_service import affected_local_system_ids
    ids = getattr(instance, '_pre_save_elo_system_ids', set()) | affected_local_system_ids(instance)
    if ids:
        cutoff = min(old_date_posted, instance.date_posted)
        _mark_local_systems_dirty(ids, cutoff)


@receiver(pre_delete, sender=Game)
def game_pre_delete_snapshot_elo(sender, instance, **kwargs):
    """Snapshot elo systems + date before delete so post_delete can mark them dirty."""
    from .services.elo_service import affected_local_system_ids
    instance._pre_delete_elo_system_ids = affected_local_system_ids(instance)
    instance._pre_delete_date_posted = instance.date_posted


@receiver(post_delete, sender=Game)
def game_post_delete_mark_elo_dirty(sender, instance, **kwargs):
    ids = getattr(instance, '_pre_delete_elo_system_ids', set())
    dt = getattr(instance, '_pre_delete_date_posted', None)
    if ids and dt is not None:
        _mark_local_systems_dirty(ids, dt)


@receiver(m2m_changed, sender=Game.extra_rounds.through)
def game_extra_rounds_changed_mark_elo_dirty(sender, instance, action, pk_set, **kwargs):
    """Adding/removing a final game from an elo-linked extra round must dirty that system.
    post_save does not fire for M2M edits, so this is required. Mirrors
    game_extra_rounds_changed_update_counts."""
    if action == 'pre_clear':
        instance._pre_clear_extra_elo_system_ids = set(
            Round.objects.filter(extra_games=instance)
            .values_list('stage__tournament__elo_system_id', flat=True)
        )
        return
    if not instance.final or instance.test_match:
        return
    system_ids = set()
    if action in ('post_add', 'post_remove') and pk_set:
        system_ids = set(
            Round.objects.filter(pk__in=pk_set)
            .values_list('stage__tournament__elo_system_id', flat=True)
        )
    elif action == 'post_clear':
        system_ids = getattr(instance, '_pre_clear_extra_elo_system_ids', set())
    _mark_local_systems_dirty(system_ids, instance.date_posted)


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