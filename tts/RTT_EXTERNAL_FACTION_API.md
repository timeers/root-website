# Proposal: an external-faction API for the RTT coordinator

**For:** Root Tabletop Tournament, "Faction Selection" coordinator, GUID `bab7e1`
**From:** the Root Database (therootdatabase.com) TTS Library
**Written against:** Root: Tournament Edition, Workshop item 3800311405

## Why

The Root Database's TTS Library is an object that spawns factions from the site, including custom factions designers build in the Root Database Forge. These factions aren't in the coordinator's catalogue, so they can't go through `manualFactionPick`:

- `makeFaction` reads `board.UI.getAttributes(id).category`. A custom faction has no button with that id, so this errors.
- `rttPlaceFaction` spawns from the coordinator's own catalogue, which doesn't contain custom factions.
- When it errors, `RTT_FAC_TAKEN[id]` and `RTT_MANUAL_PICKING[board]` are left set.

We'd like these factions to get the same treatment as a built-in pick: a seat, a Box Score seat record (colour, player, Steam ID), hand areas, the VP token on the score track and the VP panel.

## Ground rule

**No existing function changes.** This is three new functions plus a version flag, appended to the script and built from helpers that already exist. Built-in picks behave exactly as before.

Callers check `RTT_EXTERNAL_FACTION_API` before calling anything. On builds without it, they fall back to their own behaviour, so nothing breaks either way.

## Today's pick flow, and what the new functions reuse

| # | Built-in pick step | Existing code | New API |
|---|---|---|---|
| 1 | Refuse spectators / Game Master | `rttHasHand(color)` | claim |
| 2 | Ignore double clicks | `RTT_MANUAL_PICKING[board]` | not needed |
| 3 | Refuse a faction already in play | `RTT_FAC_TAKEN[id]` | claim |
| 4 | Read the button's `category` | `board.UI.getAttributes(id).category` | **omitted** |
| 5 | Clear the board's UI | `rttDestroyUI(board)` | not needed (the caller removes itself) |
| 6 | Spawn from the catalogue | `rttSpawnFaction` in `rttPlaceFaction` | **omitted**: the caller spawns |
| 7 | Seat, colour, owner, Steam ID | `rttSeatAt`, `rttSetSeatColor`, `rttSetSeatOwner`, `rttSetSeatSteam` | placed |
| 8 | Seat record and turn order | `rttPublishSeats`, `rttEnableTurns` | placed |
| 9 | VP token onto the 0 column | `RTT_VP_PLACED`, `rttPlaceVPRetry` | placed |
| 10 | VP panel | `rttSpawnVPPanel` | placed |
| 11 | Hand areas | `rttEvictStrayHands`, `rttPlaceHandsAround` | placed |
| 12 | Faction extras | `rttFactionExtras` | **omitted** (base factions only) |

## The code

**Paste this block at the very end of the coordinator's script.** It calls `rttAttachBoard`, which is `local`, so it has to come after that function's definition. Everything else it calls is an existing global.

```lua
-- ── External factions (Root Database TTS Library and others) ───────────────
-- Lets a faction spawned by another object get the same seat, Box Score,
-- VP token, VP panel and hand handling as a built-in pick. Nothing existing
-- is changed; callers check RTT_EXTERNAL_FACTION_API before calling.
RTT_EXTERNAL_FACTION_API = 1

-- Before spawning. params = { faction, row, color, x, z }
-- Returns { ok = true, color = seatColor } or { ok = false, reason = "..." }.
function rttClaimExternalFaction(params)
  if type(params) ~= "table" or params.faction == nil or params.faction == "" then
    return { ok = false, reason = "No faction given." }
  end
  local faction, color = params.faction, params.color
  if not rttHasHand(color) then
    return { ok = false, reason = "Spectators and the Game Master have no hand, so they cannot pick a faction." }
  end
  RTT_FAC_TAKEN = RTT_FAC_TAKEN or {}
  if RTT_FAC_TAKEN[faction] then
    return { ok = false, reason = faction .. " is already in play." }
  end
  local si = rttSeatAt(params.x or 0, params.z or 0, false)
  local seat = si and RTT_SEATS[si] or nil
  if seat ~= nil and seat.faction ~= nil and seat.faction ~= "" then
    return { ok = false, reason = "That seat already has " .. seat.faction .. "." }
  end
  RTT_FAC_TAKEN[faction] = true
  return { ok = true, color = (seat and seat.color) or color }
end

-- After spawning. params = { faction, row, color, x, z, ry, board, vpPanel }
--   row     = VP row: the VP token's name without " VP" (token is "<row> VP")
--   board   = GUID of the spawned faction board (optional)
--   vpPanel = { x, y, z } for the VP panel (optional; no panel when omitted)
-- Returns { ok = true } or { ok = false, reason = "..." }.
function rttExternalFactionPlaced(params)
  if type(params) ~= "table" or params.faction == nil or params.faction == "" then
    return { ok = false, reason = "No faction given." }
  end
  local faction, row, color = params.faction, params.row or params.faction, params.color
  local cx, cz, ry = params.x or 0, params.z or 0, params.ry or 0

  -- Seat: same fields rttPlaceFaction sets for a built-in pick.
  local si = rttSeatAt(cx, cz, true, faction)
  local seat = RTT_SEATS[si]
  seat.faction = faction
  seat.vagN = nil
  seat.key = row
  rttSetSeatColor(seat, color)
  if seat.color == nil then rttSetSeatColor(seat, rttFreeSeatColor()) end
  if color ~= nil and color ~= "" then
    RTT_PICK_N = (RTT_PICK_N or 0) + 1
    seat.picker, seat.pickedAt = color, RTT_PICK_N
    rttSetSeatOwner(seat, rttPersonIn(color))
    rttSetSeatSteam(seat, rttSteamIn(color))
  end
  if params.board ~= nil and params.board ~= "" then
    local b = getObjectFromGUID(params.board)
    if b ~= nil then rttAttachBoard(seat, b) end
  end
  RTT_FAC_TAKEN = RTT_FAC_TAKEN or {}
  RTT_FAC_TAKEN[faction] = true

  -- Hands: same placement makeFaction uses, relative to the seat.
  local direction = Vector(0, 4, -18)
  direction:rotateOver("y", ry)
  local seatHand = { position = Vector(cx, 10.62, cz) + direction, rotation = { 0, ry, 0 },
                     scale = RTT_HAND_SCALE }
  local seatColor = seat.color or color
  pcall(function() rttEvictStrayHands(seatHand.position, seatColor) end)
  pcall(function() rttPlaceHandsAround(seatColor, { pos = seatHand.position, rot = seatHand.rotation }) end)

  -- Seat record to the Box Score, and turn order.
  rttPublishSeats()
  pcall(function()
    local keep = nil
    pcall(function() keep = Turns.turn_color end)
    rttEnableTurns(RTT_TURN_SEATS or #RTT_SEATS, keep or "")
  end)

  -- VP token onto the score track's 0 column (finds "<row> VP" by name).
  RTT_VP_PLACED = (RTT_VP_PLACED or 0) + 1
  local vpN = RTT_VP_PLACED
  rttAfterTime(function() rttPlaceVPRetry(row, vpN, 6) end, 1.2)

  -- The coordinator's own VP panel.
  if type(params.vpPanel) == "table" then
    local p = params.vpPanel
    pcall(function() rttSpawnVPPanel({ p.x or p[1], p.y or p[2] or 11.56, p.z or p[3] }, row, ry) end)
  end
  return { ok = true }
end

-- If the caller's spawn fails after a claim. params = { faction }
function rttReleaseExternalFaction(params)
  if type(params) ~= "table" or params.faction == nil then return { ok = false } end
  if RTT_FAC_TAKEN ~= nil then RTT_FAC_TAKEN[params.faction] = nil end
  return { ok = true }
end
```

### Where each part comes from

- **`rttClaimExternalFaction`** follows the start of `makeFaction`: the `rttHasHand` check and the `RTT_FAC_TAKEN` duplicate check. It adds a seat-free check using `rttSeatAt(x, z, false)`, and returns an answer instead of going on to spawn.
- **`rttExternalFactionPlaced`** follows two existing functions:
  - **The seat block of `rttPlaceFaction`:** `rttSeatAt(..., true, faction)`, `seat.faction`, `rttSetSeatColor` with the `rttFreeSeatColor` fallback, `RTT_PICK_N` and `seat.picker`/`pickedAt`, `rttSetSeatOwner(rttPersonIn)` and `rttSetSeatSteam(rttSteamIn)`, `rttPublishSeats`, `rttEnableTurns`, `RTT_VP_PLACED` plus `rttPlaceVPRetry(..., 6)` after 1.2 s.
  - **The hand block of `makeFaction`:** the `Vector(0, 4, -18)` offset rotated by the board's rotation, `rttEvictStrayHands` and `rttPlaceHandsAround`.
- **Two deliberate differences:**
  - **`seat.key = row`** instead of `rttFactionKey(faction)`. The VP name is then exactly the caller's token name, `"<row> VP"`, which avoids `RTT_VP_SHORT` renaming.
  - **Steps 4, 6 and 12 are left out** (category lookup, catalogue spawn, faction extras).
- **`rttReleaseExternalFaction`** is new. It exists because claiming and spawning are now two steps.

## How the Root Database library calls it

The library only uses this path when `getVar("RTT_EXTERNAL_FACTION_API") >= 1` and the item has a VP token. The steps are:
1. **Fetch** the faction's TTS objects from the Root Database.
2. **Claim:** call `rttClaimExternalFaction` with the clicking player's colour and the library's position. If the answer is `ok = false`, show the reason and spawn nothing.
3. **Spawn** the objects where the library sits. If spawning fails, call `rttReleaseExternalFaction`.
4. **Report:** call `rttExternalFactionPlaced` with the spawned board's GUID and a position for the VP panel, beside the board.
5. **Remove** the library object.

**When the API is present, the library stops doing its current workarounds:**
- It no longer spawns its own copy of the VP Panel.
- It no longer writes to `RTT_VP_PENDING` / `RTT_VP_PLACED` to get the token placed.

## Naming conventions the library already follows

- **VP token:** `"<faction> VP"`. `rttFactionKey` passes unknown names through, so `rttFindVPMarker` finds it.
- **Faction board:** `"<faction> - Faction Board"`. This matches the Box Score's `facAnchor` rule (`"<faction> - "`), so the Box Score can also seat the faction by itself.
- **VP Panel:** `rttSpawnVPPanel(pos, row, ry)` writes `row` into `LuaScriptState`, so the panel finds `"<row> VP"`.

## Caveats

- **This code was written by reading the script, not by running it.** It can't be run outside TTS, so please test it in game.
- **`rttHasHand`** is called with a colour string, the same way `makeFaction` calls it.
- **`rttSpawnVPPanel`** refuses positions inside the map's footprint (`rttUnderMap`) and logs why. Callers must place the panel beside the board.
- **A pre-existing limitation, unchanged here:** `rttSpawnVPPanel` drops `row` straight into its JSON with `gsub`, so a faction name containing `%` or `"` could break the panel.

## Testing

Spawn the Root Database Library object from https://www.therootdatabase.com/api/tts/loader/object.json and pick a faction from its "The Forge" tab while sitting at a seat on the Tournament Edition table. With the API pasted in, you should see:
- the faction's seat colour and player in the Box Score,
- its VP token on the score track's 0 column,
- the VP panel beside the board,
- hand areas placed for that seat.

Picking the same faction again should be refused with "<faction> is already in play."
