"""Standings for a Qualifier: one row per player with at least one game in the
qualifier's window, from its single source (an Elo system or a Tournament).

Counting and win points run in SQL (one GROUP BY player); Python only sorts one
small row per distinct player. Ranks are assigned over the FULL list, before any
search / threshold filter the view applies, so filtering never renumbers players.
The finished list is cached briefly so search keystrokes and page clicks don't
recompute it.
"""
from dataclasses import dataclass

from django.core.cache import cache
from django.core.files.storage import default_storage
from django.db.models import Count
from django.urls import reverse

from the_gatehouse.models import DEFAULT_PROFILE_IMAGE
from the_warroom.models import (
    EloParticipant, EloSystem, Effort, Game,
    elo_eligibility_q, game_counts_for_elo_system_q, game_counts_for_tournament_q,
)
from the_warroom.services.win_rate import points_annotations

CACHE_TTL = 5 * 60  # new games appear within this long


@dataclass
class Row:
    # Plain values (no model instances) so the list pickles cleanly into the cache.
    player_id: int
    name: str
    discord: str
    slug: str
    games: int
    score: float | None  # current Elo rating, or win rate percent
    rank: int | None = None  # only for players who met game_threshold
    qualified: bool = False
    image: str = ''  # storage name of Profile.image; defaulted so older cached rows still load

    @property
    def provisional(self):
        return self.rank is None

    @property
    def image_url(self):
        return default_storage.url(self.image or DEFAULT_PROFILE_IMAGE)

    @property
    def url(self):
        return reverse('player-detail', kwargs={'slug': self.slug}) if self.slug else None


def _window_games(qualifier):
    lo, hi = qualifier.window()
    games = Game.objects.filter(final=True, test_match=False,
                                date_posted__gte=lo, date_posted__lt=hi)
    if qualifier.is_elo:
        return games.filter(game_counts_for_elo_system_q(qualifier.elo_system),
                            elo_eligibility_q(qualifier.elo_system))
    return games.filter(game_counts_for_tournament_q(qualifier.tournament))


def _compute(qualifier):
    if qualifier.source is None:  # its source was deleted (SET_NULL)
        return []

    # A game__in subquery rather than a join through extra_rounds: the join repeats
    # effort rows, which a win_share Sum can't de-duplicate (see win_rate.py).
    efforts = Effort.objects.filter(
        game__in=_window_games(qualifier).values('pk'), player__isnull=False
    )
    annotations = {'games': Count('game', distinct=True), 'efforts': Count('id')}
    if not qualifier.is_elo:
        annotations['points'] = points_annotations()['points']
    aggregates = (
        efforts.values('player_id', 'player__display_name', 'player__discord', 'player__slug',
                      'player__image')
               .annotate(**annotations)
               .order_by()
    )

    rows = [
        Row(
            player_id=a['player_id'],
            name=a['player__display_name'] or a['player__discord'] or '',
            discord=a['player__discord'] or '',
            slug=a['player__slug'] or '',
            image=a['player__image'] or '',
            games=a['games'],
            score=None if qualifier.is_elo else (a['points'] or 0) / a['efforts'] * 100,
        )
        for a in aggregates
    ]

    if qualifier.is_elo:
        system = qualifier.elo_system
        # RootELO marks players it hasn't ranked yet with rank=NULL; their feed
        # rating isn't treated as real, so they show a dash and can't qualify.
        # LOCAL systems never set rank, so the check only applies to RootELO.
        require_rank = system.calculation_type == EloSystem.CalculationType.ROOTELO
        participants = EloParticipant.objects.filter(
            elo_system=system, player_id__in=[r.player_id for r in rows]
        ).values_list('player_id', 'rating', 'rank')
        ratings = {
            pid: rating for pid, rating, elo_rank in participants
            if not require_rank or elo_rank is not None
        }
        for r in rows:
            r.score = ratings.get(r.player_id)

    # One interleaved list: score desc (missing scores last), then games, then name.
    rows.sort(key=lambda r: (r.score is None, -(r.score or 0), -r.games, r.name.lower()))

    rank = 0
    for r in rows:
        # An Elo qualifier also needs a rating: players with no participant row,
        # or unranked on RootELO, get no qualifier rank and so can't qualify.
        if qualifier.is_elo and r.score is None:
            continue
        if r.games >= qualifier.game_threshold:
            rank += 1
            r.rank = rank
            r.qualified = rank <= qualifier.qualifier_places
    return rows


def build_standings(qualifier):
    """Cached standings. updated_at is in the key, so editing the qualifier busts it."""
    key = f'qualifier-standings:{qualifier.pk}:{qualifier.updated_at.timestamp()}'
    rows = cache.get(key)
    if rows is None:
        rows = _compute(qualifier)
        cache.set(key, rows, CACHE_TTL)
    return rows
