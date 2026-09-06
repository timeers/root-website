/* Paint-to-select behaviour for a 7x24 weekly availability grid.
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

    function beginPaint(cell) {
      painting = true;
      paintTo = !isSelected(cell);
      setSelected(cell, paintTo);
      updateCount();
    }

    function continuePaint(cell) {
      if (!painting) { return; }
      if (isSelected(cell) !== paintTo) {
        setSelected(cell, paintTo);
        updateCount();
      }
    }

    // POINTER EVENTS, not mouse + touch.
    //
    // Mouse, touch and stylus all arrive here as one event type, so there is
    // exactly ONE code path that toggles a cell. The previous mouse-plus-touch
    // arrangement is what made tapping unreliable: a tap fires touchstart AND a
    // compatibility mousedown AND a synthesized click, so the same cell could be
    // toggled two or three times and land back where it started. Chrome's device
    // emulator sends that full sequence, which is why it looked random there.
    //
    // Delegated (one listener, not 168) and guarded with a pointerId so a second
    // finger can't hijack an in-progress drag.
    grid.addEventListener('pointerdown', function (e) {
      if (!e.isPrimary) { return; }          // ignore extra fingers
      var cell = e.target.closest('.avail-cell');
      if (!cell) { return; }
      activePointer = e.pointerId;
      // Keeps the drag alive if the finger leaves the grid, and stops the browser
      // turning the gesture into a scroll or a text selection mid-paint.
      if (grid.setPointerCapture) {
        try { grid.setPointerCapture(e.pointerId); } catch (err) { /* not fatal */ }
      }
      e.preventDefault();
      beginPaint(cell);
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
    }
    grid.addEventListener('pointerup', endPaint);
    grid.addEventListener('pointercancel', endPaint);
    // A drag can end anywhere, so the release is caught at the document. The
    // pointerId guard above closes over THIS grid's activePointer, so another
    // grid's release is ignored here.
    document.addEventListener('pointerup', endPaint);

    // pointerdown already toggled the cell, so the click that follows must not do
    // it again. Only a KEYBOARD activation (Enter/Space on the button, which fires
    // click with no preceding pointerdown) still needs handling here.
    grid.addEventListener('click', function (e) {
      var cell = e.target.closest('.avail-cell');
      if (!cell || e.detail !== 0) { return; }
      setSelected(cell, !isSelected(cell));
      updateCount();
    });

    // ---- Row / column toggles (the accessible path to bulk selection) -------
    grid.addEventListener('click', function (e) {
      var dayBtn = e.target.closest('.avail-day-header');
      if (dayBtn) {
        var day = parseInt(dayBtn.dataset.day, 10);
        var col = cells.filter(function (c) { return parseInt(c.dataset.day, 10) === day; });
        var turnOn = !col.every(isSelected);
        col.forEach(function (c) { setSelected(c, turnOn); });
        updateCount();
        return;
      }
      var hourBtn = e.target.closest('.avail-hour-label');
      if (hourBtn) {
        var hour = parseInt(hourBtn.dataset.hour, 10);
        var row = cells.filter(function (c) { return parseInt(c.dataset.hour, 10) === hour; });
        var on = !row.every(isSelected);
        row.forEach(function (c) { setSelected(c, on); });
        updateCount();
      }
    });

    if (clearBtn) {
      clearBtn.addEventListener('click', function () {
        cells.forEach(function (c) { setSelected(c, false); });
        updateCount();
        serialize();
      });
    }

    initialSelection();
    updateCount();
    serialize();

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
