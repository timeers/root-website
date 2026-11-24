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
//   paths_step   a Paths & Rivers step after Buildings & Ruins (path: the
//                path constants from tts_map.path_config)
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
  // Dots and line widths: these sizes on the whole map, scaled with the view
  // when the Buildings step zooms in, so they stay the same size on screen.
  const BASE_DOT_PX = Math.max(W, H) / 120;
  const BASE_STROKE = Math.max(W, H) / 600;
  let DOT_PX = BASE_DOT_PX;
  let STROKE = BASE_STROKE;

  const state = config.state;
  state.clearings = state.clearings || [];
  state.forests = state.forests || [];
  state.buildings = state.buildings || [];
  state.paths = state.paths || [];
  state.fixed = !!config.fixed;   // the Map's setting; saved back with the layout

  // name: the progress bar's label (short: at medium widths, when the name is
  // long). config.step_text can replace any step's text, keyed by step key.
  const STEPS = [
    { key: 'track', heading: 'VP and Item Tracks', name: 'VP & Item Tracks', short: 'Tracks',
      hint: 'Drag the item grid onto the printed item spaces. Drag the VP track up or down so the blue dots align with the printed 0-30 track.' },
    { key: 'clearings', heading: 'Clearings', name: 'Clearings',
      hint: 'Click the middle of a clearing, then drag (or click) to where its suit marker goes. Drag the number tile around the circle. Select a clearing to re-number or delete it.' },
    { key: 'buildings', heading: 'Buildings & Ruins', name: 'Buildings & Ruins', short: 'Buildings',
      hint: 'Each clearing is shown up close, starting with clearing 1: click each building slot, then use the arrows at the top for the next clearing. Drag inside a square to move it and press x to remove it. Select a building and click the ruin tile to make it a ruin. Click the small map to see the whole map; there, click a clearing (or the magnifier) to zoom back in, or click outside the clearings to place a building.' },
    { key: 'forests', heading: 'Forests', name: 'Forests',
      hint: 'Click the middle of each forest. Drag a dot to move it and press x to remove it. These are used for Relic placements.' },
    { key: 'review', heading: 'Review', name: 'Review',
      hint: 'Check the count of each element, then click "Complete" to generate the final JSON.' },
  ];
  if (config.paths_step) {
    STEPS.splice(STEPS.findIndex(s => s.key === 'forests'), 0,
      { key: 'paths', heading: 'Paths & Rivers', name: 'Paths & Rivers', short: 'Paths',
        hint: 'Pick a path type, then drag from one clearing to another, or to the '
              + 'middle of another path. Select a path to change its type, drag one of '
              + 'its ends to another clearing, or press x to remove it. Rivers ▾ also draws lakes: '
              + 'a lake fills the space around it, halfway to the land, and one drawn outside the '
              + "map's edge (nothing between it and the board's edge) becomes a sea. Drawing a river or lake beside "
              + 'a path (or a path beside one) between the same two clearings? Drop its end to the left '
              + "or right of the clearing's centre to choose which side it goes." });
  }
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
  const STEP_PATHS = stepOf('paths');   // -1 on the Keep
  const PRINT = config.print || null;   // the Forge's printed art (see print_art in tts_map.py)
  const PATH = config.path || null;     // the Forge's path constants (path_config in tts_map.py)
  const PATH_KINDS = ['path', 'special', 'river'];
  const BLANK_GIF = 'data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==';
  let drawKind = 'path';      // the kind of path the Paths step draws

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

  // ── Paths & Rivers geometry ───────────────────────────────────────────────
  // Mirrors path_shapes in tts_map.py, so the printed map matches the builder.
  // A path end is {c: clearing index} or {p: path index} (that path's middle).

  // [x, y, radius] of each clearing (radius: centre to marker).
  function circles() {
    return state.clearings.map(c => {
      const centre = px(c.centre);
      const r = c.marker ? dist(centre, px(c.marker)) : 0;
      return [centre[0], centre[1], r || PATH.default_radius];
    });
  }

  function quad(a, c, b, t) {
    const u = 1 - t;
    return [u * u * a[0] + 2 * u * t * c[0] + t * t * b[0], u * u * a[1] + 2 * u * t * c[1] + t * t * b[1]];
  }

  function segmentDistance(p, a, b) {
    const dx = b[0] - a[0], dy = b[1] - a[1];
    const length2 = dx * dx + dy * dy;
    const t = length2 ? Math.min(Math.max(((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length2, 0), 1) : 0;
    return Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy);
  }

  // The control point of a curve from a to b clear of every obstacle
  // ([x, y, clearance]): straight when that's clear, else bent sideways as
  // little as works (away from the worst obstacle first); straight if nothing does.
  function route(a, b, obstacles) {
    const mid = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
    const blocking = obstacles.filter(o => segmentDistance(o, a, b) < o[2]);
    if (!blocking.length) return mid;
    const length = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1e-9;
    const nx = -(b[1] - a[1]) / length, ny = (b[0] - a[0]) / length;
    let worst = blocking[0];
    for (const o of blocking) if (segmentDistance(o, a, b) - o[2] < segmentDistance(worst, a, b) - worst[2]) worst = o;
    const side = (worst[0] - mid[0]) * nx + (worst[1] - mid[1]) * ny > 0 ? -1 : 1;
    for (let step = 1; step <= PATH.bend_steps; step++) {
      const bulge = PATH.bend_max * length * step / PATH.bend_steps;
      for (const s of [side, -side]) {
        // A quadratic's middle sits halfway to its control point.
        const control = [mid[0] + s * nx * 2 * bulge, mid[1] + s * ny * 2 * bulge];
        let clear = true;
        for (let i = 0; i <= PATH.samples && clear; i++) {
          const p = quad(a, control, b, i / PATH.samples);
          clear = obstacles.every(o => Math.hypot(p[0] - o[0], p[1] - o[1]) >= o[2]);
        }
        if (clear) return control;
      }
    }
    return mid;
  }

  // t where the curve crosses the circle around its start (or end).
  function trim(a, control, b, centre, radius, start) {
    let lo = start ? 0 : 0.5, hi = start ? 0.5 : 1;
    const inside = (t) => { const p = quad(a, control, b, t); return Math.hypot(p[0] - centre[0], p[1] - centre[1]) < radius; };
    if ((start && !inside(lo)) || (!start && !inside(hi))) return start ? lo : hi;
    if ((start && inside(hi)) || (!start && inside(lo))) return start ? hi : lo;   // the circles overlap
    for (let i = 0; i < 30; i++) {
      const m = (lo + hi) / 2;
      if (inside(m) === start) lo = m; else hi = m;
    }
    return (lo + hi) / 2;
  }

  // The point halfway along a polyline, by length.
  function middleOf(points) {
    const lengths = [];
    for (let i = 1; i < points.length; i++) lengths.push(dist(points[i - 1], points[i]));
    let half = lengths.reduce((s, l) => s + l, 0) / 2;
    for (let i = 0; i < lengths.length; i++) {
      if (half <= lengths[i] && lengths[i]) {
        const t = half / lengths[i], a = points[i], b = points[i + 1];
        return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
      }
      half -= lengths[i];
    }
    return points[Math.floor(points.length / 2)];
  }

  // Each path's {kind, points (its centre line, trimmed to the clearings'
  // circles), middle}, in state order.
  // A river and a path between the same two ends: bow them apart, to `side`
  // (+1/-1, measured from the ends in key order, so both agree), by
  // PATH.shared_bend half-widths at the middle (_bow_apart).
  function bowApart(control, a, b, p, side) {
    const [from, to] = endKey(p.a) <= endKey(p.b) ? [a, b] : [b, a];
    const length = Math.hypot(to[0] - from[0], to[1] - from[1]) || 1e-9;
    const nx = -(to[1] - from[1]) / length, ny = (to[0] - from[0]) / length;
    const bulge = PATH.shared_bend * PATH.width * (PATH.river_width + 1) / 2;
    return [control[0] + side * nx * 2 * bulge, control[1] + side * ny * 2 * bulge];
  }

  // Which way a path sharing both ends with its twin of the other family bows
  // (_pair_side in tts_map.py): its own `bow` (+1/-1 towards the normal of its
  // a->b line, chosen where it was dropped), else opposite its twin's, else the
  // water one +1 and the land one -1. In key order, as bowApart measures.
  const keySign = (p) => (endKey(p.a) <= endKey(p.b) ? 1 : -1);
  function pairSide(p, twin) {
    if (p.bow === 1 || p.bow === -1) return p.bow * keySign(p);
    if (twin && (twin.bow === 1 || twin.bow === -1)) return -twin.bow * keySign(twin);
    return familyOf(p.kind) === 'river' ? 1 : -1;
  }

  // Each path's twin: the other family's path between the same two ends, or -1.
  function twinOf(index, paths = state.paths) {
    const p = paths[index];
    return paths.findIndex((q, k) => k !== index && pairKey(q.a, q.b) === pairKey(p.a, p.b)
                                     && familyOf(q.kind) !== familyOf(p.kind));
  }

  function pathShapes() {
    const cs = circles();
    const shapes = [];
    const shape = (i, depth) => {
      if (shapes[i]) return shapes[i];
      if (depth > state.paths.length) throw new Error('path attachments loop');
      const p = state.paths[i];
      const own = new Set();
      const ends = [p.a, p.b].map(e => {
        if ('c' in e) { own.add(e.c); return cs[e.c].slice(0, 2); }
        return shape(e.p, depth + 1).middle;
      });
      const [a, b] = ends;
      // Kept clear of other clearings by half its own width (a river's is wider).
      const clearance = PATH.width * (familyOf(p.kind) === 'river' ? PATH.river_width : 1) / 2;
      const obstacles = cs.filter((c, k) => !own.has(k)).map(([x, y, r]) => [x, y, r + clearance]);
      let control = route(a, b, obstacles);
      const twin = twinOf(i);
      if (twin >= 0) control = bowApart(control, a, b, p, pairSide(p, state.paths[twin]));
      const t0 = 'c' in p.a ? trim(a, control, b, a, cs[p.a.c][2], true) : 0;
      const t1 = Math.max('c' in p.b ? trim(a, control, b, b, cs[p.b.c][2], false) : 1, t0);
      const points = [];
      for (let k = 0; k <= PATH.samples; k++) points.push(quad(a, control, b, t0 + (t1 - t0) * k / PATH.samples));
      shapes[i] = { kind: p.kind, points, middle: middleOf(points) };
      return shapes[i];
    };
    state.paths.forEach((_p, i) => shape(i, 0));
    return shapes;
  }

  // The PATH.lanes polylines a path is drawn with: its centre line offset
  // across its width (path_lanes in tts_map.py).
  function laneLines(points) {
    const lane = PATH.width / PATH.lanes;
    const normals = points.map((_p, k) => {
      const a = points[Math.max(k - 1, 0)], b = points[Math.min(k + 1, points.length - 1)];
      const length = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1e-9;
      return [-(b[1] - a[1]) / length, (b[0] - a[0]) / length];
    });
    const lines = [];
    for (let i = 0; i < PATH.lanes; i++) {
      const off = (i - (PATH.lanes - 1) / 2) * lane;
      lines.push(points.map((p, k) => [p[0] + normals[k][0] * off, p[1] + normals[k][1] * off]));
    }
    return lines;
  }

  // ── Path ends and rules ──
  const endKey = (e) => ('c' in e ? `c${e.c}` : `p${e.p}`);
  const pairKey = (a, b) => [endKey(a), endKey(b)].sort().join('|');

  // Does path j lead (through the paths its ends attach to) to path i?
  function dependsOn(j, i) {
    const stack = [j], seen = new Set();
    while (stack.length) {
      const k = stack.pop();
      if (k === i) return true;
      if (seen.has(k) || !state.paths[k]) continue;
      seen.add(k);
      for (const e of [state.paths[k].a, state.paths[k].b]) if ('p' in e) stack.push(e.p);
    }
    return false;
  }

  // Rivers, and paths with special paths: one of each may join the same two ends.
  // Water (rivers and lakes) and land (paths and special paths): one of each
  // may join the same two ends (path_family in tts_map.py).
  const familyOf = (kind) => (kind === 'river' || kind === 'lake' ? 'river' : 'land');

  // Can a path of `kind` (path `self` when moving one of its ends) join ends a
  // and b? Not an end to itself, not a second of its family between the same
  // two ends, and never attached (through others) to itself (clean_paths in tts_map.py).
  function canConnect(a, b, self = null, kind = null) {
    if (!a || !b || endKey(a) === endKey(b)) return false;
    kind = kind || (self !== null ? state.paths[self].kind : drawKind);
    // A lake joins two clearings, and nothing joins a lake's middle (clean_paths).
    if (kind === 'lake' && ('p' in a || 'p' in b)) return false;
    if ([a, b].some(e => 'p' in e && state.paths[e.p] && state.paths[e.p].kind === 'lake')) return false;
    const key = pairKey(a, b);
    const family = familyOf(kind);
    if (state.paths.some((p, k) => k !== self && pairKey(p.a, p.b) === key && familyOf(p.kind) === family)) return false;
    if (self !== null) {
      for (const e of [a, b]) if ('p' in e && (e.p === self || dependsOn(e.p, self))) return false;
    }
    return true;
  }

  // Remove paths (a Set of indexes), and the paths attached to their middles,
  // renumbering the rest and the references to them.
  function removePaths(drop) {
    let grew = true;
    while (grew) {
      grew = false;
      state.paths.forEach((p, k) => {
        if (!drop.has(k) && [p.a, p.b].some(e => 'p' in e && drop.has(e.p))) { drop.add(k); grew = true; }
      });
    }
    const renumber = new Map(), kept = [];
    state.paths.forEach((p, k) => { if (!drop.has(k)) { renumber.set(k, kept.length); kept.push(p); } });
    for (const p of kept) for (const e of [p.a, p.b]) if ('p' in e) e.p = renumber.get(e.p);
    state.paths = kept;
    if (selected && selected.kind === 'path') {
      selected = renumber.has(selected.index) ? { kind: 'path', index: renumber.get(selected.index) } : null;
    }
  }

  // A path retyped into the other family (a river turned into a path, or back)
  // replaces the one of that family already between the same two ends: that
  // one goes, and paths attached to its middle move to this one.
  function replaceTwin(index) {
    const path = state.paths[index];
    const twin = state.paths.findIndex((p, k) => k !== index && pairKey(p.a, p.b) === pairKey(path.a, path.b)
                                                && familyOf(p.kind) === familyOf(path.kind));
    if (twin < 0) return;
    for (const p of state.paths) for (const e of [p.a, p.b]) if ('p' in e && e.p === twin) e.p = index;
    removePaths(new Set([twin]));
    // A moved attachment may now duplicate another: the later one goes (as clean_paths keeps the first).
    const seen = new Set(), dupes = new Set();
    state.paths.forEach((p, k) => {
      const key = `${pairKey(p.a, p.b)}|${familyOf(p.kind)}`;
      if (seen.has(key)) dupes.add(k); else seen.add(key);
    });
    if (dupes.size) removePaths(dupes);
  }

  const handleRadius = () => Math.max(PATH.width * 0.35, DOT_PX);

  // The end under a point: a path's middle handle first, then a clearing's circle.
  function anchorAt(p) {
    let best = null, bestD = handleRadius() * 1.6;
    // A lake only ever ends on a clearing; nothing ends on a lake's middle.
    const kind = drag && drag.kind === 'path-end' ? state.paths[drag.index].kind : drawKind;
    pathCache.forEach((s, j) => {
      if (kind === 'lake' || s.kind === 'lake') return;
      const d = dist(p, s.middle);
      if (d < bestD) { best = { p: j }; bestD = d; }
    });
    if (best) return best;
    const cs = circles();
    for (let i = 0; i < cs.length; i++) if (dist(p, cs[i]) <= cs[i][2]) return { c: i };
    return null;
  }

  function anchorPoint(e) {
    if ('c' in e) return px(state.clearings[e.c].centre);
    return pathCache[e.p] ? pathCache[e.p].middle : null;
  }

  let pathCache = [];   // the shapes drawn last (render), for hit-testing

  // A path just dropped (drawn, or an end moved) where its twin of the other
  // family already joins the same ends: it bows to the side of the line
  // between its ends that it was dropped on (`bow`, see pairSide), and its twin
  // to the other. Dropped (near enough) on the line itself: the sides stay as
  // they were.
  function chooseSide(index, end, dropAt) {
    const twin = twinOf(index);
    if (twin < 0) return;
    const p = state.paths[index];
    const a = anchorPoint(p.a), b = anchorPoint(p.b), at = end === 'a' ? a : b;
    if (!a || !b) return;
    const length = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1e-9;
    const across = ((dropAt[0] - at[0]) * -(b[1] - a[1]) + (dropAt[1] - at[1]) * (b[0] - a[0])) / length;
    if (Math.abs(across) < DOT_PX / 2) return;
    p.bow = across > 0 ? 1 : -1;
    delete state.paths[twin].bow;     // the latest choice wins
  }

  // ── Buildings step: one clearing at a time ────────────────────────────────
  // Buildings sit in clearings, so the Buildings & Ruins step shows one
  // clearing at a time, zoomed in. The board keeps its size and shape: only
  // the part of the map drawn in it changes. The view is a box in board pixels
  // with the board's own shape: the SVG's viewBox, and the same transform on
  // the art under it. A mini-map (drag it to a corner, click it for the whole
  // map) shows where the view is; on the whole map, a magnifier zooms back in.
  const FULL = { x: 0, y: 0, w: W, h: H };
  const ZOOM_PAD = 0.15;        // the view's margin round a clearing, each side
  const ZOOM_EDGE = 10;         // the zoom controls' distance from the board's edge (screen px)
  const ZOOM_MS = 250;
  const artImage = document.getElementById('mb-image');
  const minimap = document.getElementById('mb-minimap');
  const minimapArt = minimap.querySelector('img');
  const minimapView = document.getElementById('mb-minimap-view');
  const clearingNav = document.getElementById('mb-clearing-nav');
  const navLabel = document.getElementById('mb-nav-label');
  const navPrev = document.getElementById('mb-nav-prev');
  const navNext = document.getElementById('mb-nav-next');
  const zoomInBtn = document.getElementById('mb-zoom-in');
  const NEXT_CLEARING = navNext.getAttribute('aria-label');
  // on: zoomed in (else the whole map); clearing: which (state.clearings
  // index); view: its box, worked out when it's chosen (not on every redraw,
  // so dragging a building out never moves the view under the pointer).
  const zoom = { on: true, clearing: null, view: null };
  let shownView = FULL;         // the view on screen (part way, mid-animation)
  let targetView = FULL;        // where it's going
  let viewFrame = null;
  let countCardFrom = null;     // the count card's place before the view changed (it slides to its new one)
  let miniCorner = 'tr';        // the mini-map's corner: t/b + l/r
  try {
    const saved = window.localStorage.getItem('mb-minimap-corner');
    if (['tl', 'tr', 'bl', 'br'].includes(saved)) miniCorner = saved;
  } catch (err) { /* no storage: top right */ }

  // The clearings in number order (ties and unnumbered ones in list order).
  function clearingOrder() {
    return state.clearings.map((c, i) => [c, i]).filter(([c]) => c.marker)
      .sort((a, b) => ((a[0].number || Infinity) - (b[0].number || Infinity)) || a[1] - b[1])
      .map(([, i]) => i);
  }

  function nearestClearing(p) {
    let best = null, bestD = Infinity;
    state.clearings.forEach((c, i) => {
      if (!c.marker) return;
      const d = dist(p, px(c.centre));
      if (d < bestD) { best = i; bestD = d; }
    });
    return best;
  }

  // The clearing whose circle p is inside (the nearest centre, where circles
  // overlap), or null.
  function clearingAt(p) {
    let inside = null, nearest = Infinity;
    state.clearings.forEach((c, i) => {
      if (!c.marker) return;
      const centre = px(c.centre), d = dist(p, centre);
      if (d <= dist(centre, px(c.marker)) && d < nearest) { inside = i; nearest = d; }
    });
    return inside;
  }

  function zoomActive() {
    const c = zoom.clearing === null ? null : state.clearings[zoom.clearing];
    return step === STEP_BUILDINGS && !config.readonly && zoom.on && !!zoom.view && !!(c && c.marker);
  }

  // The view round clearing i: its circle, suit marker, number tile and the
  // buildings nearest it, with a margin; never smaller than an average
  // clearing's; centred on it, with the board's shape, and kept on the board.
  function clearingView(i) {
    const c = state.clearings[i], centre = px(c.centre), m = px(c.marker), R = dist(centre, m);
    let reachX = R, reachY = R;
    const take = (x, y, r) => {
      reachX = Math.max(reachX, Math.abs(x - centre[0]) + r);
      reachY = Math.max(reachY, Math.abs(y - centre[1]) + r);
    };
    take(m[0], m[1], Math.max(...config.marker_px));
    const t = numberTile(c);
    take(t[0], t[1], config.number_radius_px);
    for (const b of state.buildings) {
      const p = px(b.at);
      if (nearestClearing(p) === i) take(p[0], p[1], BUILDING_PX * 0.75);
    }
    const hh = Math.max(reachY * (1 + ZOOM_PAD), reachX * (1 + ZOOM_PAD) * H / W,
                        (config.default_radius_px || R) * 1.35);
    const hw = hh * W / H;
    if (2 * hw >= W || 2 * hh >= H) return FULL;
    return { x: Math.min(Math.max(centre[0] - hw, 0), W - 2 * hw),
             y: Math.min(Math.max(centre[1] - hh, 0), H - 2 * hh), w: 2 * hw, h: 2 * hh };
  }

  function zoomToClearing(i) {
    zoom.on = i !== null;
    zoom.clearing = i;
    zoom.view = i === null ? null : clearingView(i);
  }

  // Entering the step: clearing 1.
  function zoomStart() {
    const order = clearingOrder();
    zoomToClearing(order.length ? order[0] : null);
  }

  // From the review: the building's clearing (keeping it selected).
  function zoomOnBuilding(index) {
    if (step !== STEP_BUILDINGS || !state.buildings[index]) return;
    const k = nearestClearing(px(state.buildings[index].at));
    if (k !== null) zoomToClearing(k);
  }

  // Another clearing, or in and out: a building selected in the old view may
  // be out of this one, so the selection goes (its pop-up would float off).
  function changeView(fn) {
    fn();
    selected = null;
    render();
  }

  // On every redraw: head for the view this step wants. A new view sets the
  // line widths at once (they're drawn after this) and animates the box.
  function syncView() {
    const target = zoomActive() ? zoom.view : FULL;
    if (['x', 'y', 'w', 'h'].every(k => Math.abs(target[k] - targetView[k]) < 1e-6)) return;
    targetView = target;
    noteCountCard();
    DOT_PX = BASE_DOT_PX * target.w / W;
    STROKE = BASE_STROKE * target.w / W;
    if (viewFrame) cancelAnimationFrame(viewFrame);
    let still = false;
    try { still = window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (err) { /* animate */ }
    const from = shownView, start = performance.now(), ms = still ? 0 : ZOOM_MS;
    const frame = (now) => {
      const t = ms ? Math.min(1, Math.max(0, (now - start) / ms)) : 1;
      const e = t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;    // ease in and out
      applyView({ x: from.x + (target.x - from.x) * e, y: from.y + (target.y - from.y) * e,
                  w: from.w + (target.w - from.w) * e, h: from.h + (target.h - from.h) * e });
      viewFrame = t < 1 ? requestAnimationFrame(frame) : null;
      if (t >= 1 && selected) updatePopover();     // (it was placed for the old view)
    };
    if (ms) viewFrame = requestAnimationFrame(frame); else frame(start);
  }

  function applyView(v) {
    shownView = v;
    svg.setAttribute('viewBox', `${v.x} ${v.y} ${v.w} ${v.h}`);
    // The art: scaled from its top-left corner, after moving the view's
    // corner there (translate percentages are of the image's own box).
    const s = W / v.w;
    artImage.style.transform = v === FULL || (Math.abs(s - 1) < 1e-9 && !v.x && !v.y)
      ? '' : `scale(${s}) translate(${-v.x / W * 100}%, ${-v.y / H * 100}%)`;
    minimapView.setAttribute('x', v.x);
    minimapView.setAttribute('y', v.y);
    minimapView.setAttribute('width', v.w);
    minimapView.setAttribute('height', v.h);
  }

  // The count card moves with the view (onto the item track, or into a top
  // corner): it slides there, with the zoom. noteCountCard() before it's
  // placed again, slideCountCard() after.
  function noteCountCard() {
    countCardFrom = countCard.classList.contains('d-none') ? null : countCard.getBoundingClientRect();
  }

  function slideCountCard() {
    const from = countCardFrom;
    countCardFrom = null;
    if (!from || countCard.classList.contains('d-none')) return;
    let still = false;
    try { still = window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (err) { /* slide */ }
    countCard.style.transition = 'none';
    countCard.style.transform = '';
    const to = countCard.getBoundingClientRect();
    const dx = from.left - to.left, dy = from.top - to.top;
    if (still || Math.hypot(dx, dy) < 1) {
      countCard.style.transition = '';
      return;
    }
    countCard.style.transform = `translate(${dx}px, ${dy}px)`;
    countCard.getBoundingClientRect();      // (start from there)
    countCard.style.transition = `transform ${ZOOM_MS}ms cubic-bezier(0.65, 0, 0.35, 1), width .25s ease`;
    countCard.style.transform = '';
    const done = (evt) => {
      if (evt && evt.propertyName !== 'transform') return;
      countCard.style.transition = '';
      countCard.removeEventListener('transitionend', done);
    };
    countCard.addEventListener('transitionend', done);
  }

  // The mini-map's size and its corner's position (board pixels).
  function miniBox(area, corner = miniCorner) {
    const w = Math.min(Math.max(area.width * 0.22, 90), 200, area.width - 2 * ZOOM_EDGE), h = w * H / W;
    return { w, h, left: corner[1] === 'l' ? ZOOM_EDGE : area.width - w - ZOOM_EDGE,
             top: corner[0] === 't' ? ZOOM_EDGE : area.height - h - ZOOM_EDGE };
  }

  // The zoomed count card: in the top corner the mini-map isn't in.
  function placeInTopCorner(card, width) {
    const area = board.getBoundingClientRect();
    card.classList.remove('mb-grow-up');
    card.style.bottom = 'auto';
    card.style.top = `${ZOOM_EDGE}px`;
    card.style.left = `${miniCorner === 'tl' ? area.width - width - ZOOM_EDGE : ZOOM_EDGE}px`;
  }

  // Shows and places the mini-map, the clearing pill and the magnifier.
  function updateZoomControls() {
    const order = step === STEP_BUILDINGS && !config.readonly ? clearingOrder() : [];
    const zoomed = order.length > 0 && zoomActive();
    minimap.classList.toggle('d-none', !zoomed);
    clearingNav.classList.toggle('d-none', !zoomed);
    zoomInBtn.classList.toggle('d-none', !order.length || zoomed);
    const area = board.getBoundingClientRect();
    if (zoomed) {
      if (minimapArt.getAttribute('src') !== artImage.getAttribute('src')) minimapArt.src = artImage.getAttribute('src');
      if (!miniDrag) {
        const box = miniBox(area);
        Object.assign(minimap.style, { width: `${box.w}px`, height: `${box.h}px`, left: `${box.left}px`, top: `${box.top}px` });
      }
      placeClearingNav(order, area);
    } else if (order.length) {
      placeZoomIn(area);
    }
  }

  // ‹ Clearing 3 of 12 ›, at the top: centred, or in the gap between what's
  // in the top corners (the zoomed count card, the mini-map when it's up
  // there); just "3/12" where that's too narrow, and below them both if even
  // that doesn't fit.
  function placeClearingNav(order, area) {
    const k = order.indexOf(zoom.clearing);
    const n = state.clearings[zoom.clearing].number || k + 1;
    const last = k === order.length - 1;
    navPrev.disabled = k <= 0;
    navNext.querySelector('i').className = last ? 'bi bi-arrows-fullscreen' : 'bi bi-chevron-right';
    const nextLabel = last ? navNext.dataset.full : NEXT_CLEARING;
    navNext.setAttribute('aria-label', nextLabel);
    navNext.title = nextLabel;
    // The top corners' contents (board pixels; layout boxes, not mid-slide ones).
    const corners = [];
    if (!countCard.classList.contains('d-none')) {
      corners.push({ left: countCard.offsetLeft, right: countCard.offsetLeft + countCard.offsetWidth,
                     bottom: countCard.offsetTop + countCard.offsetHeight });
    }
    if (miniCorner[0] === 't') {
      const mini = miniBox(area);
      corners.push({ left: mini.left, right: mini.left + mini.w, bottom: mini.top + mini.h });
    }
    let lo = ZOOM_EDGE, hi = area.width - ZOOM_EDGE;
    for (const c of corners) {
      if (c.left + c.right < area.width) lo = Math.max(lo, c.right + ZOOM_EDGE);   // in the left half
      else hi = Math.min(hi, c.left - ZOOM_EDGE);
    }
    clearingNav.classList.remove('brief');
    navLabel.textContent = `${navLabel.dataset.clearing} ${n} ${navLabel.dataset.of} ${order.length}`;
    if (clearingNav.offsetWidth > hi - lo) {
      clearingNav.classList.add('brief');
      navLabel.textContent = `${n}/${order.length}`;
    }
    const w = clearingNav.offsetWidth;
    let top = ZOOM_EDGE;
    if (w > hi - lo) {
      // No room between them: under them, across the whole width.
      top = Math.max(ZOOM_EDGE, ...corners.map(c => c.bottom + ZOOM_EDGE));
      lo = ZOOM_EDGE;
      hi = area.width - ZOOM_EDGE;
    }
    clearingNav.style.top = `${top}px`;
    clearingNav.style.left = `${Math.max(Math.min((area.width - w) / 2, hi - w), lo)}px`;
  }

  // The magnifier: top right, below the item track (and its count card) if
  // they'd be in the way.
  function placeZoomIn(area) {
    const size = zoomInBtn.offsetWidth || 40;
    const left = area.width - size - ZOOM_EDGE;
    let top = ZOOM_EDGE;
    const track = itemTrackBox();
    const blocks = [[track.left, track.top, track.left + track.width, track.top + track.height]];
    if (!countCard.classList.contains('d-none')) {
      const r = countCard.getBoundingClientRect();
      blocks.push([r.left - area.left, r.top - area.top, r.right - area.left, r.bottom - area.top]);
    }
    for (let pass = 0; pass < 2; pass++) {
      for (const [x0, y0, x1, y1] of blocks) {
        if (left < x1 + 4 && left + size > x0 - 4 && top < y1 + 4 && top + size > y0 - 4) top = y1 + ZOOM_EDGE;
      }
    }
    zoomInBtn.style.top = `${Math.min(top, Math.max(area.height - size - ZOOM_EDGE, ZOOM_EDGE))}px`;
  }
  window.addEventListener('resize', () => updateZoomControls());

  navPrev.addEventListener('click', () => {
    const order = clearingOrder(), k = order.indexOf(zoom.clearing);
    if (k > 0) changeView(() => zoomToClearing(order[k - 1]));
  });
  navNext.addEventListener('click', () => {
    const order = clearingOrder(), k = order.indexOf(zoom.clearing);
    if (k >= 0 && k < order.length - 1) changeView(() => zoomToClearing(order[k + 1]));
    else changeView(() => { zoom.on = false; });       // after the last: the whole map
  });
  zoomInBtn.addEventListener('click', () => {
    changeView(() => {
      const order = clearingOrder();
      zoomToClearing(order.includes(zoom.clearing) ? zoom.clearing : (order.length ? order[0] : null));
    });
  });

  // The mini-map: a click shows the whole map; a drag moves it, snapping to
  // the nearest corner on release.
  let miniDrag = null;
  minimap.addEventListener('pointerdown', (evt) => {
    evt.stopPropagation();
    evt.preventDefault();
    minimap.setPointerCapture(evt.pointerId);
    const area = board.getBoundingClientRect(), r = minimap.getBoundingClientRect();
    miniDrag = { id: evt.pointerId, x: evt.clientX, y: evt.clientY,
                 left: r.left - area.left, top: r.top - area.top, moved: false };
  });
  minimap.addEventListener('pointermove', (evt) => {
    if (!miniDrag || evt.pointerId !== miniDrag.id) return;
    const dx = evt.clientX - miniDrag.x, dy = evt.clientY - miniDrag.y;
    if (!miniDrag.moved && Math.hypot(dx, dy) < 5) return;
    miniDrag.moved = true;
    minimap.classList.add('mb-dragging-mini');
    const area = board.getBoundingClientRect();
    const left = Math.min(Math.max(miniDrag.left + dx, 0), area.width - minimap.offsetWidth);
    const top = Math.min(Math.max(miniDrag.top + dy, 0), area.height - minimap.offsetHeight);
    minimap.style.left = `${left}px`;
    minimap.style.top = `${top}px`;
  });
  function endMiniDrag(evt, cancelled) {
    if (!miniDrag || evt.pointerId !== miniDrag.id) return;
    const moved = miniDrag.moved;
    miniDrag = null;
    minimap.classList.remove('mb-dragging-mini');
    if (moved || cancelled) {
      // The corner nearest its middle.
      const area = board.getBoundingClientRect(), r = minimap.getBoundingClientRect();
      const midX = r.left + r.width / 2 - area.left, midY = r.top + r.height / 2 - area.top;
      if (moved) miniCorner = (midY < area.height / 2 ? 't' : 'b') + (midX < area.width / 2 ? 'l' : 'r');
      try { window.localStorage.setItem('mb-minimap-corner', miniCorner); } catch (err) { /* not kept */ }
      noteCountCard();
      updateCountCards();       // the zoomed count card takes the other top corner
      slideCountCard();
      updateZoomControls();
      return;
    }
    changeView(() => { zoom.on = false; });
  }
  minimap.addEventListener('pointerup', (evt) => endMiniDrag(evt, false));
  minimap.addEventListener('pointercancel', (evt) => endMiniDrag(evt, true));
  minimap.addEventListener('keydown', (evt) => {
    if (evt.key === 'Enter' || evt.key === ' ') {
      evt.preventDefault();
      changeView(() => { zoom.on = false; });
    }
  });
  for (const node of [clearingNav, zoomInBtn]) node.addEventListener('pointerdown', (evt) => evt.stopPropagation());

  // ── Drawing ───────────────────────────────────────────────────────────────
  function el(name, attrs, parent) {
    const node = document.createElementNS(NS, name);
    for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, v);
    (parent || content).appendChild(node);
    return node;
  }

  const content = el('g', { id: 'mb-content' }, svg);   // (the Buildings step's mini-map shows it too)
  // The suit marker's outline, shared with the summary card's clearing icon
  // (map_summary.js keeps it as <symbol id="mb-marker"> on the page).
  MapSummary.loadMarker(config.marker_svg).then(ok => { if (ok) render(); });

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

  // A path: PATH.lanes rows of pill-shaped dashes, every other row shifted
  // half a dash, which makes cobblestones. A river: one solid line. (As the
  // PDF prints them.)
  function drawLanes(g, points, kind) {
    const colour = PATH.colours[kind];
    if (kind === 'lake') {
      // Its water is the outline (drawLakes); the line it's drawn along shows faintly over it.
      el('polyline', { points: points.map(p => p.join(',')).join(' '), fill: 'none', stroke: '#1f2d6b',
                       'stroke-opacity': 0.45, 'stroke-width': PATH.width * 0.35, 'stroke-linecap': 'round',
                       'stroke-dasharray': `${PATH.width * 0.9} ${PATH.width * 0.6}`, 'pointer-events': 'none' }, g);
      return;
    }
    if (kind === 'river') {
      el('polyline', { points: points.map(p => p.join(',')).join(' '), fill: 'none', stroke: colour,
                       'stroke-width': PATH.width * PATH.river_width, 'stroke-linecap': 'butt',   // ends at the clearing's edge
                       'stroke-linejoin': 'round', 'pointer-events': 'none' }, g);
      return;
    }
    const lane = PATH.width / PATH.lanes;
    const dash = PATH.dash * lane, gap = PATH.gap * lane;
    laneLines(points).forEach((line, i) => {
      el('polyline', { points: line.map(p => p.join(',')).join(' '), fill: 'none', stroke: colour,
                       'stroke-width': PATH.stroke * lane, 'stroke-linecap': 'round',
                       'stroke-dasharray': `${dash} ${gap}`, 'stroke-dashoffset': i % 2 ? (dash + gap) / 2 : 0,
                       'pointer-events': 'none' }, g);
    });
  }

  // ── Lakes (map_lakes.js works the water out) ──
  // Recomputed only when what they depend on changes; at half the grid while
  // something's being dragged (smooth), at full size otherwise and for saving.
  let lakeCache = { key: null, grid: 0, rings: [] };
  function lakeRings(full = false) {
    if (!PATH || !state.paths.some(p => p.kind === 'lake') || !window.MapLakes) return [];
    const grid = full || !drag ? PATH.lake.grid : Math.round(PATH.lake.grid / 2);
    const key = JSON.stringify([state.clearings, state.paths, state.forests]);
    if (lakeCache.key === key && lakeCache.grid >= grid) return lakeCache.rings;
    const shapes = pathCache.length === state.paths.length ? pathCache : pathShapes();
    const cs = circles();
    const onLake = new Set(state.paths.filter(p => p.kind === 'lake').flatMap(p => [p.a.c, p.b.c]));
    // Rivers ending at each clearing: their end on its rim and the way they
    // flow in (a lake there curves out to meet each, widening).
    const feeds = new Map();
    state.paths.forEach((p, i) => {
      if (p.kind !== 'river' || !shapes[i]) return;
      const pts = shapes[i].points;
      for (const [end, at, before] of [['a', pts[0], pts[1]], ['b', pts[pts.length - 1], pts[pts.length - 2]]]) {
        if (!('c' in p[end]) || !at || !before) continue;
        const len = Math.hypot(at[0] - before[0], at[1] - before[1]) || 1;
        if (!feeds.has(p[end].c)) feeds.set(p[end].c, []);
        feeds.get(p[end].c).push([at[0], at[1], (at[0] - before[0]) / len, (at[1] - before[1]) / len]);
      }
    });
    const centre = (e) => cs[e.c].slice(0, 2);
    const input = {
      width: W, height: H, grid,
      overlap: PATH.lake.clearing_overlap, smooth: PATH.lake.smooth, minHalf: PATH.lake.min_half,
      round: PATH.lake.round, crossReach: PATH.lake.cross_reach,
      touch: PATH.lake.touch, lens: PATH.lake.lens, funnel: PATH.lake.funnel, variation: PATH.lake.variation,
      riverHalf: PATH.width * PATH.river_width / 2, linkReach: PATH.link_reach,
      // The setup card's fit: a lake reaches a clearing as far as it is on the card.
      card: PATH.card || null,
      clearings: cs.map(([x, y, r], i) => ({ x, y, r, lake: onLake.has(i), feeds: feeds.get(i) || null,
                                             suit: config.fixed ? state.clearings[i].suit || null : null })),
      // A lake's line runs on into its clearings' centres.
      lakes: state.paths.map((p, i) => [p, i]).filter(([p]) => p.kind === 'lake')
        .map(([p, i]) => ({ a: p.a.c, b: p.b.c, points: [centre(p.a), ...shapes[i].points, centre(p.b)] })),
      lines: state.paths.map((p, i) => [p, i]).filter(([p]) => p.kind !== 'lake')
        .map(([p, i]) => ({ points: shapes[i].points,
                            half: PATH.width * (p.kind === 'river' ? PATH.river_width : 1) / 2 })),
      forests: state.forests.map(f => { const [x, y] = px(f); return { x, y, r: PATH.lake.forest_r }; }),
    };
    let rings = [];
    try {
      rings = window.MapLakes.shapes(input).rings;
    } catch (err) {
      rings = [];     // never break the board: the lakes are drawn as lines until it's fixed
    }
    lakeCache = { key, grid, rings };
    return rings;
  }

  // The saved outline (state.lake_shapes, tts_map.py): rings in 0-1 of the frame.
  function lakeShapesForSave() {
    return lakeRings(true).map(ring => ring.map(([x, y]) => [Math.round(x / W * 1e4) / 1e4, Math.round(y / H * 1e4) / 1e4]));
  }

  function drawLakes(g) {
    const rings = lakeRings();
    if (!rings.length) return;
    const d = rings.map(ring => 'M' + ring.map(p => p.map(n => n.toFixed(1)).join(' ')).join('L') + 'Z').join(' ');
    el('path', { d, fill: PATH.colours.lake, 'fill-rule': 'evenodd', 'fill-opacity': config.image ? 0.75 : 1,
                 stroke: '#2c3f80', 'stroke-width': STROKE * 1.5, 'stroke-linejoin': 'round', 'pointer-events': 'none' }, g);
  }

  // Paths & Rivers, drawn first: under the printed art and every piece.
  function drawPaths() {
    pathCache = [];
    if (STEP_PATHS < 0) return;
    const g = layer(STEP_PATHS);
    try {
      pathCache = pathShapes();
    } catch (err) {
      return;   // a loop can't happen (canConnect); draw nothing rather than break the board
    }
    const onStep = step === STEP_PATHS && !config.readonly;
    const ending = drag && drag.kind === 'path-end';
    drawLakes(g);
    // Bottom to top where they cross: rivers, special paths, paths
    // (PATH.draw_order, as printed). Stable within a kind.
    const rank = (i) => PATH.draw_order.indexOf(pathCache[i].kind);
    const order = pathCache.map((_s, i) => i).sort((i, j) => rank(i) - rank(j));
    order.forEach((i) => {
      const s = pathCache[i];
      const pg = el('g', { opacity: ending && drag.index === i ? 0.35 : 1 }, g);
      const pts = s.points.map(p => p.join(',')).join(' ');
      const width = PATH.width * (familyOf(s.kind) === 'river' ? PATH.river_width : 1);   // as drawn (a lake's line, as a river's)
      if (selected && selected.kind === 'path' && selected.index === i) {
        el('polyline', { points: pts, fill: 'none', stroke: '#dc3545', 'stroke-opacity': 0.6,
                         'stroke-width': width + STROKE * 6, 'stroke-linecap': 'round', 'pointer-events': 'none' }, pg);
      }
      drawLanes(pg, s.points, s.kind);
      // The click target: the path's whole width, and never too thin to hit.
      el('polyline', { points: pts, fill: 'none', stroke: 'transparent', 'stroke-width': Math.max(width * 1.3, DOT_PX * 2.5),
                       'pointer-events': 'stroke', 'data-kind': 'path', 'data-index': i, style: 'cursor: pointer' }, pg);
    });
    if (!onStep) return;
    // Each path's middle: another path can start or end there (not a lake's).
    for (const s of pathCache) {
      if (s.kind === 'lake' || drawKind === 'lake') continue;
      el('circle', { cx: s.middle[0], cy: s.middle[1], r: handleRadius(), fill: 'rgba(255,255,255,0.85)',
                     stroke: '#231f20', 'stroke-width': STROKE, 'pointer-events': 'none' }, g);
    }
    // The selected path's ends: drag one to another clearing or path middle.
    if (selected && selected.kind === 'path' && pathCache[selected.index] && !ending) {
      const pts = pathCache[selected.index].points;
      for (const [end, pt] of [['a', pts[0]], ['b', pts[pts.length - 1]]]) {
        el('circle', { cx: pt[0], cy: pt[1], r: handleRadius() * 1.15, fill: '#dc3545', stroke: '#fff',
                       'stroke-width': STROKE * 1.5, 'data-kind': 'path-end', 'data-index': selected.index,
                       'data-end': end, style: 'cursor: grab' }, g);
      }
    }
    // Drawing a path, or moving an end: a band from the fixed end to the
    // pointer, and a ring on the end it would join.
    if (drag && (drag.kind === 'new-path' || ending)) {
      const fixed = anchorPoint(drag.kind === 'new-path' ? drag.from : drag.other);
      if (!fixed) return;
      const kind = drag.kind === 'new-path' ? drawKind : state.paths[drag.index].kind;
      const band = { x1: fixed[0], y1: fixed[1], x2: drag.at[0], y2: drag.at[1], 'stroke-linecap': 'round',
                     'pointer-events': 'none' };
      el('line', { ...band, stroke: '#231f20', 'stroke-opacity': 0.6, 'stroke-width': PATH.width * 0.45 }, g);
      el('line', { ...band, stroke: PATH.colours[kind], 'stroke-width': PATH.width * 0.3,
                   'stroke-dasharray': `${PATH.width * 0.4} ${PATH.width * 0.3}` }, g);
      const target = anchorAt(drag.at);
      if (target && dragTargetOk(target)) {
        const at = anchorPoint(target);
        const r = 'c' in target ? circles()[target.c][2] : handleRadius() * 1.4;
        el('circle', { cx: at[0], cy: at[1], r, fill: 'none', stroke: '#20c997', 'stroke-width': STROKE * 4,
                       'pointer-events': 'none' }, g);
      }
    }
  }

  // Whether releasing the current path drag on `target` joins it.
  function dragTargetOk(target) {
    if (drag.kind === 'new-path') return canConnect(drag.from, target);
    const p = state.paths[drag.index];
    return canConnect(drag.end === 'a' ? target : p.a, drag.end === 'b' ? target : p.b, drag.index);
  }

  function render() {
    syncView();     // first: a new view sets the line widths drawn below
    content.innerHTML = '';
    drawPaths();
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
    if (![STEP_CLEARINGS, STEP_BUILDINGS, STEP_FORESTS, STEP_PATHS].includes(step)) {
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

    // The Paths step connects clearings: their circles (only) at full
    // strength, under the dimmed markers and numbers. Inert: paths find clearings by position.
    if (step === STEP_PATHS) {
      const rings = el('g', { 'pointer-events': 'none' });
      for (const c of state.clearings) {
        if (!c.marker) continue;
        const centre = px(c.centre);
        el('circle', { cx: centre[0], cy: centre[1], r: dist(centre, px(c.marker)), fill: 'rgba(255,255,255,0.12)',
                       stroke: '#ffffff', 'stroke-width': STROKE }, rings);
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
        // On the Paths step the circle is drawn at full strength below instead.
        if (step !== STEP_PATHS) {
          el('circle', { cx: centre[0], cy: centre[1], r: dist(centre, m), fill: 'rgba(255,255,255,0.12)',
                         stroke: colour, 'stroke-width': STROKE, 'pointer-events': 'none' }, g);
        }
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
    if (!MapSummary.markerReady()) {
      el('circle', { cx: m[0], cy: m[1], r: DOT_PX * 1.3, fill: suitColour(c), stroke: isSel ? '#dc3545' : '#000',
                     'stroke-width': STROKE, ...data }, g);
      return;
    }
    const [w, h] = config.marker_px;
    const angle = Math.atan2(m[1] - centre[1], m[0] - centre[0]) * 180 / Math.PI + 90;
    const box = viewBoxOf(document.getElementById('mb-marker'));
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
    // River clearings: clearings at the end of at least one river.
    // River clearings: clearings at the end of at least one river or lake.
    const riverEnds = state.paths.filter(p => familyOf(p.kind) === 'river').flatMap(p => [p.a, p.b]).filter(e => 'c' in e);
    return {
      clearings: state.clearings.length,
      forests: state.forests.length,
      buildings: state.buildings.length,
      ruins: state.buildings.filter(b => b.ruin).length,
      paths: state.paths.filter(p => p.kind === 'path').length,
      special: state.paths.filter(p => p.kind === 'special').length,
      river_clearings: new Set(riverEnds.map(e => e.c)).size,
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
    placeProgressThumb();
    document.getElementById('mb-heading').textContent = STEPS[step].heading;

    updatePopover();
    updateSuitMode();
    updatePathMode();
    updateCountCards();
    slideCountCard();
    updateZoomControls();
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

  // A piece's icon (map_summary.js draws them all, for the summary card too).
  function iconSvg(kind) {
    return MapSummary.icon(kind, PATH ? PATH.colours : null);
  }

  // The review's summary card (map_summary.js draws it, as on the map detail
  // pages); each tile opens the step where that piece is edited.
  const SUIT_NAMES = { fox: 'Fox', rabbit: 'Rabbit', mouse: 'Mouse', bird: 'Bird', city: 'Lost City' };
  function renderReview() {
    const c = counts();
    const tile = (kind, label, count, expected, step) => ({ kind, label, count, expected, step });
    const data = {
      tiles: [
        tile('clearing', 'Clearings', c.clearings, config.expected.clearings, STEP_CLEARINGS),
        tile('building', 'Building Slots', c.buildings, config.expected.buildings, STEP_BUILDINGS),
        tile('ruin', 'Ruins', c.ruins, config.expected.ruins, STEP_BUILDINGS),
        tile('forest', 'Forests', c.forests, config.expected.forests, STEP_FORESTS),
      ],
      // Special paths only when there are some.
      path_tiles: STEP_PATHS < 0 ? [] : [
        tile('path', 'Paths', c.paths, null, STEP_PATHS),
        ...(c.special ? [tile('special', state.special_path_name || 'Special Paths', c.special, null, STEP_PATHS)] : []),
        tile('river', 'River Clearings', c.river_clearings, config.expected.river_clearings, STEP_PATHS),
      ],
      colours: PATH ? PATH.colours : null,
    };
    if (config.fixed) {
      const tally = { fox: 0, rabbit: 0, mouse: 0, bird: 0, city: 0 };
      let unset = 0;
      for (const cl of state.clearings) {
        if (tally[cl.suit] !== undefined) tally[cl.suit] += 1; else unset += 1;
      }
      data.suits = { fixed: true, unset, unset_label: 'No suit',
                     chips: Object.entries(tally).filter(([, n]) => n)
                       .map(([suit, n]) => ({ suit, name: SUIT_NAMES[suit], count: n, icon: config.suit_icons[suit] })) };
    } else {
      data.suits = { fixed: false, note: 'Random Clearing Suits: shuffled each game.', icon: config.random_icon };
    }
    MapSummary.render(document.getElementById('mb-review-summary'), data, { onTile: t => goTo(t.step) });

    const problems = document.getElementById('mb-review-problems');
    problems.innerHTML = '';
    const local = localErrors();
    if (local.length) problems.appendChild(alertBox('danger', local));
  }

  function localErrors() {
    const errors = [];
    if (!state.clearings.length) errors.push('Mark at least one clearing.');
    // Same wording as check_builder(require_buildings=True): the Forge prints them.
    if (config.require_buildings) {
      if (!state.buildings.length) errors.push('Mark at least one building slot.');
      if (!state.buildings.some(b => b.ruin)) errors.push('Mark at least one ruin.');
    }
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
    // Its paths go too (and those attached to them); the rest point past the gap.
    const touching = new Set();
    state.paths.forEach((p, k) => { if (p.a.c === index || p.b.c === index) touching.add(k); });
    if (touching.size) removePaths(touching);
    for (const p of state.paths) for (const e of [p.a, p.b]) if ('c' in e && e.c > index) e.c -= 1;
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
    } else if (selected.kind === 'path') {
      removePaths(new Set([selected.index]));
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
    return { kind: target.dataset.kind, index: Number(target.dataset.index), end: target.dataset.end };
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
    // and keep the whole page unselectable until the drag ends. That also stops
    // the browser moving focus, so a text field being typed in (the special
    // paths' name) is let go of here, as a press anywhere else would.
    evt.preventDefault();
    const typing = document.activeElement;
    if (typing && ['INPUT', 'TEXTAREA'].includes(typing.tagName)) typing.blur();
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

    if (step === STEP_PATHS) {
      // Drag a selected path's end to reconnect it.
      if (h && h.kind === 'path-end') {
        const path = state.paths[h.index];
        drag = { kind: 'path-end', index: h.index, end: h.end, other: h.end === 'a' ? path.b : path.a, at: p };
        render();
        return;
      }
      // A press on a clearing or a path's middle starts a new path from it.
      const from = anchorAt(p);
      if (from) {
        // A new path: the one selected before isn't any more. (A click on a
        // path's middle, with no drag, selects that path on release.)
        selected = null;
        drag = { kind: 'new-path', from, at: p, start: p };
        render();
        return;
      }
      // A press on a path selects it (its pop-up: type and delete).
      selected = h && h.kind === 'path' ? { kind: 'path', index: h.index } : null;
      render();
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
      // A press inside an existing clearing's circle selects it rather than
      // starting a new one.
      const inside = clearingAt(p);
      if (inside !== null) {
        selected = { kind: 'clearing', index: inside };
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
      // The whole map: a press inside a clearing zooms in on it (buildings go
      // in there, up close); outside every clearing, it places one here.
      const inside = zoomActive() ? null : clearingAt(p);
      if (inside !== null) {
        changeView(() => zoomToClearing(inside));
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
      path: [STEP_PATHS, 'path'],
    }[h.kind];
    if (!target || target[0] < 0) return;
    activePointer = null;
    document.body.classList.remove('mb-dragging');
    goTo(target[0]);
    if (target[1] && Number.isInteger(h.index)) {
      selected = { kind: target[1], index: h.index };
      // A building: zoomed in on its clearing.
      if (target[1] === 'building') zoomOnBuilding(h.index);
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
    if (drag.kind === 'new-path' || drag.kind === 'path-end') {
      drag.at = p;    // nothing changes until it's dropped on an end
      render();
      return;
    }
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
    let edited = false;
    if (drag && drag.kind === 'new-path') {
      const target = anchorAt(drag.at);
      if (target && canConnect(drag.from, target)) {
        state.paths.push({ kind: drawKind, a: drag.from, b: target });
        chooseSide(state.paths.length - 1, 'b', drag.at);
        edited = true;
      } else if ('p' in drag.from && dist(drag.at, drag.start) < DOT_PX) {
        // A click on a path's middle (no drag): select that path.
        selected = { kind: 'path', index: drag.from.p };
      }
    }
    if (drag && drag.kind === 'path-end') {
      // Dropped on an end it can join: reconnect. Anywhere else: it snaps back.
      const target = anchorAt(drag.at);
      if (target && dragTargetOk(target)) {
        state.paths[drag.index][drag.end] = target;
        chooseSide(drag.index, drag.end, drag.at);
        edited = true;
      }
    }
    const wasDragging = drag !== null;
    drag = null;
    if (edited) changed();
    else if (wasDragging) render();   // the edit pop-up hides while dragging
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

  // ── Path kinds: the Draw card's toggle and the pop-up's ──────────────────
  const pathMode = document.getElementById('mb-path-mode');
  const drawToggle = document.getElementById('mb-draw-kind');
  const popKindWrap = document.getElementById('mb-pop-kind-wrap');
  const popKind = document.getElementById('mb-pop-kind');

  // Slide the toggle's highlight onto `kind`'s button. The segments differ in
  // width (special paths are just a swatch), so it's placed from the button
  // itself (--mb-kind-x / --mb-kind-w, map_builder.css).
  function setKindToggle(toggle, kind) {
    if (familyOf(kind) === 'river') setWaterKind(toggle, kind);
    toggle.querySelectorAll('[data-path-kind]').forEach(btn => {
      const on = btn.dataset.pathKind === kind;
      btn.setAttribute('aria-checked', on ? 'true' : 'false');
      if (on && btn.offsetWidth) {
        toggle.style.setProperty('--mb-kind-x', `${btn.offsetLeft}px`);
        toggle.style.setProperty('--mb-kind-w', `${btn.offsetWidth}px`);
      }
    });
  }

  // The water segment is River or Lake: its swatch, label and kind follow the
  // choice, and its menu ticks it.
  function setWaterKind(toggle, kind) {
    const btn = toggle.querySelector('[data-water]');
    if (!btn) return;
    btn.dataset.pathKind = kind;
    const label = kind === 'lake' ? btn.dataset.labelLake : btn.dataset.labelRiver;
    btn.querySelector('.mb-kind-label').textContent = label;
    btn.title = label;
    btn.setAttribute('aria-label', label);
    btn.querySelector('.mb-kind-swatch').classList.toggle('mb-kind-swatch-lake', kind === 'lake');
    toggle.querySelectorAll('[data-water-kind]').forEach(item => {
      item.setAttribute('aria-checked', item.dataset.waterKind === kind ? 'true' : 'false');
    });
  }

  function waterMenuOpen(toggle) {
    const menu = toggle.querySelector('.mb-water-menu');
    return !!menu && !menu.hidden;
  }

  function openWaterMenu(toggle, open) {
    const menu = toggle.querySelector('.mb-water-menu');
    if (!menu) return;
    menu.hidden = !open;
    toggle.classList.toggle('mb-water-open', open);
    if (!open) return;
    // Under the water segment, right edges lined up; below the toggle, or
    // above it when the board has no room below.
    const btn = toggle.querySelector('[data-water]');
    menu.style.right = `${Math.max(0, toggle.clientWidth - btn.offsetLeft - btn.offsetWidth)}px`;
    menu.classList.remove('up');
    if (menu.getBoundingClientRect().bottom > board.getBoundingClientRect().bottom) menu.classList.add('up');
    const item = menu.querySelector('[aria-checked="true"]:not(:disabled)') || menu.querySelector('button:not(:disabled)');
    if (item) item.focus();
  }

  // A kind toggle (the Draw card's, or the selected path's): onPick(kind).
  // The water segment, when it's already chosen (or by its chevron), opens its
  // River / Lake menu instead.
  function wireKindToggle(toggle, onPick) {
    toggle.querySelectorAll('[data-path-kind]').forEach(btn => {
      btn.addEventListener('click', (evt) => {
        const water = btn.hasAttribute('data-water');
        if (water && (evt.target.closest('.mb-kind-chevron') || btn.getAttribute('aria-checked') === 'true')) {
          openWaterMenu(toggle, !waterMenuOpen(toggle));
          return;
        }
        openWaterMenu(toggle, false);
        onPick(btn.dataset.pathKind);
      });
    });
    const menu = toggle.querySelector('.mb-water-menu');
    if (!menu) return;
    menu.querySelectorAll('[data-water-kind]').forEach(item => {
      item.addEventListener('click', () => {
        openWaterMenu(toggle, false);
        onPick(item.dataset.waterKind);
      });
    });
    menu.addEventListener('keydown', (evt) => {
      const items = [...menu.querySelectorAll('button:not(:disabled)')];
      const at = items.indexOf(document.activeElement);
      if (evt.key === 'Escape') {
        openWaterMenu(toggle, false);
        toggle.querySelector('[data-water]').focus();
      } else if (evt.key === 'ArrowDown' || evt.key === 'ArrowUp') {
        const next = items[(at + (evt.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length];
        if (next) next.focus();
      } else {
        return;
      }
      evt.preventDefault();
      evt.stopPropagation();     // not the builder's own keys
    });
    // A press anywhere else closes it.
    document.addEventListener('pointerdown', (evt) => {
      if (waterMenuOpen(toggle) && !toggle.contains(evt.target)) openWaterMenu(toggle, false);
    }, true);
  }

  // Can path `index` become a lake? Only joining two clearings, with nothing
  // on its middle, nor on the water path it would replace (replaceTwin).
  function lakeAllowed(index) {
    const path = state.paths[index];
    if (!('c' in path.a && 'c' in path.b)) return "A lake joins two clearings, not a path's middle.";
    const attached = (k) => state.paths.some(q => [q.a, q.b].some(e => 'p' in e && e.p === k));
    if (attached(index)) return "Another path ends on this one's middle: a lake can't have one.";
    const twin = state.paths.findIndex((q, k) => k !== index && pairKey(q.a, q.b) === pairKey(path.a, path.b)
                                                && familyOf(q.kind) === 'river');
    if (twin >= 0 && attached(twin)) return "Another path ends on the river here: a lake can't have one.";
    return null;
  }

  if (drawToggle) {
    wireKindToggle(drawToggle, (kind) => {
      drawKind = kind;
      updatePathMode();
      render();         // (a lake hides the path middles' handles)
    });
    pathMode.addEventListener('pointerdown', (evt) => evt.stopPropagation());
  }
  if (popKind) {
    wireKindToggle(popKind, (kind) => {
      if (!selected || selected.kind !== 'path') return;
      const path = state.paths[selected.index];
      if (path.kind === kind || (kind === 'lake' && lakeAllowed(selected.index))) return;
      path.kind = kind;
      replaceTwin(selected.index);
      changed();
    });
  }

  // The Draw card, where the item track sits (unused on this step). Very
  // small screens: just the three swatches.
  function updatePathMode() {
    if (!pathMode) return;
    const show = step === STEP_PATHS && !config.readonly;
    pathMode.classList.toggle('d-none', !show);
    if (!show) return;
    const title = document.getElementById('mb-path-title');
    title.textContent = title.dataset[drawKind];
    const box = itemTrackBox();
    setCompactSizes(box);
    const compact = box.width < COMPACT_BELOW;
    pathMode.classList.toggle('compact', compact);
    const width = compact
      ? Math.min(Math.max(box.width, 96), box.area.width - 8)
      : Math.min(Math.max(box.width, 230), 300, box.area.width - 8);
    placeOnItemTrack(pathMode, width);
    // Last: the highlight is placed from its button's laid-out size.
    setKindToggle(drawToggle, drawKind);
  }
  window.addEventListener('resize', updatePathMode);

  function selectedNode() {
    if (!selected) return null;
    if (selected.kind === 'clearing') return content.querySelector(`[data-clearing="${selected.index}"]`);
    return content.querySelector(`[data-kind="${selected.kind}"][data-index="${selected.index}"]`);
  }

  function updatePopover() {
    const editable = selected && !drag && (
      (step === STEP_CLEARINGS && selected.kind === 'clearing') || (step === STEP_FORESTS && selected.kind === 'forest')
      || (step === STEP_BUILDINGS && selected.kind === 'building') || (step === STEP_PATHS && selected.kind === 'path'));
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
    // A path: its kind (the same toggle as the Draw card) and delete.
    if (popKindWrap) {
      popKindWrap.classList.toggle('d-none', selected.kind !== 'path');
      if (selected.kind === 'path') {
        const kind = state.paths[selected.index].kind;
        if (familyOf(kind) === 'land') setWaterKind(popKind, 'river');   // (not left on another path's Lake)
        setKindToggle(popKind, kind);
        // Lake only where it can be one (lakeAllowed says why not).
        const lakeItem = popKind.querySelector('[data-water-kind="lake"]');
        const why = lakeAllowed(selected.index);
        lakeItem.disabled = !!why && state.paths[selected.index].kind !== 'lake';
        lakeItem.title = lakeItem.disabled ? why : '';
      } else {
        openWaterMenu(popKind, false);
      }
    }
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
    if (card === countCard && zoomActive()) {
      placeInTopCorner(card, width);   // the item track is out of view
      return;
    }
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
    // On the VP track, like Clearings.
    [STEP_PATHS]: [['path', 'paths', 'Paths'], ['special', 'special', 'Special Paths', 'Special'],
                   ['river', 'river_clearings', 'River Clearings', 'Rivers']],
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
      // Paths step, with special paths: their label is the map's name for them,
      // an input (kept even when the other labels are dropped).
      const naming = key === 'special' && step === STEP_PATHS && n >= 1;
      return `<div class="mb-count-row"><span class="mb-count-icon">${iconSvg(icon)}</span>`
        + `<span class="mb-count-figure${off}">${figure}</span>`
        + (naming ? specialNameInput() : compact ? '' : `<span class="mb-count-label">${label}</span>`) + '</div>';
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
    const onPill = step === STEP_CLEARINGS || (STEP_PATHS >= 0 && step === STEP_PATHS);
    const rows = onPill ? null : COUNT_ROWS[step];
    countCard.classList.toggle('d-none', !rows);
    if (rows) updateCountCard(rows, box, compact);

    // Clearings (and Paths) pill, centred on the VP track and kept between its grey rows.
    clearingPill.classList.toggle('d-none', !onPill);
    if (onPill) {
      const cols = trackColumns();
      const gap = config.track.row_gap * PPL * box.scale;
      const height = Math.min(Math.max(gap * 1.8, 24), 40);
      clearingPill.style.height = `${height}px`;
      clearingPill.style.fontSize = `${Math.min(Math.max(height * 0.45, 12), 17)}px`;
      clearingPill.style.setProperty('--mb-pill-icon', `${Math.round(Math.min(Math.max(height * 0.45, 12), 18))}px`);
      const nameField = document.getElementById('mb-special-name');
      if (nameField && document.activeElement === nameField) {
        // Typing the special paths' name: just the numbers, so focus stays put.
        clearingPill.querySelectorAll('.mb-count-figure').forEach((figure, i) => {
          figure.textContent = counts()[COUNT_ROWS[step][i][1]];
        });
        placePill();
        return;
      }
      // The VP track spans nearly the whole board, so the full text fits even on
      // small screens; drop the label only if the pill would be wider than the track.
      const fill = (...args) => {
        clearingPill.innerHTML = countRowsHtml(COUNT_ROWS[step], ...args);
        fitSpecialName();
      };
      fill(false);
      const trackWidth = (Math.max(...cols) - Math.min(...cols)) * box.scale;
      if (clearingPill.offsetWidth > trackWidth) fill(false, false, true);   // short labels
      if (clearingPill.offsetWidth > trackWidth) fill(true);
      placePill();
    }
  }

  // The VP track's pill: centred on the track, between its grey rows.
  function placePill() {
    const box = itemTrackBox();
    const cols = trackColumns();
    const cx = (Math.min(...cols) + Math.max(...cols)) / 2 * box.scale;
    const cy = state.track_y * H * box.scale;
    const w = clearingPill.offsetWidth, h = clearingPill.offsetHeight;
    clearingPill.style.left = `${Math.min(Math.max(cx - w / 2, 4), box.area.width - w - 4)}px`;
    clearingPill.style.top = `${cy - h / 2}px`;
  }

  // ── The special paths' name (in the Paths step's pill) ──
  // What the map's special paths are (Bridges, Mountain Passes...): saved with
  // the layout, and the label of their summary tile (tts_map.layout_summary).
  function specialNameInput() {
    return `<input type="text" id="mb-special-name" maxlength="${config.special_name_max || 24}"`
      + ' placeholder="Special Paths" aria-label="Special Path Name" autocomplete="off" spellcheck="false">';
  }

  // Fill the name input (its value set as a property: it's the user's text) and
  // size it to the text, or to the placeholder when empty.
  let measureCanvas = null;
  function fitSpecialName() {
    const input = document.getElementById('mb-special-name');
    if (!input) return;
    if (input.value !== (state.special_path_name || '')) input.value = state.special_path_name || '';
    const style = getComputedStyle(input);
    measureCanvas = measureCanvas || document.createElement('canvas');
    const ctx = measureCanvas.getContext('2d');
    ctx.font = `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
    const text = Math.max(ctx.measureText(input.value).width, ctx.measureText(input.placeholder).width);
    const extra = parseFloat(style.paddingLeft) + parseFloat(style.paddingRight)
      + parseFloat(style.borderLeftWidth) + parseFloat(style.borderRightWidth);
    input.style.width = `${Math.ceil(text + extra + 2)}px`;
  }

  clearingPill.addEventListener('input', (evt) => {
    if (evt.target.id !== 'mb-special-name') return;
    state.special_path_name = evt.target.value;
    dirty = true;
    showSaved(false);
    fitSpecialName();
    placePill();
  });
  clearingPill.addEventListener('keydown', (evt) => {
    if (evt.target.id === 'mb-special-name' && evt.key === 'Enter') evt.target.blur();
  });

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
  const languageInput = document.getElementById('mb-language');
  const removeArtButton = document.getElementById('mb-remove-art');

  // The Start step's name and language go with every save, unless the map is
  // linked to a keep post (then they're the post's and locked).
  function startStepFields(payload) {
    if (nameInput && !config.name_locked) {
      payload.name = nameInput.value;
      if (languageInput) payload.language = languageInput.value;
    }
    return payload;
  }
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

  // The Start step is done with a name (the art is optional: without it the
  // map is printed on a grey board); a new map must also be created (by Next)
  // before the later steps open.
  function artStepDone() {
    return hasName();
  }

  function artStepLocked() {
    return STEP_UPLOAD >= 0 && (!artStepDone() || isNew());
  }

  function artStepMissing() {
    return 'Name your map first.';
  }

  function updateUpload() {
    document.getElementById('mb-crop-notice').classList.toggle('d-none', !config.art_cropped || !hasArt());
    if (!artStep) return;
    artStep.classList.toggle('d-none', step !== STEP_UPLOAD || !!config.readonly);
    // The art is there but the name isn't: show what's left.
    nameInput.classList.toggle('mb-missing', hasArt() && !hasName());
    uploadButton.classList.toggle('mb-has-art', hasArt());
    if (removeArtButton) removeArtButton.classList.toggle('d-none', !config.image);
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
        if (languageInput) body.append('language', languageInput.value);
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
          document.getElementById('mb-image').src = BLANK_GIF;
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
          document.getElementById('mb-image').src = config.image || BLANK_GIF;
          previousArt = null;
        }
        artStatus('danger', result.error || "That image couldn't be used. Try another file.");
        render();
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

  // The art is optional: take it away (the map is then printed on grey).
  async function removeArt() {
    clearTimeout(retryTimer);
    pendingArt = null;
    previousArt = null;
    if (uploading) await uploading.catch(() => {});   // don't let an upload in flight bring it back
    if (!isNew() && config.upload_url) {
      try {
        const body = new FormData();
        body.append('remove', '1');
        const response = await fetch(config.upload_url, { method: 'POST', headers: { 'X-CSRFToken': csrfToken() }, body });
        if (!response.ok) throw new Error(String(response.status));
      } catch (err) {
        artStatus('danger', "Couldn't remove the art — check your connection and try again.");
        return;
      }
    }
    config.image = null;
    config.art_cropped = false;
    document.getElementById('mb-image').src = BLANK_GIF;
    artStatus(null);
    render();
  }

  if (artStep) {
    artStep.addEventListener('pointerdown', (evt) => evt.stopPropagation());
    if (removeArtButton) removeArtButton.addEventListener('click', removeArt);
    uploadButton.addEventListener('click', () => uploadInput.click());
    document.querySelector('#mb-art-status .mb-art-status-close').addEventListener('click', () => artStatus(null));
    // The name is saved with the layout (Save Progress or Complete).
    if (languageInput) {
      languageInput.addEventListener('change', () => {
        dirty = true;
        showSaved(false);
      });
      languageInput.addEventListener('pointerdown', (evt) => evt.stopPropagation());
    }
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

  // The progress bar's highlight: over the current step's number and name,
  // centred on them. A long name can be wider than its share of the bar (it
  // spills over both sides, centred), so the highlight is measured from the
  // text, never narrower than the step's own share.
  const THUMB_PAD = 12;     // px either side of the text
  const THUMB_INSET = 3;    // the bar's padding
  function placeProgressThumb() {
    const bar = document.getElementById('mb-progress');
    const thumb = document.getElementById('mb-progress-thumb');
    const btn = bar && bar.querySelector(`[data-step="${step}"]`);
    if (!thumb || !btn) return;
    const area = bar.getBoundingClientRect();
    if (!area.width) return;   // hidden (read-only): nothing to place
    const cell = btn.getBoundingClientRect();
    let left = Infinity, right = -Infinity;
    for (const child of btn.children) {   // the number and the name (whichever of its labels shows)
      const r = child.getBoundingClientRect();
      if (!r.width) continue;
      left = Math.min(left, r.left);
      right = Math.max(right, r.right);
    }
    const centre = left < right ? (left + right) / 2 : cell.left + cell.width / 2;
    const textWidth = left < right ? right - left + 2 * THUMB_PAD : 0;
    const width = Math.min(Math.max(cell.width, textWidth), area.width - 2 * THUMB_INSET);
    // Centred on the text, but kept inside the bar (the first and last steps).
    const x = Math.min(Math.max(centre - area.left - width / 2, THUMB_INSET), area.width - THUMB_INSET - width);
    thumb.style.left = `${x}px`;
    thumb.style.width = `${width}px`;
  }
  window.addEventListener('resize', placeProgressThumb);
  // The step names' font (and so their widths) may load after the first layout.
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(placeProgressThumb);

  // ── Steps and saving ──────────────────────────────────────────────────────
  function goTo(n) {
    n = Math.min(Math.max(n, 0), STEP_REVIEW);
    if (n > STEP_UPLOAD && artStepLocked()) n = STEP_UPLOAD;   // name and art first
    step = n;
    selected = null;
    if (pendingMarker !== null && !state.clearings[pendingMarker].marker) removeClearing(pendingMarker);
    pendingMarker = null;
    if (step === STEP_BUILDINGS) zoomStart();
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
  if (STEP_PATHS >= 0) STAGE_OF_STEP[STEP_PATHS] = 'paths';
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

  // Complete can take a while (the Forge renders the map): the button shows a
  // spinner and can't be pressed again until the server answers. When the
  // answer is a redirect it stays that way while the next page loads.
  async function save(generate) {
    const button = document.getElementById('mb-generate');
    if (!generate) return saveNow(false);
    if (button.disabled) return;
    const label = button.innerHTML;
    button.disabled = true;
    button.innerHTML = '<span class="spinner-border spinner-border-sm me-1" aria-hidden="true"></span>Saving…';
    let leaving = false;
    try {
      leaving = await saveNow(true);
    } finally {
      if (!leaving) {
        button.disabled = false;
        button.innerHTML = label;
      }
    }
  }

  // The layout to save: with its lakes' outline worked out from exactly this
  // layout, at full size (the printed map and setup card draw it).
  function withLakeShapes(layout) {
    const out = { ...layout };
    const shapes = lakeShapesForSave();
    if (shapes.length) out.lake_shapes = shapes; else delete out.lake_shapes;
    return out;
  }

  // Returns true when the page is moving on (a redirect).
  async function saveNow(generate) {
    const box = document.getElementById('mb-messages');
    box.innerHTML = '';
    // A new map is created first (Save is only enabled once it has a name).
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
        body: JSON.stringify(startStepFields({ state: withLakeShapes(state), generate })),
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
      const KEYS = { clearings: ['clearings'], buildings: ['buildings', 'ruins'], forests: ['forests'],
                     paths: ['river_clearings'] };
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
      return true;
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
