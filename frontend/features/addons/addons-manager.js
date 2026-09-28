// ─── Addons Manager — overlay UI for the catalog ─────────────
// Namespace: window.App.AddonsManager
// Dependencies: window.App.Addons, window.App.UI (escHtml)
//
// Sub-phase SA — opens a modal overlay listing every addon in the
// catalog with its current state and install / enable / disable /
// uninstall buttons. Reuses the .overlay / .omodal shell from
// styles.css (same one the agenda overlays sit on). No router / no
// sidebar — it's a standalone modal reachable from the ⚙️ header
// button (see index.html #btn-addons).

window.App = window.App || {};

window.App.AddonsManager = (function () {
  "use strict";

  const { escHtml } = window.App.UI;

  const OVERLAY_ID = "addons-overlay";
  const GRID_ID = "addons-overlay-grid";

  let _open = false;

  // ── public: open / close / status ────────────────────────────
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

  // ── Catalog render ────────────────────────────────────────────
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
      await render();
    } catch (err) {
      btn.textContent = "⚠ " + ((err && err.message) || "error");
      setTimeout(() => {
        btn.disabled = false;
        btn.textContent = prevText;
      }, 1800);
    }
  }

  // ── Header ⚙️ button wire-up (DOMContentLoaded-safe) ──────────────────
  function attachHeaderButton() {
    const btn = document.getElementById("btn-addons");
    if (!btn || btn.dataset.amWired) return;
    btn.dataset.amWired = "1";
    btn.addEventListener("click", (ev) => {
      ev.preventDefault();
      open();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", attachHeaderButton);
  } else {
    attachHeaderButton();
  }

  return { open, close, isOpen, render, attachHeaderButton };
})();

console.log("[Addons] manager loaded");