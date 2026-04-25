(function () {
  'use strict';

  function getCookie(name) {
    const m = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]+)'));
    return m ? decodeURIComponent(m[1]) : '';
  }
  const csrftoken = getCookie('csrftoken');

  // Generic add-form handler — picks up any <form data-add-url="..."> and POSTs
  // it, then inserts the returned HTML into the target list.
  function bindAddForm(form) {
    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const url = form.dataset.addUrl;
      const targetSelector = form.dataset.target || '#' + form.id.replace('-add-form', '-list');
      const list = document.querySelector(targetSelector);
      const fd = new FormData(form);
      const resp = await fetch(url, {
        method: 'POST',
        body: fd,
        headers: { 'X-CSRFToken': csrftoken },
      });
      if (!resp.ok) {
        alert('Failed to add: ' + await resp.text());
        return;
      }
      const html = await resp.text();
      // Remove empty-state placeholder if present.
      const empty = list.querySelector('[id$="-empty"]');
      if (empty) empty.remove();
      list.insertAdjacentHTML('beforeend', html);
      if (window.initForgeRichText) window.initForgeRichText(list);
      // Clear text/file inputs in the add form.
      form.querySelectorAll('input[type="text"], input[type="number"], textarea').forEach(el => { el.value = ''; });
      form.querySelectorAll('input[type="file"]').forEach(el => { el.value = ''; });
    });
  }

  // Generic delete — <button data-delete-url="...">
  document.addEventListener('click', async (ev) => {
    const btn = ev.target.closest('[data-delete-url]');
    if (!btn) return;
    ev.preventDefault();
    if (!confirm(btn.dataset.confirm || 'Delete this item?')) return;
    const url = btn.dataset.deleteUrl;
    const resp = await fetch(url, {
      method: 'DELETE',
      headers: { 'X-CSRFToken': csrftoken },
    });
    if (!resp.ok) {
      alert('Failed to delete');
      return;
    }
    const row = btn.closest('[data-row]');
    if (row) row.remove();
  });

  // Inline edit — submits a row's own form to its data-edit-url and replaces the row with the response.
  document.addEventListener('submit', async (ev) => {
    const form = ev.target.closest('form[data-edit-url]');
    if (!form) return;
    ev.preventDefault();
    const url = form.dataset.editUrl;
    const fd = new FormData(form);
    const resp = await fetch(url, {
      method: 'POST',
      body: fd,
      headers: { 'X-CSRFToken': csrftoken },
    });
    if (!resp.ok) {
      alert('Save failed: ' + await resp.text());
      return;
    }
    const html = await resp.text();
    const row = form.closest('[data-row]');
    if (row) {
      row.insertAdjacentHTML('afterend', html);
      row.remove();
      if (window.initForgeRichText) window.initForgeRichText(document);
    }
  });

  function initAll() {
    document.querySelectorAll('form[data-add-url]').forEach(bindAddForm);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAll);
  } else {
    initAll();
  }
})();
