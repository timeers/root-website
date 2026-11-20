# Proposal: an external API for the RTT coordinator

**For:** Root Tabletop Tournament, "Faction Selection" coordinator, GUID `bab7e1`
**From:** the Root Database (therootdatabase.com) TTS Library
**Written against:** Root: Tournament Edition, Workshop item 3800311405

## Why

The Root Database's TTS Library is an object that spawns posts from the site: factions (including custom factions built in the Root Database Forge), maps, decks, house rules, landmarks and hirelings. None of these are in the coordinator's catalogue, so today they can't get the same treatment as a built-in pick:

- **Factions** can't go through `manualFactionPick`. `makeFaction` reads `board.UI.getAttributes(id).category`, which errors for a faction with no button, and `rttPlaceFaction` only spawns from the catalogue. The error leaves `RTT_FAC_TAKEN[id]` and `RTT_MANUAL_PICKING[board]` set.
- **Maps and decks** can only be spawned from `EVERYTHING`, so an outside map doesn't get the coordinator's clearing, tagging, ruin and suit shuffle, post-map steps or VP placement. An outside deck doesn't land on the deck holder.
- **The Box Score** only recognises the base maps, decks and landmarks (by image and card-back fingerprints), never records hirelings, and its manual menus only list base items. So outside items can't be uploaded with the game.

This proposal lets any outside source hand its objects to the coordinator and get built-in behaviour.

## Ground rule

**Built-in behaviour must not change.** The proposal is:

- new functions, all behind one version flag, `RTT_EXTERNAL_API = 1`;
- a restructure of `makeMap` and `makeDeck` into shared steps. The code is **moved, not changed**, and the original catalogue paths call the same steps in the same order;
- a few clearly marked one-line additions (seat record fields, save and restore, set-up lookup fallbacks, suit tags).

Callers check `RTT_EXTERNAL_API` before calling anything, and fall back to their own behaviour on builds without it.

**Built-in checklist to verify in game after applying:**
- every base map spawns as before, including Marsh 5-player (landmarks, flood tiles, numbers), Mountain (landmark and plan), and the Summer, Lake, Mountain, Winter and Gorge priority spawns;
- ruins and clearing suits are shuffled (not on Marsh), and the clearing suits are published;
- the battle mat, turn panel and Box Score spawn after the map, and queued VP tokens move onto the new track;
- the Flotilla card is placed (theme mode);
- every base deck spawns, shuffled, tagged `Deck Object`; frog cards are restored and the Dragon God is re-placed;
- picking a built-in faction, map or deck after an outside one replaces it in the Box Score.

## 1. Factions

Today's pick flow, and what the new functions reuse:

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

```lua
-- ── External factions ──────────────────────────────────────────────────────
-- Lets a faction spawned by another object get the same seat, Box Score,
-- VP token, VP panel and hand handling as a built-in pick.

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

**Where it comes from:**
- `rttClaimExternalFaction` follows the start of `makeFaction` (the hand check, `RTT_FAC_TAKEN`) and adds a seat-free check.
- `rttExternalFactionPlaced` follows the seat block of `rttPlaceFaction` and the hand block of `makeFaction`.
- **Two deliberate differences:** `seat.key = row` (so the VP name is exactly the caller's token, `"<row> VP"`), and steps 4, 6 and 12 are omitted.

## 2. Maps and decks: the coordinator places them

`makeMap` and `makeDeck` are split into shared steps. The catalogue paths call those steps exactly as before, and the new entry points pass outside objects through the same steps.

### Maps

**Moved, not changed:**

| Original part of `makeMap` | Now in |
|---|---|
| `RTT_MAP_AFTER_Q = {}`, busy check, `RTT_5P_MARSH` reset, `RTT_MAP_GEN` | `rttBeginMap` |
| `RTT_CURRENT_MAP = id`, `fivep` | `rttBeginMap` |
| Marsh landmark hook, `RTT_MARSH_5P_BUILT`, Mountain / priority / Marsh-number `rttMapAfter` hooks | the `hooks` callback passed by `makeMap` (map-specific; stays in `makeMap`) |
| Flotilla `rttWhenMapReady`, keep board, `removeMapItems`, `rttTagMap(keep)`, VP timer, `RTT_MAP_AFTER` | `rttBeginMap` |
| Catalogue pieces, Marsh/Mountain plans, specs | `makeMap` (unchanged) |
| Per-piece callback: bag/Treasure shuffle, `Map Object`, board tag, card helper tag | `rttMapPieceCallback` |
| Per-piece callback: lock, Marsh pieces, clearing suits | `makeMap` (unchanged) |
| `rttSpawnStaggered` and its completion (shuffle, lock ruins, suits, hold locked, post-map steps) | `rttFinishMap` |

**One ordering detail:** the map-specific hooks now run just after `RTT_CURRENT_MAP` and `fivep` are set, instead of just before. The hooks only queue callbacks (`rttMapAfter`, `rttWhenMapReady`, the `RTT_MARSH_5P_BUILT` flag) and don't read either value, so the behaviour is the same.

**Replace `makeMap` with:**
```lua
function makeMap(player,value,id,keepBoard)
  local gen, fivep, RTT_KEEP_BOARD = rttBeginMap(player, id, keepBoard, function(gen, fivep)
    if id == "Marsh Map" and RTT_5P_MARSH then
      rttWhenMapReady(function() if RTT_MAP_GEN == gen then rttMarshLandmarks() end end)
    end
    if id == "Marsh Map" then RTT_MARSH_5P_BUILT = (RTT_5P_MARSH == true) end
    if id == "Mountain Map" then rttMapAfter(function() rttMountainLandmark() end) end
    if id == "Summer Map" then rttMapAfter(function() rttSpawnPriority("Summer Map", RTT_PRIO_SUMMERMAP) end) end
    if id == "Lake Map" then rttMapAfter(function() rttSpawnPriority("Lake Map", RTT_PRIO_LAKEMAP) end) end
    if id == "Mountain Map" then rttMapAfter(function() rttSpawnPriority("Mountain Map", RTT_PRIO_MOUNTAINMAP) end) end
    if id == "Winter Map" then rttMapAfter(function() rttSpawnPriority("Winter Map", RTT_PRIO_WINTERMAP) end) end
    if id == "Gorge Map" then rttMapAfter(function() rttSpawnPriority("Gorge Map", RTT_PRIO_GORGEMAP) end) end
    if id == "Marsh Map" then rttMapAfter(function() rttSpawnMarshNumbers() end) end
  end)
  if gen == nil then return end
  -- A built-in map replaces any outside one in the seat record.
  if RTT_EXTERNAL ~= nil then RTT_EXTERNAL.map, RTT_EXTERNAL.map_name = nil, nil end

  local objects = {}
  objects = EVERYTHING["Maps"][id]['data']
  local RTT_OV = nil
  if id == "Marsh Map" then
    for _, g in ipairs(RTT_MARSH_PIECES or {}) do
      local o = nil
      pcall(function() o = getObjectFromGUID(g) end)
      if o ~= nil then pcall(function() o.destruct() end) end
    end
    RTT_MARSH_PIECES = {}
    if RTT_5P_MARSH then RTT_OV = rttMarshPlan5P(objects) else RTT_OV = rttMarshPlan(objects) end
  end
  if id == "Mountain Map" then RTT_OV = rttMountainPlan(objects) end
  local scale = rttPlaceScale()

  local boardIdx = rttMapBoardIndex(objects)
  local mapSpecs = {}
  RTT_SUIT_MAP = {}
  for idx,v in ipairs(objects) do
    local rtt_rot = nil
    local rtt_ov = false
    local new_pos
    local ovJson = nil
    local skip = (RTT_KEEP_BOARD ~= nil and idx == boardIdx)
    if RTT_OV ~= nil and RTT_OV[idx] ~= nil then
      skip = skip or (RTT_OV[idx].skip == true)
      ovJson = RTT_OV[idx].json
    end
    if RTT_OV ~= nil and RTT_OV[idx] ~= nil and RTT_OV[idx].world ~= nil then
      rtt_rot = RTT_OV[idx].rot
      rtt_ov = true
      new_pos = RTT_OV[idx].world
    else
      local vec = Vector(v.move_to) * scale
      vec.y = vec.y - 0.1
      vec = vec * Vector({15.5, 1, 15.5})
      new_pos = vec
      new_pos.y = new_pos.y+10-8.5+0.05-0.07+10.08
    end
    if not skip then
    mapSpecs[#mapSpecs + 1] = {
        json              = ovJson or v.json,
        position          = new_pos,
        rotation          = rtt_rot,
        callback_function = function(spawned_object)

        if rtt_ov then spawned_object.setLock(true) end
        rttMapPieceCallback(spawned_object, idx == boardIdx, id, fivep)
        if rtt_ov and RTT_MARSH_PIECES ~= nil then
          pcall(function() RTT_MARSH_PIECES[#RTT_MARSH_PIECES + 1] = spawned_object.getGUID() end)
        end
        local sj = ovJson or v.json
        if type(sj) == "string" and string.find(sj, "Clearing Marker", 1, true) then
          local suit = rttSuitOfJson(sj)
          if suit ~= nil then
            pcall(function() RTT_SUIT_MAP[spawned_object.getGUID()] = suit end)
            rttPublishSuits()
          end
        end
        end
    }
    end
  end
  rttFinishMap(id, mapSpecs, gen, true, nil)
end
```

**Add (anywhere after `rttTagMap` and the other helpers; the end of the script is fine):**
```lua
-- Generic opening of a map spawn, moved unchanged from makeMap. `hooks(gen,
-- fivep)` runs the map-specific queueing (rttMapAfter / rttWhenMapReady) at the
-- same point makeMap ran it. Returns gen, fivep, keep-board object -- or nil
-- when the table is busy.
function rttBeginMap(player, id, keepBoard, hooks)
  RTT_MAP_AFTER_Q = {}
  if rttBusyByHuman(player, "Map is currently spawning.") then return nil end
  local byHuman = false
  pcall(function() byHuman = (player ~= nil and player ~= "" and player.color ~= nil) end)
  if byHuman then RTT_5P_MARSH = false end
  RTT_MAP_GEN = (RTT_MAP_GEN or 0) + 1
  local gen = RTT_MAP_GEN
  RTT_CURRENT_MAP = id
  local fivep = (id == "Marsh Map" and RTT_5P_MARSH == true)
  if hooks ~= nil then hooks(gen, fivep) end
  rttWhenMapReady(function()
    pcall(function() rttPlaceFlotillaCard() end)
    for _, f in ipairs({ 4, 12, 30, 60 }) do
      Wait.frames(function() pcall(function() rttPlaceFlotillaCard() end) end, f)
    end
  end)
  local RTT_KEEP_BOARD = nil
  if keepBoard then pcall(function() RTT_KEEP_BOARD = rttFindMapObject() end) end
  removeMapItems(RTT_KEEP_BOARD)
  rttTagMap(RTT_KEEP_BOARD, id, fivep)
  Wait.time(function() pcall(function() rttPlaceUnplacedVPs() end) end, 2.0)
  RTT_MAP_AFTER = function()
    local jobs = {}
    jobs[#jobs + 1] = function()
      if rttFixture(RTT_TAG_MAT) == nil then
        makeSpecialWithTag("Tools", "Battle Mat", 33.17, 1.55, 9.21, "Map Object")
        Wait.frames(function()
          for _, o in ipairs(getObjectsWithTag("Map Object")) do
            pcall(function()
              if rttNameOf(o) == "Battle Mat" then o.addTag(RTT_FIXTURE_TAG) o.addTag(RTT_TAG_MAT) end
            end)
          end
        end, 2)
      end
    end
    jobs[#jobs + 1] = function() pcall(function() rttSpawnMapExtras() end) end
    for _, fn in ipairs(RTT_MAP_AFTER_Q or {}) do jobs[#jobs + 1] = fn end
    RTT_MAP_AFTER_Q = {}
    local i = 0
    local function step()
      i = i + 1
      if jobs[i] == nil then return end
      pcall(jobs[i])
      Wait.frames(step, 1)
    end
    step()
  end

  return gen, fivep, RTT_KEEP_BOARD
end

-- Generic per-piece callback, moved unchanged from makeMap's spec callback.
function rttMapPieceCallback(spawned_object, isBoard, id, fivep)
  if spawned_object.name == "Bag" then spawned_object.shuffle() end
  if spawned_object.getName() == "Treasure" then
    spawned_object.shuffle()
    spawned_object.addTag("Map Object")
    end
  spawned_object.addTag("Map Object")
  if isBoard then rttTagMap(spawned_object, id, fivep) end
  if spawned_object.name == "CardCustom" or spawned_object.name == "Card" then
    spawned_object.addTag(RTT_HELPER_TAG)
  end
end

-- Generic close of a map spawn, moved unchanged from makeMap. Built-in maps
-- shuffle exactly as before (all but Marsh); outside maps when shuffle == true.
function rttFinishMap(id, mapSpecs, gen, builtIn, shuffle)
  rttSpawnStaggered(mapSpecs, function()
    if builtIn then
      if id ~= "Marsh Map" then shuffleMaps(id) end
    elseif shuffle == true then
      shuffleMaps(id)
    end
    rttLockRuins()
    rttPublishSuits()
    rttHoldMapLocked()
    if RTT_MAP_AFTER ~= nil then
      local after = RTT_MAP_AFTER
      RTT_MAP_AFTER = nil
      local function whenIdle()
        if RTT_SPAWN_PUMPING then Wait.frames(whenIdle, 1) return end
        if RTT_MAP_GEN ~= gen then return end
        pcall(after)
      end
      Wait.frames(whenIdle, 1)
    end
  end, function() return RTT_MAP_GEN == gen end)
end
```

### Decks

| Original part of `makeDeck` | Now in |
|---|---|
| Busy check, `clearInfo`, `rttFrogCardsInDecks`, `removeDeckItems` | `rttBeginDeck` |
| Catalogue positions | `makeDeck` (unchanged) |
| Per-piece callback: rotate −90°, tag `Deck Object`, shuffle | `rttDeckPieceCallback` (an empty loop that iterated every object and did nothing is dropped) |
| Dragon God re-place, frog restore | `rttFinishDeck` |

**Replace `makeDeck` with:**
```lua
function makeDeck(player,value,id)
  local frogs = rttBeginDeck(player)
  if frogs == nil then return end
  -- A built-in deck replaces any outside one in the seat record.
  if RTT_EXTERNAL ~= nil then RTT_EXTERNAL.deck, RTT_EXTERNAL.deck_name = nil, nil end
  local my_rot = self.getRotation()
  local objects = {}
  local scale = rttPlaceScale()

  if (ends_with(id,"2")) then
    allObjects = {EVERYTHING["Decks"]['Refill Card']['data'],EVERYTHING["Decks"][id]['data']}
  else
    allObjects = {EVERYTHING["Decks"]['Refill Card']['data'],EVERYTHING["Decks"]["Dominance Track Card"]['data'],EVERYTHING["Decks"][id]['data']}
  end

  for _,n in ipairs(allObjects) do
    for _,v in ipairs(n) do
      local vec = Vector(v.move_to) * scale
      vec.y = vec.y - 0.1

      vec = vec * Vector({15.5, 1, 15.5})

      local newVec = Vector({0,1,0})
      newVec.x = vec.z * -1
      newVec.y = vec.y + 10.01
      newVec.z = vec.x

      local new_pos = newVec
      new_pos.y = new_pos.y+10-8.5+0.05
      new_pos.x = new_pos.x - 45 + 8.01
      spawnObjectJSON({
          json              = v.json,
          position          = new_pos,
          callback_function = rttDeckPieceCallback
      })
    end
  end
  rttFinishDeck(frogs)
end
```

**Add:**
```lua
-- Generic opening of a deck spawn, moved unchanged from makeDeck. Returns the
-- frog cards to restore afterwards -- or nil when the table is busy.
function rttBeginDeck(player)
  if rttBusyByHuman(player, "The deck is being placed.") then return nil end
  clearInfo()
  local frogs = rttFrogCardsInDecks()
  removeDeckItems()
  return frogs
end

-- Per-piece callback, moved unchanged from makeDeck (minus an empty loop that
-- iterated every object and did nothing).
function rttDeckPieceCallback(o)
  o.setRotation({o.getRotation().x, o.getRotation().y-90, o.getRotation().z})
  local _tg=o.getTags(); table.insert(_tg,"Deck Object"); o.setTags(_tg)
  if o.name == "Deck" then
    o.shuffle()
  end
end

-- Generic close of a deck spawn, moved unchanged from makeDeck.
function rttFinishDeck(frogs)
  if find_object_by_gm_note("Dragon God") ~= nil then
    Wait.frames(function() pcall(function() rttPlaceDragonGod() end) end, 2)
  end
  if #frogs > 0 then Wait.time(function() rttRestoreFrogs(frogs, 0) end, RTT_FROG_WAIT) end
end
```

### New entry points and records

**Add at the end of the script:**
```lua
-- ── Outside maps, decks and records ───────────────────────────────────────
RTT_EXTERNAL_API = 1

-- What outside sources placed, for the seat record (and so the Box Score).
-- Saved and restored with the rest of the coordinator's state.
RTT_EXTERNAL = RTT_EXTERNAL or { landmarks = {}, hirelings = {}, layouts = {} }

-- Place an outside map exactly like a built-in one: clear the old map, spawn at
-- the table centre, tag, shuffle, publish suits, run the post-map steps.
-- params = {
--   name    = "Fall Map",          -- shown in tags and the Box Score
--   slug    = "fall-map",          -- Root Database slug, sent by the Box Score
--   shuffle = true,                -- run shuffleMaps (ruins/suits by tag)
--   layout  = { clearings = {{x, z}, ...}, forests = {...}, relics = {...} },
--   objects = { { json = "<object JSON>", x =, y =, z =, board = true|nil }, ... },
-- }
-- Each object's x/y/z is its offset from the board; the board sits at the
-- table centre at the same height built-in boards use (move_to.y + 11.46).
function rttPlaceExternalMap(params)
  if type(params) ~= "table" or type(params.objects) ~= "table" or #params.objects == 0 then
    return { ok = false, reason = "No map objects given." }
  end
  local name = params.name or "Custom Map"
  local gen, fivep = rttBeginMap(nil, name, false, nil)
  if gen == nil then return { ok = false, reason = "Map is currently spawning." } end
  RTT_SUIT_MAP = {}
  local specs = {}
  for _, o in ipairs(params.objects) do
    local isBoard = (o.board == true)
    local json = o.json
    specs[#specs + 1] = {
      json = json,
      position = { o.x or 0, (o.y or 0) + 11.46, o.z or 0 },
      callback_function = function(spawned_object)
        rttMapPieceCallback(spawned_object, isBoard, name, fivep)
        if type(json) == "string" and string.find(json, "Clearing Marker", 1, true) then
          local suit = rttSuitOfJson(json)
          if suit ~= nil then
            pcall(function() RTT_SUIT_MAP[spawned_object.getGUID()] = suit end)
            rttPublishSuits()
          end
        end
      end,
    }
  end
  rttFinishMap(name, specs, gen, false, params.shuffle)
  RTT_EXTERNAL.map, RTT_EXTERNAL.map_name = params.slug, name
  RTT_EXTERNAL.layouts = RTT_EXTERNAL.layouts or {}
  RTT_EXTERNAL.layouts[name] = (type(params.layout) == "table") and params.layout or nil
  rttPublishSeats()
  return { ok = true }
end

-- Place an outside deck exactly like a built-in one: clear the old deck, put the
-- new one face down on the deck holder's draw slot, tag and shuffle it.
-- params = { name = "Deck name", slug = "deck-slug", objects = { "<object JSON>", ... } }
function rttPlaceExternalDeck(params)
  if type(params) ~= "table" or type(params.objects) ~= "table" or #params.objects == 0 then
    return { ok = false, reason = "No deck objects given." }
  end
  local holder = getObjectFromGUID(RTT_HOLDER_GUID)
  if holder == nil then return { ok = false, reason = "No deck holder on this table." } end
  local frogs = rttBeginDeck(nil)
  if frogs == nil then return { ok = false, reason = "The deck is being placed." } end
  local slot = holder.positionToWorld(RTT_HOLDER_DRAW)
  local ry = holder.getRotation().y
  for i, json in ipairs(params.objects) do
    spawnObjectJSON({
      json = json,
      position = { slot.x, slot.y + 0.5 + i * 0.3, slot.z },
      -- +90 because rttDeckPieceCallback turns every deck piece -90 on y.
      rotation = { 0, ry + 90, 180 },
      callback_function = function(o)
        rttDeckPieceCallback(o)
        if o.name == "DeckCustom" then o.shuffle() end
      end,
    })
  end
  rttFinishDeck(frogs)
  RTT_EXTERNAL.deck, RTT_EXTERNAL.deck_name = params.slug, params.name
  rttPublishSeats()
  return { ok = true }
end

-- House rule by Root Database slug (sent by the Box Score as "houserules").
function rttAddHouseRule(params)
  if type(params) ~= "table" or params.slug == nil or params.slug == "" then return { ok = false } end
  RTT_HOUSERULES = RTT_HOUSERULES or {}
  for _, r in ipairs(RTT_HOUSERULES) do
    if r == params.slug then return { ok = true } end
  end
  RTT_HOUSERULES[#RTT_HOUSERULES + 1] = params.slug
  rttPublishSeats()
  return { ok = true }
end

-- Landmark or hireling by Root Database slug. params = { kind = "landmark"|"hireling", slug }
function rttExternalAssetAdded(params)
  if type(params) ~= "table" or params.slug == nil or params.slug == "" then return { ok = false } end
  local key = (params.kind == "hireling") and "hirelings" or "landmarks"
  RTT_EXTERNAL[key] = RTT_EXTERNAL[key] or {}
  for _, s in ipairs(RTT_EXTERNAL[key]) do
    if s == params.slug then return { ok = true } end
  end
  RTT_EXTERNAL[key][#RTT_EXTERNAL[key] + 1] = params.slug
  rttPublishSeats()
  return { ok = true }
end

-- An outside map's layout list ("clearings", "forests" or "relics") as world
-- {x, z} points, or nil. Offsets are from the board's centre in table axes --
-- the same convention as RTT_CLEARING_CENTRES (no rotation applied).
function rttExternalLayoutWorld(mapId, key)
  local layouts = RTT_EXTERNAL and RTT_EXTERNAL.layouts or nil
  local layout = (layouts ~= nil and mapId ~= nil) and layouts[mapId] or nil
  local points = layout and layout[key] or nil
  if type(points) ~= "table" or #points == 0 then return nil end
  local m = rttFindMapObject()
  if m == nil then return nil end
  local p = m.getPosition()
  local out = {}
  for _, pt in ipairs(points) do
    out[#out + 1] = { p.x + (pt[1] or 0), p.z + (pt[2] or 0) }
  end
  return out
end
```

## 3. Small additive changes

**Set-up lookups fall back to an outside map's layout** (base maps are unaffected):

In `rttMarquiseCats`:
```lua
  local centres = (mapId ~= nil) and (RTT_CLEARING_CENTRES[mapId] or rttExternalLayoutWorld(mapId, "clearings")) or nil
```
In `rttForestWorldCenters`:
```lua
  if cents == nil then return rttExternalLayoutWorld(mapId, "forests") or {} end
```
In `rttBadgerRelics`, in the `else` branch:
```lua
    targets = rttExternalLayoutWorld(mapId, "relics") or rttForestWorldCenters(mapId)
```

**Suit tags.** In `rttSuitOfJson`, before the final `return nil`:
```lua
  local tagged = string.match(j, '"Suit: (%a+)"')
  if tagged ~= nil then return string.lower(tagged) end
```

**Seat record.** In `rttSeatRecord`, before `return out`:
```lua
  if RTT_EXTERNAL ~= nil then
    out.map, out.map_name = RTT_EXTERNAL.map, RTT_EXTERNAL.map_name
    out.deck, out.deck_name = RTT_EXTERNAL.deck, RTT_EXTERNAL.deck_name
    if #(RTT_EXTERNAL.landmarks or {}) > 0 then out.landmarks = RTT_EXTERNAL.landmarks end
    if #(RTT_EXTERNAL.hirelings or {}) > 0 then out.hirelings = RTT_EXTERNAL.hirelings end
  end
```

**Save and restore.**
- In `onSave`'s state, next to `houserules = RTT_HOUSERULES or {},`, add:
  ```lua
                           external = RTT_EXTERNAL,
  ```
- In the restore block, after `RTT_HOUSERULES` is rebuilt from `d.houserules`, add:
  ```lua
      RTT_EXTERNAL = (type(d.external) == "table") and d.external or {}
      RTT_EXTERNAL.landmarks = RTT_EXTERNAL.landmarks or {}
      RTT_EXTERNAL.hirelings = RTT_EXTERNAL.hirelings or {}
      RTT_EXTERNAL.layouts = RTT_EXTERNAL.layouts or {}
  ```
- In the reset that sets `RTT_HOUSERULES = {}` (with `RTT_SEATS = {}`), add:
  ```lua
    RTT_EXTERNAL   = { landmarks = {}, hirelings = {}, layouts = {} }
  ```

## 4. Box Score

**In `tournamentPayload`, after the `houserules` block** (the `lms` table already exists above it):
```lua
  -- Outside map / deck / landmarks / hirelings recorded by the coordinator
  -- (RTT_EXTERNAL_API): sent as Root Database slugs.
  if seatRec ~= nil then
    if type(seatRec.map) == "string" and seatRec.map ~= "" then p.board_map = seatRec.map end
    if type(seatRec.deck) == "string" and seatRec.deck ~= "" then p.deck = seatRec.deck end
    if type(seatRec.landmarks) == "table" and #seatRec.landmarks > 0 then
      for _, s in ipairs(seatRec.landmarks) do lms[#lms + 1] = s end
      p.landmarks = lms
    end
    if type(seatRec.hirelings) == "table" and #seatRec.hirelings > 0 then p.hirelings = seatRec.hirelings end
  end
```

**In `rttSeatPush`, after `S.rttSeats = d`:**
```lua
  -- Show an outside map / deck name when nothing was detected from the table.
  if type(d.map_name) == "string" and d.map_name ~= "" and (S.meta.map or "") == "" then S.meta.map = d.map_name end
  if type(d.deck_name) == "string" and d.deck_name ~= "" and (S.meta.deck or "") == "" then S.meta.deck = d.deck_name end
```

**How these are matched:** the Root Database importer already accepts `board_map`, `deck`, `landmarks`, `houserules` and `hirelings` as slugs and matches them to its posts. The payload is encoded with `JSON.encode`, so the new key is included automatically.

## 5. How the Root Database library calls it

The library uses the API only when `getVar("RTT_EXTERNAL_API") >= 1`.

| Spawned post | Call |
|---|---|
| Faction / Clockwork | `rttClaimExternalFaction`, then it spawns, then `rttExternalFactionPlaced` (or `rttReleaseExternalFaction` if the spawn fails) |
| Map | `rttPlaceExternalMap{ name, slug, shuffle, layout, objects }`, where the objects are offsets from the board and the board entry has `board = true` |
| Deck | `rttPlaceExternalDeck{ name, slug, objects }` |
| House rule (Tweak) | it spawns, then `rttAddHouseRule{ slug }` |
| Landmark / Hireling | it spawns, then `rttExternalAssetAdded{ kind, slug }` |

**Without the API,** the library places maps and decks itself, writes house rules into `RTT_HOUSERULES`, queues VP tokens through `RTT_VP_PENDING` and spawns its own copy of the VP Panel. Once the API is present, it stops doing all of that.

## 6. Notes and caveats

- **Map data:**
  - **Random set-up:** outside maps carry `Ruin`, `Dummy`, `Clearing Marker`, `Shuffleable` and so on as object tags, so `shuffleMaps` randomises them like base maps. Suit markers can be tagged `Suit: fox|rabbit|mouse|bird`.
  - **Layout:** clearing, forest and relic positions come as data (`layout`), mirroring `RTT_CLEARING_CENTRES` / `RTT_FOREST_UV` / `RTT_RELIC_POS`, with offsets from the board's centre in table axes.
- **Score track:** the Box Score reads VP from a track it finds in a board's snap points (`findTrack`). An outside map's board needs those snap points for automatic VP scoring; it's recorded either way.
- **`rttSpawnVPPanel`** refuses positions inside the map's footprint (`rttUnderMap`), so callers place the panel beside the board.
- **Pre-existing, unchanged:** `rttSpawnVPPanel` writes `row` straight into its JSON with `gsub`, so a faction name containing `%` or `"` could break the panel.
- **Not run outside TTS:** this code was written against build 3800311405. It was checked by applying it to a copy of the script and parsing the result, but it hasn't been run outside TTS, so please test it in game against the checklist above.
- **Unified diffs** of the changes against build 3800311405 are attached: `rtt_external_api.coordinator.diff` and `rtt_external_api.boxscore.diff`.

## 7. Testing

Spawn the Root Database Library object from https://www.therootdatabase.com/api/tts/loader/object.json and try these:
- **A faction** from its Workshop tab, while seated.
- **A map, a deck, a house rule, a landmark and a hireling** from its Library tab.
- **The checklist:** run through the built-in checklist above.
