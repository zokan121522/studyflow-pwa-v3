/* ============================== STUDYFLOW PWA — APP BOOTSTRAP ============================== */

// ─── Configuration ────────────────────────────────────────────────────
const API_URL = (() => {
  // Mismo origen: el backend (Flask) sirve la PWA y el /api juntos.
  // Solo en dev local con http.server aparte (:3000) se apunta a :8082.
  if (location.port === '3000') return 'http://localhost:8082/api';
  return '/api';
})();


// ─── Service Worker Registration ────────────────────────────────────
//
// The worker waits instead of skipWaiting()ing on install, so an update sits
// in `registration.waiting` until the user accepts it. Two things make that
// notice reliable, and both were missing before:
//
//  1. The browser can fire `updatefound` BEFORE `register()` resolves. A
//     listener attached inside `.then()` therefore never sees it, and the
//     update lands silently. So we also look at `registration.waiting`.
//  2. Reloading straight after postMessage('skipWaiting') races the
//     activation: the page can come back up still controlled by the OLD
//     worker, which then serves the old cache again. Reload on
//     `controllerchange` instead — that is the signal the new worker is live.
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    let reloading = false;
    navigator.serviceWorker.addEventListener('controllerchange', () => {
      if (reloading) return;
      reloading = true;
      window.location.reload();
    });

    navigator.serviceWorker.register('/sw.js', { scope: '/' })
      .then((registration) => {
        console.log('[PWA] Service Worker registered:', registration.scope);

        const watchInstalling = (worker) => {
          if (!worker) return;
          worker.addEventListener('statechange', () => {
            // 'installed' while an older worker still controls the page means
            // a new version is ready and waiting for the user.
            if (worker.state === 'installed' && navigator.serviceWorker.controller) {
              console.log('[PWA] New version available');
              showUpdateNotification();
            }
          });
        };

        registration.addEventListener('updatefound', () => {
          watchInstalling(registration.installing);
        });

        // The case `updatefound` misses: the update was already discovered
        // during registration and the worker is sitting in `waiting`.
        if (registration.waiting && navigator.serviceWorker.controller) {
          console.log('[PWA] New version already waiting');
          showUpdateNotification();
        }

        // Returning to the tab is the moment an update is most useful, and the
        // cheapest moment to notice a stale worker.
        document.addEventListener('visibilitychange', () => {
          if (document.visibilityState !== 'visible') return;
          registration.update().catch(() => {});
          if (registration.waiting && navigator.serviceWorker.controller) {
            showUpdateNotification();
          }
        });
      })
      .catch((error) => {
        console.error('[PWA] Service Worker registration failed:', error);
      });
  });
}

let _updateNoticeShown = false;
function showUpdateNotification() {
  // updatefound, the post-register check and visibilitychange can all land on
  // the same update; without this the user gets a stack of identical banners.
  if (_updateNoticeShown) return;
  _updateNoticeShown = true;

  const notification = document.createElement('div');
  notification.className = 'update-notification';
  notification.innerHTML = `
    <span>🔄 Nueva versión disponible</span>
    <button id="update-btn">Actualizar</button>
  `;
  document.body.appendChild(notification);

  // No window.location.reload() here on purpose: the reload belongs to the
  // controllerchange listener, which fires once the new worker is actually in
  // control.
  document.getElementById('update-btn').addEventListener('click', async () => {
    const regs = await navigator.serviceWorker.getRegistrations();
    const reg = regs && regs[0] ? regs[0] : null;
    const target = reg && reg.waiting ? reg.waiting : (navigator.serviceWorker.controller || null);
    if (target) {
      target.postMessage('skipWaiting');
    }
  });
}


// ─── Online/Offline Detection ───────────────────────────────────────
function updateOnlineStatus() {
  const indicator = document.getElementById('offline-indicator');
  if (indicator) {
    indicator.classList.toggle('hidden', navigator.onLine);
  }
}

window.addEventListener('online', updateOnlineStatus);
window.addEventListener('offline', updateOnlineStatus);


// ─── PWA Install Prompt ─────────────────────────────────────────────
let deferredPrompt = null;

window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault();
  deferredPrompt = e;
  showInstallButton();
});

function showInstallButton() {
  if (window.matchMedia('(display-mode: standalone)').matches) return;
  if (localStorage.getItem('installPromptDismissed')) return;
  
  const btn = document.createElement('button');
  btn.id = 'install-btn';
  btn.className = 'install-btn';
  btn.innerHTML = '📥 Instalar App';
  btn.addEventListener('click', async () => {
    if (!deferredPrompt) return;
    deferredPrompt.prompt();
    const { outcome } = await deferredPrompt.userChoice;
    console.log('[PWA] Install outcome:', outcome);
    if (outcome === 'accepted') {
      btn.remove();
    } else {
      localStorage.setItem('installPromptDismissed', 'true');
    }
    deferredPrompt = null;
  });
  
  document.body.appendChild(btn);
}


// ─── Auth Token Management ──────────────────────────────────────────
const Auth = {
  getToken() {
    return localStorage.getItem('auth_token');
  },
  
  setToken(token) {
    localStorage.setItem('auth_token', token);
  },
  
  clearToken() {
    localStorage.removeItem('auth_token');
  },
  
  getHeaders() {
    const headers = { 'Content-Type': 'application/json' };
    const token = this.getToken();
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }
    return headers;
  }
};


// ─── API Client ──────────────────────────────────────────────────────
async function apiRequest(path, options = {}) {
  const url = `${API_URL}${path}`;
  const defaultOptions = {
    headers: Auth.getHeaders(),
    credentials: 'include'
  };
  
  const config = {
    ...defaultOptions,
    ...options,
    headers: {
      ...defaultOptions.headers,
      ...(options.headers || {})
    }
  };
  
  try {
    const response = await fetch(url, config);
    
    // Handle 401 - token expired
    if (response.status === 401) {
      Auth.clearToken();
      // Dispatch event for app to handle (redirect to login)
      window.dispatchEvent(new CustomEvent('auth:unauthorized'));
      throw new Error('Unauthorized');
    }
    
    if (!response.ok) {
      const error = await response.json().catch(() => ({ message: response.statusText }));
      throw new Error(error.message || `HTTP ${response.status}`);
    }
    
    // Handle 204 No Content
    if (response.status === 204) {
      return null;
    }
    
    return await response.json();
  } catch (error) {
    if (error.message === 'Unauthorized') throw error;
    console.error(`[API] ${options.method || 'GET'} ${path}:`, error);
    throw error;
  }
}

const API = {
  get: (path) => apiRequest(path),
  post: (path, body) => apiRequest(path, { method: 'POST', body: JSON.stringify(body) }),
  put: (path, body) => apiRequest(path, { method: 'PUT', body: JSON.stringify(body) }),
  patch: (path, body) => apiRequest(path, { method: 'PATCH', body: JSON.stringify(body) }),
  del: (path) => apiRequest(path, { method: 'DELETE' })
};


// ─── Basic Router / Dispatcher ──────────────────────────────────────
const Router = {
  routes: new Map(),
  
  add(path, handler) {
    this.routes.set(path, handler);
  },
  
  navigate(path) {
    window.history.pushState({}, '', path);
    this.handle(path);
  },
  
  handle(path) {
    const handler = this.routes.get(path) || this.routes.get('*');
    if (handler) handler();
  },
  
  init() {
    window.addEventListener('popstate', () => this.handle(window.location.pathname));
    this.handle(window.location.pathname);
  }
};


// ─── App State ──────────────────────────────────────────────────────
//
// CRITICAL: App must be ANCHORED to the shared `window.App` namespace
// created earlier by agenda-glue.js (UI, ContentBlocks, Courses,
// Auth, STATE…). Reassigning `window.App = App` would clobber every
// sub-namespace and break agenda-core.js:147 `App.UI.displayTitle(...)`,
// leaving the Agenda CENTER column empty.
//
// Fix: define members in a definition object, then MERGE them onto the
// pre-existing window.App via Object.assign. `App === window.App` so
// every `App.X` reference below keeps working unchanged.
const _appDef = {
  state: {
    currentUser: null,
    currentWeek: null,
    currentDay: null,
    activeTab: 'agenda',
    expandedCourseId: null,
    selectedBlockId: null,
    selectedTopicId: null,
    habits: { showAll: false, expanded: false, weekData: null, weekDays: null }
  },

  modules: {},

  registerModule(name, module) {
    this.modules[name] = module;
  },

  getModule(name) {
    return this.modules[name];
  },

  async initAuth() {
    const token = Auth.getToken();
    if (token) {
      try {
        const data = await API.get('/auth/me');
        this.state.currentUser = data.user;
        return true;
      } catch (error) {
        Auth.clearToken();
        return false;
      }
    }
    return false;
  }
};


// Expose globally for feature modules — MERGE into shared window.App,
// do NOT replace it (agenda-glue.js / agenda modules already populated it).
const App = Object.assign(window.App = window.App || {}, _appDef);
window.Router = Router;
window.API = API;
window.Auth = Auth;
window.API_URL = API_URL;

// ─── Register Feature Modules ──────────────────────────────
App.registerModule('Agenda', window.AgendaModule || null);
App.registerModule('Courses', window.CoursesModule || null);


// ══════════════════════════════════════════════════════════════════
// Dashboard: Habit Grid (21-day mini heatmap) — v2 parity
// ══════════════════════════════════════════════════════════════════
// Port of v2's `window.App.Dashboard` (studyflow-hub frontend/app.js). The
// dashboard home shows the same widgets as v2: this habit heatmap + the
// read-only agenda side panel (mounted by renderDashboard below). Kept in
// app.js because v2 keeps it here too and it is ~60 lines of DOM string.
App.Dashboard = (function () {
  "use strict";

  function _isWeekend(dateStr) {
    const d = new Date(dateStr + "T12:00:00");
    const day = d.getDay();
    return day === 0 || day === 6;
  }

  /** Fetch habits data and render the 21-day grid into #dhg-body. */
  async function renderHabitGrid() {
    const body = document.getElementById("dhg-body");
    if (!body) return;

    try {
      const resp = await API.get("/habits/grid?days=21");
      const { columns, grid } = resp || {};
      if (!columns || !columns.length) {
        body.innerHTML = `<div style="text-align:center;padding:16px;color:var(--text-muted);font-size:12px;">
          Sin hábitos aún. <a href="#" id="dhg-empty-link" style="color:var(--primary);">Crear hábitos</a>
        </div>`;
        document.getElementById("dhg-empty-link")?.addEventListener("click", (e) => {
          e.preventDefault();
          const agendaBtn = document.querySelector('.tab-btn[data-tab="agenda"]');
          if (agendaBtn) agendaBtn.click();
        });
        return;
      }

      let html = "";

      // Header row with day-of-month labels
      html += `<div class="dhg-row dhg-header-row">`;
      html += `<div class="dhg-label-col" title="Hábitos"></div>`;
      for (const day of grid) {
        const d = String(day.date).slice(8, 10); // DD
        const isWeekend = _isWeekend(day.date);
        html += `<div class="dhg-cell dhg-day-label ${isWeekend ? "dhg-weekend" : ""}" title="${day.date}">${d}</div>`;
      }
      html += `<div class="dhg-progress-col" title="% completado">%</div>`;
      html += `</div>`;

      // One row per habit column
      for (const col of columns) {
        html += `<div class="dhg-row">`;
        html += `<div class="dhg-label-col" title="${col.label}">${String(col.label).slice(0, 4)}</div>`;
        let checkedCount = 0;
        for (const day of grid) {
          const val = day.habits ? day.habits[col.key] : false;
          const checked = val === true || val === "true";
          if (checked) checkedCount++;
          const cls = checked ? "dhg-cell dhg-on" : "dhg-cell dhg-off";
          html += `<div class="${cls}" title="${day.date}: ${col.label} = ${checked ? "✅" : "❌"}"></div>`;
        }
        const pct = Math.round((checkedCount / grid.length) * 100);
        html += `<div class="dhg-progress-col" title="${pct}% de días">${pct > 0 ? pct + "%" : ""}</div>`;
        html += `</div>`;
      }

      // Overall progress row
      html += `<div class="dhg-row dhg-overall-row">`;
      html += `<div class="dhg-label-col" title="Global" style="font-weight:600;">✨</div>`;
      for (const day of grid) {
        const p = day.progress || 0;
        let cls = "dhg-cell";
        if (p >= 80) cls += " dhg-on";
        else if (p >= 40) cls += " dhg-mid";
        else if (p > 0) cls += " dhg-low";
        else cls += " dhg-off";
        html += `<div class="${cls}" title="${day.date}: ${p}%"></div>`;
      }
      html += `<div class="dhg-progress-col"></div>`;
      html += `</div>`;

      body.innerHTML = html;
    } catch (e) {
      body.innerHTML = `<div style="text-align:center;padding:16px;color:var(--text-muted);font-size:12px;">
        Error al cargar hábitos: ${e.message}
      </div>`;
    }
  }

  return { renderHabitGrid };
})();


// ─── Auth Event Listener ────────────────────────────────────────────
window.addEventListener('auth:unauthorized', () => {
  console.log('[Auth] Token expired or invalid, redirecting to login');
  App.state.currentUser = null;
  // Feature modules can listen for this event
});


// ─── Tab Navigation ─────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  const tabButtons = document.querySelectorAll('.tab-btn');
  const viewPanels = document.querySelectorAll('.view-panel');
  const mainEl = document.querySelector('.main');

  // Header buttons: 📦 opens the selective backup modal, ⬆️ picks a ZIP.
  // Wiring lives here (function is hoisted, defined further down).
  initBackupRestore();

  // ─── Sync main grid visibility ────────────────────────────────
  // The dashboard lives OUTSIDE .main (v2 parity). Both are flex:1
  // children of #app, so when the dashboard is shown the main grid must
  // be hidden — otherwise the empty grid steals half the viewport.
  const syncMainWithDashboard = () => {
    if (!mainEl) return;
    const dashShown = !document.getElementById('view-dashboard')?.classList.contains('hidden');
    mainEl.classList.toggle('hidden', dashShown);
    // Option A: with the dashboard visible the WHOLE PAGE scrolls (not the
    // .dashboard-container block). The CSS keys off this body class.
    document.body.classList.toggle('dashboard-view', dashShown);
  };

  // ─── Render the dashboard home (v2 parity) ────────────────────
  // The home is more than static markup: v2 fills it with the 21-day habit
  // heatmap and the read-only agenda side panel. We (re)render both every
  // time the dashboard comes into view so they are never stale.
  const renderDashboard = () => {
    if (App.Dashboard && App.Dashboard.renderHabitGrid) {
      App.Dashboard.renderHabitGrid();
    }
    const panelEl = document.getElementById('dashboard-agenda-panel');
    if (panelEl && window.App?.Agenda?.buildAgendaSidePanel) {
      window.App.Agenda.buildAgendaSidePanel(panelEl, {
        readOnly: true,
        compact: true,
        idPrefix: 'dash-',
      });
    }
    if (window.App?.CoursesDashboard?.renderFavoritesRow) {
      window.App.CoursesDashboard.renderFavoritesRow();
    }
  };

  // "Ir a Hábitos →" on the dashboard jumps to the Agenda tab.
  document.getElementById('dhg-view-all')?.addEventListener('click', (e) => {
    e.preventDefault();
    const agendaBtn = document.querySelector('.tab-btn[data-tab="agenda"]');
    if (agendaBtn) agendaBtn.click();
  });
  
  tabButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      const tab = btn.dataset.tab;
      
      tabButtons.forEach(b => {
        b.classList.remove('active');
        b.setAttribute('aria-selected', 'false');
      });
      btn.classList.add('active');
      btn.setAttribute('aria-selected', 'true');
      
      viewPanels.forEach(p => p.classList.add('hidden'));
      const view = document.getElementById(`view-${tab}`);
      if (view) view.classList.remove('hidden');
      syncMainWithDashboard();
      
      App.state.activeTab = tab;
      
      if (tab === 'agenda' && App.modules.Agenda) {
        App.modules.Agenda.render();
      } else if (tab === 'studyflow' && App.modules.Courses) {
        App.modules.Courses.render();
      }
    });
  });
  
  document.querySelectorAll('.dashboard-nav-card').forEach(card => {
    card.addEventListener('click', () => {
      const tab = card.dataset.tab;
      const tabBtn = document.querySelector(`.tab-btn[data-tab="${tab}"]`);
      if (tabBtn) tabBtn.click();
    });
  });
  
  document.querySelector('.header-logo')?.addEventListener('click', () => {
    tabButtons.forEach(b => b.classList.remove('active'));
    viewPanels.forEach(p => p.classList.add('hidden'));
    document.getElementById('view-dashboard')?.classList.remove('hidden');
    App.state.activeTab = 'dashboard';
    syncMainWithDashboard();
    renderDashboard();
  });

  // ─── Theme toggle (dual dark/light, v2 palette) ────────────────
  const btnTheme = document.getElementById('btn-theme');
  if (btnTheme) {
    const syncThemeIcon = () => {
      const isLight = document.documentElement.getAttribute('data-theme') === 'light';
      btnTheme.textContent = isLight ? '🌙' : '☀️';
      btnTheme.title = isLight ? 'Cambiar a tema oscuro' : 'Cambiar a tema claro';
    };
    syncThemeIcon();
    btnTheme.addEventListener('click', () => {
      const root = document.documentElement;
      const isLight = root.getAttribute('data-theme') === 'light';
      if (isLight) root.removeAttribute('data-theme');
      else root.setAttribute('data-theme', 'light');
      try { localStorage.setItem('sf-theme', isLight ? 'dark' : 'light'); } catch (e) {}
      const meta = document.querySelector('meta[name="theme-color"]');
      if (meta) meta.setAttribute('content', isLight ? '#16213e' : '#ffffff');
      syncThemeIcon();
    });
  }

  const hamburger = document.getElementById('hamburger-btn');
  const appEl = document.getElementById('app');
  if (hamburger && appEl) {
    hamburger.addEventListener('click', () => {
      appEl.classList.toggle('sidebar-open');
      hamburger.textContent = appEl.classList.contains('sidebar-open') ? '✕' : '☰';
    });
  }

  // Hide/show the view's nav column (#agenda-left / #studyflow-left).
  //
  // This drives the SAME sf-focus-mode class Ctrl+B already used, on .main.
  // The layout is flex, not grid, and the column is re-created per view with a
  // different id — so the rule keys off .col-left's position in .main, and the
  // only correct way to drive it is the existing class. An earlier attempt
  // collapsed grid-template-columns on .app instead: the grid rule does not
  // apply to this flex column, so the reflow tore down the whole page and it
  // looked like a navigation to the main view.
  const navToggle = document.getElementById('nav-toggle-btn');
  if (navToggle) {
    // Start with the nav visible, always — never inherit a collapsed column.
    const main = document.querySelector('.main');
    if (main) main.classList.remove('sf-focus-mode');

    const sync = () => {
      const hidden = !!main && main.classList.contains('sf-focus-mode');
      const icon = hidden ? '⇥' : '⇤';
      const label = hidden ? 'Mostrar navegación' : 'Ocultar navegación';
      const pressed = String(hidden);
      // Write only on a real change. The observer below watches the whole
      // subtree, so a sync() that rewrites identical values would retrigger it
      // forever and the app would never finish booting.
      if (navToggle.textContent !== icon) navToggle.textContent = icon;
      if (navToggle.getAttribute('aria-pressed') !== pressed) {
        navToggle.setAttribute('aria-pressed', pressed);
      }
      if (navToggle.getAttribute('aria-label') !== label) {
        navToggle.setAttribute('aria-label', label);
      }
    };
    navToggle.addEventListener('click', (e) => {
      // The button sits inside .header-logo, whose handler navigates to the
      // dashboard. Without this the click bubbled up, the whole main area got
      // hidden and the toggle looked like a navigation. Stop it right here.
      e.stopPropagation();
      e.preventDefault();
      if (!main) return;
      main.classList.toggle('sf-focus-mode');
      sync();
    });
    // Two things can change the button's truth: the view is rebuilt (the
    // column is re-created with a new id), or someone toggles focus mode some
    // other way — ⌘/Ctrl+B. The first is a childList change, the second an
    // attribute change, so both have to be observed or the button keeps
    // claiming a state that is no longer true. sync() only writes on a real
    // change, so watching the class attribute can't feed itself.
    const observer = new MutationObserver(sync);
    observer.observe(document.body, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ['class'],
    });
    sync();
  }
  
  if (appEl) {
    appEl.addEventListener('click', (e) => {
      if (appEl.classList.contains('sidebar-open')) {
        const left = e.target.closest('.col-left');
        const hb = e.target.closest('#hamburger-btn');
        if (!left && !hb) {
          appEl.classList.remove('sidebar-open');
          hamburger.textContent = '☰';
        }
      }
    });
  }
  
  document.getElementById('week-prev')?.addEventListener('click', () => {
    const today = new Date();
    const weekStart = new Date(today);
    weekStart.setDate(today.getDate() - today.getDay() - 7);
    App.state.currentWeek = getWeekId(weekStart.toISOString().slice(0, 10));
    App.state.currentDay = null;
    window.AgendaGlobals?.syncStateFromApp();
    renderActiveTab();
  });
  
  document.getElementById('week-next')?.addEventListener('click', () => {
    const today = new Date();
    const weekStart = new Date(today);
    weekStart.setDate(today.getDate() - today.getDay() + 7);
    App.state.currentWeek = getWeekId(weekStart.toISOString().slice(0, 10));
    App.state.currentDay = null;
    window.AgendaGlobals?.syncStateFromApp();
    renderActiveTab();
  });
  
  const today = new Date();
  const weekStart = new Date(today);
  weekStart.setDate(today.getDate() - today.getDay());
  App.state.currentWeek = getWeekId(weekStart.toISOString().slice(0, 10));
  App.state.currentDay = today.toISOString().slice(0, 10);
  window.AgendaGlobals?.syncStateFromApp();
  
  // Initialize auth
  // Initialize auth
  try {
    await App.initAuth();
  } catch (e) {
    console.log('[App] initAuth error, continuing:', e);
    Auth.clearToken();
  }

  renderActiveTab();
  updateOnlineStatus();
  syncMainWithDashboard();
  // The home is the default view on boot — paint its widgets now.
  renderDashboard();

  const loading = document.getElementById('loading-screen');
  if (loading) loading.classList.add('hidden');
  
  // Show the app container
  const app = document.getElementById('app');
  if (app) app.classList.remove('hidden');
});


// ─── Backup / Restore (v2 parity — issue #23) ───────────────────────
// Ported from studyflow-hub/luna frontend/app.js (BACKUP / RESTORE
// section + the btn-backup/btn-restore listeners in its DOMContentLoaded).
// Endpoints: POST /api/backup/mine (ZIP), POST /api/backup/mine/restore
// (additive). backup-selector.js (loaded right after this file) owns the
// modal and calls window.downloadBackup(body).
//
// The backup is a single synchronous POST that can return well over a GB.
// The server builds the entire archive before it sends the first byte, so
// the browser sees a dead connection for the first ~80s. Two clocks race us
// meanwhile: any reverse-proxy origin timeout and the worker's own timeout.
// A plain `await fetch()` with no AbortController turns any hiccup into a
// button stuck on "Preparando…" forever, with no error and no way out.
const BACKUP_TIMEOUT_MS = 20 * 60 * 1000;
const BACKUP_SERVER_PREP_S = 80;
const BACKUP_LOG_MAX_LINES = 200;
const BACKUP_LOG_STEP_BYTES = 64 * 1024 * 1024;

function _fmtBytes(bytes) {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** i).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function _fmtClock(seconds) {
  const m = Math.floor(seconds / 60);
  return `${m}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;
}

function backupLog(message, level = '') {
  const panel = document.getElementById('backup-log');
  const body = document.getElementById('backup-log-body');
  if (!panel || !body) return;
  panel.classList.remove('hidden');
  const line = document.createElement('div');
  line.className = `bk-log-line${level ? ' bk-' + level : ''}`;
  const ts = new Date().toLocaleTimeString('es-ES', { hour12: false });
  line.textContent = `[${ts}] ${message}`;
  body.appendChild(line);
  while (body.childElementCount > BACKUP_LOG_MAX_LINES) body.removeChild(body.firstChild);
  body.scrollTop = body.scrollHeight;
}

// v3's Auth.getHeaders() ALWAYS injects `Content-Type: application/json`,
// which kills multipart parsing on the restore upload (the browser has to
// set the boundary itself). Lift only the Authorization out of it — same
// rule the old shared/backup-restore.js followed in _uploadHeaders().
function _bkAuthHeaders(withJson) {
  const headers = {};
  const auth = window.Auth?.getHeaders?.() || {};
  if (auth.Authorization) headers.Authorization = auth.Authorization;
  if (withJson) headers['Content-Type'] = 'application/json';
  return headers;
}

async function downloadBackup(selectionBody) {
  const btn = document.getElementById('btn-backup');
  const originalText = btn.textContent;
  const controller = new AbortController();
  const t0 = performance.now();
  // Shared with the ticker so it can switch from "preparing" to "receiving"
  // the moment the first byte lands.
  const seen = { bytes: 0, total: 0, streaming: false };

  const isPartial = !!(selectionBody && Object.keys(selectionBody).length);
  backupLog(isPartial
    ? '▶ Backup personal SELECTIVO — solo lo marcado en el selector'
    : '▶ Backup personal — todo tu usuario: asignaturas, temas, contenido, tests, flashcards y agenda', 'head');
  backupLog(`⏳ El servidor tarda ~${BACKUP_SERVER_PREP_S}s en preparar el archivo y no envía nada hasta entonces. El contador de tiempo sigue corriendo: no está colgado.`, 'warn');

  const ticker = setInterval(() => {
    const elapsed = (performance.now() - t0) / 1000;
    btn.textContent = seen.streaming
      ? `⏳ ${_fmtBytes(seen.bytes)}${seen.total ? ' / ' + _fmtBytes(seen.total) : ''} · ${_fmtClock(elapsed)}`
      : `⏳ Preparando… ${_fmtClock(elapsed)}`;
  }, 1000);
  btn.textContent = '⏳ Preparando… 0:00';
  btn.disabled = true;

  const killTimer = setTimeout(() => controller.abort(new Error('timeout')), BACKUP_TIMEOUT_MS);
  let objectUrl = null;

  try {
    const resp = await fetch(`${window.API_URL}/backup/mine`, {
      method: 'POST',
      headers: _bkAuthHeaders(!!selectionBody),
      credentials: 'include',
      body: selectionBody ? JSON.stringify(selectionBody) : undefined,
      signal: controller.signal,
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ error: resp.statusText }));
      backupLog(`✗ HTTP ${resp.status}: ${err.error || 'desconocido'}`, 'err');
      alert('❌ Error al generar backup: ' + (err.error || 'desconocido'));
      return;
    }

    const total = Number(resp.headers.get('Content-Length')) || 0;
    const prepS = (performance.now() - t0) / 1000;
    seen.total = total;
    backupLog(`✓ El servidor respondió en ${_fmtClock(prepS)} — ${_fmtBytes(total)}`, 'ok');
    backupLog('↓ Descargando…');

    // Read the body as a stream so progress is real, measured bytes — not a
    // spinner that could be lying to us for another five minutes.
    const chunks = [];
    let received = 0;
    let nextLogAt = BACKUP_LOG_STEP_BYTES;
    seen.streaming = true;
    const reader = resp.body.getReader();
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
      received += value.length;
      seen.bytes = received;
      if (received >= nextLogAt) {
        nextLogAt += BACKUP_LOG_STEP_BYTES;
        backupLog(`↓ ${_fmtBytes(received)}${total ? ' / ' + _fmtBytes(total) : ''}`);
      }
    }

    const blob = new Blob(chunks, { type: 'application/zip' });
    objectUrl = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = objectUrl;
    a.download = `studyflow_backup_${new Date().toISOString().slice(0, 10)}.zip`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    backupLog(`✅ Descargado: ${_fmtBytes(blob.size)} en ${_fmtClock((performance.now() - t0) / 1000)}`, 'ok');
  } catch (e) {
    if (controller.signal.aborted) {
      const mins = Math.round(BACKUP_TIMEOUT_MS / 60000);
      backupLog(`✗ Cancelado tras ${mins} min sin completarse`, 'err');
      alert(
        `❌ El backup no terminó en ${mins} minutos y se canceló.\n\n` +
        `La causa más probable es que Cloudflare corta la respuesta del origen ` +
        `a los ~100 s, y este backup genera todo el archivo antes de enviar el ` +
        `primer byte. Es un límite conocido de este diseño, no un fallo suyo.`
      );
    } else {
      backupLog(`✗ ${e.message}`, 'err');
      alert('❌ Error al generar backup: ' + e.message);
    }
  } finally {
    clearInterval(ticker);
    clearTimeout(killTimer);
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    btn.textContent = originalText;
    btn.disabled = false;
  }
}

// The backup selector modal (backup-selector.js) calls this with the user's
// selection; without one, backup behaves exactly as before.
window.downloadBackup = downloadBackup;

// Read the zip's manifest and build the restore selector options BEFORE
// writing anything. When the zip has no manifest (or the server cannot
// build the options) we fall back to the blind full import — same endpoint,
// no `selection` field — instead of failing.
async function inspectRestore(file) {
  backupLog('▶ Inspeccionando el backup para ofrecer restauración selectiva…', 'head');
  try {
    const formData = new FormData();
    formData.append('file', file);
    const resp = await fetch(`${window.API_URL}/backup/mine/inspect`, {
      method: 'POST',
      // Content-Type omitted — the browser sets multipart + boundary.
      headers: _bkAuthHeaders(false),
      credentials: 'include',
      body: formData,
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      // Older server without /inspect, or a transport error: degrade.
      backupLog(`⚠️ No se pudo inspeccionar (${data.error || resp.status}); ` +
                `se ofrece la restauración completa.`, 'warn');
      return uploadRestore(file);
    }
    const options = data.options;
    if (data.has_manifest && options && options.tree &&
        window.BackupSelector?.openReview) {
      const opened = window.BackupSelector.openReview(
        options, (selection) => uploadRestore(file, selection));
      if (opened) return;
    }
    const why = !data.has_manifest
      ? (data.manifest_error || 'sin manifest')
      : (data.options_error || 'opciones no disponibles');
    backupLog(`⚠️ Este backup no permite selección (${why}); ` +
              `se restaura completo.`, 'warn');
    return uploadRestore(file);
  } catch (e) {
    backupLog(`⚠️ Inspección fallida (${e.message}); se restaura completo.`, 'warn');
    return uploadRestore(file);
  }
}

async function uploadRestore(file, selection) {
  // Additive, not destructive: rows that already exist are left alone and
  // the database is never dropped. The old confirmations are gone because
  // they described a risk this endpoint does not have.
  // A `selection` means the user already ticked the tree and pressed
  // "Restaurar selección", so the modal itself was the confirmation.
  if (!selection && !confirm(
    'ℹ️ Se restaurará TU backup: tus asignaturas, contenido, tests y agenda.\n\n' +
    'No se borra nada de lo que ya existe — lo que ya esté se queda, y lo que falte se añade.\n\n' +
    '¿Continuar?'
  )) return;

  const btn = document.getElementById('btn-restore');
  const originalText = btn.textContent;
  btn.textContent = '⏳ Restaurando...';
  btn.disabled = true;
  try {
    const formData = new FormData();
    formData.append('file', file);
    if (selection) formData.append('selection', JSON.stringify(selection));
    const resp = await fetch(`${window.API_URL}/backup/mine/restore`, {
      method: 'POST',
      // Content-Type omitted — the browser sets multipart + boundary.
      headers: _bkAuthHeaders(false),
      credentials: 'include',
      body: formData,
    });
    const result = await resp.json();
    if (resp.ok) {
      const rows = Object.values(result.rows_inserted || {}).reduce((a, b) => a + b, 0);
      const skipped = Number(result.skipped) || 0;
      const partialWarn = result.partial
        ? '\n\n⚠️ Este backup era PARCIAL — solo contenía lo que marcaste\n' +
          'en el selector (asignaturas, agenda, media…). No reprodujo tu cuenta\n' +
          'completa, solo añadió lo que faltaba de esa parte.'
        : '';
      alert(
        `✅ Restauración completada.\n\n` +
        `· ${rows} registros añadidos\n` +
        (skipped ? `· ${skipped} filas ya existían (no se tocaron)\n` : '') +
        `· ${result.files_written} ficheros escritos${partialWarn}\n\n` +
        `Recargando la aplicación...`
      );
      window.location.reload();
    } else {
      alert('❌ Error al restaurar: ' + (result.error || 'desconocido'));
    }
  } catch (e) {
    alert('❌ Error al restaurar: ' + e.message);
  } finally {
    btn.textContent = originalText;
    btn.disabled = false;
  }
}

// Click wiring for the header buttons. With the selector loaded, Backup opens
// the selective modal; without it (older caches) it falls back to a full
// backup — same degradation path as the hub.
function initBackupRestore() {
  document.getElementById('btn-backup')?.addEventListener('click', (e) => {
    if (window.BackupSelector) {
      e.preventDefault();
      window.BackupSelector.open();
    } else {
      downloadBackup();
    }
  });
  document.getElementById('backup-log-close')?.addEventListener('click', () => {
    document.getElementById('backup-log')?.classList.add('hidden');
  });
  document.getElementById('btn-restore')?.addEventListener('click', () => {
    document.getElementById('restore-input')?.click();
  });
  document.getElementById('restore-input')?.addEventListener('change', (e) => {
    if (e.target.files?.length) inspectRestore(e.target.files[0]);
    e.target.value = ''; // reset so the same file can be picked again
  });
}


// ─── Render Active Tab ──────────────────────────────────────────────
function renderActiveTab() {
  window.AgendaGlobals?.syncStateFromApp();
  const tab = App.state.activeTab;
  if (tab === 'agenda' && App.modules.Agenda) {
    App.modules.Agenda.render();
  } else if (tab === 'studyflow' && App.modules.Courses) {
    App.modules.Courses.render();
  }
  updateWeekLabel();
}

// ─── Agenda global handlers (Escape, focus-mode, category overlay,
// columns-overlay stub) live in features/agenda/agenda-handlers.js —
// loaded before app.js by index.html.


// ─── Utility Functions ──────────────────────────────────────────────
function getWeekId(dateStr) {
  const date = new Date(dateStr + 'T12:00:00');
  const year = date.getFullYear();
  const week = Math.ceil(((date - new Date(year, 0, 1)) / 86400000 + new Date(year, 0, 1).getDay() + 1) / 7);
  return `${year}-W${String(week).padStart(2, '0')}`;
}

function updateWeekLabel() {
  const label = document.getElementById('week-label');
  if (label && App.state.currentWeek) {
    const [year, week] = App.state.currentWeek.split('-W');
    const weekNum = parseInt(week, 10);
    const monthNames = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic'];
    const approxDate = new Date(year, 0, 1 + (weekNum - 1) * 7);
    const month = monthNames[approxDate.getMonth()];
    label.innerHTML = `<strong>Semana ${weekNum}</strong> — ${month} ${year}`;
  }
}

console.log('[PWA] App bootstrap loaded');