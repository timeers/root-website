// Lakes (the map builder's Paths step): where the water goes.
//
// A lake is drawn from clearing to clearing like a river, but printed as an
// area of water. It fills the space around it, stopping halfway to the land:
// every point of the board goes to whichever it's nearer, the water (the
// lakes' lines) or the land (other clearings, paths, special paths, rivers,
// forest markers). So lakes with nothing between them merge into one. A lake
// overlaps only the edge of its own clearings (their centres stay dry, and so
// do the buildings there), meeting each with a broad rounded contact, or,
// where a river ends at that clearing, narrowing to the river's width so the
// river seems to widen into it.
//
// A lake body (clearings joined by lake paths) is a sea when all its lines lie
// outside the map (nothing between them and the board's edge): the stretch of
// the board's edge nearest its clearings is water too, ending halfway to the
// next edge clearings. A ring of them round the map is an island's coast.
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
  //   clearings: [{x, y, r, lake, feeds}], lake: on a lake; feeds: rivers ending there (below)
  //   lakes: [{points, a, b}],          each lake path's line (clearing centre to centre) and its clearings
  //   lines: [{points, half}],          every other path, special path and river (land)
  //   forests: [{x, y, r}],
  //   overlap,                          a lake overlaps this share of its clearings' radius (their edge)
  //   smooth,                           spikes and points narrower than twice this are rounded off (pixels)
  //   minHalf,                          a lake is never narrower than twice this along its line (pixels)
  //   round,                            corners are rounded over about this distance (pixels)
  //   crossReach,                       a path an inland lake's line crosses isn't land this near the crossing (pixels)
  //   touch, lens, funnel, variation:   an inland lake's shape (shares of its clearings' radius / gap)
  //   riverHalf, linkReach,             a river's half-width (pixels), and how far into a clearing its
  //                                     join curve reaches (share of the radius, river_extras' RIVER_LINK_REACH)
  //   clearings[].feeds:                [[x, y, dx, dy], ...]: rivers ending at that clearing (their end on its
  //                                     rim, flowing in): an inland lake curves out to each, as a river widening
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
    function line(mark, points, half, skip = null) {
      const reach = Math.max(half, cell * 0.75);     // at least a cell thick, so nothing leaks across
      const keep = skip ? ((k) => { if (!skip(k)) mark(k); }) : mark;
      for (let k = 1; k < points.length; k++) {
        const [ax, ay] = points[k - 1], [bx, by] = points[k];
        const i0 = Math.max(0, Math.floor((Math.min(ax, bx) - reach) / cell + 0.5)), i1 = Math.min(cols - 1, Math.ceil((Math.max(ax, bx) + reach) / cell + 0.5));
        const j0 = Math.max(0, Math.floor((Math.min(ay, by) - reach) / cell + 0.5)), j1 = Math.min(rows - 1, Math.ceil((Math.max(ay, by) + reach) / cell + 0.5));
        for (let j = j0; j <= j1; j++) {
          for (let i = i0; i <= i1; i++) {
            if (segmentDistance(cx(i), cy(j), ax, ay, bx, by) <= reach) keep(j * cols + i);
          }
        }
      }
    }

    // ── The outside of the map ──
    // Flooded in from the border, 4-connected, through every cell not on a
    // wall, twice:
    // - `beyond`: walls are the clearings and land lines (paths, special
    //   paths, rivers), not lakes. A lake line lying out here, with nothing
    //   between it and the board's edge, is what makes a lake a sea.
    // - `outer`: lake lines are walls too: the map's outside proper, past its
    //   edge clearings and lakes (edge clearings, which of them own which
    //   stretch of the board's edge, and which way is out).
    const wall = new Uint8Array(N), lakeWall = new Uint8Array(N);
    const setWall = (k) => { wall[k] = 1; lakeWall[k] = 1; };
    input.clearings.forEach(c => disc(setWall, c.x, c.y, c.r));
    for (const l of input.lines) line(setWall, l.points, l.half);
    for (const l of lakes) line((k) => { lakeWall[k] = 1; }, l.points, 0);
    const queue = new Int32Array(N);
    let head = 0, tail = 0;
    const step = (k, fn) => {
      const i = k % cols, j = (k - i) / cols;
      if (i > 0) fn(k - 1); if (i < cols - 1) fn(k + 1);
      if (j > 0) fn(k - cols); if (j < rows - 1) fn(k + cols);
    };
    const flood = (walls) => {
      const reached = new Uint8Array(N);
      head = tail = 0;
      for (let j = 0; j < rows; j++) {
        for (let i = 0; i < cols; i++) {
          if (border(i, j)) { const k = j * cols + i; reached[k] = 1; queue[tail++] = k; }
        }
      }
      while (head < tail) {
        step(queue[head++], (n) => { if (!reached[n] && !walls[n]) { reached[n] = 1; queue[tail++] = n; } });
      }
      return reached;
    };
    const beyond = flood(wall), outer = flood(lakeWall);
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
    // A lake line is outside the map when (nearly all of) it lies in the
    // outside area: nothing between it and the board's edge. A body is a sea
    // when all its lines are; otherwise it's an inland lake, wherever its
    // clearings are.
    const cellAt = (x, y) => {
      const i = Math.round(x / cell + 0.5), j = Math.round(y / cell + 0.5);
      return i < 0 || j < 0 || i >= cols || j >= rows ? -1 : j * cols + i;
    };
    function outside(l) {
      const ca = input.clearings[l.a], cb = input.clearings[l.b];
      let inside = 0, out = 0;
      for (const [x, y] of resample(l.points, cell)) {
        if (Math.hypot(x - ca.x, y - ca.y) < ca.r + cell || Math.hypot(x - cb.x, y - cb.y) < cb.r + cell) continue;
        const k = cellAt(x, y);
        if (k < 0 || wall[k]) continue;
        if (beyond[k]) out++; else inside++;
      }
      if (out + inside < 3) {        // too short to tell: its middle decides
        const [x, y] = pointAlong(l.points, 0.5), k = cellAt(x, y);
        return k >= 0 && !!beyond[k];
      }
      return out >= 0.8 * (out + inside);
    }
    const oceans = [];
    for (const [root, members] of bodies) {
      const lines = lakes.filter(l => find(l.a) === root);
      if (lines.every(outside)) oceans.push(...members);
    }
    const oceanClearing = new Set(oceans);

    // ── Island coasts ──
    // A sea whose lakes form a ring (or a single chain) gets a coast: a
    // smooth curve through each of its clearings' outermost point, so the sea
    // only touches each clearing there and the land reads as an island.
    const overlap = input.overlap === undefined ? 0.25 : input.overlap;
    const inner = (c) => c.r * (1 - overlap);
    const coasts = [];              // [{points, outward}] (outward: per point, which side is sea)
    const coastBody = new Set();    // bodies drawn with a coast (their lake lines aren't water)
    const ownedSum = new Map();     // clearing -> [sum x, sum y, n] of its outer cells
    for (let k = 0; k < N; k++) {
      if (owner[k] < 0) continue;
      const i = k % cols, j = (k - i) / cols, s = ownedSum.get(owner[k]) || [0, 0, 0];
      s[0] += cx(i); s[1] += cy(j); s[2]++;
      ownedSum.set(owner[k], s);
    }
    for (const [root, members] of bodies) {
      if (!oceanClearing.has(root)) continue;
      const order = ringOrChain(members, lakes.filter(l => find(l.a) === root));
      if (!order) continue;             // branching: no single coast (its lake lines stay water)
      if (order.closed) {
        // A ring is an island's coast only when it goes round the rest of the
        // map (every other clearing inside it). A ring off to one side (a bay
        // in a corner) is just sea.
        const ring = order.points.map(k => [input.clearings[k].x, input.clearings[k].y]);
        const others = input.clearings.filter((c, k) => !members.includes(k));
        if (!others.every(c => insideRing([c.x, c.y], ring))) continue;
      }
      const outwardOf = (k) => {
        const c = input.clearings[k];
        // Away from where its lake lines arrive...
        let sx = 0, sy = 0, arriving = 0, first = null;
        for (const l of lakes) {
          if (l.a !== k && l.b !== k) continue;
          const pts = l.a === k ? l.points : [...l.points].reverse();
          const [px, py] = pointOnCircle(pts, [c.x, c.y], c.r);
          const len = Math.hypot(px - c.x, py - c.y) || 1;
          sx += (px - c.x) / len; sy += (py - c.y) / len;
          if (!first) first = [(px - c.x) / len, (py - c.y) / len];
          arriving++;
        }
        let ox = -sx, oy = -sy;
        // ...towards the outside of the map it owns (also when its lines
        // arrive from opposite sides, and the first guess is no guess).
        const own = ownedSum.get(k);
        const tx = own ? own[0] / own[2] - c.x : 0, ty = own ? own[1] / own[2] - c.y : 0;
        if (arriving === 1 || Math.hypot(ox, oy) < 0.3) {
          // At a chain's end (one lake line) or with lines arriving from
          // opposite sides: across its line, on the side of the outside.
          [ox, oy] = first ? [first[1], -first[0]] : [tx, ty];
        }
        if (ox * tx + oy * ty < 0) { ox = -ox; oy = -oy; }
        const len = Math.hypot(ox, oy) || 1;
        return [ox / len, oy / len];
      };
      const outs = order.points.map(outwardOf);
      const P = order.points.map((k, n) => {
        const c = input.clearings[k];
        return [c.x + outs[n][0] * inner(c), c.y + outs[n][1] * inner(c)];
      });
      coasts.push(catmullRom(P, outs, order.closed, cell * 0.5,
                             order.closed ? 0 : input.clearings[order.points[0]].r,
                             order.closed ? 0 : input.clearings[order.points[order.points.length - 1]].r));
      coastBody.add(root);
    }

    // ── Seeds: water and land ──
    // A lake overlaps only the edge of its clearings: inside that (the inner
    // disc) stays dry, and the lake's line starts at the inner disc's edge.
    // A sea with a coast takes its water from the coast, not its lake lines.
    const water = new Uint8Array(N), land = new Uint8Array(N), lakeLine = new Uint8Array(N);
    const setWater = (k) => { water[k] = 1; lakeLine[k] = 1; }, setLand = (k) => { land[k] = 1; };
    // An inland lake isn't grown from its line: it's a smooth shape through
    // its clearings (below). Only a coastless sea's lines are water here.
    const inlandLines = [];
    for (const l of lakes) {
      if (coastBody.has(find(l.a))) continue;
      if (!oceanClearing.has(l.a)) { inlandLines.push(l); continue; }
      const ca = input.clearings[l.a], cb = input.clearings[l.b];
      line(setWater, trimEnds(l.points, [ca.x, ca.y], inner(ca), [cb.x, cb.y], inner(cb)), cell * 0.5);
    }
    // The coast: water just outside it, land just inside, so the shore lies on it.
    for (const coast of coasts) {
      coast.points.forEach(([x, y], n) => {
        const [nx, ny] = coast.outward[n];
        disc((k) => { water[k] = 1; }, x + nx * cell, y + ny * cell, cell * 0.5);
        disc(setLand, x - nx * cell, y - ny * cell, cell * 0.5);
      });
    }
    // A path (or river) an inland lake's line is forced across isn't land
    // near the crossing: the lake runs on under it.
    const crossReach = input.crossReach || 0;
    const crossingsOf = (l) => crossReach ? inlandLines.flatMap(lake => crossings(l.points, lake.points)) : [];
    const lineSkips = input.lines.map(l => {
      const at = crossingsOf(l);
      return at.length ? ((k) => { const i = k % cols, j = (k - i) / cols;
                                    return at.some(([x, y]) => Math.hypot(cx(i) - x, cy(j) - y) < crossReach); }) : null;
    });
    input.clearings.forEach((c, k) => { if (!c.lake || !oceanClearing.has(k)) disc(setLand, c.x, c.y, c.r); });
    input.lines.forEach((l, n) => line(setLand, l.points, l.half, lineSkips[n]));
    for (const f of input.forests) disc(setLand, f.x, f.y, f.r);
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
    // Always dry, whatever's nearer: the inner discs of lake clearings (and
    // with them, their buildings), every other clearing, and every path,
    // special path and river line.
    const dry = new Uint8Array(N);
    const setDry = (k) => { dry[k] = 1; };
    input.clearings.forEach(c => disc(setDry, c.x, c.y, c.lake ? inner(c) : c.r));
    input.lines.forEach((l, n) => line(setDry, l.points, l.half, lineSkips[n]));
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
    // A little variation first: smooth low-frequency noise, so a lake's shore
    // bulges and dips gently (the same layout always gives the same lake).
    const meanR = input.clearings.reduce((sum, c) => sum + c.r, 0) / Math.max(1, input.clearings.length);
    const vary = (input.variation || 0) * meanR, wave = 2 * meanR;
    let open = new Float32Array(N);
    for (let k = 0; k < N; k++) {
      const i = k % cols, j = (k - i) / cols;
      open[k] = smooth - dCore[k] + (vary ? vary * valueNoise(cx(i) / wave, cy(j) / wave) : 0);
    }
    const passes = 3, radius = Math.round((input.round || 0) / cell / Math.sqrt(passes));
    for (let p = 0; p < passes && radius > 0; p++) open = boxBlur(open, cols, rows, radius);
    // An island's coast is already a smooth curve, so near it the shore is
    // the curve itself: the exact signed distance to it (+ out to sea), so the
    // sea reaches it all along (rounding would pull the sea back from round
    // the clearings it touches) and the shore is as smooth as the curve.
    const coastField = new Float32Array(N).fill(-Infinity);
    const near = cell * 3;
    for (const coast of coasts) {
      const pts = coast.points;
      for (let n = 1; n < pts.length; n++) {
        const [ax, ay] = pts[n - 1], [bx, by] = pts[n], [ox, oy] = coast.outward[n];
        const i0 = Math.max(0, Math.floor((Math.min(ax, bx) - near) / cell + 0.5)), i1 = Math.min(cols - 1, Math.ceil((Math.max(ax, bx) + near) / cell + 0.5));
        const j0 = Math.max(0, Math.floor((Math.min(ay, by) - near) / cell + 0.5)), j1 = Math.min(rows - 1, Math.ceil((Math.max(ay, by) + near) / cell + 0.5));
        for (let j = j0; j <= j1; j++) {
          for (let i = i0; i <= i1; i++) {
            const x = cx(i), y = cy(j), d = segmentDistance(x, y, ax, ay, bx, by);
            if (d > near) continue;
            const k = j * cols + i, side = (x - ax) * ox + (y - ay) * oy >= 0 ? 1 : -1;
            // The nearest segment decides (its sign included).
            if (coastField[k] === -Infinity || d < Math.abs(coastField[k])) coastField[k] = side * d;
          }
        }
      }
    }
    const fillet = Math.max((input.round || 0) * 0.5, cell);
    const field = new Float32Array(N);       // > 0: water (a smooth signed distance, for tracing)
    for (let k = 0; k < N; k++) field[k] = smax(open[k], minHalf - dLine[k], fillet);
    const around = (x, y, reach, fn) => {
      const i0 = Math.max(0, Math.floor((x - reach) / cell + 0.5)), i1 = Math.min(cols - 1, Math.ceil((x + reach) / cell + 0.5));
      const j0 = Math.max(0, Math.floor((y - reach) / cell + 0.5)), j1 = Math.min(rows - 1, Math.ceil((y + reach) / cell + 0.5));
      for (let j = j0; j <= j1; j++) for (let i = i0; i <= i1; i++) fn(j * cols + i, cx(i), cy(j));
    };

    // ── Inland lakes ──
    // A smooth shape inside the clearings that make the lake, just touching
    // each at the point facing the middle of the lake (as a coast touches its
    // clearings): a closed curve through those points round the lake's
    // clearings, or for two clearings (or ones nearly in a line) a lens along
    // each lake line. Where a river ends at one of its clearings, the water
    // runs on into the clearing, narrowing to the river's width by its middle.
    const touch = input.touch || 0.92, riverHalf = input.riverHalf || minHalf;
    const funnelHalf = (input.funnel || 0.35);
    const inlandField = new Float32Array(N).fill(-Infinity);
    const funnels = [];                     // {c, axis}: a river's way into the lake through clearing c
    const seen = new Set();
    for (const [root, members] of bodies) {
      if (oceanClearing.has(root)) continue;
      const cs = members.map(k => input.clearings[k]);
      const meanR = cs.reduce((sum, c) => sum + c.r, 0) / cs.length;
      const mid = [cs.reduce((sum, c) => sum + c.x, 0) / cs.length, cs.reduce((sum, c) => sum + c.y, 0) / cs.length];
      const hull = convexHull(members.map(k => [input.clearings[k].x, input.clearings[k].y, k]));
      const curves = [];                    // {P, outs}
      const axisOf = new Map();             // clearing -> the way into the lake from it
      if (hull.length >= 3 && Math.abs(polygonArea(hull)) >= 2 * meanR * meanR) {
        const P = [], outs = [], along = [];
        for (const [x, y, k] of hull) {
          const c = input.clearings[k], len = Math.hypot(mid[0] - x, mid[1] - y) || 1;
          const dir = [(mid[0] - x) / len, (mid[1] - y) / len];
          P.push([x + dir[0] * c.r * touch, y + dir[1] * c.r * touch]);
          outs.push([-dir[0], -dir[1]]);
          along.push([-dir[1], dir[0]]);      // along the clearing's circle there
          axisOf.set(k, dir);
        }
        curves.push({ P, outs, along });
      } else {
        for (const l of lakes) {
          if (find(l.a) !== root) continue;
          const ca = input.clearings[l.a], cb = input.clearings[l.b];
          const leave = (c, pts) => {
            const p = pointOnCircle(pts, [c.x, c.y], c.r), len = Math.hypot(p[0] - c.x, p[1] - c.y) || 1;
            return [(p[0] - c.x) / len, (p[1] - c.y) / len];
          };
          const da = leave(ca, l.points), db = leave(cb, [...l.points].reverse());
          const Pa = [ca.x + da[0] * ca.r * touch, ca.y + da[1] * ca.r * touch];
          const Pb = [cb.x + db[0] * cb.r * touch, cb.y + db[1] * cb.r * touch];
          const m = pointAlong(l.points, 0.5), len = Math.hypot(Pb[0] - Pa[0], Pb[1] - Pa[1]) || 1;
          const n = [-(Pb[1] - Pa[1]) / len, (Pb[0] - Pa[0]) / len];
          const gap = Math.hypot(cb.x - ca.x, cb.y - ca.y) - ca.r - cb.r;
          const w = (input.lens || 0.3) * Math.max(gap, meanR);
          curves.push({ P: [Pa, [m[0] + n[0] * w, m[1] + n[1] * w], Pb, [m[0] - n[0] * w, m[1] - n[1] * w]],
                        outs: [[-da[0], -da[1]], n, [-db[0], -db[1]], [-n[0], -n[1]]],
                        along: [[-da[1], da[0]], null, [-db[1], db[0]], null] });
          if (!axisOf.has(l.a)) axisOf.set(l.a, da);
          if (!axisOf.has(l.b)) axisOf.set(l.b, db);
        }
      }
      // Each curve: gently varied (not at the touch points), then filled.
      const vary = (input.variation || 0) * meanR, wave = 2 * meanR;
      for (const { P, outs, along } of curves) {
        const curve = catmullRom(P, outs, true, cell * 0.5, 0, 0, along);
        let run = 0;
        const pts = curve.points.map((p, n) => {
          if (n) run += Math.hypot(p[0] - curve.points[n - 1][0], p[1] - curve.points[n - 1][1]);
          const near = Math.min(...P.map(q => Math.hypot(p[0] - q[0], p[1] - q[1])));
          const fade = Math.min(1, near / (0.5 * meanR));
          const push = vary * valueNoise(run / wave, root * 7.31) * fade * fade * (3 - 2 * fade);
          return [p[0] + curve.outward[n][0] * push, p[1] + curve.outward[n][1] * push];
        });
        fillCurve(pts);
      }
      for (const [k, axis] of axisOf) {
        const c = input.clearings[k];
        if (seen.has(k) || !Array.isArray(c.feeds)) continue;
        seen.add(k);
        for (const [ax, ay, dx, dy] of c.feeds) funnels.push({ c, axis, A: [ax, ay], dA: [dx, dy] });
      }
    }
    function fillCurve(pts) {
      // Inside the curve: + the distance to it; outside: −.
      let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      for (const [x, y] of pts) { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); }
      const pad = cell * 3;
      const i0 = Math.max(0, Math.floor((x0 - pad) / cell + 0.5)), i1 = Math.min(cols - 1, Math.ceil((x1 + pad) / cell + 0.5));
      const j0 = Math.max(0, Math.floor((y0 - pad) / cell + 0.5)), j1 = Math.min(rows - 1, Math.ceil((y1 + pad) / cell + 0.5));
      for (let j = j0; j <= j1; j++) {
        for (let i = i0; i <= i1; i++) {
          const x = cx(i), y = cy(j);
          let d = Infinity, inside = false;
          for (let q = 0, r = pts.length - 1; q < pts.length; r = q++) {
            const [ax, ay] = pts[q], [bx, by] = pts[r];
            d = Math.min(d, segmentDistance(x, y, ax, ay, bx, by));
            if ((ay > y) !== (by > y) && x < ax + (y - ay) * (bx - ax) / (by - ay)) inside = !inside;
          }
          const k = j * cols + i;
          inlandField[k] = Math.max(inlandField[k], inside ? d : -d);
        }
      }
    }
    // A river's way into the lake through clearing c: the curve two rivers
    // take through a clearing (river_extras' join: A, A + dA·k, B − axis·k,
    // B), from the river's end A on the rim to B where the lake meets the
    // clearing, widening from the river's width to the lake's as it goes.
    const linkReach = input.linkReach || 0.6;
    const funnelField = new Float32Array(N).fill(-Infinity);
    for (const f of funnels) {
      const { c, axis, A, dA } = f, k = c.r * linkReach;
      const B = [c.x + axis[0] * c.r * 0.95, c.y + axis[1] * c.r * 0.95];
      const p1 = [A[0] + dA[0] * k, A[1] + dA[1] * k], p2 = [B[0] - axis[0] * k, B[1] - axis[1] * k];
      const samples = [];
      for (let n = 0; n <= 32; n++) {
        const t = n / 32, u = 1 - t;
        samples.push([u * u * u * A[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * B[0],
                      u * u * u * A[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * B[1]]);
      }
      const wide = funnelHalf * c.r;
      around(c.x, c.y, c.r * 1.3, (cellK, x, y) => {
        let best = Infinity, bestT = 0;
        for (let n = 1; n < samples.length; n++) {
          const [ax, ay] = samples[n - 1], [bx, by] = samples[n];
          const dx = bx - ax, dy = by - ay, len2 = dx * dx + dy * dy || 1;
          const q = Math.max(0, Math.min(1, ((x - ax) * dx + (y - ay) * dy) / len2));
          const d = Math.hypot(x - ax - dx * q, y - ay - dy * q);
          if (d < best) { best = d; bestT = (n - 1 + q) / 32; }
        }
        const half = riverHalf + (wide - riverHalf) * bestT * bestT;     // widening mostly near the lake
        funnelField[cellK] = Math.max(funnelField[cellK], half - best);
      });
    }
    const inlandFillet = Math.max(cell, 0.25 * (input.clearings.reduce((sum, c) => sum + c.r, 0) / Math.max(1, input.clearings.length)));
    for (let k = 0; k < N; k++) {
      const lake = funnelField[k] === -Infinity ? inlandField[k] : smax(inlandField[k], funnelField[k], inlandFillet);
      if (lake > field[k]) field[k] = lake;
    }
    // What's always dry, as an exact signed distance (negative inside), so
    // the shore follows a clearing's circle or a path's edge exactly instead
    // of the grid's cells.
    const dryField = new Float32Array(N).fill(Infinity);
    const reachOut = cell * 2;
    input.clearings.forEach(c => {
      const R = c.lake ? inner(c) : c.r;
      // (A river's funnel is the one water allowed inside a clearing.)
      around(c.x, c.y, R + reachOut, (k, x, y) => {
        dryField[k] = Math.min(dryField[k], Math.max(Math.hypot(x - c.x, y - c.y) - R, funnelField[k]));
      });
    });
    input.lines.forEach((l, n) => {
      const skip = lineSkips[n];
      for (let q = 1; q < l.points.length; q++) {
        const [ax, ay] = l.points[q - 1], [bx, by] = l.points[q];
        const reach = l.half + reachOut;
        const i0 = Math.max(0, Math.floor((Math.min(ax, bx) - reach) / cell + 0.5)), i1 = Math.min(cols - 1, Math.ceil((Math.max(ax, bx) + reach) / cell + 0.5));
        const j0 = Math.max(0, Math.floor((Math.min(ay, by) - reach) / cell + 0.5)), j1 = Math.min(rows - 1, Math.ceil((Math.max(ay, by) + reach) / cell + 0.5));
        for (let j = j0; j <= j1; j++) {
          for (let i = i0; i <= i1; i++) {
            const k = j * cols + i;
            if (skip && skip(k)) continue;
            dryField[k] = Math.min(dryField[k], segmentDistance(cx(i), cy(j), ax, ay, bx, by) - l.half);
          }
        }
      }
    });
    for (let k = 0; k < N; k++) {
      if (coastField[k] !== -Infinity) field[k] = coastField[k] > 0 ? Math.max(field[k], coastField[k]) : Math.min(field[k], coastField[k]);
      field[k] = Math.min(field[k], dryField[k]);
      if (dry[k] && !(funnelField[k] > 0)) field[k] = Math.min(field[k], 0);
      // A river's funnel wins over every dry rule (its own river's line too,
      // or it would stop short of the river it carries on).
      if (funnelField[k] > field[k]) field[k] = funnelField[k];
    }

    // (Specks a few cells across, where the exact dry edges meet the grid,
    // aren't water anyone drew: dropped.)
    const area = (r) => Math.abs(r.reduce((sum, p, k) => { const q = r[(k + 1) % r.length]; return sum + p[0] * q[1] - q[0] * p[1]; }, 0)) / 2;
    const rings = trace(field, cols, rows, cx, cy).map(r => thin(chaikin(chaikin(r)), cell * 0.3, 400))
      .filter(r => r.length >= 3 && area(r) > cell * cell * 4);
    return { rings, oceans };
  }

  // The convex hull of [x, y, ...] points (monotone chain), anticlockwise.
  function convexHull(points) {
    const ps = [...points].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    if (ps.length < 3) return ps;
    const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
    const lower = [], upper = [];
    for (const p of ps) { while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) lower.pop(); lower.push(p); }
    for (const p of [...ps].reverse()) { while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) upper.pop(); upper.push(p); }
    return lower.slice(0, -1).concat(upper.slice(0, -1));
  }

  function polygonArea(points) {
    let a = 0;
    for (let k = 0; k < points.length; k++) {
      const p = points[k], q = points[(k + 1) % points.length];
      a += p[0] * q[1] - q[0] * p[1];
    }
    return a / 2;
  }

  // Smooth maximum / minimum: a rounded fillet about k across where two
  // shapes meet, instead of a corner.
  function smax(a, b, k) {
    const h = Math.max(k - Math.abs(a - b), 0) / k;
    return Math.max(a, b) + h * h * k * 0.25;
  }
  function smin(a, b, k) {
    return -smax(-a, -b, k);
  }

  // Smooth value noise in about -1..1: hashed values on a lattice, eased
  // between. Repeatable: the same point always gives the same value.
  function valueNoise(x, y) {
    const hash = (i, j) => {
      let h = (i * 374761393 + j * 668265263) | 0;
      h = Math.imul(h ^ (h >>> 13), 1274126177);
      return ((h ^ (h >>> 16)) >>> 0) / 4294967295 * 2 - 1;
    };
    const i = Math.floor(x), j = Math.floor(y), fx = x - i, fy = y - j;
    const ease = (t) => t * t * (3 - 2 * t);
    const u = ease(fx), v = ease(fy);
    const a = hash(i, j), b = hash(i + 1, j), c = hash(i, j + 1), d = hash(i + 1, j + 1);
    return a + (b - a) * u + (c - a) * v + (a - b - c + d) * u * v;
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

  // Is p inside the polygon (even-odd)?
  function insideRing(p, ring) {
    let inside = false;
    for (let k = 0, j = ring.length - 1; k < ring.length; j = k++) {
      const [ax, ay] = ring[k], [bx, by] = ring[j];
      if ((ay > p[1]) !== (by > p[1]) && p[0] < ax + (p[1] - ay) * (bx - ax) / (by - ay)) inside = !inside;
    }
    return inside;
  }

  // Points along a polyline about `step` apart.
  function resample(points, step) {
    const out = [points[0]];
    for (let k = 1; k < points.length; k++) {
      const [ax, ay] = points[k - 1], [bx, by] = points[k];
      const n = Math.max(1, Math.ceil(Math.hypot(bx - ax, by - ay) / step));
      for (let i = 1; i <= n; i++) out.push([ax + (bx - ax) * i / n, ay + (by - ay) * i / n]);
    }
    return out;
  }

  // The point a share `t` of the way along a polyline, by length.
  function pointAlong(points, t) {
    let total = 0;
    for (let k = 1; k < points.length; k++) total += Math.hypot(points[k][0] - points[k - 1][0], points[k][1] - points[k - 1][1]);
    let left = total * t;
    for (let k = 1; k < points.length; k++) {
      const [ax, ay] = points[k - 1], [bx, by] = points[k];
      const len = Math.hypot(bx - ax, by - ay);
      if (left <= len && len) return [ax + (bx - ax) * left / len, ay + (by - ay) * left / len];
      left -= len;
    }
    return points[points.length - 1];
  }

  // Where a polyline (starting inside the circle) first leaves it.
  function pointOnCircle(points, centre, r) {
    for (const p of resample(points, r / 8)) {
      if (Math.hypot(p[0] - centre[0], p[1] - centre[1]) >= r) return p;
    }
    return points[points.length - 1];
  }

  // Where two polylines cross.
  function crossings(a, b) {
    const out = [];
    for (let i = 1; i < a.length; i++) {
      for (let j = 1; j < b.length; j++) {
        const p = segmentCross(a[i - 1], a[i], b[j - 1], b[j]);
        if (p) out.push(p);
      }
    }
    return out;
  }

  function segmentCross(p, q, r, s) {
    const d = (q[0] - p[0]) * (s[1] - r[1]) - (q[1] - p[1]) * (s[0] - r[0]);
    if (!d) return null;
    const t = ((r[0] - p[0]) * (s[1] - r[1]) - (r[1] - p[1]) * (s[0] - r[0])) / d;
    const u = ((r[0] - p[0]) * (q[1] - p[1]) - (r[1] - p[1]) * (q[0] - p[0])) / d;
    return t >= 0 && t <= 1 && u >= 0 && u <= 1 ? [p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t] : null;
  }

  // The order a body's clearings are joined in: { points, closed } for a
  // ring (each clearing has two lake neighbours) or a chain (two ends with
  // one), else null.
  function ringOrChain(members, lines) {
    const next = new Map(members.map(k => [k, []]));
    for (const l of lines) { next.get(l.a).push(l.b); next.get(l.b).push(l.a); }
    const degrees = members.map(k => next.get(k).length);
    if (degrees.some(d => d < 1 || d > 2)) return null;
    const ends = members.filter(k => next.get(k).length === 1);
    if (ends.length !== 0 && ends.length !== 2) return null;
    const closed = ends.length === 0;
    if (closed && members.length < 3) return null;
    const order = [closed ? members[0] : ends[0]];
    let previous = null;
    while (order.length < members.length) {
      const here = order[order.length - 1];
      const step = next.get(here).find(k => k !== previous && !order.includes(k));
      if (step === undefined) return null;
      previous = here;
      order.push(step);
    }
    return { points: order, closed };
  }

  // A Catmull-Rom curve through points P (closed, or open with a run-on past
  // each end), sampled about `step` apart, with which side is outward at each
  // sample (from the points' own outward directions).
  // `along` (optional): per point, a direction the curve must run along there
  // (an inland lake runs along each clearing's circle where it touches it);
  // the curve keeps its Catmull-Rom speed, just turned to that direction.
  function catmullRom(P, outs, closed, step, runStart, runEnd, along = null) {
    const n = P.length;
    const at = (i) => (closed ? P[(i + n) % n] : P[Math.max(0, Math.min(n - 1, i))]);
    const out = (i) => (closed ? outs[(i + n) % n] : outs[Math.max(0, Math.min(n - 1, i))]);
    const tangent = (i) => {
      const a = at(i - 1), b = at(i + 1);
      const m = [(b[0] - a[0]) / 2, (b[1] - a[1]) / 2];
      const dir = along && along[closed ? (i + n) % n : Math.max(0, Math.min(n - 1, i))];
      if (!dir) return m;
      const speed = Math.hypot(m[0], m[1]), sign = dir[0] * m[0] + dir[1] * m[1] >= 0 ? 1 : -1;
      return [dir[0] * speed * sign, dir[1] * speed * sign];
    };
    const points = [], outward = [];
    const segments = closed ? n : n - 1;
    for (let i = 0; i < segments; i++) {
      const p1 = at(i), p2 = at(i + 1), m1 = tangent(i), m2 = tangent(i + 1);
      const count = Math.max(2, Math.ceil(Math.hypot(p2[0] - p1[0], p2[1] - p1[1]) / step));
      for (let s = 0; s < count; s++) {
        const t = s / count, t2 = t * t, t3 = t2 * t;
        // Cubic Hermite (with Catmull-Rom's tangents this is Catmull-Rom).
        const h00 = 2 * t3 - 3 * t2 + 1, h10 = t3 - 2 * t2 + t, h01 = -2 * t3 + 3 * t2, h11 = t3 - t2;
        points.push([h00 * p1[0] + h10 * m1[0] + h01 * p2[0] + h11 * m2[0],
                     h00 * p1[1] + h10 * m1[1] + h01 * p2[1] + h11 * m2[1]]);
        const o1 = out(i), o2 = out(i + 1);
        outward.push([o1[0] * (1 - t) + o2[0] * t, o1[1] * (1 - t) + o2[1] * t]);
      }
    }
    if (!closed) { points.push(P[n - 1]); outward.push(outs[n - 1]); }
    // Each sample's normal, turned to the outward side.
    const normals = points.map((p, k) => {
      const a = points[Math.max(0, k - 1)], b = points[Math.min(points.length - 1, k + 1)];
      const len = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
      let nx = -(b[1] - a[1]) / len, ny = (b[0] - a[0]) / len;
      if (nx * outward[k][0] + ny * outward[k][1] < 0) { nx = -nx; ny = -ny; }
      return [nx, ny];
    });
    if (!closed) {
      // A short run-on past each end, so the coast doesn't stop abruptly.
      const run = (from, towards, length, normal) => {
        const dx = from[0] - towards[0], dy = from[1] - towards[1], len = Math.hypot(dx, dy) || 1;
        const extra = [];
        for (let d = step; d <= length; d += step) extra.push([from[0] + dx / len * d, from[1] + dy / len * d]);
        return extra.map(p => [p, normal]);
      };
      const head = run(points[0], points[1], runStart, normals[0]).reverse();
      const tail = run(points[points.length - 1], points[points.length - 2], runEnd, normals[normals.length - 1]);
      return { points: [...head.map(e => e[0]), ...points, ...tail.map(e => e[0])],
               outward: [...head.map(e => e[1]), ...normals, ...tail.map(e => e[1])] };
    }
    return { points, outward: normals };
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
