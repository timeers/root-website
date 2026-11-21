// Map builder: lay out a Map post's VP track, items, clearings, forests and
// buildings on its board image. The layout is kept in 0-1 image coordinates
// (u right, v down) and saved to the server, which builds the TTS JSON from it
// (the_keep/services/tts_map.py). Geometry here mirrors MapGeometry there.
//
// Shared by the Keep (a Map post's board image) and the Forge (a map built
// from scratch), switched by config flags that are off on the Keep:
//   upload_step  a first step to upload the map's art and place the Root logo
//   print        art printed on the map (VP track, items, logo), drawn under
//                the pieces so the layout is built around it
//   readonly     just the board, as on the review step, with no editing
(function () {
  'use strict';

  const config = JSON.parse(document.getElementById('map-builder-config').textContent);
  const W = config.width, H = config.height;
  const svg = document.getElementById('mb-svg');
  const NS = 'http://www.w3.org/2000/svg';
  const PPL = config.px_per_local;              // pixels per local unit
  const STEP_TRACK_PX = config.track.step * PPL;
  const ITEM = config.item_grid;
  const BUILDING_PX = config.building_size * PPL;   // building square size
  const DOT_PX = Math.max(W, H) / 120;
  const STROKE = Math.max(W, H) / 600;

  const state = config.state;
  state.clearings = state.clearings || [];
  state.forests = state.forests || [];
  state.buildings = state.buildings || [];
  state.fixed = !!config.fixed;   // the Map's setting; saved back with the layout

  // name: the progress bar's label (short: at medium widths, when the name is
  // long). config.step_text can replace any step's text, keyed by step key.
  const STEPS = [
    { key: 'track', heading: 'VP and Item Tracks', name: 'VP & Item Tracks', short: 'Tracks',
      hint: 'Drag the item grid onto the printed item spaces. Drag the VP track up or down so the blue dots align with the printed 0-30 track.' },
    { key: 'clearings', heading: 'Clearings', name: 'Clearings',
      hint: 'Click the middle of a clearing, then drag (or click) to where its suit marker goes. Drag the number tile around the circle. Select a clearing to re-number or delete it.' },
    { key: 'buildings', heading: 'Buildings & Ruins', name: 'Buildings & Ruins', short: 'Buildings',
      hint: 'Click each building slot. Drag inside a square to move it and press x to remove it. Select a building and click the ruin tile to make it a ruin.' },
    { key: 'forests', heading: 'Forests', name: 'Forests',
      hint: 'Click the middle of each forest. Drag a dot to move it and press x to remove it. These are used for Relic placements.' },
    { key: 'review', heading: 'Review', name: 'Review',
      hint: 'Check the count of each element, then click "Complete" to generate the final JSON.' },
  ];
  if (config.upload_step) {
    STEPS.unshift({ key: 'upload', heading: 'Map Name & Art', name: 'Start', short: 'Start',
                    hint: 'Choose a name for your map and upload your art.' });
  }
  for (const s of STEPS) Object.assign(s, (config.step_text || {})[s.key] || {});

  // Step numbers, in the order the steps appear (Review is the last; there's
  // no upload step on the Keep, so STEP_UPLOAD is -1 there).
  const stepOf = (key) => STEPS.findIndex(s => s.key === key);
  const STEP_UPLOAD = stepOf('upload'), STEP_TRACK = stepOf('track'), STEP_CLEARINGS = stepOf('clearings'),
        STEP_BUILDINGS = stepOf('buildings'), STEP_FORESTS = stepOf('forests'), STEP_REVIEW = STEPS.length - 1;
  const PRINT = config.print || null;   // the Forge's printed art (see print_art in tts_map.py)

  let step = config.readonly ? STEP_REVIEW : 0;
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

  function logoAt() {
    return state.logo || config.default_logo;
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

  // A step's pieces appear from that step on: never before it (they'd be in
  // the way of what's being placed now), dimmed on the steps after it, and at
  // full strength on the review (the last step), which shows the whole map.
  // Step numbers follow STEPS, so a step added or moved keeps the rule; a step
  // this builder doesn't have (-1) is never shown.
  function shownFrom(own) {
    return own >= 0 && step >= own;
  }

  // The group for a step's pieces: full strength and editable on their own
  // step, clickable on the review (to jump to their step; nothing is dragged
  // there), dimmed and inert in between, absent before their step.
  function layer(own) {
    const active = step === own || step === STEP_REVIEW;
    const g = el('g', { opacity: active ? 1 : 0.35, 'pointer-events': active ? 'auto' : 'none' });
    if (!shownFrom(own)) g.setAttribute('display', 'none');
    return g;
  }

  function plus(g, x, y, size, colour, data) {
    el('line', { x1: x - size, y1: y, x2: x + size, y2: y, stroke: colour, 'stroke-width': STROKE * 1.5, ...data }, g);
    el('line', { x1: x, y1: y - size, x2: x, y2: y + size, stroke: colour, 'stroke-width': STROKE * 1.5, ...data }, g);
  }

  // The Forge's printed art, under the pieces: the VP track, the items and the
  // Root logo. It belongs to the Tracks step (shownFrom), but it's part of the
  // map, so it's never dimmed like the pieces' layers.
  function drawPrinted() {
    if (!PRINT || !shownFrom(STEP_TRACK)) return;
    const under = el('g', { 'pointer-events': 'none' });
    const image = (art, cx, cy, parent, data) => {
      const w = art.w * PPL, h = art.h * PPL;
      return el('image', { href: art.url, x: cx - w / 2, y: cy - h / 2, width: w, height: h,
                           preserveAspectRatio: 'none', ...data }, parent);
    };
    image(PRINT.vp, imageU(0) * W, state.track_y * H, under);
    const slots = itemSlots();
    const xs = slots.map(s => s[0]), ys = slots.map(s => s[1]);
    image(PRINT.items, (Math.min(...xs) + Math.max(...xs)) / 2, (Math.min(...ys) + Math.max(...ys)) / 2, under);
    // The logo can be picked up on the Tracks step, and clicked on the review.
    const onLogo = step === STEP_TRACK || step === STEP_REVIEW;
    const g = el('g', { 'pointer-events': onLogo && !config.readonly ? 'auto' : 'none' });
    const [lx, ly] = px(logoAt());
    const logo = image(PRINT.logo, lx, ly, g, { 'data-kind': 'logo', style: 'cursor: move' });
    if (step === STEP_TRACK) {
      const w = Number(logo.getAttribute('width')), h = Number(logo.getAttribute('height'));
      el('rect', { x: lx - w / 2, y: ly - h / 2, width: w, height: h, fill: 'none', stroke: '#0d6efd',
                   'stroke-width': STROKE * 1.5, 'stroke-dasharray': `${STROKE * 5} ${STROKE * 4}`,
                   'pointer-events': 'none' }, g);
    }
  }

  function render() {
    content.innerHTML = '';
    drawPrinted();
    // Track and items.
    const g0 = layer(STEP_TRACK);
    const rows = trackRows(), cols = trackColumns();
    const band = el('rect', {
      x: Math.min(...cols) - STEP_TRACK_PX, y: rows[0] - STEP_TRACK_PX * 0.6,
      width: Math.max(...cols) - Math.min(...cols) + 2 * STEP_TRACK_PX, height: rows[2] - rows[0] + STEP_TRACK_PX * 1.2,
      fill: 'rgba(13,110,253,0.15)', stroke: '#0d6efd', 'stroke-width': STROKE, 'data-kind': 'track',
      style: 'cursor: ns-resize',
    }, g0);
    // The track's grid: 31 square cells (the printed cells are square, one cell
    // spacing on a side), each centred on a blue dot, to line up with the art.
    const cellLeft = Math.min(...cols) - STEP_TRACK_PX / 2;
    const cellTop = rows[1] - STEP_TRACK_PX / 2;
    el('rect', { x: cellLeft, y: cellTop, width: STEP_TRACK_PX * cols.length, height: STEP_TRACK_PX,
                 fill: 'none', stroke: '#0d6efd', 'stroke-width': STROKE * 1.5, 'pointer-events': 'none' }, g0);
    for (let i = 1; i < cols.length; i++) {
      const x = cellLeft + i * STEP_TRACK_PX;
      el('line', { x1: x, y1: cellTop, x2: x, y2: cellTop + STEP_TRACK_PX, stroke: '#0d6efd',
                   'stroke-width': STROKE, 'pointer-events': 'none' }, g0);
    }
    // The middle row (on the printed numbers) in blue; the rows above and below
    // in grey. Not the blue ones in the Forge: the printed track drawn under
    // them is already where it'll be, and the dots would hide its numbers.
    rows.forEach((y, row) => {
      if (row === 1 && PRINT) return;
      const fill = row === 1 ? '#0d6efd' : '#6c757d';
      for (const x of cols) el('circle', { cx: x, cy: y, r: STEP_TRACK_PX * 0.18, fill, 'pointer-events': 'none' }, g0);
    });
    const slots = itemSlots();
    const size = ITEM.step_x * PPL * 0.85;
    const xs = slots.map(s => s[0]), ys = slots.map(s => s[1]);
    // On the Clearings, Buildings & Ruins and Forests steps a card sits over
    // the item grid instead.
    if (![STEP_CLEARINGS, STEP_BUILDINGS, STEP_FORESTS].includes(step)) {
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
    const g1 = layer(STEP_CLEARINGS);
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
    const g2 = layer(STEP_FORESTS);
    state.forests.forEach((f, i) => {
      const p = px(f);
      const isSel = selected && selected.kind === 'forest' && selected.index === i;
      el('circle', { cx: p[0], cy: p[1], r: DOT_PX * 1.4, fill: '#198754', stroke: isSel ? '#dc3545' : '#fff',
                     'stroke-width': STROKE * 2, 'data-kind': 'forest', 'data-index': i, style: 'cursor: move' }, g2);
    });

    // Buildings and ruins (steps 3 and 4).
    const g3 = layer(STEP_BUILDINGS);
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
    const colours = { fox: '#EC2121', rabbit: '#FFE400', mouse: '#F78B57', bird: '#44C3BC', city: '#9C5A2E' };
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
      // The Forge's later steps wait for the map's art.
      btn.disabled = n > STEP_UPLOAD && artStepLocked();
      if (n === step) btn.setAttribute('aria-current', 'step'); else btn.removeAttribute('aria-current');
    });
    document.getElementById('mb-progress-thumb').style.transform = `translateX(${step * 100}%)`;
    document.getElementById('mb-heading').textContent = STEPS[step].heading;

    updatePopover();
    updateSuitMode();
    updateCountCards();
    updateUpload();

    document.getElementById('mb-back').disabled = step === 0;
    document.getElementById('mb-next').classList.toggle('d-none', step === STEP_REVIEW);
    const next = document.getElementById('mb-next');
    next.disabled = step === STEP_UPLOAD && !artStepDone();
    next.title = next.disabled ? artStepMissing() : '';
    // A new map can't be saved until it's created (its name and art, then Next).
    document.getElementById('mb-save').disabled = isNew() && !artStepDone();
    document.getElementById('mb-generate').classList.toggle('d-none', step !== STEP_REVIEW);
    document.getElementById('mb-review').classList.toggle('d-none', step !== STEP_REVIEW || !!config.readonly);
    board.classList.toggle('mb-review', step === STEP_REVIEW && !config.readonly);
    if (step === STEP_REVIEW && !config.readonly) renderReview();
  }

  // Review summary: a tile per piece, drawn like the board draws it, plus how
  // the clearing suits are handled.
  function iconSvg(kind) {
    if (kind === 'clearing') {
      if (markerSymbol) {
        // Inset in a slightly larger box: the outline's stroke sits half outside
        // the shape and would otherwise be cut off at the edges.
        return '<svg width="40" height="22" viewBox="0 0 40 22" overflow="visible" aria-hidden="true">'
          + '<use href="#mb-marker" x="3" y="3" width="34" height="17" style="color:#fff;--mb-marker-stroke:#231f20"></use></svg>';
      }
      return '<svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">'
        + '<circle cx="9" cy="9" r="7" fill="#fff" stroke="#231f20" stroke-width="1.5"/></svg>';
    }
    if (kind === 'forest') {
      return '<svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true">'
        + '<circle cx="11" cy="11" r="8" fill="#198754" stroke="#fff" stroke-width="3"/>'
        + '<circle cx="11" cy="11" r="9.5" fill="none" stroke="rgba(0,0,0,.15)" stroke-width="1"/></svg>';
    }
    const ruin = kind === 'ruin';
    return '<svg width="24" height="24" viewBox="0 0 24 24" aria-hidden="true">'
      + `<rect x="2" y="2" width="20" height="20" rx="4" fill="${ruin ? '#dc3545' : '#fff'}" stroke="#333" stroke-width="1.5"/>`
      + (ruin
        ? '<text x="12" y="12.5" fill="#fff" font-size="13" font-weight="700" text-anchor="middle" dominant-baseline="central">R</text>'
        : '<path d="M12 7v10M7 12h10" stroke="#333" stroke-width="2" stroke-linecap="round"/>')
      + '</svg>';
  }

  function renderReview() {
    const c = counts();
    // Each tile opens the step where that piece is edited.
    const tiles = [
      ['clearing', 'Clearings', c.clearings, config.expected.clearings, STEP_CLEARINGS],
      ['building', 'Building Slots', c.buildings, config.expected.buildings, STEP_BUILDINGS],
      ['ruin', 'Ruins', c.ruins, config.expected.ruins, STEP_BUILDINGS],
      ['forest', 'Forests', c.forests, config.expected.forests, STEP_FORESTS],
    ];
    const grid = document.getElementById('mb-review-counts');
    grid.innerHTML = '';
    for (const [kind, label, n, expected, editStep] of tiles) {
      const tile = document.createElement('button');
      tile.type = 'button';
      tile.className = 'mb-sum-tile';
      tile.setAttribute('aria-label', `${label}: ${n}. Edit ${label.toLowerCase()}`);
      tile.addEventListener('click', () => goTo(editStep));
      tile.innerHTML = `<div class="mb-sum-icon">${iconSvg(kind)}</div>`
        + `<div class="mb-sum-count">${n}</div><div class="mb-sum-label"></div>`;
      tile.querySelector('.mb-sum-label').textContent = label;
      if (expected !== null && expected !== undefined && expected !== n) {
        const pill = document.createElement('span');
        pill.className = 'mb-sum-pill';
        pill.textContent = `${expected} → ${n}`;
        pill.title = `The Map currently has ${expected}. Completing the map updates it to ${n}.`;
        tile.appendChild(pill);
      }
      grid.appendChild(tile);
    }

    // How the clearing suits are handled.
    const suits = document.getElementById('mb-review-suits');
    suits.innerHTML = '';
    if (config.fixed) {
      const tally = { fox: 0, rabbit: 0, mouse: 0, bird: 0, city: 0 };
      const SUIT_NAMES = { fox: 'Fox', rabbit: 'Rabbit', mouse: 'Mouse', bird: 'Bird', city: 'Lost City' };
      let unset = 0;
      for (const cl of state.clearings) {
        if (tally[cl.suit] !== undefined) tally[cl.suit] += 1; else unset += 1;
      }
      for (const [suit, n] of Object.entries(tally)) {
        if (!n) continue;
        const chip = document.createElement('span');
        chip.className = 'mb-suit-chip';
        chip.title = SUIT_NAMES[suit];
        chip.innerHTML = `<img src="${config.suit_icons[suit]}" alt=""><span></span>`;
        chip.querySelector('span').textContent = `${chip.title} ${n}`;
        suits.appendChild(chip);
      }
      if (unset) {
        const chip = document.createElement('span');
        chip.className = 'mb-suit-chip mb-suit-chip-unset';
        chip.textContent = `No suit ${unset}`;
        suits.appendChild(chip);
      }
    } else {
      const note = document.createElement('span');
      note.className = 'mb-suit-note';
      note.innerHTML = `<img src="${config.random_icon}" alt=""><span></span>`;
      note.querySelector('span').textContent = 'Random suits: shuffled each time the map loads.';
      suits.appendChild(note);
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

  // A press that turns into a pinch-zoom undoes whatever it created -- even if
  // the fingers moved first (which sets a new clearing's marker, or drags a new
  // forest or building).
  function abandonDrag() {
    if (drag && drag.kind === 'new-marker') removeClearing(drag.index);
    if (drag && drag.created && drag.kind === 'forest') {
      state.forests.splice(drag.index, 1);
      selected = null;
    }
    if (drag && drag.created && drag.kind === 'building') {
      state.buildings.splice(drag.index, 1);
      selected = null;
    }
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
    if (config.readonly) return;
    activePointer = evt.pointerId;
    // Don't let a press on the board start a text selection or a native drag,
    // and keep the whole page unselectable until the drag ends.
    evt.preventDefault();
    window.getSelection().removeAllRanges();
    document.body.classList.add('mb-dragging');
    const p = pointer(evt);
    const h = hit(evt);
    svg.setPointerCapture(evt.pointerId);

    // Review: clicking a piece opens its step with it selected for editing.
    if (step === STEP_REVIEW) {
      if (h) editFromReview(h);
      return;
    }

    if (step === STEP_TRACK && h) {
      if (h.kind === 'logo') {
        const [x0, y0] = px(logoAt());
        drag = { kind: 'logo', offset: [p[0] - x0, p[1] - y0] };
      }
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
      drag = { kind: 'forest', index: selected.index, created: true };
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
      drag = { kind: 'building', index: selected.index, offset: [0, 0], created: true };
      changed();
      return;
    }
  });

  // From the review: go to the piece's step and select it (its edit pop-up opens).
  function editFromReview(h) {
    const target = {
      track: [STEP_TRACK, null], items: [STEP_TRACK, null], logo: [STEP_TRACK, null],
      centre: [STEP_CLEARINGS, 'clearing'], marker: [STEP_CLEARINGS, 'clearing'], number: [STEP_CLEARINGS, 'clearing'],
      building: [STEP_BUILDINGS, 'building'], forest: [STEP_FORESTS, 'forest'],
    }[h.kind];
    if (!target || target[0] < 0) return;
    activePointer = null;
    document.body.classList.remove('mb-dragging');
    goTo(target[0]);
    if (target[1] && Number.isInteger(h.index)) {
      selected = { kind: target[1], index: h.index };
      render();
    }
  }

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
    } else if (drag.kind === 'logo') {
      state.logo = norm(p[0] - drag.offset[0], p[1] - drag.offset[1]);
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

  // A press outside the board deselects, like Escape (a clearing still waiting
  // for its marker is cancelled). Presses on the board select or place pieces
  // themselves; step cards are left alone. Checked on the event's path, not its
  // target: selecting a piece redraws the board at once, detaching the pressed
  // element before the event gets here.
  document.addEventListener('pointerdown', (evt) => {
    if (selected === null && pendingMarker === null) return;
    const path = evt.composedPath();
    if (path.includes(board) || path.includes(document.getElementById('mb-step-card'))) return;
    if (pendingMarker !== null) removeClearing(pendingMarker);
    selected = null;
    render();
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

    const trackBox = itemTrackBox();
    const { width: gridWidth, area } = trackBox;
    setCompactSizes(trackBox);
    // Small screens: start folded down to icons; unfold for the full card.
    const compact = gridWidth < COMPACT_BELOW;
    if (compact && !modeTouched) modeFolded = true;
    modePanel.classList.toggle('compact', compact);
    modePanel.classList.toggle('folded', modeFolded);
    fold.setAttribute('aria-expanded', modeFolded ? 'false' : 'true');
    // Folded on small screens: as wide as its content (the two icons, sized from
    // the item track, plus the chevron) -- a fixed minimum left gaps around them.
    const icons = modePanel.querySelector('.mb-mode-icons');
    const width = compact && modeFolded
      ? Math.min(icons.scrollWidth + 12, area.width - 8)
      : Math.min(Math.max(gridWidth, 200), 300, area.width - 8);
    modePanel.style.width = `${width}px`;
    // Folded, the card should stay within the item track, so a title that would
    // wrap falls back to the short one. Expanded (taller anyway) it's always full.
    const title = document.getElementById('mb-mode-title');
    title.textContent = title.dataset.full;
    if (modeFolded) {
      const lineHeight = parseFloat(getComputedStyle(title).lineHeight) || title.offsetHeight;
      if (title.offsetHeight > lineHeight * 1.5) title.textContent = title.dataset.short;
    }
    placeOnItemTrack(modePanel, width, Math.min(Math.max(gridWidth, 200), 300, area.width - 8));
  }

  // The item grid's box on screen (board pixels): where the step cards sit.
  function itemTrackBox() {
    const slots = itemSlots();
    const half = ITEM.step_x * PPL * 0.5;
    const area = board.getBoundingClientRect();
    const scale = area.width / W;
    const xs = slots.map(s => s[0]), ys = slots.map(s => s[1]);
    return {
      area, scale,
      left: (Math.min(...xs) - half) * scale,
      top: (Math.min(...ys) - half) * scale,
      width: (Math.max(...xs) - Math.min(...xs) + 2 * half) * scale,
      height: (Math.max(...ys) - Math.min(...ys) + 2 * half) * scale,
    };
  }

  // Small screens: the folded cards' badges, icons and chevrons are sized from
  // the item track's height, so the cards stay about as tall as the track.
  // Sets --mb-c (badge / icon size) and --mb-f (chevron size) on the board;
  // returns the chevron size for width sums.
  function setCompactSizes(box) {
    const c = Math.round(Math.min(Math.max(box.height - 14, 16), 30));
    const f = Math.min(24, c);
    board.style.setProperty('--mb-c', `${c}px`);
    board.style.setProperty('--mb-f', `${f}px`);
    return f;
  }

  // Anchor a card at the item grid's top-left, kept inside the board. A card
  // that wouldn't fit below the grid once unfolded (the grid near the board's
  // bottom) is anchored at the grid's bottom instead and grows upwards; its
  // chevron turns to match (mb-grow-up, map_builder.css).
  // width: a pixel width, or null to fit the card's content.
  // openWidth: the card's width once unfolded, if different.
  function placeOnItemTrack(card, width, openWidth = width) {
    const box = itemTrackBox();
    card.style.width = width === null ? 'auto' : `${width}px`;
    if (width === null) width = card.offsetWidth;
    const left = Math.min(Math.max(box.left, 4), box.area.width - width - 4);
    card.style.left = `${left}px`;
    const up = box.top + openHeight(card, openWidth === null ? width : openWidth) > box.area.height - 4;
    card.classList.toggle('mb-grow-up', up);
    if (up) {
      card.style.top = 'auto';
      card.style.bottom = `${Math.max(box.area.height - (box.top + box.height), 4)}px`;
    } else {
      card.style.bottom = 'auto';
      card.style.top = `${Math.min(Math.max(box.top, 4), Math.max(box.area.height - card.offsetHeight - 4, 4))}px`;
    }
  }

  // A card's height once unfolded, measured on a hidden copy: a new element
  // starts in its final state (no transition), and folded small-screen cards
  // hide parts of themselves entirely, so the real card can't be measured.
  // Cached: cards are placed on every render, drags included.
  const openHeights = new Map();
  function openHeight(card, width) {
    const key = `${step}|${width}|${card.classList.contains('compact')}`;
    if (openHeights.get(card) && openHeights.get(card).key === key) return openHeights.get(card).height;
    const copy = card.cloneNode(true);
    copy.classList.remove('folded', 'd-none');
    // The copy keeps its own id (the cards are styled by id) for the moment
    // it's on the page; its children's ids are dropped.
    copy.querySelectorAll('[id]').forEach(node => node.removeAttribute('id'));
    copy.querySelectorAll('[data-full]').forEach(node => { node.textContent = node.dataset.full; });
    Object.assign(copy.style, { visibility: 'hidden', width: `${width}px`, left: '0', top: '0', bottom: 'auto' });
    copy.setAttribute('aria-hidden', 'true');
    board.appendChild(copy);
    const height = copy.offsetHeight;
    copy.remove();
    openHeights.set(card, { key, height });
    return height;
  }

  // ── Count cards ───────────────────────────────────────────────────────────
  // Buildings & Ruins and Forests: a card over the item track. Clearings: a
  // slim pill centred on the VP track (the item track holds Clearing Distribution).
  const countCard = document.getElementById('mb-count-card');
  let countExpanded = false;   // small screens: the count card's full rows shown
  const clearingPill = document.getElementById('mb-clearing-count');
  const COUNT_ROWS = {
    [STEP_CLEARINGS]: [['clearing', 'clearings', 'Clearings']],
    // An optional 4th entry is a shorter label for when the full one doesn't fit.
    [STEP_BUILDINGS]: [['building', 'buildings', 'Building Slots', 'Buildings'], ['ruin', 'ruins', 'Ruins']],
    [STEP_FORESTS]: [['forest', 'forests', 'Forests']],
  };

  // compact: no labels. brief: just the current number (no "/ expected").
  function countRowsHtml(rows, compact, brief = false, short = false) {
    const c = counts();
    return rows.map(([icon, key, fullLabel, shortLabel]) => {
      const label = short && shortLabel ? shortLabel : fullLabel;
      const n = c[key], expected = config.expected[key];
      const known = expected !== null && expected !== undefined;
      const off = known && n !== expected ? ' off' : '';
      const figure = known && !brief ? `${n} / ${expected}` : `${n}`;
      return `<div class="mb-count-row"><span class="mb-count-icon">${iconSvg(icon)}</span>`
        + `<span class="mb-count-figure${off}">${figure}</span>`
        + (compact ? '' : `<span class="mb-count-label">${label}</span>`) + '</div>';
    }).join('');
  }

  // Buildings & Ruins / Forests card, built like the Clearing Distribution
  // card: on small screens it folds to a row of badges (each count drawn inside
  // its piece's icon) with a corner chevron that slides open the full rows
  // ("12 / 22 Building Slots"); larger screens always show the full rows.
  // Built once per step and screen size and then updated in place, so the
  // fold animates.
  let countCardKey = null;

  function countBadgesHtml(rows) {
    const c = counts();
    return rows.map(([icon, key, label]) => {
      const n = c[key], expected = config.expected[key];
      const off = expected !== null && expected !== undefined && n !== expected ? ' off' : '';
      return `<span class="mb-count-badge mb-badge-${icon}${off}" title="${label}">${n}</span>`;
    }).join('');
  }

  function updateCountCard(rows, box, compact) {
    const key = `${step}|${compact}`;
    if (key !== countCardKey) {
      countCardKey = key;
      countCard.classList.toggle('compact', compact);
      countCard.innerHTML =
        (compact ? '<button type="button" class="mb-count-fold" aria-label="Show details" aria-expanded="false">'
          + '<i class="bi bi-chevron-up"></i></button>'
          + '<div class="mb-count-brief"><div class="mb-count-badges"></div></div>' : '')
        + '<div class="mb-count-body"><div class="mb-count-body-inner"></div></div>';
    }
    const inner = countCard.querySelector('.mb-count-body-inner');
    inner.innerHTML = countRowsHtml(rows, false);
    if (!compact) {
      countCard.classList.remove('folded');
      placeOnItemTrack(countCard, Math.min(Math.max(box.width, 150), 240, box.area.width - 8));
      // Just above small screens the card is narrow: use the short labels
      // ("Buildings") when the full ones don't fit on their line.
      if (inner.scrollWidth > inner.clientWidth) inner.innerHTML = countRowsHtml(rows, false, false, true);
      return;
    }
    const folded = !countExpanded;
    countCard.classList.toggle('folded', folded);
    countCard.querySelector('.mb-count-badges').innerHTML = countBadgesHtml(rows);
    const fold = countCard.querySelector('.mb-count-fold');
    fold.setAttribute('aria-expanded', folded ? 'false' : 'true');
    fold.setAttribute('aria-label', folded ? 'Show details' : 'Hide details');
    // Folded: as wide as the badges plus the chevron. Expanded: room for the rows.
    // Padding is the same in both states: 6px left, the chevron plus 8px right.
    const f = setCompactSizes(box);
    const openWidth = Math.min(Math.max(countCard.querySelector('.mb-count-body-inner').scrollWidth + 6 + f + 8, 180),
                               box.area.width - 8);
    const width = folded
      ? Math.min(countCard.querySelector('.mb-count-badges').scrollWidth + 6 + f + 8, box.area.width - 8)
      : openWidth;
    placeOnItemTrack(countCard, width, openWidth);
  }

  function updateCountCards() {
    const box = itemTrackBox();
    const compact = box.width < COMPACT_BELOW;

    // Buildings & Ruins / Forests card.
    const rows = step === STEP_CLEARINGS ? null : COUNT_ROWS[step];
    countCard.classList.toggle('d-none', !rows);
    if (rows) updateCountCard(rows, box, compact);

    // Clearings pill, centred on the VP track and kept between its grey rows.
    const onClearings = step === STEP_CLEARINGS;
    clearingPill.classList.toggle('d-none', !onClearings);
    if (onClearings) {
      const cols = trackColumns();
      const gap = config.track.row_gap * PPL * box.scale;
      const height = Math.min(Math.max(gap * 1.8, 24), 40);
      clearingPill.style.height = `${height}px`;
      clearingPill.style.fontSize = `${Math.min(Math.max(height * 0.45, 12), 17)}px`;
      clearingPill.style.setProperty('--mb-pill-icon', `${Math.round(Math.min(Math.max(height * 0.45, 12), 18))}px`);
      // The VP track spans nearly the whole board, so the full text fits even on
      // small screens; drop the label only if the pill would be wider than the track.
      clearingPill.innerHTML = countRowsHtml(COUNT_ROWS[STEP_CLEARINGS], false);
      const trackWidth = (Math.max(...cols) - Math.min(...cols)) * box.scale;
      if (clearingPill.offsetWidth > trackWidth) {
        clearingPill.innerHTML = countRowsHtml(COUNT_ROWS[STEP_CLEARINGS], true);
      }
      const cx = (Math.min(...cols) + Math.max(...cols)) / 2 * box.scale;
      const cy = state.track_y * H * box.scale;
      const w = clearingPill.offsetWidth;
      const left = Math.min(Math.max(cx - w / 2, 4), box.area.width - w - 4);
      clearingPill.style.left = `${left}px`;
      clearingPill.style.top = `${cy - height / 2}px`;
    }
  }

  countCard.addEventListener('pointerdown', (evt) => evt.stopPropagation());
  countCard.addEventListener('click', (evt) => {
    if (!evt.target.closest('.mb-count-fold')) return;
    countExpanded = !countExpanded;
    updateCountCards();
  });
  // Like Clearing Distribution: a press anywhere outside an expanded card
  // (except on a step card) folds it again.
  document.addEventListener('pointerdown', (evt) => {
    if (!countExpanded || countCard.contains(evt.target) || evt.target.closest('#mb-step-card')) return;
    countExpanded = false;
    updateCountCards();
  }, true);
  clearingPill.addEventListener('pointerdown', (evt) => evt.stopPropagation());
  window.addEventListener('resize', updateCountCards);

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
  // Luminari may load after the first layout; re-check the title then.
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(updateSuitMode);
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

  // ── Map art (the Forge's upload step) ─────────────────────────────────────
  // The art shows as soon as it's picked and uploads straight away, on its own:
  // a failed upload keeps the file and tries again (before the next save, and
  // every few seconds), so it never has to be picked twice.
  const artStep = document.getElementById('mb-art-step');
  const uploadButton = document.getElementById('mb-upload');
  const uploadInput = document.getElementById('mb-upload-input');
  const nameInput = document.getElementById('mb-name');
  let pendingArt = null;      // a picked File not stored on the server yet
  let uploading = null;       // the upload in progress (a Promise of success)
  let retryTimer = null;
  let previousArt = null;     // the stored art, to go back to if a file is refused

  function hasArt() {
    return !config.upload_step || !!config.image;
  }

  // A new Forge map isn't created until the Art step's Next (or Save) sends
  // its name and art (createMap); until then there's nothing to save to.
  function isNew() {
    return !!config.create_url && !config.save_url;
  }

  function hasName() {
    return !nameInput || nameInput.value.trim() !== '';
  }

  // The Art step is done with a name and art; a new map must also be created
  // (by Next) before the later steps open.
  function artStepDone() {
    return hasArt() && hasName();
  }

  function artStepLocked() {
    return STEP_UPLOAD >= 0 && (!artStepDone() || isNew());
  }

  function artStepMissing() {
    if (!hasName() && !hasArt()) return 'Name your map and upload its art first.';
    return hasName() ? "Upload your map's art first." : 'Name your map first.';
  }

  function updateUpload() {
    document.getElementById('mb-crop-notice').classList.toggle('d-none', !config.art_cropped || !hasArt());
    if (!artStep) return;
    artStep.classList.toggle('d-none', step !== STEP_UPLOAD || !!config.readonly);
    // The art is there but the name isn't: show what's left.
    nameInput.classList.toggle('mb-missing', hasArt() && !hasName());
    uploadButton.classList.toggle('mb-has-art', hasArt());
    const title = uploadButton.querySelector('.mb-upload-title');
    title.textContent = hasArt() ? title.dataset.replace : title.dataset.upload;
  }

  // Upload progress and problems pop up over the map (kind: 'info', 'warning'
  // or 'danger'; null hides it), so nothing below the map moves.
  const STATUS_ICONS = { info: '<span class="spinner-border spinner-border-sm"></span>',
                         warning: '<i class="bi bi-arrow-repeat"></i>', danger: '<i class="bi bi-exclamation-triangle"></i>' };
  function artStatus(kind, text) {
    const status = document.getElementById('mb-art-status');
    if (!status) return;
    if (!kind) {
      status.classList.remove('show');
      return;
    }
    status.className = `${kind} show`;
    status.querySelector('.mb-art-status-icon').innerHTML = STATUS_ICONS[kind] || '';
    status.querySelector('.mb-art-status-text').textContent = text;
  }

  function pickArt(file) {
    if (!file) return;
    if (!/^image\/(png|jpeg|webp)$/.test(file.type)) {
      artStatus('danger', 'Choose a PNG, JPG or WebP image.');
      return;
    }
    const url = URL.createObjectURL(file);
    const probe = new Image();
    probe.onload = () => {
      // Cropped by the server the same way (centre, never stretched).
      const ratio = probe.naturalWidth / probe.naturalHeight;
      config.art_cropped = Math.abs(ratio / config.official_ratio - 1) > config.ratio_tolerance;
      render();
    };
    probe.src = url;
    previousArt = previousArt || { image: config.image, cropped: config.art_cropped };
    document.getElementById('mb-image').src = url;
    config.image = url;
    pendingArt = file;
    render();
    // A new map has nowhere to upload to yet: the file goes with createMap.
    if (!isNew()) uploadArt();
  }

  let creating = null;        // createMap in progress (a Promise of success)

  // Create a new map from the Art step's name and art. The page then carries
  // on as that map's builder (its URL too), on the same layout.
  function createMap() {
    if (!isNew()) return Promise.resolve(true);
    if (creating) return creating;
    creating = (async () => {
      artStatus('info', 'Creating your map…');
      let response = null, result = {};
      try {
        const body = new FormData();
        body.append('name', nameInput.value.trim());
        if (pendingArt) body.append('art', pendingArt);
        body.append('state', JSON.stringify(state));
        response = await fetch(config.create_url, { method: 'POST', headers: { 'X-CSRFToken': csrfToken() }, body });
        result = await response.json();
      } catch (err) {
        response = null;
      }
      if (response && response.ok && result.ok) {
        pendingArt = null;
        previousArt = null;
        Object.assign(config, { save_url: result.save_url, upload_url: result.upload_url, create_url: null,
                                image: result.image, art_cropped: !!result.cropped });
        nameInput.value = result.name;
        history.replaceState(null, '', result.builder_url);
        const heading = document.getElementById('mb-page-title');
        if (heading) heading.textContent = result.name;
        const back = document.getElementById('mb-back-link');
        if (back) {
          back.href = result.detail_url;
          back.querySelector('span').textContent = 'Back to Map';
        }
        artStatus(null);
        render();
        return true;
      }
      if (response && response.status === 400) {
        artStatus('danger', result.error || "Your map couldn't be created.");
        if (result.field === 'art') {
          // The file itself was refused: pick another.
          pendingArt = null;
          previousArt = null;
          config.image = null;
          config.art_cropped = false;
          document.getElementById('mb-image').src = 'data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==';
        }
        render();
        return false;
      }
      artStatus('danger', "Couldn't create your map — check your connection and click Next again.");
      return false;
    })();
    creating.finally(() => { creating = null; });
    return creating;
  }

  function uploadArt() {
    if (!pendingArt) return Promise.resolve(true);
    if (uploading) return uploading;
    const file = pendingArt;
    clearTimeout(retryTimer);
    artStatus('info', 'Uploading your art…');
    uploading = (async () => {
      let response, result = {};
      try {
        const body = new FormData();
        body.append('art', file);
        response = await fetch(config.upload_url, { method: 'POST', headers: { 'X-CSRFToken': csrfToken() }, body });
        result = await response.json().catch(() => ({}));
      } catch (err) {
        response = null;
      }
      if (response && response.ok && result.ok) {
        if (pendingArt === file) {
          pendingArt = null;
          previousArt = null;
          config.art_cropped = !!result.cropped;
          artStatus(null);
        }
        render();
        return true;
      }
      if (response && response.status === 400 && pendingArt === file) {
        // The file itself was refused (not an image, too big...): retrying won't help.
        pendingArt = null;
        if (previousArt) {
          config.image = previousArt.image;
          config.art_cropped = previousArt.cropped;
          document.getElementById('mb-image').src = config.image || 'data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==';
          previousArt = null;
        }
        artStatus('danger', result.error || "That image couldn't be used. Try another file.");
        if (!hasArt()) goTo(STEP_UPLOAD); else render();
        return false;
      }
      artStatus('warning', 'Art not uploaded yet — retrying.');
      retryTimer = setTimeout(uploadArt, 8000);
      return false;
    })();
    uploading.finally(() => {
      uploading = null;
      // Another file was picked while this one was uploading: send that one now.
      if (pendingArt && pendingArt !== file) uploadArt();
    });
    return uploading;
  }

  if (artStep) {
    artStep.addEventListener('pointerdown', (evt) => evt.stopPropagation());
    uploadButton.addEventListener('click', () => uploadInput.click());
    document.querySelector('#mb-art-status .mb-art-status-close').addEventListener('click', () => artStatus(null));
    // The name is saved with the layout (Save Progress or Complete).
    nameInput.addEventListener('input', () => {
      dirty = true;
      showSaved(false);
      updatePanel();   // Next waits for a name
    });
    nameInput.addEventListener('keydown', (evt) => {
      if (evt.key === 'Enter') nameInput.blur();
    });
    uploadInput.addEventListener('change', () => {
      pickArt(uploadInput.files[0]);
      uploadInput.value = '';
    });
    board.addEventListener('dragover', (evt) => {
      if (step !== STEP_UPLOAD || !evt.dataTransfer.types.includes('Files')) return;
      evt.preventDefault();
      uploadButton.classList.add('mb-drop');
    });
    board.addEventListener('dragleave', (evt) => {
      if (!board.contains(evt.relatedTarget)) uploadButton.classList.remove('mb-drop');
    });
    board.addEventListener('drop', (evt) => {
      uploadButton.classList.remove('mb-drop');
      if (step !== STEP_UPLOAD) return;
      evt.preventDefault();
      pickArt(evt.dataTransfer.files[0]);
    });
    window.addEventListener('online', () => { if (pendingArt) uploadArt(); });
  }

  // ── Steps and saving ──────────────────────────────────────────────────────
  function goTo(n) {
    n = Math.min(Math.max(n, 0), STEP_REVIEW);
    if (n > STEP_UPLOAD && artStepLocked()) n = STEP_UPLOAD;   // name and art first
    step = n;
    selected = null;
    if (pendingMarker !== null && !state.clearings[pendingMarker].marker) removeClearing(pendingMarker);
    pendingMarker = null;
    render();
    introduceStep();
  }
  // The progress bar: a button per step, with a highlight that slides along.
  const progress = document.getElementById('mb-progress');
  progress.style.setProperty('--mb-steps', STEPS.length);
  STEPS.forEach((s, i) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.setAttribute('role', 'listitem');
    btn.dataset.step = i;
    btn.innerHTML = '<span class="mb-step-num"></span><span class="mb-step-name"></span>';
    btn.querySelector('.mb-step-num').textContent = i + 1;
    const name = btn.querySelector('.mb-step-name');
    if (s.short) {
      name.innerHTML = '<span class="mb-name-full"></span><span class="mb-name-short"></span>';
      name.querySelector('.mb-name-full').textContent = s.name;
      name.querySelector('.mb-name-short').textContent = s.short;
    } else {
      name.textContent = s.name;
    }
    btn.addEventListener('click', () => {
      if (i !== step) goTo(i);
    });
    progress.appendChild(btn);
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
    // Cards open by themselves only until the user's first finished map; the
    // info button beside the title always shows them.
    if (config.readonly || !config.show_intros || introduced.has(step)) return;
    introduced.add(step);
    openCard();
  }

  function cardOpen() {
    return card.classList.contains('show');
  }

  function openCard() {
    const s = STEPS[step];
    document.getElementById('mb-card-heading').textContent = s.heading;
    const hint = document.getElementById('mb-card-hint');
    hint.textContent = s.hint;
    // An optional link after the hint: {before, label, url, after}.
    if (s.link) {
      hint.append(' ' + (s.link.before || ''));
      const a = document.createElement('a');
      a.href = s.link.url;
      a.textContent = s.link.label;
      a.target = '_blank';
      a.rel = 'noopener';
      hint.append(a, s.link.after || '');
    }
    // On touch screens, mention that two fingers zoom (one finger edits).
    const touch = window.matchMedia && window.matchMedia('(pointer: coarse)').matches;
    if (touch && step !== STEP_REVIEW && step !== STEP_UPLOAD) hint.append(' Pinch with two fingers to zoom in.');
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
  document.getElementById('mb-next').addEventListener('click', async () => {
    // A new map is created from its name and art when it leaves the Art step.
    if (step === STEP_UPLOAD && isNew() && !(await createMap())) return;
    goTo(step + 1);
  });
  document.getElementById('mb-save').addEventListener('click', () => save(false));
  document.getElementById('mb-generate').addEventListener('click', () => save(true));

  function csrfToken() {
    const input = document.querySelector('input[name="csrfmiddlewaretoken"]');
    return input ? input.value : '';
  }

  async function save(generate) {
    const box = document.getElementById('mb-messages');
    box.innerHTML = '';
    // A new map is created first (Save is only enabled once it has a name and art).
    if (isNew() && !(await createMap())) return;
    // Art picked but not stored yet (the upload failed): send it again first.
    // Generating needs it on the server; a plain save goes ahead anyway.
    if (pendingArt && !(await uploadArt()) && generate) {
      box.appendChild(alertBox('danger', ["Your map's art hasn't uploaded yet, so the map can't be generated. Check your connection and try again."]));
      return;
    }
    let result;
    try {
      const response = await fetch(config.save_url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
        body: JSON.stringify(nameInput ? { state, generate, name: nameInput.value } : { state, generate }),
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
    if (nameInput && result.name) {
      nameInput.value = result.name;
      const heading = document.getElementById('mb-page-title');
      if (heading) heading.textContent = result.name;
    }
    // No message for a plain save: the Save button already reads "Saved", and the
    // counters show the Map's new figures.
    if (result.changes && result.changes.length) {
      // Only the stages the server counted now match the Map.
      const KEYS = { clearings: ['clearings'], buildings: ['buildings', 'ruins'], forests: ['forests'] };
      for (const stage of result.counted || []) {
        for (const key of KEYS[stage] || []) config.expected[key] = counts()[key];
      }
    }
    // Problems the review step already lists aren't repeated: that list is
    // highlighted instead. Errors only block generating (the layout is still
    // saved), so a plain save doesn't show them.
    const shown = new Set(step === STEP_REVIEW ? localErrors() : []);
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
    if (dirty || pendingArt || (isNew() && (hasArt() || hasName()))) {
      evt.preventDefault();
      evt.returnValue = '';
    }
  });

  render();
  // A new map starts in its name field; focused before the step card opens,
  // which gives focus back to it when it closes.
  if (nameInput && step === STEP_UPLOAD && !hasName()) {
    nameInput.focus({ preventScroll: true });
  }
  introduceStep();
})();
