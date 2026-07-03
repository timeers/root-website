def calculate_and_cache_winrate(obj):
    """
    Recalculate and cache the winrate for a Faction, Vagabond, or Profile instance.
    Uses the coalition-based leaderboard formula (coalition wins count as half),
    the site's single source of truth via filtered_winrate():
      - win_points = wins - coalition_wins / 2
      - win_rate   = win_points / total_plays * 100
    over efforts with game__final=True, game__test_match=False.
    Writes cached_winrate, cached_plays, and cached_tourney_points, saving only
    those fields to avoid triggering unrelated post_save signals.
    """
    from the_warroom.models import filtered_winrate

    model_name = obj._meta.model_name
    if model_name == 'profile':
        stats = filtered_winrate(player=obj)
    elif model_name == 'faction':
        stats = filtered_winrate(faction=obj)
    else:  # vagabond
        stats = filtered_winrate(vagabond=obj)

    total = stats['total']
    obj.cached_plays = total
    obj.cached_tourney_points = stats['win_points'] if total else None
    obj.cached_winrate = round(stats['win_rate'], 1) if total else None
    obj.save(update_fields=['cached_winrate', 'cached_plays', 'cached_tourney_points'])
