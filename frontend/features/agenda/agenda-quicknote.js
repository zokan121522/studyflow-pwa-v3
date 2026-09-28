// ─── Agenda QuickNote module — load + auto-save quick note ──────────
// Namespace: window.App.AgendaQuickNote
// Dependencies: API (global, defined in app.js — routes through API_URL on :8082
//                and adds the auth header; the static server on :3000 has no
//                /api/* route, so raw fetch() previously 404'd).

window.App = window.App || {};
window.App.AgendaQuickNote = (function () {
  "use strict";

  // ─── Load quick note from API and wire auto-save on blur ────────
  async function load() {
    const qn = document.getElementById("quick-note");
    if (!qn) return;
    try {
      const data = await API.get("/quick-note");
      qn.value = (data && data.content) || "";
    } catch {
      qn.value = "";
    }

    // Auto-save on blur with status indicator
    const statusEl = document.getElementById("qn-status");
    if (!statusEl) return;
    qn.addEventListener("blur", async () => {
      statusEl.textContent = "◌";
      statusEl.className = "note-status saving";
      try {
        await API.put("/quick-note", { content: qn.value });
        statusEl.textContent = "✓";
        statusEl.className = "note-status ok";
        setTimeout(() => {
          statusEl.textContent = "●";
          statusEl.className = "note-status";
        }, 2500);
      } catch {
        statusEl.textContent = "✗";
        statusEl.className = "note-status err";
        setTimeout(() => {
          statusEl.textContent = "●";
          statusEl.className = "note-status";
        }, 2500);
      }
    });
  }

  // ─── Public API ──────────────────────────────────────────────────
  return {
    load,
  };
})();
