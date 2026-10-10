/* ============================== SERVER STATUS (poll /api/health) ==============================
   V3: the "API" pill is a real button. Green = server up, red = server down.
   Click while green → confirm ("Servidor ya encendido"). Click while red → ask
   the local launcher helper (127.0.0.1:8478) to start the server, then poll
   /api/health until healthy (or 45 s). The helper is optional: if it is not
   running, the button honestly tells the user to open StudyFlow themselves. */
(function () {
  "use strict";
  var dot = document.getElementById("server-status");
  var wrap = document.getElementById("server-status-wrap");
  if (!dot) return;
  var INTERVAL = 5000;
  var base = window.API_URL || "/api";
  var HELPER = "http://127.0.0.1:8478";
  var START_TIMEOUT = 2000;
  var HEALTH_TIMEOUT = 45000;
  var HEALTH_POLL = 1000;
  var timer = null;
  var busy = false;

  function set(ok) {
    dot.classList.toggle("online", ok === true);
    dot.classList.toggle("offline", ok === false);
    dot.title = ok ? "Servidor conectado" : (ok === false ? "Servidor no disponible" : "Comprobando servidor…");
  }

  function check() {
    var ctl = new AbortController();
    var t = setTimeout(function () { ctl.abort(); }, 4000);
    fetch(base + "/health", { signal: ctl.signal, cache: "no-store" })
      .then(function (r) { clearTimeout(t); set(r.ok); })
      .catch(function () { clearTimeout(t); set(false); });
  }

  /* ---- minimal, dismissible toast (bottom-right, role=status) --------------- */
  function toast(msg, ms) {
    var host = document.getElementById("sf-server-toast");
    if (!host) {
      host = document.createElement("div");
      host.id = "sf-server-toast";
      host.setAttribute("role", "status");
      host.setAttribute("aria-live", "polite");
      host.title = "Clic para cerrar";
      host.style.cssText =
        "position:fixed;right:16px;bottom:16px;z-index:100001;"
        + "max-width:min(92vw,380px);padding:10px 14px;border-radius:10px;"
        + "background:#16213e;color:#fff;font-size:13px;line-height:1.4;"
        + "box-shadow:0 8px 28px rgba(0,0,0,.45);border:1px solid #3a4a6e;"
        + "white-space:pre-wrap;word-break:break-word;cursor:pointer;";
      host.addEventListener("click", function () {
        clearTimeout(host._t);
        host.style.display = "none";
      });
      document.body.appendChild(host);
    }
    host.textContent = msg;
    host.style.display = "block";
    clearTimeout(host._t);
    if (ms !== 0) host._t = setTimeout(function () { host.style.display = "none"; }, ms || 4000);
  }

  /* ---- helpers ------------------------------------------------------------- */
  function fetchWithTimeout(url, opts, ms) {
    var ctl = new AbortController();
    var t = setTimeout(function () { ctl.abort(); }, ms);
    opts = opts || {};
    opts.signal = ctl.signal;
    return fetch(url, opts).then(
      function (r) { clearTimeout(t); return r; },
      function (e) { clearTimeout(t); throw e; }
    );
  }

  function authHeaders() {
    var h = {};
    try {
      var tok = localStorage.getItem("auth_token");
      if (tok) h.Authorization = "Bearer " + tok;
    } catch (e) { /* private mode — server falls back to the local user */ }
    return h;
  }

  function withVersion(msg, v) {
    if (!v) return msg;
    var s = String(v).trim();
    if (!s) return msg;
    if (s.charAt(0) !== "v") s = "v" + s;
    return msg + " · " + s;
  }

  /* Version: prefer the app's own /api/status (same source as the pills), fall
     back to the launcher helper GET /status. Never rejects. */
  function getVersion() {
    return fetchWithTimeout(base + "/status", { cache: "no-store", headers: authHeaders() }, 3000)
      .then(function (r) { if (!r.ok) throw new Error(String(r.status)); return r.json(); })
      .then(function (s) { return (s && s.version && s.version.installed) || null; })
      .catch(function () {
        return fetchWithTimeout(HELPER + "/status", { cache: "no-store" }, 2000)
          .then(function (r) { if (!r.ok) throw new Error(String(r.status)); return r.json(); })
          .then(function (s) { return (s && s.version) || null; })
          .catch(function () { return null; });
      });
  }

  function helperStatus() {
    return fetchWithTimeout(HELPER + "/status", { cache: "no-store" }, 2000)
      .then(function (r) { if (!r.ok) throw new Error(String(r.status)); return r.json(); })
      .catch(function () { return null; });
  }

  function setBusy(on) {
    busy = on;
    if (!wrap) return;
    if (on) wrap.setAttribute("aria-busy", "true");
    else wrap.removeAttribute("aria-busy");
  }

  /* ---- click behaviour ----------------------------------------------------- */
  function onClickGreen() {
    getVersion().then(function (v) { toast(withVersion("Servidor ya encendido", v)); });
  }

  /* Poll /api/health every ~1 s until healthy or HEALTH_TIMEOUT. Never rejects:
     on timeout shows the launcher log path (helper GET /status → log). */
  function pollUntilHealthy() {
    var started = Date.now();
    toast("Arrancando StudyFlow… 0s", 0);
    var tick = setInterval(function () {
      var secs = Math.round((Date.now() - started) / 1000);
      toast("Arrancando StudyFlow… " + secs + "s", 0);
    }, 1000);
    return new Promise(function (resolve) {
      function attempt() {
        if (Date.now() - started >= HEALTH_TIMEOUT) {
          clearInterval(tick);
          set(false);
          helperStatus().then(function (s) {
            var log = s && s.log ? s.log : null;
            toast("No arrancó. Revisa el log: " + (log || "el log del launcher"));
            resolve();
          });
          return;
        }
        fetchWithTimeout(base + "/health", { cache: "no-store" }, 3000)
          .then(function (r) {
            if (!r.ok) { setTimeout(attempt, HEALTH_POLL); return; }
            clearInterval(tick);
            set(true);
            getVersion().then(function (v) {
              toast(withVersion("Servidor listo", v));
              resolve();
            });
          })
          .catch(function () { setTimeout(attempt, HEALTH_POLL); });
      }
      attempt();
    });
  }

  function startServer() {
    return fetchWithTimeout(HELPER + "/start", { method: "POST", cache: "no-store" }, START_TIMEOUT)
      .then(function (r) {
        if (r.status === 200) {
          set(true);
          return getVersion().then(function (v) {
            toast(withVersion("Servidor ya encendido", v));
          });
        }
        if (r.status === 202) return pollUntilHealthy();
        if (r.status === 409) {
          toast("Ya se está arrancando…");
          return pollUntilHealthy();
        }
        throw new Error("unexpected " + r.status);
      })
      .catch(function () {
        // Helper not running / timeout / unexpected → honest fallback.
        set(false);
        toast("StudyFlow está apagado. Ábrelo con su icono para reactivarlo.");
      });
  }

  function onClickRed() {
    if (busy) return;
    setBusy(true);
    toast("Buscando servidor local…", 0);
    startServer().then(function () { setBusy(false); }, function () { setBusy(false); });
  }

  if (wrap) {
    wrap.addEventListener("click", function () {
      if (busy) return;
      if (dot.classList.contains("online")) onClickGreen();
      else onClickRed();
    });
  }

  function start() { if (!timer) timer = setInterval(check, INTERVAL); }
  function stop() { clearInterval(timer); timer = null; }
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) stop(); else { check(); start(); }
  });
  window.addEventListener("online", check);
  window.addEventListener("offline", function () { set(false); });
  check(); start();
})();
