// Opt-in "Saving..." spinner for a form's submit button.
//
// Give the button a data-saving-label ("Saving...") and load this script. When
// its form ACTUALLY submits, the button is disabled and shows a spinner plus
// that label -- the same look as the other save spinners on the site -- so a
// slow save reads as "registered, loading" rather than a click that did nothing,
// and can't be double-submitted.
//
// Hooked on the form's `submit` event, not the button's click: that fires only
// after the browser's own validation passes, and the listener sits on the
// document so it runs after any form-level handler -- one that cancels the
// submit (event.defaultPrevented) leaves the button alone.
(function () {
  document.addEventListener('submit', function (event) {
    if (event.defaultPrevented) return;
    var form = event.target;
    var btn = (event.submitter && event.submitter.matches('[data-saving-label]'))
      ? event.submitter
      : form.querySelector('button[type="submit"][data-saving-label]');
    if (!btn || btn.disabled) return;
    btn.dataset.savingOriginal = btn.innerHTML;
    // Disabled AFTER the submit was triggered, so the request still goes out;
    // these buttons carry no name/value, so nothing is dropped from the POST.
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1" role="status" aria-hidden="true"></span>'
      + btn.dataset.savingLabel;
  });

  // Coming BACK to the page (browser back button) can restore it from the
  // back/forward cache exactly as it was left -- spinner and all, with a
  // disabled button. Put the button back.
  window.addEventListener('pageshow', function (event) {
    if (!event.persisted) return;
    document.querySelectorAll('[data-saving-original]').forEach(function (btn) {
      btn.disabled = false;
      btn.innerHTML = btn.dataset.savingOriginal;
      delete btn.dataset.savingOriginal;
    });
  });
})();
