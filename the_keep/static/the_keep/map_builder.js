// Map builder: lay out a Map post's VP track, items, clearings, forests and
// buildings on its board image. The layout is kept in 0-1 image coordinates
// (u right, v down) and saved to the server, which builds the TTS JSON from it
// (the_keep/services/tts_map.py). Geometry here mirrors MapGeometry there.
(function () {
  'use strict';

  const config = JSON.parse(document.getElementById('map-builder-config').textContent);
  const W = config.width, H = config.height;
  const svg = document.getElementById('mb-svg');
  const NS = 'http://www.w3.org/2000/svg';
  const PPL = config.px_per_local;              // pixels per local unit
  const STEP_TRACK_PX = config.track.step * PPL;
  const ITEM = config.item_grid;
  const BUILDING_PX = 0.1 * PPL;                // building square size (local 0.1)
  const DOT_PX = Math.max(W, H) / 120;
  const STROKE = Math.max(W, H) / 600;

  const state = config.state;
  state.clearings = state.clearings || [];
  state.forests = state.forests || [];
  state.buildings = state.buildings || [];
  state.fixed = !!config.fixed;   // the Map's setting; saved back with the layout

  const STEPS = [
    { heading: 'VP and Item Tracks',
      hint: 'Drag the item grid onto the printed item spaces. Drag the VP track up or down so the blue dots align with the printed 0-30 track.' },
    { heading: 'Clearings',
      hint: 'Click the middle of a clearing, then drag (or click) to where its suit marker goes. Drag the number tile around the circle. Select a clearing to re-number or delete it.' },
    { heading: 'Buildings & Ruins',
      hint: 'Click each building space. Drag inside a square to move it and press X to remove it. Select a building and click the ruin tile to make it a ruin.' },
    { heading: 'Forests',
      hint: 'Click the middle of each forest. Drag a dot to move it and press X to remove it. These are used for Relic placements.' },
    { heading: 'Review', 
      hint: 'Check the counts, then save and generate the TTS JSON.' },
  ];

  // Step numbers, in the order the steps appear (Review is the last).
  const STEP_TRACK = 0, STEP_CLEARINGS = 1, STEP_BUILDINGS = 2, STEP_FORESTS = 3;

  let step = 0;
  let selected = null;        // {kind: 'clearing'|'forest'|'building', index}
  let drag = null;
  let pendingMarker = null;   // clearing index waiting for a marker click
  let dirty = false;

  // ── Geometry ──────────────────────────────────────────────────────────────
  const px = (p) => [p[0] * W, p[1] * H];
  const norm = (x, y) => [Math.min(Math.max(x / W, 0), 1), Math.min(Math.max(y / H, 0), 1)];
  const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
  const imageU = (localX) => (1 - localX / config.track.half_width) / 2;

  function pointer(evt) {
    const pt = svg.createSVGPoint();
    pt.x = evt.clientX; pt.y = evt.clientY;
    const p = pt.matrixTransform(svg.getScreenCTM().inverse());
    return [p.x, p.y];
  }

  function trackRows() {
    const gapPx = config.track.row_gap * PPL;
    const mid = state.track_y * H;
    return [mid - gapPx, mid, mid + gapPx];
  }

  function trackColumns() {
    const cols = [];
    for (let i = 0; i < config.track.cells; i++) {
      cols.push(imageU(config.track.x0 + i * config.track.step) * W);
    }
    return cols;
  }

  function itemSlots() {
    const [x0, y0] = px(state.items);
    const slots = [];
    for (let r = 0; r < ITEM.rows; r++) {
      for (let c = 0; c < ITEM.cols; c++) {
        slots.push([x0 + c * ITEM.step_x * PPL, y0 + r * ITEM.step_z * PPL]);
      }
    }
    return slots;
  }

  function numberTile(c) {
    const centre = px(c.centre);
    const r = c.marker ? dist(centre, px(c.marker)) : 0;
    const a = c.number_angle !== undefined ? c.number_angle : Math.PI / 2;
    const d = r + config.number_radius_px;
    return [centre[0] + d * Math.cos(a), centre[1] + d * Math.sin(a)];
  }

  // ── Drawing ───────────────────────────────────────────────────────────────
  function el(name, attrs, parent) {
    const node = document.createElementNS(NS, name);
    for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, v);
    (parent || content).appendChild(node);
    return node;
  }

  // <defs> (the marker symbol) survives redraws; only `content` is cleared.
  const defs = el('defs', {}, svg);
  const content = el('g', {}, svg);
  let markerSymbol = false;

  if (config.marker_svg) {
    fetch(config.marker_svg).then(r => r.ok ? r.text() : Promise.reject()).then(text => {
      const doc = new DOMParser().parseFromString(text, 'image/svg+xml');
      const source = doc.documentElement;
      if (source.nodeName !== 'svg') return;
      const symbol = el('symbol', { id: 'mb-marker', viewBox: source.getAttribute('viewBox'),
                                    preserveAspectRatio: 'none', overflow: 'visible' }, defs);
      for (const child of Array.from(source.childNodes)) symbol.appendChild(document.importNode(child, true));
      markerSymbol = true;
      render();
    }).catch(() => {});   // keep the round dots
  }

  function layer(active) {
    return el('g', { opacity: active ? 1 : 0.35, 'pointer-events': active ? 'auto' : 'none' });
  }

  function plus(g, x, y, size, colour, data) {
    el('line', { x1: x - size, y1: y, x2: x + size, y2: y, stroke: colour, 'stroke-width': STROKE * 1.5, ...data }, g);
    el('line', { x1: x, y1: y - size, x2: x, y2: y + size, stroke: colour, 'stroke-width': STROKE * 1.5, ...data }, g);
  }

  function render() {
    content.innerHTML = '';
    // Track and items (step 0).
    const g0 = layer(step === STEP_TRACK);
    const rows = trackRows(), cols = trackColumns();
    const band = el('rect', {
      x: Math.min(...cols) - STEP_TRACK_PX, y: rows[0] - STEP_TRACK_PX * 0.6,
      width: Math.max(...cols) - Math.min(...cols) + 2 * STEP_TRACK_PX, height: rows[2] - rows[0] + STEP_TRACK_PX * 1.2,
      fill: 'rgba(13,110,253,0.15)', stroke: '#0d6efd', 'stroke-width': STROKE, 'data-kind': 'track',
      style: 'cursor: ns-resize',
    }, g0);
    // The middle row (on the printed numbers) in blue; the rows above and below in grey.
    rows.forEach((y, row) => {
      const fill = row === 1 ? '#0d6efd' : '#6c757d';
      for (const x of cols) el('circle', { cx: x, cy: y, r: STEP_TRACK_PX * 0.18, fill, 'pointer-events': 'none' }, g0);
    });
    const slots = itemSlots();
    const size = ITEM.step_x * PPL * 0.85;
    const xs = slots.map(s => s[0]), ys = slots.map(s => s[1]);
    // On the Clearings step the fixed/random panel sits over the item grid instead.
    if (step !== STEP_CLEARINGS) {
    el('rect', {
      x: Math.min(...xs) - size * 0.65, y: Math.min(...ys) - size * 0.65,
      width: Math.max(...xs) - Math.min(...xs) + size * 1.3, height: Math.max(...ys) - Math.min(...ys) + size * 1.3,
      fill: 'rgba(255,193,7,0.15)', stroke: '#ffc107', 'stroke-width': STROKE, 'data-kind': 'items', style: 'cursor: move',
    }, g0);
    for (const [x, y] of slots) {
      el('rect', { x: x - size / 2, y: y - size / 2, width: size, height: size, rx: size * 0.15,
                   fill: 'none', stroke: '#ffc107', 'stroke-width': STROKE * 1.5, 'pointer-events': 'none' }, g0);
    }
    }

    // Clearings (step 1).
    const g1 = layer(step === STEP_CLEARINGS);
    state.clearings.forEach((c, i) => {
      // One group per clearing, so the edit pop-up can measure the whole clearing.
      const g = el('g', { 'data-clearing': i }, g1);
      const centre = px(c.centre);
      const isSel = selected && selected.kind === 'clearing' && selected.index === i;
      const colour = isSel ? '#dc3545' : '#ffffff';
      if (c.marker) {
        const m = px(c.marker);
        el('circle', { cx: centre[0], cy: centre[1], r: dist(centre, m), fill: 'rgba(255,255,255,0.12)',
                       stroke: colour, 'stroke-width': STROKE, 'pointer-events': 'none' }, g);
        el('line', { x1: centre[0], y1: centre[1], x2: m[0], y2: m[1], stroke: colour,
                     'stroke-width': STROKE, 'stroke-dasharray': `${STROKE * 4} ${STROKE * 3}`, 'pointer-events': 'none' }, g);
        drawMarker(g, c, i, centre, m, isSel);
        const t = numberTile(c);
        el('circle', { cx: t[0], cy: t[1], r: config.number_radius_px, fill: '#231f20', stroke: colour,
                       'stroke-width': STROKE, 'data-kind': 'number', 'data-index': i, style: 'cursor: grab' }, g);
        const label = el('text', { x: t[0], y: t[1], fill: '#fff', 'font-size': config.number_radius_px * 1.1,
                                   'text-anchor': 'middle', 'dominant-baseline': 'central', 'pointer-events': 'none',
                                   'font-weight': 'bold' }, g);
        label.textContent = c.number || '?';
      }
      el('circle', { cx: centre[0], cy: centre[1], r: DOT_PX * 1.6, fill: 'rgba(0,0,0,0.001)',
                     'data-kind': 'centre', 'data-index': i, style: 'cursor: move' }, g);
      plus(g, centre[0], centre[1], DOT_PX * 1.4, colour, { 'pointer-events': 'none' });
    });

    // Forests (step 2).
    const g2 = layer(step === STEP_FORESTS);
    state.forests.forEach((f, i) => {
      const p = px(f);
      const isSel = selected && selected.kind === 'forest' && selected.index === i;
      el('circle', { cx: p[0], cy: p[1], r: DOT_PX * 1.4, fill: '#198754', stroke: isSel ? '#dc3545' : '#fff',
                     'stroke-width': STROKE * 2, 'data-kind': 'forest', 'data-index': i, style: 'cursor: move' }, g2);
    });

    // Buildings and ruins (steps 3 and 4).
    const g3 = layer(step === STEP_BUILDINGS);
    state.buildings.forEach((b, i) => {
      const p = px(b.at);
      const isSel = selected && selected.kind === 'building' && selected.index === i;
      const s = BUILDING_PX;
      el('rect', { x: p[0] - s / 2, y: p[1] - s / 2, width: s, height: s, rx: s * 0.2,
                   fill: b.ruin ? 'rgba(220,53,69,0.85)' : 'rgba(255,255,255,0.85)',
                   stroke: isSel ? '#dc3545' : '#333', 'stroke-width': STROKE * 1.5,
                   'data-kind': 'building', 'data-index': i, style: 'cursor: move' }, g3);
      if (b.ruin) {
        const r = el('text', { x: p[0], y: p[1], fill: '#fff', 'font-size': s * 0.6, 'font-weight': 'bold',
                               'text-anchor': 'middle', 'dominant-baseline': 'central', 'pointer-events': 'none' }, g3);
        r.textContent = 'R';
      } else {
        plus(g3, p[0], p[1], s * 0.25, '#333', { 'pointer-events': 'none' });
      }
    });

    updatePanel();
  }

  // The suit marker: its real outline (turned so its bump faces away from the
  // clearing centre, as TTS will place it) at its real size, or a dot until the
  // outline has loaded.
  function drawMarker(g, c, i, centre, m, isSel) {
    const data = { 'data-kind': 'marker', 'data-index': i, style: 'cursor: move' };
    if (!markerSymbol) {
      el('circle', { cx: m[0], cy: m[1], r: DOT_PX * 1.3, fill: suitColour(c), stroke: isSel ? '#dc3545' : '#000',
                     'stroke-width': STROKE, ...data }, g);
      return;
    }
    const [w, h] = config.marker_px;
    const angle = Math.atan2(m[1] - centre[1], m[0] - centre[0]) * 180 / Math.PI + 90;
    const box = viewBoxOf(defs.querySelector('#mb-marker'));
    // Place the symbol so its (0, 0) -- the marker's spot -- sits on m.
    const x = box[0] / box[2] * w, y = box[1] / box[3] * h;
    el('use', { href: '#mb-marker', x, y, width: w, height: h,
                transform: `translate(${m[0]} ${m[1]}) rotate(${angle})`,
                style: `color: ${suitColour(c)}; --mb-marker-stroke: ${isSel ? '#dc3545' : '#231f20'}; cursor: move`,
                'data-kind': 'marker', 'data-index': i }, g);
  }

  function viewBoxOf(node) {
    return (node.getAttribute('viewBox') || '0 0 1 1').split(/[\s,]+/).map(Number);
  }

  function suitColour(c) {
    const colours = { fox: '#EC2121', rabbit: '#FFE400', mouse: '#F78B57', bird: '#44C3BC' };
    return (config.fixed && colours[c.suit]) || '#ffffff';
  }

  // ── Counters, tools, review ───────────────────────────────────────────────
  function counts() {
    return {
      clearings: state.clearings.length,
      forests: state.forests.length,
      buildings: state.buildings.length,
      ruins: state.buildings.filter(b => b.ruin).length,
    };
  }

  function updatePanel() {
    // Progress bar: the highlight slides to the current step.
    document.querySelectorAll('#mb-progress [data-step]').forEach(btn => {
      const n = Number(btn.dataset.step);
      btn.classList.toggle('active', n === step);
      btn.classList.toggle('done', n < step);
      if (n === step) btn.setAttribute('aria-current', 'step'); else btn.removeAttribute('aria-current');
    });
    document.getElementById('mb-progress-thumb').style.transform = `translateX(${step * 100}%)`;
    document.getElementById('mb-heading').textContent = STEPS[step].heading;

    // One badge per count on this step; amber when it differs from the Map.
    const COUNTERS = { [STEP_CLEARINGS]: [['clearings']], [STEP_BUILDINGS]: [['buildings', 'Buildings'], ['ruins', 'Ruins']],
                       [STEP_FORESTS]: [['forests']] };
    const counter = document.getElementById('mb-counter');
    counter.innerHTML = '';
    for (const [key, label] of COUNTERS[step] || []) {
      const n = counts()[key], expected = config.expected[key];
      const known = expected !== null && expected !== undefined;
      const badge = document.createElement('span');
      badge.className = 'badge fs-6 ' + (!known || n === expected ? 'bg-secondary' : 'bg-warning text-dark');
      badge.textContent = (known ? `${n} / ${expected}` : `${n}`) + (label ? ` ${label}` : '');
      badge.title = known && n !== expected ? `Currently ${expected}. Saving updates the Map to ${n}.` : '';
      counter.appendChild(badge);
    }

    updatePopover();
    updateSuitMode();

    document.getElementById('mb-back').disabled = step === 0;
    document.getElementById('mb-next').classList.toggle('d-none', step === STEPS.length - 1);
    document.getElementById('mb-generate').classList.toggle('d-none', step !== STEPS.length - 1);
    document.getElementById('mb-review').classList.toggle('d-none', step !== STEPS.length - 1);
    if (step === STEPS.length - 1) renderReview();
  }

  function renderReview() {
    const c = counts();
    const rows = [['Clearings', c.clearings, config.expected.clearings], ['Forests', c.forests, config.expected.forests],
                  ['Building spaces', c.buildings, config.expected.buildings], ['Ruins', c.ruins, config.expected.ruins]];
    const list = document.getElementById('mb-review-counts');
    list.innerHTML = '';
    for (const [label, n, expected] of rows) {
      const li = document.createElement('li');
      li.textContent = `${label}: ${n}`;
      if (expected !== null && expected !== undefined && expected !== n) {
        li.textContent += ` (the post says ${expected}; saving updates it to ${n})`;
        li.className = 'text-warning-emphasis';
      }
      list.appendChild(li);
    }
    const problems = document.getElementById('mb-review-problems');
    problems.innerHTML = '';
    const local = localErrors();
    if (local.length) problems.appendChild(alertBox('danger', local));
  }

  function localErrors() {
    const errors = [];
    if (!state.clearings.length) errors.push('Mark at least one clearing.');
    if (state.clearings.some(c => !c.marker)) errors.push('Every clearing needs its suit-marker spot.');
    // Same wording as check_builder in tts_map.py, so a server copy can be matched to it.
    if (config.fixed && state.clearings.some(c => !c.suit)) errors.push('This Map has fixed clearings: choose a suit for each clearing before generating.');
    if (state.clearings.length > config.numbers) {
      errors.push(`Only numbers 1-${config.numbers} have tiles: clearings above ${config.numbers} get no number tile.`);
    }
    return errors;
  }

  // Draw attention to the review step's list of problems (instead of repeating it).
  function highlightReview() {
    const alert = document.querySelector('#mb-review-problems .alert');
    if (!alert) return;
    alert.classList.remove('mb-flash');
    void alert.offsetWidth;   // restart the animation
    alert.classList.add('mb-flash');
    alert.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  function alertBox(kind, lines) {
    const div = document.createElement('div');
    div.className = `alert alert-${kind} small mb-2`;
    const ul = document.createElement('ul');
    ul.className = 'mb-0';
    for (const line of lines) {
      const li = document.createElement('li');
      li.textContent = line;
      ul.appendChild(li);
    }
    div.appendChild(ul);
    return div;
  }

  // ── Editing ───────────────────────────────────────────────────────────────
  function changed() {
    dirty = true;
    showSaved(false);
    render();
  }

  // The Save button reads "Saved" with a check after a save, until the next change.
  function showSaved(saved) {
    const btn = document.getElementById('mb-save');
    btn.querySelector('i').className = saved ? 'bi bi-check-lg me-1' : 'bi bi-floppy-fill me-1';
    btn.querySelector('span').textContent = saved ? btn.dataset.savedLabel : btn.dataset.label;
  }

  // Remove a clearing and close the gap in the numbers. The selection and a
  // clearing waiting for its marker refer to clearings by position, so they're
  // moved along (or cleared if they were this one).
  function removeClearing(index) {
    const removed = state.clearings.splice(index, 1)[0];
    if (!removed) return;
    for (const c of state.clearings) if (c.number > removed.number) c.number -= 1;
    if (pendingMarker === index) pendingMarker = null;
    else if (pendingMarker !== null && pendingMarker > index) pendingMarker -= 1;
    if (selected && selected.kind === 'clearing') {
      if (selected.index === index) selected = null;
      else if (selected.index > index) selected = { kind: 'clearing', index: selected.index - 1 };
    }
  }

  function deleteSelected() {
    if (!selected) return;
    if (selected.kind === 'clearing') {
      removeClearing(selected.index);
    } else if (selected.kind === 'forest') {
      state.forests.splice(selected.index, 1);
    } else if (selected.kind === 'building') {
      state.buildings.splice(selected.index, 1);
    }
    selected = null;
    changed();
  }

  function renumber(index, n) {
    const c = state.clearings[index];
    n = Math.round(Number(n));
    if (!n || n < 1 || n > state.clearings.length || n === c.number) return;
    const other = state.clearings.find(o => o.number === n);
    if (other) other.number = c.number;
    c.number = n;
    changed();
  }

  function hit(evt) {
    const target = evt.target;
    if (!target.dataset || !target.dataset.kind) return null;
    return { kind: target.dataset.kind, index: Number(target.dataset.index) };
  }

  // Only one finger edits: a second finger is the start of a pinch-zoom, so it's
  // left to the browser and whatever the first finger began is undone.
  const pressed = new Set();
  let activePointer = null;

  function abandonDrag() {
    if (drag && drag.kind === 'new-marker' && !state.clearings[drag.index].marker) removeClearing(drag.index);
    if (drag && drag.fromPending && state.clearings[drag.index]) {
      // The marker had only just been placed by this press: wait for it again.
      state.clearings[drag.index].marker = null;
      pendingMarker = drag.index;
    }
    drag = null;
    activePointer = null;
    document.body.classList.remove('mb-dragging');
    render();
  }

  svg.addEventListener('pointerdown', (evt) => {
    const second = !evt.isPrimary || pressed.size > 0;
    pressed.add(evt.pointerId);
    if (second) {
      if (activePointer !== null) abandonDrag();
      return;
    }
    activePointer = evt.pointerId;
    // Don't let a press on the board start a text selection or a native drag,
    // and keep the whole page unselectable until the drag ends.
    evt.preventDefault();
    window.getSelection().removeAllRanges();
    document.body.classList.add('mb-dragging');
    const p = pointer(evt);
    const h = hit(evt);
    svg.setPointerCapture(evt.pointerId);

    if (step === STEP_TRACK && h) {
      if (h.kind === 'track') drag = { kind: 'track', offset: p[1] - state.track_y * H };
      if (h.kind === 'items') {
        const [x0, y0] = px(state.items);
        drag = { kind: 'items', offset: [p[0] - x0, p[1] - y0] };
      }
      return;
    }

    if (step === STEP_CLEARINGS) {
      if (pendingMarker !== null && !h) {
        state.clearings[pendingMarker].marker = norm(...p);
        setDefaultAngle(state.clearings[pendingMarker]);
        drag = { kind: 'marker', index: pendingMarker, fromPending: true };
        pendingMarker = null;
        changed();
        return;
      }
      if (h && ['centre', 'marker', 'number'].includes(h.kind)) {
        selected = { kind: 'clearing', index: h.index };
        const c = state.clearings[h.index];
        if (h.kind === 'centre') {
          drag = { kind: 'centre', index: h.index, start: p, centre: px(c.centre), marker: c.marker && px(c.marker), moved: false };
        } else {
          drag = { kind: h.kind, index: h.index };
        }
        render();
        return;
      }
      // New clearing: its centre here, then drag (or click) to its marker.
      const centre = norm(...p);
      state.clearings.push({ centre, marker: null, number: state.clearings.length + 1 });
      const index = state.clearings.length - 1;
      selected = { kind: 'clearing', index };
      drag = { kind: 'new-marker', index, start: p };
      changed();
      return;
    }

    if (step === STEP_FORESTS) {
      if (h && h.kind === 'forest') {
        selected = { kind: 'forest', index: h.index };
        drag = { kind: 'forest', index: h.index };
        render();
        return;
      }
      // Place it and keep it under the pointer, so press-and-drag places and moves it.
      state.forests.push(norm(...p));
      selected = { kind: 'forest', index: state.forests.length - 1 };
      drag = { kind: 'forest', index: selected.index };
      changed();
      return;
    }

    if (step === STEP_BUILDINGS) {
      if (h && h.kind === 'building') {
        selected = { kind: 'building', index: h.index };
        const at = px(state.buildings[h.index].at);
        drag = { kind: 'building', index: h.index, offset: [p[0] - at[0], p[1] - at[1]] };
        render();
        return;
      }
      // Place it and keep it under the pointer, so press-and-drag places and moves it.
      state.buildings.push({ at: norm(...p), ruin: false });
      selected = { kind: 'building', index: state.buildings.length - 1 };
      drag = { kind: 'building', index: selected.index, offset: [0, 0] };
      changed();
      return;
    }
  });

  function setDefaultAngle(c) {
    // The number tile starts opposite the marker.
    const centre = px(c.centre), m = px(c.marker);
    c.number_angle = Math.atan2(centre[1] - m[1], centre[0] - m[0]);
  }

  svg.addEventListener('pointermove', (evt) => {
    if (!drag || evt.pointerId !== activePointer) return;
    const p = pointer(evt);
    if (drag.kind === 'track') {
      state.track_y = Math.min(Math.max((p[1] - drag.offset) / H, 0), 1);
    } else if (drag.kind === 'items') {
      state.items = norm(p[0] - drag.offset[0], p[1] - drag.offset[1]);
    } else if (drag.kind === 'new-marker') {
      if (dist(p, drag.start) < DOT_PX) return;
      const c = state.clearings[drag.index];
      c.marker = norm(...p);
      setDefaultAngle(c);
    } else if (drag.kind === 'marker') {
      state.clearings[drag.index].marker = norm(...p);
    } else if (drag.kind === 'centre') {
      const c = state.clearings[drag.index];
      const dx = p[0] - drag.start[0], dy = p[1] - drag.start[1];
      if (Math.hypot(dx, dy) > DOT_PX / 2) drag.moved = true;
      c.centre = norm(drag.centre[0] + dx, drag.centre[1] + dy);
      if (drag.marker) c.marker = norm(drag.marker[0] + dx, drag.marker[1] + dy);
    } else if (drag.kind === 'number') {
      const c = state.clearings[drag.index];
      const centre = px(c.centre);
      c.number_angle = Math.atan2(p[1] - centre[1], p[0] - centre[0]);
    } else if (drag.kind === 'forest') {
      state.forests[drag.index] = norm(...p);
    } else if (drag.kind === 'building') {
      state.buildings[drag.index].at = norm(p[0] - drag.offset[0], p[1] - drag.offset[1]);
    }
    changed();
  });

  function endDrag() {
    document.body.classList.remove('mb-dragging');
    if (drag && drag.kind === 'new-marker' && !state.clearings[drag.index].marker) {
      // A click, not a drag: the next click places the marker.
      pendingMarker = drag.index;
    }
    const wasDragging = drag !== null;
    drag = null;
    if (wasDragging) render();   // the edit pop-up hides while dragging
  }
  svg.addEventListener('pointerup', (evt) => {
    pressed.delete(evt.pointerId);
    if (evt.pointerId === activePointer) {
      activePointer = null;
      endDrag();
    }
  });
  // The browser cancels a press when it takes the gesture over (a pinch-zoom):
  // undo anything that press started.
  svg.addEventListener('pointercancel', (evt) => {
    pressed.delete(evt.pointerId);
    if (evt.pointerId === activePointer) abandonDrag();
  });

  document.addEventListener('keydown', (evt) => {
    if (cardOpen()) {
      if (evt.key === 'Escape' || evt.key === 'Enter') {
        evt.preventDefault();
        closeCard();
      }
      return;   // the board ignores keys while a step card is open
    }
    if ((evt.key === 'Delete' || evt.key === 'Backspace') && selected
        && !['INPUT', 'TEXTAREA'].includes(document.activeElement.tagName)) {
      evt.preventDefault();
      deleteSelected();
    }
    if (evt.key === 'Escape') {
      if (pendingMarker !== null) removeClearing(pendingMarker);
      selected = null;
      render();
    }
  });

  // ── Edit pop-up ──────────────────────────────────────────────────────────
  // Floats above (or, without room, below) the selected clearing, forest or
  // building: the clearing's number and suit, and a delete button.
  const board = document.getElementById('mb-board');
  const popover = document.getElementById('mb-popover');
  const popNumber = document.getElementById('mb-pop-number');
  const popSuits = document.getElementById('mb-pop-suits');

  popSuits.querySelectorAll('[data-suit]').forEach(btn => {
    const img = btn.querySelector('img');
    img.src = config.suit_icons[btn.dataset.suit];
    btn.addEventListener('click', () => {
      if (selected && selected.kind === 'clearing') {
        state.clearings[selected.index].suit = btn.dataset.suit;
        changed();
      }
    });
  });
  document.getElementById('mb-pop-minus').addEventListener('click', () => {
    if (selected && selected.kind === 'clearing') renumber(selected.index, state.clearings[selected.index].number - 1);
  });
  document.getElementById('mb-pop-plus').addEventListener('click', () => {
    if (selected && selected.kind === 'clearing') renumber(selected.index, state.clearings[selected.index].number + 1);
  });
  document.getElementById('mb-pop-delete').addEventListener('click', deleteSelected);
  document.getElementById('mb-pop-ruin').addEventListener('click', () => {
    if (selected && selected.kind === 'building') {
      const b = state.buildings[selected.index];
      b.ruin = !b.ruin;
      changed();
    }
  });
  // The pop-up sits over the board: keep its clicks away from the board.
  popover.addEventListener('pointerdown', (evt) => evt.stopPropagation());

  function selectedNode() {
    if (!selected) return null;
    if (selected.kind === 'clearing') return content.querySelector(`[data-clearing="${selected.index}"]`);
    return content.querySelector(`[data-kind="${selected.kind}"][data-index="${selected.index}"]`);
  }

  function updatePopover() {
    const editable = selected && !drag && (
      (step === STEP_CLEARINGS && selected.kind === 'clearing') || (step === STEP_FORESTS && selected.kind === 'forest')
      || (step === STEP_BUILDINGS && selected.kind === 'building'));
    const node = editable && selectedNode();
    if (!node) {
      popover.classList.remove('show');
      return;
    }
    const isClearing = selected.kind === 'clearing';
    const ruinBtn = document.getElementById('mb-pop-ruin');
    ruinBtn.classList.toggle('d-none', selected.kind !== 'building');
    if (selected.kind === 'building') {
      const on = !!state.buildings[selected.index].ruin;
      ruinBtn.classList.toggle('on', on);
      ruinBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
    }
    // A new clearing still waiting for its suit-marker spot gets the prompt on top.
    document.getElementById('mb-pop-prompt').classList.toggle('d-none', !(isClearing && pendingMarker === selected.index));
    document.getElementById('mb-pop-clearing').classList.toggle('d-none', !isClearing);
    popSuits.classList.toggle('d-none', !(isClearing && config.fixed));
    if (isClearing) {
      const c = state.clearings[selected.index];
      popNumber.textContent = c.number || '?';
      document.getElementById('mb-pop-minus').disabled = !(c.number > 1);
      document.getElementById('mb-pop-plus').disabled = !(c.number < state.clearings.length);
      popSuits.querySelectorAll('[data-suit]').forEach(btn => {
        const on = btn.dataset.suit === c.suit;
        btn.classList.toggle('selected', on);
        btn.setAttribute('aria-pressed', on ? 'true' : 'false');
      });
    }

    popover.classList.add('show');
    const area = board.getBoundingClientRect();
    const target = node.getBoundingClientRect();
    const w = popover.offsetWidth, h = popover.offsetHeight, gap = 10;
    let top = target.top - area.top - h - gap;
    const below = top < 0;
    if (below) top = target.bottom - area.top + gap;
    const centreX = target.left + target.width / 2 - area.left;
    const left = Math.min(Math.max(centreX - w / 2, 4), Math.max(area.width - w - 4, 4));
    popover.style.top = `${top}px`;
    popover.style.left = `${left}px`;
    popover.classList.toggle('below', below);
    popover.style.setProperty('--mb-arrow-x', `${Math.min(Math.max(centreX - left, 14), w - 14)}px`);
  }
  window.addEventListener('resize', updatePopover);

  // ── Fixed / random clearing suits ─────────────────────────────────────────
  // Shown on the Clearings step where the item track sits (unused there).
  const modePanel = document.getElementById('mb-suit-mode');
  let modeFolded = false;
  let modeTouched = false;   // folded or unfolded by the user (or an outside press)
  const COMPACT_BELOW = 160; // item track narrower than this on screen: compact panel

  function setFixed(fixed) {
    if (config.fixed === fixed) return;
    config.fixed = fixed;
    state.fixed = fixed;
    changed();
  }

  function updateSuitMode() {
    const show = step === STEP_CLEARINGS;
    modePanel.classList.toggle('d-none', !show);
    if (!show) return;
    const fixed = !!config.fixed;
    modePanel.classList.toggle('fixed', fixed);
    modePanel.classList.toggle('folded', modeFolded);
    modePanel.querySelectorAll('.mb-mode-image img').forEach(img => {
      img.style.opacity = (img.dataset.mode === 'fixed') === fixed ? '1' : '0';
    });
    modePanel.querySelectorAll('.mb-mode-switch button, .mb-mode-icons button').forEach(btn => {
      btn.setAttribute('aria-pressed', (btn.dataset.mode === 'fixed') === fixed ? 'true' : 'false');
    });
    const fold = document.getElementById('mb-mode-fold');
    fold.setAttribute('aria-expanded', modeFolded ? 'false' : 'true');

    // Anchor at the item grid's top-left, in board pixels; keep it inside the board.
    const slots = itemSlots();
    const half = ITEM.step_x * PPL * 0.5;
    const area = board.getBoundingClientRect();
    const scale = area.width / W;
    const gridLeft = (Math.min(...slots.map(s => s[0])) - half) * scale;
    const gridTop = (Math.min(...slots.map(s => s[1])) - half) * scale;
    const gridWidth = (Math.max(...slots.map(s => s[0])) - Math.min(...slots.map(s => s[0])) + 2 * half) * scale;
    // Small screens: start folded down to icons; unfold for the full card.
    const compact = gridWidth < COMPACT_BELOW;
    if (compact && !modeTouched) modeFolded = true;
    modePanel.classList.toggle('compact', compact);
    modePanel.classList.toggle('folded', modeFolded);
    fold.setAttribute('aria-expanded', modeFolded ? 'false' : 'true');
    const width = compact && modeFolded
      ? Math.min(Math.max(gridWidth, 120), area.width - 8)
      : Math.min(Math.max(gridWidth, 200), 300, area.width - 8);
    modePanel.style.width = `${width}px`;
    const left = Math.min(Math.max(gridLeft, 4), area.width - width - 4);
    const top = Math.min(Math.max(gridTop, 4), Math.max(area.height - modePanel.offsetHeight - 4, 4));
    modePanel.style.left = `${left}px`;
    modePanel.style.top = `${top}px`;
  }

  modePanel.addEventListener('pointerdown', (evt) => evt.stopPropagation());
  modePanel.querySelectorAll('.mb-mode-switch button').forEach(btn => {
    btn.addEventListener('click', () => setFixed(btn.dataset.mode === 'fixed'));
  });
  modePanel.querySelectorAll('.mb-mode-icons button').forEach(btn => {
    btn.addEventListener('click', () => setFixed(btn.dataset.mode === 'fixed'));
  });
  document.getElementById('mb-mode-image').addEventListener('click', () => setFixed(!config.fixed));
  document.getElementById('mb-mode-fold').addEventListener('click', () => {
    modeFolded = !modeFolded;
    modeTouched = true;
    updateSuitMode();
  });
  window.addEventListener('resize', updateSuitMode);
  // The panel opens unfolded, then folds on the first press anywhere outside it
  // (except on a step card), once you start working.
  document.addEventListener('pointerdown', (evt) => {
    if (modeFolded || step !== STEP_CLEARINGS || modePanel.classList.contains('d-none')) return;
    if (modePanel.contains(evt.target) || evt.target.closest('#mb-step-card')) return;
    modeFolded = true;
    modeTouched = true;
    updateSuitMode();
  }, true);
  window.addEventListener('scroll', updatePopover, { passive: true });

  // ── Steps and saving ──────────────────────────────────────────────────────
  function goTo(n) {
    step = Math.min(Math.max(n, 0), STEPS.length - 1);
    selected = null;
    if (pendingMarker !== null && !state.clearings[pendingMarker].marker) removeClearing(pendingMarker);
    pendingMarker = null;
    render();
    introduceStep();
  }
  document.querySelectorAll('#mb-progress [data-step]').forEach(btn => {
    btn.addEventListener('click', () => {
      if (Number(btn.dataset.step) !== step) goTo(Number(btn.dataset.step));
    });
  });

  // ── Step cards ────────────────────────────────────────────────────────────
  // A card introduces each step; the info button beside the title reopens it.
  const card = document.getElementById('mb-step-card');
  const cardOk = document.getElementById('mb-card-ok');
  let cardReturn = null;
  const introduced = new Set();   // steps whose card has shown on this visit

  // Show a step's card the first time it's reached; going back doesn't
  // reopen it (the info button does).
  // Stages reached so far (saved with the layout): a save only updates the
  // Map's counts for these, so an unvisited stage never overwrites them with 0.
  const STAGE_OF_STEP = { [STEP_CLEARINGS]: 'clearings', [STEP_BUILDINGS]: 'buildings', [STEP_FORESTS]: 'forests' };
  const visited = new Set(state.visited || []);

  function introduceStep() {
    if (STAGE_OF_STEP[step] && !visited.has(STAGE_OF_STEP[step])) {
      visited.add(STAGE_OF_STEP[step]);
      state.visited = Array.from(visited).sort();
    }
    if (introduced.has(step)) return;
    introduced.add(step);
    openCard();
  }

  function cardOpen() {
    return card.classList.contains('show');
  }

  function openCard() {
    document.getElementById('mb-card-heading').textContent = STEPS[step].heading;
    // On touch screens, mention that two fingers zoom (one finger edits).
    const touch = window.matchMedia && window.matchMedia('(pointer: coarse)').matches;
    document.getElementById('mb-card-hint').textContent =
      STEPS[step].hint + (touch && step !== STEPS.length - 1 ? ' Pinch with two fingers to zoom in.' : '');
    cardReturn = document.activeElement;
    card.classList.add('show');
    card.setAttribute('aria-hidden', 'false');
    cardOk.focus({ preventScroll: true });
  }

  function closeCard() {
    card.classList.remove('show');
    card.setAttribute('aria-hidden', 'true');
    if (cardReturn && document.body.contains(cardReturn)) cardReturn.focus({ preventScroll: true });
    cardReturn = null;
  }

  cardOk.addEventListener('click', closeCard);
  card.addEventListener('click', (evt) => {
    if (evt.target === card) closeCard();   // the backdrop, not the card
  });
  document.getElementById('mb-info').addEventListener('click', openCard);
  document.getElementById('mb-back').addEventListener('click', () => goTo(step - 1));
  document.getElementById('mb-next').addEventListener('click', () => goTo(step + 1));
  document.getElementById('mb-save').addEventListener('click', () => save(false));
  document.getElementById('mb-generate').addEventListener('click', () => save(true));

  function csrfToken() {
    const input = document.querySelector('input[name="csrfmiddlewaretoken"]');
    return input ? input.value : '';
  }

  async function save(generate) {
    const box = document.getElementById('mb-messages');
    box.innerHTML = '';
    let result;
    try {
      const response = await fetch(config.save_url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
        body: JSON.stringify({ state, generate }),
      });
      result = await response.json();
    } catch (err) {
      box.appendChild(alertBox('danger', ['Saving failed. Check your connection and try again.']));
      return;
    }
    if (result.saved) {
      dirty = false;
      showSaved(true);
    }
    if (result.changes && result.changes.length) {
      box.appendChild(alertBox('info', ['Updated the post: ' + result.changes.join(', ') + '.']));
      // Only the stages the server counted now match the Map.
      const KEYS = { clearings: ['clearings'], buildings: ['buildings', 'ruins'], forests: ['forests'] };
      for (const stage of result.counted || []) {
        for (const key of KEYS[stage] || []) config.expected[key] = counts()[key];
      }
    }
    // Problems the review step already lists aren't repeated: that list is
    // highlighted instead. Errors only block generating (the layout is still
    // saved), so a plain save doesn't show them.
    const shown = new Set(step === STEPS.length - 1 ? localErrors() : []);
    const errors = result.errors || [];
    const newErrors = generate ? errors.filter(e => !shown.has(e)) : [];
    const warnings = (result.warnings || []).filter(w => !shown.has(w));
    const flash = errors.some(e => shown.has(e));
    if (newErrors.length) box.appendChild(alertBox('danger', newErrors));
    if (warnings.length) box.appendChild(alertBox('warning', warnings));
    if (result.redirect) {
      window.location = result.redirect;
      return;
    }
    if (result.message) box.appendChild(alertBox(result.ok ? 'success' : 'danger', [result.message]));
    render();
    // After render(): it rebuilds the review list, which would drop the pulse.
    if (flash) highlightReview();
  }

  window.addEventListener('beforeunload', (evt) => {
    if (dirty) {
      evt.preventDefault();
      evt.returnValue = '';
    }
  });

  render();
  introduceStep();
})();
