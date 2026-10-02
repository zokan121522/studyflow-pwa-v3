/* Calendar sync status, shown next to the "📅 Calendarios" button.
 *
 * The user asked to be able to tell, just by opening the agenda, whether the
 * calendar is fine and whether anything is waiting. So the chip reports four
 * genuinely different states rather than one reassuring tick:
 *
 *   ok        ✅ synced, N new notices pending
 *   partial   ⚠️ some calendars unreachable — others still imported
 *   stale     🔄 nothing synced for over a day
 *   error     ❌ the last run failed
 *
 * A dead feed is the case this exists for. The importer skips an unreachable
 * calendar and carries on, so an old "all good" would look identical to a
 * healthy one. Colour follows state rather than being decorative.
 */
(function () {
  "use strict";

  var STATES = {
    ok:      { icon: "✅", color: "var(--green,#2e7d32)" },
    partial: { icon: "⚠️", color: "var(--orange,#ef6c00)" },
    stale:   { icon: "🔄", color: "var(--orange,#ef6c00)" },
    error:   { icon: "❌", color: "var(--red,#c62828)" },
    never:   { icon: "❔", color: "var(--text-muted,#888)" }
  };

  // Last status we successfully fetched. The chip is rebuilt from scratch on
  // every renderAgenda(), so a paint that lands before the new markup exists
  // would otherwise be lost and the chip would sit on its "comprobando…"
  // placeholder forever. Keeping the value lets it be re-applied whenever
  // the element comes back.
  var _last = null;

  var _observer = null;

  function describe(s) {
    if (!s) return "estado desconocido";
    var base = s.message || "";
    if (s.pending_notices > 0) {
      var n = s.pending_notices;
      base += " · " + (n === 1 ? "1 aviso pendiente" : n + " avisos pendientes");
    }
    return base;
  }

  function _el() {
    return document.querySelector(".cal-import-status");
  }

  // Set while we are writing to the chip. Without it the observer below sees
  // its own textContent write, calls _apply again, and loops forever — which
  // freezes the tab. Cheap re-entrancy guard.
  var _applying = false;

  function _apply(el) {
    if (!el) return false;
    if (_applying) return true;
    _applying = true;
    try {
      var style = STATES[_last && _last.state] || STATES.never;
      el.textContent = style.icon + " " + describe(_last);
      el.style.color = style.color;
      // A <button> needs no cursor override, but the chip is also rendered as
      // a <span> in older cached markup until the cache bust lands.
      el.style.cursor = "pointer";
      el.title = _tooltip(_last) + "\n\nClic para ver y copiar las nuevas.";
      if (!_wire) _wire = _wireChip(el);
      return true;
    } finally {
      _applying = false;
    }
  }

  /* One delegated listener for the whole document.
   *
   * The chip is destroyed and rebuilt on every renderAgenda(), so binding a
   * handler to the element itself would have to be redone each time and would
   * leak the old nodes' listeners. Delegation off a stable ancestor is the
   * one place that does not move.
   */
  var _wire = false;

  function _wireChip(el) {
    if (_wire) return;
    if (!document.addEventListener) return;
    // Delegated on document: the chip element is replaced on every
    // renderAgenda(), so a per-element listener would be lost with it.
    document.addEventListener("click", function (ev) {
      var chip = ev.target && ev.target.closest &&
        ev.target.closest(".cal-import-status");
      if (!chip) return;
      ev.preventDefault();
      ev.stopPropagation();
      openNotices();
    });
    _wire = true;
  }

  /* Synchronise on demand, then show what came in.
   *
   * The scheduler ticks once a day, so clicking "everything is fine" and having
   * it stay "everything is fine" after deleting two sessions read as a broken
   * importer. It is only a timer. So the click now does the thing the user is
   * asking for: run an import, then report it.
   */
  async function openNotices() {
    if (typeof window.API === "undefined" || !window.API) {
      window.addEventListener("load", function () { openNotices(); }, { once: true });
      return;
    }
    var panel = document.getElementById("cal-notices-panel");
    if (panel) panel.remove();

    var busy = document.getElementById("cal-notices-busy");
    if (busy) busy.remove();
    busy = document.createElement("div");
    busy.id = "cal-notices-busy";
    busy.textContent = "⏳ sincronizando…";
    busy.style.cssText = [
      "position:fixed", "left:50%", "top:64px", "transform:translateX(-50%)",
      "z-index:10000", "padding:8px 14px", "border-radius:10px",
      "background:var(--bg-card,#fff)", "color:var(--text,#111)",
      "border:1px solid var(--border,#ddd)", "font-size:12px",
      "box-shadow:0 6px 24px rgba(0,0,0,.2)"
    ].join(";");
    document.body.appendChild(busy);

    var sync = null;
    var notices = [];
    try {
      sync = await API.post("/calendar/sync", {});
      notices = (sync && sync.notifications) || [];
      if (sync && sync.status) {
        _last = sync.status;
        _apply(_el());
      }
    } catch (e) {
      // The sync failed; fall back to whatever is already pending so the user
      // still sees the notices they came for.
      try {
        var resp = await API.get("/calendar/notifications/pending");
        notices = (resp && resp.notifications) || [];
      } catch (e2) { notices = []; }
    }
    if (busy) busy.remove();

    var n = sync && typeof sync.new_sessions === "number" ? sync.new_sessions : null;
    if (!notices.length) {
      _toast(n === 0 ? "Sincronizado: no hay novedades."
                      : "Sincronizado: " + n + " nuevas.");
      return;
    }

    var box = document.createElement("div");
    box.id = "cal-notices-panel";
    box.className = "cal-import-status";
    box.style.cssText = [
      "position:fixed", "left:50%", "top:64px", "transform:translateX(-50%)",
      "z-index:10000", "width:min(560px,calc(100vw - 32px))",
      "max-height:70vh", "overflow:auto", "white-space:pre-wrap",
      "font-size:13px", "line-height:1.5", "padding:14px 16px",
      "border-radius:12px", "background:var(--bg-card,#fff)",
      "color:var(--text,#111)", "border:1px solid var(--border,#ddd)",
      "box-shadow:0 12px 40px rgba(0,0,0,.24)", "text-align:left"
    ].join(";");

    var combined = notices.map(function (n) { return n.message; }).join("\n\n");
    var body = document.createElement("div");
    body.textContent = combined;

    var bar = document.createElement("div");
    bar.style.cssText = "display:flex;gap:8px;margin-bottom:10px;align-items:center;";

    var copyBtn = document.createElement("button");
    copyBtn.textContent = "📋 Copiar todo";
    copyBtn.style.cssText = "font-size:12px;padding:4px 10px;cursor:pointer;";
    copyBtn.addEventListener("click", async function () {
      var label = copyBtn.textContent;
      var ok = false;
      if (window.CalendarImportNotice) {
        ok = await window.CalendarImportNotice._copy(combined);
      }
      copyBtn.textContent = ok ? "✓ Copiado" : "✕ No se pudo";
      setTimeout(function () { copyBtn.textContent = label; }, 1800);
    });

    var closeBtn = document.createElement("button");
    closeBtn.textContent = "✕";
    closeBtn.title = "Cerrar";
    closeBtn.style.cssText =
      "margin-left:auto;font-size:12px;padding:4px 8px;cursor:pointer;" +
      "border:0;background:transparent;color:var(--text-muted);";
    closeBtn.addEventListener("click", function () {
      box.remove();
      notices.forEach(function (n) {
        if (!n.id) return;
        API.post("/calendar/notifications/" + n.id + "/read", {})
          .catch(function () { /* non-critical */ });
      });
      refresh();
    });

    bar.appendChild(copyBtn);
    bar.appendChild(closeBtn);
    box.appendChild(bar);
    box.appendChild(body);
    document.body.appendChild(box);
  }

  function _toast(text) {
    var t = document.createElement("div");
    t.textContent = text;
    t.style.cssText = [
      "position:fixed", "left:50%", "top:64px", "transform:translateX(-50%)",
      "z-index:10000", "padding:8px 14px", "border-radius:10px",
      "background:var(--bg-card,#fff)", "color:var(--text,#111)",
      "border:1px solid var(--border,#ddd)", "font-size:12px",
      "box-shadow:0 6px 24px rgba(0,0,0,.2)"
    ].join(";");
    document.body.appendChild(t);
    setTimeout(function () { t.remove(); }, 2200);
  }

  function paint(status) {
    if (status) _last = status;
    return _apply(_el());
  }

  /* Re-apply the cached status whenever the chip is (re)inserted.
   *
   * renderAgenda() replaces the whole side panel, so the chip is a brand new
   * element on every render. Watching for it is the only way to cover every
   * render path without remembering to call refresh() from each one — and
   * the alternative was a chip that silently reverts to "comprobando…".
   *
   * Two guards keep this from being a trap:
   *   - only mutations that actually ADD the chip are acted on, not every
   *     DOM change in an app that re-renders constantly;
   *   - _apply is re-entrancy guarded, because writing the chip's own
   *     textContent is itself a mutation.
   */
  function _observerWantsChip(mutations) {
    for (var i = 0; i < mutations.length; i++) {
      var added = mutations[i].addedNodes;
      for (var j = 0; j < added.length; j++) {
        var n = added[j];
        if (n.nodeType !== 1) continue;
        if (n.classList && n.classList.contains("cal-import-status")) return true;
        if (n.querySelector && n.querySelector(".cal-import-status")) return true;
      }
    }
    return false;
  }

  function _observe() {
    if (_observer || !document.body || !window.MutationObserver) return;
    // window.MutationObserver, matching the guard above — a bare reference
    // works in a browser but is not the same binding everywhere.
    _observer = new window.MutationObserver(function (mutations) {
      if (!_last) return;
      if (_observerWantsChip(mutations)) _apply(_el());
    });
    _observer.observe(document.body, { childList: true, subtree: true });
  }

  function repaint() {
    _observe();
    return _apply(_el());
  }

  function _tooltip(status) {
    if (!status || !status.last_run) return "Sin ejecuciones registradas";
    var r = status.last_run;
    var lines = [
      "Última ejecución: " + new Date(r.started_at).toLocaleString(),
      "Resultado: " + r.outcome,
      "Calendarios OK: " + r.calendars_ok +
        (r.calendars_bad ? " · fallidos: " + r.calendars_bad : ""),
      "Nuevas: " + r.new_sessions + " · actualizadas: " + r.updated +
        " · fuera de ventana: " + r.skipped
    ];
    if (r.detail) lines.push("Detalle: " + r.detail);
    if (status.last_successful_run) {
      lines.push("Última sincronización correcta: " +
        new Date(status.last_successful_run).toLocaleString());
    }
    return lines.join("\n");
  }

  async function refresh() {
    _observe();
    // app.js defines the API layer and is loaded after this file, so at boot
    // the first call can find nothing to talk to. Wait for the page rather
    // than reporting a failure that is really just load order.
    if (typeof window.API === "undefined" || !window.API) {
      if (window.console && console.info) {
        console.info("[calendar] API todavía no disponible, reintento al cargar");
      }
      window.addEventListener("load", function () { refresh(); }, { once: true });
      return null;
    }
    // Also logged to the console: the user asked for a log when opening the
    // agenda, and a console line is what you can paste when something looks
    // wrong. Cheap, and it survives a UI bug.
    if (window.console && console.info) {
      console.info("[calendar] comprobando estado de sincronización…");
    }
    try {
      var status = await API.get("/calendar/status");
      _last = status;
      _apply(_el());
      if (window.console && console.info) console.info("[calendar]", status);
      return status;
    } catch (e) {
      var el = _el();
      if (el) {
        el.textContent = "❔ no se pudo comprobar";
        el.style.color = "var(--text-muted,#888)";
      }
      if (window.console && console.warn) {
        console.warn("[calendar] status check failed", e);
      }
      return null;
    }
  }

  window.CalendarImportStatus = {
    refresh: refresh,
    repaint: repaint,
    paint: paint,
    _tooltip: _tooltip,
    openNotices: openNotices,
    STATES: STATES,
    _last: function () { return _last; }
  };
})();