/* ============================== SERVER STATUS (poll /api/health) ============================== */
(function () {
  "use strict";
  var dot = document.getElementById("server-status");
  if (!dot) return;
  var INTERVAL = 5000;
  var base = window.API_URL || "/api";
  var timer = null;
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
  function start() { if (!timer) timer = setInterval(check, INTERVAL); }
  function stop() { clearInterval(timer); timer = null; }
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) stop(); else { check(); start(); }
  });
  window.addEventListener("online", check);
  window.addEventListener("offline", function () { set(false); });
  check(); start();
})();
