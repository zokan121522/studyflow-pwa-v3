// ─── Agenda QuickNote module — load + auto-save quick note ──────────
// Namespace: window.App.AgendaQuickNote
// Dependencies: authHeaders() from window.App.Auth

window.App = window.App || {};
window.App.AgendaQuickNote = (function () {
  "use strict";

  const { authHeaders } = window.App.Auth;

  // ─── Load quick note from API and wire auto-save on blur ────────
  async function load() {
    const qn = document.getElementById("quick-note");
    if (!qn) return;
    try {
      const r = await fetch("/api/quick-note", { headers: { ...authHeaders() } });
      const data = await r.json();
      qn.value = data.content || "";
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
        await fetch("/api/quick-note", {
          method: "PUT",
          headers: { "Content-Type": "application/json", ...authHeaders() },
          body: JSON.stringify({ content: qn.value }),
        });
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
