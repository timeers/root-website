(function () {
  'use strict';

  const SCALE = 60; // px per inch

  const canvas = document.getElementById('forge-preview-canvas');
  const inputsContainer = document.getElementById('forge-preview-inputs');
  if (!canvas || !inputsContainer) return;

  // Editable elements (their coords get hidden inputs and -- in later steps --
  // drag/resize handles). Decorative elements ride along with their parent.
  const EDITABLE_KINDS = new Set([
    'phase_box', 'content_box', 'card_pile', 'decree',
  ]);

  // Decorative kinds drawn but never directly editable.
  const DECOR_KINDS = new Set([
    'phase_header_bar', 'phase_step',
    'header_bar', 'header_color_bar', 'header_ability', 'header_flavor', 'header_crafted',
    'card_slot',
  ]);

  function parsePayloads() {
    try {
      return JSON.parse(canvas.dataset.payloads || '{}');
    } catch (e) {
      console.error('forge_preview: failed to parse payloads', e);
      return {};
    }
  }

  const payloads = parsePayloads();
  let activeMode = canvas.dataset.activeMode || 'horizontal';

  function elementKey(el) {
    if (el.kind === 'phase_box') return 'phase_box';
    if (el.kind === 'decree') return 'decree';
    if (el.kind === 'content_box') return `content_box_${el.id}`;
    if (el.kind === 'card_pile') return `card_pile_${el.id}`;
    return null;
  }

  // Render Forge markup (data-forge="header") so phase-step previews preserve
  // the size differences the PDF shows.
  function renderForgeMarkup(text) {
    if (!text) return '';
    // Use DOMParser to safely transform <span data-forge="header"> spans.
    const tmpl = document.createElement('template');
    tmpl.innerHTML = text;
    tmpl.content.querySelectorAll('span[data-forge="header"]').forEach((span) => {
      span.classList.add('forge-header');
    });
    tmpl.content.querySelectorAll('span[data-forge="luminari"]').forEach((span) => {
      span.classList.add('forge-luminari');
    });
    return tmpl.innerHTML;
  }

  function renderElementContent(el, div) {
    switch (el.kind) {
      case 'phase_header_bar': {
        if (el.fill) div.style.backgroundColor = el.fill;
        if (el.label) div.textContent = el.label;
        break;
      }
      case 'phase_step': {
        const num = (el.number != null) ? String(el.number) : '';
        const text = el.text || '';
        const row = document.createElement('div');
        row.className = 'step-row';
        const iconEl = document.createElement('div');
        iconEl.className = 'step-icon';
        iconEl.textContent = num;
        const textEl = document.createElement('div');
        textEl.className = 'step-text';
        textEl.innerHTML = renderForgeMarkup(text);
        row.appendChild(iconEl);
        row.appendChild(textEl);
        div.appendChild(row);
        break;
      }
      case 'header_color_bar': {
        if (el.fill) div.style.backgroundColor = el.fill;
        if (el.text_color) div.style.color = el.text_color;
        if (el.label) div.textContent = el.label;
        break;
      }
      case 'header_ability': {
        const title = document.createElement('span');
        title.className = 'hdr-title';
        title.textContent = el.title || '';
        const body = document.createElement('span');
        body.className = 'hdr-body';
        body.innerHTML = renderForgeMarkup(el.body || '');
        div.appendChild(title);
        div.appendChild(body);
        break;
      }
      case 'header_flavor': {
        div.innerHTML = renderForgeMarkup(el.text || '');
        break;
      }
      case 'header_crafted': {
        div.textContent = 'Crafted';
        break;
      }
      case 'card_slot': {
        if (el.title) {
          const titleEl = document.createElement('div');
          titleEl.className = 'card-slot-title';
          titleEl.textContent = el.title;
          div.appendChild(titleEl);
        }
        break;
      }
      case 'decree': {
        if (el.title) {
          const lbl = document.createElement('div');
          lbl.className = 'decree-label';
          lbl.textContent = el.title;
          div.appendChild(lbl);
        }
        break;
      }
      case 'content_box': {
        if (el.title) {
          const titleEl = document.createElement('div');
          titleEl.style.fontWeight = 'bold';
          titleEl.style.fontSize = '10px';
          titleEl.textContent = el.title;
          div.appendChild(titleEl);
        }
        break;
      }
      case 'card_pile': {
        const titleEl = document.createElement('div');
        titleEl.style.fontWeight = 'bold';
        titleEl.style.fontSize = '11px';
        titleEl.style.textAlign = 'center';
        titleEl.textContent = el.title || `Pile ${el.number || ''}`;
        div.appendChild(titleEl);
        break;
      }
    }
  }

  function renderCanvas(payload) {
    canvas.innerHTML = '';
    const pageW = payload.page.w * SCALE;
    const pageH = payload.page.h * SCALE;
    canvas.style.width = pageW + 'px';
    canvas.style.height = pageH + 'px';

    const elements = payload.elements || [];
    elements.forEach((el) => {
      if (el.x == null || el.y == null) return;
      const div = document.createElement('div');
      div.className = `forge-preview-element forge-preview-element--${el.kind}`;
      const w = (el.w || 0) * SCALE;
      const h = (el.h || 0) * SCALE;
      const left = el.x * SCALE;
      // PDF uses bottom-left origin; HTML uses top-left.
      const top = pageH - (el.y * SCALE) - h;
      div.style.left = left + 'px';
      div.style.top = top + 'px';
      div.style.width = w + 'px';
      div.style.height = h + 'px';
      const key = elementKey(el);
      if (key) {
        div.dataset.key = key;
        div.dataset.kind = el.kind;
      }
      renderElementContent(el, div);
      canvas.appendChild(div);
    });
  }

  function buildHiddenInputs(payload) {
    inputsContainer.innerHTML = '';
    const elements = payload.elements || [];
    elements.forEach((el) => {
      if (!EDITABLE_KINDS.has(el.kind)) return;
      const key = elementKey(el);
      if (!key) return;
      // Card pile and decree don't have all four coords editable.
      const fields = (el.kind === 'card_pile') ? ['x', 'y']
        : (el.kind === 'decree') ? ['y']
        : ['x', 'y', 'w', 'h'];
      fields.forEach((field) => {
        const input = document.createElement('input');
        input.type = 'hidden';
        input.name = `${key}_${field}`.replace('phase_box_phase_box_', 'phase_box_');
        // Keys are 'phase_box', 'decree', 'content_box_<id>', 'card_pile_<id>'.
        // Above replace is defensive; in practice key never starts with phase_box_.
        input.name = (key === 'phase_box' || key === 'decree')
          ? `${key}_${field}`
          : `${key}_${field}`;
        const v = el[field];
        input.value = (v == null) ? '' : String(v);
        input.dataset.elementKey = key;
        input.dataset.field = field;
        inputsContainer.appendChild(input);
      });
    });
  }

  function activate(mode) {
    const payload = payloads[mode];
    if (!payload) return;
    activeMode = mode;
    canvas.dataset.activeMode = mode;
    renderCanvas(payload);
    buildHiddenInputs(payload);
  }

  // Layout-mode toggle (radio buttons in the form).
  document.querySelectorAll('input[name="layout_mode"]').forEach((radio) => {
    radio.addEventListener('change', (ev) => {
      if (ev.target.checked) activate(ev.target.value);
    });
  });

  activate(activeMode);
})();
