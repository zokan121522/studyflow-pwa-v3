/* ============================== STUDYFLOW PWA — APP BOOTSTRAP ============================== */

// ─── Configuration ────────────────────────────────────────────────────
const API_URL = (() => {
  // Mismo origen: el backend (Flask) sirve la PWA y el /api juntos.
  // Solo en dev local con http.server aparte (:3000) se apunta a :8082.
  if (location.port === '3000') return 'http://localhost:8082/api';
  return '/api';
})();


// ─── Service Worker Registration ────────────────────────────────────
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js', { scope: '/' })
      .then((registration) => {
        console.log('[PWA] Service Worker registered:', registration.scope);
        
        registration.addEventListener('updatefound', () => {
          const newWorker = registration.installing;
          newWorker.addEventListener('statechange', () => {
            if (newWorker.state === 'installed' && navigator.serviceWorker.controller) {
              console.log('[PWA] New version available');
              showUpdateNotification();
            }
          });
        });
      })
      .catch((error) => {
        console.error('[PWA] Service Worker registration failed:', error);
      });
  });
}

function showUpdateNotification() {
  const notification = document.createElement('div');
  notification.className = 'update-notification';
  notification.innerHTML = `
    <span>🔄 Nueva versión disponible</span>
    <button id="update-btn">Actualizar</button>
  `;
  document.body.appendChild(notification);
  
  document.getElementById('update-btn').addEventListener('click', () => {
    if (navigator.serviceWorker.controller) {
      navigator.serviceWorker.controller.postMessage('skipWaiting');
      window.location.reload();
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
  });
  
  const hamburger = document.getElementById('hamburger-btn');
  const appEl = document.getElementById('app');
  if (hamburger && appEl) {
    hamburger.addEventListener('click', () => {
      appEl.classList.toggle('sidebar-open');
      hamburger.textContent = appEl.classList.contains('sidebar-open') ? '✕' : '☰';
    });
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

  const loading = document.getElementById('loading-screen');
  if (loading) loading.classList.add('hidden');
  
  // Show the app container
  const app = document.getElementById('app');
  if (app) app.classList.remove('hidden');
});


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