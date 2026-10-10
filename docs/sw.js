/* Network-first PWA; never cache-bust a new data entry every minute. */
const CACHE = 'stock-radar-terminal-v4';
const CORE = ['./', './index.html', './terminal.css', './terminal.js', './theme.js', './manifest.json', './icon.svg', './settings.html'];
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(CORE)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key.startsWith('stock-radar-') && key !== CACHE).map(key => caches.delete(key)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  const scope = new URL(self.registration.scope);
  if (event.request.method !== 'GET' || url.origin !== scope.origin || !url.pathname.startsWith(scope.pathname)) return;
  const relative = url.pathname.slice(scope.pathname.length);
  const allowed = ['', 'index.html', 'terminal.css', 'terminal.js', 'theme.js', 'manifest.json', 'icon.svg', 'settings.html', 'data.json'];
  if (!allowed.includes(relative)) return;
  const key = new Request(url.origin + url.pathname);
  event.respondWith((async () => {
    const cache = await caches.open(CACHE);
    try {
      const response = await fetch(event.request);
      if (response.ok) await cache.put(key, response.clone());
      return response;
    } catch (error) {
      const saved = await cache.match(key);
      if (!saved) return new Response('Offline: no saved radar file', {status: 503});
      const headers = new Headers(saved.headers);
      headers.set('X-Radar-Offline', 'true');
      return new Response(await saved.arrayBuffer(), {status: saved.status, headers});
    }
  })());
});
