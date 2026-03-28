def calculate_and_cache_winrate(obj):
    """
    Recalculate and cache the winrate for a Faction, Vagabond, or Profile instance.
    Uses the same formula as the @property winrate on those models:
      - Each winning effort contributes 1 / winners_count points
      - Result = (total points / total qualifying efforts) * 100, rounded to 1dp
    Saves only the cached_winrate field to avoid triggering unrelated post_save signals.
    """
    efforts = obj.efforts.filter(game__test_match=False, game__final=True)
    total_plays = efforts.count()
    if total_plays == 0:
        obj.cached_winrate = None
        obj.save(update_fields=['cached_winrate'])
        return

    points = 0
    for effort in efforts.filter(win=True).select_related('game'):
        winners_count = effort.game.get_winners().count()
        if winners_count > 0:
            points += 1 / winners_count

    obj.cached_winrate = round(points / total_plays * 100, 1)
    obj.save(update_fields=['cached_winrate'])
