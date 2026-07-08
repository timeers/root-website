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


def _scaled_threshold(games_count):
    """Minimum qualifying plays for the global leaderboard, scaled by dataset
    size. Mirrors leaderboard_view()'s default so the cached boards match the
    site's unfiltered /leaderboard/."""
    if games_count > 5000:
        return 25
    if games_count > 2000:
        return 15
    if games_count > 1500:
        return 10
    if games_count > 1000:
        return 5
    if games_count > 500:
        return 3
    return 1


def _cached_board(model_qs, threshold, limit):
    """Top-`limit` rows from a model's cached winrate fields (coalition formula),
    as the {title, win_rate, total_efforts, url, slug} dicts the /stats embed
    consumes. Ordered by winrate then plays, matching the site's default board."""
    from django.db.models import F

    rows = (model_qs.filter(cached_plays__gte=threshold)
            .order_by('-cached_winrate', '-cached_plays')[:limit])
    return [
        {
            'title': getattr(r, 'display_name', None) or getattr(r, 'discord', None) or r.title,
            'win_rate': r.cached_winrate or 0.0,
            'total_efforts': r.cached_plays or 0,
            'url': r.get_absolute_url(),
            'slug': r.slug,
        }
        for r in rows
    ]


def cached_top_factions(limit=5):
    """Global top factions from cached fields, as /stats-embed JSON dicts.
    Excludes Clockwork (component='Faction' only), matching the live board."""
    from the_keep.models import Faction
    from the_warroom.models import Game

    threshold = _scaled_threshold(Game.objects.filter(final=True, test_match=False).count())
    return _cached_board(
        Faction.objects.filter(component='Faction'), threshold, limit
    )


def cached_top_players(limit=5):
    """Global top players from cached fields, as /stats-embed JSON dicts."""
    from the_gatehouse.models import Profile
    from the_warroom.models import Game

    threshold = _scaled_threshold(Game.objects.filter(final=True, test_match=False).count())
    return _cached_board(Profile.objects.all(), threshold, limit)
