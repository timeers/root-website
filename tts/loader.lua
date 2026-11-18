--[[
  Root Database Library for Tabletop Simulator.

  Lists everything the site has published for TTS:
    * The Keep  -- active posts whose Post.tts_json is set
    * The Forge -- Forged drafts an admin approved for the TTS Library
  Search, pick one, and its objects spawn beside this object.

  This file (tts/loader.lua) is the single source. The site serves it from
    GET /api/tts/loader.lua           -> this script, LOADER_VERSION/BASE_URL filled in
    GET /api/tts/loader/version/      -> { version }
    GET /api/tts/loader/object.json   -> a ready-made object carrying this script
  and an object built that way updates itself on load when the deployed file
  changes. A copy pasted straight from the repo keeps LOADER_VERSION = "dev"
  and never updates itself.

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

MAX_ROWS = 50             -- result rows built once; filtering only toggles them
SPAWN_OFFSET = {x = 30, y = 2, z = 0}   -- where spawned objects land, relative to this object

COMPONENT_TYPES = {"Faction", "Clockwork", "Vagabond", "Map", "Deck", "Hireling", "Landmark", "Tweak"}

catalog = {keep = {}, forge = {}}
section = "keep"          -- "keep" or "forge"
typeFilter = {}           -- per tab: typeFilter[section] = component, or nil for all
searchText = ""
rowItems = {}             -- row index -> catalog entry currently shown there
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
    table.insert(rows, buttonXml("row" .. i, "", "onRowClick",
      'active="false" preferredHeight="34" fontSize="16" textAlignment="MiddleLeft"'))
  end

  -- Sized for a Custom_Tile at scale 15.5 (same as the TTS Faction Board): the
  -- tile face holds about 240 x 180 UI units, with -20 on z lifting the UI above
  -- the surface and no rotation needed. The panel is laid out at 4x and scaled
  -- down by 0.25 (928 x 688 -> 232 x 172) to sit just inside the edges.
  return [[
<Defaults>
  <Button fontSize="16" color="#EFE6D2" textColor="#2B2118"/>
  <Text color="#EFE6D2"/>
</Defaults>
<Panel position="0 0 -20" width="928" height="688" scale="0.25 0.25 1" color="#2B2118EE">
  <VerticalLayout padding="12" spacing="8" childForceExpandHeight="false">
    <Text fontSize="26" fontStyle="Bold" preferredHeight="36">Root Database Library</Text>
    <HorizontalLayout spacing="6" preferredHeight="40">
      ]] .. buttonXml("tab_keep", "The Keep", "onTabClick", 'color="#C8A86B"') .. [[
      ]] .. buttonXml("tab_forge", "The Forge", "onTabClick") .. [[
      ]] .. buttonXml("refresh", "Refresh", "onRefreshClick", 'preferredWidth="110"') .. [[
    </HorizontalLayout>
    <InputField id="search" onValueChanged="onSearch" placeholder="Search by name or designer"
                preferredHeight="40" fontSize="18"/>
    <GridLayout id="types" cellSize="128 32" spacing="4 4" preferredHeight="72">
      ]] .. table.concat(typeButtons, "\n      ") .. [[
    </GridLayout>
    <Text id="status" fontSize="15" preferredHeight="24" alignment="MiddleLeft">Loading...</Text>
    <VerticalScrollView preferredHeight="410" scrollSensitivity="30">
      <VerticalLayout id="rows" spacing="4" childForceExpandHeight="false" contentSizeFitter="vertical">
        ]] .. table.concat(rows, "\n        ") .. [[
      </VerticalLayout>
    </VerticalScrollView>
  </VerticalLayout>
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
      print("Root Database Library updated to " .. version)
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

  -- Rows: fill the first N matches, hide the rest
  rowItems = {}
  local total = 0
  for _, entry in ipairs(list) do
    if matches(entry) then
      total = total + 1
      if total <= MAX_ROWS then
        rowItems[total] = entry
      end
    end
  end
  for i = 1, MAX_ROWS do
    local entry = rowItems[i]
    if entry then
      local who = entry.designers or entry.designer or ""
      local label = entry.title
      if entry.component and entry.component ~= "" then
        label = label .. "  [" .. entry.component .. "]"
      end
      if who ~= "" then
        label = label .. "  -  " .. who
      end
      self.UI.setValue("row" .. i, label)
      self.UI.setAttribute("row" .. i, "active", "true")
    else
      self.UI.setAttribute("row" .. i, "active", "false")
    end
  end

  if total == 0 then
    setStatus("Nothing matches.")
  elseif total > MAX_ROWS then
    setStatus(string.format("Showing %d of %d -- refine your search.", MAX_ROWS, total))
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

function onRowClick(player, mouseButton, id)
  local entry = rowItems[tonumber(string.sub(id, 4))]   -- strip "row"
  if not entry then
    return
  end
  if busy then
    broadcastToColor("Still loading the last selection...", player.color, {1, 0.8, 0.4})
    return
  end
  local url
  if section == "keep" then
    url = BASE_URL .. "/api/tts/keep/" .. entry.slug .. "/"
  else
    url = BASE_URL .. "/api/tts/forge/" .. tostring(entry.id) .. "/"
  end
  busy = true
  broadcastToColor("Loading " .. entry.title .. "...", player.color, {0.8, 0.8, 0.8})
  WebRequest.get(url, function(request)
    busy = false
    if request.is_error or request.response_code ~= 200 then
      broadcastToColor(errorText(request), player.color, {1, 0.4, 0.4})
      return
    end
    local data = JSON.decode(request.text)
    spawnAll(data, player)
  end)
end

-- ── Spawning ──────────────────────────────────────────────────────────────

function spawnAll(data, player)
  local origin = self.getPosition()
  local base = {
    x = origin.x + SPAWN_OFFSET.x,
    y = origin.y + SPAWN_OFFSET.y,
    z = origin.z + SPAWN_OFFSET.z,
  }
  local count = 0
  for i, obj in ipairs(data.objects or {}) do
    -- Keep each object's layout offset; stagger height so decks settle on boards.
    spawnObjectJSON({
      json = obj.json,
      position = {base.x + obj.x, base.y + obj.y + 1 + i * 0.2, base.z + obj.z},
    })
    count = count + 1
  end
  broadcastToColor(
    string.format("Loaded %s (%d object%s).", data.title or "item", count, count == 1 and "" or "s"),
    player.color, {0.6, 1, 0.6})
end
