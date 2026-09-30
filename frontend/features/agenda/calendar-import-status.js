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

  function paint(status) {
    var el = _el();
    if (!el) return;
    var style = STATES[status && status.state] || STATES.never;
    el.textContent = style.icon + " " + describe(status);
    el.style.color = style.color;
    el.style.cursor = "help";
    el.title = _tooltip(status);
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
    // Also logged to the console: the user asked for a log when opening the
    // agenda, and a console line is what you can paste when something looks
    // wrong. Cheap, and it survives a UI bug.
    if (window.console && console.info) {
      console.info("[calendar] comprobando estado de sincronización…");
    }
    try {
      var status = await API.get("/calendar/status");
      paint(status);
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

  window.CalendarImportStatus = { refresh: refresh, _tooltip: _tooltip, STATES: STATES };
})();