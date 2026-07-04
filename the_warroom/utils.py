import re

from the_gatehouse.tasks import send_discord_message_task

from better_profanity import profanity
profanity.load_censor_words()
# # Remove words you don't want censored
# whitelist = ["dummy", "drunk", "fat", "god", 
#              "heck", "hell", "jerk", "junky", "junkie", 
#              "kill", "lmao", "lmfao", "moron", "omg", "pawn", 
#              "pot", "prick", "prude", "rum", "sadism", 
#              "sadist", "screw", "thug", "thrust", "ugly", 
#              "vomit", "weed", "weirdo", "womb"]
# clean_list = profanity.CENSOR_WORDSET - whitelist

# # Reload the profanity filter with the cleaned list
# profanity.load_censor_words(clean_list)

def clean_nickname(raw_title):
    nickname = (raw_title or '').strip()
    lower_nickname = nickname.lower()

    # Block completely if link or Imported game
    blocked_substrings = ['https://', 'discord.com', 'import game 20']
    if any(substring in lower_nickname for substring in blocked_substrings):
        return None

    # # List of substrings to remove (case-insensitive)
    # substrings_to_remove = [' async ', 
    #                         ' ep ', ' e&p ', 'ep deck', 'e&p deck', 'base deck',
    #                         ' rc ', 'random clearings', 'random clearing ',
    #                         ' 1vb ', 'ban second vg', 'ban second vb', 'ban 2nd', ' 1 vb',
    #                         'live nt', ' nt ', 'live with timer', '**live**'
    #                         ]
    
    # # Remove each substring (case-insensitive)
    # for substring in substrings_to_remove:
    #     nickname = re.sub(re.escape(substring), ' ', nickname, flags=re.IGNORECASE)
    
    # Clean up extra whitespace
    nickname = ' '.join(nickname.split()).strip()

    # If nickname contains profanity, censor it
    # if profanity.contains_profanity(nickname):
    #     new_nickname = profanity.censor(nickname)
    #     send_discord_message_task.delay(f'Nickname "{nickname}" replaced with "{new_nickname}"')
    #     nickname = new_nickname
        

    return nickname[:50]  # Truncate to 50 characters


def get_single_round(tournament, stage):
    use_stages = tournament.use_stages

    if not stage and not use_stages:
        only_stage = tournament.stages.first()
    elif stage:
        only_stage = stage
    else:
        only_stage = None

    use_rounds = only_stage.use_rounds if only_stage else False

    if not use_rounds and only_stage:
        only_round = only_stage.rounds.first()
    else:
        only_round = None

    return only_round

def get_single_stage(tournament):
    use_stages = tournament.use_stages

    if not use_stages:
        only_stage = tournament.stages.first()
    else:
        only_stage = None

    return only_stage


def _expand_grid_rows(rows, max_turn):
    """Expand row sources into dense per-turn cells aligned to ``max_turn`` so the
    box_score.html template can render columns without gaps. Each cell gets a `mode`:
      'score' → show the score only
      'icon'  → show the dominance-type icon in place of the score
      'both'  → show the score (bottom-left) and the icon (top-right, behind)
    Brazen Demagogue efforts show both on dominant turns; the deciding winner cell
    shows the icon unless the score reached 30 (a points win). Non-brazen efforts
    keep icon-in-place-of-score on dominant turns. Vagabond coalition turns show the
    partner's faction icon."""
    grid = []
    for row in rows:
        cells = []
        has_coalition = bool(row['coalition_icon'])
        for n in range(1, max_turn + 1):
            t = row['turns'].get(n)
            is_dom = bool(t['dominance']) if t else False
            has_dom_type = is_dom and bool(row['effort_dominance'])
            has_coalition_dom = is_dom and has_coalition
            is_winner_cell = bool(row['effort_win']) and n == row['row_max']
            value = t['game_points'] if t else None

            if has_coalition_dom:
                # Vagabond coalition: show the partner's faction icon (vagabonds
                # can't be Brazen Demagogue, so it's always icon-in-place).
                mode = 'coalition_icon'
            elif not has_dom_type:
                mode = 'score'
            elif row['brazen']:
                if is_winner_cell:
                    # Deciding cell: score only if a points win (>=30), else icon.
                    mode = 'score' if (value is not None and value >= 30) else 'icon'
                else:
                    mode = 'both'
            else:
                mode = 'icon'

            cells.append({
                'value': value,
                'dominance': is_dom,
                'dom_type': row['effort_dominance'] if has_dom_type else None,
                'mode': mode,
                'winner': is_winner_cell,
            })
        grid.append({
            'faction': row['faction'],
            'small_icon': row['small_icon'],
            'small_icon_version': row['small_icon_version'],
            'color': row['color'],
            'coalition_icon': row['coalition_icon'],
            'coalition_icon_version': row['coalition_icon_version'],
            'coalition_name': row['coalition_name'],
            'cells': cells,
        })
    return grid


def build_scorecard_grid(efforts):
    """Build a read-only grid of the game score per turn (rows = factions, columns =
    turns) for box_score.html. Mirrors the chart's data source (every effort with a
    scorecard) so the two stay consistent. Reads ``effort.translated_faction_title``
    via getattr fallback so translation prefetch stays optional.

    Returns ``(scorecard_grid, scorecard_grid_turns)``.
    """
    from .models import ScoreCard

    rows = []
    max_turn = 0
    for effort in efforts:
        try:
            scorecard = effort.scorecard
        except ScoreCard.DoesNotExist:
            scorecard = None
        if not scorecard:
            continue
        turns = {t['turn_number']: t for t in scorecard.get_turns()}
        if not turns:
            continue
        row_max = max(turns)
        if row_max > max_turn:
            max_turn = row_max
        # Vagabonds play a coalition instead of dominance — a dominance turn shows
        # the coalition partner's faction icon rather than a dominance-type icon.
        coalition = effort.coalition_with
        rows.append({
            'faction': getattr(effort, 'translated_faction_title', effort.faction.title),
            'small_icon': effort.faction.small_icon,
            'small_icon_version': effort.faction.small_icon_version,
            'color': effort.faction.color,
            'effort_dominance': effort.dominance,  # e.g. 'Mouse' (or None)
            'coalition_icon': coalition.small_icon if coalition else None,
            'coalition_icon_version': coalition.small_icon_version if coalition else 0,
            'coalition_name': coalition.title if coalition else None,
            'effort_win': effort.win,
            'brazen': bool(effort.brazen_demagogue),
            'row_max': row_max,  # this scorecard's own last turn number
            'turns': turns,
        })

    scorecard_grid = _expand_grid_rows(rows, max_turn)
    scorecard_grid_turns = list(range(1, max_turn + 1))
    return scorecard_grid, scorecard_grid_turns


def build_single_scorecard_grid(scorecard, faction_title=None):
    """Single-row box-score grid for a scorecard with no linked effort (no
    coalition / brazen / win / dominance-type). Dominance turns fall back to the
    generic Dominance tag because ``dom_type`` is None.

    Returns ``(scorecard_grid, scorecard_grid_turns)``.
    """
    if not scorecard:
        return [], []
    turns = {t['turn_number']: t for t in scorecard.get_turns()}
    if not turns:
        return [], []
    max_turn = max(turns)
    faction = scorecard.faction
    rows = [{
        'faction': faction_title or faction.title,
        'small_icon': faction.small_icon,
        'small_icon_version': faction.small_icon_version,
        'color': faction.color,
        'effort_dominance': None,
        'coalition_icon': None,
        'coalition_icon_version': 0,
        'coalition_name': None,
        'effort_win': False,
        'brazen': False,
        'row_max': max_turn,
        'turns': turns,
    }]
    scorecard_grid = _expand_grid_rows(rows, max_turn)
    scorecard_grid_turns = list(range(1, max_turn + 1))
    return scorecard_grid, scorecard_grid_turns