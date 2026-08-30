/*
 * Forum-tag <select>s: build their options client-side from a page-level JSON map of
 * forum→tags (embedded by the view via json_script — see edit_guild.html). No network
 * round-trip.
 *
 * Serves TWO forms, which are identical in behaviour but have their own container, blob
 * and channel/tag controls (see SCOPES): the LFG-role form and the series-channels form.
 * A tag-required forum rejects every post without applied_tags, so both forms must be
 * able to mark the tag required — sharing this logic keeps that rule in one place.
 *
 * The map is the single source of truth for the tag choices. The server seeds the tag
 * <select> only with the saved option (so an edit form is correct with no flash before
 * this runs); we rebuild the full option list here, preserving the current pick.
 *
 * Runs in three places so every form is always correct:
 *   - on initial load (the add form + any inline edit forms already present),
 *   - after each HTMX swap (edit forms / the reset add form swapped in later),
 *   - on every forum-channel change/input.
 * Event handlers are delegated on `document`, so newly-swapped forms need no rebinding.
 * Each rebuild scopes itself to its enclosing container, so multiple forms open at once
 * stay independent.
 */
(function () {
  // Each scope: the container that bounds one form, the JSON blob holding its forum→tag
  // map, and the channel select whose change triggers a rebuild. The tag select and hint
  // are looked up inside the container, so they need no per-scope selector.
  var SCOPES = [
    {
      fields: '.lfg-role-fields',
      blob: 'lfg-forum-tags',
      channel: '.js-forum-channel-select'
    },
    {
      fields: '.tournament-channel-fields',
      blob: 'tournament-forum-tags',
      channel: '.js-game-threads-select'
    }
  ];

  function scopeFor(el) {
    for (var i = 0; i < SCOPES.length; i++) {
      if (el.closest(SCOPES[i].fields)) return SCOPES[i];
    }
    return null;
  }

  function forumMap(blobId) {
    var el = document.getElementById(blobId);
    if (!el) return {};
    try { return JSON.parse(el.textContent) || {}; } catch (e) { return {}; }
  }

  function makeOption(value, text, selected, disabled) {
    var o = document.createElement('option');
    o.value = value;
    o.textContent = text;
    o.selected = !!selected;
    o.disabled = !!disabled;
    return o;
  }

  function rebuild(channelEl) {
    var scope = scopeFor(channelEl);
    if (!scope) return;
    var fields = channelEl.closest(scope.fields);
    if (!fields) return;
    var select = fields.querySelector('.js-forum-tag-select');
    var hint = fields.querySelector('.js-forum-tag-hint');
    if (!select) return;

    var map = forumMap(scope.blob);
    var channelId = (channelEl.value || '').trim();
    var entry = map[channelId];
    var prev = select.value; // keep the current/saved pick only if it's in this forum

    select.innerHTML = '';

    if (entry && entry.tags && entry.tags.length) {
      // A pick that isn't one of this forum's tags is cleared (no "not in forum" fallback).
      var found = entry.tags.some(function (t) { return String(t.id) === String(prev); });
      if (entry.requires_tag) {
        // Forum requires a tag: no "— No tag —" option. When the current pick is invalid,
        // show a disabled placeholder selected so nothing valid is defaulted and `required`
        // forces the user to choose.
        if (!found) select.appendChild(makeOption('', '— Choose a tag —', true, true));
      } else {
        select.appendChild(makeOption('', '— No tag —', !found, false));
      }
      entry.tags.forEach(function (t) {
        var keep = String(t.id) === String(prev);
        select.appendChild(makeOption(t.id, t.name, keep, false));
      });
      select.required = !!entry.requires_tag;
      if (hint) hint.classList.toggle('d-none', !entry.requires_tag);
    } else {
      var label = channelId ? '— No tags —' : '— Choose a forum first —';
      select.appendChild(makeOption('', label, true, true));
      select.required = false;
      if (hint) hint.classList.add('d-none');
    }
  }

  // Every scope's channel selector, as one selector list.
  var CHANNEL_SELECTOR = SCOPES.map(function (s) {
    return s.fields + ' ' + s.channel;
  }).join(', ');
  // Same, for matching an event target directly (no container prefix — scopeFor()
  // establishes which form the element belongs to).
  var TARGET_SELECTOR = SCOPES.map(function (s) { return s.channel; }).join(', ');
  // The manual-entry fallback only: `input` fires while typing an id when Discord's
  // channel list couldn't be fetched. Restricted to <input> so it never double-fires
  // alongside `change` on a <select>.
  var INPUT_TARGET_SELECTOR = SCOPES.map(function (s) {
    return 'input' + s.channel;
  }).join(', ');

  // Rebuild the tag select for every form under `root`.
  function syncAll(root) {
    (root || document).querySelectorAll(CHANNEL_SELECTOR).forEach(rebuild);
  }

  // A forum <select> change, or typing in the manual-fallback <input>.
  document.addEventListener('change', function (e) {
    var el = e.target.closest(TARGET_SELECTOR);
    if (el) rebuild(el);
  });
  document.addEventListener('input', function (e) {
    var el = e.target.closest(INPUT_TARGET_SELECTOR);
    if (el) rebuild(el);
  });

  // Initial load, and after every HTMX swap (scoped to the swapped-in subtree; OOB
  // row swaps have no forum select, so this is a no-op there).
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { syncAll(document); });
  } else {
    syncAll(document); // DOM already parsed (script loaded at end of body)
  }
  document.body.addEventListener('htmx:afterSwap', function (e) { syncAll(e.target); });
})();
