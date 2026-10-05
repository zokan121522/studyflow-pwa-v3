/* ============================== STUDYFLOW PWA — SERVICE WORKER ============================== */

const CACHE_NAME = 'studyflow-pwa-v2';
// v27: the generated NotebookLM test must render as a quiz. Installed PWAs
// were still serving the pre-fix ai.js, where the test landed as raw JSON in
// a `content` block, so a reload looked like the bug was unfixed. Bump so
// the precache is refetched.
// v24: content-blocks.js now sanitises through html-sanitizer.js, so embedded
// <img>/<audio> render instead of showing as escaped text. Bump so installed
// PWAs stop serving the old renderer.
// v20 (#8 fix): v2Restore modal had `onConfirm` vs `onDone` typo — the
// "Migrar a v3" button threw on click. Fixed + added a deferred-data
// warning. Bump so installed PWAs stop serving the broken modal.
// v29: course cards are flex rows again, so the drag grip sits beside the
// title instead of above it. Both stylesheets are precached, so installed
// PWAs would keep serving the broken layout until the cache is rebuilt.
// v30: imported/generated courses rendered stale until a full reload.
// courses-api.js now expires its per-session cache (TTL + revalidate on
// tab focus) and pdf-import.js stops bypassing the cache layer, but both are
// precached, so installed PWAs need the bump to pick any of it up.
// v35: the five insert-flow polish fixes (#8c501f4) touched four precached
// assets — ai.js, courses.js, markdown-editor.js and studyflow-editor.css —
// and v34 was left in place. Static assets are cache-first, and the service
// worker only re-installs when sw.js itself changes, so every installed PWA
// kept serving the pre-fix code with no update prompt: the "Nueva versión
// disponible" banner in app.js never fired because no new worker was ever
// installed. Bump so the precache is refetched. tests/test_sw_asset_version.py
// now fails the build if a precached asset changes without this bump, so this
// cannot silently recur.
// v36: por_tema now splits a NotebookLM YouTube run into one markdown block
// per `## ` section (ai.js), and the nav no longer prefixes a 📝 to blocks
// whose title already carries an emoji (courses-sidebar.js). Both are
// precached, so installed PWAs need the bump to pick them up. The digest
// guard added in v35 caught this omission at build time, which is the whole
// point of having added it.
// v37: courses-sidebar.js only. The first attempt at the duplicate-icon fix
// matched a hand-written list of eight emoji, and real titles proved it
// incomplete — 📊 🎵 ❓ ✨ 🌙 all still rendered as "📄 📊", "❓ ❓". Now keyed
// off Unicode Extended_Pictographic, so no future title prefix can outrun it.
// +78: pill de 'sonando ahora' en el header (titulo + contador + play/stop)
// +79: formatTimer trunca los segundos (EXTRACT(EPOCH) devolvia decimales)
// v81: OpenZen stream panel labelled as OpenZen (startStreamPoll takes an
// optional title override) and its 404/405 fallback matches the prose error
// text window.API actually throws. Both files are precached, and editing a
// precached file is NOT enough on its own: the SW only re-precaches on
// install, so without this bump installed PWAs keep serving the old JS.
// v83: the update notice is now mandatory. The worker waits in
// registration.waiting and app.js announces it, instead of skipWaiting()
// activating silently mid-session. v81/v82 never left this machine — they
// were local iterations while diagnosing why the OpenZen fix had reached the
// server but not the browser.
// v85: propagates the noVNC login modal (notebooklm-settings.js +
// study-scheduler.css) edited since v84. The NotebookLM active-profile root
// fix itself is backend-only, but these precached assets only refresh on
// install — the bump is what actually delivers them to installed PWAs.
// v86: propagates the topic-level "➕ Añadir" bar (courses.js) and the removal
// of the per-block add group + hidden add types (ai.js, courses-blocks.js).
// v87: propagates R4 image blocks — image-upload.js is brand new and must be
// precached (the 🖼 chip resolves App.ImageUpload at click time), plus the
// courses-blocks.js image render branch and studyflow-blocks.css.
// Editing a precached .js without this bump ships code that no installed PWA
// can load — see tests/test_sw_asset_version.py.
// v88: R5 — the JSON question editor (quiz-json-editor.js is new and must be
// precached, since quiz-embed.js resolves App.QuizJsonEditor at open time),
// plus the rewritten quiz-embed.js manager and the editor's CSS.
// Editing a precached .js without this bump ships code that no installed PWA
// can load — see tests/test_sw_asset_version.py.
// v89: added backup-selector.js (the selective backup modal, loaded on
// demand when #btn-backup is clicked).
// v90: added the three addon modules that index.html loads with <script src>
//      but that were missing here (knowledge-pipeline, english-grammar,
//      vocabulary). Offline they silently failed to load, taking "Gen.
//      Contenido", "English Exercises" and "Vocabulary Suite" with them.
//      Bumping the name is what makes clients drop the stale v89 asset cache;
//      the activate handler deletes every cache that is not ASSET_CACHE.
// v91: the three assets v89/v90 added to the precache never got a digest pin
// in sw_asset_pins.json, so test_asset_cache_version_tracks_a_content_digest
// had been red since the bump to v90. This bump adds the pin and turns the
// suite green. No precached asset changed.
// v92: index.html changed, so it needed a new cache name. The dashboard's
//      "Guia y tutoriales" card dropped target="_blank": /landing/ is inside
//      the manifest scope, so the blank attribute sent the guide to a browser
//      tab and out of the installed PWA. The <video controls> already offer
//      fullscreen, which is why no allowfullscreen attribute was needed.
// v93: the five fixes of the pre-release audit. index.html is precached, so
//      changing it needs a new name whatever else ships.
//      - courses-blocks painted a second 📝 on every markdown block; the
//        icon now resolves through App.CoursesSidebar.getBlockIcon.
//      - agenda-calendars.js no longer PUTs a list it cannot fully
//        represent, which was silently deleting saved calendars.
//      - openzen_retry_chunk's arguments were in the wrong order, so every
//        "reiniciar sección" 500'd.
//      - _coerce_ids(None) raised TypeError (500) instead of ValueError (400).
//      - /quiz/answer now validates like /quiz/answers does.
// v94: landing sin placeholders 03/13 + 4 videos regrabados con difuminado
// v95: tutoriales regrabados con datos ficticios
// v96: durata dels tutoriales sincronitzada
// v97: session timers. _getTimerSeconds() took no prefix, so pause time was
// saved as elapsed time and effective time read 0m; and the create path never
// sent the timer fields at all.
const ASSET_CACHE = 'studyflow-assets-v97';
const API_CACHE = 'studyflow-api-v1';

// Assets to cache on install (cache-first strategy).
// v8 (S7b-A): added features/studyflow/pdf-import-stream.js.
const PRECACHE_ASSETS = [
  '/index.html',
  '/manifest.json',
  '/styles.css',
  '/app.js',
  '/backup-selector.js',
  '/shared/ui-common.js',
  '/shared/load-partial.js',
  '/features/studyflow/content-blocks.js',
  '/features/studyflow/content-blocks.css',
  '/features/studyflow/courses.js',
  '/features/studyflow/courses-api.js',
  '/features/studyflow/courses-blocks.js',
  '/features/studyflow/courses-dnd.js',
  '/features/studyflow/courses-dashboard.js',
  '/features/studyflow/openzen-settings.js',
  '/features/studyflow/courses-sidebar.js',
  '/features/studyflow/markdown-editor.js',
  '/features/studyflow/courses.css',
  '/features/studyflow/studyflow-blocks.css',
  '/features/studyflow/courses-sidebar-blocks.css',
  '/features/studyflow/studyflow-editor.css',
  // S7: pdf viewer
  '/features/studyflow/pdf-viewer.js',
  '/features/studyflow/pdf-viewer-annots.js',
  '/features/studyflow/pdf-viewer.css',
  // S7b: unified PDF + SCORM import popover
  '/features/studyflow/pdf-import.js',
  // S7b-A: SSE pseudo-terminal for the SCORM import
  '/features/studyflow/pdf-import-stream.js',
  // R4: image block upload (picker + FormData + block creation)
  '/features/studyflow/image-upload.js',
  // R5: exercise questions as JSON (editor + preview; mounted by quiz-embed.js)
  '/features/studyflow/quiz-json-editor.js',
  // S7b-B: floating study menu + SCORM credentials settings
  '/features/studyflow/study-scheduler.js',
  '/features/studyflow/study-scheduler.css',
  '/features/studyflow/scorm-settings.js',
  // 7.2: NotebookLM settings panel
  '/features/studyflow/notebooklm-settings.js',
  // 7.7: AI ✨ Generate — toolbar manifests/click/counters + generators
  // + streaming task poll/modal + toolbar styles.
  '/features/studyflow/ai-tasks.js',
  '/features/studyflow/ai-notebooklm.js',
  '/features/studyflow/ai-openzen.js',
  '/features/studyflow/ai-config-modal.js',
  '/features/studyflow/ai-modals.js',
  '/features/studyflow/ai.js',
  '/features/studyflow/ai.css',
  '/vendor/pdfjs/pdf.min.js',
  '/vendor/pdfjs/pdf.worker.min.js',
  // SA.2: addons foundation (registry + marketplace). The S4.7 sticky
  // floating toolbar was removed in V3 — see git history.
  '/features/addons/addons-core.js',
  '/features/addons/addons-manager.js',
  '/features/addons/addons.css',
  // #7/#8: local Backup (ZIP export) + Restore (v3 and v2 migration).
  // These were missing from the precache, so the toolbar buttons only worked
  // while online — unacceptable for a headline feature of an offline PWA.
  '/shared/backup-restore.js',
  '/shared/session-miniplayer.js',
  '/shared/v2-restore-modal.js',
  // S5: quiz ecosystem. Same reason as above: index.html loads these with
  // <script src>, so a single missing entry means no test, no failed pool
  // and no summary while offline — the feature is simply gone.
  '/features/quiz/quiz-api.js',
  '/features/quiz/quiz-runner.js',
  '/features/quiz/quiz-pool.js',
  '/features/quiz/quiz-summary.js',
'/features/quiz/quiz-center.js',
    '/features/quiz/quiz.css',
    // v90: addon modules loaded via <script src> in index.html. They were
    // absent from the precache, so an offline launch loaded the app but not
    // these — the buttons were still rendered by ai.js and their handlers were
    // undefined, i.e. a feature that looks present and does nothing.
    '/features/studyflow/knowledge-pipeline.js',
    '/features/studyflow/english-grammar.js',
    '/features/studyflow/vocabulary.js',
  ];

// Maximum age for cached API responses (5 minutes)
const API_CACHE_MAX_AGE = 5 * 60 * 1000;

// Install event: cache basic assets
//
// Deliberately NOT cache.addAll(). addAll() is atomic: a single 404 rejects
// the whole promise and leaves the precache completely empty, so one stale
// entry takes offline support down with it and nothing in the app notices —
// the SW installs, the update banner still fires, and only the failure to
// precache is invisible. That is exactly what happened: /features/studyflow/
// courses-notes.js was listed here but never existed in the repo, so the
// precache had been failing silently for a long time and the cache we
// inspected had in fact been filled by the runtime cache-first path instead.
//
// allSettled keeps every asset that does resolve and reports the rest.
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(ASSET_CACHE)
      .then((cache) => {
        console.log('[SW] Precaching assets');
        return Promise.allSettled(
          PRECACHE_ASSETS.map(url =>
            cache.add(new Request(url, { credentials: 'same-origin' }))
          )
        ).then((results) => {
          const failed = PRECACHE_ASSETS.filter((_, i) => results[i].status === 'rejected');
          if (failed.length) {
            console.error('[SW] Precache failed for', failed.length, 'asset(s):', failed);
          } else {
            console.log('[SW] Precached', PRECACHE_ASSETS.length, 'assets');
          }
        });
      })
  );
});

// NO skipWaiting() in the install handler, deliberately.
//
// skipWaiting() here makes the new worker activate the instant it finishes
// precaching — mid-session, under the user's feet. The page keeps running the
// old JS while the cache underneath it has already been swapped, which is
// version skew: one codebase, two versions in play. That is exactly how a
// fix reached the server and never reached the browser here: assets changed,
// /sw.js did not, so no install ran and the old cache kept serving.
//
// So the new worker waits in `registration.waiting` and app.js shows
// "Nueva versión disponible". The user clicks, we postMessage('skipWaiting')
// (see the message listener at the bottom of this file), and only then does
// activate run and delete the old caches. Assets change only at a reload the
// user chose.
//
// The install handler above must therefore resolve to undefined. Adding a
// skipWaiting() back silently disables the update notice, and
// test_sw_asset_version.py asserts its absence for exactly that reason.

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

  // API calls: Network-first strategy.
  //
  // Two requests must reach the origin untouched (S7b-A):
  //  - Accept: text/event-stream (SCORM import progress). The SW's
  //    networkFirstApi awaits response.blob(), which buffers the whole
  //    body and turns the stream into a single late chunk — the 524 story
  //    all over again.
  //  - non-GET. Cache writes are only meaningful for GETs anyway.
  if (url.pathname.startsWith('/api/')) {
    const accept = (request.headers.get('accept') || '').toLowerCase();
    if (request.method !== 'GET' || accept.includes('text/event-stream')) {
      return;
    }
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
