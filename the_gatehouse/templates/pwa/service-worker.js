{% load static %}const CACHE_NAME = 'rdb-shell-v{{ version }}';

const PRECACHE_URLS = [
  '/offline/',
  '{% static "the_keep/main.css" %}',
  '{% static "js/htmx.min.js" %}',
  '{% static "images/favicon.png" %}',
  '{% static "images/apple-touch-icon.png" %}',
  '{% static "images/icon-192.png" %}',
  '{% static "images/icon-512.png" %}',
  '{% static "images/icon-maskable-512.png" %}'
];

const BYPASS_PREFIXES = [
  '/accounts/',
  '/admin/',
  '/woodland-admin/',
  '/hx/',
  '/api/',
  '/__debug__/',
  '/media/',
  '/onboard/',
  '/set-language/',
  '/password-reset'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => cache.addAll(PRECACHE_URLS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((names) => Promise.all(
        names.filter((n) => n !== CACHE_NAME).map((n) => caches.delete(n))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;

  if (req.method !== 'GET') return;
  if (req.headers.get('HX-Request')) return;
  if (req.headers.has('range')) return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (BYPASS_PREFIXES.some((p) => url.pathname.startsWith(p))) return;

  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(req).then((hit) => {
        if (hit) return hit;
        return fetch(req).then((res) => {
          const copy = res.clone();
          caches.open(CACHE_NAME).then((c) => c.put(req, copy));
          return res;
        });
      })
    );
    return;
  }

  if (req.mode === 'navigate') {
    event.respondWith(
      fetch(req).catch(() => caches.match('/offline/'))
    );
    return;
  }
});
