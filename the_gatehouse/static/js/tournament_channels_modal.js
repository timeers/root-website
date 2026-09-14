/*
 * Series-channels modal on the Edit Guild page.
 *
 * Each row's Edit button hx-gets the server-rendered form into
 * #tournament-channels-modal-body while data-bs-toggle opens the shell immediately, so
 * the click feels instant and all the form machinery (channel dropdowns, validation)
 * stays server-side.
 *
 * Edit-only: unlike the LFG-role modal there is no add or delete, so there's no confirm
 * dialog here. Each save commits on its own, so the main guild form's unsaved input is
 * never touched.
 *
 * Listeners are delegated on document.body, so rows swapped in later need no rebinding.
 */
(function () {
  var MODAL_ID = 'tournament-channels-modal';
  var BODY_ID = 'tournament-channels-modal-body';

  function byId(id) { return document.getElementById(id); }

  // ---- Validation errors ---------------------------------------------------------
  // htmx doesn't swap non-2xx responses. An invalid save returns 422 with the re-rendered
  // form, which we DO want swapped so the modal stays open showing the errors and the
  // user's input. Scoped to this one target so no other page behaviour changes.
  document.body.addEventListener('htmx:beforeSwap', function (e) {
    if (e.detail.target && e.detail.target.id === BODY_ID && e.detail.xhr.status === 422) {
      e.detail.shouldSwap = true;
      e.detail.isError = false;
    }
  });

  var modalEl = byId(MODAL_ID);
  if (modalEl) {
    // The title is the series name, which lives on the trigger, not the fetched body.
    modalEl.addEventListener('show.bs.modal', function (e) {
      var title = e.relatedTarget && e.relatedTarget.dataset.modalTitle;
      var slot = byId('tournament-channels-modal-title-text');
      if (title && slot) slot.textContent = title;
    });

    // Reset to the spinner so reopening never flashes the previous series' form. Safe
    // here: `hidden` fires after the fade completes, by which point htmx has applied
    // both the main and OOB swaps — and the OOB target (the row) lives outside this modal.
    modalEl.addEventListener('hidden.bs.modal', function () {
      var body = byId(BODY_ID);
      if (body) {
        body.innerHTML =
          '<div class="text-center text-muted py-4">' +
          '<div class="spinner-border" role="status"></div></div>';
      }
    });
  }

  // Focus the first field once the fetched form has settled.
  document.body.addEventListener('htmx:afterSettle', function (e) {
    if (!e.detail.target || e.detail.target.id !== BODY_ID) return;
    var first = e.detail.target.querySelector('select, input:not([type=hidden]), textarea');
    if (first) first.focus();
  });

  // ---- Match reminder rows ------------------------------------------------------
  // A Django inline formset, added to and removed from client-side. Nothing here
  // saves: the modal's own Save button commits the rows with the rest of the form.
  //
  // Delegated on the body, like every other listener in this file, so rows and forms
  // htmx swaps in later need no rebinding.

  // The formset's TOTAL_FORMS input. Django reads it to decide how many rows to bind,
  // so adding a row means appending markup AND bumping this.
  function totalForms() {
    return document.querySelector('input[name$="-TOTAL_FORMS"]');
  }

  document.body.addEventListener('click', function (e) {
    var addBtn = e.target.closest && e.target.closest('#add-reminder-btn');
    if (addBtn) {
      var tpl = byId('reminder-empty-form');
      var rows = byId('reminder-rows');
      var total = totalForms();
      if (!tpl || !rows || !total) return;

      // empty_form ships with __prefix__ where the index goes; swap in the next one.
      var index = parseInt(total.value, 10) || 0;
      var html = tpl.innerHTML.replace(/__prefix__/g, index);

      var holder = document.createElement('div');
      holder.innerHTML = html;
      var row = holder.firstElementChild;
      if (!row) return;
      rows.appendChild(row);
      total.value = index + 1;

      var firstInput = row.querySelector('input:not([type=hidden])');
      if (firstInput) firstInput.focus();
      return;
    }

    var rmBtn = e.target.closest && e.target.closest('.remove-reminder-btn');
    if (rmBtn) {
      var target = rmBtn.closest('.reminder-row');
      if (!target) return;
      var del = target.querySelector('input[name$="-DELETE"]');
      var id = target.querySelector('input[name$="-id"]');

      // A SAVED row must stay in the DOM with DELETE ticked — that is the only way
      // the formset learns to delete it. A row added in this session was never
      // saved, so it can just go; leaving it would submit a blank form and, with
      // TOTAL_FORMS still counting it, trip the formset's validation.
      if (del && id && id.value) {
        del.checked = true;
        target.classList.add('d-none');
      } else {
        target.remove();
        var total2 = totalForms();
        if (total2) {
          // Re-index the surviving rows: Django requires form indexes to run
          // 0..TOTAL_FORMS-1 with no gaps, which removing from the middle breaks.
          var rows2 = byId('reminder-rows');
          var remaining = rows2 ? rows2.querySelectorAll('.reminder-row') : [];
          Array.prototype.forEach.call(remaining, function (r, i) {
            Array.prototype.forEach.call(
              r.querySelectorAll('input, select, textarea, label'), function (el2) {
                ['name', 'id', 'for'].forEach(function (attr) {
                  var v = el2.getAttribute(attr);
                  if (v) el2.setAttribute(attr, v.replace(/-\d+-/, '-' + i + '-'));
                });
              });
          });
          total2.value = remaining.length;
        }
      }
    }
  });

  // ---- Success ------------------------------------------------------------------
  // The server sets `HX-Trigger: tournamentChannelsSaved` on a successful save; htmx
  // fires it on the requesting element and it bubbles to the body.
  document.body.addEventListener('tournamentChannelsSaved', function () {
    var el = byId(MODAL_ID);
    if (el && window.bootstrap) bootstrap.Modal.getOrCreateInstance(el).hide();
  });
})();
