// ─── Agenda module — weekly view, drag-drop, timer, sessions ───────
// Namespace: window.App.Agenda
// Dependencies: global STATE, API, window.App.UI.*, window.App.Habits.*

window.App = window.App || {};
window.App.Agenda = (function () {
  "use strict";

  // ─── Utilities from shared UI ─────────────────────────────────────
  const { getWeekId, getDaysOfWeek, todayStr, formatTimer, _setTimerFields, _getTimerSeconds, _updateTimerEffective, formatDateShort } = window.App.UI;
  const AgendaTimer = window.App.AgendaTimer;
  const AgendaQuickNote = window.App.AgendaQuickNote;
  const AgendaHabits = window.App.AgendaHabits;
  const AgendaDnd = window.App.AgendaDnd;
  const AgendaSession = window.App.AgendaSession;
  const AgendaCalendar = window.App.AgendaCalendar;
  const AgendaCore = window.App.AgendaCore;

  // ─── Format seconds as "Xh Ym" ──────────────────────────────────
  function formatEffective(seconds) {
    if (!seconds && seconds !== 0) return "—";
    const total = Math.floor(Math.max(0, seconds));
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    if (h > 0) return `${h}h ${m}m`;
    return `${m}m`;
  }

  // ─── loadAgendaWeek — navigate calendar to a specific date's week ─
  function loadAgendaWeek(targetDate) {
    const weekId = getWeekId(targetDate);
    STATE.currentWeek = weekId;
    STATE.currentDay = targetDate;
    STATE.selectedHabitDay = targetDate; // sync habit notes day
    renderAgenda();
  }

  // ─── _navigateToAgendaDay — dashboard → Agenda tab (readOnly) ────
  // Shared by both day-item and month-calendar clicks when the side
  // panel is in readOnly mode (e.g. mounted on the dashboard). Uses the
  // same tab-btn[data-tab="agenda"] mechanism as the nav cards.
  function _navigateToAgendaDay(dateStr) {
    STATE.currentWeek = getWeekId(dateStr);
    STATE.currentDay = dateStr;
    STATE.selectedHabitDay = dateStr;
    if (window.App?.Auth?.hideDashboard) window.App.Auth.hideDashboard();
    const tabBtn = document.querySelector('.tab-btn[data-tab="agenda"]');
    if (tabBtn) tabBtn.click();
  }

  // ==================================================================
  // SHARED SIDE-PANEL BUILDER (Agenda tab + Dashboard side panel)
  // ==================================================================
  // Builds the monthly calendar + week day list + week summary inside
  // `containerEl`. Used both by `renderAgenda()` (Agenda tab, full
  // editing) and by the Dashboard side panel (readOnly, compact).
  //
  // opts:
  //   readOnly    — when true, day clicks + month-day clicks just
  //                 navigate to the Agenda tab with that day selected.
  //                 DnD targets are NOT bound.
  //   compact     — applies compact CSS hooks (panel-side overrides
  //                 live in styles.css under .dashboard-agenda-panel).
  //   idPrefix    — string prefixed to the month-calendar container id
  //                 so two side panels can coexist in DOM without
  //                 duplicate #month-calendar elements.
  //
  // Returns the fetched weekData so the caller can re-use it (Agenda
  // tab needs it to render the centre column).
  async function buildAgendaSidePanel(containerEl, opts = {}) {
    const {
      readOnly = false,
      compact = false,
      idPrefix = "",
    } = opts;

    const weekId = STATE.currentWeek || getWeekId(todayStr());
    STATE.currentWeek = weekId;

    const days = getDaysOfWeek(weekId);
    const today = todayStr();
    const selectedDay = STATE.currentDay || today;

    // Load week data from API (no cache — both views are mounted at
    // different times, freshness beats micro-optimisation here).
    let weekData;
    try {
      weekData = await API.get(`/agenda/week/${weekId}`);
    } catch {
      weekData = { week_id: weekId, days: {} };
    }

    // ─── Build month-cal + week list + summary HTML ───────────────
    const monthCalId = `${idPrefix}month-calendar`;
    let html = `<div id="${monthCalId}"></div><div class="col-title" style="margin-top:8px;">📅 Semana</div>`;
    let totalSessions = 0, totalDone = 0, totalEffectiveSecs = 0;
    days.forEach((d) => {
      const daySessions = weekData.days?.[d.date]?.sessions || [];
      const isActive = !readOnly && d.date === selectedDay ? "active" : "";
      const isToday = d.date === today ? " (hoy)" : "";
      totalSessions += daySessions.length;
      totalDone += daySessions.filter((s) => s.state === "completed").length;

      // Effective hours per day
      const dayEffectiveSecs = daySessions.reduce((sum, s) => {
        const elapsed = s.timer_elapsed || 0;
        const paused = s.timer_paused_duration || 0;
        return sum + Math.max(0, elapsed - paused);
      }, 0);
      totalEffectiveSecs += dayEffectiveSecs;

      // Dots
      const cats = [...new Set(daySessions.map((s) => s.category))];
      const dots = cats
        .map((c) => {
          const cls = c.replace("_", "-");
          return `<span class="dot dot-${cls}"></span>`;
        })
        .join("");

      const dayCompleted = daySessions.filter((s) => s.state === "completed").length;
      const dayPending = daySessions.length - dayCompleted;
      let statsColor = "var(--text-muted)";
      if (daySessions.length > 0) {
        statsColor = dayPending === 0 ? "var(--green)" : "var(--amber)";
      }
      const statsText = daySessions.length > 0
        ? `<span class="d-stats" style="color:${statsColor}">${dayCompleted}/${daySessions.length}</span>`
        : "";
      const effText = dayEffectiveSecs > 0
        ? `<span class="d-eff">⏱ ${formatEffective(dayEffectiveSecs)}</span>`
        : "";

      html += `<div class="day-item ${isActive}${isToday ? ' today' : ''}" data-date="${d.date}">
        <span class="d-name">${d.name}</span>
        <span class="d-num">${d.num}</span>
        <div class="d-dots">${dots || '<span style="font-size:8px;color:var(--text-muted);">—</span>'}</div>
        ${statsText}
        ${effText}
      </div>`;
    });

    html += `<div class="week-summary">
      <div class="ws"><span>📚 Sesiones</span><strong>${totalSessions}</strong></div>
      <div class="ws"><span>✅ Completadas</span><strong style="color:var(--green)">${totalDone}</strong></div>
      <div class="ws"><span>⏳ Pendientes</span><strong style="color:var(--amber)">${totalSessions - totalDone}</strong></div>
      <div class="ws ws-eff"><span>⏱ Efectivas</span><strong>${formatEffective(totalEffectiveSecs)}</strong></div>
    </div>`;

    // Compact mode adds a hook class so CSS can shrink the panel.
    if (compact) containerEl.classList.add("agenda-side-panel--compact");
    else containerEl.classList.remove("agenda-side-panel--compact");

    containerEl.innerHTML = html;

    // ─── Render monthly calendar into the (possibly prefixed) id ──
    AgendaCalendar.renderMonthCalendar(selectedDay, {
      onNavigateWeek: readOnly ? _navigateToAgendaDay : loadAgendaWeek,
      containerId: monthCalId,
    });

    // ─── Day click handlers ───────────────────────────────────────
    containerEl.querySelectorAll(".day-item").forEach((item) => {
      item.addEventListener("click", () => {
        const date = item.dataset.date;
        if (readOnly) {
          // Dashboard mode: switch to Agenda tab with that day active.
          _navigateToAgendaDay(date);
          return;
        }
        containerEl.querySelectorAll(".day-item").forEach((x) => x.classList.remove("active"));
        item.classList.add("active");
        STATE.currentDay = date;
        STATE.selectedHabitDay = date;
        AgendaCore.renderAgendaCenter(date, weekData, { onRefresh: renderAgenda });
        AgendaHabits.renderNotes();
      });
    });

    // ─── Drag & drop — only in editable Agenda mode ──────────────
    if (!readOnly) {
      AgendaDnd.bindDayTargets(containerEl, {
        onDrop: async (sessionId, targetDate) => {
          if (targetDate === STATE.currentDay) return;
          try {
            await API.post(`/agenda/session/${sessionId}/move`, { target_date: targetDate });
            renderAgenda();
          } catch {
            renderAgenda();
          }
        },
      });
    }

    return weekData;
  }

  // ==================================================================
  // AGENDA
  // ==================================================================
  async function renderAgenda() {
    // Clean up any running timer intervals before re-render
    AgendaTimer.stopAll();

    const weekId = STATE.currentWeek || getWeekId(todayStr());
    STATE.currentWeek = weekId;
    const days = getDaysOfWeek(weekId);
    const today = todayStr();
    const selectedDay = STATE.currentDay || today;

    // Update header (this is a global element, so renderAgenda owns it,
    // not the shared panel builder).
    const d1 = days[0].date, d2 = days[6].date;
    document.getElementById("week-label").innerHTML =
      `<strong>Semana ${weekId.split("-W")[1]}</strong> · ${formatDateShort(d1)}–${formatDateShort(d2)}`;

    // ─── LEFT: Build the shared side panel (Agenda = editable) ──
    const leftEl = document.getElementById("agenda-left");
    const weekData = await buildAgendaSidePanel(leftEl, {
      readOnly: false,
      compact: false,
      idPrefix: "",
    });

    // ─── LEFT: Quick note (Agenda only — too tall for dashboard) ──
    const qnHtml = `<div class="panel" style="margin-top:12px;">
      <div class="panel-title">⚡ Nota rápida <span id="qn-status" class="note-status">●</span></div>
      <textarea id="quick-note" placeholder="Escribe algo rápido..." style="width:100%;min-height:360px;padding:8px 10px;border:1px solid var(--border);border-radius:var(--radius-xs);background:var(--bg);font-family:var(--font);font-size:12px;color:var(--text);resize:vertical;outline:none;"></textarea>
    </div>`;
    leftEl.insertAdjacentHTML("beforeend", qnHtml);
    AgendaQuickNote.load();

    // ─── CENTER: Sessions for selected day ────────────────────────
    AgendaCore.renderAgendaCenter(selectedDay, weekData, { onRefresh: renderAgenda });

    // ─── HABIT NOTES (center column, below habits table) ─────
    AgendaHabits.renderNotes();
  }



  // ─── Init session overlay (one-time event wiring) ────────────────
  AgendaSession.init();

  // ─── Init calendar import panel (Phase 71, #262) ──────────────────
  // Wired once at module load — the open/close API is then driven by the
  // "📅 Calendarios" button that AgendaCore renders in the centre column.
if (window.App.AgendaCalendars && typeof window.App.AgendaCalendars.init === "function") {
      window.App.AgendaCalendars.init();
    }

    // ─── Pending calendar-import notice (Phase 9) ────────────────────
    // Asked for on open so a session imported overnight is visible without
    // the user visiting the calendar panel. Failures stay silent — there is
    // nothing for the user to do about a missing notice.
    if (window.CalendarImportNotice && typeof window.CalendarImportNotice.refresh === "function") {
      window.CalendarImportNotice.refresh();
    }

  // ─── Public API ──────────────────────────────────────────────────
  return {
    renderAgenda,
    loadAgendaWeek,
    buildAgendaSidePanel,
  };
})();

// PORT NOTE (sub-phase B): expose the module under the v3 App.modules
// surface so app.js's `App.registerModule('Agenda', window.AgendaModule)`
// picks it up. v3 calls .render(), v2 exports .renderAgenda() — alias here.
window.AgendaModule = {
  render: window.App.Agenda.renderAgenda,
  loadAgendaWeek: window.App.Agenda.loadAgendaWeek,
  buildAgendaSidePanel: window.App.Agenda.buildAgendaSidePanel,
};