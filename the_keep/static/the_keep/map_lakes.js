// Lakes (the map builder's Paths step): where the water goes.
//
// A lake is drawn from clearing to clearing like a river, but printed as an
// area of water. It fills the space around it, stopping halfway to the land:
// every point of the board goes to whichever it's nearer, the water (the
// lakes' lines and their clearings) or the land (other clearings, paths,
// special paths, rivers, forest markers, building slots and ruins). So lakes
// with nothing between them merge into one, and a lake covers its own
// clearings but leaves their buildings dry.
//
// A lake body (clearings joined by lake paths) whose clearings are all on the
// edge of the map (reachable from the board's edge without crossing a path,
// river or clearing) is an ocean: the stretch of the board's edge nearest its
// clearings is water too, ending halfway to the next edge clearings; all of
// it when its clearings ring the map.
//
// Plain data in, plain data out (no DOM, no builder state): map_builder.js
// calls MapLakes.shapes() and saves the outline with the layout
// (state.lake_shapes, tts_map.py), and the printed map and the setup card
// draw that saved outline.
window.MapLakes = window.MapLakes || (function () {
  'use strict';

  // input: {
  //   width, height,                    the frame (pixels)
  //   grid,                             cells across the frame
  //   clearings: [{x, y, r, lake}],     lake: on a lake
  //   lakes: [{points, a, b}],          each lake path's line (clearing centre to centre) and its clearings
  //   lines: [{points, half}],          every other path, special path and river (land)
  //   forests: [{x, y, r}], buildings: [{x, y, r, margin}],
  //   overlap,                          a lake overlaps this share of its clearings' radius (their edge)
  //   smooth,                           spikes and points narrower than twice this are rounded off (pixels)
  //   minHalf,                          a lake is never narrower than twice this along its line (pixels)
  //   round,                            corners are rounded over about this distance (pixels)
  // }
  // returns { rings: [[[x, y], ...], ...] (pixels, even-odd), oceans: [clearing index, ...] }
  function shapes(input) {
    const W = input.width, H = input.height;
    const cell = W / Math.max(8, input.grid || 180);
    // A ring of cells outside the frame: the board's edge, and so every
    // outline closes.
    const cols = Math.ceil(W / cell) + 2, rows = Math.ceil(H / cell) + 2;
    const N = cols * rows;
    const cx = (i) => (i - 0.5) * cell, cy = (j) => (j - 0.5) * cell;   // cell centre (i = 0: just outside)
    const border = (i, j) => i === 0 || j === 0 || i === cols - 1 || j === rows - 1;
    const lakes = input.lakes || [];
    if (!lakes.length) return { rings: [], oceans: [] };

    // ── Marking cells ──
    function disc(mark, x, y, r) {
      const i0 = Math.max(0, Math.floor(x / cell + 0.5 - r / cell)), i1 = Math.min(cols - 1, Math.ceil(x / cell + 0.5 + r / cell));
      const j0 = Math.max(0, Math.floor(y / cell + 0.5 - r / cell)), j1 = Math.min(rows - 1, Math.ceil(y / cell + 0.5 + r / cell));
      for (let j = j0; j <= j1; j++) {
        for (let i = i0; i <= i1; i++) {
          if (Math.hypot(cx(i) - x, cy(j) - y) <= Math.max(r, cell * 0.5)) mark(j * cols + i);
        }
      }
    }
    function line(mark, points, half) {
      const reach = Math.max(half, cell * 0.75);     // at least a cell thick, so nothing leaks across
      for (let k = 1; k < points.length; k++) {
        const [ax, ay] = points[k - 1], [bx, by] = points[k];
        const i0 = Math.max(0, Math.floor((Math.min(ax, bx) - reach) / cell + 0.5)), i1 = Math.min(cols - 1, Math.ceil((Math.max(ax, bx) + reach) / cell + 0.5));
        const j0 = Math.max(0, Math.floor((Math.min(ay, by) - reach) / cell + 0.5)), j1 = Math.min(rows - 1, Math.ceil((Math.max(ay, by) + reach) / cell + 0.5));
        for (let j = j0; j <= j1; j++) {
          for (let i = i0; i <= i1; i++) {
            if (segmentDistance(cx(i), cy(j), ax, ay, bx, by) <= reach) mark(j * cols + i);
          }
        }
      }
    }

    // ── Which clearings are on the map's edge ──
    // Flood in from the border through every cell not on a clearing or a
    // line (paths, rivers and lakes alike), 4-connected.
    const wall = new Uint8Array(N);
    const setWall = (k) => { wall[k] = 1; };
    input.clearings.forEach(c => disc(setWall, c.x, c.y, c.r));
    for (const l of input.lines) line(setWall, l.points, l.half);
    for (const l of lakes) line(setWall, l.points, 0);
    const outer = new Uint8Array(N);
    const queue = new Int32Array(N);
    let head = 0, tail = 0;
    for (let j = 0; j < rows; j++) {
      for (let i = 0; i < cols; i++) {
        if (border(i, j)) { const k = j * cols + i; outer[k] = 1; queue[tail++] = k; }
      }
    }
    const step = (k, fn) => {
      const i = k % cols, j = (k - i) / cols;
      if (i > 0) fn(k - 1); if (i < cols - 1) fn(k + 1);
      if (j > 0) fn(k - cols); if (j < rows - 1) fn(k + cols);
    };
    while (head < tail) {
      step(queue[head++], (n) => { if (!outer[n] && !wall[n]) { outer[n] = 1; queue[tail++] = n; } });
    }
    // A clearing is on the edge when the outer area touches its circle.
    // Each one also claims the outer area nearest it (through the outer area,
    // never across the network), for the oceans below.
    const owner = new Int32Array(N).fill(-1);
    head = tail = 0;
    const edge = new Set();
    input.clearings.forEach((c, index) => {
      disc((k) => {
        step(k, (n) => {
          if (outer[n] && owner[n] < 0) { owner[n] = index; queue[tail++] = n; edge.add(index); }
        });
      }, c.x, c.y, c.r + cell);
    });
    while (head < tail) {
      const k = queue[head++];
      step(k, (n) => { if (outer[n] && owner[n] < 0) { owner[n] = owner[k]; queue[tail++] = n; } });
    }

    // ── Lake bodies, and which are oceans ──
    const parent = new Map();
    const find = (k) => { while (parent.get(k) !== k) { parent.set(k, parent.get(parent.get(k))); k = parent.get(k); } return k; };
    for (const l of lakes) {
      for (const k of [l.a, l.b]) if (!parent.has(k)) parent.set(k, k);
      parent.set(find(l.a), find(l.b));
    }
    const bodies = new Map();
    for (const k of parent.keys()) {
      const root = find(k);
      if (!bodies.has(root)) bodies.set(root, []);
      bodies.get(root).push(k);
    }
    const oceans = [];
    for (const members of bodies.values()) {
      if (members.every(k => edge.has(k))) oceans.push(...members);
    }
    const oceanClearing = new Set(oceans);

    // ── Seeds: water and land ──
    // A lake overlaps only the edge of its clearings: inside that (the inner
    // disc) stays dry, and the lake's line starts at the inner disc's edge.
    const overlap = input.overlap === undefined ? 0.25 : input.overlap;
    const inner = (c) => c.r * (1 - overlap);
    const water = new Uint8Array(N), land = new Uint8Array(N), lakeLine = new Uint8Array(N);
    const setWater = (k) => { water[k] = 1; lakeLine[k] = 1; }, setLand = (k) => { land[k] = 1; };
    for (const l of lakes) {
      const ca = input.clearings[l.a], cb = input.clearings[l.b];
      line(setWater, trimEnds(l.points, [ca.x, ca.y], inner(ca), [cb.x, cb.y], inner(cb)), cell * 0.5);
    }
    input.clearings.forEach(c => { if (!c.lake) disc(setLand, c.x, c.y, c.r); });
    for (const l of input.lines) line(setLand, l.points, l.half);
    for (const f of input.forests) disc(setLand, f.x, f.y, f.r);
    // A building slot or ruin is land, except in a lake's own clearing: there
    // it only stays dry (below), so the lake still reaches the clearing's edge.
    const inLakeClearing = (b) => input.clearings.some(c => c.lake && Math.hypot(b.x - c.x, b.y - c.y) < c.r);
    for (const b of input.buildings) if (!inLakeClearing(b)) disc(setLand, b.x, b.y, b.r);
    // Outside the network (between the edge clearings and the board's edge):
    // - the board's edge nearest an ocean's clearings is water, the rest land,
    //   so an ocean takes in its stretch of the edge, and its shore curves
    //   away halfway to the next edge clearings (not a hard line);
    // - the outer area nearest an inland lake's clearings is land, so an
    //   inland lake never spills out past the map's edge clearings.
    const inland = new Set([...parent.keys()].filter(k => !oceanClearing.has(k)));
    for (let k = 0; k < N; k++) {
      if (!outer[k]) continue;
      const i = k % cols, j = (k - i) / cols;
      if (inland.has(owner[k])) land[k] = 1;
      else if (border(i, j)) {
        if (owner[k] >= 0 && oceanClearing.has(owner[k])) water[k] = 1; else land[k] = 1;
      }
    }

    // ── Halfway to the land ──
    const dWater = distances(water, cols, rows, cell), dLand = distances(land, cols, rows, cell);
    // Always dry, whatever's nearer: building slots and ruins (and a margin
    // round them), the inner discs of lake clearings, every other clearing,
    // and every path, special path and river line.
    const dry = new Uint8Array(N);
    const setDry = (k) => { dry[k] = 1; };
    for (const b of input.buildings) disc(setDry, b.x, b.y, b.r + (b.margin || 0));
    input.clearings.forEach(c => disc(setDry, c.x, c.y, c.lake ? inner(c) : c.r));
    for (const l of input.lines) line(setDry, l.points, l.half);
    const wet = new Uint8Array(N);
    for (let k = 0; k < N; k++) wet[k] = !dry[k] && (dLand[k] - dWater[k] > 0 || (water[k] && !land[k])) ? 1 : 0;

    // ── No spikes ──
    // Open the water: shrink it by `smooth`, then grow what's left back by
    // `smooth`. Anything narrower than twice that (the wedges the halfway
    // rule makes between land, a thin ring round a clearing) goes; corners
    // round off; big water keeps its shape.
    const smooth = Math.max(input.smooth || 0, cell);
    const notWet = new Uint8Array(N);
    for (let k = 0; k < N; k++) notWet[k] = wet[k] ? 0 : 1;
    const dShore = distances(notWet, cols, rows, cell);
    const core = new Uint8Array(N);
    for (let k = 0; k < N; k++) core[k] = dShore[k] > smooth ? 1 : 0;
    const dCore = distances(core, cols, rows, cell);
    // Never narrower than a wide river along a lake's own line, so opening
    // can't cut a narrow lake in two.
    const dLine = distances(lakeLine, cols, rows, cell);
    const minHalf = input.minHalf || cell;
    // Soften: blur the shore (a signed distance, so a straight shore stays
    // put) over `round` pixels, rounding every corner, inside and out, so
    // lakes read as lakes, not polygons.
    let open = new Float32Array(N);
    for (let k = 0; k < N; k++) open[k] = smooth - dCore[k];
    const passes = 3, radius = Math.round((input.round || 0) / cell / Math.sqrt(passes));
    for (let p = 0; p < passes && radius > 0; p++) open = boxBlur(open, cols, rows, radius);
    const field = new Float32Array(N);       // > 0: water (a smooth signed distance, for tracing)
    for (let k = 0; k < N; k++) {
      field[k] = Math.max(open[k], minHalf - dLine[k]);
      if (dry[k]) field[k] = Math.min(field[k], -cell * 0.5);
    }

    const rings = trace(field, cols, rows, cx, cy).map(r => thin(chaikin(chaikin(r)), cell * 0.3, 400))
      .filter(r => r.length >= 3);
    return { rings, oceans };
  }

  // A box blur of radius r cells, rows then columns (three passes are close
  // to a Gaussian). Off the grid counts as the edge cell.
  function boxBlur(values, cols, rows, r) {
    const tmp = new Float32Array(values.length), out = new Float32Array(values.length);
    const span = 2 * r + 1;
    for (let j = 0; j < rows; j++) {
      let sum = 0;
      const at = (i) => values[j * cols + Math.min(cols - 1, Math.max(0, i))];
      for (let i = -r; i <= r; i++) sum += at(i);
      for (let i = 0; i < cols; i++) {
        tmp[j * cols + i] = sum / span;
        sum += at(i + r + 1) - at(i - r);
      }
    }
    for (let i = 0; i < cols; i++) {
      let sum = 0;
      const at = (j) => tmp[Math.min(rows - 1, Math.max(0, j)) * cols + i];
      for (let j = -r; j <= r; j++) sum += at(j);
      for (let j = 0; j < rows; j++) {
        out[j * cols + i] = sum / span;
        sum += at(j + r + 1) - at(j - r);
      }
    }
    return out;
  }

  // The polyline without the parts inside the two discs at its ends.
  function trimEnds(points, a, ra, b, rb) {
    let start = 0, end = points.length - 1;
    while (start < end && Math.hypot(points[start + 1][0] - a[0], points[start + 1][1] - a[1]) <= ra) start++;
    while (end > start && Math.hypot(points[end - 1][0] - b[0], points[end - 1][1] - b[1]) <= rb) end--;
    const cut = (p, q, centre, r) => {
      // Where the segment p->q (p inside, q outside) crosses the circle.
      let lo = 0, hi = 1;
      for (let i = 0; i < 20; i++) {
        const m = (lo + hi) / 2, x = p[0] + (q[0] - p[0]) * m, y = p[1] + (q[1] - p[1]) * m;
        if (Math.hypot(x - centre[0], y - centre[1]) <= r) lo = m; else hi = m;
      }
      return [p[0] + (q[0] - p[0]) * hi, p[1] + (q[1] - p[1]) * hi];
    };
    const out = points.slice(start, end + 1);
    if (out.length < 2) return points;
    out[0] = cut(points[start], points[start + 1], a, ra);
    out[out.length - 1] = cut(points[end], points[end - 1], b, rb);
    return out;
  }

  function segmentDistance(px, py, ax, ay, bx, by) {
    const dx = bx - ax, dy = by - ay;
    const length2 = dx * dx + dy * dy;
    const t = length2 ? Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / length2)) : 0;
    return Math.hypot(px - ax - t * dx, py - ay - t * dy);
  }

  // Each cell's distance to its nearest seed (8SSEDT: two sweeps, carrying the
  // nearest seed's offset).
  function distances(seeds, cols, rows, cell) {
    const N = cols * rows, FAR = 1e9;
    const ox = new Float32Array(N), oy = new Float32Array(N);
    for (let k = 0; k < N; k++) {
      if (!seeds[k]) { ox[k] = FAR; oy[k] = FAR; }
    }
    const d2 = (k) => ox[k] * ox[k] + oy[k] * oy[k];
    const pull = (k, n, dx, dy) => {
      const x = ox[n] + dx, y = oy[n] + dy;
      if (x * x + y * y < d2(k)) { ox[k] = x; oy[k] = y; }
    };
    for (let j = 0; j < rows; j++) {
      for (let i = 0; i < cols; i++) {
        const k = j * cols + i;
        if (i > 0) pull(k, k - 1, 1, 0);
        if (j > 0) {
          pull(k, k - cols, 0, 1);
          if (i > 0) pull(k, k - cols - 1, 1, 1);
          if (i < cols - 1) pull(k, k - cols + 1, -1, 1);
        }
      }
      for (let i = cols - 2; i >= 0; i--) pull(j * cols + i, j * cols + i + 1, -1, 0);
    }
    for (let j = rows - 1; j >= 0; j--) {
      for (let i = cols - 1; i >= 0; i--) {
        const k = j * cols + i;
        if (i < cols - 1) pull(k, k + 1, -1, 0);
        if (j < rows - 1) {
          pull(k, k + cols, 0, -1);
          if (i < cols - 1) pull(k, k + cols + 1, -1, -1);
          if (i > 0) pull(k, k + cols - 1, 1, -1);
        }
      }
      for (let i = 1; i < cols; i++) pull(j * cols + i, j * cols + i - 1, 1, 0);
    }
    const out = new Float32Array(N);
    for (let k = 0; k < N; k++) out[k] = Math.sqrt(d2(k)) * cell;
    return out;
  }

  // The outline where the field crosses 0 (marching squares between cell
  // centres, interpolated along each edge), stitched into closed rings.
  // Outside the grid counts as land, so every ring closes.
  function trace(field, cols, rows, cx, cy) {
    const at = (i, j) => (i < 0 || j < 0 || i >= cols || j >= rows ? -1 : field[j * cols + i]);
    const segments = [];
    // Edge points, keyed so neighbouring squares share them exactly.
    const point = (i, j, horizontal) => {
      const a = at(i, j), b = horizontal ? at(i + 1, j) : at(i, j + 1);
      const t = a / (a - b);
      const x0 = cx(i), y0 = cy(j);
      const x1 = horizontal ? cx(i + 1) : x0, y1 = horizontal ? y0 : cy(j + 1);
      return { key: `${horizontal ? 'h' : 'v'}${i},${j}`, p: [x0 + (x1 - x0) * t, y0 + (y1 - y0) * t] };
    };
    for (let j = -1; j < rows; j++) {
      for (let i = -1; i < cols; i++) {
        const tl = at(i, j) > 0, tr = at(i + 1, j) > 0, br = at(i + 1, j + 1) > 0, bl = at(i, j + 1) > 0;
        const index = (tl ? 8 : 0) | (tr ? 4 : 0) | (br ? 2 : 0) | (bl ? 1 : 0);
        if (index === 0 || index === 15) continue;
        const top = () => point(i, j, true), bottom = () => point(i, j + 1, true);
        const left = () => point(i, j, false), right = () => point(i + 1, j, false);
        // Ambiguous squares (water at opposite corners): joined through the
        // middle when the square's average is water.
        const middle = (at(i, j) + at(i + 1, j) + at(i + 1, j + 1) + at(i, j + 1)) > 0;
        const cases = {
          1: [[left, bottom]], 2: [[bottom, right]], 3: [[left, right]], 4: [[right, top]],
          5: middle ? [[left, top], [right, bottom]] : [[left, bottom], [right, top]],
          6: [[bottom, top]], 7: [[left, top]], 8: [[top, left]], 9: [[top, bottom]],
          10: middle ? [[top, right], [bottom, left]] : [[top, left], [bottom, right]],
          11: [[top, right]], 12: [[right, left]], 13: [[right, bottom]], 14: [[bottom, left]],
        };
        for (const [from, to] of cases[index]) segments.push([from(), to()]);
      }
    }
    // Stitch: each edge point starts exactly one segment and ends one.
    const next = new Map();
    for (const [a, b] of segments) next.set(a.key, b);
    const used = new Set(), rings = [];
    for (const [a] of segments) {
      if (used.has(a.key)) continue;
      const ring = [];
      let key = a.key, p = a;
      while (!used.has(key) && next.has(key)) {
        used.add(key);
        ring.push(p.p);
        p = next.get(key);
        key = p.key;
      }
      if (ring.length >= 3) rings.push(ring);
    }
    return rings;
  }

  // One round of Chaikin smoothing (a closed ring).
  function chaikin(ring) {
    const out = [];
    for (let k = 0; k < ring.length; k++) {
      const a = ring[k], b = ring[(k + 1) % ring.length];
      out.push([a[0] * 0.75 + b[0] * 0.25, a[1] * 0.75 + b[1] * 0.25]);
      out.push([a[0] * 0.25 + b[0] * 0.75, a[1] * 0.25 + b[1] * 0.75]);
    }
    return out;
  }

  // Drop points that barely change the ring (nearer than `tolerance` to the
  // line between their neighbours), then, if it's still too long, every other
  // point until it fits `max`.
  function thin(ring, tolerance, max) {
    let out = ring.filter((p, k) => {
      const a = ring[(k - 1 + ring.length) % ring.length], b = ring[(k + 1) % ring.length];
      return segmentDistance(p[0], p[1], a[0], a[1], b[0], b[1]) > tolerance * 0.1
        || k % 3 === 0;
    });
    while (out.length > max) out = out.filter((_p, k) => k % 2 === 0);
    return out;
  }

  return { shapes };
})();
