// Browser-detected timezone for the website's timezone pickers (/availability and
// the survey Timezone question).
//
// The pickers list a curated set of zones (the same one the Discord bot uses),
// which can't cover everywhere -- Darwin, Kabul, Nuuk and friends have no entry.
// The browser knows the player's exact IANA zone, so when it isn't already an
// option it is added at the top as "Detected from your browser". The server
// accepts any valid zone (TimezoneChoiceField), not just the curated ones.
(function () {
  'use strict';

  // Legacy spellings browsers still report -> the canonical zone the curated list
  // uses. Mirrors _TIMEZONE_ALIASES in the_databot/services/time_parsing.py; the
  // server normalises too, this just stops a duplicate "detected" entry.
  var ALIASES = {
    'Asia/Calcutta': 'Asia/Kolkata',
    'Asia/Saigon': 'Asia/Ho_Chi_Minh',
    'Asia/Katmandu': 'Asia/Kathmandu',
    'Asia/Rangoon': 'Asia/Yangon',
    'Asia/Dacca': 'Asia/Dhaka',
    'Europe/Kiev': 'Europe/Kyiv',
    'America/Buenos_Aires': 'America/Argentina/Buenos_Aires',
    'America/Godthab': 'America/Nuuk',
    'Pacific/Enderbury': 'Pacific/Kanton',
    'Atlantic/Faeroe': 'Atlantic/Faroe',
    'Etc/UTC': 'UTC',
    'Etc/GMT': 'UTC',
    'Etc/Universal': 'UTC',
    'Etc/Zulu': 'UTC',
    'GMT': 'UTC',
    'UCT': 'UTC'
  };

  function detectedTimezone() {
    try {
      var zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
      if (!zone || zone === 'Etc/Unknown') { return null; }
      return ALIASES[zone] || zone;
    } catch (err) { return null; }
  }

  // "UTC+9:30" / "UTC-4" / "UTC", matching format_utc_offset server-side.
  function utcOffsetLabel(zone) {
    try {
      var parts = new Intl.DateTimeFormat('en-US', {
        timeZone: zone, timeZoneName: 'shortOffset'
      }).formatToParts(new Date());
      for (var i = 0; i < parts.length; i++) {
        if (parts[i].type === 'timeZoneName') {
          return parts[i].value.replace(/^GMT/, 'UTC');
        }
      }
    } catch (err) { /* shortOffset unsupported -- label without an offset */ }
    return '';
  }

  // Adds the detected zone to `select` when it isn't already an option, and
  // when opts.preselect is true selects it. Returns the zone it selected, or
  // null -- callers with side effects (the /availability grid's drawn zone)
  // key them off the return value.
  function enhanceTimezoneSelect(select, opts) {
    if (!select) { return null; }
    var zone = detectedTimezone();
    if (!zone) { return null; }

    var exists = Array.prototype.some.call(select.options, function (o) {
      return o.value === zone;
    });
    if (!exists) {
      var group = document.createElement('optgroup');
      group.label = select.dataset.detectedLabel || 'Detected from your browser';
      var option = document.createElement('option');
      var offset = utcOffsetLabel(zone);
      option.value = zone;
      option.textContent = offset && offset !== 'UTC' ? zone + ' — ' + offset : zone;
      group.appendChild(option);
      // Above the region groups, but below a blank "---------" placeholder.
      var first = select.firstElementChild;
      if (first && first.tagName === 'OPTION' && first.value === '') {
        select.insertBefore(group, first.nextSibling);
      } else {
        select.insertBefore(group, first);
      }
    }

    if (opts && opts.preselect) {
      select.value = zone;
      return zone;
    }
    return null;
  }

  // ---- Reference-week offsets ------------------------------------------------
  // The General Availability grid is stored in UTC against a fixed reference
  // Monday (services.availability._REFERENCE_MONDAY, 2024-01-01) and drawn
  // with the zone's offset AT THAT DATE -- not today's -- so these measure
  // there too. No zone changes offset during that week (pinned by a test), so
  // one offset converts the whole week.
  var REFERENCE_MONDAY_UTC = Date.UTC(2024, 0, 1);

  // Full UTC offset in minutes at the reference Monday ("GMT+5:30" -> 330), or
  // 0 for an unknown zone, matching the server's fall back to UTC.
  function zoneOffsetMinutes(zone) {
    try {
      var parts = new Intl.DateTimeFormat('en-US', {
        timeZone: zone, timeZoneName: 'shortOffset'
      }).formatToParts(new Date(REFERENCE_MONDAY_UTC));
      for (var i = 0; i < parts.length; i++) {
        if (parts[i].type !== 'timeZoneName') { continue; }
        var m = /^GMT(?:([+-])(\d{1,2})(?::(\d{2}))?)?$/.exec(parts[i].value);
        if (!m || !m[1]) { return 0; }
        var minutes = parseInt(m[2], 10) * 60 + (m[3] ? parseInt(m[3], 10) : 0);
        return m[1] === '-' ? -minutes : minutes;
      }
    } catch (err) { /* unknown zone or no shortOffset support */ }
    return 0;
  }

  // The minute past the hour the zone's rows start at: 0, 30 or 45. Mirrors
  // services.availability.zone_minute_offset (St. John's -210 -> 30).
  function zoneMinuteOffset(zone) {
    var off = zoneOffsetMinutes(zone);
    return ((off % 60) + 60) % 60;
  }

  // UTC hour-of-week ints -> the zone's local hour-of-week ints, exactly as
  // services.availability.utc_to_local_hours does (a test checks the formula
  // against it for every zone). JS % keeps the dividend's sign, hence the
  // double modulo.
  function utcToLocalHours(hours, zone) {
    var off = zoneOffsetMinutes(zone);
    var seen = {};
    hours.forEach(function (h) {
      var local = ((Math.floor((h * 60 + off) / 60) % 168) + 168) % 168;
      seen[local] = true;
    });
    return Object.keys(seen).map(Number).sort(function (a, b) { return a - b; });
  }

  window.enhanceTimezoneSelect = enhanceTimezoneSelect;
  window.detectedTimezone = detectedTimezone;
  window.zoneOffsetMinutes = zoneOffsetMinutes;
  window.zoneMinuteOffset = zoneMinuteOffset;
  window.utcToLocalHours = utcToLocalHours;
})();
