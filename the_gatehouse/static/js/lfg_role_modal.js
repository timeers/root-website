/*
 * LFG-role modals on the Edit Guild page.
 *
 * Add/Edit share one modal (#lfg-role-modal): the trigger hx-gets the server-rendered
 * form into #lfg-role-modal-body while data-bs-toggle opens the shell immediately, so the
 * click feels instant and all the form machinery (dropdowns, crispy fields, validation)
 * stays server-side. Delete uses a separate confirm modal that reads the role's details
 * from the clicked button's data-* attributes.
 *
 * Each action commits on its own, so the main guild form's unsaved input is never touched.
 *
 * Listeners are delegated on document/document.body, so rows swapped in later need no
 * rebinding.
 */
(function () {
  var MODAL_ID = 'lfg-role-modal';
  var BODY_ID = 'lfg-role-modal-body';
  var DELETE_MODAL_ID = 'lfg-role-delete-modal';

  function byId(id) { return document.getElementById(id); }

  function hide(id) {
    var el = byId(id);
    // getOrCreateInstance so a modal opened purely by data-bs-toggle is still hidable;
    // hiding an already-hidden modal is a no-op.
    if (el && window.bootstrap) bootstrap.Modal.getOrCreateInstance(el).hide();
  }

  function csrfToken() {
    var el = document.querySelector('[name=csrfmiddlewaretoken]');
    return el ? el.value : '';
  }

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

  // ---- Add / Edit modal ----------------------------------------------------------
  var modalEl = byId(MODAL_ID);
  if (modalEl) {
    // The title differs per action but lives in the shell, not the fetched body.
    modalEl.addEventListener('show.bs.modal', function (e) {
      var title = e.relatedTarget && e.relatedTarget.dataset.modalTitle;
      var slot = byId('lfg-role-modal-title-text');
      if (title && slot) slot.textContent = title;
    });

    // Reset to the spinner so reopening never flashes the previous role's form. Safe
    // here: `hidden` fires after the fade completes, by which point htmx has applied
    // both the main and OOB swaps — and every OOB target lives outside this modal.
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

  // ---- Delete confirm modal ------------------------------------------------------
  var deleteEl = byId(DELETE_MODAL_ID);
  if (deleteEl) {
    deleteEl.addEventListener('show.bs.modal', function (e) {
      var btn = e.relatedTarget;
      if (!btn) return;
      var nameSlot = byId('lfg-delete-role-name');
      if (nameSlot) nameSlot.textContent = btn.dataset.roleName || '';
      deleteEl.dataset.deleteUrl = btn.dataset.deleteUrl || '';
      deleteEl.dataset.rowTarget = btn.dataset.rowTarget || '';
      var err = byId('lfg-delete-error');
      if (err) err.classList.add('d-none');
    });

    var confirmBtn = byId('lfg-delete-confirm');
    if (confirmBtn) {
      confirmBtn.addEventListener('click', function () {
        var url = deleteEl.dataset.deleteUrl;
        var rowSel = deleteEl.dataset.rowTarget;
        if (!url || !rowSel) return;

        var spinner = byId('lfg-delete-spinner');
        var icon = byId('lfg-delete-icon');
        confirmBtn.disabled = true;
        if (spinner) spinner.classList.remove('d-none');
        if (icon) icon.classList.add('d-none');

        // htmx.ajax rather than hx-post so the row target can vary per click. The CSRF
        // header must be passed explicitly: hx-headers is inherited through the DOM and
        // does not apply to a programmatic call made from this modal.
        htmx.ajax('POST', url, {
          target: rowSel,
          swap: 'outerHTML',
          headers: {'X-CSRFToken': csrfToken()}
        }).catch(function () {
          var err = byId('lfg-delete-error');
          if (err) {
            err.textContent = 'Could not delete that role. Please try again.';
            err.classList.remove('d-none');
          }
        }).finally(function () {
          confirmBtn.disabled = false;
          if (spinner) spinner.classList.add('d-none');
          if (icon) icon.classList.remove('d-none');
        });
      });
    }
  }

  // ---- Success ------------------------------------------------------------------
  // The server sets `HX-Trigger: lfgRoleSaved` on a successful add/edit/delete; htmx
  // fires it on the requesting element and it bubbles to the body. Close whichever modal
  // is open (the other call is a no-op).
  document.body.addEventListener('lfgRoleSaved', function () {
    hide(MODAL_ID);
    hide(DELETE_MODAL_ID);
  });
})();
