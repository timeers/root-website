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
    if (form._addFormBound) return;
    form._addFormBound = true;
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
      // Remove any open inline-form (since a row replaces it).
      list.querySelector('.forge-inline-form')?.remove();
      if (window.initForgeRichText) window.initForgeRichText(list);
      list.dispatchEvent(new CustomEvent('forge:row-replaced', { bubbles: true }));
      // Clear text/file inputs in the add form.
      form.querySelectorAll('input[type="text"], input[type="number"], textarea').forEach(el => { el.value = ''; });
      form.querySelectorAll('input[type="file"]').forEach(el => { el.value = ''; });
      // Clear any rich-text contentEditable mirrors so they match the cleared textareas.
      form.querySelectorAll('.forge-rich-text-editor').forEach(el => { el.innerHTML = '<br>'; });
      const previewImg = form.querySelector('.image-preview');
      if (previewImg) {
        previewImg.hidden = true;
        previewImg.src = '';
      }
      const currentIcon = form.querySelector('.piece-current-icon');
      if (currentIcon) currentIcon.hidden = false;
      // Phase-step add form: hide the form and show the toggle button again.
      if (form.id.startsWith('phase-step-add-form-')) {
        const phase = form.id.replace('phase-step-add-form-', '');
        form.hidden = true;
        const toggle = document.querySelector(`[data-phase-step-add-toggle="${phase}"]`);
        if (toggle) toggle.hidden = false;
      }
    });
  }

  // Show a thumbnail preview when a piece icon file is selected,
  // and hide the existing icon so the new selection takes its place.
  document.addEventListener('change', (ev) => {
    const input = ev.target.closest('.compact-file-input');
    if (!input) return;
    const form = input.closest('form');
    if (!form) return;
    const previewImg = form.querySelector('.image-preview');
    const currentIcon = form.querySelector('.piece-current-icon');
    if (!previewImg) return;
    const file = input.files && input.files[0];
    if (!file) {
      previewImg.hidden = true;
      previewImg.src = '';
      if (currentIcon) currentIcon.hidden = false;
      return;
    }
    const reader = new FileReader();
    reader.onload = (e) => {
      previewImg.src = e.target.result;
      previewImg.hidden = false;
      if (currentIcon) currentIcon.hidden = true;
    };
    reader.readAsDataURL(file);
  });

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
    const actionList = btn.closest('.phase-step-action-list');
    if (row) row.remove();
    if (actionList?.dataset.stepId) setActionTypeLock(actionList.dataset.stepId);
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
      const newRow = row.nextElementSibling;
      const parent = row.parentElement;
      row.remove();
      if (window.initForgeRichText) window.initForgeRichText(document);
      if (parent) parent.dispatchEvent(new CustomEvent('forge:row-replaced', { bubbles: true, detail: { row: newRow } }));
    }
  });

  // Generic dirty-form tracking — any input inside a [data-dirty-form] form
  // marks the surrounding [data-row] as dirty so its [data-save-btn] reveals.
  document.addEventListener('input', (ev) => {
    const form = ev.target.closest('form[data-dirty-form]');
    if (!form) return;
    form.closest('[data-row]')?.classList.add('is-dirty');
  });

  // Auto-save handler — fires on toggle/segmented inputs with [data-autosave-url].
  // Sends `value` as the only POST field; server returns 204 on success.
  async function autosavePost(url, value, statusEl) {
    const fd = new FormData();
    fd.append('value', value);
    try {
      const resp = await fetch(url, {
        method: 'POST',
        body: fd,
        headers: { 'X-CSRFToken': csrftoken },
      });
      if (!resp.ok) throw new Error(await resp.text());
      flashStatus(statusEl, 'Saved ✓', false);
    } catch (err) {
      if (statusEl) {
        flashStatus(statusEl, 'Save failed', true);
      } else {
        alert('Save failed: ' + (err && err.message ? err.message : 'unknown error'));
      }
    }
  }

  function flashStatus(el, text, isError) {
    if (!el) return;
    el.textContent = text;
    el.classList.toggle('is-error', !!isError);
    el.classList.add('is-visible');
    clearTimeout(el._autosaveTimer);
    el._autosaveTimer = setTimeout(() => {
      el.classList.remove('is-visible');
    }, 1500);
  }

  // Boolean toggles: <input type="checkbox" data-autosave-url="..." data-autosave-type="boolean">
  document.addEventListener('change', (ev) => {
    const input = ev.target.closest('input[data-autosave-url][data-autosave-type="boolean"]');
    if (!input) return;
    const status = input.parentElement.querySelector('[data-autosave-status]');
    autosavePost(input.dataset.autosaveUrl, input.checked ? 'true' : 'false', status);
  });

  // Segmented + crafted-image toggles. Both fire on click.
  document.addEventListener('click', (ev) => {
    // Segmented (Layout): child <button data-value="..."> in a group.
    const segBtn = ev.target.closest('[data-autosave-type="segmented"] [data-value]');
    if (segBtn) {
      const group = segBtn.closest('[data-autosave-url][data-autosave-type="segmented"]');
      if (group && !segBtn.classList.contains('is-active')) {
        group.querySelectorAll('[data-value]').forEach(b => b.classList.remove('is-active'));
        segBtn.classList.add('is-active');
        const status = group.querySelector('[data-autosave-status]');
        autosavePost(group.dataset.autosaveUrl, segBtn.dataset.value, status);
      }
      return;
    }

    // Crafted-items image toggle: button itself toggles on/off.
    const craftBtn = ev.target.closest('button[data-autosave-url][data-autosave-type="crafted"]');
    if (craftBtn) {
      const turningOn = !craftBtn.classList.contains('is-on');
      craftBtn.classList.toggle('is-on', turningOn);
      craftBtn.classList.toggle('is-off', !turningOn);
      craftBtn.setAttribute('aria-pressed', turningOn ? 'true' : 'false');
      const status = craftBtn.parentElement.querySelector('[data-autosave-status]');
      autosavePost(craftBtn.dataset.autosaveUrl, turningOn ? 'true' : 'false', status);
    }
  });

  // ---------- Phase-step add toggle (one button per phase) ----------
  document.addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-phase-step-add-toggle]');
    if (btn) {
      const phase = btn.dataset.phaseStepAddToggle;
      const form = document.getElementById(`phase-step-add-form-${phase}`);
      if (form) {
        form.hidden = false;
        btn.hidden = true;
        form.querySelector('textarea')?.focus();
      }
      return;
    }
    const cancelBtn = ev.target.closest('[data-phase-step-add-cancel]');
    if (cancelBtn) {
      const phase = cancelBtn.dataset.phaseStepAddCancel;
      const form = document.getElementById(`phase-step-add-form-${phase}`);
      const toggle = document.querySelector(`[data-phase-step-add-toggle="${phase}"]`);
      if (form) {
        form.hidden = true;
        form.reset();
      }
      if (toggle) toggle.hidden = false;
    }
  });

  // ---------- Inline add forms for action / box / track ----------
  function insertInlineForm(templateId, container, stepPk) {
    if (!container) return;
    // Don't double-insert if one is already open in this container.
    const existing = container.querySelector('.forge-inline-form');
    if (existing) {
      existing.querySelector('input,textarea,select')?.focus();
      return;
    }
    const tpl = document.getElementById(templateId);
    if (!tpl) return;
    // Templates render with step_pk=0; rewrite the URL segment to the real step.
    const html = tpl.innerHTML.replace(/phase-step\/0\//g, `phase-step/${stepPk}/`);
    const wrap = document.createElement('div');
    wrap.innerHTML = html.trim();
    const node = wrap.firstElementChild;
    // Also fix the data-target selector which includes step_pk.
    node.querySelectorAll('form[data-target]').forEach(f => {
      f.dataset.target = f.dataset.target.replace(/data-step-id='0'/g, `data-step-id='${stepPk}'`);
    });
    container.appendChild(node);
    const form = node.querySelector('form');
    if (form) bindAddForm(form);
    if (window.initForgeRichText) window.initForgeRichText(node);
    node.querySelector('input,textarea,select')?.focus();
  }

  async function insertActionInlineForm(container, stepPk) {
    if (!container) return;
    const panel = container.closest('[data-actions-panel]');
    if (panel?.hasAttribute('hidden')) {
      panel.removeAttribute('hidden');
      const toggle = document.querySelector(`[data-toggle-actions][data-step-id="${stepPk}"]`);
      toggle?.setAttribute('aria-expanded', 'true');
    }
    if (container.querySelector('.forge-inline-form')) {
      container.querySelector('.forge-inline-form input,textarea,select')?.focus();
      return;
    }
    const url = `/hx/forge/phase-step/${stepPk}/action/form/`;
    const res = await fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
    if (!res.ok) return;
    const html = await res.text();
    const wrap = document.createElement('div');
    wrap.innerHTML = html.trim();
    const node = wrap.firstElementChild;
    container.appendChild(node);
    const form = node.querySelector('form');
    if (form) bindAddForm(form);
    if (window.initForgeRichText) window.initForgeRichText(node);
    node.querySelector('input,textarea,select')?.focus();
    setActionTypeLock(stepPk);
  }

  function setActionTypeLock(stepPk) {
    const sel = document.querySelector(`.phase-step-action-type[data-step-id="${stepPk}"]`);
    if (!sel) return;
    const list = document.querySelector(`.phase-step-action-list[data-step-id="${stepPk}"]`);
    const formOpen = !!list?.querySelector('.forge-inline-form');
    const hasActions = !!list?.querySelector('[data-row][data-kind="action"]');
    const shouldLock = formOpen || hasActions;
    sel.disabled = shouldLock;
    if (shouldLock) {
      sel.setAttribute('disabled', '');
    } else {
      sel.removeAttribute('disabled');
    }
  }

  document.addEventListener('click', (ev) => {
    const cancel = ev.target.closest('[data-cancel-inline-form]');
    if (cancel) {
      const inline = cancel.closest('.forge-inline-form');
      const stepPk = inline?.dataset.stepId;
      inline?.remove();
      if (stepPk) setActionTypeLock(stepPk);
      return;
    }
    const toggleBtn = ev.target.closest('[data-toggle-actions]');
    if (toggleBtn) {
      const stepPk = toggleBtn.dataset.stepId;
      const panel = document.querySelector(`[data-actions-panel][data-step-id="${stepPk}"]`);
      if (panel) {
        const willOpen = panel.hasAttribute('hidden');
        if (willOpen) panel.removeAttribute('hidden');
        else panel.setAttribute('hidden', '');
        toggleBtn.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
      }
      return;
    }
    const actionBtn = ev.target.closest('[data-add-action]');
    if (actionBtn) {
      const stepPk = actionBtn.dataset.stepId;
      const list = document.querySelector(`.phase-step-action-list[data-step-id="${stepPk}"]`);
      // Action form is fetched per-step (filtered by step.action_type) instead
      // of cloned from a static template.
      insertActionInlineForm(list, stepPk);
      return;
    }
    const boxBtn = ev.target.closest('[data-add-box]');
    if (boxBtn) {
      const stepPk = boxBtn.dataset.stepId;
      const list = document.querySelector(`.phase-step-children[data-step-id="${stepPk}"]`);
      insertInlineForm('forge-box-form-template', list, stepPk);
      return;
    }
    const trackBtn = ev.target.closest('[data-add-track]');
    if (trackBtn) {
      const stepPk = trackBtn.dataset.stepId;
      const list = document.querySelector(`.phase-step-children[data-step-id="${stepPk}"]`);
      insertInlineForm('forge-track-form-template', list, stepPk);
      return;
    }
  });

  // ---------- Sortable initialization for phase-step children + actions ----------
  function initStepSortables(scope) {
    if (typeof Sortable === 'undefined') return;
    (scope || document).querySelectorAll('.phase-step-children').forEach(list => {
      if (list._sortableInit) return;
      list._sortableInit = true;
      const stepId = list.dataset.stepId;
      new Sortable(list, {
        animation: 150,
        handle: '.row-grip',
        group: 'step-children-' + stepId,
        filter: '.forge-inline-form',
        preventOnFilter: false,
        onEnd: async () => {
          const order = [...list.querySelectorAll('[data-row]')].map(r => ({
            kind: r.dataset.kind,
            id: parseInt(r.dataset.id, 10),
          })).filter(o => o.kind === 'box' || o.kind === 'track');
          await fetch(list.dataset.reorderUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrftoken },
            body: JSON.stringify({ order }),
          });
        }
      });
    });
    (scope || document).querySelectorAll('.phase-step-action-list').forEach(list => {
      if (list._sortableInit) return;
      list._sortableInit = true;
      const stepId = list.dataset.stepId;
      new Sortable(list, {
        animation: 150,
        handle: '.action-grip',
        group: 'step-actions-' + stepId,
        filter: '.forge-inline-form',
        preventOnFilter: false,
        onEnd: async () => {
          const order = [...list.querySelectorAll('[data-row]')].map(r => parseInt(r.dataset.id, 10));
          await fetch(list.dataset.reorderUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrftoken },
            body: JSON.stringify({ order }),
          });
        }
      });
    });
  }
  window.initForgeStepSortables = initStepSortables;

  // After any inline-edit response replaces a row, re-init sortables so a
  // newly-rendered phase-step-row gets its child sortables wired up.
  document.addEventListener('forge:row-replaced', (ev) => {
    initStepSortables(ev.target);
    // If the replaced container is an action list, re-evaluate the action-type lock.
    const list = ev.target.closest?.('.phase-step-action-list');
    if (list?.dataset.stepId) setActionTypeLock(list.dataset.stepId);
  });

  // Cost-image preview: when the user picks a file, show it next to the input.
  document.addEventListener('change', (ev) => {
    const input = ev.target.closest('[data-cost-image-input]');
    if (!input) return;
    const preview = input.parentElement.querySelector('[data-cost-image-preview]');
    if (!preview) return;
    const file = input.files && input.files[0];
    if (!file) {
      preview.hidden = true;
      preview.src = '';
      return;
    }
    preview.src = URL.createObjectURL(file);
    preview.hidden = false;
  });

  // Action-type dropdown: POST to the setter when changed.
  document.addEventListener('change', async (ev) => {
    const sel = ev.target.closest('.phase-step-action-type');
    if (!sel) return;
    const url = sel.closest('.phase-step-action-header')?.dataset.actionTypeUrl;
    if (!url) return;
    const fd = new FormData();
    fd.append('action_type', sel.value);
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'X-CSRFToken': csrftoken },
      body: fd,
    });
    if (!res.ok) {
      // Revert by reloading the header (server is the source of truth).
      console.warn('action_type set failed', await res.text());
    }
  });

  // ---------- Slot modal ----------
  const slotModal = document.getElementById('forge-slot-modal');
  let slotModalState = null;

  function openSlotModal({ trackId, row, col, content, rowTitle, bgUrl }) {
    if (!slotModal) return;
    slotModalState = { trackId, row, col };
    slotModal.querySelector('#forge-slot-modal-title').textContent = `Edit slot (row ${row}, col ${col})`;
    const tray = slotModal.querySelector('[data-slot-tray]');
    tray.innerHTML = '';
    const keywords = (content || '').split('|').map(k => k.trim()).filter(Boolean);
    keywords.forEach(k => addTrayItem(k));
    slotModal.querySelector('[data-slot-row-title-input]').value = rowTitle || '';
    const wrap = slotModal.querySelector('[data-slot-row-title-wrap]');
    if (wrap) wrap.style.display = (parseInt(col, 10) === 0) ? '' : 'none';
    const bgInput = slotModal.querySelector('[data-slot-bg-input]');
    if (bgInput) bgInput.value = '';
    const bgClear = slotModal.querySelector('[data-slot-bg-clear]');
    if (bgClear) bgClear.checked = false;
    const bgCurrent = slotModal.querySelector('[data-slot-bg-current]');
    const bgThumb = slotModal.querySelector('[data-slot-bg-thumb]');
    if (bgCurrent && bgThumb) {
      if (bgUrl) {
        bgThumb.src = bgUrl;
        bgCurrent.style.display = 'flex';
      } else {
        bgThumb.removeAttribute('src');
        bgCurrent.style.display = 'none';
      }
    }
    const previewWrap = slotModal.querySelector('[data-slot-bg-preview-wrap]');
    const previewImg = slotModal.querySelector('[data-slot-bg-preview]');
    if (previewWrap && previewImg) {
      previewImg.removeAttribute('src');
      previewWrap.style.display = 'none';
    }
    syncContentInput();
    slotModal.hidden = false;
    document.body.classList.add('forge-modal-open');
  }

  function closeSlotModal() {
    if (!slotModal) return;
    slotModal.hidden = true;
    document.body.classList.remove('forge-modal-open');
    slotModalState = null;
  }

  function addTrayItem(keyword) {
    const tray = slotModal.querySelector('[data-slot-tray]');
    if (!tray) return;
    if (tray.querySelectorAll('[data-keyword]').length >= 4) return;
    const pickerImg = slotModal.querySelector(`[data-slot-picker] [data-keyword="${CSS.escape(keyword)}"] img`);
    const url = pickerImg ? pickerImg.src : '';
    const item = document.createElement('button');
    item.type = 'button';
    item.className = 'forge-slot-modal__tray-item';
    item.dataset.keyword = keyword;
    item.title = 'Click to remove';
    item.innerHTML = url ? `<img src="${url}" alt="${keyword}">` : keyword;
    tray.appendChild(item);
  }

  function syncContentInput() {
    if (!slotModal) return;
    const tray = slotModal.querySelector('[data-slot-tray]');
    const keywords = [...tray.querySelectorAll('[data-keyword]')].map(el => el.dataset.keyword);
    slotModal.querySelector('[data-slot-content-input]').value = keywords.join('|');
  }

  document.addEventListener('click', (ev) => {
    // Open modal from a track cell click.
    const cell = ev.target.closest('[data-slot-cell]');
    if (cell) {
      ev.preventDefault();
      openSlotModal({
        trackId: cell.dataset.trackId,
        row: cell.dataset.row,
        col: cell.dataset.col,
        content: cell.dataset.content,
        rowTitle: cell.dataset.rowTitle,
        bgUrl: cell.dataset.bgUrl,
      });
      return;
    }
    if (!slotModal || slotModal.hidden) return;
    if (ev.target.closest('[data-slot-modal-close]')) {
      closeSlotModal();
      return;
    }
    // Picker click → add to tray.
    const pick = ev.target.closest('[data-slot-picker] [data-keyword]');
    if (pick) {
      addTrayItem(pick.dataset.keyword);
      syncContentInput();
      return;
    }
    // Tray click → remove.
    const trayItem = ev.target.closest('[data-slot-tray] [data-keyword]');
    if (trayItem) {
      trayItem.remove();
      syncContentInput();
    }
  });

  // Slot modal: live preview when user picks a file
  document.addEventListener('change', (ev) => {
    const input = ev.target.closest('[data-slot-bg-input]');
    if (!input || !slotModal || slotModal.hidden) return;
    const previewWrap = slotModal.querySelector('[data-slot-bg-preview-wrap]');
    const previewImg = slotModal.querySelector('[data-slot-bg-preview]');
    if (!previewWrap || !previewImg) return;
    const file = input.files && input.files[0];
    if (!file) {
      previewImg.removeAttribute('src');
      previewWrap.style.display = 'none';
      return;
    }
    previewImg.src = URL.createObjectURL(file);
    previewWrap.style.display = 'flex';
  });

  // Slot modal submit
  document.addEventListener('submit', async (ev) => {
    const form = ev.target;
    if (form.id !== 'forge-slot-modal-form' || !slotModalState) return;
    ev.preventDefault();
    syncContentInput();
    const { trackId, row, col } = slotModalState;
    const url = `/hx/forge/track/${trackId}/slot/${row}/${col}/upsert/`;
    const fd = new FormData(form);
    const resp = await fetch(url, {
      method: 'POST',
      body: fd,
      headers: { 'X-CSRFToken': csrftoken },
    });
    if (!resp.ok) {
      alert('Failed to save slot: ' + await resp.text());
      return;
    }
    const html = await resp.text();
    // Replace just the affected cell.
    const oldCell = document.querySelector(
      `[data-slot-cell][data-track-id="${trackId}"][data-row="${row}"][data-col="${col}"]`
    );
    if (oldCell) {
      const wrap = document.createElement('div');
      wrap.innerHTML = html.trim();
      const newCell = wrap.firstElementChild;
      if (newCell) oldCell.replaceWith(newCell);
    }
    closeSlotModal();
  });

  function initAll() {
    document.querySelectorAll('form[data-add-url]').forEach(bindAddForm);
    initStepSortables(document);
  }
  window.bindForgeAddForm = bindAddForm;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAll);
  } else {
    initAll();
  }
})();
