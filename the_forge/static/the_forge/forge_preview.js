(function () {
  'use strict';

  const SCALE = 60; // px per inch

  const canvas = document.getElementById('forge-preview-canvas');
  const inputsContainer = document.getElementById('forge-preview-inputs');
  if (!canvas || !inputsContainer) return;

  const EDITABLE_KINDS = new Set(['phase_box', 'content_box', 'card_pile', 'decree', 'character_image']);

  // Decorative kinds that visually belong to a parent and should follow it
  // when dragged. Map → the parent's element key.
  function decorativeParentKey(el) {
    if (el.kind === 'phase_header_bar' || el.kind === 'phase_step') return 'phase_box';
    if (el.kind === 'bordered_box' || el.kind === 'track' || el.kind === 'legend' || el.kind === 'scale') {
      return el.parent_key || 'phase_box';
    }
    if (el.kind === 'card_slot') return 'decree';
    return null;
  }

  // Header band decoratives (faction title bar + abilities) follow the decree
  // because the engine anchors them to PAGE_H - TOP_MARGIN - decree_slide.
  // Phase box also follows the decree, so its decoratives ride along too.
  const HEADER_BAND_KINDS = new Set([
    'header_bar', 'header_color_bar', 'header_image',
    'header_ability', 'header_flavor', 'header_crafted',
  ]);

  // Stash header-band decoratives so the decree drag can move them.
  let headerBandDecorations = [];
  // Lowest y of the header band (bottom edge in PDF coords). Phase box top
  // must stay below this so they don't overlap.
  let headerFloorYIn = null;

  function elementKey(el) {
    if (el.kind === 'phase_box') return 'phase_box';
    if (el.kind === 'decree') return 'decree';
    if (el.kind === 'content_box') return `content_box_${el.id}`;
    if (el.kind === 'card_pile') return `card_pile_${el.id}`;
    if (el.kind === 'character_image') return `character_image_${el.id}`;
    return null;
  }

  function parsePayloads() {
    try { return JSON.parse(canvas.dataset.payloads || '{}'); }
    catch (e) { console.error('forge_preview: failed to parse payloads', e); return {}; }
  }

  const payloads = parsePayloads();
  let activeMode = canvas.dataset.activeMode || 'horizontal';
  let pageW = 0, pageH = 0; // px

  // Working state per editable element key → {x, y, w, h} in inches
  let editState = {};

  // DOM index per element key → {primary: HTMLElement, decorations: HTMLElement[], data: original element record}
  let domIndex = {};

  function renderForgeMarkup(text) {
    if (!text) return '';
    const tmpl = document.createElement('template');
    tmpl.innerHTML = text;
    tmpl.content.querySelectorAll('span[data-forge="header"]').forEach((s) => s.classList.add('forge-header'));
    tmpl.content.querySelectorAll('span[data-forge="luminari"]').forEach((s) => s.classList.add('forge-luminari'));
    return tmpl.innerHTML;
  }

  function renderElementContent(el, div) {
    switch (el.kind) {
      case 'phase_header_bar':
        if (el.fill) div.style.backgroundColor = el.fill;
        if (el.label) div.textContent = el.label;
        break;
      case 'phase_step': {
        const row = document.createElement('div');
        row.className = 'step-row';
        const iconEl = document.createElement('div');
        iconEl.className = 'step-icon';
        iconEl.textContent = (el.number != null) ? String(el.number) : '';
        const textEl = document.createElement('div');
        textEl.className = 'step-text';
        textEl.innerHTML = renderForgeMarkup(el.text || '');
        row.appendChild(iconEl);
        row.appendChild(textEl);
        div.appendChild(row);
        break;
      }
      case 'header_color_bar':
        if (el.fill) div.style.backgroundColor = el.fill;
        if (el.text_color) div.style.color = el.text_color;
        if (el.label) {
          // Wrap the label so it can sit at a higher z-index than the
          // header image (which paints on top of the bar itself).
          const lbl = document.createElement('span');
          lbl.className = 'header-color-bar-label';
          lbl.textContent = el.label;
          div.appendChild(lbl);
        }
        break;
      case 'header_image':
        if (el.image_url) {
          const himg = document.createElement('img');
          himg.className = 'hi-thumb';
          himg.src = el.image_url;
          himg.alt = '';
          div.appendChild(himg);
        }
        break;
      case 'header_ability': {
        const title = document.createElement('span');
        title.className = 'hdr-title';
        title.textContent = el.title || '';
        div.appendChild(title);
        break;
      }
      case 'header_flavor':
        div.textContent = 'Flavor Text';
        break;
      case 'header_crafted':
        div.textContent = 'Crafted Items';
        break;
      case 'card_slot':
        if (el.title) {
          const t = document.createElement('div');
          t.className = 'card-slot-title';
          t.textContent = el.title;
          div.appendChild(t);
        }
        break;
      case 'decree':
        if (el.title) {
          const lbl = document.createElement('div');
          lbl.className = 'decree-label';
          lbl.textContent = el.title;
          div.appendChild(lbl);
        }
        break;
      case 'content_box':
        if (el.title) {
          const t = document.createElement('div');
          t.style.fontWeight = 'bold';
          t.style.fontSize = '10px';
          t.textContent = el.title;
          div.appendChild(t);
        }
        break;
      case 'card_pile': {
        const t = document.createElement('div');
        t.style.fontWeight = 'bold';
        t.style.fontSize = '11px';
        t.style.textAlign = 'center';
        t.textContent = el.title || `Pile ${el.number || ''}`;
        div.appendChild(t);
        break;
      }
      case 'character_image': {
        if (el.image_url) {
          const img = document.createElement('img');
          img.className = 'ci-thumb';
          img.src = el.image_url;
          img.alt = '';
          div.appendChild(img);
        }
        const lbl = document.createElement('span');
        lbl.className = 'ci-label';
        lbl.textContent = `Image ${el.order || ''}`.trim();
        div.appendChild(lbl);
        break;
      }
      case 'bordered_box':
        if (el.title) {
          const t = document.createElement('div');
          t.className = 'bordered-box-title';
          t.textContent = el.title;
          div.appendChild(t);
        }
        break;
      case 'legend':
        if (el.title) {
          const t = document.createElement('div');
          t.className = 'legend-title';
          t.textContent = el.title;
          div.appendChild(t);
        }
        break;
      case 'scale':
        if (el.title) {
          const t = document.createElement('div');
          t.className = 'scale-title';
          t.textContent = el.title;
          div.appendChild(t);
        }
        break;
      case 'track': {
        if (el.title) {
          const t = document.createElement('div');
          t.className = 'track-title';
          t.textContent = el.title;
          div.appendChild(t);
        }
        const isToken = el.track_type === 'token';
        // Absolute-position slots using engine-computed coords (in inches,
        // top-left origin within the track flowable).
        (el.slots || []).forEach((s) => {
          const slot = document.createElement('div');
          slot.className = 'track-slot' + (isToken ? ' track-slot--token' : ' track-slot--building');
          slot.style.position = 'absolute';
          slot.style.left = (s.x * SCALE) + 'px';
          slot.style.top = (s.y * SCALE) + 'px';
          slot.style.width = (s.size * SCALE) + 'px';
          slot.style.height = (s.size * SCALE) + 'px';
          div.appendChild(slot);
        });
        // Section divider lines (full grid height).
        if (el.divider_lines && el.grid_h != null && el.grid_top != null) {
          el.divider_lines.forEach((dx) => {
            const sep = document.createElement('div');
            sep.className = 'track-divider';
            sep.style.position = 'absolute';
            sep.style.left = (dx * SCALE) + 'px';
            sep.style.top = (el.grid_top * SCALE) + 'px';
            sep.style.width = '1px';
            sep.style.height = (el.grid_h * SCALE) + 'px';
            div.appendChild(sep);
          });
        }
        break;
      }
    }
  }

  function placeDiv(div, xIn, yIn, wIn, hIn) {
    const w = (wIn || 0) * SCALE;
    const h = (hIn || 0) * SCALE;
    div.style.left = (xIn * SCALE) + 'px';
    div.style.top = (pageH - (yIn * SCALE) - h) + 'px';
    div.style.width = w + 'px';
    div.style.height = h + 'px';
  }

  function renderCanvas(payload) {
    canvas.innerHTML = '';
    domIndex = {};
    headerBandDecorations = [];
    headerFloorYIn = null;
    pageW = payload.page.w * SCALE;
    pageH = payload.page.h * SCALE;
    canvas.style.width = pageW + 'px';
    canvas.style.height = pageH + 'px';

    const elements = payload.elements || [];

    // First pass: editable elements
    elements.forEach((el) => {
      if (!EDITABLE_KINDS.has(el.kind)) return;
      if (el.x == null || el.y == null) return;
      const key = elementKey(el);
      if (!key) return;
      const div = document.createElement('div');
      div.className = `forge-preview-element forge-preview-element--${el.kind}`;
      div.dataset.key = key;
      div.dataset.kind = el.kind;
      placeDiv(div, el.x, el.y, el.w || 0, el.h || 0);
      renderElementContent(el, div);
      canvas.appendChild(div);
      domIndex[key] = { primary: div, decorations: [], data: el };
    });

    // Second pass: decorative elements; if they belong to an editable parent,
    // record them so dragging the parent can move them too.
    elements.forEach((el) => {
      if (EDITABLE_KINDS.has(el.kind)) return;
      if (el.x == null || el.y == null) return;
      const div = document.createElement('div');
      div.className = `forge-preview-element forge-preview-element--${el.kind}`;
      placeDiv(div, el.x, el.y, el.w || 0, el.h || 0);
      renderElementContent(el, div);
      canvas.appendChild(div);
      const parentKey = decorativeParentKey(el);
      if (parentKey && domIndex[parentKey]) {
        // Stash the decoration's original offset relative to its parent.
        // For phase_box children, anchor to the top edge so resizing the
        // bottom doesn't pull the header/steps off the box's top-left.
        const parent = domIndex[parentKey].data;
        div._dxIn = el.x - parent.x;
        div._wIn = el.w || 0;
        div._hIn = el.h || 0;
        if (parentKey === 'phase_box' || parentKey.startsWith('content_box_')) {
          div._topAnchored = true;
          // Distance from the decoration's top to the parent's top, in inches.
          // PDF top edge of decoration is el.y + el.h; parent top is parent.y + parent.h.
          div._dTopIn = (parent.y + (parent.h || 0)) - (el.y + (el.h || 0));
        } else {
          div._dyIn = el.y - parent.y;
        }
        domIndex[parentKey].decorations.push(div);
      }
      if (HEADER_BAND_KINDS.has(el.kind)) {
        div._origXIn = el.x;
        div._origYIn = el.y;
        div._wIn = el.w || 0;
        div._hIn = el.h || 0;
        headerBandDecorations.push(div);
        if (headerFloorYIn == null || el.y < headerFloorYIn) {
          headerFloorYIn = el.y;
        }
      }
    });
  }

  function initEditState(payload) {
    editState = {};
    (payload.elements || []).forEach((el) => {
      if (!EDITABLE_KINDS.has(el.kind)) return;
      const key = elementKey(el);
      if (!key) return;
      editState[key] = {
        x: (el.x != null) ? el.x : 0,
        y: (el.y != null) ? el.y : 0,
        w: el.w || 0,
        h: el.h || 0,
        kind: el.kind,
        y_min: el.y_min,
        y_max: el.y_max,
      };
    });
  }

  function buildHiddenInputs() {
    inputsContainer.innerHTML = '';
    Object.entries(editState).forEach(([key, st]) => {
      const fields = (st.kind === 'card_pile') ? ['x', 'y']
        : (st.kind === 'decree') ? ['y']
        : (st.kind === 'character_image') ? ['x', 'y', 'w']
        : ['x', 'y', 'w', 'h'];
      fields.forEach((field) => {
        const input = document.createElement('input');
        input.type = 'hidden';
        input.name = `${key}_${field}`;
        input.value = String(st[field]);
        input.dataset.elementKey = key;
        input.dataset.field = field;
        inputsContainer.appendChild(input);
      });
    });
  }

  function syncHiddenInputs(key) {
    const st = editState[key];
    if (!st) return;
    inputsContainer.querySelectorAll(`input[data-element-key="${key}"]`).forEach((input) => {
      const f = input.dataset.field;
      if (st[f] != null) input.value = String(st[f]);
    });
  }

  function clamp(val, lo, hi) { return Math.max(lo, Math.min(hi, val)); }

  function applyState(key) {
    const entry = domIndex[key];
    if (!entry) return;
    const st = editState[key];
    placeDiv(entry.primary, st.x, st.y, st.w, st.h);
    entry.decorations.forEach((dec) => {
      const xIn = st.x + (dec._dxIn || 0);
      let yIn;
      if (dec._topAnchored) {
        // Keep the decoration glued to the parent's top edge, not its bottom.
        // parent top (PDF) = st.y + st.h; decoration top = yIn + dec._hIn.
        yIn = st.y + st.h - (dec._hIn || 0) - (dec._dTopIn || 0);
      } else {
        yIn = st.y + (dec._dyIn || 0);
      }
      placeDiv(dec, xIn, yIn, dec._wIn || 0, dec._hIn || 0);
    });
  }

  // ---- Drag wiring ----

  let dragActive = false;

  function startDrag(ev, key) {
    if (ev.button !== 0 && ev.pointerType === 'mouse') return;
    if (dragActive) return; // ignore re-entry while another drag is active
    const st = editState[key];
    if (!st) return;
    const entry = domIndex[key];
    if (!entry) return;
    const startX = ev.clientX;
    const startY = ev.clientY;
    const origX = st.x;
    const origY = st.y;
    const pageWIn = pageW / SCALE;
    const pageHIn = pageH / SCALE;
    const lockY = (st.kind === 'decree'); // decree drags y-only

    // For decree: also slide phase_box + header band by the same dy.
    const isDecreeDrag = (st.kind === 'decree');
    const phaseSt = isDecreeDrag ? editState['phase_box'] : null;
    const phaseOrigY = phaseSt ? phaseSt.y : 0;
    // Capture the CURRENT y of each header decoration, not the original
    // payload y — otherwise a second drag would snap them back.
    const headerBandStartY = isDecreeDrag
      ? headerBandDecorations.map((d) => d._curYIn != null ? d._curYIn : d._origYIn) : [];

    // Capture on whichever element actually received the pointerdown (the
    // primary or one of its decoratives), so move/up events route back here.
    const captureEl = ev.currentTarget;
    captureEl.setPointerCapture(ev.pointerId);
    entry.primary.classList.add('forge-preview-element--dragging');
    dragActive = true;
    ev.preventDefault();
    ev.stopPropagation();

    function onMove(mv) {
      const dxIn = (mv.clientX - startX) / SCALE;
      const dyInScreen = (mv.clientY - startY) / SCALE;
      // Screen y grows down; PDF y grows up — flip dy.
      const dyIn = -dyInScreen;
      let nx = origX + (lockY ? 0 : dxIn);
      let ny = origY + dyIn;
      const w = st.w || 0;
      const h = st.h || 0;
      if (st.kind === 'decree') {
        // Decree slides between min/max provided by the engine; the image
        // intentionally extends below the page edge so we can't use the
        // generic 0..pageH clamp.
        const decreeLo = (st.y_min != null) ? st.y_min : (-h);
        const decreeHi = (st.y_max != null) ? st.y_max : (pageHIn - h);
        // Sliding the decree DOWN (decreasing decree y in PDF coords) shifts
        // the phase box DOWN too. dy applied to phase_box matches dy on decree.
        // Constrain so phase_box bottom >= 0 (its bottom is phase.y, since y
        // is its bottom-left). i.e. phase_box.y + dy >= 0 → dy >= -phase.y.
        let lo = decreeLo;
        let hi = decreeHi;
        if (phaseSt) {
          const minDy = -phaseOrigY; // dy >= -phaseOrigY
          // Convert to ny bounds: dy = ny - origY → ny >= origY + minDy
          lo = Math.max(lo, origY + minDy);
        }
        ny = clamp(ny, lo, hi);
      } else if (st.kind === 'card_pile') {
        // Card piles can hang off the bottom edge — y can go negative until
        // y_min (text zone still on-page).
        const pileLo = (st.y_min != null) ? st.y_min : 0;
        nx = clamp(nx, 0, Math.max(0, pageWIn - w));
        ny = clamp(ny, pileLo, Math.max(pileLo, pageHIn - h));
      } else if (st.kind === 'phase_box') {
        // Phase box top can't overlap the header band.
        nx = clamp(nx, 0, Math.max(0, pageWIn - w));
        const yHi = (headerFloorYIn != null)
          ? Math.max(0, headerFloorYIn - h)
          : Math.max(0, pageHIn - h);
        ny = clamp(ny, 0, yHi);
      } else {
        nx = clamp(nx, 0, Math.max(0, pageWIn - w));
        ny = clamp(ny, 0, Math.max(0, pageHIn - h));
      }
      st.x = nx;
      st.y = ny;
      applyState(key);
      syncHiddenInputs(key);

      if (isDecreeDrag) {
        const dy = ny - origY;
        if (phaseSt) {
          phaseSt.y = phaseOrigY + dy;
          applyState('phase_box');
          syncHiddenInputs('phase_box');
        }
        // Header band moves by dy; recompute the new header floor so the phase
        // box top can't overlap it after the slide.
        let newFloor = null;
        headerBandDecorations.forEach((d, i) => {
          const newY = headerBandStartY[i] + dy;
          placeDiv(d, d._origXIn, newY, d._wIn, d._hIn);
          d._curYIn = newY;
          if (newFloor == null || newY < newFloor) newFloor = newY;
        });
        if (newFloor != null) headerFloorYIn = newFloor;
      }
    }

    function onUp() {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      window.removeEventListener('pointercancel', onUp);
      entry.primary.classList.remove('forge-preview-element--dragging');
      try { captureEl.releasePointerCapture(ev.pointerId); } catch (e) { /* already released */ }
      dragActive = false;
    }

    // Attach to window (not captureEl) so the gesture is always cleaned up
    // even if pointer events get retargeted by capture/release transitions.
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    window.addEventListener('pointercancel', onUp);
  }

  function wireDrag() {
    Object.entries(domIndex).forEach(([key, entry]) => {
      const div = entry.primary;
      div.classList.add('forge-preview-element--draggable');
      div.style.cursor = (editState[key] && editState[key].kind === 'decree') ? 'ns-resize' : 'move';
      div.addEventListener('pointerdown', (ev) => startDrag(ev, key));
      // Decoratives belonging to this editable element should drag the
      // parent. Forward their pointer events.
      entry.decorations.forEach((dec) => {
        dec.classList.add('forge-preview-element--draggable');
        dec.style.cursor = div.style.cursor;
        dec.addEventListener('pointerdown', (ev) => startDrag(ev, key));
      });
    });
  }

  // ---- Resize wiring ----

  const RESIZE_DIRS = ['n', 's', 'e', 'w', 'nw', 'ne', 'sw', 'se'];
  const MIN_SIZE_IN = 0.25; // smallest box footprint while resizing

  function startResize(ev, key, dir) {
    if (ev.button !== 0 && ev.pointerType === 'mouse') return;
    if (dragActive) return;
    const st = editState[key];
    if (!st) return;
    const entry = domIndex[key];
    if (!entry) return;

    const startX = ev.clientX;
    const startY = ev.clientY;
    const orig = { x: st.x, y: st.y, w: st.w, h: st.h };
    const pageWIn = pageW / SCALE;
    const pageHIn = pageH / SCALE;
    const headerLid = (st.kind === 'phase_box' && headerFloorYIn != null)
      ? headerFloorYIn : pageHIn;

    const captureEl = ev.currentTarget;
    captureEl.setPointerCapture(ev.pointerId);
    entry.primary.classList.add('forge-preview-element--dragging');
    dragActive = true;
    ev.preventDefault();
    ev.stopPropagation();

    const aspect = (orig.h > 0) ? (orig.w / orig.h) : 1;

    function onMove(mv) {
      const dxIn = (mv.clientX - startX) / SCALE;
      const dyInScreen = (mv.clientY - startY) / SCALE;
      // Screen y grows down; PDF y grows up.
      const dyIn = -dyInScreen;

      let nx = orig.x;
      let ny = orig.y;
      let nw = orig.w;
      let nh = orig.h;

      // CharacterImage: aspect-ratio locked diagonal resize. Width drives,
      // height follows from the captured aspect. Anchor the corner opposite
      // the dragged handle.
      if (st.kind === 'character_image') {
        // signed delta-w driven by horizontal direction
        const dwSigned = dir.includes('e') ? dxIn : (dir.includes('w') ? -dxIn : 0);
        // signed delta-h driven by vertical direction
        const dhSigned = dir.includes('n') ? dyIn : (dir.includes('s') ? -dyIn : 0);
        // Dominant axis wins so the cursor follows the corner naturally.
        let candidateW;
        if (Math.abs(dwSigned) >= Math.abs(dhSigned)) {
          candidateW = orig.w + dwSigned;
        } else {
          candidateW = (orig.h + dhSigned) * aspect;
        }
        nw = Math.max(MIN_SIZE_IN, candidateW);
        nh = nw / aspect;
        if (dir.includes('w')) nx = orig.x + (orig.w - nw);
        if (dir.includes('s')) ny = orig.y + (orig.h - nh);
        st.x = nx;
        st.y = ny;
        st.w = nw;
        st.h = nh;
        applyState(key);
        syncHiddenInputs(key);
        return;
      }

      // PDF coords: y is bottom; y + h is top. dxIn screen-right; dyIn PDF-up.
      if (dir.includes('e')) {
        // Right edge: w changes, x stays. Right edge clamped to page.
        nw = clamp(orig.w + dxIn, MIN_SIZE_IN, pageWIn - orig.x);
      }
      if (dir.includes('w')) {
        // Left edge: x and w change in opposite directions.
        // Clamp x >= 0 and w >= MIN.
        const dx = clamp(dxIn, -orig.x, orig.w - MIN_SIZE_IN);
        nx = orig.x + dx;
        nw = orig.w - dx;
      }
      if (dir.includes('s')) {
        // Bottom edge: y and h change in opposite directions when bottom moves.
        // Mouse down → bottom moves down → y decreases, h increases.
        // dyIn is PDF-up; bottom moving down corresponds to dyIn negative.
        // Let dyB = movement of bottom edge in PDF coords (positive = up).
        // Then y_new = y + dyB, h_new = h - dyB.
        // dyB = dyIn (mouse up moves bottom up).
        // Constraints: y_new >= 0 → dyB >= -y, h_new >= MIN → dyB <= h - MIN.
        const dyB = clamp(dyIn, -orig.y, orig.h - MIN_SIZE_IN);
        ny = orig.y + dyB;
        nh = orig.h - dyB;
      }
      if (dir.includes('n')) {
        // Top edge: only h changes (y is the bottom, stays put).
        // Mouse up (dyIn positive) → top moves up → h increases.
        // Top can't cross headerLid: y + h <= headerLid.
        const maxH = headerLid - orig.y;
        nh = clamp(orig.h + dyIn, MIN_SIZE_IN, Math.max(MIN_SIZE_IN, maxH));
      }

      st.x = nx;
      st.y = ny;
      st.w = nw;
      st.h = nh;
      applyState(key);
      syncHiddenInputs(key);
    }

    function onUp() {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      window.removeEventListener('pointercancel', onUp);
      entry.primary.classList.remove('forge-preview-element--dragging');
      try { captureEl.releasePointerCapture(ev.pointerId); } catch (e) { /* already released */ }
      dragActive = false;
    }

    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    window.addEventListener('pointercancel', onUp);
  }

  const CORNER_DIRS = ['nw', 'ne', 'sw', 'se'];

  function wireResize() {
    Object.entries(domIndex).forEach(([key, entry]) => {
      const st = editState[key];
      if (!st) return;
      let dirs;
      if (st.kind === 'phase_box' || st.kind === 'content_box') {
        dirs = RESIZE_DIRS;
      } else if (st.kind === 'character_image') {
        // Aspect ratio is locked, so only diagonal corners make sense.
        dirs = CORNER_DIRS;
      } else {
        return;
      }
      dirs.forEach((dir) => {
        const handle = document.createElement('div');
        handle.className = `forge-resize-handle forge-resize-handle--${dir}`;
        handle.addEventListener('pointerdown', (ev) => startResize(ev, key, dir));
        entry.primary.appendChild(handle);
      });
    });
  }

  function activate(mode) {
    const payload = payloads[mode];
    if (!payload) return;
    activeMode = mode;
    canvas.dataset.activeMode = mode;
    renderCanvas(payload);
    initEditState(payload);
    buildHiddenInputs();
    wireDrag();
    wireResize();
  }

  document.querySelectorAll('input[name="layout_mode"]').forEach((radio) => {
    radio.addEventListener('change', (ev) => { if (ev.target.checked) activate(ev.target.value); });
  });

  activate(activeMode);
})();
