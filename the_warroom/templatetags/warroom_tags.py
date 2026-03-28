from django import template

register = template.Library()


@register.filter
def is_series_winner(series, tp_id):
    """Check if a TournamentPlayer ID is among the series winners."""
    if not hasattr(series, '_winner_tp_ids_cache'):
        series._winner_tp_ids_cache = set(
            series.winners.values_list('tournament_player_id', flat=True)
        )
    return tp_id in series._winner_tp_ids_cache
