const STATIC_CACHE = 'plair-static-v5';
const DYNAMIC_CACHE = 'plair-dynamic-v4';
const DYNAMIC_CACHE_MAX_ENTRIES = 150;
const CURRENT_CACHES = [STATIC_CACHE, DYNAMIC_CACHE];
const ASSET_MANIFEST_URL = '/asset-manifest.json';
const NAVIGATION_TIMEOUT_MS = 4000;
const MATCH_OPTIONS = { ignoreVary: true };

const STATIC_ASSETS = [
  '/',
  '/index.html',
  '/manifest.json',
  '/images/plair_icon.png',
  '/images/plair_icon_192.png',
  '/images/plair_icon_512.png',
  '/images/default_background.webp',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE)
      .then((cache) => cache.addAll(STATIC_ASSETS))
      .then(() => precacheFromManifest({ prune: false }))
      .catch((error) => console.warn('[SW] Precache incomplete:', error))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((cacheNames) => Promise.all(
        cacheNames
          .filter((cacheName) => cacheName.startsWith('plair-') && !CURRENT_CACHES.includes(cacheName))
          .map((cacheName) => caches.delete(cacheName))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api') || url.pathname.startsWith('/ws') || url.pathname === ASSET_MANIFEST_URL) return;

  if (request.mode === 'navigate' || url.pathname === '/' || url.pathname === '/index.html') {
    event.respondWith(handleNavigationRequest(event));
    return;
  }

  if (url.pathname.startsWith('/assets/')) {
    event.respondWith(handleHashedAssetRequest(event));
    return;
  }

  event.respondWith(handleNetworkFirstRequest(event));
});

self.addEventListener('message', (event) => {
  const { data } = event;
  if (!data) return;

  if (data.type === 'SKIP_WAITING') {
    event.waitUntil(self.skipWaiting());
  } else if (data.type === 'PRECACHE') {
    event.waitUntil(precacheFromManifest({ prune: true }));
  } else if (data.type === 'CLEAR_AUDIO_CACHE') {
    event.waitUntil(caches.delete('plair-audio-v2'));
  }
});

function isCacheableResponse(response) {
  return !!response && response.status === 200 && response.type === 'basic';
}

function offlineResponse() {
  return new Response('', { status: 503, statusText: 'Offline' });
}

async function trimCache(cacheName, maxEntries) {
  const cache = await caches.open(cacheName);
  const keys = await cache.keys();
  if (keys.length <= maxEntries) return;
  await Promise.all(keys.slice(0, keys.length - maxEntries).map((key) => cache.delete(key)));
}

function putInCache(event, cacheName, key, response) {
  const update = caches.open(cacheName)
    .then((cache) => cache.put(key, response))
    .then(() => (cacheName === DYNAMIC_CACHE ? trimCache(DYNAMIC_CACHE, DYNAMIC_CACHE_MAX_ENTRIES) : undefined))
    .catch((error) => console.warn('[SW] Cache write failed:', error));
  event.waitUntil(update);
}

async function precacheFromManifest({ prune }) {
  let manifest;
  try {
    const response = await fetch(ASSET_MANIFEST_URL, { cache: 'no-store' });
    if (!response.ok) return;
    manifest = await response.json();
  } catch {
    return;
  }
  const urls = Array.isArray(manifest?.assets) ? manifest.assets.filter((u) => typeof u === 'string' && u.startsWith('/')) : [];
  if (!urls.length) return;

  const cache = await caches.open(STATIC_CACHE);
  await Promise.all(urls.map(async (url) => {
    if (await cache.match(url, MATCH_OPTIONS)) return;
    try {
      const response = await fetch(url, { cache: 'no-cache' });
      if (isCacheableResponse(response)) await cache.put(url, response);
    } catch {
      return;
    }
  }));

  if (prune) {
    const keep = new Set(urls);
    const keys = await cache.keys();
    await Promise.all(keys
      .filter((request) => {
        const path = new URL(request.url).pathname;
        return path.startsWith('/assets/') && !keep.has(path);
      })
      .map((request) => cache.delete(request)));
  }
}

async function handleNavigationRequest(event) {
  const network = fetch(event.request).then((response) => {
    if (isCacheableResponse(response)) putInCache(event, STATIC_CACHE, '/index.html', response.clone());
    return response;
  });
  event.waitUntil(network.then(() => undefined, () => undefined));

  const cached = await caches.match('/index.html', MATCH_OPTIONS) || await caches.match('/', MATCH_OPTIONS);
  if (!cached) return network.catch(() => offlineResponse());

  const timeout = new Promise((resolve) => setTimeout(() => resolve(null), NAVIGATION_TIMEOUT_MS));
  try {
    const response = await Promise.race([network, timeout]);
    if (response && response.ok) return response;
    return cached;
  } catch {
    return cached;
  }
}

async function handleHashedAssetRequest(event) {
  const { request } = event;
  const cached = await caches.match(request, MATCH_OPTIONS);
  if (cached) return cached;

  try {
    const response = await fetch(request);
    if (isCacheableResponse(response)) putInCache(event, STATIC_CACHE, request, response.clone());
    return response;
  } catch {
    return offlineResponse();
  }
}

async function handleNetworkFirstRequest(event) {
  const { request } = event;
  const range = request.headers.get('range');
  try {
    const response = await fetch(request);
    if (!range && isCacheableResponse(response)) putInCache(event, DYNAMIC_CACHE, request, response.clone());
    return response;
  } catch {
    const cached = await caches.match(request, MATCH_OPTIONS);
    if (!cached) return offlineResponse();
    return range ? rangeResponse(cached, range) : cached;
  }
}

async function rangeResponse(response, rangeHeader) {
  if (response.status !== 200) return response;
  const match = /^bytes=(\d*)-(\d*)$/.exec(rangeHeader.trim());
  const blob = await response.blob();
  const size = blob.size;
  if (!match || (match[1] === '' && match[2] === '')) {
    return new Response(null, { status: 416, headers: { 'Content-Range': `bytes */${size}` } });
  }

  let start;
  let end;
  if (match[1] === '') {
    const suffix = Number(match[2]);
    start = Math.max(0, size - suffix);
    end = size - 1;
  } else {
    start = Number(match[1]);
    end = match[2] === '' ? size - 1 : Math.min(Number(match[2]), size - 1);
  }
  if (start >= size || start > end) {
    return new Response(null, { status: 416, headers: { 'Content-Range': `bytes */${size}` } });
  }

  const headers = new Headers(response.headers);
  headers.set('Content-Range', `bytes ${start}-${end}/${size}`);
  headers.set('Content-Length', String(end - start + 1));
  headers.set('Accept-Ranges', 'bytes');
  return new Response(blob.slice(start, end + 1), { status: 206, statusText: 'Partial Content', headers });
}
