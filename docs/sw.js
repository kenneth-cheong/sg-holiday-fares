/* Makes the installed app open without a connection instead of on a browser
   error page. Network first for everything on this origin, falling back to the
   last copy; prices come from the API on another origin and are never touched
   here, so nothing about a fare is ever served stale. */
const CACHE = 'sg-fares-shell-v3';
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

  // GitHub Pages sends max-age=600, so a plain fetch can hand back a page up
  // to ten minutes behind a deploy; revalidating the page itself is one
  // cheap 304 when nothing changed.
  const network = request.mode === 'navigate'
    ? fetch(new Request(request.url, { cache: 'no-cache', credentials: 'same-origin' }))
    : fetch(request);
  event.respondWith(
    network
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
