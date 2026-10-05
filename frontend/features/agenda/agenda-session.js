// ─── Agenda Session — session CRUD overlay ──────────────────────
// Namespace: window.App.AgendaSession
// Dependencies: API (global), window.App.UI (timer fields)

window.App = window.App || {};
window.App.AgendaSession = (function () {
  "use strict";

  const { _setTimerFields, _getTimerSeconds, _updateTimerEffective } = window.App.UI;

  let _sessionDate = null;
  let _sessionWeekId = null;
  let _editingSessionId = null;
  let _onSaved = null; // callback after save (triggers re-render)

  // ─── Render notes preview from textarea content ────────────────
  function _renderNotesPreview() {
    var preview = document.getElementById("so-notes-preview");
    var textarea = document.getElementById("so-notes");
    if (!preview || !textarea) return;
    var md = textarea.value || "";
    if (md.trim()) {
      preview.innerHTML = App.ContentBlocks._renderMd(md);
    } else {
      preview.innerHTML = '<span style="color:var(--text-muted);font-style:italic;">Sin notas</span>';
    }
  }

  // ─── Load categories into dropdown ────────────────────────────────
  async function loadCategories() {
    const sel = document.getElementById("so-categoria");
    if (!sel) return;
    try {
      const cats = await API.get("/agenda/categories");
      sel.innerHTML = Object.entries(cats).map(([key, cat]) =>
        `<option value="${key}" data-icon="${cat.icon || ''}">${cat.icon || ''} ${cat.label}</option>`
      ).join("");
    } catch {
      // Fallback: leave current options in place
    }
  }

  // ─── Open overlay for create / edit ──────────────────────────────
  function openSessionOverlay(dateStr, weekId, session, onSavedCallback) {
    _sessionDate = dateStr;
    _sessionWeekId = weekId;
    _editingSessionId = session?.id || null;
    _onSaved = onSavedCallback || null;

    const d = new Date(dateStr + "T12:00:00");
    const dayName = d.toLocaleDateString("es-ES", { weekday: "long", day: "numeric", month: "long" });

    // Load categories into dropdown, then set value
    loadCategories().then(() => {
      document.getElementById("so-categoria").value = session?.category || "formal_study";
    });

    // Change title / save button text based on edit mode
    const titleEl = document.querySelector("#session-overlay .omodal-head h3");
    const saveBtn = document.getElementById("so-save");
    // Delete / move / copy only make sense on a session that already exists.
    // Hiding them on create avoids a "delete" button for something with no id.
    const dangerActs = document.getElementById("so-danger-acts");
    if (session) {
      titleEl.textContent = `✏️ Editar sesión — ${dayName}`;
      saveBtn.textContent = "💾 Guardar cambios";
      if (dangerActs) dangerActs.hidden = false;
    } else {
      titleEl.textContent = `➕ Nueva sesión — ${dayName}`;
      saveBtn.textContent = "➕ Guardar sesión";
      if (dangerActs) dangerActs.hidden = true;
    }

    document.getElementById("so-titulo").value = session?.title || "";
    document.getElementById("so-start").value = session?.start_time || "";
    document.getElementById("so-end").value = session?.end_time || "";
    document.getElementById("so-notes").value = session?.notes || "";

    // Render notes preview (View tab is default)
    _renderNotesPreview();

    // Populate timer values (elapsed / paused)
    const elapsed = session?.timer_elapsed || 0;
    const paused = session?.timer_paused_duration || 0;
    _setTimerFields("so-te", elapsed);
    _setTimerFields("so-tp", paused);
    _updateTimerEffective();

    document.getElementById("session-overlay").classList.add("open");
    setTimeout(() => document.getElementById("so-titulo").focus(), 100);
  }

  // ─── Close overlay ───────────────────────────────────────────────
  function closeSessionOverlay() {
    document.getElementById("session-overlay").classList.remove("open");
    _sessionDate = null;
    _sessionWeekId = null;
    _editingSessionId = null;
  }

  // ─── Save (create or update) ─────────────────────────────────────
  async function saveSession() {
    const titulo = document.getElementById("so-titulo").value.trim();
    const categoria = document.getElementById("so-categoria").value;
    const start = document.getElementById("so-start").value;
    const end = document.getElementById("so-end").value;
    const notes = document.getElementById("so-notes").value.trim();

    if (!titulo) {
      document.getElementById("so-titulo").focus();
      document.getElementById("so-titulo").style.borderColor = "var(--red)";
      setTimeout(() => document.getElementById("so-titulo").style.borderColor = "", 2000);
      return;
    }

    try {
      if (_editingSessionId) {
        // ─── EDIT mode: PATCH session fields ───────────────────
        const timerElapsed = _getTimerSeconds("so-te");
        const timerPaused = _getTimerSeconds("so-tp");
        await API.patch(`/agenda/session/${_editingSessionId}`, {
          title: titulo,
          category: categoria,
          start_time: start || null,
          end_time: end || null,
          notes: notes || null,
          timer_elapsed: timerElapsed,
          timer_paused_duration: timerPaused,
        });
        closeSessionOverlay();
        if (_onSaved) _onSaved();
      } else {
        // ─── CREATE mode: add session to week data ─────────────
        // The timer fields are included here too. They used to be missing,
        // so setting effective time while *adding* a session discarded it
        // silently -- the form showed 01:15:00 and the saved row had NULL.
        // Only editing an existing session persisted them.
        const timerElapsed = _getTimerSeconds("so-te");
        const timerPaused = _getTimerSeconds("so-tp");
        const session = {
          id: crypto.randomUUID?.() || Date.now().toString() + Math.random().toString(36).slice(2, 6),
          title: titulo,
          category: categoria,
          state: "pending",
          start_time: start || null,
          end_time: end || null,
          notes: notes || null,
          timer_elapsed: timerElapsed,
          timer_paused_duration: timerPaused,
        };

        const weekData = await API.get(`/agenda/week/${_sessionWeekId}`);
        const dateStr = _sessionDate;
        const day = weekData.days?.[dateStr] || { date: dateStr, sessions: [] };
        day.sessions = day.sessions || [];
        day.sessions.push(session);
        await API.patch(`/agenda/week/${_sessionWeekId}/day/${dateStr}`, { sessions: day.sessions });
        closeSessionOverlay();
        if (_onSaved) _onSaved();
      }
    } catch (e) {
      alert("Error al guardar la sesión. Inténtalo de nuevo.");
    }
  }

  // ─── Init: wire overlay event handlers ───────────────────────────
  function init() {
    // Delete / move / copy — bound once, since the buttons live in the static
    // overlay markup and the overlay is shown/hidden rather than rebuilt.
    _wireDangerActions();

    // Timer input change → update effective display
    ["so-te-h","so-te-m","so-te-s","so-tp-h","so-tp-m","so-tp-s"].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.addEventListener("input", _updateTimerEffective);
    });

    // Session overlay event handlers
    document.getElementById("session-overlay").addEventListener("click", (e) => {
      if (e.target === e.currentTarget) closeSessionOverlay();
    });
    document.getElementById("so-close-btn").addEventListener("click", closeSessionOverlay);
    document.getElementById("so-cancel").addEventListener("click", closeSessionOverlay);
    document.getElementById("so-save").addEventListener("click", saveSession);

    // Category button → open category overlay (defined in app.js)
    document.getElementById("so-cat-add").addEventListener("click", () => {
      openCategoryOverlay();
    });

    // ─── Notes tab switching (View / Edit) ──────────────────────
    document.querySelectorAll(".so-ntab").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var tab = btn.dataset.tab;

        // Toggle tab buttons
        document.querySelectorAll(".so-ntab").forEach(function (b) {
          b.classList.toggle("active", b === btn);
        });

        // Toggle panels
        document.querySelectorAll(".so-notes-panel").forEach(function (p) {
          p.classList.toggle("active", p.classList.contains("so-notes-" + tab));
        });

        // When switching to View, re-render preview from textarea
        if (tab === "view") {
          _renderNotesPreview();
        }
      });
    });
  }

  // ─── Delete / move / copy an existing session ───────────────────
  // All three endpoints already existed and were used from the session cards;
  // what was missing was a way to reach them once a session was opened. Being
  // marked done is not a lock — it is a state, and the session stays editable.

  function _toast(msg, isError) {
    // Reuse the app's existing .br-toast rather than adding a second one.
    let el = document.getElementById("br-toast");
    if (!el) {
      el = document.createElement("div");
      el.id = "br-toast";
      el.className = "br-toast";
      document.body.appendChild(el);
    }
    el.textContent = msg;
    el.classList.toggle("error", !!isError);
    el.classList.add("show");
    clearTimeout(el._t);
    el._t = setTimeout(() => el.classList.remove("show"), 3800);
  }

  function _afterMutation(message, isError) {
    closeSessionOverlay();
    if (window.App && window.App.Agenda && window.App.Agenda.renderAgenda) {
      window.App.Agenda.renderAgenda();
    }
    if (message) _toast(message, isError);
  }

  function _confirmDelete() {
    if (!_editingSessionId) return;
    // Confirm on purpose: this is the only irreversible action in the app.
    if (!window.confirm("¿Eliminar esta sesión?\n\nNo se puede deshacer.")) return;
    API.del("/agenda/session/" + _editingSessionId)
      .then(function () { _afterMutation("Sesión eliminada."); })
      .catch(function (e) {
          console.error("[agenda] delete failed", e);
          _toast("No se pudo eliminar la sesión.", true);
        });
  }

  function _copySession() {
    if (!_editingSessionId) return;
    API.post("/agenda/session/" + _editingSessionId + "/copy", {})
      .then(function () { _afterMutation("Sesión copiada."); })
      .catch(function (e) {
          console.error("[agenda] copy failed", e);
          _toast("No se pudo copiar la sesión.", true);
        });
  }

  function _moveSession() {
    if (!_editingSessionId) return;
    var input = window.prompt("Mover a otro día (AAAA-MM-DD):", _sessionDate || "");
    if (input === null) return;                   // cancelled
    var target = String(input).trim();
    if (!/^\d{4}-\d{2}-\d{2}$/.test(target)) {
      window.alert("Formato inválido. Usa AAAA-MM-DD, por ejemplo 2026-10-15.");
      return;
    }
    API.post("/agenda/session/" + _editingSessionId + "/move", { target_date: target })
      .then(function () { _afterMutation("Sesión movida al " + target + "."); })
      .catch(function (e) {
          console.error("[agenda] move failed", e);
          _toast("No se pudo mover la sesión.", true);
        });
  }

  function _wireDangerActions() {
    var del = document.getElementById("so-delete");
    var mv = document.getElementById("so-move");
    var cp = document.getElementById("so-copy");
    if (del) del.addEventListener("click", _confirmDelete);
    if (mv) mv.addEventListener("click", _moveSession);
    if (cp) cp.addEventListener("click", _copySession);
  }

  // ─── Public API ──────────────────────────────────────────────────
  return {
    init,
    loadCategories,
    openSessionOverlay,
    closeSessionOverlay,
    saveSession,
    _wireDangerActions: _wireDangerActions,
  };
})();
