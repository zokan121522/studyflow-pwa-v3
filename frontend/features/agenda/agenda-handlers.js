/* ============================== AGENDA GLOBAL HANDLERS ==============================
   One-time wiring for the agenda tab's overlay + focus-mode behaviour. Lives
   outside app.js so the bootstrap file stays under the 500-line ceiling.

   Loaded by index.html AFTER agenda.js — at this point App.Agenda, App.modules.Agenda,
   App.AgendaSession, App.Auth (from agenda-glue) all exist.

   Exposes: openCategoryOverlay, closeCategoryOverlay, loadCategoryList,
   _closeTopOverlay (Escape handler), focus-mode toggle, category/columns/notes
   overlay close + add button wiring. */

(function () {
  "use strict";

  // ─── Close any open overlay (top-most first by z-order) ──────────
  function _closeTopOverlay() {
    var ids = ["calendar-overlay", "move-session-overlay", "category-overlay",
               "columns-overlay", "session-overlay", "notes-habit-overlay"];
    for (var i = 0; i < ids.length; i++) {
      var el = document.getElementById(ids[i]);
      if (el && el.classList.contains("open")) {
        el.classList.remove("open");
        return true;
      }
    }
    return false;
  }

  // ─── Category overlay: opens when so-cat-add is clicked inside the
  // session modal. Delegates to /agenda/categories. ────────────────
  async function openCategoryOverlay() {
    var el = document.getElementById("category-overlay");
    if (!el) return;
    el.classList.add("open");
    await loadCategoryList();
  }
  function closeCategoryOverlay() {
    var el = document.getElementById("category-overlay");
    if (el) el.classList.remove("open");
  }
  async function loadCategoryList() {
    var el = document.getElementById("co-list");
    if (!el) return;
    var cats = {};
    try { cats = await API.get("/agenda/categories"); } catch (e) { el.innerHTML = ""; return; }
    var entries = Object.entries(cats);
    if (!entries.length) {
      el.innerHTML = '<div style="color:var(--text-muted);font-size:12px;padding:6px 0;">Sin categorías personalizadas</div>';
      return;
    }
    el.innerHTML = entries.map(function (kv) {
      var key = kv[0], cat = kv[1];
      return '<div class="co-item ' + (cat.builtin ? "builtin" : "") + '">' +
        '<span>' + (cat.icon || "") + " " + cat.label + '</span>' +
        (cat.builtin
          ? '<span style="font-size:9px;color:var(--text-muted);margin-left:auto;">integrada</span>'
          : '<span class="co-del" data-key="' + key + '">🗑️</span>') +
      '</div>';
    }).join("");
    el.querySelectorAll(".co-del").forEach(function (del) {
      del.addEventListener("click", async function () {
        var key = del.dataset.key;
        if (!confirm('¿Borrar categoría "' + key + '"?')) return;
        try {
          await API.del("/agenda/categories/" + encodeURIComponent(key));
          loadCategoryList();
          if (window.App.AgendaSession && window.App.AgendaSession.loadCategories) {
            window.App.AgendaSession.loadCategories();
          }
        } catch (e) { alert("Error al borrar categoría"); }
      });
    });
  }

  // ─── Wire all the one-time global handlers ───────────────────────
  function _wire() {
    // Escape closes top-most overlay
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") _closeTopOverlay();
    });
    // ⌘/Ctrl+B + body double-tap → toggle focus-mode (CSS expands center column)
    document.addEventListener("keydown", function (e) {
      if ((e.metaKey || e.ctrlKey) && e.key === "b") {
        e.preventDefault();
        var main = document.querySelector(".main");
        if (main) main.classList.toggle("sf-focus-mode");
      }
    });
var mainEl = document.querySelector(".main");
      if (mainEl) {
        // dblclick eliminado a petición del usuario (no le gustaba)
      }

    // Category overlay
    document.getElementById("category-overlay")?.addEventListener("click", function (e) {
      if (e.target === e.currentTarget) closeCategoryOverlay();
    });
    document.getElementById("co-close-btn")?.addEventListener("click", closeCategoryOverlay);
    document.getElementById("co-add")?.addEventListener("click", async function () {
      var input = document.getElementById("co-name");
      var name = (input.value || "").trim();
      if (!name) return;
      try {
        await API.post("/agenda/categories", {
          key: name.toLowerCase().replace(/\s+/g, "_"),
          label: name,
        });
        input.value = "";
        loadCategoryList();
        if (window.App.AgendaSession && window.App.AgendaSession.loadCategories) {
          window.App.AgendaSession.loadCategories();
        }
      } catch (e) { alert("Error al añadir categoría (puede que ya exista)"); }
    });

    // Columns overlay (sub-phase D — wired to App.Habits.addColumn)
    document.getElementById("columns-overlay")?.addEventListener("click", function (e) {
      if (e.target === e.currentTarget) e.currentTarget.classList.remove("open");
    });
    document.getElementById("clo-close-btn")?.addEventListener("click", function () {
      document.getElementById("columns-overlay")?.classList.remove("open");
    });
    document.getElementById("clo-add")?.addEventListener("click", function () {
      if (window.App.Habits && window.App.Habits.addColumn) {
        window.App.Habits.addColumn();
      }
    });

    // Habit-notes overlay (✕ button — the overlay shell also closes on
    // backdrop click via the Escape handler iterating overlay IDs)
    document.getElementById("nho-close-btn")?.addEventListener("click", function () {
      document.getElementById("notes-habit-overlay")?.classList.remove("open");
    });
  }

  // ─── Sync App.state ↔ STATE (one-way: App.state → STATE) ────────
  function _syncStateFromApp() {
    if (typeof window.STATE === "undefined" || !window.App) return;
    window.STATE.currentWeek = window.App.state.currentWeek;
    window.STATE.currentDay = window.App.state.currentDay;
    window.STATE.activeTab = window.App.state.activeTab;
    if (!window.STATE.selectedHabitDay) window.STATE.selectedHabitDay = window.App.state.currentDay;
  }

  // Expose for app.js + agenda modules
  window.AgendaGlobals = {
    openCategoryOverlay: openCategoryOverlay,
    closeCategoryOverlay: closeCategoryOverlay,
    loadCategoryList: loadCategoryList,
    closeTopOverlay: _closeTopOverlay,
    syncStateFromApp: _syncStateFromApp,
  };

  // Auto-wire on DOMContentLoaded (modules may also be already loaded)
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", _wire);
  } else {
    _wire();
  }
})();

console.log("[Agenda] global handlers loaded");
