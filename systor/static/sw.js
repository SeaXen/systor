/* Systor Service Worker — v1.0.0
   Strategy:
   - Static assets (CSS, JS, icons): Cache-First with network fallback
   - API telemetry & dynamic queries: Network-Only (guarantees real-time accuracy)
   - Navigation: Network-First with cache fallback
*/

const CACHE_NAME = 'systor-shell-v1.0.0';
const STATIC_ASSETS = [
  '/static/css/style.min.css?v=3.0',
  '/static/js/app.js',
  '/static/img/systor-logo-header.png?v=2',
  '/static/img/icon-192.png',
  '/static/img/icon-512.png',
  '/static/manifest.json'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(STATIC_ASSETS);
    }).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.map((key) => {
          if (key !== CACHE_NAME) {
            return caches.delete(key);
          }
        })
      );
    }).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // 1. API calls are strictly Network-Only (never stale telemetry)
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(fetch(event.request));
    return;
  }

  // 2. Navigation / HTML requests: Network-First
  if (event.request.mode === 'navigate') {
    event.respondWith(
      fetch(event.request).catch(() => caches.match('/'))
    );
    return;
  }

  // 3. Static assets: Cache-First
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(event.request).then((cached) => {
        if (cached) return cached;
        return fetch(event.request).then((response) => {
          if (!response || response.status !== 200 || response.type !== 'basic') {
            return response;
          }
          const responseToCache = response.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(event.request, responseToCache);
          });
          return response;
        });
      })
    );
    return;
  }

  // Default: pass through to network
  event.respondWith(fetch(event.request));
});
