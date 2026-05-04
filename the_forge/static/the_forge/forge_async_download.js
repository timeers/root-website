(function () {
  function setSpinner(el) {
    el.dataset.originalHtml = el.innerHTML;
    el.classList.add('disabled');
    el.setAttribute('aria-busy', 'true');
    el.style.pointerEvents = 'none';
    el.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Generating…';
  }

  function restoreButton(el) {
    if (el.dataset.originalHtml !== undefined) {
      el.innerHTML = el.dataset.originalHtml;
      delete el.dataset.originalHtml;
    }
    el.classList.remove('disabled');
    el.removeAttribute('aria-busy');
    el.style.pointerEvents = '';
  }

  function findSpinnerTarget(el) {
    const group = el.closest('.btn-group');
    if (group) {
      // Split-button groups have a primary action button alongside the
      // dropdown-toggle-split caret; prefer the primary so the spinner
      // replaces the visible "Download All" label, not the small caret.
      const primary = group.querySelector(':scope > .btn:not(.dropdown-toggle)');
      if (primary) return primary;
      const toggle = group.querySelector('.dropdown-toggle');
      if (toggle) return toggle;
    }
    return el;
  }

  function statusLabel(url) {
    if (/\/webp\/?$/.test(url) || url.indexOf('/webp/') !== -1) return 'Generating image, please wait…';
    if (/\/tts\/?$/.test(url) || url.indexOf('/tts/') !== -1) return 'Generating JSON, please wait…';
    return 'Generating PDF, please wait…';
  }

  function filenameFromDisposition(header) {
    if (!header) return '';
    const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(header);
    if (utf8) {
      try { return decodeURIComponent(utf8[1]); } catch (e) { /* fall through */ }
    }
    const quoted = /filename="([^"]+)"/i.exec(header);
    if (quoted) return quoted[1];
    const bare = /filename=([^;]+)/i.exec(header);
    if (bare) return bare[1].trim();
    return '';
  }

  function triggerDownload(blob, filename) {
    const blobUrl = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = blobUrl;
    a.download = filename || 'download';
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(blobUrl), 60000);
  }

  function spinnerHtml(label) {
    const urls = window.FORGE_SPINNER_URLS || {};
    const bg = urls.bg || '';
    const top = urls.top || '';
    const vb = urls.vb || '';
    return (
      '<!doctype html>' +
      '<html><head><meta charset="utf-8"><title>Generating…</title>' +
      '<style>' +
      'html,body{height:100%;margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:#f5efe2;color:#444;}' +
      '.wrap{min-height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:2rem;}' +
      '.image-container{position:relative;width:min(60%,360px);aspect-ratio:1/1;display:grid;grid-template-columns:1fr;grid-template-rows:1fr;place-items:center;}' +
      '.image{width:100%;grid-area:1/1;}' +
      '.back-image{z-index:8;}' +
      '.movement-image{z-index:9;animation:moveImage 3s ease-in-out infinite;}' +
      '.top-image{z-index:10;}' +
      '@keyframes moveImage{' +
        '0%{transform:translateY(34%) translateX(-6%);}' +
        '30%{transform:translateY(0) translateX(0);}' +
        '60%{transform:translateY(0) translateX(0);}' +
        '100%{transform:translateY(34%) translateX(-6%);}' +
      '}' +
      '.status{margin-top:1.25rem;font-size:1rem;color:#666;}' +
      '</style></head><body>' +
      '<div class="wrap">' +
        '<div class="image-container">' +
          (bg  ? '<img class="image back-image" src="' + bg  + '" alt="">' : '') +
          (vb  ? '<img class="image movement-image" src="' + vb  + '" alt="">' : '') +
          (top ? '<img class="image top-image" src="' + top + '" alt="">' : '') +
        '</div>' +
        '<p class="status">' + label + '</p>' +
      '</div>' +
      '</body></html>'
    );
  }

  function attach(el) {
    el.addEventListener('click', async function (ev) {
      if (ev.defaultPrevented) return;
      ev.preventDefault();
      const spinTarget = findSpinnerTarget(el);
      if (spinTarget.getAttribute('aria-busy') === 'true') return;

      const looksLikeJson = /\/tts\/?$/.test(el.href) || el.href.indexOf('/tts/') !== -1;
      const placeholder = looksLikeJson ? null : window.open('', '_blank');
      if (placeholder) {
        try {
          placeholder.document.open();
          placeholder.document.write(spinnerHtml(statusLabel(el.href)));
          placeholder.document.close();
        } catch (e) { /* cross-origin write may fail; ignore */ }
      }

      setSpinner(spinTarget);
      try {
        const res = await fetch(el.href, { credentials: 'same-origin' });
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const blob = await res.blob();
        const contentType = (res.headers.get('Content-Type') || '').toLowerCase();
        const disposition = res.headers.get('Content-Disposition') || '';
        const isAttachment = /^attachment/i.test(disposition);
        const isJson = contentType.indexOf('application/json') !== -1;
        if (isJson || isAttachment) {
          if (placeholder && !placeholder.closed) {
            try { placeholder.close(); } catch (e) { /* ignore */ }
          }
          triggerDownload(blob, filenameFromDisposition(disposition));
        } else {
          const blobUrl = URL.createObjectURL(blob);
          if (placeholder && !placeholder.closed) {
            placeholder.location.replace(blobUrl);
          } else {
            window.open(blobUrl, '_blank');
          }
          setTimeout(() => URL.revokeObjectURL(blobUrl), 60000);
        }
      } catch (err) {
        if (placeholder && !placeholder.closed) {
          try { placeholder.close(); } catch (e) { /* ignore */ }
        }
        alert('Download failed. Please try again.');
        console.error('forge-async-download:', err);
      } finally {
        restoreButton(spinTarget);
      }
    });
  }

  document.querySelectorAll('.forge-async-download').forEach(attach);
})();
