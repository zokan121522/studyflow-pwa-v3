// ─── Agenda Calendars — external ICS import panel (Phase 71, #262) ───
// Namespace: window.App.AgendaCalendars
// Dependencies: API (global), window.App.Agenda (render refresh)

window.App = window.App || {};
window.App.AgendaCalendars = (function () {
  "use strict";

  // ─── State ────────────────────────────────────────────────────────
  let _onRefresh = null;     // callback to re-render the agenda after import
  let _calendars = [];       // [{id, name, url_masked}, ...]
  // Mirrors calendar_import.window.VALID_DAYS — 60 is the "2 meses"
  // option. Kept as a literal rather than fetched: one extra round trip
  // to render three radios is not worth it, and test_js_day_options.py
  // fails if the two ever drift.
  var VALID_DAYS = [7, 15, 30, 60];

  let _days = 7;             // selected import range

  // ─── Render the panel contents ────────────────────────────────────
  function _renderList() {
    var ul = document.getElementById("cal-list");
    if (!ul) return;
    // Sync the import-target dropdown too
    var sel = document.getElementById("cal-import-target");
    if (sel) {
      sel.innerHTML = _calendars.length
        ? _calendars.map(function (c) {
            return '<option value="' + _esc(c.id) + '">' + _esc(c.name) + '</option>';
          }).join("")
        : '<option value="" disabled selected>— añade un calendario primero —</option>';
    }
    if (!_calendars.length) {
      ul.innerHTML = '<div style="font-size:11px;color:var(--text-muted);padding:6px 0;">' +
        'No hay calendarios guardados. Añade uno abajo (URL del feed ICS).</div>';
      return;
    }
    ul.innerHTML = _calendars.map(function (c) {
      return '<div class="cal-row" data-id="' + c.id + '" style="' +
        'display:flex;align-items:center;gap:8px;padding:8px 10px;' +
        'border:1px solid var(--border);border-radius:6px;background:var(--bg);' +
        'margin-bottom:6px;font-size:12px;">' +
        '<div style="flex:1;min-width:0;">' +
          '<div style="font-weight:600;color:var(--text);">' + _esc(c.name) + '</div>' +
          '<div class="cal-url" data-masked="' + _esc(c.url_masked) + '" ' +
            'style="font-size:10px;color:var(--text-muted);' +
            'font-family:var(--font-mono,monospace);' +
            'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' +
            _esc(c.url_masked) +
          '</div>' +
        '</div>' +
        '<button class="ht-btn cal-toggle" data-action="toggle-url" title="Mostrar/ocultar URL" ' +
          'style="font-size:11px;padding:3px 7px;">👁</button>' +
        '<button class="ht-btn" data-action="delete-cal" ' +
          'style="font-size:11px;padding:3px 7px;color:var(--red);" title="Borrar">✕</button>' +
      '</div>';
    }).join("");
  }

  // ─── Tiny HTML escaper ────────────────────────────────────────────
  function _esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  // ─── Open the panel ────────────────────────────────────────────────
  async function openOverlay(onRefresh) {
    _onRefresh = onRefresh || null;
    document.getElementById("calendar-overlay").classList.add("open");
    await _loadAndRender();
  }

  // ─── Close the panel ──────────────────────────────────────────────
  function closeOverlay() {
    document.getElementById("calendar-overlay").classList.remove("open");
    _onRefresh = null;
  }

  // ─── Fetch the saved calendars ────────────────────────────────────
  async function _loadAndRender() {
    var status = document.getElementById("cal-status");
    if (status) status.textContent = "Cargando…";
    try {
      var resp = await API.get("/calendar/calendars");
      _calendars = (resp && resp.calendars) || [];
    } catch (e) {
      _calendars = [];
      if (status) status.textContent = "❌ Error al listar calendarios";
      return;
    }
    _renderList();
    if (status) status.textContent = _calendars.length
      ? (_calendars.length + " calendario" + (_calendars.length === 1 ? "" : "s"))
      : "Sin calendarios";
  }

  // ─── PUT current state to /api/calendar/calendars ─────────────────
  // We keep an in-memory authoritative list (matching what's on the server)
  // and re-send the whole array on every mutation — small enough (<=5).
  // Real URLs live in `_real_url`; the masked form from GET is shown to the
  // user but discarded on PUT (we'd never want to round-trip a stripped URL
  // back to the server).
  async function _saveCurrent() {
    var payload = { calendars: _calendars.map(function (c) {
      return { id: c.id, name: c.name, url: c._real_url || "" };
    }).filter(function (c) { return c.url; }) };
    try {
      var resp = await API.put("/calendar/calendars", payload);
      _calendars = (resp && resp.calendars) || [];
    } catch (e) {
      alert("❌ Error al guardar calendarios: " + (e.message || e));
      throw e;
    }
    _renderList();
  }

  // ─── Add new calendar (PUT) ───────────────────────────────────────
  async function _onAdd() {
    var nameEl = document.getElementById("cal-new-name");
    var urlEl = document.getElementById("cal-new-url");
    var name = (nameEl.value || "").trim();
    var url = (urlEl.value || "").trim();
    if (!name || !url) {
      nameEl.style.borderColor = url ? "" : "var(--red)";
      urlEl.style.borderColor = url ? "" : "var(--red)";
      setTimeout(function () {
        nameEl.style.borderColor = "";
        urlEl.style.borderColor = "";
      }, 1500);
      return;
    }
    if (_calendars.length >= 5) {
      alert("Máximo 5 calendarios.");
      return;
    }
    // Optimistic local update — keep the real URL in _real_url so
    // subsequent PUTs round-trip it back to the server.
    _calendars.push({ id: "", name: name, url_masked: url, _real_url: url });
    try {
      await _saveCurrent();
    } catch (e) {
      _calendars.pop();
      return;
    }
    nameEl.value = "";
    urlEl.value = "";
  }

  // ─── Delete one calendar ──────────────────────────────────────────
  async function _onDelete(id) {
    var before = _calendars.length;
    _calendars = _calendars.filter(function (c) { return c.id !== id; });
    if (_calendars.length === before) return;
    try {
      await _saveCurrent();
    } catch (e) {
      // Roll back on failure
      await _loadAndRender();
    }
  }

  // ─── Toggle URL visibility ────────────────────────────────────────
  function _onToggleUrl(row) {
    var urlEl = row.querySelector(".cal-url");
    var btn = row.querySelector(".cal-toggle");
    if (!urlEl) return;
    var masked = urlEl.dataset.masked || urlEl.textContent;
    var full = row.dataset.fullUrl || "";
    if (btn.dataset.shown === "1") {
      urlEl.textContent = masked;
      btn.textContent = "👁";
      btn.dataset.shown = "0";
    } else {
      if (!full) {
        // Server only returned the masked form; nothing to reveal here.
        // The full URL is held in our local _real_url field if available.
        var cid = row.dataset.id;
        var cal = _calendars.find(function (c) { return c.id === cid; });
        if (cal && cal._real_url) {
          full = cal._real_url;
          row.dataset.fullUrl = full;
        } else {
          return; // no full URL available — keep showing the masked form
        }
      }
      urlEl.textContent = full;
      btn.textContent = "🙈";
      btn.dataset.shown = "1";
    }
  }

  // ─── Run an import against one of the saved calendars ────────────
  async function _onImport(calendarId, days, templateNotes) {
    var status = document.getElementById("cal-import-status");
    status.textContent = "⏳ Importando…";
    status.style.color = "var(--text-muted)";
    try {
      var payload = { calendar_id: calendarId, days: days };
      // Only attach template_notes when the textarea exists in the DOM.
      // Empty string is a valid value (user may clear the template) — the
      // backend treats "" as "no template" and keeps current notes behaviour.
      if (typeof templateNotes === "string") {
        payload.template_notes = templateNotes;
      }
      var resp = await API.post("/calendar/import", payload);
      var imp = (resp && resp.imported) || 0;
      var upd = (resp && resp.updated) || 0;
      var skp = (resp && resp.skipped_out_of_range) || 0;
      status.innerHTML =
        '<span style="color:var(--green);">✅ ' + imp + ' creadas</span> · ' +
        '<span style="color:var(--blue);">' + upd + ' actualizadas</span> · ' +
        '<span style="color:var(--text-muted);">' + skp + ' fuera de rango</span>';
      // R5: trigger a refresh of the current week. NEVER call
      // saveWeek from here — the import replaces only its own `cal-`
      // rows and the user may still have unsaved edits in the
      // session panel that a full replace would clobber.
      if (_onRefresh) {
        try { await _onRefresh(); } catch (e) { /* noop */ }
      }
    } catch (e) {
      status.innerHTML = '<span style="color:var(--red);">❌ ' +
        _esc(e.message || "Error al importar") + '</span>';
    }
  }

  // ─── Init: one-time event wiring ──────────────────────────────────
  function init() {
    var overlay = document.getElementById("calendar-overlay");
    if (!overlay) return;

    overlay.addEventListener("click", function (e) {
      if (e.target === e.currentTarget) closeOverlay();
    });
    document.getElementById("cal-close-btn").addEventListener("click", closeOverlay);

    document.getElementById("cal-add-btn").addEventListener("click", _onAdd);

    // List delegated events (toggle URL / delete)
    document.getElementById("cal-list").addEventListener("click", function (e) {
      var row = e.target.closest(".cal-row");
      if (!row) return;
      var action = e.target.dataset && e.target.dataset.action;
      if (action === "toggle-url") {
        _onToggleUrl(row);
      } else if (action === "delete-cal") {
        if (confirm("¿Borrar este calendario?")) _onDelete(row.dataset.id);
      }
    });

    // Range radio
    document.querySelectorAll('input[name="cal-days"]').forEach(function (r) {
      r.addEventListener("change", function () {
        var v = parseInt(r.value, 10);
        if (VALID_DAYS.indexOf(v) !== -1) _days = v;
      });
    });

    // Import button — runs against whichever calendar the user picks
    document.getElementById("cal-import-btn").addEventListener("click", function () {
      var sel = document.getElementById("cal-import-target");
      var id = sel && sel.value;
      if (!id) {
        alert("Selecciona un calendario para importar.");
        return;
      }
      var days = parseInt(
        (document.querySelector('input[name="cal-days"]:checked') || {}).value, 10
      );
      if (VALID_DAYS.indexOf(days) === -1) days = 7;
      var tplEl = document.getElementById("cal-template-notes");
      // Read raw — backend trims + caps. undefined ⇒ don't send the key at all.
      var templateNotes = tplEl ? tplEl.value : undefined;
      _onImport(id, days, templateNotes);
    });
  }

  // ─── Public API ───────────────────────────────────────────────────
  return {
    init,
    openOverlay,
    closeOverlay,
  };
})();