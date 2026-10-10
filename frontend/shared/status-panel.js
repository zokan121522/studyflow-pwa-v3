/* ============================== STATUS PANEL (poll /api/status) ==============================
   Header pills next to the API one: NotebookLM dot, OpenCode dot, version text.
   Mirrors shared/server-status.js: immediate check on load, every 5 s, and on
   visibilitychange (covers opening the app and ctrl+cmd+R = reload). */
(function () {
  "use strict";
  var nbDot = document.getElementById("nb-status");
  var ocDot = document.getElementById("oc-status");
  var verEl = document.getElementById("ver-status");
  if (!nbDot && !ocDot && !verEl) return;
  var INTERVAL = 5000;
  var base = window.API_URL || "/api";
  var timer = null;

  function setDot(el, ok, titleOk, titleBad) {
    if (!el) return;
    el.classList.toggle("online", ok === true);
    el.classList.toggle("offline", ok === false);
    el.title = ok ? titleOk : titleBad;
  }

  function setVersion(v) {
    if (!verEl) return;
    verEl.classList.remove("online", "offline", "amber");
    var latest = v && v.latest ? v.latest.version : null;
    if (!v || v.source !== "downloads" || !v.installed) {
      // Downloads page unreachable → gray, no guesswork.
      verEl.textContent = "v—";
      verEl.title = "Versión: sin datos de descargas";
      verEl.dataset.url = "";
      return;
    }
    if (v.is_latest === true) {
      verEl.textContent = "v" + v.installed + " ✓";
      verEl.classList.add("online");
      verEl.title = "Última versión instalada (" + v.installed + ")";
    } else if (v.is_latest === false && latest) {
      verEl.textContent = "v" + v.installed + " → " + latest;
      verEl.classList.add("amber");
      verEl.title = "Nueva versión disponible: " + latest;
    } else {
      verEl.textContent = "v" + v.installed;
      verEl.title = "Versión instalada: " + v.installed;
    }
    verEl.dataset.url = v.url || "";
  }

  function degrade() {
    setDot(nbDot, false, "", "API no disponible");
    setDot(ocDot, false, "", "API no disponible");
    if (verEl) {
      verEl.classList.remove("online", "amber");
      verEl.classList.add("offline");
      verEl.textContent = "v—";
      verEl.title = "API no disponible";
    }
  }

  function check() {
    var ctl = new AbortController();
    var t = setTimeout(function () { ctl.abort(); }, 4000);
    var headers = {};
    try {
      var tok = localStorage.getItem("auth_token");
      if (tok) headers.Authorization = "Bearer " + tok;
    } catch (e) { /* private mode etc. — server falls back to local user */ }
    fetch(base + "/status", { signal: ctl.signal, cache: "no-store", headers: headers })
      .then(function (r) { clearTimeout(t); if (!r.ok) throw new Error(String(r.status)); return r.json(); })
      .then(function (s) {
        var nb = (s && s.notebooklm) || {};
        setDot(nbDot, nb.connected === true,
          nb.connected ? "NotebookLM conectado" + (nb.profile ? " — " + nb.profile : "") : "",
          "NotebookLM desconectado" + (nb.profile ? " — " + nb.profile : ""));
        var oc = (s && s.opencode) || {};
        setDot(ocDot, oc.connected === true,
          oc.connected ? "OpenCode conectado" + (oc.model ? " — " + oc.model : "") : "",
          "OpenCode no disponible" + (oc.server_url ? " — " + oc.server_url : ""));
        setVersion(s && s.version);
      })
      .catch(function () { clearTimeout(t); degrade(); });
  }

  function start() { if (!timer) timer = setInterval(check, INTERVAL); }
  function stop() { clearInterval(timer); timer = null; }
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) stop(); else { check(); start(); }
  });
  window.addEventListener("online", check);
  window.addEventListener("offline", degrade);
  if (verEl) {
    verEl.style.cursor = "pointer";
    verEl.addEventListener("click", function () {
      var url = verEl.dataset.url;
      if (url) window.open(url, "_blank");
    });
  }
  check(); start();
})();
