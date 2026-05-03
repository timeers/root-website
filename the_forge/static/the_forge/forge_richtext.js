(function () {
  'use strict';

  // ----- Inline image map (key -> URL), emitted by editor templates as
  // <script type="application/json" id="forge-inline-images">…</script> -----
  let INLINE_IMAGES = null;
  function getInlineImages() {
    if (INLINE_IMAGES !== null) return INLINE_IMAGES;
    const node = document.getElementById('forge-inline-images');
    if (node) {
      try { INLINE_IMAGES = JSON.parse(node.textContent); }
      catch (e) { INLINE_IMAGES = {}; }
    } else {
      INLINE_IMAGES = {};
    }
    return INLINE_IMAGES;
  }

  // Drop the cached map. Called from forge_editor.js after a CustomInlineImage
  // mutation invalidates the per-sheet overlay. Next call to getInlineImages
  // re-reads from the (also updated) <script id="forge-inline-images"> tag.
  function invalidateInlineImages() {
    INLINE_IMAGES = null;
    INLINE_LABELS = null;
  }

  // Per-sheet labels for custom_image_N keywords. Keyword -> human label.
  // Built-in keywords are not present here; callers should fall back to the
  // keyword itself as the tooltip / alt text.
  let INLINE_LABELS = null;
  function getInlineLabels() {
    if (INLINE_LABELS !== null) return INLINE_LABELS;
    const node = document.getElementById('forge-inline-image-labels');
    if (node) {
      try { INLINE_LABELS = JSON.parse(node.textContent) || {}; }
      catch (e) { INLINE_LABELS = {}; }
    } else {
      INLINE_LABELS = {};
    }
    return INLINE_LABELS;
  }
  function labelFor(key) {
    const labels = getInlineLabels();
    return (labels && labels[key]) || key;
  }

  // Set of per-picker rebind functions, registered by editor init. After a
  // refresh, forge_editor.js calls rebindAllImagePickers() to re-render each
  // picker's button list and re-attach the per-editor click handlers.
  const PICKER_REBINDERS = new Set();
  function rebindAllImagePickers() {
    PICKER_REBINDERS.forEach((fn) => {
      try { fn(); } catch (e) { /* per-editor failure shouldn't break the rest */ }
    });
  }

  const ZWSP = '\u200B';

  // =====================================================================
  // Storage HTML allowlist
  // =====================================================================
  // Storage format is HTML restricted to:
  //   <strong>…</strong>          - bold
  //   <em>…</em>                  - italic
  //   <em><strong>…</strong></em> - bold-italic (em-outer)
  //   <span data-forge="header">…</span>
  //   <span data-forge="luminari">…</span>
  //   <img data-forge-image="KEY"> - inline image (bare; src/alt/class added on hydrate)
  //   <br>
  //   plain text (HTML-escaped on write, unescaped by browser on hydrate)
  // Header/luminari/bold/italic are mutually exclusive in the editor, so
  // header/luminari never wrap or are wrapped by <strong>/<em>.

  function escapeHtml(s) {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
  }

  function escapeAttr(s) {
    return String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;')
                    .replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  // =====================================================================
  // Storage HTML -> editor HTML (hydrate on init)
  // =====================================================================
  // Parse the saved HTML, walk it, emit a sanitized editor-ready HTML
  // string with src/alt/class re-attached to inline images.
  function htmlToEditorHtml(value) {
    if (!value) return '';
    const tpl = document.createElement('template');
    tpl.innerHTML = String(value);
    return sanitizeChildrenForEditor(tpl.content);
  }

  function sanitizeChildrenForEditor(parent) {
    let out = '';
    for (let i = 0; i < parent.childNodes.length; i++) {
      out += sanitizeNodeForEditor(parent.childNodes[i]);
    }
    return out;
  }

  function sanitizeNodeForEditor(node) {
    if (node.nodeType === Node.TEXT_NODE) {
      return escapeHtml((node.nodeValue || '').replace(/\u200B/g, ''));
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return '';

    const tag = node.nodeName;
    if (tag === 'BR') return '<br>';

    if (tag === 'IMG') {
      const key = node.getAttribute('data-forge-image');
      if (!key) return '';
      const url = getInlineImages()[key];
      if (!url) return '';
      return '<img data-forge-image="' + escapeAttr(key) + '" src="' + escapeAttr(url)
        + '" alt="' + escapeAttr(key) + '" class="inline-icon">';
    }

    if (tag === 'SPAN') {
      const forge = node.getAttribute('data-forge');
      if (forge === 'header' || forge === 'luminari') {
        const inner = sanitizeChildrenForEditor(node);
        return inner ? '<span data-forge="' + forge + '">' + inner + '</span>' : '';
      }
      return sanitizeChildrenForEditor(node);
    }

    if (tag === 'STRONG' || tag === 'B') {
      const inner = sanitizeChildrenForEditor(node);
      return inner ? '<strong>' + inner + '</strong>' : '';
    }
    if (tag === 'EM' || tag === 'I') {
      const inner = sanitizeChildrenForEditor(node);
      return inner ? '<em>' + inner + '</em>' : '';
    }

    // DIV/P or any other unknown wrapper: emit children only (strip wrapper).
    return sanitizeChildrenForEditor(node);
  }

  // =====================================================================
  // Editor DOM -> storage HTML (serialize on every editor input)
  // =====================================================================
  function serializeNode(node) {
    if (node.nodeType === Node.TEXT_NODE) {
      return escapeHtml((node.nodeValue || '').replace(/\u200B/g, ''));
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return '';

    const tag = node.nodeName;
    if (tag === 'BR') return '<br>';

    if (tag === 'IMG') {
      const key = node.getAttribute('data-forge-image');
      if (!key) return '';
      // Strip src/alt/class — those are re-resolved at render time from
      // FORGE_INLINE_IMAGES so updates to the icon map propagate without
      // re-saving every record.
      return '<img data-forge-image="' + escapeAttr(key) + '">';
    }

    const inner = serializeChildren(node);
    const forge = node.getAttribute && node.getAttribute('data-forge');
    if (forge === 'header') return inner ? '<span data-forge="header">' + inner + '</span>' : '';
    if (forge === 'luminari') return inner ? '<span data-forge="luminari">' + inner + '</span>' : '';

    if (tag === 'STRONG' || tag === 'B') {
      if (!inner) return '';
      // BI normalization: if STRONG wraps a single EM child, swap to em-outer.
      if (node.childNodes.length === 1) {
        const c = node.childNodes[0];
        if (c.nodeType === Node.ELEMENT_NODE && (c.nodeName === 'EM' || c.nodeName === 'I')) {
          const ci = serializeChildren(c);
          return ci ? '<em><strong>' + ci + '</strong></em>' : '';
        }
      }
      return '<strong>' + inner + '</strong>';
    }
    if (tag === 'EM' || tag === 'I') {
      if (!inner) return '';
      if (node.childNodes.length === 1) {
        const c = node.childNodes[0];
        if (c.nodeType === Node.ELEMENT_NODE && (c.nodeName === 'STRONG' || c.nodeName === 'B')) {
          const ci = serializeChildren(c);
          return ci ? '<em><strong>' + ci + '</strong></em>' : '';
        }
      }
      return '<em>' + inner + '</em>';
    }

    if (tag === 'DIV' || tag === 'P') {
      // Browsers wrap a freshly-Enter'd line in a fresh DIV. Storage is a
      // single-line-stream HTML, so collapse blocks to inline content + a
      // <br> separator. Emit a leading <br> only when the previous sibling
      // exists and isn't itself a block (so consecutive DIVs don't double
      // up; a leading inline + DIV gets the separator we'd otherwise miss).
      const prev = node.previousSibling;
      const prevIsBlock = prev && prev.nodeType === Node.ELEMENT_NODE
        && (prev.nodeName === 'DIV' || prev.nodeName === 'P');
      const lead = prev && !prevIsBlock ? '<br>' : '';
      return lead + inner + '<br>';
    }

    // Unknown wrapper: emit children only.
    return inner;
  }

  function serializeChildren(parent) {
    let out = '';
    for (let i = 0; i < parent.childNodes.length; i++) {
      out += serializeNode(parent.childNodes[i]);
    }
    return out;
  }

  function serialize(editor) {
    let out = serializeChildren(editor);
    // Drop a single trailing <br> the browser sometimes leaves in an empty
    // contenteditable, so an empty editor saves as ''.
    if (out.endsWith('<br>')) out = out.slice(0, -4);
    return out;
  }

  // =====================================================================
  // Selection helpers
  // =====================================================================
  function findAncestor(node, editor, predicate) {
    while (node && node !== editor) {
      if (node.nodeType === Node.ELEMENT_NODE && predicate(node)) return node;
      node = node.parentNode;
    }
    return null;
  }

  // Predicate factories — used by both wrap/unwrap and ancestor lookups.
  const isStrong = (n) => n.nodeName === 'STRONG' || n.nodeName === 'B';
  const isEm = (n) => n.nodeName === 'EM' || n.nodeName === 'I';
  const isForge = (kind) => (n) =>
    n.nodeType === Node.ELEMENT_NODE
    && n.getAttribute && n.getAttribute('data-forge') === kind;

  const STYLE_DEFS = {
    bold:     { match: isStrong,           build: () => document.createElement('strong') },
    italic:   { match: isEm,               build: () => document.createElement('em') },
    header:   { match: isForge('header'),  build: () => { const s = document.createElement('span'); s.setAttribute('data-forge', 'header'); return s; } },
    luminari: { match: isForge('luminari'),build: () => { const s = document.createElement('span'); s.setAttribute('data-forge', 'luminari'); return s; } },
  };

  // =====================================================================
  // Range wrap/unwrap primitives
  // =====================================================================

  // Unwrap every ancestor matching `match` that intersects the range.
  // For each match: split into before/middle/after, unwrap the middle.
  // After this call, no element matching `match` overlaps the range.
  function unwrapRange(editor, range, match) {
    if (range.collapsed) return; // nothing to unwrap for a collapsed range

    // Find every matching ancestor that contains some part of the range,
    // plus matching descendants fully inside the range.
    const sel = window.getSelection();

    // Walk up: any ancestor of start (or end) that matches and is within editor.
    let ancestor = findAncestor(range.startContainer, editor, match);
    if (!ancestor) {
      ancestor = findAncestor(range.endContainer, editor, match);
    }

    if (ancestor && ancestor.contains(range.startContainer) && ancestor.contains(range.endContainer)) {
      // Range is entirely inside one matched element. Split into 3 slices.
      const before = document.createRange();
      before.setStartBefore(ancestor);
      before.setEnd(range.startContainer, range.startOffset);
      const after = document.createRange();
      after.setStart(range.endContainer, range.endOffset);
      after.setEndAfter(ancestor);

      const beforeFrag = before.extractContents();
      const middleFrag = range.extractContents();
      const afterFrag = after.extractContents();

      const parent = ancestor.parentNode;
      const placeholder = document.createTextNode('');
      parent.replaceChild(placeholder, ancestor);

      parent.insertBefore(beforeFrag, placeholder);
      const middleNodes = [];
      while (middleFrag.firstChild) {
        const node = middleFrag.firstChild;
        if (node.nodeType === Node.ELEMENT_NODE && match(node)) {
          while (node.firstChild) {
            middleNodes.push(node.firstChild);
            parent.insertBefore(node.firstChild, placeholder);
          }
          middleFrag.removeChild(node);
        } else {
          middleNodes.push(node);
          parent.insertBefore(node, placeholder);
        }
      }
      parent.insertBefore(afterFrag, placeholder);
      parent.removeChild(placeholder);

      // Remove now-empty before/after slices that came from the original ancestor.
      // beforeFrag / afterFrag inserted clones of the ancestor with possibly empty
      // content — strip them if empty.
      // (extractContents on a Range straddling ancestor preserves it as a wrapper;
      //  so we may have introduced empty <strong></strong> / <span></span>.)
      cleanupEmptyMatches(parent, match);

      // Reselect the unwrapped slice.
      if (middleNodes.length) {
        const newRange = document.createRange();
        newRange.setStartBefore(middleNodes[0]);
        newRange.setEndAfter(middleNodes[middleNodes.length - 1]);
        sel.removeAllRanges();
        sel.addRange(newRange);
      }
      return;
    }

    // Range is not strictly inside one matched ancestor. Walk descendants
    // of the common ancestor; unwrap any matched element fully inside the
    // range, and split-unwrap matched ancestors that straddle either edge.

    // Edge case: matched ancestor straddles only the start.
    const startAncestor = findAncestor(range.startContainer, editor, match);
    if (startAncestor) {
      const before = document.createRange();
      before.setStartBefore(startAncestor);
      before.setEnd(range.startContainer, range.startOffset);
      const beforeFrag = before.extractContents();
      const parent = startAncestor.parentNode;
      parent.insertBefore(beforeFrag, startAncestor);
      // startAncestor now begins at the original range.startContainer offset 0.
      // Unwrap it: move its children out before it, then remove it.
      while (startAncestor.firstChild) {
        parent.insertBefore(startAncestor.firstChild, startAncestor);
      }
      parent.removeChild(startAncestor);
      // Update range to start at the original startContainer (still valid).
    }

    const endAncestor = findAncestor(range.endContainer, editor, match);
    if (endAncestor) {
      const after = document.createRange();
      after.setStart(range.endContainer, range.endOffset);
      after.setEndAfter(endAncestor);
      const afterFrag = after.extractContents();
      const parent = endAncestor.parentNode;
      parent.insertBefore(afterFrag, endAncestor.nextSibling);
      while (endAncestor.firstChild) {
        parent.insertBefore(endAncestor.firstChild, endAncestor);
      }
      parent.removeChild(endAncestor);
    }

    // Unwrap any fully-contained matched descendants of the common ancestor.
    const common = range.commonAncestorContainer;
    const root = common.nodeType === Node.ELEMENT_NODE ? common : common.parentNode;
    const candidates = [];
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT, {
      acceptNode: (n) => match(n) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP,
    });
    let c;
    while ((c = walker.nextNode())) candidates.push(c);
    candidates.forEach((node) => {
      if (!editor.contains(node)) return;
      // Only unwrap if range covers the entire node.
      if (range.intersectsNode(node)
          && range.isPointInRange(node, 0)
          && range.isPointInRange(node, node.childNodes.length)) {
        const parent = node.parentNode;
        while (node.firstChild) parent.insertBefore(node.firstChild, node);
        parent.removeChild(node);
      }
    });

    cleanupEmptyMatches(editor, match);
  }

  function cleanupEmptyMatches(root, match) {
    const dead = [];
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT, {
      acceptNode: (n) => match(n) && !n.textContent.replace(/\u200B/g, '').length && n.childNodes.length <= 1
        ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP,
    });
    let n;
    while ((n = walker.nextNode())) dead.push(n);
    dead.forEach((node) => {
      if (node.parentNode) node.parentNode.removeChild(node);
    });
  }

  // Wrap a non-collapsed range in an element built by `build`. Returns the
  // new wrapper element so callers can reposition the selection.
  function wrapRange(range, build) {
    const wrapper = build();
    const contents = range.extractContents();
    wrapper.appendChild(contents);
    range.insertNode(wrapper);
    return wrapper;
  }

  // =====================================================================
  // Editor init — one per .forge-rich-text wrapper
  // =====================================================================
  function init(wrapper) {
    if (wrapper.dataset.forgeRichtextInit === '1') return;
    wrapper.dataset.forgeRichtextInit = '1';

    const textarea = wrapper.querySelector('textarea');
    const toolbar = wrapper.querySelector('.forge-rich-text-toolbar');
    const picker = wrapper.querySelector('.forge-rich-text-image-picker');
    if (!textarea || !toolbar) return;

    const editor = document.createElement('div');
    editor.className = 'forge-rich-text-editor';
    editor.contentEditable = 'true';
    editor.setAttribute('role', 'textbox');
    editor.setAttribute('aria-multiline', 'true');
    editor.innerHTML = htmlToEditorHtml(textarea.value) || '<br>';
    textarea.parentNode.insertBefore(editor, textarea);
    textarea.hidden = true;
    textarea.setAttribute('aria-hidden', 'true');
    textarea.tabIndex = -1;

    function sync() {
      const md = serialize(editor);
      if (textarea.value !== md) {
        textarea.value = md;
        textarea.dispatchEvent(new Event('input', { bubbles: true }));
      }
    }
    editor.addEventListener('input', sync);

    // ---- Selection capture ----
    let savedRange = null;
    function liveSelectionInsideEditor() {
      const sel = window.getSelection();
      if (!sel || sel.rangeCount === 0) return null;
      const range = sel.getRangeAt(0);
      if (editor.contains(range.startContainer) && editor.contains(range.endContainer)) {
        return range;
      }
      return null;
    }
    function captureSelection() {
      const range = liveSelectionInsideEditor();
      if (range) savedRange = range.cloneRange();
    }
    function ensureSelectionInEditor() {
      if (liveSelectionInsideEditor()) return;
      if (!savedRange) return;
      if (!editor.contains(savedRange.startContainer) || !editor.contains(savedRange.endContainer)) {
        savedRange = null;
        return;
      }
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(savedRange);
    }

    // ---- Toolbar buttons ----
    const btnB = toolbar.querySelector('[data-style="bold"]');
    const btnI = toolbar.querySelector('[data-style="italic"]');
    const btnBI = toolbar.querySelector('[data-style="bolditalic"]');
    const btnL = toolbar.querySelector('[data-style="luminari"]');
    const btnH = toolbar.querySelector('[data-style="header"]');

    [btnB, btnI, btnBI, btnL, btnH].forEach((btn) => {
      if (btn) btn.addEventListener('mousedown', (e) => e.preventDefault());
    });

    function setBtnActive(btn, on) {
      if (!btn) return;
      if (on) btn.setAttribute('data-active', 'true');
      else btn.removeAttribute('data-active');
    }

    // All styles are mutually exclusive — only one formatting mark may be
    // active at a time. (BI is a compound action that applies both bold and
    // italic simultaneously; it isn't a STYLE_DEF entry of its own.)
    const STYLE_BTN = { bold: btnB, italic: btnI, header: btnH, luminari: btnL };
    const STYLE_GROUP = { bold: 'bold', italic: 'italic', header: 'header', luminari: 'luminari' };

    // ---- Pending state (collapsed-caret arming) ----
    // Maps style name -> wrapper element holding the pending ZWSP caret.
    // When the user types, the character lands inside the innermost wrapper
    // and we strip the ZWSP. When the caret leaves the wrapper, we delete
    // the empty wrapper and clear pending state.
    const pendingStyles = new Set();
    let pendingMarkerOuter = null; // outermost pending wrapper currently in DOM

    function clearPendingDom() {
      if (pendingMarkerOuter && pendingMarkerOuter.parentNode) {
        pendingMarkerOuter.parentNode.removeChild(pendingMarkerOuter);
      }
      pendingMarkerOuter = null;
    }

    function rebuildPendingMarker() {
      // Remove previous marker (if any) and rebuild from current pendingStyles.
      // Caller must ensure caret is in editor.
      const sel = window.getSelection();

      // If an old marker exists, anchor the caret to a position OUTSIDE it
      // before we remove it — otherwise removing the marker orphans the
      // selection and the new marker can't find an insertion point.
      if (pendingMarkerOuter && pendingMarkerOuter.parentNode) {
        const anchorRange = document.createRange();
        anchorRange.setStartBefore(pendingMarkerOuter);
        anchorRange.collapse(true);
        sel.removeAllRanges();
        sel.addRange(anchorRange);
      }
      clearPendingDom();

      if (pendingStyles.size === 0) {
        captureSelection();
        return;
      }
      const range = liveSelectionInsideEditor();
      if (!range || !range.collapsed) return;

      // Build nested wrappers (bold outside italic by convention to match
      // serializer's preference for _**x**_).
      const order = ['bold', 'italic', 'header', 'luminari'].filter((s) => pendingStyles.has(s));
      let outer = null;
      let innermost = null;
      order.forEach((s) => {
        const el = STYLE_DEFS[s].build();
        if (innermost) innermost.appendChild(el);
        else outer = el;
        innermost = el;
      });
      const zwsp = document.createTextNode(ZWSP);
      innermost.appendChild(zwsp);
      range.insertNode(outer);
      pendingMarkerOuter = outer;

      const newRange = document.createRange();
      newRange.setStart(zwsp, 1);
      newRange.collapse(true);
      sel.removeAllRanges();
      sel.addRange(newRange);
      captureSelection();
    }

    function caretInsidePendingMarker() {
      if (!pendingMarkerOuter) return false;
      const sel = window.getSelection();
      if (!sel || sel.rangeCount === 0) return false;
      const range = sel.getRangeAt(0);
      return pendingMarkerOuter.contains(range.startContainer)
          && pendingMarkerOuter.contains(range.endContainer);
    }

    // ---- Style application ----
    // Wrap selection in `name`'s tag IF not already fully styled. Unlike
    // applyStyleToSelection, this never toggles off — it's idempotent and
    // suitable for compound actions (e.g. BI = ensure bold + ensure italic).
    function ensureStyleOnSelection(name, range) {
      const def = STYLE_DEFS[name];
      const startInside = findAncestor(range.startContainer, editor, def.match);
      const endInside = findAncestor(range.endContainer, editor, def.match);
      if (startInside && startInside === endInside) return; // already styled, leave it
      // Strip any partial existing instances inside the range, then wrap.
      unwrapRange(editor, range, def.match);
      const newRange = liveSelectionInsideEditor() || range;
      if (newRange.collapsed) return;
      const wrapper = wrapRange(newRange, def.build);
      const sel = window.getSelection();
      const r2 = document.createRange();
      r2.selectNodeContents(wrapper);
      sel.removeAllRanges();
      sel.addRange(r2);
    }

    function applyStyleToSelection(name, range) {
      // Non-collapsed range. Toggle the style: if range is fully covered by
      // the style's tag, unwrap; otherwise wrap.
      const def = STYLE_DEFS[name];
      const startInside = findAncestor(range.startContainer, editor, def.match);
      const endInside = findAncestor(range.endContainer, editor, def.match);

      // Determine "is selection currently fully styled" — best-effort:
      // both endpoints share the same matched ancestor.
      const fullyStyled = startInside && startInside === endInside;

      if (fullyStyled) {
        unwrapRange(editor, range, def.match);
      } else {
        // Strip any existing instances of this tag *inside* the range first
        // so wrapping doesn't produce nested duplicates.
        unwrapRange(editor, range, def.match);
        const newRange = liveSelectionInsideEditor() || range;
        if (newRange.collapsed) return;
        const wrapper = wrapRange(newRange, def.build);
        const sel = window.getSelection();
        const r2 = document.createRange();
        r2.selectNodeContents(wrapper);
        sel.removeAllRanges();
        sel.addRange(r2);
      }
    }

    function clearConflictingStyles(name, range) {
      // Remove every style outside `name`'s group from the range AND from
      // the pending set. With strict mutual-exclusivity all other styles
      // count as conflicts.
      //
      // After each DOM mutation we re-fetch the live selection — unwrapRange
      // collapses the original `range` argument (extractContents consumes
      // it), so subsequent iterations would no-op without a refresh.
      const group = STYLE_GROUP[name];
      Object.keys(STYLE_GROUP).forEach((other) => {
        if (other === name) return;
        if (STYLE_GROUP[other] === group) return;
        const live = liveSelectionInsideEditor() || range;
        if (live && !live.collapsed) {
          unwrapRange(editor, live, STYLE_DEFS[other].match);
        } else if (live) {
          const ancestor = findAncestor(live.startContainer, editor, STYLE_DEFS[other].match);
          if (ancestor) splitOutOfAncestor(ancestor, live);
        }
        pendingStyles.delete(other);
        setBtnActive(STYLE_BTN[other], false);
      });
    }

    function applyStyle(name) {
      editor.focus();
      ensureSelectionInEditor();
      const range = liveSelectionInsideEditor();
      if (!range) return;

      // Derive on/off from the DOM (and pending-state for collapsed caret),
      // not the button's stored attribute. This keeps repeat clicks toggling
      // predictably even if toolbar state has drifted out of sync.
      const insideStart = findAncestor(range.startContainer, editor, STYLE_DEFS[name].match);
      const turningOn = !insideStart && !pendingStyles.has(name);

      // Special case: caret/selection is currently bold-italic and the user
      // clicks plain B or plain I. Mutual-exclusivity intent says "switch to
      // that single style," not "toggle this style off." Strip the partner
      // and leave `name` in place.
      if (!turningOn && (name === 'bold' || name === 'italic')) {
        const partner = name === 'bold' ? 'italic' : 'bold';
        const inPartnerDom = !!findAncestor(range.startContainer, editor, STYLE_DEFS[partner].match);
        const inPartnerPending = pendingStyles.has(partner);
        if (inPartnerDom || inPartnerPending) {
          if (range.collapsed) {
            const partnerAnc = findAncestor(range.startContainer, editor, STYLE_DEFS[partner].match);
            if (partnerAnc) splitOutOfAncestor(partnerAnc, liveSelectionInsideEditor() || range);
            pendingStyles.delete(partner);
            // After splitOutOfAncestor the caret sits OUTSIDE both wrappers
            // (between split halves). Rebuild the pending marker with `name`
            // alone so the caret lands inside a fresh single-style wrapper —
            // otherwise updateActiveStates sees no ancestor and clears the
            // button we just turned on.
            pendingStyles.add(name);
            rebuildPendingMarker();
          } else {
            // Non-collapsed BI -> single style. Unwrapping just one of the
            // two nested tags is structurally fragile: extractContents can
            // destroy the inner wrapper around the selected text. So we
            // strip BOTH bold and italic and then re-wrap with `name`.
            unwrapRange(editor, range, STYLE_DEFS.bold.match);
            const r2 = liveSelectionInsideEditor() || range;
            if (r2 && !r2.collapsed) {
              unwrapRange(editor, r2, STYLE_DEFS.italic.match);
              const r3 = liveSelectionInsideEditor() || r2;
              if (r3 && !r3.collapsed) {
                ensureStyleOnSelection(name, r3);
              }
            }
          }
          setBtnActive(STYLE_BTN[partner], false);
          setBtnActive(STYLE_BTN[name], true);
          setBtnActive(btnBI, false);
          captureSelection();
          sync();
          updateActiveStates();
          return;
        }
      }

      if (range.collapsed) {
        // Collapsed caret: pending-flag mode.
        // First, if the caret sits inside an existing tag of `name`, we
        // treat that as "currently on" for toggle-off semantics.
        const inside = insideStart;

        if (turningOn) {
          // Clear conflicts (other groups) from pending set.
          clearConflictingStyles(name, range);
          if (inside) {
            // Already inside this tag — split it so caret sits between two
            // halves and add `name` to pendingStyles only if user wants to
            // continue typing styled, but inside means it's already styled.
            // Actually: turningOn=true + already inside means our state was
            // out of sync. Treat as a no-op other than recording pending.
            pendingStyles.add(name);
          } else {
            pendingStyles.add(name);
          }
          rebuildPendingMarker();
        } else {
          // Turning off.
          pendingStyles.delete(name);
          if (inside) {
            // Caret is inside a real styled element — split it so caret
            // sits outside the tag, then add a ZWSP marker between halves.
            splitOutOfAncestor(inside, range);
            // No pending styles for `name`; rebuild marker for any others.
            rebuildPendingMarker();
          } else {
            rebuildPendingMarker();
          }
        }
        setBtnActive(STYLE_BTN[name], turningOn);
        // Don't sync — DOM only carries an empty pending wrapper; serializer
        // will skip it because the wrapper is empty (cleanup elides it).
        // But marker insertion into DOM does dispatch nothing; serializer
        // sees the wrapper but treats empty-text nodes as ''.
        sync();
        return;
      }

      // Non-collapsed range.
      if (turningOn) {
        clearConflictingStyles(name, range);
        // Re-fetch range after possible DOM mutations.
        const r2 = liveSelectionInsideEditor() || range;
        applyStyleToSelection(name, r2);
      } else {
        unwrapRange(editor, range, STYLE_DEFS[name].match);
      }

      pendingStyles.clear();
      clearPendingDom();
      setBtnActive(STYLE_BTN[name], turningOn);
      // Selected-range path: any other-group buttons that were active
      // should turn off (we did unwrap them above when turning on).
      if (turningOn) {
        Object.keys(STYLE_BTN).forEach((other) => {
          if (other === name) return;
          if (STYLE_GROUP[other] === STYLE_GROUP[name]) return;
          setBtnActive(STYLE_BTN[other], false);
        });
      }
      captureSelection();
      sync();
    }

    // Split an ancestor at the caret, placing caret between the two halves.
    function splitOutOfAncestor(ancestor, range) {
      const splitRange = range.cloneRange();
      splitRange.setEndAfter(ancestor.lastChild || ancestor);
      const tail = splitRange.extractContents();
      ancestor.parentNode.insertBefore(tail, ancestor.nextSibling);
      const caret = document.createTextNode(ZWSP);
      ancestor.parentNode.insertBefore(caret, ancestor.nextSibling);
      const newRange = document.createRange();
      newRange.setStart(caret, 1);
      newRange.collapse(true);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(newRange);
    }

    // Compound action: apply bold + italic together (single mark _**...**_).
    // Toggles off when caret is inside both <strong> and <em>.
    function applyBoldItalic() {
      editor.focus();
      ensureSelectionInEditor();
      const range = liveSelectionInsideEditor();
      if (!range) return;

      const inBold = !!findAncestor(range.startContainer, editor, STYLE_DEFS.bold.match);
      const inItalic = !!findAncestor(range.startContainer, editor, STYLE_DEFS.italic.match);
      const pendingBoth = pendingStyles.has('bold') && pendingStyles.has('italic');
      const turningOn = !((inBold && inItalic) || pendingBoth);

      if (turningOn) {
        // Strip header/luminari first, then apply bold + italic.
        if (!range.collapsed) {
          unwrapRange(editor, range, STYLE_DEFS.header.match);
          const afterH = liveSelectionInsideEditor() || range;
          if (afterH && !afterH.collapsed) {
            unwrapRange(editor, afterH, STYLE_DEFS.luminari.match);
          }
        } else {
          const headerAnc = findAncestor(range.startContainer, editor, STYLE_DEFS.header.match);
          if (headerAnc) splitOutOfAncestor(headerAnc, liveSelectionInsideEditor() || range);
          const lumAnc = findAncestor((liveSelectionInsideEditor() || range).startContainer, editor, STYLE_DEFS.luminari.match);
          if (lumAnc) splitOutOfAncestor(lumAnc, liveSelectionInsideEditor() || range);
        }
        pendingStyles.delete('header');
        pendingStyles.delete('luminari');
        setBtnActive(btnH, false);
        setBtnActive(btnL, false);

        let liveAfterClear = liveSelectionInsideEditor() || range;
        if (liveAfterClear.collapsed) {
          // Caret may currently be inside an existing <strong> or <em> from
          // adjacent styled content (e.g. user just typed an italic word and
          // clicked BI without moving). Inserting the new BI marker there
          // would nest it inside, producing malformed markdown like
          // `_text _**bi**__` on serialize. Split out first so the new
          // marker is a sibling, not a descendant.
          const boldAnc = findAncestor(liveAfterClear.startContainer, editor, STYLE_DEFS.bold.match);
          if (boldAnc) splitOutOfAncestor(boldAnc, liveSelectionInsideEditor() || liveAfterClear);
          liveAfterClear = liveSelectionInsideEditor() || liveAfterClear;
          const italicAnc = findAncestor(liveAfterClear.startContainer, editor, STYLE_DEFS.italic.match);
          if (italicAnc) splitOutOfAncestor(italicAnc, liveSelectionInsideEditor() || liveAfterClear);
          pendingStyles.add('bold');
          pendingStyles.add('italic');
          rebuildPendingMarker();
        } else {
          // Idempotent: ensure both italic and bold are present without
          // toggling off whichever was already applied. Italic outer / bold
          // inner — so a later "back to italic" toggle (which unwraps bold)
          // leaves italic intact around the text. The reverse nesting would
          // destroy the inner em when bold is unwrapped.
          ensureStyleOnSelection('italic', liveAfterClear);
          const r2 = liveSelectionInsideEditor() || liveAfterClear;
          if (!r2.collapsed) ensureStyleOnSelection('bold', r2);
          pendingStyles.clear();
          clearPendingDom();
        }
        setBtnActive(btnB, false);
        setBtnActive(btnI, false);
      } else {
        // Toggle off: remove both bold and italic.
        if (range.collapsed) {
          pendingStyles.delete('bold');
          pendingStyles.delete('italic');
          if (inBold) {
            splitOutOfAncestor(findAncestor(range.startContainer, editor, STYLE_DEFS.bold.match), liveSelectionInsideEditor() || range);
          }
          const r2 = liveSelectionInsideEditor() || range;
          const stillInItalic = findAncestor(r2.startContainer, editor, STYLE_DEFS.italic.match);
          if (stillInItalic) {
            splitOutOfAncestor(stillInItalic, r2);
          }
          rebuildPendingMarker();
        } else {
          unwrapRange(editor, range, STYLE_DEFS.bold.match);
          const r2 = liveSelectionInsideEditor() || range;
          unwrapRange(editor, r2, STYLE_DEFS.italic.match);
          pendingStyles.clear();
          clearPendingDom();
        }
      }
      captureSelection();
      sync();
      updateActiveStates();
    }

    if (btnB) btnB.addEventListener('click', () => applyStyle('bold'));
    if (btnI) btnI.addEventListener('click', () => applyStyle('italic'));
    if (btnBI) btnBI.addEventListener('click', applyBoldItalic);
    if (btnL) btnL.addEventListener('click', () => applyStyle('luminari'));
    if (btnH) btnH.addEventListener('click', () => applyStyle('header'));

    // ---- Image picker ----
    const imgBtn = toolbar.querySelector('[data-action="image"]');
    function insertImageAtCaret(key) {
      editor.focus();
      ensureSelectionInEditor();
      const range = liveSelectionInsideEditor();
      if (!range) return;
      const url = getInlineImages()[key];
      if (!url) return;
      const img = document.createElement('img');
      img.setAttribute('data-forge-image', key);
      img.src = url;
      img.alt = labelFor(key);
      img.className = 'inline-icon';
      range.deleteContents();
      range.insertNode(img);
      const newRange = document.createRange();
      newRange.setStartAfter(img);
      newRange.collapse(true);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(newRange);
      editor.dispatchEvent(new Event('input', { bubbles: true }));
    }
    if (imgBtn && picker) {
      // Build the picker button list. Extracted so we can rebuild it after
      // CustomInlineImage add/edit/delete without re-initing the editor.
      function buildPickerButtons() {
        const images = getInlineImages();
        // Existing buttons we'll reuse where possible (preserves any focus
        // state); leftovers are removed at the end.
        const existing = new Map();
        picker.querySelectorAll('button[data-insert]').forEach((btn) => {
          existing.set(btn.dataset.insert, btn);
        });
        // Server gives us the ordered keyword list separately. Read from the
        // payload set by forge_editor.js, falling back to the keys present in
        // the map at first init (initial render uses pre-rendered <button>
        // tags so this fallback is fine).
        const ordered = window.forgeInlineKeywords && window.forgeInlineKeywords.length
          ? window.forgeInlineKeywords
          : Array.from(existing.keys());
        const frag = document.createDocumentFragment();
        ordered.forEach((key) => {
          const url = images[key];
          const label = labelFor(key);
          let btn = existing.get(key);
          const isNew = !btn;
          if (isNew) {
            btn = document.createElement('button');
            btn.type = 'button';
            btn.dataset.insert = key;
          } else {
            existing.delete(key);
            btn.innerHTML = '';
          }
          btn.title = label;
          btn.classList.add('rt-image-btn');
          if (url) {
            const img = document.createElement('img');
            img.src = url;
            img.alt = label;
            btn.appendChild(img);
          }
          if (isNew || !btn._forgeBound) {
            btn._forgeBound = true;
            btn.addEventListener('mousedown', (e) => e.preventDefault());
            btn.addEventListener('click', () => {
              insertImageAtCaret(key);
              picker.hidden = true;
            });
          }
          frag.appendChild(btn);
        });
        existing.forEach((btn) => btn.remove());
        picker.appendChild(frag);
      }
      buildPickerButtons();
      PICKER_REBINDERS.add(buildPickerButtons);
      imgBtn.addEventListener('mousedown', (e) => e.preventDefault());
      imgBtn.addEventListener('click', () => {
        picker.hidden = !picker.hidden;
      });
    }

    // ---- beforeinput: consume pending state on first typed char ----
    editor.addEventListener('beforeinput', (e) => {
      if (!pendingMarkerOuter) return;

      // If the caret left the marker, drop it.
      if (!caretInsidePendingMarker()) {
        clearPendingDom();
        pendingStyles.clear();
        syncToolbarFromPending();
        return;
      }

      if (e.inputType === 'insertText' || e.inputType === 'insertCompositionText') {
        // Mark for cleanup on the next input event — we'll strip the ZWSP
        // after the browser inserts the character.
        pendingNeedsCleanup = true;
      } else if (e.inputType === 'deleteContentBackward' || e.inputType === 'deleteContentForward') {
        // Remove the entire pending wrapper, clear pending state.
        e.preventDefault();
        clearPendingDom();
        pendingStyles.clear();
        syncToolbarFromPending();
      }
    });

    let pendingNeedsCleanup = false;
    editor.addEventListener('input', () => {
      if (!pendingNeedsCleanup) return;
      pendingNeedsCleanup = false;
      // The browser inserted the typed character at offset 1 of the ZWSP
      // text node, leaving content "<ZWSP><char>" with caret at offset 2.
      // We deliberately do NOT mutate the text node here — replacing
      // nodeValue would collapse the caret to offset 0 and visually move
      // the cursor in front of the typed character. The serializer
      // already strips ZWSP from text output, so the marker remains
      // invisible to saved markdown.
      //
      // Pending state has been "consumed" — the wrappers are now real
      // formatting around the typed text. Clear pendingStyles + marker
      // pointer so further typing extends the style naturally (browsers
      // continue typing inside the same wrapper).
      pendingMarkerOuter = null;
      pendingStyles.clear();
      syncToolbarFromPending();
      sync();
    });

    function syncToolbarFromPending() {
      // No-op — toolbar reflects pendingStyles via updateActiveStates on
      // selectionchange. Kept as a hook for future explicit refresh.
    }

    // ---- Selection-change toolbar sync ----
    function selectionStyles(range) {
      // Returns Set of styles whose tag is an ancestor of the caret/selection.
      // We probe both startContainer AND the first text-or-element node
      // actually inside the range — when a selection was set via
      // setStartBefore(elem), startContainer is the *parent*, so walking up
      // from it misses tags that wrap the content inside.
      const out = new Set();
      const probes = [range.startContainer];
      // Walk down to find the first element/text node at the range start.
      let node = range.startContainer;
      if (node.nodeType === Node.ELEMENT_NODE && range.startOffset < node.childNodes.length) {
        probes.push(node.childNodes[range.startOffset]);
      }
      Object.keys(STYLE_DEFS).forEach((s) => {
        const def = STYLE_DEFS[s];
        for (const p of probes) {
          if (!p) continue;
          if (p.nodeType === Node.ELEMENT_NODE && def.match(p)) { out.add(s); break; }
          if (findAncestor(p, editor, def.match)) { out.add(s); break; }
        }
      });
      return out;
    }

    function updateActiveStates() {
      const range = liveSelectionInsideEditor();
      if (!range) return;

      // If there's pending state and caret is inside the marker, pendingStyles wins.
      if (pendingMarkerOuter && caretInsidePendingMarker()) {
        const pBold = pendingStyles.has('bold');
        const pItalic = pendingStyles.has('italic');
        const pBI = pBold && pItalic;
        setBtnActive(btnB, pBold && !pBI);
        setBtnActive(btnI, pItalic && !pBI);
        setBtnActive(btnBI, pBI);
        setBtnActive(btnH, pendingStyles.has('header'));
        setBtnActive(btnL, pendingStyles.has('luminari'));
        return;
      }

      const styles = selectionStyles(range);
      const inHeader = styles.has('header');
      const inLuminari = styles.has('luminari');
      let bold = styles.has('bold');
      let italic = styles.has('italic');
      if (inHeader || inLuminari) {
        bold = false;
        italic = false;
      }
      const isBI = bold && italic;
      setBtnActive(btnB, bold && !isBI);
      setBtnActive(btnI, italic && !isBI);
      setBtnActive(btnBI, isBI);
      setBtnActive(btnH, inHeader && !inLuminari);
      setBtnActive(btnL, inLuminari && !inHeader);
    }

    document.addEventListener('selectionchange', () => {
      if (document.activeElement !== editor) return;
      // Pending marker cleanup: if caret left the marker, drop it.
      if (pendingMarkerOuter && !caretInsidePendingMarker()) {
        clearPendingDom();
        pendingStyles.clear();
      }
      captureSelection();
      updateActiveStates();
    });

    // ---- Disable Ctrl+B / Ctrl+I (custom engine handles formatting) ----
    editor.addEventListener('keydown', (e) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      const k = e.key.toLowerCase();
      if (k === 'b' || k === 'i') {
        e.preventDefault();
      }
    });

    // ---- Paste as plain text ----
    editor.addEventListener('paste', (e) => {
      e.preventDefault();
      const text = (e.clipboardData || window.clipboardData).getData('text/plain');
      document.execCommand('insertText', false, text);
    });
  }

  function initAll(root) {
    (root || document).querySelectorAll('.forge-rich-text').forEach(init);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => initAll());
  } else {
    initAll();
  }

  window.initForgeRichText = initAll;
  window.forgeRichText = {
    invalidateInlineImages: invalidateInlineImages,
    rebindAllImagePickers: rebindAllImagePickers,
  };
})();
