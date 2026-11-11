/* Systor Service Worker — synchronized with app version
   Strategy:
   - Dynamic API: Network-Only (telemetry is never served stale)
   - Static assets: Stale-While-Revalidate (instant offline load, background update)
   - Navigation: Network-First with dedicated offline page fallback
*/

const CACHE_NAME = 'systor-shell-v1.1.0';
const OFFLINE_URL = '/offline';
const STATIC_ASSETS = [
  '/static/css/style.css',
  '/static/css/tokens.css',
  '/static/js/app.js',
  '/static/js/theme-boot.js',
  '/static/img/systor-logo-header.png',
  '/static/img/icon-192.png',
  '/static/img/icon-512.png',
  '/static/manifest.json',
  OFFLINE_URL
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

  // 1. API calls and live metrics are strictly Network-Only
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(fetch(event.request));
    return;
  }

  // 2. Navigation / HTML requests: Network-First with dedicated /offline fallback
  if (event.request.mode === 'navigate') {
    event.respondWith(
      fetch(event.request).catch(() => caches.match(OFFLINE_URL))
    );
    return;
  }

  // 3. Static assets: Stale-While-Revalidate (serve cached immediately, refresh in background)
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.open(CACHE_NAME).then((cache) => {
        return cache.match(event.request).then((cached) => {
          const fetchPromise = fetch(event.request).then((networkResponse) => {
            if (networkResponse && networkResponse.status === 200 && networkResponse.type === 'basic') {
              cache.put(event.request, networkResponse.clone());
            }
            return networkResponse;
          }).catch(() => cached);
          return cached || fetchPromise;
        });
      })
    );
    return;
  }

  // Default: pass through to network
  event.respondWith(fetch(event.request));
});
