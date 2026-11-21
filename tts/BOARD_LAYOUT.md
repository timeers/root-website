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

**Validation** is `validate_board_layout` in `the_keep/utils.py`, called by `validate_tts_save`. A malformed layout is rejected when the file is uploaded.

## What each list is used for

These are used on the RTT table, when the coordinator offers `RTT_EXTERNAL_API`. See `tts/RTT_EXTERNAL_API.md`.

| List | Used by | For |
|---|---|---|
| `clearings` | `rttMarquiseCats` | Marquise de Cat set-up: one warrior in each clearing |
| `relics` | `rttBadgerRelics` | Keepers in Iron set-up: relics dealt onto these spots |
| `forests` | `rttForestWorldCenters` | The relic fallback, used when there are no `relics` |

**Without a layout:** a custom map still spawns and plays normally. Those set-ups just aren't automated: the coordinator asks the player to place the cats, and places no relics.

## How it flows

1. **An admin uploads** the map's TTS JSON, including `BoardLayout`, on the post's TTS JSON page (settings hub → TTS JSON; `the_keep/tts_views.py`). `tts_json` can't be edited in the Django admin.
2. **`validate_tts_save`** checks the shape (`the_keep/utils.py`). The admin then reviews the upload's warnings (scripts, files from untrusted hosts, missing Library tags; `inspect_tts_json` in `the_keep/services/tts_inspect.py`) and confirms it.
3. **It's stored exactly as uploaded,** with the derived `Post.tts_info` (`map_board`, `shuffle`; `describe_tts_json`).
4. **The keep detail API** (`TTSKeepDetailView` / `_tts_objects_payload` in `the_keep/api_views.py`) serves the JSON as stored, and sends `BoardLayout` unchanged as `layout`, for Map posts only.
5. **The TTS loader** (`placeMap` in `tts/loader.lua`) passes `layout` to the coordinator's `rttPlaceExternalMap`.
6. **The coordinator** stores it per map (`RTT_EXTERNAL.layouts[name]`), saved with the table. `rttExternalLayoutWorld(mapId, key)` turns each offset into a world position by adding the board's current position.

## Related tags (on objects, not in BoardLayout)

Randomised set-up comes from tags on the map's pieces, which the coordinator's `shuffleMaps` acts on:

| Tag | Effect |
|---|---|
| `Ruin` | Ruins swap positions among themselves |
| `Dummy` | One candidate ruin is kept at random; the rest are removed |
| `Clearing Marker` | Suit markers swap positions and rotations among themselves (random suits) |
| `Shuffleable` | That deck is shuffled |
| `Suit: fox` / `Suit: rabbit` / `Suit: mouse` / `Suit: bird` | Tells the coordinator a marker's suit (`rttSuitOfJson`), so the suits are published |

**Tags travel in the JSON itself:** the site serves uploaded JSON exactly as stored and adds nothing. So a map's pieces need `Map Object` (so the RTT table clears them when the map is replaced) and the board needs `RTT Map`, before you export it from TTS. The upload review lists any that are missing.

**Fixed suits:** leave `Clearing Marker` off the suit markers. The site no longer changes the JSON for a Map post's `fixed_clearings` setting; the review warns when the setting is on but the JSON still has the tag.

## The map builder

Map posts get their layout from the **map builder** (post → TTS JSON → Open map builder; `the_keep/tts_views.py` `map_builder`, `the_keep/static/the_keep/map_builder.js`). Admins and the map's designer mark the VP track, item grid, clearings (centre, suit-marker spot, number tile), forests, building spaces and ruins on the board image. The layout is saved in `Map.tts_builder` (0–1 image coordinates), and `the_keep/services/tts_map.py` builds the TTS JSON from it, including `BoardLayout` (`clearings` in clearing-number order, and `forests`).

**Measured from the RTT mod's six official maps** (constants in `MapGeometry` and `tts_map.py`):
- **The board:** a `Custom_Token` at scale `12.9794`, rotated 180°. Its image spans **2.0 local units either side of centre across its width**, and `2.0·H/W` down its height.
  - **Unverified:** every official image is about 1.09 : 1, so this rule is unconfirmed for other image shapes.
  - **Axes:** image left is local +x and image top is local −z.
  - **Table offsets:** `dx = −x·12.9794`, `dz = −z·12.9794`.
- **The VP track:** 3 rows × 31 snaps, centred, 0.1232 apart (the spacing of the track printed on the official board images; the mod's own snaps are about 2.5% tighter), rows 0.112 apart. The RTT Box Score finds the track by looking for evenly spaced rows of 28–60 snaps.
- **Items:** a 2 × 6 grid of snaps, 0.1421 × 0.1372 local units apart.
- **Suit markers:**
  - **Snaps:** each marker spot is a snap tagged `Enclave`, whose local rotY is the marker's rotY − 180.
  - **Rotation:** a marker's world rotY is the direction from its clearing centre to the marker (`atan2(dx, dz)`).
- **Clearing number tiles:** round tiles at scale 1.0 (about 1.0 table unit in radius), sitting just outside the clearing circle.

The pieces (item tiles, ruin tiles, suit markers, number tiles) come from `tts/rtt_map_pieces.json`, with art in `the_keep/static/images/tts/assets/`. Refresh them with `tts/extract_rtt_map_pieces.py`.

## Notes for a future editor or Forge builder
- **Keep the key name and convention**, so existing maps keep working, and validate with `validate_board_layout`.
- **Reuse `tts_map.py`** (`MapGeometry`, `build_map_tts_json`) rather than re-deriving the geometry.
- **If Forge Map drafts are built later,** they can store the same builder layout and generate their JSON the same way.
