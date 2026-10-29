"""Derivation of Effort.win_share: each effort's share of its game's win.

A game is worth 1.0, split evenly among its winners -- one winner gets 1.0, three
winners get 1/3 each, losers get 0. Pure functions (no signal/task knowledge) so the
Celery task and the recalculate_win_shares management command share one
implementation, mirroring draft_service.py.
"""
from django.db import transaction


def win_share_for(winner_count):
    """A winner's share of a game with `winner_count` winners."""
    return 1 / winner_count if winner_count else 0.0


def refresh_game_win_shares(game):
    """Recompute and persist win_share for every effort on `game`. Returns the number of
    efforts rewritten.

    Writes with queryset.update() so it never runs Effort.save() or its signals (which
    would re-enqueue this refresh), and excludes rows already correct so a no-op save
    doesn't churn the table.
    """
    from the_warroom.models import Effort, Game

    with transaction.atomic():
        # Serialize concurrent runs for one game: without the lock, a run that counted
        # before another edit committed could overwrite the newer run's correct share
        # with its stale one. Any edit committed after this count enqueues another run,
        # which waits here and then counts fresh data.
        if not list(Game.objects.select_for_update().filter(pk=game.pk).values_list('pk', flat=True)):
            return 0
        efforts = Effort.objects.filter(game_id=game.pk)
        share = win_share_for(efforts.filter(win=True).count())
        changed = efforts.filter(win=True).exclude(win_share=share).update(win_share=share)
        changed += efforts.filter(win=False).exclude(win_share=0).update(win_share=0)
        # Game.coalition_win is derived from the efforts too. Effort.save keeps it
        # current on edits; this catches deletes. update() skips Game signals.
        coalition = efforts.filter(win=True, coalition_with__isnull=False).exists()
        Game.objects.filter(pk=game.pk).exclude(coalition_win=coalition).update(
            coalition_win=coalition)
    return changed
