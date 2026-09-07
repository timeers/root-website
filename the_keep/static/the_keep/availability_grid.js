/* Selection behaviour for a 7x24 weekly availability grid.
 *
 * Mouse and pen DRAG to paint a range; touch taps one cell at a time. Touch is
 * deliberately excluded from painting: the grid is wider than a phone viewport
 * and scrolls inside .availability-scroll, and a paint gesture has to suppress
 * the browser's native pan to work, which left the right-hand days unreachable.
 * See the pointerdown handler.
 *
 * Shared by the /availability settings page and WEEKLY_AVAILABILITY survey
 * questions. A survey can put SEVERAL grids on one page, so every piece of
 * state lives inside initAvailabilityGrid -- module-level paint state would let
 * a drag on one grid toggle cells in another.
 *
 * The grid element owns everything: its hidden field, count badge and clear
 * button are found relative to it via data-* attributes, so a caller only has to
 * render the markup and this file wires it up. Grids marked
 * .js-availability-grid are initialised automatically on DOM ready.
 */
(function () {
  'use strict';

  function initAvailabilityGrid(grid) {
    if (!grid || grid.dataset.availInit === '1') { return; }
    grid.dataset.availInit = '1';

    // The hidden input this grid serializes into. Named by the grid rather than
    // looked up by a fixed id, so several grids can coexist.
    var hiddenField = grid.dataset.fieldId
      ? document.getElementById(grid.dataset.fieldId)
      : null;
    // Optional chrome. A survey grid has neither, so both are null-guarded
    // everywhere below -- an unguarded lookup here used to throw outright.
    var countBadge = grid.dataset.countId
      ? document.getElementById(grid.dataset.countId)
      : null;
    var clearBtn = grid.dataset.clearId
      ? document.getElementById(grid.dataset.clearId)
      : null;

    // ---- Cell indexing -----------------------------------------------------
    // Template loops can't compute day*24+hour, so each cell's real hour-of-week
    // is assigned here from its data-day / data-hour pair.
    var cells = Array.prototype.slice.call(grid.querySelectorAll('.avail-cell'));
    cells.forEach(function (cell) {
      var day = parseInt(cell.dataset.day, 10);
      var hour = parseInt(cell.dataset.hour, 10);
      cell.dataset.how = String(day * 24 + hour);
    });

    var byHow = {};
    cells.forEach(function (cell) { byHow[cell.dataset.how] = cell; });

    function setSelected(cell, on) {
      cell.classList.toggle('is-selected', on);
      cell.setAttribute('aria-pressed', on ? 'true' : 'false');
    }

    function isSelected(cell) { return cell.classList.contains('is-selected'); }

    function updateCount() {
      if (!countBadge) { return; }
      var n = grid.querySelectorAll('.avail-cell.is-selected').length;
      var label = countBadge.dataset.label || 'hours selected';
      countBadge.textContent = n + ' ' + label;
    }

    function serialize() {
      var hours = cells.filter(isSelected).map(function (c) { return c.dataset.how; });
      if (hiddenField) { hiddenField.value = hours.join(','); }
      return hours;
    }

    // THE hook for "the selection changed" -- every mutation below goes through
    // it. serialize() deliberately sits here rather than inside updateCount():
    // that one returns early on a grid with no count badge, so folding the write
    // into it would silently skip serializing exactly the grids that opted out of
    // the badge.
    //
    // Without this the hidden field was only ever written at init and by the
    // clear button, so a survey answer submitted whatever it started with. The
    // /availability page hid the bug by re-serializing on submit; take_survey has
    // no such flush and saved nothing.
    function changed() {
      updateCount();
      serialize();
    }

    function initialSelection() {
      var raw = hiddenField ? (hiddenField.value || '').trim() : '';
      if (!raw) { return; }
      raw.split(',').forEach(function (part) {
        var cell = byHow[part.trim()];
        if (cell) { setSelected(cell, true); }
      });
    }

    // ---- Painting ----------------------------------------------------------
    // Drag to paint: the first cell's CURRENT state decides whether the drag is
    // selecting or clearing, so a drag started on a lit cell erases.
    var painting = false;
    var paintTo = true;
    var activePointer = null;
    // Set when a MOUSE/PEN pointerdown has just toggled a cell, and read by the
    // click the browser fires straight after so it doesn't toggle again. This
    // replaces an `e.detail !== 0` test that meant "keyboard only" -- correct
    // when both mouse AND touch toggled on pointerdown, but it would now swallow
    // every touch tap, since a tap's click carries detail === 1.
    //
    // Per grid, never module-level: a drag on one survey grid must not suppress
    // the next tap on another.
    var handledByPaint = false;

    function beginPaint(cell) {
      painting = true;
      paintTo = !isSelected(cell);
      setSelected(cell, paintTo);
      changed();
    }

    function continuePaint(cell) {
      if (!painting) { return; }
      if (isSelected(cell) !== paintTo) {
        setSelected(cell, paintTo);
        changed();
      }
    }

    // POINTER EVENTS, not mouse + touch.
    //
    // Mouse, touch and stylus all arrive as one event type, so a cell is toggled
    // from one of exactly TWO places and never both for the same gesture: this
    // handler (mouse/pen, which paints) or the click handler below (touch and
    // keyboard). `handledByPaint` is what keeps them exclusive.
    //
    // Getting that wrong is the bug this file was rewritten to escape: the old
    // mouse-plus-touch arrangement made tapping unreliable, because a tap fires
    // touchstart AND a compatibility mousedown AND a synthesized click, so the
    // same cell was toggled two or three times and landed back where it started.
    // Chrome's device emulator sends that full sequence, which is why it looked
    // random there. Any change here should be checked by tapping a cell and
    // watching the count badge move by exactly one.
    //
    // Delegated (one listener, not 168) and guarded with a pointerId so a second
    // finger can't hijack an in-progress drag.
    grid.addEventListener('pointerdown', function (e) {
      if (!e.isPrimary) { return; }          // ignore extra fingers
      var cell = e.target.closest('.avail-cell');
      if (!cell) { return; }
      // TOUCH DOES NOT PAINT. The capture and preventDefault below are exactly
      // what made the grid unscrollable on a phone: both suppress the browser's
      // native pan, and the grid is deliberately wider than the viewport, so the
      // right-hand days became unreachable. A finger instead falls through to the
      // browser -- which scrolls .availability-scroll sideways, or the page
      // vertically -- and if the gesture turns out to be a stationary tap rather
      // than a swipe, it arrives at the click handler below and toggles one cell.
      //
      // Checked AFTER the .avail-cell lookup so a touch on a day header or hour
      // label is unaffected either way; those aren't cells and never reach here.
      if (e.pointerType === 'touch') { return; }
      activePointer = e.pointerId;
      // Keeps the drag alive if the pointer leaves the grid, and stops the
      // browser turning it into a text selection mid-paint.
      if (grid.setPointerCapture) {
        try { grid.setPointerCapture(e.pointerId); } catch (err) { /* not fatal */ }
      }
      e.preventDefault();
      beginPaint(cell);
      handledByPaint = true;
    });

    grid.addEventListener('pointermove', function (e) {
      if (!painting || e.pointerId !== activePointer) { return; }
      // With pointer capture every move retargets to the grid, so hit-test the
      // live coordinates to find the cell actually under the pointer.
      var el = document.elementFromPoint(e.clientX, e.clientY);
      var cell = el && el.closest ? el.closest('.avail-cell') : null;
      if (cell) {
        e.preventDefault();
        continuePaint(cell);
      }
    });

    function endPaint(e) {
      if (e && e.pointerId !== activePointer) { return; }
      painting = false;
      activePointer = null;
      // Only on cancel. A pointercancel is never followed by a click, so the flag
      // would otherwise stay set and eat the next tap or keypress. It must NOT be
      // cleared on pointerup: that fires BEFORE click, so clearing there would
      // let the mouse toggle twice -- the double-fire this file's header warns
      // about.
      if (e && e.type === 'pointercancel') { handledByPaint = false; }
    }
    grid.addEventListener('pointerup', endPaint);
    grid.addEventListener('pointercancel', endPaint);
    // A drag can end anywhere, so the release is caught at the document. The
    // pointerId guard above closes over THIS grid's activePointer, so another
    // grid's release is ignored here.
    document.addEventListener('pointerup', function (e) {
      endPaint(e);
      // A drag released OUTSIDE the grid fires no click on it, so nothing would
      // consume the flag and the next activation would be swallowed. The timeout
      // runs after any click the browser is about to dispatch, so a normal
      // in-grid release still gets to consume it first.
      setTimeout(function () { handledByPaint = false; }, 0);
    });

    // The single toggle path for everything that is NOT a mouse/pen drag: a touch
    // tap, and a keyboard Enter/Space on the button.
    grid.addEventListener('click', function (e) {
      var cell = e.target.closest('.avail-cell');
      if (!cell) { return; }
      if (handledByPaint) {
        // Consume the one click owed to the mouse/pen gesture that set it.
        handledByPaint = false;
        return;
      }
      setSelected(cell, !isSelected(cell));
      changed();
    });

    // ---- Row / column toggles (the accessible path to bulk selection) -------
    grid.addEventListener('click', function (e) {
      var dayBtn = e.target.closest('.avail-day-header');
      if (dayBtn) {
        var day = parseInt(dayBtn.dataset.day, 10);
        var col = cells.filter(function (c) { return parseInt(c.dataset.day, 10) === day; });
        var turnOn = !col.every(isSelected);
        col.forEach(function (c) { setSelected(c, turnOn); });
        changed();
        return;
      }
      var hourBtn = e.target.closest('.avail-hour-label');
      if (hourBtn) {
        var hour = parseInt(hourBtn.dataset.hour, 10);
        var row = cells.filter(function (c) { return parseInt(c.dataset.hour, 10) === hour; });
        var on = !row.every(isSelected);
        row.forEach(function (c) { setSelected(c, on); });
        changed();
      }
    });

    if (clearBtn) {
      clearBtn.addEventListener('click', function () {
        cells.forEach(function (c) { setSelected(c, false); });
        changed();
      });
    }

    initialSelection();
    changed();

    // Handed back so a page can serialize on its own submit or timezone change.
    return { serialize: serialize, grid: grid };
  }

  window.initAvailabilityGrid = initAvailabilityGrid;

  function initAll() {
    document.querySelectorAll('.js-availability-grid').forEach(initAvailabilityGrid);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAll);
  } else {
    initAll();
  }
})();
