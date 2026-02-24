const STATIC_CACHE = 'plair-static-v2';
const DYNAMIC_CACHE = 'plair-dynamic-v2';
const AUDIO_CACHE = 'plair-audio-v2';

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
    })
  );
});

self.addEventListener('activate', (event) => {
  console.log('[SW] Activating service worker...');
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames.map((cacheName) => {
          if (cacheName !== STATIC_CACHE && cacheName !== DYNAMIC_CACHE && cacheName !== AUDIO_CACHE) {
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
    event.respondWith(handleAudioRequest(event));
    return;
  }

  if (url.pathname.startsWith('/api') || url.pathname.startsWith('/ws')) {
    return;
  }

  event.respondWith(
    caches.match(request).then((cachedResponse) => {
      if (cachedResponse) {
        return cachedResponse;
      }

      return fetch(request).then((response) => {
        if (!response || response.status !== 200 || response.type === 'error') {
          return response;
        }

        const responseToCache = response.clone();

        const cacheUpdate = caches.open(DYNAMIC_CACHE).then((cache) => {
          return cache.put(request, responseToCache);
        });
        event.waitUntil(cacheUpdate);

        return response;
      }).catch(() => {
        if (request.destination === 'document') {
          return caches.match('/index.html');
        }
        if (request.destination === 'audio' || url.pathname.includes('/audio/')) {
          return new Response('', { status: 503, statusText: 'Offline' });
        }
        return new Response('', { status: 503, statusText: 'Offline' });
      });
    })
  );
});

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