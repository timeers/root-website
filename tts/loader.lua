--[[
  Root Database loader for Tabletop Simulator.

  Paste this onto any object (a tile or a token works well). It lists
  everything the site has published for TTS:
    * The Keep  -- posts whose Post.tts_json an admin has set
    * The Forge -- Forged Factions whose designer turned on "Publish to TTS"
  Search, pick one, and its objects spawn beside this object.

  Endpoints (the_keep/api_views.py):
    GET /api/tts/catalog/           -> { keep = [...], forge = [...] }
    GET /api/tts/keep/<slug>/       -> { title, objects = [{x, y, z, json}] }
    GET /api/tts/forge/<id>/        -> same shape
  Each object arrives pre-encoded ("json") with its position pulled out, so
  this script never runs MoonSharp's (slow) JSON on the big object blobs.
]]

BASE_URL = "https://www.therootdatabase.com"

MAX_ROWS = 50             -- result rows built once; filtering only toggles them
SPAWN_OFFSET = {x = 30, y = 2, z = 0}   -- where spawned objects land, relative to this object

COMPONENT_TYPES = {"Faction", "Clockwork", "Vagabond", "Map", "Deck", "Hireling", "Landmark", "Tweak"}

catalog = {keep = {}, forge = {}}
section = "keep"          -- "keep" or "forge"
typeFilter = nil          -- nil = all component types
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

  -- position/rotation put the panel on top of a flat object; adjust for yours.
  return [[
<Defaults>
  <Button fontSize="16" color="#EFE6D2" textColor="#2B2118"/>
  <Text color="#EFE6D2"/>
</Defaults>
<Panel position="0 0 -20" rotation="180 180 0" width="560" height="760" scale="0.25 0.25 1" color="#2B2118EE">
  <VerticalLayout padding="12" spacing="8" childForceExpandHeight="false">
    <Text fontSize="26" fontStyle="Bold" preferredHeight="36">Root Database Loader</Text>
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
    <VerticalScrollView preferredHeight="480" scrollSensitivity="30">
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
  if section == "keep" and typeFilter and entry.component ~= typeFilter then
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
  self.UI.setAttribute("types", "active", section == "keep" and "true" or "false")
  local present = {}
  for _, entry in ipairs(catalog.keep) do
    present[entry.component] = true
  end
  self.UI.setAttribute("type_all", "color", typeFilter == nil and "#C8A86B" or "#EFE6D2")
  for _, t in ipairs(COMPONENT_TYPES) do
    self.UI.setAttribute("type_" .. t, "active", present[t] and "true" or "false")
    self.UI.setAttribute("type_" .. t, "color", typeFilter == t and "#C8A86B" or "#EFE6D2")
  end

  -- Rows: fill the first N matches, hide the rest
  local list = section == "keep" and catalog.keep or catalog.forge
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
      if section == "keep" and entry.component ~= "" then
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
  typeFilter = (chosen == "all") and nil or chosen
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
