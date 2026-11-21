--[[
  Root Database Library for Tabletop Simulator.

  Lists everything the site has published for TTS:
    * Archive  -- active posts whose Post.tts_json is set
    * Workshop -- Forged drafts an admin approved for the TTS Library
  Search, pick one, and its objects spawn beside this object.

  This file (tts/loader.lua) is the single source. The site serves it from
    GET /api/tts/loader.lua           -> this script, LOADER_VERSION/BASE_URL filled in
    GET /api/tts/loader/version/      -> { version }
    GET /api/tts/loader/object.json   -> a ready-made object carrying this script
  and an object built that way updates itself on load when the deployed file
  changes. BASE_URL is always the production site. A copy pasted straight from
  the repo, or downloaded from a dev server, keeps LOADER_VERSION = "dev" and
  never updates itself (re-download it to test another change).

  Data endpoints (the_keep/api_views.py):
    GET /api/tts/catalog/           -> { keep = [...], forge = [...] }
    GET /api/tts/keep/<slug>/       -> { title, objects = [{x, y, z, json}] }
    GET /api/tts/forge/<id>/        -> same shape
  Each object arrives pre-encoded ("json") with its position pulled out, so
  this script never runs MoonSharp's (slow) JSON on the big object blobs.
]]

-- Both filled in by the server when it serves this script; keep these two
-- lines' format (the_keep/api_views.py substitutes them by regex).
LOADER_VERSION = "dev"
BASE_URL = "https://www.therootdatabase.com"

PANEL_W, PANEL_H = 880, 640   -- library panel, laid out at 4x (drawn at 0.25 scale)
CLOSE_SIZE = 11           -- close button size, in UI units (fills the margin around the panel)
CLOSE_GAP = 3             -- extra space between the panel's top-right corner and the close button
MAX_ROWS = 50             -- result rows built once; filtering only toggles them
SPAWN_OFFSET = {x = 0, y = 2, z = 0}    -- spawned items replace this object, in its place

-- Root Tabletop Tournament integration. A faction item ("<faction> VP" marker)
-- also gets RTT's VP Panel, but only when the RTT coordinator is on the table:
-- every panel button relays to it, so without it the panel would do nothing.
RTT_COORD_GUID = "bab7e1"
VP_PANEL_OFFSET = {x = 3, z = -5}       -- panel position relative to its VP marker; tune in game

-- Maps and decks on the RTT table go where the coordinator puts its own.
MAP_CENTER = {x = 0, y = 11.61, z = 0}   -- where makeMap puts a board: move_to.y (0.15 for every board) + 11.46;
                                         -- the board is locked, so any lower and it sits inside the table
MAP_SETTLE_SECONDS = 2.5                -- let a map spawn before shuffling / placing VP tokens
DECK_HOLDER_GUID = "aa1464"             -- the RTT deck holder
DECK_DRAW_SLOT = {0.957, 0.178, 0.222}  -- its draw slot, in holder-local coordinates
DECK_ROT_Y = 0                          -- extra y rotation for spawned decks; tune in game
DECK_ACCESSORY_SLOT = {1.9, 0.178, 0.222} -- first accessory deck, holder-local, past the draw slot; tune in game
DECK_ACCESSORY_STEP = {0.85, 0, 0}        -- holder-local step between accessory decks; tune in game
vpPanelTemplate = nil                   -- fetched once, from /api/tts/vp-panel/

-- Shown under the tabs; explains where each tab's items come from.
TAB_DESCRIPTIONS = {
  keep = "Published components from the Root Database Library.",
  forge = "Prototype components from the RDB Forge meant for playtesting.",
}

COMPONENT_TYPES = {"Faction", "Clockwork", "Vagabond", "Map", "Deck", "Hireling", "Landmark", "Tweak"}

catalog = {keep = {}, forge = {}}
section = "keep"          -- "keep" or "forge"
typeFilter = {}           -- per tab: typeFilter[section] = component, or nil for all
searchText = ""
rowItems = {}             -- row index -> catalog entry currently shown there (nil for headers)
rowKind = {}              -- row index -> "header" or "entry": the style it currently has

-- Row colours. Status headers on the Keep tab use the darker pair.
ROW_COLOR, ROW_TEXT_COLOR = "#EFE6D2", "#2B2118"
HEADER_COLOR, HEADER_TEXT_COLOR = "#6B4F2E", "#EFE6D2"
busy = false

-- ── UI ────────────────────────────────────────────────────────────────────

local function buttonXml(id, text, onClick, extra)
  return string.format(
    '<Button id="%s" onClick="%s" %s>%s</Button>', id, onClick, extra or "", text)
end

local function buildXml()
  local typeButtons = {buttonXml("type_all", "All", "onTypeClick", 'color="#C8A86B"')}
  for _, t in ipairs(COMPONENT_TYPES) do
    table.insert(typeButtons, buttonXml("type_" .. t, t, "onTypeClick"))
  end

  local rows = {}
  for i = 1, MAX_ROWS do
    -- TTS ignores UI.setValue on a Button's own label, so each row carries a
    -- child Text ("rowLabel<i>") that refreshUi writes into; setValue works on
    -- Text (the status line uses it too). raycastTarget="false" lets clicks
    -- pass through the label to the button.
    local label = string.format(
      '<Text id="rowLabel%d" raycastTarget="false" fontSize="16" color="#2B2118" ' ..
      'alignment="MiddleLeft" offsetXY="8 0">-</Text>', i)
    table.insert(rows, buttonXml("row" .. i, label, "onRowClick",
      'active="false" preferredHeight="34"'))
  end

  -- Sized for a Custom_Tile at scale 15.5 (same as the TTS Faction Board): the
  -- tile face reaches about x 121, y 91 in UI units, with -20 on z lifting the
  -- UI above the surface and no rotation needed. The panel is laid out at 4x and
  -- scaled down by 0.25 (880 x 640 -> 220 x 160, edges at x 110, y 80), leaving
  -- an ~11 unit margin that the close button fills, just outside the panel's
  -- top-right corner. Shrink the panel if CLOSE_SIZE grows.
  local panelHalfW, panelHalfH = PANEL_W * 0.25 / 2, PANEL_H * 0.25 / 2
  local closeX = panelHalfW + CLOSE_SIZE / 2 + CLOSE_GAP
  local closeY = panelHalfH + CLOSE_SIZE / 2 + CLOSE_GAP
  -- Built at 4x and drawn at 0.25 scale, like the panel: at its real size
  -- (~11 units) the button's fixed corner radius turns it into a circle and its
  -- padding leaves no room for the X. Scaling keeps a rounded square with a
  -- readable label while it still covers CLOSE_SIZE x CLOSE_SIZE on the tile.
  local closeBuilt = CLOSE_SIZE * 4
  local closeButton = string.format(
    '<Button id="closeButton" onClick="deleteThis" position="%.1f %.1f -21" ' ..
    'width="%d" height="%d" scale="0.25 0.25 1" padding="0 0 0 0" ' ..
    'color="#bd2608" textColor="#FFFFFF" fontSize="%d" fontStyle="Bold">X</Button>',
    closeX, closeY, closeBuilt, closeBuilt, math.floor(closeBuilt * 0.7))
  return [[
<Defaults>
  <Button fontSize="16" color="#EFE6D2" textColor="#2B2118"/>
  <Text color="#EFE6D2"/>
</Defaults>
<Panel position="0 0 -20" width="]] .. PANEL_W .. [[" height="]] .. PANEL_H .. [[" scale="0.25 0.25 1" color="#2B2118EE">
  <VerticalLayout padding="12" spacing="8" childForceExpandHeight="false">
    <HorizontalLayout preferredHeight="44" spacing="10" childAlignment="MiddleCenter"
                      childForceExpandWidth="false" childForceExpandHeight="false">
      <Image image="rdbLogo" preferredWidth="44" preferredHeight="44" preserveAspect="true"/>
      <Text fontSize="26" fontStyle="Bold" preferredWidth="300" preferredHeight="44"
            alignment="MiddleLeft">Root Database Archive</Text>
      ]] .. buttonXml("refresh", "Refresh", "onRefreshClick",
        'preferredWidth="76" preferredHeight="28" fontSize="13"') .. [[
    </HorizontalLayout>
    <HorizontalLayout spacing="6" preferredHeight="40">
      ]] .. buttonXml("tab_keep", "Archive", "onTabClick", 'color="#C8A86B"') .. [[
      ]] .. buttonXml("tab_forge", "Workshop", "onTabClick") .. [[
    </HorizontalLayout>
    <Text id="tabDescription" fontSize="14" preferredHeight="36" alignment="MiddleLeft"
          fontStyle="Italic">]] .. TAB_DESCRIPTIONS.keep .. [[</Text>
    <InputField id="search" onValueChanged="onSearch" placeholder="Search by name or designer"
                preferredHeight="40" fontSize="18"/>
    <GridLayout id="types" cellSize="128 32" spacing="4 4" preferredHeight="72">
      ]] .. table.concat(typeButtons, "\n      ") .. [[
    </GridLayout>
    <Text id="status" fontSize="15" preferredHeight="24" alignment="MiddleLeft">Loading...</Text>
    <VerticalScrollView preferredHeight="302" scrollSensitivity="30">
      <VerticalLayout id="rows" spacing="4" childForceExpandHeight="false" contentSizeFitter="vertical">
        ]] .. table.concat(rows, "\n        ") .. [[
      </VerticalLayout>
    </VerticalScrollView>
  </VerticalLayout>
</Panel>
<!-- Close button, as on the TTS Faction Board, in the margin at the top right. -->
]] .. closeButton .. [[
<!-- Asks before replacing the table's map or deck, like the RTT buttons do. -->
<Panel id="confirm" active="false" position="0 0 -22" width="]] .. PANEL_W .. [[" height="]] .. PANEL_H .. [["
       scale="0.25 0.25 1" color="#000000AA">
  <Panel width="520" height="230" color="#2B2118" outline="#C8A86B" outlineSize="3 3">
    <VerticalLayout padding="24" spacing="14" childForceExpandHeight="false">
      <Text id="confirmTitle" fontSize="26" fontStyle="Bold" preferredHeight="36"
            alignment="MiddleCenter">This will reset the map.</Text>
      <Text id="confirmText" fontSize="17" preferredHeight="48" alignment="MiddleCenter"></Text>
      <HorizontalLayout spacing="16" preferredHeight="48" childForceExpandWidth="true">
        ]] .. buttonXml("confirmCancel", "Cancel", "onConfirmCancel", 'fontSize="18"') .. [[
        ]] .. buttonXml("confirmGo", "Replace", "onConfirmGo",
          'fontSize="18" fontStyle="Bold" color="#bd2608" textColor="#FFFFFF"') .. [[
      </HorizontalLayout>
    </VerticalLayout>
  </Panel>
</Panel>
]]
end

-- setAttribute fails while the XML is still loading, so every UI update
-- waits for UI.loading to clear.
local function whenUiReady(fn)
  if not self.UI.loading then
    fn()
  else
    Wait.condition(fn, function() return not self.UI.loading end)
  end
end

local function setStatus(text)
  whenUiReady(function() self.UI.setValue("status", text) end)
end

function onLoad()
  -- The logo beside the title: icon-512 pre-rounded like the home page hero
  -- (XML UI can't round image corners). The plain static path keeps working
  -- in production, where collectstatic keeps an unhashed copy.
  self.UI.setCustomAssets({
    {name = "rdbLogo", url = BASE_URL .. "/static/images/tts/library_logo.png"},
  })
  self.UI.setXml(buildXml())
  fetchCatalog()
  checkForUpdate()
end

-- ── Self-update ───────────────────────────────────────────────────────────

-- If the server has a newer version of this script, swap it in and reload.
-- Any failure just keeps the current script.
function checkForUpdate()
  if LOADER_VERSION == "dev" then
    return    -- pasted from the repo for local testing: never overwrite it
  end
  WebRequest.get(BASE_URL .. "/api/tts/loader/version/", function(request)
    if request.is_error or request.response_code ~= 200 then
      return
    end
    local data = JSON.decode(request.text)
    local version = data and data.version
    if not version or version == LOADER_VERSION then
      return
    end
    WebRequest.get(BASE_URL .. "/api/tts/loader.lua", function(scriptRequest)
      if scriptRequest.is_error or scriptRequest.response_code ~= 200 then
        return
      end
      -- Loop guard: only apply a script that really is the advertised version
      -- (a deploy landing between the two requests just waits for next load).
      local marker = 'LOADER_VERSION = "' .. version .. '"'
      if not string.find(scriptRequest.text, marker, 1, true) then
        return
      end
      self.setLuaScript(scriptRequest.text)
      self.reload()
    end)
  end)
end

-- ── Catalog + filtering ───────────────────────────────────────────────────

function fetchCatalog()
  setStatus("Loading...")
  WebRequest.get(BASE_URL .. "/api/tts/catalog/", function(request)
    if request.is_error or request.response_code ~= 200 then
      setStatus(errorText(request))
      return
    end
    catalog = JSON.decode(request.text) or {keep = {}, forge = {}}
    catalog.keep = catalog.keep or {}
    catalog.forge = catalog.forge or {}
    whenUiReady(refreshUi)
  end)
end

function errorText(request)
  if request.response_code == 429 then
    return "Too many requests -- wait a minute and try again."
  elseif request.response_code == 404 then
    return "Not found -- it may have been unpublished. Try Refresh."
  end
  return "Couldn't reach the Root Database: " .. tostring(request.error or request.response_code)
end

local function matches(entry)
  local wanted = typeFilter[section]
  if wanted and entry.component ~= wanted then
    return false
  end
  if searchText == "" then
    return true
  end
  local designer = entry.designers or entry.designer or ""
  local haystack = string.lower((entry.title or "") .. " " .. designer)
  return string.find(haystack, searchText, 1, true) ~= nil
end

function refreshUi()
  -- Tabs and the type row
  self.UI.setValue("tabDescription", TAB_DESCRIPTIONS[section])
  self.UI.setAttribute("tab_keep", "color", section == "keep" and "#C8A86B" or "#EFE6D2")
  self.UI.setAttribute("tab_forge", "color", section == "forge" and "#C8A86B" or "#EFE6D2")
  -- Type buttons: only the types this tab actually has
  local list = section == "keep" and catalog.keep or catalog.forge
  local present = {}
  for _, entry in ipairs(list) do
    present[entry.component] = true
  end
  -- A remembered type that's gone (e.g. after Refresh) falls back to All, so a
  -- hidden filter can never leave the list empty.
  if typeFilter[section] and not present[typeFilter[section]] then
    typeFilter[section] = nil
  end
  local selected = typeFilter[section]
  self.UI.setAttribute("type_all", "color", selected == nil and "#C8A86B" or "#EFE6D2")
  for _, t in ipairs(COMPONENT_TYPES) do
    self.UI.setAttribute("type_" .. t, "active", present[t] and "true" or "false")
    self.UI.setAttribute("type_" .. t, "color", selected == t and "#C8A86B" or "#EFE6D2")
  end

  -- Matches, in the server's order (the Keep list arrives grouped by status).
  local matched, perStatus = {}, {}
  for _, entry in ipairs(list) do
    if matches(entry) then
      matched[#matched + 1] = entry
      if entry.status_label then
        perStatus[entry.status_label] = (perStatus[entry.status_label] or 0) + 1
      end
    end
  end
  local total = #matched

  -- Display slots: on the Keep tab a header ("Stable (12)") starts each status
  -- group; the Forge tab has no statuses, so entries only.
  local slots, lastStatus = {}, nil
  for _, entry in ipairs(matched) do
    if section == "keep" and entry.status_label and entry.status_label ~= lastStatus then
      slots[#slots + 1] = {header = entry.status_label .. " (" .. perStatus[entry.status_label] .. ")"}
      lastStatus = entry.status_label
    end
    slots[#slots + 1] = {entry = entry}
  end

  -- Fill the row pool. rowItems is set only for entries, so headers can't be
  -- clicked (onRowClick ignores rows without an item).
  rowItems = {}
  local shown = 0
  for i = 1, MAX_ROWS do
    local slot = slots[i]
    local id, labelId = "row" .. i, "rowLabel" .. i
    if slot == nil then
      self.UI.setAttribute(id, "active", "false")
    else
      -- Show first, style, then set the text last: setAttribute can rebuild an
      -- element and drop a value set before it.
      self.UI.setAttribute(id, "active", "true")
      local kind = slot.header and "header" or "entry"
      if rowKind[i] ~= kind then   -- restyle only on a change, so typing stays fast
        if kind == "header" then
          self.UI.setAttribute(id, "color", HEADER_COLOR)
          self.UI.setAttribute(id, "interactable", "false")
          self.UI.setAttribute(labelId, "color", HEADER_TEXT_COLOR)
          self.UI.setAttribute(labelId, "fontStyle", "Bold")
          self.UI.setAttribute(labelId, "alignment", "MiddleCenter")
        else
          self.UI.setAttribute(id, "color", ROW_COLOR)
          self.UI.setAttribute(id, "interactable", "true")
          self.UI.setAttribute(labelId, "color", ROW_TEXT_COLOR)
          self.UI.setAttribute(labelId, "fontStyle", "Normal")
          self.UI.setAttribute(labelId, "alignment", "MiddleLeft")
        end
        rowKind[i] = kind
      end
      if slot.header then
        self.UI.setValue(labelId, slot.header)
      else
        local entry = slot.entry
        rowItems[i] = entry
        shown = shown + 1
        local who = entry.designers or entry.designer or ""
        local label = entry.title
        if entry.component and entry.component ~= "" then
          label = label .. "  [" .. entry.component .. "]"
        end
        if who ~= "" then
          label = label .. "  -  " .. who
        end
        self.UI.setValue(labelId, label)
      end
    end
  end

  -- Counts are entries only, never headers.
  if total == 0 then
    setStatus("Nothing matches.")
  elseif shown < total then
    setStatus(string.format("Showing %d of %d -- refine your search.", shown, total))
  else
    setStatus(string.format("%d result%s", total, total == 1 and "" or "s"))
  end
end

-- ── UI events ─────────────────────────────────────────────────────────────

function onSearch(player, value, id)
  searchText = string.lower(value or "")
  whenUiReady(refreshUi)
end

function onTabClick(player, mouseButton, id)
  section = (id == "tab_forge") and "forge" or "keep"
  whenUiReady(refreshUi)
end

function onTypeClick(player, mouseButton, id)
  local chosen = string.sub(id, 6)          -- strip "type_"
  if chosen == "all" then
    typeFilter[section] = nil
  else
    typeFilter[section] = chosen
  end
  whenUiReady(refreshUi)
end

function onRefreshClick(player, mouseButton, id)
  fetchCatalog()
end

-- A map or deck the table already has, which loading this entry would replace
-- (only on the RTT table: elsewhere new items spawn beside the old ones).
-- Mirrors the coordinator's own checks (rttMapWouldChange / rttDeckOnTable).
local function wouldReplace(entry)
  if entry.component ~= "Map" and entry.component ~= "Deck" then
    return nil
  end
  if not getObjectFromGUID(RTT_COORD_GUID) then
    return nil
  end
  local tag = (entry.component == "Map") and "Map Object" or "Deck Object"
  local found = false
  pcall(function() found = #(getObjectsWithTag(tag) or {}) > 0 end)
  return found and string.lower(entry.component) or nil
end

pendingConfirm = nil   -- {entry, player} waiting on the confirm panel

function onRowClick(player, mouseButton, id)
  local entry = rowItems[tonumber(string.sub(id, 4))]   -- strip "row"
  if not entry then
    return
  end
  if busy then
    return    -- still loading the last selection; ignore extra clicks
  end
  local what = wouldReplace(entry)
  if what then
    pendingConfirm = {entry = entry, player = player}
    whenUiReady(function()
      self.UI.setValue("confirmTitle", "This will reset the " .. what .. ".")
      self.UI.setValue("confirmText", "The table's current " .. what .. " and its pieces will be replaced by " ..
        (entry.title or "this " .. what) .. ".")
      self.UI.setAttribute("confirm", "active", "true")
    end)
    return
  end
  loadEntry(entry, player)
end

function onConfirmGo(player, mouseButton, id)
  local pending = pendingConfirm
  pendingConfirm = nil
  self.UI.setAttribute("confirm", "active", "false")
  if pending and not busy then
    loadEntry(pending.entry, pending.player)
  end
end

function onConfirmCancel(player, mouseButton, id)
  pendingConfirm = nil
  self.UI.setAttribute("confirm", "active", "false")
end

function loadEntry(entry, player)
  -- Archive entries have a slug, Workshop drafts an id (not the open tab: it
  -- may have changed while the confirm panel was up).
  local url
  if entry.slug then
    url = BASE_URL .. "/api/tts/keep/" .. entry.slug .. "/"
  else
    url = BASE_URL .. "/api/tts/forge/" .. tostring(entry.id) .. "/"
  end
  busy = true
  WebRequest.get(url, function(request)
    busy = false
    if request.is_error or request.response_code ~= 200 then
      broadcastToColor(errorText(request), player.color, {1, 0.4, 0.4})
      return
    end
    local data = JSON.decode(request.text)
    withVPPanel(data, function(panelTemplate)
      spawnAll(data, player, panelTemplate)
    end)
  end)
end

-- ── Spawning ──────────────────────────────────────────────────────────────

-- Remove this object the way the TTS Faction Board does: clear the XML UI
-- first (deleting an object that still has UI attached can leave TTS unable to
-- hand out player colours until the server restarts), then destruct a frame later.
function deleteThis()
  pcall(function() self.UI.setXml("") end)
  Wait.frames(function() self.destruct() end, 1)
end

-- ── RTT VP Panel ──────────────────────────────────────────────────────────

-- The same check the VP Panel itself makes before relaying a click.
local function rttCoordinatorPresent()
  local coordinator = getObjectFromGUID(RTT_COORD_GUID)
  if coordinator == nil then
    return false
  end
  local ok = false
  pcall(function() ok = (coordinator.getVar("RTT_VP_API") == true) end)
  return ok
end

-- Calls done(template) with the VP Panel JSON when this item has VP markers and
-- the coordinator is present, else done(nil). A failed fetch still spawns the
-- item, just without panels.
function withVPPanel(data, done)
  local rows = (data and data.vp_rows) or {}
  -- With the external-faction API the coordinator spawns its own panel.
  if #rows == 0 or not rttCoordinatorPresent() or externalAPI() ~= nil then
    done(nil)
    return
  end
  if vpPanelTemplate ~= nil then
    done(vpPanelTemplate)
    return
  end
  WebRequest.get(BASE_URL .. "/api/tts/vp-panel/", function(request)
    if not request.is_error and request.response_code == 200 then
      vpPanelTemplate = request.text
    end
    done(vpPanelTemplate)
  end)
end

-- The coordinator, when it offers the external-faction API (see
-- tts/RTT_EXTERNAL_API.md): claim before spawning, report after, and
-- it then handles seat, Box Score record, hands, VP token and VP panel itself.
function externalAPI()
  local coordinator = getObjectFromGUID(RTT_COORD_GUID)
  if coordinator == nil then
    return nil
  end
  local version = nil
  pcall(function() version = coordinator.getVar("RTT_EXTERNAL_API") end)
  if type(version) == "number" and version >= 1 then
    return coordinator
  end
  return nil
end

-- Without that API: hand each faction's VP token to the coordinator's own
-- placement queue (rttPlaceVP puts it on the score track's 0 column, like a
-- built-in pick). Its retries run on the coordinator's timers, so they finish
-- after this object has deleted itself. Returns true when queued.
local function queueVPPlacement(rows)
  local coordinator = getObjectFromGUID(RTT_COORD_GUID)
  if coordinator == nil then
    return false
  end
  local ok = pcall(function()
    local pending = coordinator.getTable("RTT_VP_PENDING") or {}
    local placed = coordinator.getVar("RTT_VP_PLACED") or 0
    for _, vp in ipairs(rows) do
      placed = placed + 1
      pending[vp.row] = placed          -- slot n: next spot in the 0 column
    end
    coordinator.setVar("RTT_VP_PLACED", placed)
    coordinator.setTable("RTT_VP_PENDING", pending)
    coordinator.call("rttPlaceUnplacedVPs")
  end)
  return ok
end

local function releaseClaims(coordinator, claimed)
  for _, faction in ipairs(claimed) do
    pcall(function() coordinator.call("rttReleaseExternalFaction", { faction = faction }) end)
  end
end

local function replacePlain(text, find, replacement)
  local i, j = string.find(text, find, 1, true)
  if not i then
    return text
  end
  return text:sub(1, i - 1) .. replacement .. text:sub(j + 1)
end

local function jsonString(value)
  local escaped = value:gsub("\\", "\\\\"):gsub('"', '\\"')
  return '"' .. escaped .. '"'
end

-- ── Maps, decks, house rules (RTT table) ────────────────────────────────────

-- A map replaces the table's map at its centre. With the coordinator's API it
-- places the map itself (clearing, tags, shuffle, extras, layout); otherwise we
-- clear and place it the same way. Layout data: tts/BOARD_LAYOUT.md.
function placeMap(data, player)
  local coordinator = getObjectFromGUID(RTT_COORD_GUID)
  local objects = data.objects
  local boardIndex = data.map_board or 1
  local board = objects[boardIndex] or objects[1]
  local api = externalAPI()
  if api ~= nil then
    local list = {}
    for i, o in ipairs(objects) do
      list[i] = {json = o.json, x = o.x - board.x, y = o.y - board.y, z = o.z - board.z,
                 board = (i == boardIndex)}
    end
    local answer = nil
    pcall(function()
      answer = api.call("rttPlaceExternalMap", {name = data.title, slug = data.slug,
        shuffle = data.shuffle, layout = data.layout, objects = list})
    end)
    if type(answer) == "table" and answer.ok then
      deleteThis()
    else
      broadcastToColor((type(answer) == "table" and answer.reason) or "The table couldn't place that map.",
        player.color, {1, 0.4, 0.4})
    end
    return
  end

  pcall(function() coordinator.call("removeMapItems") end)
  for _, o in ipairs(objects) do
    spawnObjectJSON({
      json = o.json,
      position = {MAP_CENTER.x + o.x - board.x, MAP_CENTER.y + o.y - board.y, MAP_CENTER.z + o.z - board.z},
    })
  end
  busy = true   -- ignore clicks while the map settles; this object is about to go
  Wait.time(function()
    if data.shuffle then pcall(function() coordinator.call("shuffleMaps") end) end
    pcall(function() coordinator.call("rttPlaceUnplacedVPs") end)
    pcall(function() coordinator.call("rttTellSheet") end)
    deleteThis()
  end, MAP_SETTLE_SECONDS)
end

-- A deck replaces the table's deck on the deck holder's draw slot. Returns
-- false when it can't (no holder, or the coordinator refused), so the caller
-- spawns it where the library sits instead.
function placeDeck(data)
  -- The server marks the deck with the most cards "main"; the rest are
  -- accessories, kept apart so they never merge into it.
  local main, accessories = {}, {}
  for _, o in ipairs(data.objects) do
    if o.deck_role == "accessory" then
      table.insert(accessories, o)
    else
      table.insert(main, o)
    end
  end

  local api = externalAPI()
  if api ~= nil then
    local mainJson, accessoryJson = {}, {}
    for i, o in ipairs(main) do mainJson[i] = o.json end
    for i, o in ipairs(accessories) do accessoryJson[i] = o.json end
    local answer = nil
    pcall(function()
      answer = api.call("rttPlaceExternalDeck", {
        name = data.title, slug = data.slug, dominance = data.dominance == true,
        objects = mainJson, accessories = accessoryJson,
      })
    end)
    if type(answer) == "table" and answer.ok then
      deleteThis()
      return true
    end
    return false
  end

  local holder = getObjectFromGUID(DECK_HOLDER_GUID)
  if holder == nil then
    return false
  end
  -- Clear the old deck's decks and cards ("Deck Object"), but keep the Refill
  -- Card and Dominance Track Card tokens: the coordinator's removeDeckItems
  -- would delete those too, and only its own deck buttons respawn them.
  for _, o in ipairs(getObjectsWithTag("Deck Object")) do
    if o.type == "Deck" or o.type == "Card" then
      o.destruct()
    end
  end
  local ry = holder.getRotation().y + DECK_ROT_Y
  local slot = holder.positionToWorld(DECK_DRAW_SLOT)
  for i, o in ipairs(main) do
    spawnObjectJSON({
      json = o.json,
      position = {slot.x, slot.y + 0.5 + i * 0.3, slot.z},
      rotation = {0, ry, 180},   -- face down
    })
  end
  for i, o in ipairs(accessories) do
    local at = holder.positionToWorld({
      DECK_ACCESSORY_SLOT[1] + (i - 1) * DECK_ACCESSORY_STEP[1],
      DECK_ACCESSORY_SLOT[2] + (i - 1) * DECK_ACCESSORY_STEP[2],
      DECK_ACCESSORY_SLOT[3] + (i - 1) * DECK_ACCESSORY_STEP[3],
    })
    spawnObjectJSON({
      json = o.json,
      position = {at.x, at.y + 0.5, at.z},
      rotation = {0, ry, 180},
    })
  end
  deleteThis()
  return true
end

-- House rules always reach the Box Score (through the coordinator's house-rule
-- list); landmarks and hirelings only with the coordinator's API.
function recordAsset(data)
  if data.slug == nil or data.slug == "" then
    return
  end
  local api = externalAPI()
  if data.component == "Tweak" then
    if api ~= nil then
      pcall(function() api.call("rttAddHouseRule", {slug = data.slug}) end)
      return
    end
    local coordinator = getObjectFromGUID(RTT_COORD_GUID)
    pcall(function()
      local rules = coordinator.getTable("RTT_HOUSERULES") or {}
      for _, r in ipairs(rules) do
        if r == data.slug then return end
      end
      rules[#rules + 1] = data.slug
      coordinator.setTable("RTT_HOUSERULES", rules)
      coordinator.call("rttPublishSeats")
    end)
  elseif api ~= nil and (data.component == "Landmark" or data.component == "Hireling") then
    pcall(function()
      api.call("rttExternalAssetAdded", {kind = string.lower(data.component), slug = data.slug})
    end)
  end
end

-- The selected item replaces the library: spawn it where this object sits,
-- then delete this object. Nothing is deleted if there was nothing to spawn.
function spawnAll(data, player, panelTemplate)
  local objects = (data and data.objects) or {}
  if #objects == 0 then
    broadcastToColor("Nothing to spawn for " .. ((data and data.title) or "that item") .. ".",
      player.color, {1, 0.4, 0.4})
    return
  end
  local origin = self.getPosition()
  local base = {
    x = origin.x + SPAWN_OFFSET.x,
    y = origin.y + SPAWN_OFFSET.y,
    z = origin.z + SPAWN_OFFSET.z,
  }
  local vpRows = data.vp_rows or {}

  -- Maps and decks replace the table's current ones, like the coordinator's buttons.
  if rttCoordinatorPresent() then
    if data.component == "Map" then
      placeMap(data, player)
      return
    end
    if data.component == "Deck" and placeDeck(data) then
      return
    end
  end

  -- With the coordinator's external-faction API, claim each faction first so
  -- it can refuse spectators, duplicates and taken seats before anything spawns.
  local api = (#vpRows > 0) and externalAPI() or nil
  local claimed = {}
  if api ~= nil then
    for _, vp in ipairs(vpRows) do
      local answer = nil
      pcall(function()
        answer = api.call("rttClaimExternalFaction",
          { faction = vp.row, row = vp.row, color = player.color, x = base.x, z = base.z })
      end)
      if type(answer) ~= "table" or not answer.ok then
        releaseClaims(api, claimed)
        broadcastToColor((type(answer) == "table" and answer.reason) or "The table refused that faction.",
          player.color, {1, 0.4, 0.4})
        return
      end
      claimed[#claimed + 1] = vp.row
    end
  end

  local spawned = {}
  local spawnedOk = pcall(function()
    for i, obj in ipairs(objects) do
      -- Keep each object's layout offset; stagger height so decks settle on boards.
      spawned[i] = spawnObjectJSON({
        json = obj.json,
        position = {base.x + obj.x, base.y + obj.y + 1 + i * 0.2, base.z + obj.z},
      })
    end
  end)
  if not spawnedOk then
    if api ~= nil then releaseClaims(api, claimed) end
    broadcastToColor("Something went wrong spawning " .. (data.title or "that item") .. ".",
      player.color, {1, 0.4, 0.4})
    return
  end

  if api ~= nil then
    -- The coordinator takes it from here: seat, Box Score record, hands, VP
    -- token on the track and its own VP panel.
    local ry = self.getRotation().y
    for _, vp in ipairs(vpRows) do
      local boardGuid = nil
      if vp.board ~= nil and spawned[vp.board] ~= nil then
        pcall(function() boardGuid = spawned[vp.board].getGUID() end)
      end
      pcall(function()
        api.call("rttExternalFactionPlaced", {
          faction = vp.row, row = vp.row, color = player.color,
          x = base.x, z = base.z, ry = ry, board = boardGuid,
          vpPanel = {x = base.x + vp.x + VP_PANEL_OFFSET.x, y = base.y + 1, z = base.z + vp.z + VP_PANEL_OFFSET.z},
        })
      end)
    end
  else
    -- One RTT VP Panel per faction, set to that faction's row (its VP marker's
    -- name minus " VP"), placed beside its marker.
    if panelTemplate ~= nil then
      for _, vp in ipairs(vpRows) do
        spawnObjectJSON({
          json = replacePlain(panelTemplate, '"__VP_ROW__"', jsonString(vp.row)),
          position = {base.x + vp.x + VP_PANEL_OFFSET.x, base.y + 1, base.z + vp.z + VP_PANEL_OFFSET.z},
        })
      end
    end
    if #vpRows > 0 and rttCoordinatorPresent() then
      queueVPPlacement(vpRows)
    end
  end

  -- House rules, landmarks and hirelings: record them for the Box Score.
  if rttCoordinatorPresent() then
    recordAsset(data)
  end

  -- No chat message on success (only errors are reported); the spawned item is
  -- the confirmation.
  deleteThis()
end
