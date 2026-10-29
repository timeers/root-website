"""How a win counts toward win rates and tournament points -- one switchable rule.

settings.WIN_RATE_MODE picks it (see settings.py):

  'win_share' -- sum Effort.win_share: a game is worth 1.0 split among its
                 winners (win_share_service.py keeps it current).
  'coalition' -- the previous rule, kept so it can be switched back to: wins
                 minus half the wins in Game.coalition_win games. Sites that
                 used their own older rule (raw counts, the coalition_with
                 effort alone) get exactly that rule back in this mode.

Every win-rate site except Elo and draft statistics builds its numbers from
these helpers, so the rule lives here. The expressions work in both
.annotate() and .aggregate(): each is self-contained rather than F()-referencing
a sibling, which aggregate() can't do.

Sum() has no row-level distinct -- Sum(distinct=True) drops equal VALUES, so two
0.5 winners would sum to 0.5 -- so a win_share sum must never run over a join
that repeats effort rows. points_annotations() refuses distinct in that mode,
and the rosters (which do repeat rows) are flattened by annotate_roster().
"""
from django.conf import settings
from django.db.models import (
    Case, Count, ExpressionWrapper, F, FloatField, Q, Sum, Value, When,
)
from django.db.models.functions import Cast, Coalesce

WIN_SHARE = 'win_share'
COALITION = 'coalition'


def win_rate_mode():
    """The active rule. Read at call time so override_settings works in tests."""
    mode = getattr(settings, 'WIN_RATE_MODE', WIN_SHARE)
    if mode not in (WIN_SHARE, COALITION):
        raise ValueError(f'Unknown WIN_RATE_MODE {mode!r}')
    return mode


def _coalition_counts(prefix, q, distinct):
    """The previous rule's raw win and coalition-win Counts over `prefix`
    efforts ('' when aggregating Efforts directly, 'efforts__' from a Profile or
    Faction), restricted by `q`."""
    target = f'{prefix}id' if prefix else 'id'
    win_q = Q(**{f'{prefix}win': True})
    coalition_q = win_q & Q(**{f'{prefix}game__coalition_win': True})
    if q is not None:
        win_q &= q
        coalition_q &= q
    return (Count(target, filter=win_q, distinct=distinct),
            Count(target, filter=coalition_q, distinct=distinct))


def _coalition_points(win_count, coalition_count):
    return ExpressionWrapper(
        Cast(win_count, FloatField()) - Cast(coalition_count, FloatField()) / 2,
        output_field=FloatField(),
    )


def points_annotations(prefix='', q=None, distinct=False):
    """{name: expression} for .annotate()/.aggregate(): `points` (the wins the
    rate is built from) and `display_wins` (the win figure a page shows).

    win_share mode: points = display_wins = the summed win_share.
    coalition mode: points = wins - coalition wins / 2, display_wins = the raw
    win count (what those pages showed), plus `win_count` / `coalition_count`.

    `distinct` is for querysets whose joins repeat effort rows; it's only
    meaningful for the coalition Counts (see the module docstring).
    """
    if win_rate_mode() == WIN_SHARE:
        if distinct:
            raise ValueError(
                'Sum(win_share) cannot be de-duplicated with distinct; '
                'filter without repeating rows (game__in) or use annotate_roster().')
        points = Coalesce(Sum(f'{prefix}win_share', filter=q), 0.0,
                          output_field=FloatField())
        return {'points': points, 'display_wins': points}

    wins, coalition = _coalition_counts(prefix, q, distinct)
    return {
        'win_count': wins,
        'coalition_count': coalition,
        'points': _coalition_points(wins, coalition),
        'display_wins': wins,
    }


def win_rate_pct(points_field='points', total_field='total_efforts'):
    """0-100 win rate from two existing annotations; 0 when there are none."""
    return Case(
        When(**{total_field: 0}, then=Value(0.0)),
        default=ExpressionWrapper(
            Cast(F(points_field), FloatField()) / Cast(F(total_field), FloatField()) * 100,
            output_field=FloatField(),
        ),
        output_field=FloatField(),
    )


def effort_points(effort, legacy):
    """One effort's points, for code already holding efforts.

    In coalition mode each site keeps the rule it used before, named by `legacy`:
      'raw'            -- 1 for a win (stage advancement)
      'coalition_with' -- 0.5 for the winner holding coalition_with, else 1
                          (the game API's tournament_score)
    """
    if win_rate_mode() == WIN_SHARE:
        return effort.win_share or 0.0
    if not effort.win:
        return 0
    if legacy == 'raw':
        return 1
    if legacy == 'coalition_with':
        return 0.5 if effort.coalition_with_id else 1
    raise ValueError(f'Unknown legacy rule {legacy!r}')


def annotate_roster(players, counts_q, scoped_games):
    """Annotate a Profile roster -- the tournament / stage / round rosters --
    with total_efforts, points and display_wins.

    `players` is the roster's own queryset, whose filter joins through extra
    rounds and participations and so repeats effort rows (hence its
    .distinct()); `counts_q` is its effort Q; `scoped_games` is a Game queryset
    of the scope's counting games, already final-filtered.

    coalition mode: the previous Count(distinct=True) annotations over that
    join, unchanged.

    win_share mode: the roster is flattened to a pk__in set first, so the only
    join left is the players' efforts -- one row each -- restricted to the
    scope with game__in. A plain Sum is then exact. (A correlated Subquery per
    player was tried and is far slower: the scope's game lookup re-runs for
    every row.)
    """
    if win_rate_mode() == WIN_SHARE:
        in_scope = Q(efforts__game__in=scoped_games.values('pk'))
        return (players.model.objects.filter(pk__in=players.values('pk'))
                .annotate(total_efforts=Count('efforts', filter=in_scope),
                          **points_annotations('efforts__', in_scope)))

    final_q = counts_q & Q(efforts__game__final=True)
    return players.annotate(
        total_efforts=Count('efforts', distinct=True, filter=final_q),
        **points_annotations('efforts__', final_q, distinct=True),
    )
