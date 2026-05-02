(function () {
  'use strict';

  function getCookie(name) {
    const m = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]+)'));
    return m ? decodeURIComponent(m[1]) : '';
  }
  const csrftoken = getCookie('csrftoken');

  // Validate [data-required] inputs (which may have been swapped to type=hidden
  // by the icon-picker init, so the browser skips its `required` check).
  // Reports validity on the corresponding contenteditable mirror, or alerts.
  function validateRequiredFields(form) {
    const fields = form.querySelectorAll('[data-required]');
    for (const input of fields) {
      const value = (input.value || '').trim();
      if (value) continue;
      const iconEditor = input.previousElementSibling;
      const richEditor = input.parentElement?.querySelector('.forge-rich-text-editor');
      if (iconEditor && iconEditor.dataset.forgeIconEditor === '1') {
        iconEditor.focus();
        iconEditor.classList.add('is-invalid');
        setTimeout(() => iconEditor.classList.remove('is-invalid'), 1500);
      } else if (richEditor) {
        richEditor.focus();
        richEditor.classList.add('is-invalid');
        setTimeout(() => richEditor.classList.remove('is-invalid'), 1500);
      } else {
        input.focus?.();
      }
      return false;
    }
    return true;
  }

  // Generic add-form handler — picks up any <form data-add-url="..."> and POSTs
  // it, then inserts the returned HTML into the target list.
  function bindAddForm(form) {
    if (form._addFormBound) return;
    form._addFormBound = true;
    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      if (!validateRequiredFields(form)) return;
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
      const newRow = list.lastElementChild;
      // Remove any open inline-form (since a row replaces it).
      list.querySelector('.forge-inline-form')?.remove();
      if (window.initForgeRichText) window.initForgeRichText(list);
      list.dispatchEvent(new CustomEvent('forge:row-replaced', { bubbles: true, detail: { row: newRow } }));
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

  // Renumber siblings after a row is removed. Called from the delete handler.
  function renumberAfterDelete(list) {
    if (!list) return;
    if (list.id === 'card-pile-list') {
      list.querySelectorAll('[data-row]').forEach((row, idx) => {
        const n = idx + 1;
        const label = row.querySelector('.card-pile-number');
        if (label) label.textContent = n;
        const numInput = row.querySelector('input[name="number"]');
        if (numInput) numInput.value = n;
      });
      return;
    }
    if (list.id === 'card-slot-list') {
      list.querySelectorAll('[data-row]').forEach((row, idx) => {
        const n = idx + 1;
        const label = row.querySelector('.card-slot-number');
        if (label) label.textContent = '#' + n;
        const numInput = row.querySelector('input[name="number"]');
        if (numInput) numInput.value = n;
      });
      return;
    }
    if (list.id === 'character-image-list') {
      list.querySelectorAll(':scope > [data-row]').forEach((row, idx) => {
        const label = row.querySelector('.character-image-label');
        if (label) label.textContent = 'Image ' + (idx + 1);
      });
      return;
    }
    if (list.id === 'content-box-list') {
      list.querySelectorAll(':scope > [data-row]').forEach((row, idx) => {
        const n = idx + 1;
        const label = row.querySelector('.content-box-section-label');
        if (label) label.textContent = 'Section ' + n;
        const orderInput = row.querySelector('input[name="order"]');
        if (orderInput) orderInput.value = n;
      });
      return;
    }
    // Phase-step lists: the per-phase containers and per-content-box step lists
    // both render their numbers via setup-step-marker SVGs.
    if (list.id?.startsWith('phase-step-list-') || list.matches('.content-box-steps[data-step-list]')) {
      [...list.children].filter(el => el.matches('[data-row]')).forEach((row, idx) => {
        const n = idx + 1;
        const img = row.querySelector(':scope > .setup-step-marker');
        if (img) {
          img.src = img.src.replace(/\d+\.svg$/, `${n % 10}.svg`);
          img.alt = String(n);
        }
      });
    }
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
    const actionList = btn.closest('.phase-step-action-list');
    const parentList = row?.parentElement;
    if (row) row.remove();
    renumberAfterDelete(parentList);
    if (actionList?.dataset.stepId) setActionTypeLock(actionList.dataset.stepId);
    parentList?.dispatchEvent(new CustomEvent('forge:row-replaced', { bubbles: true, detail: { row: null } }));
  });

  // Inline edit — submits a row's own form to its data-edit-url and replaces the row with the response.
  document.addEventListener('submit', async (ev) => {
    const form = ev.target.closest('form[data-edit-url]');
    if (!form) return;
    ev.preventDefault();
    if (!validateRequiredFields(form)) return;
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
    if (!row) return;
    // If the row has children that are themselves rows (e.g. a content box
    // containing phase-step rows), swap only the form so we don't blow away
    // unsaved child edits. Otherwise replace the whole row as before.
    const hasNestedRows = row.querySelector('[data-row]') !== null;
    const parent = row.parentElement;
    if (hasNestedRows) {
      const tmp = document.createElement('div');
      tmp.innerHTML = html;
      const newForm = tmp.querySelector('form[data-edit-url="' + url + '"]');
      if (newForm) {
        form.replaceWith(newForm);
        row.classList.remove('is-dirty');
        if (window.initForgeRichText) window.initForgeRichText(newForm);
        parent?.dispatchEvent(new CustomEvent('forge:row-replaced', { bubbles: true, detail: { row } }));
        return;
      }
      // fall through to full-row replace if the response shape was unexpected
    }
    row.insertAdjacentHTML('afterend', html);
    const newRow = row.nextElementSibling;
    row.remove();
    if (window.initForgeRichText) window.initForgeRichText(document);
    parent?.dispatchEvent(new CustomEvent('forge:row-replaced', { bubbles: true, detail: { row: newRow } }));
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
    if (isError) return;
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

  // Insert a nested row form for legend/scale parents. Templates render with
  // parent_pk=0 in URL segments and the data-target selector — rewrite both.
  function insertNestedRowForm(templateId, container, urlSegment, parentPk) {
    if (!container) return;
    const existing = container.querySelector('.forge-inline-form');
    if (existing) {
      existing.querySelector('input,textarea,select')?.focus();
      return;
    }
    const tpl = document.getElementById(templateId);
    if (!tpl) return;
    const segRe = new RegExp(urlSegment + '\\/0\\/', 'g');
    const html = tpl.innerHTML
      .replace(segRe, `${urlSegment}/${parentPk}/`)
      .replace(/data-(legend|scale)-id='0'/g, (_m, kind) => `data-${kind}-id='${parentPk}'`);
    const wrap = document.createElement('div');
    wrap.innerHTML = html.trim();
    const node = wrap.firstElementChild;
    container.appendChild(node);
    const form = node.querySelector('form');
    if (form) bindAddForm(form);
    initIconInputsIn(node);
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

  // Disable + Add Box / + Add Track / + Add Legend / + Add Scale while an
  // inline child form is open in this step. The forms include image pickers and
  // contenteditable mirrors, so letting two open at once would split focus and
  // confuse the picker state. Only block when the OPEN form lives directly in
  // .phase-step-children (top-level child) — nested legend/scale row forms are fine.
  function setStepChildAddLock(stepPk) {
    const list = document.querySelector(`.phase-step-children[data-step-id="${stepPk}"]`);
    const formOpen = !!list && [...list.children].some(el => el.classList?.contains('forge-inline-form'));
    document.querySelectorAll(
      `[data-add-box][data-step-id="${stepPk}"],` +
      `[data-add-track][data-step-id="${stepPk}"],` +
      `[data-add-legend][data-step-id="${stepPk}"],` +
      `[data-add-scale][data-step-id="${stepPk}"]`
    ).forEach(btn => {
      btn.disabled = formOpen;
      btn.classList.toggle('disabled', formOpen);
    });
  }

  function setActionTypeLock(stepPk) {
    const list = document.querySelector(`.phase-step-action-list[data-step-id="${stepPk}"]`);
    const formOpen = !!list?.querySelector('.forge-inline-form');
    document.querySelectorAll(`[data-add-action][data-step-id="${stepPk}"]`).forEach(btn => {
      btn.disabled = formOpen;
      btn.classList.toggle('disabled', formOpen);
    });
    const sel = document.querySelector(`.phase-step-action-type[data-step-id="${stepPk}"]`);
    if (!sel) return;
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
      const childList = inline?.closest('.phase-step-children');
      const childStepPk = childList?.dataset.stepId;
      const parent = inline?.parentElement;
      inline?.remove();
      if (stepPk) setActionTypeLock(stepPk);
      if (childStepPk) setStepChildAddLock(childStepPk);
      parent?.dispatchEvent(new CustomEvent('forge:row-replaced', { bubbles: true, detail: { row: null } }));
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
      if (actionBtn.disabled) return;
      const stepPk = actionBtn.dataset.stepId;
      const list = document.querySelector(`.phase-step-action-list[data-step-id="${stepPk}"]`);
      // Action form is fetched per-step (filtered by step.action_type) instead
      // of cloned from a static template.
      insertActionInlineForm(list, stepPk);
      return;
    }
    const boxBtn = ev.target.closest('[data-add-box]');
    if (boxBtn) {
      if (boxBtn.disabled) return;
      const stepPk = boxBtn.dataset.stepId;
      const list = document.querySelector(`.phase-step-children[data-step-id="${stepPk}"]`);
      insertInlineForm('forge-box-form-template', list, stepPk);
      setStepChildAddLock(stepPk);
      return;
    }
    const trackBtn = ev.target.closest('[data-add-track]');
    if (trackBtn) {
      if (trackBtn.disabled) return;
      const stepPk = trackBtn.dataset.stepId;
      const list = document.querySelector(`.phase-step-children[data-step-id="${stepPk}"]`);
      insertInlineForm('forge-track-form-template', list, stepPk);
      setStepChildAddLock(stepPk);
      return;
    }
    const legendBtn = ev.target.closest('[data-add-legend]');
    if (legendBtn) {
      if (legendBtn.disabled) return;
      const stepPk = legendBtn.dataset.stepId;
      const list = document.querySelector(`.phase-step-children[data-step-id="${stepPk}"]`);
      insertInlineForm('forge-legend-form-template', list, stepPk);
      setStepChildAddLock(stepPk);
      return;
    }
    const scaleBtn = ev.target.closest('[data-add-scale]');
    if (scaleBtn) {
      if (scaleBtn.disabled) return;
      const stepPk = scaleBtn.dataset.stepId;
      const list = document.querySelector(`.phase-step-children[data-step-id="${stepPk}"]`);
      insertInlineForm('forge-scale-form-template', list, stepPk);
      setStepChildAddLock(stepPk);
      return;
    }
    const legendRowBtn = ev.target.closest('[data-add-legend-row]');
    if (legendRowBtn) {
      const legendPk = legendRowBtn.dataset.legendId;
      const container = document.querySelector(`[data-legend-entries][data-legend-id="${legendPk}"]`);
      insertNestedRowForm('forge-legend-row-form-template', container, 'legend', legendPk);
      return;
    }
    const scaleRowBtn = ev.target.closest('[data-add-scale-row]');
    if (scaleRowBtn) {
      const scalePk = scaleRowBtn.dataset.scaleId;
      const scaleRow = scaleRowBtn.closest('.scale-row');
      const container = scaleRow?.querySelector(`[data-scale-entries][data-scale-id="${scalePk}"]`);
      const tpl = scaleRow?.querySelector('template[data-scale-row-template]');
      if (!container || !tpl) return;
      const node = tpl.content.firstElementChild.cloneNode(true);
      container.appendChild(node);
      initIconInputsIn(node);
      scaleRow.classList.add('is-dirty');
      node.querySelector('input[name="range"]')?.focus();
      return;
    }
    const removeScaleRowBtn = ev.target.closest('[data-remove-scale-row]');
    if (removeScaleRowBtn) {
      const row = removeScaleRowBtn.closest('[data-scale-row]');
      const scaleRow = removeScaleRowBtn.closest('.scale-row');
      row?.remove();
      scaleRow?.classList.add('is-dirty');
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
          })).filter(o => ['box', 'track', 'legend', 'scale'].includes(o.kind));
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
    // Re-enable + Add Box / + Add Track once the inline form was consumed.
    const childList = ev.target.closest?.('.phase-step-children') || ev.target;
    const childStepPk = childList?.dataset?.stepId;
    if (childStepPk && childList.classList?.contains('phase-step-children')) {
      setStepChildAddLock(childStepPk);
    }
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
    const panel = sel.closest('.phase-step-action-header');
    const url = panel?.dataset.actionTypeUrl;
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
      return;
    }
    const html = await res.text();
    if (panel && html) {
      const tmp = document.createElement('div');
      tmp.innerHTML = html.trim();
      const fresh = tmp.querySelector('.phase-step-action-header');
      if (fresh) {
        // Preserve the user's open/closed state — server re-renders default to
        // hidden when no actions exist, but we don't want to collapse the panel
        // out from under them mid-edit.
        if (!panel.hasAttribute('hidden')) fresh.removeAttribute('hidden');
        panel.replaceWith(fresh);
      }
    }
  });

  // Step cost-image clear button: set the Django ClearableFileInput's clear
  // flag and submit the form.
  document.addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-cost-image-clear]');
    if (!btn) return;
    const form = btn.closest('form[data-step-cost-image-url]');
    if (!form) return;
    const flag = form.querySelector('[data-cost-image-clear-flag]');
    if (flag) flag.value = 'on';
    const fileInput = form.querySelector('[data-cost-image-input]');
    if (fileInput) fileInput.value = '';
    if (form.requestSubmit) form.requestSubmit();
    else form.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
  });

  // Step cost-image form: AJAX submit and replace ONLY the form itself, not
  // the surrounding action rows (which may have unsaved edits).
  document.addEventListener('submit', async (ev) => {
    const form = ev.target.closest('form[data-step-cost-image-url]');
    if (!form) return;
    ev.preventDefault();
    const url = form.dataset.stepCostImageUrl;
    const fd = new FormData(form);
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'X-CSRFToken': csrftoken },
      body: fd,
    });
    if (!res.ok) {
      alert('Save failed: ' + await res.text());
      return;
    }
    form.classList.remove('is-dirty');
    const html = await res.text();
    if (html) {
      const tmp = document.createElement('div');
      tmp.innerHTML = html.trim();
      const freshForm = tmp.querySelector('form[data-step-cost-image-url]');
      const parent = form.parentElement;
      if (freshForm) {
        form.replaceWith(freshForm);
      }
      parent?.dispatchEvent(new CustomEvent('forge:row-replaced', { bubbles: true, detail: { row: freshForm || null } }));
    }
  });

  // ---------- Slot modal ----------
  const slotModal = document.getElementById('forge-slot-modal');
  let slotModalState = null;

  function openSlotModal({ trackId, row, col, content, bgUrl, numRows, numCols }) {
    if (!slotModal) return;
    slotModalState = { trackId, row, col };
    const r = parseInt(row, 10);
    const c = parseInt(col, 10);
    const nRows = parseInt(numRows, 10);
    const nCols = parseInt(numCols, 10);
    let title;
    if (nRows === 1 && nCols === 1) {
      title = 'Edit slot';
    } else if (nRows === 1) {
      title = `Edit slot ${c + 1}`;
    } else if (nCols === 1) {
      title = `Edit slot ${r + 1}`;
    } else {
      title = `Edit slot (${r + 1}, ${c + 1})`;
    }
    slotModal.querySelector('#forge-slot-modal-title').textContent = title;
    const tray = slotModal.querySelector('[data-slot-tray]');
    tray.innerHTML = '';
    const keywords = (content || '').split('|').map(k => k.trim()).filter(Boolean);
    keywords.forEach(k => addTrayItem(k));
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
        bgUrl: cell.dataset.bgUrl,
        numRows: cell.dataset.numRows,
        numCols: cell.dataset.numCols,
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

  // ---------- Cardboard track editor ----------
  // Inputs around the grid (column headers above each column, row titles to the
  // left of each row, dividers between columns, +/- ghost rows/cols) are pure
  // DOM — they get serialised into the track form's hidden fields on submit.
  function trackEditorMarkDirty(form) {
    form.closest('[data-row]')?.classList.add('is-dirty');
  }

  function trackEditorSerialise(form) {
    const cols = parseInt(form.querySelector('[data-track-num-columns]').value, 10) || 1;
    const rows = parseInt(form.querySelector('[data-track-num-rows]').value, 10) || 1;

    const headerVals = Array.from({ length: cols }, (_, i) => {
      const el = form.querySelector(`input[data-col-header="${i}"]`)
        || form.querySelector(`[data-col-header="${i}"]`);
      return el ? (el.value !== undefined ? el.value : '') : '';
    });
    const anyHeader = headerVals.some(v => v !== '');
    form.querySelector('[data-track-column-headers]').value = anyHeader ? headerVals.join('|') : '';

    const titleVals = Array.from({ length: rows }, (_, i) => {
      const el = form.querySelector(`input[data-row-title="${i}"]`)
        || form.querySelector(`[data-row-title="${i}"]`);
      return el ? (el.value !== undefined ? el.value : '') : '';
    });
    const anyTitle = titleVals.some(v => v !== '');
    form.querySelector('[data-track-row-titles]').value = anyTitle ? titleVals.join('|') : '';

    const dividerCols = [];
    form.querySelectorAll('.track-editor__divider.is-active').forEach(d => {
      const c = parseInt(d.dataset.dividerCol, 10);
      if (!Number.isNaN(c) && c < cols - 1) dividerCols.push(c);
    });
    form.querySelector('[data-track-column-dividers]').value = dividerCols.join(',');
  }

  function trackEditorAddColumn(form) {
    const numColInput = form.querySelector('[data-track-num-columns]');
    const oldCount = parseInt(numColInput.value, 10) || 0;
    const newIdx = oldCount;
    numColInput.value = oldCount + 1;

    const headerCells = form.querySelector('[data-track-header-cells]');
    if (oldCount > 0) {
      const spacer = document.createElement('button');
      spacer.type = 'button';
      spacer.className = 'track-editor__divider-spacer';
      spacer.dataset.dividerSpacer = newIdx - 1;
      spacer.title = `Toggle divider after column ${newIdx - 1}`;
      spacer.setAttribute('aria-label', `Toggle divider after column ${newIdx - 1}`);
      headerCells.appendChild(spacer);
    }
    const headerInput = document.createElement('input');
    headerInput.type = 'text';
    headerInput.className = 'form-control form-control-sm track-editor__col-header track-editor__col-header--ghost';
    headerInput.dataset.colHeader = newIdx;
    headerInput.dataset.trackImageInput = '';
    headerInput.disabled = true;
    headerInput.dataset.ghost = '1';
    headerCells.appendChild(headerInput);

    const grid = form.querySelector('.track-editor__grid');
    const trackType = grid.classList.contains('track-grid--token') ? 'token' : 'building';
    grid.querySelectorAll('[data-grid-row]').forEach(rowEl => {
      const r = rowEl.dataset.gridRow;
      // If row already has a divider before the new column position, leave it.
      if (oldCount > 0) {
        const lastChild = rowEl.lastElementChild;
        const beforeNewCol = lastChild && lastChild.matches('.track-cell') ? lastChild : null;
        if (beforeNewCol) {
          const div = document.createElement('button');
          div.type = 'button';
          div.className = 'track-editor__divider track-editor__divider--ghost';
          div.dataset.dividerCol = newIdx - 1;
          div.dataset.ghost = '1';
          div.disabled = true;
          rowEl.appendChild(div);
        }
      }
      const ghost = document.createElement('span');
      ghost.className = `track-cell track-cell--${trackType} track-cell--ghost`;
      ghost.dataset.ghost = '1';
      ghost.dataset.row = r;
      ghost.dataset.col = newIdx;
      rowEl.appendChild(ghost);
    });

    form.querySelector('.track-editor').style.setProperty('--track-cols', String(oldCount + 1));
  }

  function trackEditorRemoveColumn(form) {
    const numColInput = form.querySelector('[data-track-num-columns]');
    const oldCount = parseInt(numColInput.value, 10) || 0;
    if (oldCount <= 1) return;

    // Always remove the highest column to keep indices stable for remaining cells.
    const removeIdx = oldCount - 1;
    numColInput.value = oldCount - 1;

    form.querySelector(`[data-col-header="${removeIdx}"]`)?.remove();
    form.querySelectorAll(`[data-track-header-cells] [data-divider-spacer="${removeIdx - 1}"]`).forEach(s => s.remove());
    form.querySelectorAll('.track-editor__grid [data-grid-row]').forEach(rowEl => {
      // Remove last cell + the divider before it (if any).
      const last = rowEl.lastElementChild;
      if (last) last.remove();
      const newLast = rowEl.lastElementChild;
      if (newLast && newLast.classList.contains('track-editor__divider')) newLast.remove();
    });
    form.querySelector('.track-editor').style.setProperty('--track-cols', String(oldCount - 1));
  }

  function trackEditorAddRow(form) {
    const numRowInput = form.querySelector('[data-track-num-rows]');
    const oldCount = parseInt(numRowInput.value, 10) || 0;
    const newIdx = oldCount;
    numRowInput.value = oldCount + 1;
    const cols = parseInt(form.querySelector('[data-track-num-columns]').value, 10) || 1;

    const rowTitleCol = form.querySelector('[data-track-row-title-col]');
    const rtInput = document.createElement('input');
    rtInput.type = 'text';
    rtInput.className = 'form-control form-control-sm track-editor__row-title track-editor__row-title--ghost';
    rtInput.dataset.rowTitle = newIdx;
    rtInput.dataset.trackImageInput = '';
    rtInput.disabled = true;
    rtInput.dataset.ghost = '1';
    rowTitleCol.appendChild(rtInput);

    const grid = form.querySelector('.track-editor__grid');
    const trackType = grid.classList.contains('track-grid--token') ? 'token' : 'building';
    const rowEl = document.createElement('div');
    rowEl.className = 'track-editor__grid-row';
    rowEl.dataset.gridRow = newIdx;
    rowEl.dataset.ghost = '1';
    // Mirror dividers from previous row positions so the layout stays consistent.
    const dividerCols = new Set();
    form.querySelectorAll('.track-editor__divider.is-active').forEach(d => {
      const c = parseInt(d.dataset.dividerCol, 10);
      if (!Number.isNaN(c)) dividerCols.add(c);
    });
    for (let c = 0; c < cols; c++) {
      const ghost = document.createElement('span');
      ghost.className = `track-cell track-cell--${trackType} track-cell--ghost`;
      ghost.dataset.ghost = '1';
      ghost.dataset.row = newIdx;
      ghost.dataset.col = c;
      rowEl.appendChild(ghost);
      if (c < cols - 1) {
        const div = document.createElement('button');
        div.type = 'button';
        div.className = 'track-editor__divider track-editor__divider--ghost';
        div.dataset.dividerCol = c;
        if (dividerCols.has(c)) div.classList.add('is-active');
        div.dataset.ghost = '1';
        div.disabled = true;
        rowEl.appendChild(div);
      }
    }
    grid.appendChild(rowEl);
  }

  function trackEditorRemoveRow(form) {
    const numRowInput = form.querySelector('[data-track-num-rows]');
    const oldCount = parseInt(numRowInput.value, 10) || 0;
    if (oldCount <= 1) return;
    const removeIdx = oldCount - 1;
    numRowInput.value = oldCount - 1;
    form.querySelector(`[data-row-title="${removeIdx}"]`)?.remove();
    form.querySelector(`.track-editor__grid [data-grid-row="${removeIdx}"]`)?.remove();
  }

  document.addEventListener('click', (ev) => {
    const editor = ev.target.closest('[data-track-editor]');
    if (!editor) return;

    if (ev.target.closest('[data-track-add-col]')) {
      ev.preventDefault();
      trackEditorAddColumn(editor);
      trackEditorMarkDirty(editor);
      return;
    }
    if (ev.target.closest('[data-track-add-row]')) {
      ev.preventDefault();
      trackEditorAddRow(editor);
      trackEditorMarkDirty(editor);
      return;
    }
    const removeColBtn = ev.target.closest('[data-track-remove-col]');
    if (removeColBtn && !removeColBtn.disabled) {
      ev.preventDefault();
      trackEditorRemoveColumn(editor);
      trackEditorMarkDirty(editor);
      return;
    }
    const removeRowBtn = ev.target.closest('[data-track-remove-row]');
    if (removeRowBtn && !removeRowBtn.disabled) {
      ev.preventDefault();
      trackEditorRemoveRow(editor);
      trackEditorMarkDirty(editor);
      return;
    }
    const spacerBtn = ev.target.closest('.track-editor__divider-spacer');
    if (spacerBtn) {
      ev.preventDefault();
      const colIdx = spacerBtn.dataset.dividerSpacer;
      const target = !spacerBtn.classList.contains('is-active');
      editor.querySelectorAll(`.track-editor__divider[data-divider-col="${colIdx}"]`).forEach(d => {
        d.classList.toggle('is-active', target);
      });
      editor.querySelectorAll(`.track-editor__divider-spacer[data-divider-spacer="${colIdx}"]`).forEach(s => {
        s.classList.toggle('is-active', target);
      });
      trackEditorMarkDirty(editor);
      return;
    }
    const dividerBtn = ev.target.closest('.track-editor__divider');
    if (dividerBtn && !dividerBtn.disabled) {
      ev.preventDefault();
      const colIdx = dividerBtn.dataset.dividerCol;
      const target = !dividerBtn.classList.contains('is-active');
      editor.querySelectorAll(`.track-editor__divider[data-divider-col="${colIdx}"]`).forEach(d => {
        d.classList.toggle('is-active', target);
      });
      editor.querySelectorAll(`.track-editor__divider-spacer[data-divider-spacer="${colIdx}"]`).forEach(s => {
        s.classList.toggle('is-active', target);
      });
      trackEditorMarkDirty(editor);
    }
  });

  // Header position toggle: just update the hidden field and a CSS class so CSS
  // re-orders the header strip via flex/grid `order`.
  document.addEventListener('change', (ev) => {
    if (ev.target.matches('[data-header-pos-toggle] [name="header_position_ui"]')) {
      const editor = ev.target.closest('[data-track-editor]');
      if (!editor) return;
      const value = ev.target.value;
      editor.querySelector('[data-track-header-position]').value = value;
      const root = editor.querySelector('.track-editor');
      root.classList.remove('track-editor--hp-above', 'track-editor--hp-below');
      root.classList.add(`track-editor--hp-${value}`);
      trackEditorMarkDirty(editor);
      return;
    }
    if (ev.target.matches('[data-rt-orientation-toggle] [name="rt_orient_ui"]')) {
      const editor = ev.target.closest('[data-track-editor]');
      if (!editor) return;
      const value = ev.target.value;
      editor.querySelector('[data-track-row-title-orientation]').value = value;
      const root = editor.querySelector('.track-editor');
      root.classList.remove('track-editor--rt-horizontal', 'track-editor--rt-vertical');
      root.classList.add(`track-editor--rt-${value}`);
      trackEditorMarkDirty(editor);
    }
  });

  // ---------- Inline icon picker ----------
  // Each [data-track-image-input] gets a contenteditable mounted in front of it
  // so picker-inserted icons render as real <img>. The original <input> stays
  // as the form's source of truth (hidden, kept in sync). On disk and over the
  // wire we still use `{{ key }}` tokens so the PDF engine and the
  // format_forge_text filter work unchanged.

  // Forms that host the inline icon picker: track grid editors *and* generic
  // single-line inputs that opt in via [data-forge-icon-form] (e.g. scale rows).
  const PICKER_FORM_SELECTOR = 'form[data-track-editor], form[data-forge-icon-form]';
  function pickerFormFor(node) {
    return node && node.closest ? node.closest(PICKER_FORM_SELECTOR) : null;
  }

  let INLINE_IMAGES_CACHE = null;
  function getInlineImagesMap() {
    if (INLINE_IMAGES_CACHE !== null) return INLINE_IMAGES_CACHE;
    const node = document.getElementById('forge-inline-images');
    let parsed = {};
    try { parsed = node ? JSON.parse(node.textContent) : {}; }
    catch (e) { parsed = {}; }
    // Be defensive: json_script can produce "", null, or a non-object if the
    // view didn't pass the map. Fall back to scraping the picker buttons,
    // which carry both the key and the image URL on them.
    if (!parsed || typeof parsed !== 'object') parsed = {};
    if (!Object.keys(parsed).length) {
      document.querySelectorAll('.forge-icon-picker__btn').forEach((btn) => {
        const k = btn.dataset.iconKey;
        const img = btn.querySelector('img');
        if (k && img && img.src) parsed[k] = img.src;
      });
    }
    INLINE_IMAGES_CACHE = parsed;
    return INLINE_IMAGES_CACHE;
  }

  function escapeHtml(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function tokensToHtml(value) {
    if (!value) return '';
    const images = getInlineImagesMap();
    return escapeHtml(value).replace(/\{\{\s*([\w-]+)\s*\}\}/g, (m, key) => {
      const url = images[key];
      if (!url) return m;
      return '<img data-forge-image="' + escapeHtml(key) + '" src="' + escapeHtml(url) +
        '" alt="' + escapeHtml(key) + '" class="inline-icon">';
    });
  }

  function htmlToTokens(root) {
    let out = '';
    function walk(node) {
      if (node.nodeType === Node.TEXT_NODE) {
        out += (node.nodeValue || '').replace(/\u200B/g, '');
        return;
      }
      if (node.nodeType !== Node.ELEMENT_NODE) return;
      if (node.nodeName === 'IMG') {
        const key = node.getAttribute('data-forge-image');
        if (key) out += '{{ ' + key + ' }}';
        return;
      }
      // Enter/<br>/<div> shouldn't reach here (Enter is blocked) but be safe.
      if (node.nodeName === 'BR') return;
      for (const c of node.childNodes) walk(c);
    }
    for (const c of root.childNodes) walk(c);
    return out;
  }

  function insertPlainTextAtCaret(text) {
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0) return;
    const range = sel.getRangeAt(0);
    range.deleteContents();
    const node = document.createTextNode(text);
    range.insertNode(node);
    const after = document.createRange();
    after.setStartAfter(node);
    after.collapse(true);
    sel.removeAllRanges();
    sel.addRange(after);
  }

  // Per-form picker state: which contenteditable is active, the saved range
  // for caret restore, and whether the visualViewport listener is attached.
  const pickerState = new WeakMap(); // form -> { activeEditor, savedRange, vvHandler }

  function getPickerState(form) {
    let st = pickerState.get(form);
    if (!st) { st = { activeEditor: null, savedRange: null, vvHandler: null }; pickerState.set(form, st); }
    return st;
  }

  function isMobileLike() {
    return (window.matchMedia && window.matchMedia('(pointer: coarse)').matches) || window.innerWidth < 768;
  }

  function positionPicker(form, picker, editor) {
    const mobile = isMobileLike();
    picker.classList.toggle('forge-icon-picker--keyboard', mobile);
    if (mobile) {
      // Pinned to bottom of visualViewport. Initial bottom = 0; viewport
      // listener fine-tunes when the soft keyboard opens.
      picker.style.position = 'fixed';
      picker.style.left = '0';
      picker.style.right = '0';
      picker.style.top = 'auto';
      picker.style.bottom = '0';
      attachViewportListener(form, picker);
    } else {
      detachViewportListener(form);
      const rect = editor.getBoundingClientRect();
      const formRect = form.getBoundingClientRect();
      // Position relative to the form (form must be position: relative; CSS sets that).
      const top = (rect.bottom - formRect.top) + 4;
      let left = rect.left - formRect.left;
      // Clamp so the popover doesn't escape the form's right edge.
      const panel = picker.querySelector('.forge-icon-picker__panel');
      const panelW = panel ? panel.offsetWidth || 280 : 280;
      const maxLeft = formRect.width - panelW - 4;
      if (left > maxLeft) left = Math.max(4, maxLeft);
      if (left < 4) left = 4;
      picker.style.position = 'absolute';
      picker.style.top = top + 'px';
      picker.style.left = left + 'px';
      picker.style.right = '';
      picker.style.bottom = '';
    }
  }

  function attachViewportListener(form, picker) {
    const st = getPickerState(form);
    if (st.vvHandler || !window.visualViewport) return;
    let raf = 0;
    const update = () => {
      raf = 0;
      const vv = window.visualViewport;
      const offset = Math.max(0, window.innerHeight - vv.height - vv.offsetTop);
      picker.style.bottom = offset + 'px';
    };
    st.vvHandler = () => { if (!raf) raf = requestAnimationFrame(update); };
    window.visualViewport.addEventListener('resize', st.vvHandler);
    window.visualViewport.addEventListener('scroll', st.vvHandler);
    update();
  }

  function detachViewportListener(form) {
    const st = getPickerState(form);
    if (!st.vvHandler || !window.visualViewport) return;
    window.visualViewport.removeEventListener('resize', st.vvHandler);
    window.visualViewport.removeEventListener('scroll', st.vvHandler);
    st.vvHandler = null;
  }

  function showPicker(form, editor) {
    const picker = form.querySelector('[data-forge-icon-picker]');
    if (!picker) return;
    const st = getPickerState(form);
    st.activeEditor = editor;
    picker.hidden = false;
    positionPicker(form, picker, editor);
  }

  function hidePicker(form) {
    const picker = form.querySelector('[data-forge-icon-picker]');
    if (!picker) return;
    picker.hidden = true;
    detachViewportListener(form);
    const st = getPickerState(form);
    st.activeEditor = null;
    st.savedRange = null;
  }

  function syncEditorToInput(editor, input) {
    const next = htmlToTokens(editor);
    if (input.value !== next) {
      input.value = next;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }

  function initForgeIconInput(input) {
    if (input.dataset.forgeIconInit === '1') return;
    if (input.disabled || input.dataset.ghost) return;
    input.dataset.forgeIconInit = '1';

    const editor = document.createElement('div');
    editor.contentEditable = 'true';
    editor.className = input.className + ' forge-icon-input';
    // Copy inline style (min-width:0, max-width, etc) so the contenteditable
    // div sizes the same way as the original input inside flex containers.
    const inputStyle = input.getAttribute('style');
    if (inputStyle) editor.setAttribute('style', inputStyle);
    editor.setAttribute('role', 'textbox');
    editor.setAttribute('aria-label', input.getAttribute('aria-label') || input.name || '');
    editor.dataset.forgeIconEditor = '1';
    // Mirror the data-* attributes that other JS (e.g. trackEditorSerialise)
    // queries — col-header, row-title, track-header-title, track-image-input.
    if (input.dataset.colHeader !== undefined) editor.dataset.colHeader = input.dataset.colHeader;
    if (input.dataset.rowTitle !== undefined) editor.dataset.rowTitle = input.dataset.rowTitle;
    if (input.dataset.trackHeaderTitle !== undefined) editor.dataset.trackHeaderTitle = input.dataset.trackHeaderTitle;
    editor.dataset.trackImageInput = '';
    editor.innerHTML = tokensToHtml(input.value);

    input.parentNode.insertBefore(editor, input);
    input.type = 'hidden';
    input.removeAttribute('data-track-image-input');
    // Keep a back-pointer so the editor's input handler can find its hidden input.
    editor._forgeHiddenInput = input;

    // Block Enter — these are single-line fields. Allow form submit on Enter
    // by blurring; the existing dirty-form save flow takes over from there.
    editor.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter') {
        ev.preventDefault();
        editor.blur();
      } else if (ev.key === 'Escape') {
        const form = pickerFormFor(editor);
        if (form) hidePicker(form);
        editor.blur();
      }
    });

    editor.addEventListener('paste', (ev) => {
      ev.preventDefault();
      const text = (ev.clipboardData || window.clipboardData).getData('text/plain') || '';
      // Single-line: collapse newlines to spaces.
      insertPlainTextAtCaret(text.replace(/\r?\n/g, ' '));
      editor.dispatchEvent(new Event('input', { bubbles: true }));
    });

    editor.addEventListener('input', () => {
      syncEditorToInput(editor, input);
    });
  }

  // selectionchange runs at the document level — capture the savedRange for
  // the active editor so picker clicks can restore it.
  document.addEventListener('selectionchange', () => {
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0) return;
    const range = sel.getRangeAt(0);
    let node = range.startContainer;
    while (node && node.nodeType !== Node.ELEMENT_NODE) node = node.parentNode;
    const editor = node && node.closest && node.closest('[data-forge-icon-editor="1"]');
    if (!editor) return;
    const form = pickerFormFor(editor);
    if (!form) return;
    const st = getPickerState(form);
    if (st.activeEditor === editor) st.savedRange = range.cloneRange();
  });

  document.addEventListener('focusin', (ev) => {
    const editor = ev.target.closest('[data-forge-icon-editor="1"]');
    if (!editor) return;
    const form = pickerFormFor(editor);
    if (!form) return;
    showPicker(form, editor);
  });

  document.addEventListener('focusout', (ev) => {
    const editor = ev.target.closest('[data-forge-icon-editor="1"]');
    if (!editor) return;
    const form = pickerFormFor(editor);
    if (!form) return;
    // Defer so a click moving focus into the picker (or another editor) doesn't
    // close it. If after the tick neither the picker nor another editor has focus,
    // hide the picker.
    setTimeout(() => {
      const active = document.activeElement;
      if (active && active.closest && active.closest('[data-forge-icon-picker]')) return;
      const otherEditor = active && active.closest && active.closest('[data-forge-icon-editor="1"]');
      if (otherEditor && pickerFormFor(otherEditor) === form) return;
      hidePicker(form);
    }, 0);
  });

  // Picker buttons: keep focus on the editor (no blur), insert at savedRange.
  document.addEventListener('mousedown', (ev) => {
    const picker = ev.target.closest('[data-forge-icon-picker]');
    if (picker) ev.preventDefault();
  });
  document.addEventListener('touchstart', (ev) => {
    const picker = ev.target.closest('[data-forge-icon-picker]');
    if (picker) ev.preventDefault();
  }, { passive: false });

  document.addEventListener('click', (ev) => {
    const btn = ev.target.closest('.forge-icon-picker__btn');
    if (!btn) return;
    ev.preventDefault();
    const form = pickerFormFor(btn);
    if (!form) return;
    const st = getPickerState(form);
    const editor = st.activeEditor;
    if (!editor) return;
    const key = btn.dataset.iconKey;
    const url = getInlineImagesMap()[key];
    if (!url) return;

    // Restore caret if it drifted out (focus was momentarily on the button).
    let range = st.savedRange;
    if (!range || !editor.contains(range.startContainer) || !editor.contains(range.endContainer)) {
      range = document.createRange();
      range.selectNodeContents(editor);
      range.collapse(false);
    }
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);

    const img = document.createElement('img');
    img.setAttribute('data-forge-image', key);
    img.src = url;
    img.alt = key;
    img.className = 'inline-icon';
    range.deleteContents();
    range.insertNode(img);
    const after = document.createRange();
    after.setStartAfter(img);
    after.collapse(true);
    sel.removeAllRanges();
    sel.addRange(after);
    st.savedRange = after.cloneRange();
    editor.focus();
    editor.dispatchEvent(new Event('input', { bubbles: true }));
  });

  function initIconInputsIn(root) {
    (root || document).querySelectorAll('[data-track-image-input]').forEach(initForgeIconInput);
  }

  // Re-init when a row gets replaced after save (event dispatched by the
  // inline-edit submit handler above). Also re-init on the new row whenever
  // ghosts are promoted to real inputs. Avoid a document-wide MutationObserver
  // — picker-driven DOM mutations would re-fire it on every keystroke.
  document.addEventListener('forge:row-replaced', (ev) => {
    const row = ev.detail && ev.detail.row;
    if (row) initIconInputsIn(row);
  });

  // Reposition desktop popover on window resize / scroll.
  window.addEventListener('resize', () => {
    document.querySelectorAll('[data-forge-icon-picker]:not([hidden])').forEach((p) => {
      const form = pickerFormFor(p);
      const st = form && pickerState.get(form);
      if (form && st && st.activeEditor) positionPicker(form, p, st.activeEditor);
    });
  });

  // Serialise per-cell inputs into hidden track fields right before submit.
  document.addEventListener('submit', (ev) => {
    const form = ev.target.closest('form[data-track-editor]');
    if (form) trackEditorSerialise(form);
  }, true);

  // ---------- Unsaved-changes banner + beforeunload ----------
  // Watches the page for [data-row].is-dirty rows (set by the input listener
  // above when the user types in a [data-dirty-form]). Shows a sticky banner
  // with a count and warns on navigation. Clicking the banner scrolls to the
  // first dirty row and pulses it so the user can find which Save to click.
  function initDirtyBanner() {
    if (window.__forgeDirtyBannerBound) return;
    window.__forgeDirtyBannerBound = true;

    const banner = document.createElement('button');
    banner.type = 'button';
    banner.id = 'forge-unsaved-banner';
    banner.className = 'forge-unsaved-banner';
    banner.hidden = true;
    banner.setAttribute('aria-live', 'polite');
    document.body.appendChild(banner);

    function dirtyRows() {
      return document.querySelectorAll('[data-row].is-dirty');
    }

    function update() {
      const rows = dirtyRows();
      const count = rows.length;
      if (!count) {
        banner.hidden = true;
        return;
      }
      banner.hidden = false;
      banner.textContent = count === 1
        ? '1 unsaved section — click to find it'
        : count + ' unsaved sections — click to find them';
    }

    banner.addEventListener('click', () => {
      const first = dirtyRows()[0];
      if (!first) return;
      first.scrollIntoView({ behavior: 'smooth', block: 'center' });
      first.classList.remove('forge-dirty-pulse');
      // force reflow so the animation re-runs even if class lingers
      void first.offsetWidth;
      first.classList.add('forge-dirty-pulse');
      setTimeout(() => first.classList.remove('forge-dirty-pulse'), 1600);
    });

    // Update on dirty-mark (input inside a [data-dirty-form]) and on row
    // replacement (forge:row-replaced fires after a successful save). Both are
    // already dispatched by the existing handlers — no broad observer needed.
    let scheduled = false;
    function scheduleUpdate() {
      if (scheduled) return;
      scheduled = true;
      requestAnimationFrame(() => { scheduled = false; update(); });
    }
    document.addEventListener('input', (ev) => {
      if (ev.target.closest('form[data-dirty-form]')) scheduleUpdate();
    });
    document.addEventListener('forge:row-replaced', scheduleUpdate);

    window.addEventListener('beforeunload', (ev) => {
      if (!dirtyRows().length) return;
      ev.preventDefault();
      ev.returnValue = '';
      return '';
    });

    update();
  }

  function initAll() {
    document.querySelectorAll('form[data-add-url]').forEach(bindAddForm);
    initStepSortables(document);
    initIconInputsIn(document);
    initDirtyBanner();
  }
  window.bindForgeAddForm = bindAddForm;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAll);
  } else {
    initAll();
  }
})();
