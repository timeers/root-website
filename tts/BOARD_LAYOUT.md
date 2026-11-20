# BoardLayout: a custom map's clearings, forests and relic spots

This document is for anyone adding a way to edit or build a map layout, for example a layout editor on the site or Map drafts in the Forge. It records how the format works so it doesn't have to be worked out again.

## What it is

`BoardLayout` is optional data stored in a **Map post's TTS JSON** (`Post.tts_json`), as a top-level key next to `ObjectStates`:

```json
{
  "ObjectStates": [ ... the map board and its pieces ... ],
  "BoardLayout": {
    "clearings": [[-19.93, 17.20], [18.64, 12.35], ...],
    "forests":   [[-3.10, 12.40], ...],
    "relics":    [[0.80, 8.70], ...]
  }
}
```

It says where a map's clearings, forests and relic spots are. It's **data, not TTS objects**: no snap points or markers are added to the board, so nothing snaps where it wouldn't on a normal map. This mirrors how the Root Tabletop Tournament (RTT) coordinator handles the official maps, with hand-measured tables inside its script: `RTT_CLEARING_CENTRES`, `RTT_FOREST_UV` and `RTT_RELIC_POS`.

## Format

- **Keys:** `clearings`, `forests` and `relics`. Each is optional, and each is a list of `[x, z]` number pairs. No other keys are allowed.
- **Coordinates:** an offset **from the map board's centre, in TTS table units, along the table's own axes**, with the board as it spawns (at the rotation saved in its JSON).
  - **No rotation is applied when it's used.** This is the same convention as `RTT_CLEARING_CENTRES`, whose values are world positions for a board at the table centre.
  - **x** runs left/right across the table, and **z** runs away from / towards you. y (height) isn't stored.
- **Scale is baked in:** the offsets assume the board's saved scale. If the board's scale changes, re-measure the layout or scale it to match.
- **Order:**
  - `clearings`: nothing relies on the order today. **List them in the map's clearing-number order anyway**, so later features (clearing numbers, suits per clearing) can use it.
  - `forests` and `relics`: the order doesn't matter.

**Validation** is `validate_board_layout` in `the_keep/utils.py`, called by `validate_tts_save` (the field validator on `Post.tts_json`). A malformed layout is rejected when an admin saves the post.

## What each list is used for

These are used on the RTT table, when the coordinator offers `RTT_EXTERNAL_API`. See `tts/RTT_EXTERNAL_API.md`.

| List | Used by | For |
|---|---|---|
| `clearings` | `rttMarquiseCats` | Marquise de Cat set-up: one warrior in each clearing |
| `relics` | `rttBadgerRelics` | Keepers in Iron set-up: relics dealt onto these spots |
| `forests` | `rttForestWorldCenters` | The relic fallback, used when there are no `relics` |

**Without a layout:** a custom map still spawns and plays normally. Those set-ups just aren't automated: the coordinator asks the player to place the cats, and places no relics.

## How it flows

1. **An admin pastes** the map's TTS JSON, including `BoardLayout`, into the Map post's `tts_json` in the Django admin.
2. **`validate_tts_save`** checks the shape (`the_keep/utils.py`).
3. **The keep detail API** (`TTSKeepDetailView` / `_tts_objects_payload` in `the_keep/api_views.py`) sends it unchanged as `layout`, for Map posts only.
4. **The TTS loader** (`placeMap` in `tts/loader.lua`) passes `layout` to the coordinator's `rttPlaceExternalMap`.
5. **The coordinator** stores it per map (`RTT_EXTERNAL.layouts[name]`), saved with the table. `rttExternalLayoutWorld(mapId, key)` turns each offset into a world position by adding the board's current position.

## Related tags (on objects, not in BoardLayout)

Randomised set-up comes from tags on the map's pieces, which the coordinator's `shuffleMaps` acts on:

| Tag | Effect |
|---|---|
| `Ruin` | Ruins swap positions among themselves |
| `Dummy` | One candidate ruin is kept at random; the rest are removed |
| `Clearing Marker` | Suit markers swap positions and rotations among themselves (random suits) |
| `Shuffleable` | That deck is shuffled |
| `Suit: fox` / `Suit: rabbit` / `Suit: mouse` / `Suit: bird` | Tells the coordinator a marker's suit (`rttSuitOfJson`), so the suits are published |

If a Map post has `fixed_clearings` set, the site removes `Clearing Marker` from the copy it serves, so suits stay where the designer put them.

## Measuring a layout today (until an editor exists)

1. Spawn the map from the Library tab on a table, so the board lands at the table centre at its saved rotation.
2. Note the board's position (right-click the board → Scripting, or the Gizmo tool).
3. Drop a small object on each clearing, then read its position.
4. Store `[object.x - board.x, object.z - board.z]`.
5. Repeat for forests and relic spots.

## Notes for a future editor or Forge builder

- **Keep the key name and convention**, so existing maps keep working, and validate with `validate_board_layout`.
- **A visual editor** can show the board image and let you click to place points.
  - To convert a click into an offset, use the board's TTS size: the board's world width and depth follow from its scale and the image's aspect ratio.
  - Measure from the image centre.
  - Flip the image's y axis to the table's z axis, accounting for the board's saved rotation. At the usual saved rotation of 180°, the image's top edge faces −z.
- **If Forge Map drafts are built later,** the Forge can produce `BoardLayout` alongside the map's TTS objects, the same way faction drafts produce theirs.
