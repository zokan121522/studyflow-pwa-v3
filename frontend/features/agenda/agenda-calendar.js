// ─── Agenda Calendar — monthly calendar + move session modal ───────
// Namespace: window.App.AgendaCalendar
// Dependencies: API (global), STATE (global), window.App.Auth (authHeaders),
//                window.App.UI (escHtml)

window.App = window.App || {};
window.App.AgendaCalendar = (function () {
  "use strict";

  const { authHeaders } = window.App.Auth;
  const { escHtml } = window.App.UI;

  let _calMonthOffset = 0;    // month navigation offset for calendar widget
  let _moveCalMonthOffset = 0; // month navigation offset for move modal

  // ─── Monthly calendar widget (left panel) ────────────────────────
  // `callbacks.containerId` lets the dashboard side-panel reuse this
  // renderer with a different DOM id (default keeps Agenda behaviour).
  async function renderMonthCalendar(dateStr, callbacks = {}) {
    const { onNavigateWeek, containerId = "month-calendar" } = callbacks;
    const container = document.getElementById(containerId);
    if (!container) return;

    const base = new Date(dateStr + "T12:00:00");
    base.setMonth(base.getMonth() + _calMonthOffset);
    const year = base.getFullYear();
    const month = base.getMonth();
    const monthStr = `${year}-${String(month + 1).padStart(2, "0")}-01`;

    let monthData = { year, month: month + 1, days: {} };
    try {
      const r = await fetch(`/api/agenda/month?date=${monthStr}`, { headers: { ...authHeaders() } });
      monthData = await r.json();
    } catch { /* use empty data */ }

    const selDate = STATE.currentDay || dateStr;
    const selNum = selDate ? parseInt(selDate.split("-")[2], 10) : null;
    const todayNum = new Date().getDate();
    const isCurrentMonth =
      new Date().getFullYear() === year && new Date().getMonth() === month;

    const first = new Date(year, month, 1);
    const last = new Date(year, month + 1, 0);
    const totalDays = last.getDate();

    let startDow = first.getDay() - 1;
    if (startDow < 0) startDow = 6;

    const monthNames = [
      "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
      "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
    ];

    let html = `<div class="mc-head">
      <span class="mc-nav" data-offset="-1">◀</span>
      <span class="mc-title">${monthNames[month]} ${year}</span>
      <span class="mc-nav" data-offset="1">▶</span>
    </div>
    <div class="mc-grid">
      <span class="mc-dow">L</span><span class="mc-dow">M</span><span class="mc-dow">X</span>
      <span class="mc-dow">J</span><span class="mc-dow">V</span><span class="mc-dow">S</span>
      <span class="mc-dow">D</span>`;

    for (let i = 0; i < startDow; i++) {
      html += '<span class="mc-day mc-empty"></span>';
    }

    for (let d = 1; d <= totalDays; d++) {
      const dayKey = String(d);
      const dayInfo = monthData.days?.[dayKey];
      const hasSessions = dayInfo && dayInfo.total > 0;
      const allCompleted = hasSessions && dayInfo.completed === dayInfo.total;
      const isSelected = d === selNum && isCurrentMonth;
      const isToday = d === todayNum && isCurrentMonth;

      let circleHtml = "";
      let dayCls = "mc-day";
      if (hasSessions) {
        const circleCls = allCompleted ? "mc-circle mc-circle-done" : "mc-circle mc-circle-pending";
        circleHtml = `<span class="${circleCls}"></span>`;
        dayCls += allCompleted ? " mc-day-done" : " mc-day-pending";
      }
      if (isSelected) dayCls += " mc-selected";
      if (isToday) dayCls += " mc-today";

      const dateVal = `${year}-${String(month + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
      html += `<span class="${dayCls}" data-date="${dateVal}"><span class="mc-day-num">${d}</span>${circleHtml}</span>`;
    }

    html += "</div>";
    container.innerHTML = html;

    // Navigation click handlers
    container.querySelectorAll(".mc-nav").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        _calMonthOffset += parseInt(btn.dataset.offset, 10);
        renderMonthCalendar(dateStr, callbacks);
      });
    });

    // Day click → navigate to that week
    container.querySelectorAll(".mc-day:not(.mc-empty)").forEach((cell) => {
      cell.addEventListener("click", () => {
        const targetDate = cell.dataset.date;
        if (onNavigateWeek) onNavigateWeek(targetDate);
      });
    });
  }

  // ─── Open move session modal ─────────────────────────────────────
  async function openMoveModal(sessionId, callbacks = {}) {
    const { onMoved, weekData, dateStr } = callbacks;

    // Determine the origin day sessions (the ones to offer for batch move).
    const origin = await _loadOriginSessions(sessionId, weekData, dateStr);
    const originDate = origin.date || dateStr;

    let overlay = document.getElementById("move-session-overlay");
    if (!overlay) {
      overlay = document.createElement("div");
      overlay.className = "overlay";
      overlay.id = "move-session-overlay";
      overlay.innerHTML = `
        <div class="omodal" style="max-width:420px;">
          <div class="omodal-head">
            <h3>📅 Mover sesiones</h3>
            <button id="ms-close-btn">✕</button>
          </div>
          <div class="omodal-body" style="padding:16px;">
            <div style="font-size:12px;color:var(--text-secondary);margin-bottom:6px;">
              Marca las sesiones a mover (la pulsada ya viene marcada)
            </div>
            <div id="ms-session-list"></div>
            <div style="text-align:center;font-size:12px;color:var(--text-secondary);margin:12px 0 8px;">
              📆 Selecciona el día destino
            </div>
            <div id="ms-calendar"></div>
          </div>
        </div>`;
      document.body.appendChild(overlay);

      overlay.querySelector("#ms-close-btn").addEventListener("click", () => {
        overlay.classList.remove("open");
      });

      overlay.addEventListener("click", (e) => {
        if (e.target === overlay) overlay.classList.remove("open");
      });
    }

    _moveCalMonthOffset = 0;
    overlay.dataset.sessionId = sessionId;
    overlay._originDate = originDate;
    overlay._onMoved = onMoved;

    // Render the list of origin-day sessions with checkboxes.
    _renderOriginSessionList(overlay, origin.sessions, sessionId);

    overlay.classList.add("open");
    _renderMoveCalendar(overlay, sessionId, onMoved);
  }

  // ─── Load the sessions of the origin day, marking the tapped one ──
  async function _loadOriginSessions(sessionId, weekData, dateStr) {
    // Prefer in-memory week data if provided (fast path).
    if (weekData && dateStr && weekData.days?.[dateStr]) {
      return {
        date: dateStr,
        sessions: weekData.days[dateStr].sessions || [],
      };
    }

    // Fallback: fetch the tapped session to learn its day, then the week.
    try {
      const session = await API.get(`/agenda/session/${sessionId}`);
      const day = session.day_date;
      const weekId = STATE.currentWeek;
      const fresh = await API.get(`/agenda/week/${weekId}`);
      return {
        date: day,
        sessions: (fresh.days?.[day]?.sessions) || [],
      };
    } catch {
      return { date: dateStr, sessions: [] };
    }
  }

  // ─── Render origin-day sessions as checkbox rows ─────────────────
  function _renderOriginSessionList(overlay, sessions, focusedId) {
    const listEl = overlay.querySelector("#ms-session-list");
    if (!listEl) return;

    if (!sessions || !sessions.length) {
      listEl.innerHTML = '<div style="font-size:12px;color:var(--text-muted);">No hay sesiones en el día de origen.</div>';
      return;
    }

    // The tapped session is pre-selected; the rest start unchecked.
    const html = sessions
      .map((s) => {
        const checked = s.id === focusedId ? "checked" : "";
        // Sessions with a running/paused timer cannot be moved.
        const locked = s.timer_state === "running" || s.timer_state === "paused";
        const lockedAttr = locked ? "disabled" : "";
        const lockLabel = locked
          ? '<span style="color:var(--text-muted);font-size:10px;">⏱ en curso, no movible</span>'
          : "";
        const icon = { formal_study: "🎓", self_study: "📚", work: "💼", language: "🌐", health: "🏋️", mind: "🧠", project: "⚡" }[s.category] || "";
        const time = s.start_time && s.end_time ? `${s.start_time}–${s.end_time}` : "";
        return `<label class="ms-row" style="display:flex;align-items:center;gap:8px;padding:5px 8px;border:1px solid var(--border);border-radius:8px;background:var(--surface);margin-bottom:4px;cursor:${locked ? "not-allowed" : "pointer"};opacity:${locked ? 0.55 : 1};">
          <input type="checkbox" class="ms-check" data-sid="${s.id}" ${checked} ${lockedAttr} style="cursor:pointer;">
          <span style="flex:1;font-size:12px;">${icon} ${escHtml(s.title) || "Sesión"} <span style="color:var(--text-muted);font-size:10px;">${escHtml(time)}</span></span>
          ${lockLabel}
        </label>`;
      })
      .join("");

    listEl.innerHTML = html;
  }

  async function _renderMoveCalendar(container, sessionId, onMoved) {
    const calEl = container.querySelector("#ms-calendar");
    if (!calEl) return;

    const originDate = container._originDate;
    const today = new Date();
    today.setMonth(today.getMonth() + _moveCalMonthOffset);
    const year = today.getFullYear();
    const month = today.getMonth();
    const monthStr = `${year}-${String(month + 1).padStart(2, "0")}-01`;

    let monthData = { year, month: month + 1, days: {} };
    try {
      const r = await fetch(`/api/agenda/month?date=${monthStr}`, { headers: { ...authHeaders() } });
      monthData = await r.json();
    } catch { /* use empty */ }

    const todayNum = new Date().getDate();
    const isCurrentMonth =
      new Date().getFullYear() === year && new Date().getMonth() === month;

    const first = new Date(year, month, 1);
    const last = new Date(year, month + 1, 0);
    const totalDays = last.getDate();

    let startDow = first.getDay() - 1;
    if (startDow < 0) startDow = 6;

    const monthNames = [
      "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
      "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
    ];

    let html = `<div class="mc-head">
      <span class="mc-nav" data-offset="-1">◀</span>
      <span class="mc-title">${monthNames[month]} ${year}</span>
      <span class="mc-nav" data-offset="1">▶</span>
    </div>
    <div class="mc-grid" style="margin-top:4px;">
      <span class="mc-dow">L</span><span class="mc-dow">M</span><span class="mc-dow">X</span>
      <span class="mc-dow">J</span><span class="mc-dow">V</span><span class="mc-dow">S</span>
      <span class="mc-dow">D</span>`;

    for (let i = 0; i < startDow; i++) {
      html += '<span class="mc-day mc-empty"></span>';
    }

    for (let d = 1; d <= totalDays; d++) {
      const dayKey = String(d);
      const dayInfo = monthData.days?.[dayKey];
      const hasSessions = dayInfo && dayInfo.total > 0;
      const allCompleted = hasSessions && dayInfo.completed === dayInfo.total;

      let circleHtml = "";
      let dayCls = "mc-day ms-clickable";
      if (hasSessions) {
        const circleCls = allCompleted ? "mc-circle mc-circle-done" : "mc-circle mc-circle-pending";
        circleHtml = `<span class="${circleCls}"></span>`;
        dayCls += allCompleted ? " mc-day-done" : " mc-day-pending";
      }
      if (d === todayNum && isCurrentMonth) dayCls += " mc-today";
      if (originDate === `${year}-${String(month + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`) {
        dayCls += " ms-origin";
      }

      const dateVal = `${year}-${String(month + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
      html += `<span class="${dayCls}" data-date="${dateVal}">
        <span class="mc-day-num">${d}</span>${circleHtml}
      </span>`;
    }

    html += "</div>";
    calEl.innerHTML = html;

    // Navigation
    calEl.querySelectorAll(".mc-nav").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        _moveCalMonthOffset += parseInt(btn.dataset.offset, 10);
        _renderMoveCalendar(container, sessionId, onMoved);
      });
    });

    // Day click → move all checked sessions
    calEl.querySelectorAll(".ms-clickable").forEach((cell) => {
      cell.addEventListener("click", async () => {
        const targetDate = cell.dataset.date;
        if (!targetDate) return;

        // Collect the ids of the checked sessions in this modal.
        const ids = Array.from(
          container.querySelectorAll(".ms-check:checked")
        ).map((cb) => cb.dataset.sid);

        if (!ids.length) {
          alert("No hay sesiones marcadas para mover");
          return;
        }

        const count = ids.length;
        const label = count === 1 ? "esta sesión" : `estas ${count} sesiones`;
        if (!confirm(`📅 ¿Mover ${label} al ${targetDate}?`)) return;

        try {
          await API.post("/agenda/sessions/bulk-move", {
            ids: ids,
            target_date: targetDate,
          });
          container.classList.remove("open");
          if (container._onMoved) container._onMoved();
        } catch (err) {
          alert("Error al mover las sesiones");
          container.classList.remove("open");
        }
      });
    });
  }

  // ─── Public API ──────────────────────────────────────────────────
  return {
    renderMonthCalendar,
    openMoveModal,
  };
})();
