/* Makes the installed app open without a connection instead of on a browser
   error page. Network first for everything on this origin, falling back to the
   last copy; prices come from the API on another origin and are never touched
   here, so nothing about a fare is ever served stale. */
const CACHE = 'sg-fares-shell-v2';
const SHELL = [
  './', 'index.html', 'manifest.webmanifest',
  'data/plan.json', 'data/airports.json',
  'icons/icon-192.png', 'icons/favicon-32.png',
];

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', event => {
  const { request } = event;
  if (request.method !== 'GET' || new URL(request.url).origin !== location.origin) return;

  event.respondWith(
    fetch(request)
      .then(response => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(CACHE).then(cache => cache.put(request, copy));
        }
        return response;
      })
      .catch(() => caches.match(request, { ignoreSearch: true })
        .then(hit => hit || (request.mode === 'navigate' ? caches.match('index.html') : Response.error()))),
  );
});
