# Prompt: add Vagabond and Knaves-captain follow-ups to `/pick`

Paste everything below the line into a fresh Claude Code session in this repo.

---

In this Django + Discord-bot repo, the `/pick` slash command lets players choose factions
for an LFG game. Two gaps need closing. Read the code before planning — the line numbers
below are from commit `a559283e` and may have drifted.

## Background you can rely on

`/pick` lives entirely in `the_gatehouse/discord_interactions.py`, in the section starting
at the `# ── /pick ──` banner (~line 2545). The relevant pieces:

- `_pick_pool(thread)` (~2645) returns the pickable factions as
  `[(slug, title, vagabond_slug), ...]`. With a draft, each entry carries the vagabond
  rolled for it. **Without a draft it hardcodes `None`** for the third element.
- `_handle_pick_faction(payload)` (~2915) is the select handler: it authorizes the clicker,
  writes `faction`/`vagabond` to the seat under `select_for_update`, captures a roll, then
  rebuilds the panel with `_pick_panel_data`.
- `_pick_panel_data(thread, seats, mode, owner, pool=None)` (~2684) rebuilds the public
  panel from the DB on every interaction. The bot is stateless; state rides in custom_ids.
- Component handlers are registered in `COMPONENT_HANDLERS` (~3822).
- custom_id convention: `encode_custom_id(action, *args)` → `"action:arg1:arg2"`, max 100
  chars, never pack lists. The dispatcher (~4134) owner-locks a component when the **last**
  arg looks like a snowflake (all digits, len ≥ 17). `PICK_OPEN = "g"` (~2555) is the escape
  hatch: ending a custom_id with `"g"` keeps the owner-lock OFF so any seated player can
  click, and the handler authorizes per-turn itself (see the check at ~2953-2958).

## Task 1 — Vagabond identity (do this first; it is a live data bug)

All 12 vagabond variants share a single `Faction` row (see the note in
`the_gatehouse/models.py` on `LFGDraftPick`, ~454-456). So when a player picks Vagabond in a
game with **no draft**, `_pick_pool` supplies `vagabond_slug=None` and the seat is saved with
`seat.vagabond = None` — Ranger and Thief collapse into the same record.

Add a follow-up select: when a seat takes the faction whose slug is `vagabond`, respond with
a second string-select of Vagabonds instead of advancing the panel. On choice, write
`seat.vagabond` and then advance the panel as normal.

- Pool: `Vagabond.objects.filter(official=True, status=1).exclude(slug__isnull=True)`
  (9 rows today). Note `status=1` is an int against a CharField of string choices — Django
  coerces it, and this matches every other call site in the file.
- Emoji: `vagabond_emoji_for(vagabond)` in `the_gatehouse/services/discordservice.py`
  (~1436), passed through `parse_emoji_object` for component form. Compare how
  `_pick_panel_data` uses `faction_emoji_object(slug)`.
- **Skip the prompt entirely when the pool entry already carries a `vagabond_slug`** — that
  is the draft path, where the vagabond is already decided. Only the no-draft branch needs
  this.
- Extend the roll capture at ~2990-2994 to append the chosen Vagabond via
  `_lfg_item("Vagabond", vagabond)`, mirroring what `/draft` does at ~2292-2293. This is what
  makes the record form offer it later.
- Reuse the `PICK_OPEN` convention and authorize against the same seat, exactly as
  `_handle_pick_faction` does.

## Task 2 — Knaves of the Deepwood captains

`LFGSeat` (`the_gatehouse/models.py` ~481-519) has **no `captains` field**, so a seat cannot
record captains at all. Only `LFGDraftPick.captains` exists (~467-468).

The real game rule is **pick 3 of 4 offered**. Useful data fact: only **4** captain-capable
Vagabonds exist (Harrier, Ranger, Thief, Vagrant), and `DRAFT_CAPTAIN_COUNT = 4` (~2245)
therefore rolls the entire pool. So "roll 4 then pick 3" and "offer all captain-capable
Vagabonds, pick 3" produce the same list today — **no rolling step is needed in `/pick`**.

Steps:

1. **Model + migration.** Add to `LFGSeat`, mirroring `LFGDraftPick.captains`:
   `captains = models.ManyToManyField("the_keep.Vagabond", blank=True, related_name="+")`.
   M2M needs no default and is `blank=True`, so the migration is additive and safe on a
   populated table. Document *why* it belongs on the seat: like `vagabond`, the faction row
   alone cannot express which captains a seat took.
2. **Follow-up multi-select.** When a seat takes `knaves-of-the-deepwood`, prompt with a
   `string_select` of captain-capable Vagabonds (`Vagabond.objects.filter(official=True,
   status=1, captain=True)` — the same pool `_random_draft_captains` uses at ~2249-2257),
   `min_values=3, max_values=3`. Then `locked.captains.set(chosen)`.
   - `string_select` already clamps `max_values` against the option count
     (`services/discord_components.py` ~46), but `min_values=3` with fewer than 3 options is
     still an invalid payload. Guard by skipping the prompt when fewer than 3 captain-capable
     Vagabonds exist, and log it.
   - Write the M2M **outside** the `transaction.atomic()` / `select_for_update` block — an
     M2M `.set()` cannot run before the row exists and does not need the row lock.
3. **Capture the captains.** Append `_lfg_item("Captain", c)` per chosen captain to the roll
   capture, exactly as `/draft` does at ~2292-2293. `ROLL_KIND_TO_BUCKET` in
   `the_gatehouse/services/lfg_game.py` (~33) already maps `"Captain" → "captains"`, and
   `lfg_option_querysets` (~129) already has a `captains` bucket. Without the rolls, the
   record form's narrowing will not offer them.
4. **Prefill the record form.** `seated_profiles` (`services/lfg_game.py` ~82-107) returns a
   4-tuple and its docstring warns *"callers unpack a fixed width."* Its only unpacking site
   is `the_warroom/views.py` ~1338, and `the_gatehouse/tests.py` ~3062-3111 asserts the
   4-tuple shape.
   **Do not widen the tuple.** Add a sibling helper instead — e.g.
   `captains_by_seat(thread) -> {seat_number: [slug, ...]}` — and use it in the prefill block
   at `views.py` ~1347-1354, following the existing "only pre-select when the value is
   actually offered on this row" rule:
   ```python
   seat_captains = lfg_opts['captains'].filter(slug__in=slugs)
   if seat_captains:
       formset.forms[i].initial['captains'] = [v.pk for v in seat_captains]
   ```
   The form fields already exist: `Effort.captains` (M2M) and `Effort.discarded_captain`
   (FK) in `the_warroom/models.py` ~2366-2367, wired up at `views.py` ~1332-1333.
   Leave `discarded_captain` unset — `/pick` records the 3 taken; which of the 4 was
   discarded is derivable and not worth inferring here.
5. **Admin.** `LFGSeatInline.readonly_fields` (`the_gatehouse/admin.py` ~69-74) currently
   lists only `seat_number, profile, faction` — it already omits the existing `vagabond`
   field. Add both `vagabond` and `captains`. Note `filter_horizontal` does not work on a
   readonly M2M; prefer a small read-only display method, since these rows are bot-written.
   The inline's docstring ("written by /draft's seat button") is stale — `/pick` writes these
   too.

## Constraints

- **`/draft` enforces Vagabond/Knaves mutual exclusion** via `DRAFT_EXCLUSIONS` (~159-160),
  but **`/pick` with no draft does not** — both can be picked in the same game. Treat the two
  follow-ups as independent, keyed off the picked faction's slug. Do not assume exclusivity.
- Keep the bot stateless: rebuild panels from the DB, never trust a stale message.
- Match the file's existing comment style — it explains *why*, especially around locking,
  nullable FKs, and MTI. It is dense but deliberate; follow it.

## Testing

Existing `/pick` coverage: `the_gatehouse/tests.py` classes `PickCommandTests` (~3118),
`PickSeatChoiceTests` (~3439), `PickCommandGroupThreadTests` (~3568). There is a `_select(slug,
clicker, mode=...)` helper (~3163) that drives `_handle_pick_faction` directly — reuse it.

Add cases for: picking Vagabond prompts and stores a vagabond; picking Knaves prompts for
exactly 3 of 4 and stores them; both write the right `Vagabond`/`Captain` rolls; and the
existing `seated_profiles` 4-tuple tests still pass untouched (that is the check that step 4
stayed backward-compatible).

Run `python manage.py test the_gatehouse`.

**Important testing caveat:** dev runs on SQLite, where `select_for_update` is a silent
no-op, while production is Postgres. A bug in this exact handler shipped for that reason —
`select_related` on nullable FKs inside a `select_for_update` query emits a `LEFT OUTER JOIN`,
which Postgres rejects with `NotSupportedError: FOR UPDATE cannot be applied to the nullable
side of an outer join`. It was fixed by removing the `select_related`; see the comment above
the lock in `_handle_pick_faction`. **Do not reintroduce `select_related` into any
`select_for_update` query on a nullable FK**, and note a green SQLite suite does not prove
locking behavior. There is a Postgres-gated regression test,
`test_the_seat_lock_does_not_join_across_nullable_fks`, that skips locally.

## Deliverable

Plan first, then implement. Task 1 is small and self-contained; Task 2 involves a migration
and touches `the_warroom` — flag anything ambiguous before writing the migration.
