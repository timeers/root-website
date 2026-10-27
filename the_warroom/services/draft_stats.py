"""Per-seat draft pick rates for the /statistics/drafting/ page.

Reads the SeatDraftOption cache (see draft_service.py, which writes it): each row
is one option one seat could have taken, and `chosen` marks the one it did. The
statistic is the PICK RATE WHEN OFFERED -- of the times a faction was available
to a seat, how often that seat took it -- so it measures demand independent of
how often the faction turns up in drafts.

The Overall column is PER GAME, not per seat: of the games a faction was in the
draft at all (taken by anyone, or left as the undrafted faction), how many
someone took it in. Summing seats would count one game's pool once per seat that
saw it.

Pure functions over a games queryset, like draft_service.py, so the view stays
thin and the numbers are testable without a request.
"""
from django.db.models import Count, Q

DEFAULT_MIN_OFFERED = 10


def _cell(taken, offered):
    """One table cell. rate is None when never offered -- unknown, not 0%."""
    return {
        'taken': taken,
        'offered': offered,
        'rate': (taken / offered) if offered else None,
    }


def _table(counts, overall_counts, objects, seats, min_offered):
    """Rows for one table from {key: {seat: (taken, offered)}} and the per-game
    {key: (taken_games, offered_games)}.

    Sorted by overall pick rate, highest first, then name -- except rows in the
    draft in fewer than `min_offered` games, which are flagged `low_sample` and
    sorted after the rest: a faction in one draft and taken would otherwise top
    the table at 100%.
    """
    rows = []
    for key, by_seat in counts.items():
        obj = objects.get(key)
        if obj is None:
            continue
        cells = [_cell(*by_seat.get(seat, (0, 0))) for seat in seats]
        overall = _cell(*overall_counts.get(key, (0, 0)))
        rows.append({
            'object': obj,
            'seats': cells,
            'overall': overall,
            'low_sample': overall['offered'] < min_offered,
        })
    rows.sort(key=lambda r: (r['low_sample'], -(r['overall']['rate'] or 0),
                             r['object'].title.lower()))
    return rows


def draft_pick_rates(games, player_count, min_offered=DEFAULT_MIN_OFFERED,
                     player=None):
    """Pick rates for `games` (a Game queryset), seats 1..player_count.

    With `player` (a Profile), only the seats THEY sat in are counted, so the
    rates describe their own drafting: what they took when it was available.

    Returns a dict:
      seats         [1, 2, ..., player_count]
      factions      rows for every faction -- every vagabond character adds into
                    the one shared "Vagabond" faction row
      vagabonds     rows per vagabond character (empty when none was drafted)
      games_counted games that actually contributed draft data -- only drafted
                    games have SeatDraftOption rows, so this can be lower than
                    the filtered game count

    Each row: {'object', 'seats': [cell per seat], 'overall': cell, 'low_sample'}
    where a cell is {'taken', 'offered', 'rate'}. Seat cells count OFFERS to that
    seat; the overall cell counts GAMES (see the module docstring). With
    `player`, each game is only their one seat, so the two agree.

    The undrafted leftover is in every seat's pool and never chosen, so it counts
    as offered-but-not-taken at every seat -- right for "taken when available".
    """
    from the_keep.models import Faction, Vagabond
    from the_warroom.models import SeatDraftOption

    seats = list(range(1, player_count + 1))
    options = SeatDraftOption.objects.filter(
        # A pk subquery: the filtered games queryset may be annotated and
        # .distinct() (GameFilter's "contains all" filters), which a values()
        # subquery handles cleanly.
        effort__game__in=games.values('pk'),
        seat__in=seats,
    )
    if player is not None:
        options = options.filter(effort__player=player)
    grouped = (options.values('faction_id', 'vagabond_id', 'seat')
               .annotate(offered=Count('id'),
                         taken=Count('id', filter=Q(chosen=True))))

    faction_counts, vagabond_counts = {}, {}
    for row in grouped:
        seat, taken, offered = row['seat'], row['taken'], row['offered']
        by_seat = faction_counts.setdefault(row['faction_id'], {})
        prev = by_seat.get(seat, (0, 0))
        by_seat[seat] = (prev[0] + taken, prev[1] + offered)
        if row['vagabond_id']:
            v_seat = vagabond_counts.setdefault(row['vagabond_id'], {})
            prev = v_seat.get(seat, (0, 0))
            v_seat[seat] = (prev[0] + taken, prev[1] + offered)

    # Per game: distinct games an option was in the draft (any seat's pool),
    # and distinct games someone took it. Grouped by faction for the faction
    # table -- a game with two vagabonds counts once for the "Vagabond" row --
    # and by character for the vagabond table.
    def per_game(key):
        return {row[key]: (row['taken'], row['offered'])
                for row in (options.exclude(**{f'{key}__isnull': True})
                            .values(key)
                            .annotate(offered=Count('effort__game', distinct=True),
                                      taken=Count('effort__game', distinct=True,
                                                  filter=Q(chosen=True))))}

    faction_overall = per_game('faction_id') if faction_counts else {}
    vagabond_overall = per_game('vagabond_id') if vagabond_counts else {}

    factions = Faction.objects.in_bulk(list(faction_counts))
    vagabonds = Vagabond.objects.in_bulk(list(vagabond_counts))
    games_counted = (options.values('effort__game_id').distinct().count()
                     if faction_counts else 0)

    return {
        'seats': seats,
        'factions': _table(faction_counts, faction_overall, factions, seats, min_offered),
        'vagabonds': _table(vagabond_counts, vagabond_overall, vagabonds, seats,
                            min_offered),
        'games_counted': games_counted,
    }
