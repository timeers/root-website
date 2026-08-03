"""Local Elo rating engine.

Split into a pure math core (no DB) and a DB orchestration layer:

- `apply_game_to_ratings` — pure, unit-testable winner-vs-field Elo update.
- `game_participants` — resolve winners/losers/credit from a game's efforts.
- `_system_games` / `_eligible_games_for_system` — the games a system rates.
- `affected_local_system_ids` — which LOCAL systems a changed game touches.
- `elo_config_change_cutoff` — earliest date to replay from on a config edit.
- `recompute_system_from` — transactional forward-replay orchestrator.

Ratings are recomputed asynchronously by the scheduled `recompute_dirty_local_elo`
task, which replays each dirty system from its `recompute_from` watermark.
"""

from django.db import transaction
from django.db.models import Min, Q

from the_warroom.models import (
    Game, Effort, EloSystem, EloParticipant, EloRating,
)


# --------------------------------------------------------------------------- #
# Pure math (no DB access) — unit-testable in isolation.
# --------------------------------------------------------------------------- #

def _expected(ra, rb):
    """Expected score for a player rated `ra` against an opponent rated `rb`."""
    return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))


def _k(elo_system, games_played):
    """Provisional K while a player is below the provisional threshold, else the base K."""
    return (elo_system.k_provisional if games_played < elo_system.provisional_games
            else elo_system.k_factor)


def apply_game_to_ratings(elo_system, winners, losers, ratings, credit):
    """Winner-vs-field pairwise Elo. Pure — no DB.

    winners/losers: ordered lists of player_id.
    ratings: {player_id: (rating, games_played)} entering this game.
    credit: {winner_id: 1.0 or 0.5} — 0.5 each for a coalition win.

    Returns {player_id: (rating_before, rating_after)} for every participant.

    Degenerate games (no winners, no losers, or fewer than 2 rated players) return {}
    — this also prevents the opponent-count division below from ever dividing by zero.
    """
    if not winners or not losers or (len(winners) + len(losers)) < 2:
        return {}

    all_players = winners + losers
    winner_set = set(winners)
    deltas = {pid: 0.0 for pid in all_players}

    # Accumulate each player's pairwise deltas against every opponent, all computed from
    # their ENTERING rating (so within-game order doesn't matter).
    for w in winners:
        rw, gw = ratings[w]
        s = credit.get(w, 1.0)  # winner's pairwise score: 1.0 solo, 0.5 coalition
        for l in losers:
            rl, gl = ratings[l]
            deltas[w] += _k(elo_system, gw) * (s - _expected(rw, rl))
            deltas[l] += _k(elo_system, gl) * ((1.0 - s) - _expected(rl, rw))

    # NOTE: normalize each player's summed delta by their OPPONENT COUNT.
    #   Each player accrues one pairwise delta per opponent, so without this a 6-player win
    #   (5 pairwise deltas) would swing ratings ~5x a 2-player win (1 delta) for the same K.
    #   Dividing by opponent count makes a win/loss move a rating a comparable amount
    #   regardless of table size, so K keeps its usual "max points per game" meaning.
    #   This weights all game sizes EQUALLY (the intended behavior) — it is NOT the same as
    #   making 2-player games less impactful.
    #   To later weight games by size (e.g. count heads-up games for less), multiply by a
    #   SEPARATE, named size-weight factor here — keep it distinct from this fairness
    #   divisor rather than folding a tuning knob into it.
    n = len(all_players)
    results = {}
    for pid in all_players:
        opponents = n - 1
        rating_before = ratings[pid][0]
        rating_after = rating_before + (deltas[pid] / opponents)
        results[pid] = (rating_before, rating_after)
    return results


# --------------------------------------------------------------------------- #
# DB helpers.
# --------------------------------------------------------------------------- #

def game_participants(game):
    """Return (winners, losers, credit) from a game's efforts.

    - Efforts with no player are skipped.
    - credit = 0.5 per winner when game.coalition_win (shared win), else 1.0.
    - Returns ([], [], {}) for a degenerate game (no winner, no loser, or <2 rated
      players) so callers skip it — no rating rows written.
    """
    winners, losers = [], []
    for effort in game.efforts.all():
        if not effort.player_id:
            continue
        (winners if effort.win else losers).append(effort.player_id)

    # Guard: need at least one winner, one loser, and two distinct rated players.
    if not winners or not losers or len(set(winners) | set(losers)) < 2:
        return [], [], {}

    win_value = 0.5 if game.coalition_win else 1.0
    credit = {pid: win_value for pid in winners}
    return winners, losers, credit


def _bounds_q(sys):
    """Q matching games whose cached player count is within this system's bounds."""
    return Q(cached_player_count__gte=sys.min_players,
             cached_player_count__lte=sys.max_players)


def _system_games(elo_system):
    """Final, non-test games structurally attached to this system (NO player-count filter)."""
    return Game.objects.filter(final=True, test_match=False).filter(
        Q(round__stage__tournament__elo_system=elo_system) |
        Q(extra_rounds__stage__tournament__elo_system=elo_system)
    ).distinct()


def _apply_bounds(qs, sys):
    return qs.filter(_bounds_q(sys))


def _eligible_games_for_system(elo_system, since=None):
    """Games this system rates, chronologically ordered. Optionally only >= `since`."""
    qs = _apply_bounds(_system_games(elo_system), elo_system)
    if since is not None:
        qs = qs.filter(date_posted__gte=since)
    return qs.order_by('date_posted', 'id')


def affected_local_system_ids(game):
    """LOCAL EloSystem ids a changed game touches: structurally attached OR with history.

    The import of `_tournament_ids_for_game` is function-local to avoid a circular import
    (signals.py imports this module at load time)."""
    from the_warroom.signals import _tournament_ids_for_game  # local: avoid circular import
    tids = _tournament_ids_for_game(game)
    structural = EloSystem.objects.filter(
        calculation_type=EloSystem.CalculationType.LOCAL, tournaments__id__in=tids
    ).values_list('id', flat=True)
    historical = EloRating.objects.filter(
        game=game, elo_system__calculation_type=EloSystem.CalculationType.LOCAL
    ).values_list('elo_system_id', flat=True)
    return set(structural) | set(historical)


def elo_config_change_cutoff(old, new):
    """Earliest date_posted whose stored ratings must change given an EloSystem config edit.

    The cutoff must cover every game affected under EITHER config, so games dropped by a
    narrowing change still get their stale rows wiped, and games earlier than the current
    earliest-eligible aren't left stale. Returns None if nothing local-affecting changed.
    """
    old_local = old.calculation_type == EloSystem.CalculationType.LOCAL
    new_local = new.calculation_type == EloSystem.CalculationType.LOCAL
    if not (old_local or new_local):
        return None  # rootelo -> rootelo: never touches local ratings

    math_changed = any(getattr(old, f) != getattr(new, f) for f in EloSystem.MATH_FIELDS)
    bounds_changed = (old.min_players, old.max_players) != (new.min_players, new.max_players)
    type_changed = old.calculation_type != new.calculation_type

    attached = _system_games(new)  # structural; NO count filter
    dates = []

    if math_changed:
        # Every ever-rated game changes -> union of old- and new-eligible sets.
        dates.append(attached.filter(_bounds_q(old) | _bounds_q(new))
                     .aggregate(m=Min('date_posted'))['m'])
    if bounds_changed and not math_changed:
        # Only games that flip eligibility -> symmetric difference (XOR).
        dates.append(attached.filter(_bounds_q(old) ^ _bounds_q(new))
                     .aggregate(m=Min('date_posted'))['m'])
    if type_changed:
        # local<->rootelo: the LOCAL-side eligible set turns on/off.
        local_side = new if new_local else old
        dates.append(_apply_bounds(attached, local_side)
                     .aggregate(m=Min('date_posted'))['m'])

    dates = [d for d in dates if d is not None]
    return min(dates) if dates else None


def _anchor_ratings_before(elo_system, player_ids, cutoff_dt):
    """Reconstruct each player's (rating, games_played, wins) as of just before cutoff_dt.

    One query over pre-cutoff EloRating rows: rating = last row's rating_after,
    games_played = row count, wins = sum(win_credit). Players with no pre-cutoff rows are
    absent (seeded with initial_rating when first seen during replay).
    """
    working = {}
    rows = (EloRating.objects
            .filter(elo_system=elo_system, player_id__in=player_ids, played_at__lt=cutoff_dt)
            .order_by('player_id', 'played_at', 'game_id')
            .values_list('player_id', 'rating_after', 'win_credit'))
    for player_id, rating_after, win_credit in rows:
        if player_id not in working:
            working[player_id] = [rating_after, 0, 0.0]
        working[player_id][0] = rating_after   # last row wins (ordered ascending)
        working[player_id][1] += 1
        working[player_id][2] += win_credit
    return working


def _write_participants(elo_system, working):
    """Upsert EloParticipant rows for every player in `working` ({pid: [rating, games, wins]})."""
    existing = {p.player_id: p for p in
                EloParticipant.objects.filter(elo_system=elo_system,
                                              player_id__in=working.keys())}
    to_create, to_update = [], []
    for pid, (rating, games, wins) in working.items():
        p = existing.get(pid)
        if p:
            p.rating, p.games_played, p.wins = rating, games, wins
            to_update.append(p)
        else:
            to_create.append(EloParticipant(elo_system=elo_system, player_id=pid,
                                             rating=rating, games_played=games, wins=wins))
    if to_create:
        EloParticipant.objects.bulk_create(to_create, batch_size=500)
    if to_update:
        EloParticipant.objects.bulk_update(
            to_update, ['rating', 'games_played', 'wins'], batch_size=500)


# --------------------------------------------------------------------------- #
# Orchestrator.
# --------------------------------------------------------------------------- #

def recompute_system_from(elo_system, cutoff_dt):
    """Replay LOCAL elo for one system from cutoff_dt forward. Caller ensures LOCAL.

    Re-anchors affected players to their state just before cutoff, deletes the history
    from cutoff onward, replays every eligible game chronologically, and upserts the
    current standings. Wrapped in one transaction with a coarse per-system lock.
    """
    with transaction.atomic():
        # Coarse per-system lock so overlapping replays serialize.
        EloSystem.objects.select_for_update().get(pk=elo_system.pk)

        games = list(_eligible_games_for_system(elo_system, since=cutoff_dt))
        affected = set(Effort.objects.filter(game__in=games, player__isnull=False)
                       .values_list('player_id', flat=True))

        # 1. Re-anchor affected players to their state as of just before cutoff.
        working = _anchor_ratings_before(elo_system, affected, cutoff_dt)

        # 2. Drop the history we're rewriting.
        EloRating.objects.filter(elo_system=elo_system, played_at__gte=cutoff_dt).delete()

        # 3. Replay chronologically.
        new_rows = []
        for game in games:
            winners, losers, credit = game_participants(game)
            if not winners or not losers:
                continue  # degenerate game — skip (no rating rows written)
            for pid in winners + losers:
                working.setdefault(pid, [elo_system.initial_rating, 0, 0.0])
            ratings = {pid: (working[pid][0], working[pid][1]) for pid in winners + losers}
            results = apply_game_to_ratings(elo_system, winners, losers, ratings, credit)
            winner_set = set(winners)
            for pid, (before, after) in results.items():
                wc = credit.get(pid, 0.0) if pid in winner_set else 0.0
                new_rows.append(EloRating(
                    elo_system=elo_system, game=game, player_id=pid,
                    rating_before=before, rating_after=after,
                    win_credit=wc, played_at=game.date_posted))
                working[pid][0] = after
                working[pid][1] += 1
                working[pid][2] += wc
        if new_rows:
            EloRating.objects.bulk_create(new_rows, batch_size=500)

        # 4. Upsert current standings for all touched players.
        _write_participants(elo_system, working)
