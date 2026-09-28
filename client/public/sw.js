const STATIC_CACHE = 'plair-static-v4';
const DYNAMIC_CACHE = 'plair-dynamic-v3';
const AUDIO_CACHE = 'plair-audio-v2';
const DYNAMIC_CACHE_MAX_ENTRIES = 150;
const CURRENT_CACHES = [STATIC_CACHE, DYNAMIC_CACHE, AUDIO_CACHE];

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
  console.log('[SW] Installing service worker...');
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) => {
      console.log('[SW] Caching static assets');
      return cache.addAll(STATIC_ASSETS);
    }).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  console.log('[SW] Activating service worker...');
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames.map((cacheName) => {
          if (cacheName.startsWith('plair-') && !CURRENT_CACHES.includes(cacheName)) {
            console.log('[SW] Deleting old cache:', cacheName);
            return caches.delete(cacheName);
          }
        })
      );
    }).then(() => {
      console.log('[SW] Service worker activated');
      return self.clients.claim();
    })
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  if (request.method !== 'GET') {
    return;
  }

  if (url.pathname.startsWith('/api/stream/')) {
    if (url.searchParams.get('render') === '1') {
      event.respondWith(fetch(request));
      return;
    }
    event.respondWith(handleAudioRequest(event));
    return;
  }

  if (url.pathname.startsWith('/api') || url.pathname.startsWith('/ws')) {
    return;
  }

  if (url.origin !== self.location.origin) {
    return;
  }

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

function isCacheableResponse(response) {
  return response && response.status === 200 && response.type !== 'error' && response.type !== 'opaque';
}

async function trimCache(cacheName, maxEntries) {
  const cache = await caches.open(cacheName);
  const keys = await cache.keys();
  if (keys.length <= maxEntries) return;
  await Promise.all(keys.slice(0, keys.length - maxEntries).map((key) => cache.delete(key)));
}

function putInCache(event, cacheName, request, response) {
  const cacheUpdate = caches.open(cacheName)
    .then((cache) => cache.put(request, response))
    .then(() => (cacheName === DYNAMIC_CACHE ? trimCache(DYNAMIC_CACHE, DYNAMIC_CACHE_MAX_ENTRIES) : undefined))
    .catch((error) => console.warn('[SW] Cache write failed:', error));
  event.waitUntil(cacheUpdate);
}

function offlineResponse() {
  return new Response('', { status: 503, statusText: 'Offline' });
}

async function handleNavigationRequest(event) {
  const { request } = event;
  try {
    const response = await fetch(request);
    if (isCacheableResponse(response)) {
      putInCache(event, STATIC_CACHE, '/index.html', response.clone());
    }
    return response;
  } catch {
    const cached = await caches.match('/index.html') || await caches.match('/');
    return cached || offlineResponse();
  }
}

async function handleHashedAssetRequest(event) {
  const { request } = event;
  const cached = await caches.match(request);
  if (cached) return cached;

  try {
    const response = await fetch(request);
    if (isCacheableResponse(response)) {
      putInCache(event, STATIC_CACHE, request, response.clone());
    }
    return response;
  } catch {
    return offlineResponse();
  }
}

async function handleNetworkFirstRequest(event) {
  const { request } = event;
  try {
    const response = await fetch(request);
    if (isCacheableResponse(response)) {
      putInCache(event, DYNAMIC_CACHE, request, response.clone());
    }
    return response;
  } catch {
    const cached = await caches.match(request);
    return cached || offlineResponse();
  }
}

async function handleAudioRequest(event) {
  const request = event.request;
  const url = new URL(request.url);

  try {
    const cachedResponse = await caches.match(request);

    if (cachedResponse) {
      console.log('[SW] Audio cache HIT:', url.pathname);
      return cachedResponse;
    }

    console.log('[SW] Audio cache MISS, fetching:', url.pathname);

    const response = await fetch(request);

    if (response && response.status === 200) {
      const responseToCache = response.clone();

      const cacheUpdate = caches.open(AUDIO_CACHE).then((cache) => {
        console.log('[SW] Cached audio stream:', url.pathname);
        return cache.put(request, responseToCache);
      });
      event.waitUntil(cacheUpdate);
    }

    return response;
  } catch (error) {
    console.error('[SW] Audio request failed:', error);

    const cachedResponse = await caches.match(request);
    if (cachedResponse) {
      console.log('[SW] Returning cached audio (offline):', url.pathname);
      return cachedResponse;
    }

    return new Response('Audio not available offline', {
      status: 503,
      statusText: 'Service Unavailable'
    });
  }
}

self.addEventListener('message', (event) => {
  const { data } = event;

  if (data && data.type === 'SKIP_WAITING') {
    event.waitUntil(self.skipWaiting());
  }

  if (data && data.type === 'CACHE_AUDIO') {
    const { url } = data;
    event.waitUntil(
      caches.open(AUDIO_CACHE).then(cache => {
        return fetch(url).then(response => {
          if (response.status === 200) {
            return cache.put(url, response);
          }
        });
      })
    );
  }

  if (data && data.type === 'DELETE_CACHED_AUDIO') {
    const { url } = data;
    event.waitUntil(
      caches.open(AUDIO_CACHE).then(cache => cache.delete(url))
    );
  }

  if (data && data.type === 'CLEAR_AUDIO_CACHE') {
    event.waitUntil(
      caches.delete(AUDIO_CACHE).then(() => {
        console.log('[SW] Cleared audio cache');
      })
    );
  }

  if (data && data.type === 'GET_CACHE_SIZE') {
    event.waitUntil(
      caches.open(AUDIO_CACHE).then(async cache => {
        const keys = await cache.keys();
        const sizes = await Promise.all(
          keys.map(async req => {
            const response = await cache.match(req);
            if (response) {
              const blob = await response.blob();
              return blob.size;
            }
            return 0;
          })
        );
        const totalSize = sizes.reduce((sum, size) => sum + size, 0);

        self.clients.matchAll().then(clients => {
          clients.forEach(client => {
            client.postMessage({
              type: 'CACHE_SIZE_RESPONSE',
              size: totalSize,
              count: keys.length
            });
          });
        });
      })
    );
  }
});
