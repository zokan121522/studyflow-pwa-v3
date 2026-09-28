/* ============================== STUDYFLOW PWA — SERVICE WORKER ============================== */

const CACHE_NAME = 'studyflow-pwa-v1';
const ASSET_CACHE = 'studyflow-assets-v1';
const API_CACHE = 'studyflow-api-v1';

// Assets to cache on install (cache-first strategy)
const PRECACHE_ASSETS = [
  '/index.html',
  '/manifest.json',
  '/styles.css',
  '/app.js',
  '/shared/ui-common.js',
  '/shared/load-partial.js',
  '/features/studyflow/content-blocks.js',
  '/features/studyflow/content-blocks.css',
  '/features/studyflow/courses.js',
  '/features/studyflow/courses-api.js',
  '/features/studyflow/courses-blocks.js',
  '/features/studyflow/courses-sidebar.js',
  '/features/studyflow/courses.css',
  '/features/studyflow/studyflow-blocks.css',
];

// Maximum age for cached API responses (5 minutes)
const API_CACHE_MAX_AGE = 5 * 60 * 1000;

// Install event: cache basic assets
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(ASSET_CACHE)
      .then((cache) => {
        console.log('[SW] Precaching assets');
        return cache.addAll(PRECACHE_ASSETS.map(url => new Request(url, { credentials: 'same-origin' })));
      })
      .then(() => self.skipWaiting())
  );
});

// Activate event: cleanup old caches
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((cacheNames) => {
        return Promise.all(
          cacheNames
            .filter((name) => name !== ASSET_CACHE && name !== API_CACHE)
            .map((name) => {
              console.log('[SW] Deleting old cache:', name);
              return caches.delete(name);
            })
        );
      })
      .then(() => self.clients.claim())
  );
});

// Fetch event: route-based strategy
self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Only handle same-origin requests
  if (url.origin !== location.origin) {
    return;
  }

  // API calls: Network-first strategy
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(networkFirstApi(request));
    return;
  }

  // Static assets: Cache-first strategy
  if (isStaticAsset(url.pathname)) {
    event.respondWith(cacheFirstAsset(request, event));
    return;
  }

  // HTML/navigation: Network-first with offline fallback
  if (request.mode === 'navigate' || request.headers.get('accept')?.includes('text/html')) {
    event.respondWith(networkFirstHtml(request));
    return;
  }

  // Default: network-first
  event.respondWith(networkFirst(request));
});

// Cache-first strategy for static assets (CSS, JS, fonts, images)
async function cacheFirstAsset(request, event) {
  const cache = await caches.open(ASSET_CACHE);
  const cached = await cache.match(request);

  if (cached) {
    // Serve from cache, update in background
    event.waitUntil(updateAssetCache(request, cache));
    return cached;
  }

  try {
    const response = await fetch(request);
    if (response.ok) {
      cache.put(request, response.clone());
    }
    return response;
  } catch (error) {
    console.error('[SW] Asset fetch failed:', error);
    // Return offline fallback if available
    return new Response('Offline', { status: 503, statusText: 'Service Unavailable' });
  }
}

// Network-first strategy for API calls
async function networkFirstApi(request) {
  const cache = await caches.open(API_CACHE);
  
  try {
    const response = await fetch(request);
    if (response.ok) {
      // Cache successful responses with timestamp
      const cloned = response.clone();
      const responseWithTimestamp = new Response(await cloned.blob(), {
        status: cloned.status,
        statusText: cloned.statusText,
        headers: {
          ...Object.fromEntries(cloned.headers),
          'sw-cached-at': Date.now().toString()
        }
      });
      cache.put(request, responseWithTimestamp);
    }
    return response;
  } catch (error) {
    console.log('[SW] Network failed, trying cache for:', request.url);
    const cached = await cache.match(request);
    
    if (cached) {
      const cachedAt = cached.headers.get('sw-cached-at');
      const age = cachedAt ? Date.now() - parseInt(cachedAt) : Infinity;
      
      // Serve stale cache if not too old
      if (age < API_CACHE_MAX_AGE) {
        return cached;
      }
      
      // Too old - still serve but warn
      console.warn('[SW] Serving stale API cache:', request.url);
      return cached;
    }
    
    // No cache available
    return new Response(JSON.stringify({ error: 'Offline', message: 'No cached data available' }), {
      status: 503,
      headers: { 'Content-Type': 'application/json' }
    });
  }
}

// Network-first for HTML with offline fallback to cached index.html
async function networkFirstHtml(request) {
  const cache = await caches.open(ASSET_CACHE);
  
  try {
    const response = await fetch(request);
    if (response.ok) {
      cache.put(request, response.clone());
    }
    return response;
  } catch (error) {
    console.log('[SW] HTML fetch failed, trying cache');
    const cached = await cache.match(request);
    if (cached) return cached;
    
    // Fallback to index.html for SPA routing
    const indexCached = await cache.match('/index.html');
    if (indexCached) return indexCached;
    
    return new Response('Offline', { status: 503, statusText: 'Service Unavailable' });
  }
}

// Generic network-first fallback
async function networkFirst(request) {
  const cache = await caches.open(ASSET_CACHE);
  
  try {
    const response = await fetch(request);
    if (response.ok) {
      cache.put(request, response.clone());
    }
    return response;
  } catch (error) {
    const cached = await cache.match(request);
    if (cached) return cached;
    return new Response('Offline', { status: 503 });
  }
}

// Background update for cache-first assets
async function updateAssetCache(request, cache) {
  try {
    const response = await fetch(request);
    if (response.ok) {
      await cache.put(request, response);
    }
  } catch (error) {
    // Ignore background update failures
  }
}

// Check if path is a static asset
function isStaticAsset(pathname) {
  return /\.(css|js|png|jpg|jpeg|gif|svg|woff|woff2|ttf|eot|ico|webp|avif|map)$/i.test(pathname);
}

// Listen for messages from clients (e.g., skipWaiting)
self.addEventListener('message', (event) => {
  if (event.data === 'skipWaiting') {
    self.skipWaiting();
  }
  
  if (event.data === 'clearCache') {
    event.waitUntil(
      caches.keys().then(names => Promise.all(names.map(name => caches.delete(name))))
    );
  }
});

console.log('[SW] Service Worker loaded');
