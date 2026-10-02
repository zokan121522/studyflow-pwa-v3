// ─── Header session player — a "now playing" pill in the header ──────
// Namespace: window.App.SessionMiniPlayer
//
// Why it exists: the session timer only ever lived inside the session card.
// Press play, switch to another tab, and the one thing you most need to
// remember — that something is still running — is off-screen. That is how a
// session gets left running for an hour.
//
// Lives in the header because the header is the one strip that is on screen in
// every view. Takes over .header-week while a session is live and hands the
// week label back on stop.
//
// Timing is NOT owned here. The authoritative state lives on the server and on
// the card's .te dataset (data-started / data-paused-at / data-paused-dur),
// which is refreshed after every patch. We re-read that dataset on each sync
// and apply the same formula as agenda-timer.js, so the header and the card
// cannot drift apart.
window.App = window.App || {};
window.App.SessionMiniPlayer = (function () {
  "use strict";

  let _state = null; // { id, title, dateStr, weekId, onRefresh, playing }
  let _interval = null;
  let _els = {};

  function _q(id) {
    return document.getElementById(id);
  }

  function _cacheEls() {
    _els.root = _q("header-player");
    _els.title = _q("hp-title");
    _els.time = _q("hp-time");
    _els.play = _q("hp-play");
    _els.stop = _q("hp-stop");
    _els.week = document.querySelector(".header-week");
  }

  function _fmt(sec) {
    const { formatTimer } = window.App.UI || {};
    if (typeof formatTimer === "function") return formatTimer(Math.max(0, sec));
    const s = Math.max(0, Math.floor(sec));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const ss = String(s % 60).padStart(2, "0");
    return h > 0 ? h + ":" + String(m).padStart(2, "0") + ":" + ss : m + ":" + ss;
  }

  // Same arithmetic as agenda-timer.js startTick. Reading the card (not our own
  // bookkeeping) is the point: one source of truth.
  function _elapsedNow() {
    if (!_state) return 0;
    const te = document.querySelector(
      '[data-session-id="' + _state.id + '"] .sc-timer .te'
    );
    if (!te) return 0;
    const started = te.dataset.started
      ? new Date(te.dataset.started + "Z").getTime()
      : null;
    const pausedAt = te.dataset.pausedAt
      ? new Date(te.dataset.pausedAt + "Z").getTime()
      : null;
    const pausedDur = parseInt(te.dataset.pausedDur || "0", 10);
    if (!started) return 0;
    // Paused: what keeps growing is the paused time, so show that — frozen
    // elapsed told you nothing while the session sat idle. Same arithmetic as
    // the card's .tp (pausedDur + time since pause started).
    if (pausedAt) {
      return Math.max(0, pausedDur + Math.floor((Date.now() - pausedAt) / 1000));
    }
    return Math.max(0, Math.floor((Date.now() - started) / 1000) - pausedDur);
  }

  function _renderTime() {
    if (!_els.time || !_state) return;
    const mark = _state.playing ? "⏱" : "⏸";
    _els.time.textContent = mark + " " + _fmt(_elapsedNow());
  }

  function _paint() {
    if (!_state || !_els.root) return;
    _els.root.hidden = false;
    if (_els.week) _els.week.classList.add("is-playing");
    _els.root.classList.toggle("paused", !_state.playing);
    _els.title.textContent = _state.title;
    _els.title.title = "Abrir «" + _state.title + "»";
    _els.play.textContent = _state.playing ? "⏸" : "▶";
    _els.play.title = _state.playing ? "Pausar" : "Reanudar";
    _els.play.setAttribute("aria-label", _state.playing ? "Pausar" : "Reanudar");
    _renderTime();
  }

  function _clearInterval() {
    if (_interval) {
      clearInterval(_interval);
      _interval = null;
    }
  }

  function _startTicking() {
    _clearInterval();
    _interval = setInterval(_renderTime, 1000);
  }

  // ── Public API ──────────────────────────────────────────────────

  // Called after the play/pause/stop patch AND after the list has re-rendered,
  // so the .te dataset is already up to date.
  function sync(sessionId, timerState) {
    if (timerState === "stopped") {
      hide();
      return;
    }
    const card = document.querySelector('[data-session-id="' + sessionId + '"]');
    if (!card) return;
    const titleEl = card.querySelector(".sc-title");
    _cacheEls();
    _state = _state || {};
    _state.id = sessionId;
    _state.playing = timerState === "running";
    if (titleEl) {
      // Drop the trailing category tag: we want the session's own title.
      const tag = titleEl.querySelector(".sc-tag");
      if (tag) tag.remove();
      _state.title = (titleEl.textContent || "").trim();
    }
    _paint();
    // Tick while paused too: the counter now shows the paused total, which is
    // still moving. Only stop() / hide() tears the interval down.
    _startTicking();
  }

  // agenda-core calls this when it wires the cards, so play/stop have the
  // routing info the title link needs without the module reaching into agenda.
  function setContext(ctx) {
    _cacheEls();
    _state = Object.assign(_state || {}, ctx);
  }

  function hide() {
    _clearInterval();
    _state = null;
    _cacheEls();
    if (_els.root) _els.root.hidden = true;
    if (_els.week) _els.week.classList.remove("is-playing");
  }

  function _openSession() {
    if (!_state || !window.App.AgendaSession) return;
    API.get("/agenda/session/" + _state.id)
      .then(function (session) {
        window.App.AgendaSession.openSessionOverlay(
          (session && session.day_date) || _state.dateStr,
          _state.weekId,
          session,
          _state.onRefresh || null
        );
      })
      .catch(function () {
        /* silent — same as the card click path */
      });
  }

  function _init() {
    _cacheEls();
    if (!_els.root) return;
    _els.title.addEventListener("click", _openSession);
    _els.play.addEventListener("click", function () {
      if (!_state) return;
      const timer = _state.playing ? "paused" : "running";
      const state = "in_progress";
      window.App.AgendaTimer.patchSession(_state.id, state, timer).then(function () {
        if (_state.onRefresh) _state.onRefresh();
        // Re-read the card after the refresh repainted it.
        window.setTimeout(function () {
          sync(_state.id, timer);
        }, 60);
      });
    });
    _els.stop.addEventListener("click", function () {
      if (!_state) return;
      const id = _state.id;
      window.App.AgendaTimer.patchSession(id, "completed", "stopped").then(
        function () {
          if (_state && _state.onRefresh) _state.onRefresh();
          hide();
        }
      );
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", _init);
  } else {
    _init();
  }

  return { sync, setContext, hide };
})();