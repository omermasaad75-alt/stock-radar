const CACHE = 'stock-radar-v2';
const CORE = ['./','./index.html','./manifest.json','./icon.svg','./dashboard-fix.js','./candles.js'];
self.addEventListener('install', event => { event.waitUntil(caches.open(CACHE).then(c => c.addAll(CORE)).then(() => self.skipWaiting())); });
self.addEventListener('activate', event => { event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim())); });
self.addEventListener('fetch', event => { if (event.request.method !== 'GET') return; event.respondWith(fetch(event.request).then(r => { const copy=r.clone(); caches.open(CACHE).then(c => c.put(event.request, copy)); return r; }).catch(() => caches.match(event.request))); });
