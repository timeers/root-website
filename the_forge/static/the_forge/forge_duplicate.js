(function () {
  function getCookie(name) {
    const m = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]+)'));
    return m ? decodeURIComponent(m[1]) : '';
  }
  function getCsrfToken() {
    const input = document.querySelector('input[name="csrfmiddlewaretoken"]');
    if (input && input.value) return input.value;
    return getCookie('csrftoken');
  }

  function setSpinner(el) {
    el.dataset.originalHtml = el.innerHTML;
    el.classList.add('disabled');
    el.setAttribute('aria-busy', 'true');
    el.style.pointerEvents = 'none';
    el.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Duplicating…';
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

  const POLL_MS = 1500;

  function poll(statusUrl, el) {
    fetch(statusUrl, { credentials: 'same-origin' })
      .then((res) => {
        if (res.status >= 500) throw new Error('clone failed');
        return res.json();
      })
      .then((data) => {
        if (data.state === 'SUCCESS' && data.redirect) {
          window.location = data.redirect;
          return;
        }
        setTimeout(() => poll(statusUrl, el), POLL_MS);
      })
      .catch((err) => {
        console.error('forge-duplicate:', err);
        alert('Duplicating failed. Please try again.');
        restoreButton(el);
      });
  }

  // Delegated so the handler works regardless of when the button enters the
  // DOM — the confirm modal is included *after* this script in the detail
  // template, so a parse-time querySelectorAll would bind nothing.
  document.addEventListener('click', function (ev) {
    const el = ev.target.closest('.forge-duplicate');
    if (!el) return;
    if (el.getAttribute('aria-busy') === 'true') return;  // guard double-click
    setSpinner(el);
    fetch(el.dataset.url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'X-CSRFToken': getCsrfToken(),
        'X-Requested-With': 'XMLHttpRequest',
      },
    })
      .then((res) => {
        if (res.status === 403) throw new Error('not permitted');
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
      })
      .then((data) => {
        const statusUrl = el.dataset.statusBase.replace('TASK_ID', encodeURIComponent(data.task_id));
        poll(statusUrl, el);
      })
      .catch((err) => {
        console.error('forge-duplicate:', err);
        alert(err && err.message === 'not permitted'
          ? 'You do not have permission to duplicate this faction.'
          : 'Duplicating failed. Please try again.');
        restoreButton(el);
      });
  });
})();
