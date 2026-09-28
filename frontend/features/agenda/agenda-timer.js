// ─── Agenda Timer module — timer lifecycle, patch session state ────
// Namespace: window.App.AgendaTimer
// Dependencies: API (global), window.App.UI.formatTimer

window.App = window.App || {};
window.App.AgendaTimer = (function () {
  "use strict";

  const { formatTimer } = window.App.UI;
  let _timerIntervals = {};

  // ─── Start timer tick for a session ─────────────────────────────
  function startTick(sessionId) {
    if (_timerIntervals[sessionId]) return;
    _timerIntervals[sessionId] = setInterval(() => {
      const el = document.querySelector(
        `[data-session-id="${sessionId}"] .sc-timer .te`
      );
      const pausedEl = document.querySelector(
        `[data-session-id="${sessionId}"] .sc-timer .tp`
      );
      const now = Date.now();
      const started = el?.dataset?.started
        ? new Date(el.dataset.started + "Z").getTime()
        : null;
      const pausedAt = el?.dataset?.pausedAt
        ? new Date(el.dataset.pausedAt + "Z").getTime()
        : null;
      const pausedDur = parseInt(el?.dataset?.pausedDur || "0", 10);
      if (started && !pausedAt) {
        const elapsed = Math.floor((now - started) / 1000) - pausedDur;
        if (el) el.textContent = formatTimer(Math.max(0, elapsed));
        const effEl = document.querySelector(
          `[data-session-id="${sessionId}"] .sc-timer .tef`
        );
        if (effEl)
          effEl.textContent = `✓ ${formatTimer(Math.max(0, elapsed))}`;
      }
      if (pausedEl && pausedAt) {
        const totalPaused = pausedDur + Math.floor((now - pausedAt) / 1000);
        pausedEl.textContent = `⏸ ${formatTimer(totalPaused)}`;
      }
    }, 1000);
  }

  // ─── Stop timer tick for a session ──────────────────────────────
  function stopTick(sessionId) {
    if (_timerIntervals[sessionId]) {
      clearInterval(_timerIntervals[sessionId]);
      delete _timerIntervals[sessionId];
    }
  }

  // ─── Stop all timer ticks ───────────────────────────────────────
  function stopAll() {
    Object.keys(_timerIntervals).forEach((sid) => stopTick(sid));
  }

  // ─── Patch session state and timer state ─────────────────────────
  async function patchSession(sessionId, state, timerState) {
    try {
      await API.post(`/agenda/session/state`, {
        session_id: sessionId,
        state,
        timer_state: timerState,
      });
    } catch {
      // silent
    }
  }

  // ─── Public API ──────────────────────────────────────────────────
  return {
    startTick,
    stopTick,
    stopAll,
    patchSession,
  };
})();
