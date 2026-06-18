# Optimize games-list views (slow pages / WSGI timeouts)

## Context

Production Apache/mod_wsgi logs show `Timeout when reading response headers from daemon
process 'django_app'` plus a confirmed `Slow request: /hireling/furious-protector/games/ took
5.15s`. That URL is served by `component_games` in [the_keep/views.py](the_keep/views.py)
(~1543). The page is slow because it **counts and paginates a heavy queryset** (many
`select_related` joins + a `DISTINCT` forced by the filter) and runs redundant work. The **same
"prefetch-before-paginate" pattern** exists in every games-list view, so under concurrent bot
traffic these slow requests saturate the WSGI worker pool and cause the timeouts. Per decision,
fix **all games-list views**. View-only change — no template/model/settings changes.

> **First step on execution:** save a durable copy of this plan to
> `games-list-optimization-plan.md` at the repo root (alongside `views-audit.md` /
> `changelog.md`) so it survives plan-mode resets and can be revisited. The
> `views-audit.md` "Audit Notes" table already flags `component_games` count/`len()` perf
> issues — cross-reference them.

## Affected views (same pattern)
All apply `select_related`/`prefetch_related` (+ sometimes `.distinct()`) to the **full**
queryset, then `Paginator(...).count` over it:
- [the_keep/views.py](the_keep/views.py): `component_games` (~1543) — heaviest (extra
  `hirelings/landmarks/tweaks/undrafted_*`, double-distinct, second full count).
- [the_warroom/views.py](the_warroom/views.py): `game_list_view` (~66, /battlefield/),
  `player_games` (~290), `my_submitted_games_view` (~357), `tournament_games_page` (~1927),
  `stage_games_page` (~4542), `round_games_page` (~4805).

## Root causes found (all in `component_games`)

1. **Heavy joins counted/paginated.** `select_related(*opts['select'], 'undrafted_faction',
   'undrafted_vagabond')` + `prefetch_related(...)` are applied to the queryset *before*
   pagination. `Paginator.count` then runs `SELECT COUNT(*)` over a query carrying those
   `select_related` JOINs combined with `DISTINCT` — joins that COUNT does not need. The
   prefetches are correctly needed only for the **rendered rows**, not for counting.
2. **Redundant `.distinct()` (x3).** `BaseGameFilter.filter_queryset`
   ([the_warroom/filters.py:77](the_warroom/filters.py)) **already** ends with `.distinct()`. The
   view adds `.distinct()` at the base queryset *and* again on `game_filter.qs.distinct()` — two
   redundant DISTINCTs.
3. **Second full count when filtered.** `games_total = games.count()` (line ~1624) runs a second
   expensive `COUNT(DISTINCT …)` over the base set when any filter is active.
4. **Stray debug `print('here')`** in the sibling `ultimate_component_view`
   ([the_keep/views.py:1119](the_keep/views.py)) — cheap to remove while here (optional).

## Why deferring prefetch is safe (verified)

The per-row template [game_detail_button.html](the_warroom/templates/the_warroom/partials/game_detail_button.html)
accesses `game.round.get_tournament`, `game.deck`, `game.map`, `game.landmarks/hirelings/tweaks`,
and `game.get_efforts` (efforts). So the joins/prefetches in
[Game.with_efforts()](the_warroom/models.py:1466) + `hirelings/landmarks/tweaks/undrafted_*` are
genuinely required — but only for the **~25 rows actually rendered** (`PAGE_SIZE = 25`), not for
counting the whole set. The efforts stats aggregate uses `filtered_games.values('id')` (a lean id
subquery) and is unaffected.

## Why deferring prefetch is safe (verified)

The per-row template
[game_detail_button.html](the_warroom/templates/the_warroom/partials/game_detail_button.html)
accesses `game.round.get_tournament`, `game.deck`, `game.map`,
`game.landmarks/hirelings/tweaks`, and `game.get_efforts`. So those joins/prefetches are
genuinely needed — but only for the **~25 rendered rows** (`PAGE_SIZE = 25`), not for counting
the whole set. `BaseGameFilter.filter_queryset` ([the_warroom/filters.py:77](the_warroom/filters.py))
**already** ends with `.distinct()`, so the view-level `.distinct()` calls are redundant.

## Recommended approach

**1. Add a shared helper** `hydrate_page(page_obj, *, select_related=(), prefetch_related=())`
in [the_warroom/utils.py](the_warroom/utils.py). It re-fetches only the current page's rows with
the heavy joins/prefetches and restores order, then sets it back on the page:
```python
def hydrate_page(page_obj, *, select_related=(), prefetch_related=()):
    """Re-fetch just this page's Game rows with select/prefetch, preserving order.

    Lets the Paginator count/paginate a lean queryset (cheap COUNT) while the
    expensive joins/prefetches run over only the rendered page (<= PAGE_SIZE rows).
    """
    rows = list(page_obj.object_list)
    if not rows:
        return page_obj
    Game = rows[0].__class__
    ids = [g.pk for g in rows]
    hydrated = Game.objects.filter(pk__in=ids)
    if select_related:
        hydrated = hydrated.select_related(*select_related)
    if prefetch_related:
        hydrated = hydrated.prefetch_related(*prefetch_related)
    order = {pk: i for i, pk in enumerate(ids)}
    page_obj.object_list = sorted(hydrated, key=lambda g: order[g.pk])
    return page_obj
```

**2. In each affected view, apply the same transformation:**
- Build the base queryset + official filter as today, but **do not** add
  `select_related`/`prefetch_related`/`.distinct()` before filtering. Pass the lean qs to the
  filterset (the filter adds its own `.distinct()`).
- Paginate the lean filtered qs → `Paginator.count` becomes a plain `COUNT(DISTINCT id)` with
  only the filter's `efforts` joins (no `select_related` columns).
- After `page_obj = paginator.get_page(...)`, call
  `hydrate_page(page_obj, select_related=opts['select'] + (...), prefetch_related=opts['prefetch'] + (...))`
  using each view's existing `opts = Game.with_efforts()` plus that view's extras:
  - `component_games`: select `+ undrafted_faction, undrafted_vagabond`; prefetch `+ hirelings,
    landmarks, tweaks`.
  - `player_games`, `my_submitted_games_view`: same extras as today (they already added
    `hirelings/landmarks/tweaks`/undrafted — match their current lists).
  - `game_list_view`, `tournament_games_page`, `stage_games_page`, `round_games_page`: just
    `opts['select']` / `opts['prefetch']` (their current lists).
- Keep all context keys, ordering (`-date_posted`), and both render branches unchanged. Templates
  iterate `games`/`page_obj` and use `games.has_next`/`games.number`, which still work since we
  mutate `object_list` in place on the same `page_obj`.

**3. `component_games` extras:**
- Drop the redundant view-level `.distinct()` (filter already distincts).
- `games_total = games.count() if has_filters else filtered_count` stays, but `games` is now lean
  so the `has_filters=True` count no longer pays for joins. Keep `has_filters` detection as-is.

**4. Secondary cleanups (cheap, low risk):** where a view passes a heavy queryset into an
`Effort.objects.filter(game__in=<qs>)` (e.g. `player_games` line ~306 uses `filtered_qs`), switch
to `<qs>.values('id')` so the subquery doesn't drag select_related/prefetch. Apply only where the
subquery already exists.

**5. Remove** the stray `print('here')` debug line in `ultimate_component_view`
([the_keep/views.py:1119](the_keep/views.py)). (Per decision: print only — leave the duplicate
`get_object_or_404` lookup as-is.)

## Critical files
- [the_warroom/utils.py](the_warroom/utils.py) — new `hydrate_page` helper.
- [the_keep/views.py](the_keep/views.py) — `component_games`; `print` removal in
  `ultimate_component_view`.
- [the_warroom/views.py](the_warroom/views.py) — `game_list_view`, `player_games`,
  `my_submitted_games_view`, `tournament_games_page`, `stage_games_page`, `round_games_page`.
- Read-only refs: [the_warroom/filters.py](the_warroom/filters.py) (already `distinct()`s),
  [the_warroom/models.py](the_warroom/models.py) (`with_efforts`, `get_games_queryset`,
  `get_efforts`).

## Verification
1. **Correctness (each view):** load and eyeball that output is identical to before —
   `/hireling/<slug>/games/` (+ a Faction and Vagabond component page), `/battlefield/`, a
   player's games page, my-submitted games, and a tournament/stage/round games page. Confirm game
   cards render deck/map/hirelings/landmarks/tweaks/efforts, the stats header
   ("Recorded Games (N)" / "Filtered Games (n/N)", winrate, plays) is unchanged, and order is
   newest-first.
2. **Filtering + htmx infinite scroll:** apply filters (factions/players/map/deck), confirm
   counts and list correct; scroll to load page 2+ (htmx) and confirm hydrated rows show all
   related data with no missing/N+1 gaps. Confirm the persistent filter-card count still updates
   (the OOB count work from earlier still relies on `games_count`/`paginator.count`).
3. **Query cost:** with `CaptureQueriesContext`/`assertNumQueries` (or Debug Toolbar) on a
   component/battlefield page with many games, confirm: (a) the COUNT SQL no longer contains the
   `select_related` JOINs, (b) total query count is bounded and similar to before (one extra
   lightweight `id__in` hydrate query per page is expected), (c) wall-time drops materially on
   large result sets.
4. **No duplicate rows:** spot-check a Map/Deck/Landmark/Tweak/Hireling page (M2M `games`
   relation) — `distinct()` from the filter must keep rows unique.
5. `python manage.py check` clean.
