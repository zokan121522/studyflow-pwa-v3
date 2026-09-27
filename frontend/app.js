/* ============================== STUDYFLOW PWA — APP BOOTSTRAP ============================== */

// ─── Service Worker Registration ────────────────────────────────────
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js', { scope: '/' })
      .then((registration) => {
        console.log('[PWA] Service Worker registered:', registration.scope);
        
        // Check for updates
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

// Show update notification
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
  // Only show if not already installed and not shown before
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
const App = {
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
  }
};

// Expose globally for feature modules
window.App = App;
window.Router = Router;

// ─── Tab Navigation ─────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  const tabButtons = document.querySelectorAll('.tab-btn');
  const viewPanels = document.querySelectorAll('.view-panel');
  
  tabButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      const tab = btn.dataset.tab;
      
      // Update buttons
      tabButtons.forEach(b => {
        b.classList.remove('active');
        b.setAttribute('aria-selected', 'false');
      });
      btn.classList.add('active');
      btn.setAttribute('aria-selected', 'true');
      
      // Update views
      viewPanels.forEach(p => p.classList.add('hidden'));
      const view = document.getElementById(`view-${tab}`);
      if (view) view.classList.remove('hidden');
      
      App.state.activeTab = tab;
      
      // Trigger module render
      if (tab === 'agenda' && App.modules.Agenda) {
        App.modules.Agenda.render();
      } else if (tab === 'studyflow' && App.modules.Courses) {
        App.modules.Courses.render();
      } else if (tab === 'dashboard') {
        // Dashboard is static
      }
    });
  });
  
  // Dashboard nav cards
  document.querySelectorAll('.dashboard-nav-card').forEach(card => {
    card.addEventListener('click', () => {
      const tab = card.dataset.tab;
      const tabBtn = document.querySelector(`.tab-btn[data-tab="${tab}"]`);
      if (tabBtn) tabBtn.click();
    });
  });
  
  // Logo click -> Dashboard
  document.querySelector('.header-logo')?.addEventListener('click', () => {
    tabButtons.forEach(b => b.classList.remove('active'));
    viewPanels.forEach(p => p.classList.add('hidden'));
    document.getElementById('view-dashboard')?.classList.remove('hidden');
    App.state.activeTab = 'dashboard';
  });
  
  // Hamburger menu
  const hamburger = document.getElementById('hamburger-btn');
  const appEl = document.getElementById('app');
  if (hamburger && appEl) {
    hamburger.addEventListener('click', () => {
      appEl.classList.toggle('sidebar-open');
      hamburger.textContent = appEl.classList.contains('sidebar-open') ? '✕' : '☰';
    });
  }
  
  // Close sidebar on outside click (mobile)
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
  
  // Week navigation
  document.getElementById('week-prev')?.addEventListener('click', () => {
    const today = new Date();
    const weekStart = new Date(today);
    weekStart.setDate(today.getDate() - today.getDay() - 7);
    App.state.currentWeek = getWeekId(weekStart.toISOString().slice(0, 10));
    App.state.currentDay = null;
    renderActiveTab();
  });
  
  document.getElementById('week-next')?.addEventListener('click', () => {
    const today = new Date();
    const weekStart = new Date(today);
    weekStart.setDate(today.getDate() - today.getDay() + 7);
    App.state.currentWeek = getWeekId(weekStart.toISOString().slice(0, 10));
    App.state.currentDay = null;
    renderActiveTab();
  });
  
  // Initialize week
  const today = new Date();
  const weekStart = new Date(today);
  weekStart.setDate(today.getDate() - today.getDay());
  App.state.currentWeek = getWeekId(weekStart.toISOString().slice(0, 10));
  App.state.currentDay = today.toISOString().slice(0, 10);
  
  // Initial render
  renderActiveTab();
  updateOnlineStatus();
  
  // Hide loading screen
  const loading = document.getElementById('loading-screen');
  if (loading) loading.classList.add('hidden');
});

// ─── Render Active Tab ──────────────────────────────────────────────
function renderActiveTab() {
  const tab = App.state.activeTab;
  if (tab === 'agenda' && App.modules.Agenda) {
    App.modules.Agenda.render();
  } else if (tab === 'studyflow' && App.modules.Courses) {
    App.modules.Courses.render();
  }
  updateWeekLabel();
}

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

// ─── API Helpers (shared) ───────────────────────────────────────────
const API = {
  async get(path) {
    const r = await fetch(`/api${path}`, { 
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin'
    });
    if (!r.ok) throw new Error(`GET ${path} ${r.status}`);
    return r.json();
  },
  async post(path, body) {
    const r = await fetch(`/api${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify(body)
    });
    if (!r.ok) throw new Error(`POST ${path} ${r.status}`);
    return r.json();
  },
  async put(path, body) {
    const r = await fetch(`/api${path}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify(body)
    });
    if (!r.ok) throw new Error(`PUT ${path} ${r.status}`);
    return r.json();
  },
  async patch(path, body) {
    const r = await fetch(`/api${path}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify(body)
    });
    if (!r.ok) throw new Error(`PATCH ${path} ${r.status}`);
    return r.json();
  },
  async del(path) {
    const r = await fetch(`/api${path}`, { 
      method: 'DELETE',
      credentials: 'same-origin'
    });
    if (!r.ok) throw new Error(`DELETE ${path} ${r.status}`);
    return r.json();
  }
};

window.API = API;

console.log('[PWA] App bootstrap loaded');
