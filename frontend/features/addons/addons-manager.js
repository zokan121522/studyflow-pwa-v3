// ─── Addons Manager — sidebar nav + center-view marketplace ─────
// Namespace: window.App.AddonsManager
// Dependencies: window.App.Addons, window.App.UI (escHtml),
//               window.STATE, window.App.Courses (updateCenter).
//
// Sub-phase SA.2 — move the addons entry out of the header gear (⚙️
// was wrong per user) and into the Studyflow column as a left-nav
// item ABOVE the course tree (v2 pattern). The marketplace catalog
// is rendered as a CENTER VIEW (one tab replaces the topic detail
// without leaving Studyflow), reusing the existing card markup
// from the overlay build. The overlay itself is retained for
// programmatic calls / fallback but the primary entry is the nav.
//
// Routing contract (mirrors v2 addons-marketplace.js):
//   • STATE._view === "addons" → App.Courses.updateCenter routes
//     to AddonsManager.renderView(centerEl).
//   • selecting a course/topic clears STATE._view so the topic
//     detail / landing re-render normally.
//   • renderMarketplaceNav() is called from courses.js on every
//     renderStudyflow() so the nav survives repaints.
window.App = window.App || {};

window.App.AddonsManager = (function () {
  "use strict";

  const { escHtml } = window.App.UI;

  const OVERLAY_ID = "addons-overlay";
  const GRID_ID = "addons-overlay-grid";

  let _open = false;

  // ── public: nav + view + legacy overlay ───────────────────────
  // renderMarketplaceNav() — idempotent. Appends a .sf-stats-section
  // with «🧩 Addons» col-title + «🛍️ Marketplace» qs-nav-item into
  // the #studyflow-left-stats container emitted by courses-sidebar.js.
  // The click handler sets STATE._view='addons' and calls
  // App.Courses.updateCenter(). Survives sidebar repaints.
  function renderMarketplaceNav() {
    const statsEl = document.getElementById("studyflow-left-stats");
    if (!statsEl) return;
    let navEl = statsEl.querySelector("[data-am-nav]");
    if (!navEl) {
      const html = `<div class="sf-stats-section">
        <div class="col-title">🧩 Addons</div>
        <div class="qs-nav-item" data-am-nav data-am-view="addons">
          <span class="qs-nav-icon">🛍️</span>
          <span class="qs-nav-title">Marketplace</span>
        </div>
      </div>`;
      statsEl.insertAdjacentHTML("beforeend", html);
      navEl = statsEl.querySelector("[data-am-nav]");
      navEl.addEventListener("click", async () => {
        try {
          window.STATE = window.STATE || {};
          window.STATE._view = "addons";
        } catch (_) { /* STATE may be frozen in tests */ }
        if (window.App && window.App.Courses
            && typeof window.App.Courses.updateCenter === "function") {
          await window.App.Courses.updateCenter();
        }
      });
    }
    // Refresh active state (idempotent on re-renders).
    try {
      const active = window.STATE && window.STATE._view === "addons";
      navEl.classList.toggle("active", !!active);
    } catch (_) { /* ignore */ }
  }

  // renderView(centerEl) — render the catalog as a CENTER VIEW
  // (replaces the topic detail / landing). Reuses the same card
  // markup the overlay uses so the visuals stay consistent.
  async function renderView(centerEl) {
    if (!centerEl) return;
    centerEl.innerHTML =
      '<div class="am-loading">🧩 Cargando catálogo…</div>';
    let addons = [];
    try {
      addons = await window.App.Addons.refresh();
    } catch (_) {
      centerEl.innerHTML =
        '<div class="am-empty">No se pudo cargar el catálogo.</div>';
      return;
    }
    const visible = (addons || []).filter((a) => !a.hidden);
    centerEl.innerHTML = _renderGrid(visible);
    _attachHandlers(centerEl);
  }

  // ── Overlay API (kept for programmatic / fallback use) ─────────
  async function open() {
    ensureOverlay();
    const overlay = document.getElementById(OVERLAY_ID);
    overlay.classList.add("open");
    _open = true;
    try { await render(); } catch (_) { /* render handles errors */ }
  }

  function close() {
    const overlay = document.getElementById(OVERLAY_ID);
    if (overlay) overlay.classList.remove("open");
    _open = false;
  }

  function isOpen() { return _open; }

  // ── Overlay markup (lazy-injected, idempotent) ────────────────
  function ensureOverlay() {
    if (document.getElementById(OVERLAY_ID)) return;
    _injectOverlayMarkup();
    _wireOverlayEvents();
  }

  function _injectOverlayMarkup() {
    const wrap = document.createElement("div");
    wrap.innerHTML = `<div class="overlay" id="${OVERLAY_ID}">
      <div class="omodal">
        <div class="omodal-head">
          <h3>🧩 Complementos</h3>
          <button id="addons-overlay-close" aria-label="Cerrar">✕</button>
        </div>
        <div class="omodal-body"><div id="${GRID_ID}"></div></div>
      </div>
    </div>`;
    document.body.appendChild(wrap.firstChild);
  }

  function _wireOverlayEvents() {
    document.getElementById("addons-overlay-close")
      .addEventListener("click", close);
    document.getElementById(OVERLAY_ID).addEventListener("click", (ev) => {
      // click on the backdrop (not the modal) → close
      if (ev.target && ev.target.id === OVERLAY_ID) close();
    });
    document.addEventListener("keydown", (ev) => {
      if (_open && ev.key === "Escape") close();
    });
    // React to state changes from other views (e.g. external toggle).
    window.addEventListener("addons:changed", () => {
      if (_open) render();
    });
  }

  // ── Catalog render (overlay + center view share this markup) ──
  async function render() {
    const grid = document.getElementById(GRID_ID);
    if (!grid) return;
    grid.innerHTML = `<div class="am-loading">🧩 Cargando catálogo…</div>`;

    let addons = [];
    try {
      addons = await window.App.Addons.refresh();
    } catch (_) {
      grid.innerHTML =
        `<div class="am-empty">No se pudo cargar el catálogo.</div>`;
      return;
    }

    // Drop hidden rows from the marketplace view (IA-phase addons stay tucked).
    const visible = addons.filter((a) => !a.hidden);

    if (!visible.length) {
      grid.innerHTML =
        `<div class="am-empty">No hay complementos visibles.</div>`;
      return;
    }

    grid.innerHTML = _renderGrid(visible);
    _attachHandlers(grid);
  }

  // ── _renderGrid → grid HTML (header + cards) ──────────────────
  function _renderGrid(addons) {
    const cards = addons.map(_renderCard).join("");
    return `<div class="am-view">
      <div class="am-view-header">
        <span class="am-view-title">Complementos instalados</span>
        <span class="am-view-sub">`
      + `Activa o desactiva funciones (S5/S6/S9) según las necesites`
      + `</span></div>
      <div class="am-grid">${cards}</div>
      <div class="am-spacer" aria-hidden="true"></div>
    </div>`;
  }

  // ── _renderCard → single addon card ───────────────────────────
  function _renderCard(a) {
    const installed = !!a.installed;
    const enabled = !!a.enabled;
    const stateLabel = !installed ? "No instalado"
      : enabled ? "Activo" : "Desactivado";
    const stateClass = !installed ? "am-state-off"
      : enabled ? "am-state-ok" : "am-state-disabled";
    const head = _renderCardHead(a);
    const statePill = `<span class="am-state-pill ${stateClass}">${stateLabel}</span>`;
    const actions = _renderActions(a.slug, installed, enabled);
    return `<div class="am-card" data-slug="${escHtml(a.slug)}">
      ${head}
      <div class="am-card-state">${statePill}</div>
      <div class="am-card-actions">${actions}</div>
    </div>`;
  }

  // ── _renderCardHead → icon + name + version + desc ────────────
  function _renderCardHead(a) {
    const icon = _iconFor(a.slug);
    let html = `<div class="am-card-head">`;
    html += `<span class="am-card-icon">${escHtml(icon)}</span>`;
    html += `<span class="am-card-name">${escHtml(a.name)}</span>`;
    if (a.version) {
      html += `<span class="am-card-version">v${escHtml(a.version)}</span>`;
    }
    html += `</div>`;
    if (a.description) {
      html += `<div class="am-card-desc">${escHtml(a.description)}</div>`;
    }
    return html;
  }

  // ── _renderActions → install / enable / disable / uninstall ───
  function _renderActions(slug, installed, enabled) {
    if (!installed) {
      return `<button class="am-btn am-btn-primary" data-act="install">`
        + `Instalar</button>`;
    }
    if (enabled) {
      return `<button class="am-btn" data-act="disable">Desactivar</button>`
        + `<button class="am-btn am-btn-danger" data-act="uninstall">`
        + `Desinstalar</button>`;
    }
    return `<button class="am-btn am-btn-primary" data-act="enable">`
      + `Activar</button>`
      + `<button class="am-btn am-btn-danger" data-act="uninstall">`
      + `Desinstalar</button>`;
  }

  // ── Per-slug icons (registry overrides win once S5/S6/S9 register) ──
  function _iconFor(slug) {
    const reg = window.App.Addons.get(slug);
    if (reg && reg.icon) return reg.icon;
    const defaults = {
      quiz: "📝",
      flashcards: "🃏",
      jsplayground: "💻",
      notebooklm: "📓",
      opencode: "🧠",
      english: "🇬🇧",
      core: "🧩",
      add: "➕",
    };
    return defaults[slug] || "🧩";
  }

  // ── Delegated click handler (event delegation, idempotent) ────
  function _attachHandlers(grid) {
    if (grid.dataset.amHandlers) return;
    grid.dataset.amHandlers = "1";
    grid.addEventListener("click", async (ev) => {
      const btn = ev.target.closest(".am-btn[data-act]");
      if (!btn) return;
      const card = btn.closest(".am-card");
      if (!card) return;
      await _handleAction(card.dataset.slug, btn.dataset.act, btn);
    });
  }

  async function _handleAction(slug, act, btn) {
    const Addons = window.App.Addons;
    const actions = {
      install:   () => Addons.install(slug),
      uninstall: () => Addons.uninstall(slug),
      enable:    () => Addons.enable(slug),
      disable:   () => Addons.disable(slug),
    };
    const fn = actions[act];
    if (!fn) return;

    btn.disabled = true;
    const prevText = btn.textContent;
    btn.textContent = "…";
    try {
      await Addons.mutate(fn, slug);
      // If the click came from the center view, re-render it so the
      // new state is reflected without leaving the page.
      const center = document.getElementById("studyflow-center");
      if (center && center.contains(btn)) {
        await renderView(center);
      } else {
        await render();
      }
    } catch (err) {
      btn.textContent = "⚠ " + ((err && err.message) || "error");
      setTimeout(() => {
        btn.disabled = false;
        btn.textContent = prevText;
      }, 1800);
    }
  }

  // Re-render center view when addons change (so the marketplace
  // mirrors install/uninstall/enable/disable toggles from S5/S6/S9
  // without leaving the view).
  function _onAddonsChanged() {
    const center = document.getElementById("studyflow-center");
    if (!center) return;
    try {
      if (window.STATE && window.STATE._view === "addons") {
        renderView(center).catch(() => {});
      }
    } catch (_) { /* ignore */ }
  }
  window.addEventListener("addons:changed", _onAddonsChanged);

  return {
    open, close, isOpen, render,
    renderMarketplaceNav, renderView,
  };
})();

console.log("[Addons] manager loaded");