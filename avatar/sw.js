const CACHE = 'amora-avatar-v09-security-20261023';
const SHELL = [
  '/avatar/',
  '/avatar/index.html',
  '/avatar/styles.css',
  '/avatar/app.js',
  '/avatar/rig-v2.js',
  '/avatar/engine.js',
  '/avatar/manifest.webmanifest',
];

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(key => key !== CACHE && key.startsWith('amora-avatar-')).map(key => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.pathname.startsWith('/v1/')) return;
  if (!url.pathname.startsWith('/avatar/')) return;
  event.respondWith(
    fetch(event.request)
      .then(response => {
        const copy = response.clone();
        caches.open(CACHE).then(cache => cache.put(event.request, copy));
        return response;
      })
      .catch(() => caches.match(event.request).then(cached => cached || caches.match('/avatar/')))
  );
});
