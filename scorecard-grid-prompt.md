# Scorecard Grid Entry Mode — Implementation Prompt

**Goal:** Add a simplified "scorecard grid" entry mode to the game recording form (`the_warroom/templates/the_warroom/record_game_v2.html`), so a recorder can enter turn-by-turn scores for every seat in one grid instead of opening a separate scorecard per effort. On submit, the grid is persisted as one `ScoreCard` + multiple `TurnScore` rows per effort.

Before implementing, review these files so the new code matches existing patterns:

- Template being extended: [record_game_v2.html](the_warroom/templates/the_warroom/record_game_v2.html) (effort formset rows, `add_new_form`, `deleteForm`, submit/save-progress buttons).
- Reference behavior to mirror: [record_scores_v2.html](the_warroom/templates/the_warroom/record_scores_v2.html) — copy its dominance toggle pattern (`selectDominance`, `setInitialDominance`, `toggle_dominance`) and running-score logic.
- Models: [models.py:1928-2028](the_warroom/models.py#L1928-L2028) — `ScoreCard`, `TurnScore`, `Effort`. Note: `ScoreCard.effort` is a `OneToOneField(related_name='scorecard')`, so `effort.scorecard` is the reverse accessor. `TurnScore` has `game_points_total` (cumulative score at that turn), `generic_points`, `total_points`, `turn_number`, `dominance`, and FK `scorecard`.
- View that saves the game form: `manage_game_v2` at [views.py:900-1183](the_warroom/views.py#L900-L1183). Effort formset is `EffortCreateForm` via `modelformset_factory`.

## UI / interaction

1. Add a button on the game form (near the Players section) that slides the existing form off-screen and slides in a new "Scorecard Grid" panel (CSS transform transition; a back button slides it back). Both panels live inside the same `<form id="game-form-v2">`.

2. The grid has **one row per effort** currently in the effort formset, labeled by that effort's selected faction. Rows must stay in sync as efforts are added/removed/reordered (hook into the existing `add_new_form` / `deleteForm` / sortable `stop` handlers).

3. Columns: `T1, T2, … T9` (turn columns), then a final **Score** column. A **"+ Add Turn"** button inserts a new turn column after the last one (cap consistent with the scorecard page's `MAX_TURNS = 30`). Each turn cell holds a numeric input = the **Game Points Total** (cumulative score) for that effort at that turn.

4. **Score column (per row):** via JS, show the value of the **last non-blank** turn cell in that row (ignore blank/empty cells). This value must also write into that effort's existing **Score** field in the game form (`id_form-<index>-score`).

5. **Dominance tag per cell:** in the top-right of each turn cell, show a greyed-out `{% static 'images/tags/Dominance_Tag.png' %}`. Clicking it toggles it to the colored version and also colors **all subsequent cells in that row** (mirror `selectDominance` forward-fill). Un-clicking greys it out for that cell and **all previous cells in that row** (mirror the reverse case).

## Validation (on Submit and Save Progress)

6. For any row whose dominance tag is activated, validate that the corresponding effort in the game form has a **dominance choice** selected (`id_form-<index>-dominance`). If not, highlight that field red and show: *"Select dominance to match with scorecard"*. **Hard block** submission until resolved.

7. Validate each effort's game-form **Score** against the ending Score computed by the grid (last non-blank cell). On mismatch, highlight the field and show a message, and **hard block** submission until resolved.

## Persistence — grid → ScoreCard + TurnScores

8. On submit, convert each grid row into one `ScoreCard` linked to that effort (`ScoreCard.effort`, matching the effort's `faction`), plus one `TurnScore` per non-blank turn cell:
   - `TurnScore.game_points_total` = the cell value (cumulative).
   - `TurnScore.turn_number` = the column number (T1 → 1, etc.).
   - `TurnScore.generic_points` = this cell's value **minus the previous non-blank cell's value** in that row (the per-turn delta).
   - `TurnScore.dominance` = the cell's dominance-tag state.
   - `ScoreCard.faction` / effort linkage = from the effort being submitted.
   - Extend the save logic in `manage_game_v2` ([views.py:1041-1179](the_warroom/views.py#L1041-L1179)), reusing the `ScoreCard`/`TurnScore` consistency checks already there.

## When the grid is shown, pre-filled, or read-only

9. **Blank/editable grid** is offered only when none of the efforts already have a `ScoreCard` containing battle/crafting/faction/other points (i.e., a *detailed* scorecard).

10. **Detailed scorecard present (read-only):** if any effort has a `ScoreCard` with battle/crafting/faction/other points, render the grid **read-only** and show the message: *"This scorecard contains detailed values and can be edited from the Game's page."* Do not allow grid edits or submission of grid data in this state.

11. **Generic-only scorecard present (pre-fill + update):** if an effort has an existing `ScoreCard` with `generic_points` only (no detailed points), **pre-fill** the grid — populate each turn cell with its `game_points_total` (adding turn columns as needed) and activate the dominance tag where `TurnScore.dominance` is true. On submit, **update** that existing `ScoreCard` and its `TurnScore` rows in place (do not create duplicates); add/remove `TurnScore` rows to match the current grid.
