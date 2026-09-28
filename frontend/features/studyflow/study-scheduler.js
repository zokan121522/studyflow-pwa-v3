/* ============================== STUDY SCHEDULER FAB (S7b-B) ==============================
 * Port of v2's study-scheduler floating action button + dropdown menu
 * (studyflow-hub/luna/frontend/features/studyflow/study-scheduler.js).
 *
 * Scope in v3: the FAB carries a single menu item — "⚙️ Configuración" —
 * which opens the SCORM credentials panel. The registry (`_menuItems`)
 * is kept extensible so future entries (planner, TTS) can be dropped in
 * exactly like v2 without touching the button/menu plumbing.
 *
 * Public: App.StudyScheduler.mount()  — idempotent, called on boot.
 * ========================================================================== */

window.App = window.App || {};

window.App.StudyScheduler = (function () {
  "use strict";

  let _mounted = false;

  // ── Menu registry: add {id, icon, label, run} here ────────────────
  function _menuItems() {
    return [
      {
        id: "scorm-settings",
        icon: "⚙️",
        label: "Configuración (Moodle)",
        run: () => window.App.ScormSettings.open(),
      },
    ];
  }

  function _esc(s) {
    return String(s).replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }

  function _createButton() {
    if (document.getElementById("study-scheduler-fab")) return;
    const btn = document.createElement("button");
    btn.id = "study-scheduler-fab";
    btn.className = "study-scheduler-fab";
    btn.title = "Menú de estudio";
    btn.setAttribute("aria-haspopup", "menu");
    btn.setAttribute("aria-label", "Menú de estudio");
    btn.textContent = "⋯";
    btn.addEventListener("click", _toggleMenu);
    document.body.appendChild(btn);
    _createMenu();
  }

  function _createMenu() {
    if (document.getElementById("study-scheduler-menu")) return;
    const menu = document.createElement("div");
    menu.id = "study-scheduler-menu";
    menu.className = "study-scheduler-menu";
    menu.setAttribute("role", "menu");
    menu.innerHTML = _menuItems().map((item) => `
      <button type="button" class="study-scheduler-menu-item"
              role="menuitem" data-menu-item="${item.id}">
        <span class="ss-menu-icon">${item.icon}</span>
        <span class="ss-menu-label">${_esc(item.label)}</span>
      </button>`).join("");
    menu.addEventListener("click", (e) => {
      const itemBtn = e.target.closest("[data-menu-item]");
      if (!itemBtn) return;
      _closeMenu();
      const item = _menuItems().find((i) => i.id === itemBtn.dataset.menuItem);
      if (item) item.run();
    });
    document.body.appendChild(menu);
  }

  function _toggleMenu() {
    const menu = document.getElementById("study-scheduler-menu");
    if (!menu) return;
    menu.classList.toggle("open");
  }

  function _closeMenu() {
    const menu = document.getElementById("study-scheduler-menu");
    if (menu) menu.classList.remove("open");
  }

  // Close on outside click / Escape.
  function _bindDismiss() {
    document.addEventListener("click", (e) => {
      if (e.target.closest("#study-scheduler-fab, #study-scheduler-menu")) return;
      _closeMenu();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") _closeMenu();
    });
  }

  function mount() {
    if (_mounted) return;
    _mounted = true;
    _createButton();
    _bindDismiss();
  }

  return { mount };
})();

console.log("[Studyflow] study-scheduler.js loaded");

// Self-mount on boot (idempotent) — the FAB lives in document.body.
// mount() is a member of the returned namespace, not a global, so it has to
// be reached through App.StudyScheduler; calling a bare `mount()` here threw
// "ReferenceError: mount is not defined" and the FAB never appeared.
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", function () {
    window.App.StudyScheduler.mount();
  });
} else {
  window.App.StudyScheduler.mount();
}
