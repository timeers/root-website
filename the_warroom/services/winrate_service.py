# Field-prefix -> platform mapping for the per-platform cached stats. The
# empty-string prefix ('cached_') holds the overall (all-platforms) stats;
# platform is None so filtered_winrate aggregates across every platform.
def _cached_prefixes():
    """Return {field_prefix: platform_value_or_None}, overall first."""
    from the_warroom.models import PlatformChoices
    return {
        'cached_': None,
        'cached_irl_': PlatformChoices.IRL,
        'cached_dwd_': PlatformChoices.DWD,
        'cached_tts_': PlatformChoices.TTS,
    }


# All cached field names written by _apply_cached_stats, in prefix order. Shared
# with the recalculate_winrates management command's bulk_update so the two
# stay in sync.
CACHED_FIELDS = [
    f'{prefix}{suffix}'
    for prefix in ('cached_', 'cached_irl_', 'cached_dwd_', 'cached_tts_')
    for suffix in ('winrate', 'plays', 'tourney_points')
]


def _filter_kwarg(obj):
    """The filtered_winrate() kwarg for obj's model (player/faction/vagabond)."""
    model_name = obj._meta.model_name
    if model_name == 'profile':
        return 'player'
    if model_name == 'faction':
        return 'faction'
    return 'vagabond'


def _apply_cached_stats(obj):
    """Compute and set every cached_* field on obj (overall + per platform).

    Uses the coalition-based leaderboard formula via filtered_winrate() — the
    site's single source of truth — for the overall stats and once per platform.
    Sets fields but does not save; callers persist (save/bulk_update).
    """
    from the_warroom.models import filtered_winrate

    kwarg = _filter_kwarg(obj)
    for prefix, platform in _cached_prefixes().items():
        stats = filtered_winrate(**{kwarg: obj}, platform=platform)
        total = stats['total']
        setattr(obj, f'{prefix}plays', total)
        setattr(obj, f'{prefix}tourney_points', stats['win_points'] if total else None)
        setattr(obj, f'{prefix}winrate', round(stats['win_rate'], 1) if total else None)


def calculate_and_cache_winrate(obj):
    """
    Recalculate and cache the winrate for a Faction, Vagabond, or Profile instance.
    Uses the coalition-based leaderboard formula (coalition wins count as half),
    the site's single source of truth via filtered_winrate():
      - win_points = wins - coalition_wins / 2
      - win_rate   = win_points / total_plays * 100
    over efforts with game__final=True, game__test_match=False.
    Writes the overall cached_* fields plus a per-platform set (irl/dwd/tts),
    saving only those fields to avoid triggering unrelated post_save signals.
    """
    _apply_cached_stats(obj)
    obj.save(update_fields=CACHED_FIELDS)


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


def _platform_prefix(platform):
    """Field prefix for a platform value (or None for overall), e.g.
    PlatformChoices.IRL -> 'cached_irl_'. Falls back to the overall 'cached_'
    prefix for None or any unrecognized value."""
    for prefix, value in _cached_prefixes().items():
        if value == platform:
            return prefix
    return 'cached_'


def _platform_threshold(platform):
    """Scaled qualifying threshold over the game set for `platform` (or all
    games when platform is None), so a platform board's cutoff tracks that
    platform's dataset size the way the overall board tracks the whole one."""
    from the_warroom.models import Game

    games = Game.objects.filter(final=True, test_match=False)
    if platform:
        games = games.filter(platform=platform)
    return _scaled_threshold(games.count())


def _cached_board(model_qs, threshold, limit, prefix='cached_'):
    """Top-`limit` rows from a model's cached winrate fields (coalition formula),
    as the {title, win_rate, total_efforts, url, slug} dicts the /stats embed
    consumes. Ordered by winrate then plays, matching the site's default board.
    `prefix` selects the field set: 'cached_' overall, or 'cached_irl_' /
    'cached_dwd_' / 'cached_tts_' for a single platform."""
    winrate_field = f'{prefix}winrate'
    plays_field = f'{prefix}plays'
    rows = (model_qs.filter(**{f'{plays_field}__gte': threshold})
            .order_by(f'-{winrate_field}', f'-{plays_field}')[:limit])
    return [
        {
            'title': getattr(r, 'display_name', None) or getattr(r, 'discord', None) or r.title,
            'win_rate': getattr(r, winrate_field) or 0.0,
            'total_efforts': getattr(r, plays_field) or 0,
            'url': r.get_absolute_url(),
            'slug': r.slug,
        }
        for r in rows
    ]


def cached_top_factions(limit=5, platform=None):
    """Global top factions from cached fields, as /stats-embed JSON dicts.
    Excludes Clockwork (component='Faction' only), matching the live board.
    With a platform, reads that platform's cached fields instead of overall."""
    from the_keep.models import Faction

    return _cached_board(
        Faction.objects.filter(component='Faction'),
        _platform_threshold(platform), limit, _platform_prefix(platform),
    )


def cached_top_players(limit=5, platform=None):
    """Global top players from cached fields, as /stats-embed JSON dicts.
    With a platform, reads that platform's cached fields instead of overall."""
    from the_gatehouse.models import Profile

    return _cached_board(
        Profile.objects.all(),
        _platform_threshold(platform), limit, _platform_prefix(platform),
    )
