/* Pending-import notice for the calendar panel.
 *
 * The user asked for an in-app notice they could copy, so the banner is
 * text-only: no rich formatting, because the whole point is pasting it into
 * a notes app or a chat where markdown renders badly. The message text is
 * built server-side by calendar_import/notifications.py.
 *
 * Dismissal happens on close, not on read — the user asked to be told about
 * new sessions, not to be made to acknowledge them.
 */
(function () {
  "use strict";

  var BANNER_ID = "cal-import-banner";

  function _esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function _remove() {
    var el = document.getElementById(BANNER_ID);
    if (el) el.remove();
  }

  async function _copy(text) {
    // navigator.clipboard needs a secure context; the app is served over
    // Tailscale HTTPS but also runs on plain localhost and http, so the
    // execCommand path is still needed rather than theoretical.
    if (navigator.clipboard && window.isSecureContext) {
      try {
        await navigator.clipboard.writeText(text);
        return true;
      } catch (e) { /* fall through */ }
    }
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    var ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    document.body.removeChild(ta);
    return ok;
  }

  function _render(notices) {
    _remove();
    if (!notices || !notices.length) return;

    var texts = notices.map(function (n) { return n.message; });
    var combined = texts.join("\n\n");

    var el = document.createElement("div");
    el.id = BANNER_ID;
    el.className = "cal-import-banner";
    el.style.cssText = [
      "position:fixed", "bottom:16px", "right:16px", "z-index:9999",
      "max-width:340px", "white-space:pre-wrap", "font-size:13px",
      "line-height:1.45", "padding:12px 14px", "border-radius:10px",
      "background:var(--bg-card,#fff)", "color:var(--text,#111)",
      "border:1px solid var(--border,#ddd)", "box-shadow:0 6px 24px rgba(0,0,0,.18)"
    ].join(";");
    el.innerHTML =
      '<div style="display:flex;gap:8px;align-items:flex-start">' +
        '<div style="flex:1">' + _esc(combined) + '</div>' +
        '<button class="cal-banner-copy" style="flex:0 0 auto;font-size:12px;' +
          'padding:3px 8px;cursor:pointer;">📋 Copiar</button>' +
        '<button class="cal-banner-close" style="flex:0 0 auto;font-size:12px;' +
          'padding:3px 6px;cursor:pointer;border:0;background:transparent;' +
          'color:var(--text-muted);" title="Cerrar">✕</button>' +
      "</div>" +
      '<textarea class="cal-banner-raw" style="position:fixed;left:-9999px;" ' +
        'aria-hidden="true"></textarea>';

    var raw = el.querySelector(".cal-banner-raw");
    if (raw) raw.value = combined;

    el.querySelector(".cal-banner-copy").addEventListener("click", async function () {
      var btn = this;
      var label = btn.textContent;
      var ok = await _copy(combined);
      btn.textContent = ok ? "✓ Copiado" : "✕ No se pudo";
      setTimeout(function () { btn.textContent = label; }, 1800);
    });

    el.querySelector(".cal-banner-close").addEventListener("click", function () {
      _remove();
    });

    document.body.appendChild(el);

    // Dismissing marks every shown notice as read. A failure here must not
    // leave the banner stuck: the user still has the close button.
    notices.forEach(function (n) {
      if (!n.id) return;
      API.post("/calendar/notifications/" + n.id + "/read", {})
        .catch(function () { /* non-critical */ });
    });
  }

  async function refresh() {
    // Wait for API layer just like the status chip does, because the script is
    // loaded before app.js and a synchronous call would throw and be swallowed.
    if (typeof window.API === "undefined" || !window.API) {
      window.addEventListener("load", function () { refresh(); }, { once: true });
      return;
    }
    try {
      var resp = await API.get("/calendar/notifications/pending");
      _render((resp && resp.notifications) || []);
    } catch (e) {
      // No endpoint or no token yet — stay silent, this is not an error the
      // user needs to see.
    }
  }

  window.CalendarImportNotice = { refresh: refresh, _copy: _copy, _esc: _esc };
})();