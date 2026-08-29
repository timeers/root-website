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

  // ---- Success ------------------------------------------------------------------
  // The server sets `HX-Trigger: tournamentChannelsSaved` on a successful save; htmx
  // fires it on the requesting element and it bubbles to the body.
  document.body.addEventListener('tournamentChannelsSaved', function () {
    var el = byId(MODAL_ID);
    if (el && window.bootstrap) bootstrap.Modal.getOrCreateInstance(el).hide();
  });
})();
