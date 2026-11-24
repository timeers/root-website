// A map layout's summary card: a tile per piece (Clearings, Building Slots,
// Ruins, Forests, then Paths / Special Paths / River Clearings in a row of
// their own) and how the clearing suits are handled.
//
// The one renderer for it: the map builder's review step (live, its tiles
// open their step) and the Forge and Keep map detail pages (from the data
// tts_map.layout_summary / figures_summary make). Data:
//   { tiles: [{kind, label, count, expected?, step?}], path_tiles: [...],
//     suits: {fixed: true, chips: [{suit, name, count, icon}], unset, unset_label}
//          | {fixed, note, icon},   (a note: random suits, or fixed with no tally)
//     marker_svg, colours: {path, special, river} }
// Loaded once per page, however many templates include it.
window.MapSummary = window.MapSummary || (function () {
  'use strict';

  const NS = 'http://www.w3.org/2000/svg';
  const PATH_KINDS = ['path', 'special', 'river'];
  const COLOURS = { path: '#ffffff', special: '#ed2731', river: '#3e55a5' };   // tts_map.PATH_COLOURS
  const PATH_ICON_GREY = '#c4c9ce';   // white paths, as an icon on white

  // ── The clearing marker's outline (tts/make_marker_svg.py) ──
  // Fetched once per page and kept as <symbol id="mb-marker"> in a hidden
  // <svg>, for the map builder's markers and the clearing icon.
  let markerPromise = null;

  function markerReady() {
    return !!document.getElementById('mb-marker');
  }

  function loadMarker(url) {
    if (markerPromise) return markerPromise;
    if (!url) return (markerPromise = Promise.resolve(false));
    markerPromise = fetch(url).then(r => (r.ok ? r.text() : Promise.reject())).then(text => {
      const source = new DOMParser().parseFromString(text, 'image/svg+xml').documentElement;
      if (source.nodeName !== 'svg') return false;
      const holder = document.createElementNS(NS, 'svg');
      holder.setAttribute('aria-hidden', 'true');
      holder.setAttribute('style', 'position: absolute; width: 0; height: 0; overflow: hidden');
      const symbol = document.createElementNS(NS, 'symbol');
      symbol.setAttribute('id', 'mb-marker');
      symbol.setAttribute('viewBox', source.getAttribute('viewBox'));
      symbol.setAttribute('preserveAspectRatio', 'none');
      symbol.setAttribute('overflow', 'visible');
      for (const child of Array.from(source.childNodes)) symbol.appendChild(document.importNode(child, true));
      holder.appendChild(symbol);
      document.body.appendChild(holder);
      return true;
    }).catch(() => false);   // keep the round dots
    return markerPromise;
  }

  // ── Icons, drawn like the board draws each piece ──
  function icon(kind, colours) {
    colours = colours || COLOURS;
    if (kind === 'clearing') {
      if (markerReady()) {
        // Inset in a slightly larger box: the outline's stroke sits half outside
        // the shape and would otherwise be cut off at the edges.
        return '<svg width="40" height="22" viewBox="0 0 40 22" overflow="visible" aria-hidden="true">'
          + '<use href="#mb-marker" x="3" y="3" width="34" height="17" style="color:#fff;--mb-marker-stroke:#231f20"></use></svg>';
      }
      return '<svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">'
        + '<circle cx="9" cy="9" r="7" fill="#fff" stroke="#231f20" stroke-width="1.5"/></svg>';
    }
    if (PATH_KINDS.includes(kind)) {
      const colour = kind === 'path' ? PATH_ICON_GREY : (colours[kind] || COLOURS[kind]);
      if (kind === 'river') {
        // One solid line, as rivers are drawn.
        return '<svg width="30" height="18" viewBox="0 0 30 18" aria-hidden="true">'
          + `<line x1="4" y1="9" x2="26" y2="9" stroke="${colour}" stroke-width="10" stroke-linecap="round"/></svg>`;
      }
      // A little cobblestone path in the kind's colour; white paths in light
      // grey (PATH_ICON_GREY), so they show on white.
      return '<svg width="30" height="18" viewBox="0 0 30 18" aria-hidden="true">'
        + [4.6, 9, 13.4].map((y, i) => `<line x1="${i % 2 ? 6.5 : 4}" y1="${y}" x2="26" y2="${y}" stroke="${colour}"`
          + ' stroke-width="2.8" stroke-linecap="round" stroke-dasharray="3.2 3.4"/>').join('')
        + '</svg>';
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

  // ── The card ──
  // onTile(tile): the tiles are buttons that call it (the builder: open the
  // piece's step) and show "expected → count" pills; without it, plain tiles.
  function render(container, data, options) {
    const onTile = (options || {}).onTile || null;
    container.innerHTML = '';
    const grid = document.createElement('div');
    grid.className = 'mb-sum-grid mb-3';
    container.appendChild(grid);

    const pathTiles = data.path_tiles || [];
    let pathRow = null;
    if (pathTiles.length) {
      // Paths get a row of their own: halves, or thirds with special paths.
      pathRow = document.createElement('div');
      pathRow.className = 'mb-sum-paths';
      pathRow.style.setProperty('--mb-path-tiles', pathTiles.length);
    }
    for (const tile of [...(data.tiles || []), ...pathTiles]) {
      const node = document.createElement(onTile ? 'button' : 'div');
      node.className = 'mb-sum-tile';
      if (onTile) {
        node.type = 'button';
        node.setAttribute('aria-label', `${tile.label}: ${tile.count}. Edit ${tile.label.toLowerCase()}`);
        node.addEventListener('click', () => onTile(tile));
      }
      node.innerHTML = `<div class="mb-sum-icon">${icon(tile.kind, data.colours)}</div>`
        + '<div class="mb-sum-count"></div><div class="mb-sum-label"></div>';
      node.querySelector('.mb-sum-count').textContent = tile.count;
      node.querySelector('.mb-sum-label').textContent = tile.label;
      if (onTile && tile.expected !== null && tile.expected !== undefined && tile.expected !== tile.count) {
        const pill = document.createElement('span');
        pill.className = 'mb-sum-pill';
        pill.textContent = `${tile.expected} → ${tile.count}`;
        pill.title = `The Map currently has ${tile.expected}. Completing the map updates it to ${tile.count}.`;
        node.appendChild(pill);
      }
      (PATH_KINDS.includes(tile.kind) ? pathRow : grid).appendChild(node);
    }
    if (pathRow) grid.appendChild(pathRow);

    // How the clearing suits are handled.
    const suits = document.createElement('div');
    suits.className = 'mb-sum-suits';
    container.appendChild(suits);
    const s = data.suits || {};
    if (s.chips) {
      for (const chip of s.chips) {
        const node = document.createElement('span');
        node.className = 'mb-suit-chip';
        node.title = chip.name;
        node.innerHTML = '<img alt=""><span></span>';
        node.querySelector('img').src = chip.icon;
        node.querySelector('span').textContent = `${chip.name} ${chip.count}`;
        suits.appendChild(node);
      }
      if (s.unset) {
        const node = document.createElement('span');
        node.className = 'mb-suit-chip mb-suit-chip-unset';
        node.textContent = `${s.unset_label} ${s.unset}`;
        suits.appendChild(node);
      }
    } else if (s.note) {   // random suits, or fixed with no tally (a Map without a layout)
      const node = document.createElement('span');
      node.className = 'mb-suit-note';
      node.innerHTML = '<img alt=""><span></span>';
      if (s.icon) node.querySelector('img').src = s.icon; else node.querySelector('img').remove();
      node.querySelector('span').textContent = s.note;
      suits.appendChild(node);
    }
  }

  // A page's card from its data (json_script): draw it, and again once the
  // marker outline arrives (the clearing icon).
  function mount(container, data) {
    render(container, data);
    loadMarker(data.marker_svg).then(ok => { if (ok) render(container, data); });
  }

  return { icon, loadMarker, markerReady, render, mount };
})();
