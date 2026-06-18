from django import template

register = template.Library()


@register.filter
def is_series_winner(series, tp_id):
    """Check if a TournamentPlayer ID is among the series winners.

    Reads from ``series.winners.all()`` so that a prefetched ``winners``
    queryset is reused instead of issuing a query per call.
    """
    if not hasattr(series, '_winner_tp_ids_cache'):
        series._winner_tp_ids_cache = {
            winner.tournament_player_id for winner in series.winners.all()
        }
    return tp_id in series._winner_tp_ids_cache
