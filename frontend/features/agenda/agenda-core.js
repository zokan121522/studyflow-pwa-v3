// ─── Agenda Core — center column session cards + event wiring ─────
// Namespace: window.App.AgendaCore
// Dependencies: STATE, API (global), window.App.UI, window.App.* modules
// PORT NOTE (sub-phase B): extracted from the 594-line v2 agenda-core.js so
// the day-list render path stays in this file and the month-view path lives
// in agenda-month-view.js. Each function kept under 30 lines.

window.App = window.App || {};
window.App.AgendaCore = (function () {
  "use strict";

  const { formatTimer } = window.App.UI;
  const AgendaTimer = window.App.AgendaTimer;
  const AgendaHabits = window.App.AgendaHabits;
  const AgendaSession = window.App.AgendaSession;
  const AgendaDnd = window.App.AgendaDnd;
  const AgendaCalendar = window.App.AgendaCalendar;

  // ── MEJORA 5 — strip the legacy "🧠 Ritual Matinal — <fecha>" H1 that
  // pre-prompt-fix rituals used to embed before the H2 sc-title that
  // already says "🧠 Ritual Matinal". Localized here so other consumers
  // (study plans, course notes) keep their H1s intact.
  function _stripRitualH1(md, category) {
    if (!md || category !== "mind") return md;
    var head = md.split("\n").find(function (l) { return l.trim().length > 0; });
    if (!head) return md;
    var m = head.match(/^#\s+(.*)$/);
    if (!m) return md;
    if (m[1].trim().indexOf("🧠 Ritual Matinal") !== 0) return md;
    var lines = md.split("\n");
    var idx = lines.findIndex(function (l) { return l.trim().length > 0; });
    lines.splice(idx, 1);
    while (idx < lines.length && lines[idx].trim() === "") lines.splice(idx, 1);
    return lines.join("\n");
  }

  // ── Tiny HTML attribute escaper (used by data-* attrs that roundtrip
  // back to JS via dataset).
  function _escAttr(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  // ── View mode state: list | timeline | week | month ────────────
  var _viewMode = "list";
  var _monthOffset = 0;

  function setViewMode(mode) {
    if (_viewMode === "month" && mode !== "month") _monthOffset = 0;
    _viewMode = mode;
  }
function getViewMode() { return _viewMode; }

    // ── Single click -> open session overlay (registered once) ─────
    var _curDateStr = "";
    var _curWeekId = "";
    var _curRefresh = null;
    var _clickOpenRegistered = false;
    var _downXY = null;

    function _openSessionOnClick(e) {
      if (e.target.closest("input, textarea, select, button, a, [data-action], .vt-btn, .sc-sub")) return;
      var block = e.target.closest(".tl-block, .tlw-block, .tlm-session, .s-card");
      if (!block) return;
      var sid = block.dataset.sid || block.dataset.sessionId;
      if (!sid) return;
      if (_downXY && (Math.abs(e.clientX - _downXY[0]) > 5 || Math.abs(e.clientY - _downXY[1]) > 5)) return;
      e.stopPropagation();
      API.get("/agenda/session/" + sid)
        .then(function (session) {
          AgendaSession.openSessionOverlay(
            session.day_date || block.dataset.date || _curDateStr,
            _curWeekId, session, _curRefresh
          );
        })
        .catch(function () { /* silent */ });
    }

    function _registerClickOpen(el) {
      if (_clickOpenRegistered) return;
      el.addEventListener("pointerdown", function (e) { _downXY = [e.clientX, e.clientY]; }, true);
      el.addEventListener("click", _openSessionOnClick);
      _clickOpenRegistered = true;
    }

    // ── Category lookup maps (extracted to keep render functions short) ──
  var CAT_LABELS = {
    formal_study: { label: "Formal", cls: "formal" },
    self_study:   { label: "Self",   cls: "self" },
    work:         { label: "Work",   cls: "work" },
    language:     { label: "Lang",   cls: "lang" },
    health:       { label: "Health", cls: "health" },
    mind:         { label: "Mind",   cls: "mind" },
    project:      { label: "Project",cls: "project" },
  };
  var CAT_COLORS = {
    formal_study: "var(--cat-formal)", self_study: "var(--cat-self)", work: "var(--cat-work)",
    language: "var(--cat-lang)", health: "var(--cat-health)", mind: "var(--cat-mind)", project: "var(--cat-project)",
  };
  var CAT_ICONS = { formal_study: "🎓", self_study: "📚", work: "💼", language: "🌐", health: "🏋️", mind: "🧠", project: "⚡" };

  // ── Build the action buttons HTML for a single session card ─────
  // Returns inline spans/buttons for play/pause/stop/del/move/copy.
  // Splitting this out keeps _renderSessionCard readable.
  function _buildActionsHtml(s) {
    var ts = s.timer_state;
    // Stopping the timer sets state to "completed" (see .btn-stop), which used
    // to collapse the entire row into the "Hecho" label. Timer controls are
    // meaningless once settled, but delete / move / copy still are: a finished
    // session is the one you most often want to tidy up. So the status tag
    // becomes a prefix and only the timer buttons are dropped.
    var settled = s.state === "completed" || s.state === "cancelled";
    var statusTag = "";
    if (s.state === "completed") {
      statusTag = '<span style="font-size:11px;color:var(--green);font-weight:600;margin-right:4px;">✅ Hecho</span>';
    } else if (s.state === "cancelled") {
      statusTag = '<span style="font-size:11px;color:var(--text-muted);font-weight:600;margin-right:4px;">⏹ Cancelado</span>';
    }
    var playDisabled  = (settled || ts === "running") ? "disabled" : "";
    var pauseDisabled = (settled || ts !== "running") ? "disabled" : "";
    var stopDisabled  = (settled || (ts !== "running" && ts !== "paused")) ? "disabled" : "";
    var moveDelDisabled = (ts === "running" || ts === "paused") ? "disabled" : "";
    return statusTag +
      '<button class="btn-play" data-sid="' + s.id + '" title="Iniciar" draggable="false" ' + playDisabled + '>▶️</button>' +
      '<button class="btn-pause" data-sid="' + s.id + '" title="Pausar" draggable="false" ' + pauseDisabled + '>⏸</button>' +
      '<button class="btn-stop" data-sid="' + s.id + '" title="Detener" draggable="false" ' + stopDisabled + '>⏹</button>' +
      '<button class="btn-del" data-sid="' + s.id + '" data-scat="' + (s.category || "") +
      '" data-stitle="' + _escAttr(s.title) + '" title="Borrar sesión" draggable="false" ' + moveDelDisabled + '>🗑️</button>' +
      '<button class="btn-move" data-sid="' + s.id + '" title="Mover a otro día" draggable="false" ' + moveDelDisabled + '>📅</button>' +
      '<button class="btn-copy" data-sid="' + s.id + '" title="Duplicar sesión" draggable="false">📋</button>';
  }

  // ── Build the inner card body (.sc-body) — title + note + timer row ─
  function _buildCardBodyHtml(s, info, shownTitle, notes, actsHtml, effective) {
    var tElapsed = s.timer_elapsed || 0;
    var tPaused = s.timer_paused_duration || 0;
    return '' +
      '<div class="sc-body">' +
        '<div class="sc-title">' + shownTitle + ' <span class="sc-tag ' + info.cls + '">' + info.label + '</span></div>' +
        '<div class="sc-sub collapsed">' + notes + '</div>' +
        '<div class="sc-timer">' +
          '<button class="btn-expand" data-sid="' + s.id + '" title="Expandir nota" draggable="false">▶</button>' +
          '<span class="te" data-started="' + (s.timer_started_at || '') + '" data-paused-at="' + (s.timer_paused_at || '') + '" data-paused-dur="' + (s.timer_paused_duration || 0) + '">⏱ ' + formatTimer(tElapsed) + '</span>' +
          '<span class="tp">⏸ ' + formatTimer(tPaused) + '</span>' +
          '<span class="tef">✓ ' + formatTimer(effective) + '</span>' +
          '<div class="sc-acts">' + actsHtml + '</div>' +
        '</div>' +
      '</div>';
  }

  // ── Render one session card (.s-card) ────────────────────────────
  function _renderSessionCard(s) {
    var info = CAT_LABELS[s.category] || { label: s.category, cls: "" };
    var cls = [s.state === "completed" ? "done" : "",
               s.timer_state === "running" ? "running" : "",
               s.timer_state === "paused" ? "paused" : ""].join(" ");
    var time = s.start_time && s.end_time ? (s.start_time + "–" + s.end_time) : "";
    var icon = CAT_ICONS[s.category] || "";
    var shownTitle = window.App.UI.displayTitle(icon, s.title || "Sesión");
    var actsHtml = _buildActionsHtml(s);
    var notes = s.notes ? App.ContentBlocks._renderMd(_stripRitualH1(s.notes, s.category)) : "Sin descripción";
    var effective = Math.max(0, (s.timer_elapsed || 0) - (s.timer_paused_duration || 0));
    var body = _buildCardBodyHtml(s, info, shownTitle, notes, actsHtml, effective);
    return '<div class="s-card ' + cls + '" data-session-id="' + s.id + '">' +
      '<div class="sc-cat" style="background:' + (CAT_COLORS[s.category] || 'var(--border)') + '"></div>' +
      body +
      '<div class="sc-time" draggable="true" title="Arrastra para reordenar">' + time + '</div>' +
    '</div>';
  }

  // ── Build the whole sessions list HTML for a day ────────────────
  function _buildSessionsHtml(daySessions) {
    if (!daySessions.length) {
      return '<div class="empty-state"><span class="big">📅</span>No hay sesiones planificadas para este día.<br>' +
             '<button class="ht-btn" style="margin-top:8px;" data-action="add-session">➕ Añadir sesión</button></div>';
    }
    return daySessions.map(_renderSessionCard).join("");
  }

  // ── Build the day header (H2 + view toggle + add-session row) ───
  function _buildCenterHeaderHtml(dayNameCapitalized, dayNum, daySessions, mindPillHtml) {
    var listActive  = _viewMode === "list"     ? "active" : "";
    var dayActive   = _viewMode === "timeline" ? "active" : "";
    var weekActive  = _viewMode === "week"     ? "active" : "";
    var monthActive = _viewMode === "month"    ? "active" : "";
    var viewToggle = '<div class="view-toggle">' +
      '<button class="vt-btn ' + listActive  + '" data-view="list">📋 Lista</button>' +
      '<button class="vt-btn ' + dayActive   + '" data-view="timeline">📅 Día</button>' +
      '<button class="vt-btn ' + weekActive  + '" data-view="week">🗓️ Semana</button>' +
      '<button class="vt-btn ' + monthActive + '" data-view="month">📅 Mes</button>' +
    '</div>';
    return '' +
      '<div style="display:flex;align-items:baseline;justify-content:space-between;margin-bottom:12px;flex-wrap:wrap;gap:6px;">' +
        '<div style="display:flex;align-items:baseline;gap:6px;">' +
          '<h2 style="font-size:19px;font-weight:700;">' + dayNameCapitalized + ' <span style="font-weight:400;font-size:13px;color:var(--text-secondary);margin-left:6px;">' + dayNum + '</span></h2>' +
          mindPillHtml +
        '</div>' +
        '<div style="display:flex;gap:6px;align-items:center;">' + viewToggle + '</div>' +
      '</div>' +
      '<div style="display:flex;gap:8px;align-items:center;margin-bottom:6px;">' +
        '<button class="ht-btn" data-action="add-session" style="font-size:10px;">➕ Añadir sesión</button>' +
'<button class="ht-btn" data-action="open-calendars" style="font-size:10px;">📅 Calendarios</button>' +
          '<button type="button" class="cal-import-status" style="font-size:10px;color:var(--text-muted);padding:2px 8px;border-radius:10px;border:1px solid var(--border);background:transparent;cursor:pointer;font-family:inherit;" title="Estado de la sincronización del calendario — clic para ver las nuevas">⏳ comprobando…</button>' +
          '<span style="font-size:11px;color:var(--text-muted);background:var(--surface);padding:2px 10px;border-radius:10px;border:1px solid var(--border);">' + daySessions.length + ' sesiones</span>' +
      '</div>';
  }

  // ── Build the list body for the default "list" view mode ────────
  function _buildListBodyHtml(sessionsHtml) {
    return '<div class="sessions-scroll-wrapper">' +
           '<div class="section-label">⏰ Sesiones de hoy</div>' +
           '<div class="session-list">' + sessionsHtml + '</div>' +
           '</div>';
  }

  // ── Resolve the mind-gen pill HTML (delegated to AgendaMind if present)
  function _buildMindPillHtml(dateStr) {
    if (window.App.AgendaMind && window.App.AgendaMind.isGenerating(dateStr)) {
      return window.App.AgendaMind.renderPillHtml(dateStr);
    }
    return "";
  }

  // ── Auto-create daily "Ritual Matinal" (mind 04:30). Mind module owns
  // its own idempotency guard; fire-and-forget so it never blocks render.
  function _maybeAutoCreateRitual(dateStr, daySessions, callbacks) {
    if (window.App.AgendaMind && typeof window.App.AgendaMind.maybeAutoCreate === "function") {
      window.App.AgendaMind.maybeAutoCreate(dateStr, daySessions, callbacks);
    }
  }

  // ── Wire the per-card action buttons (play/pause/stop/del/move/copy/expand)
  // The timer buttons also drive the header "now playing" pill. Without it the
  // running session was only visible on its own card, so switching tab hid it
  // and the session ran on forgotten.
  function _wireCardActions(el, callbacks, weekData, dateStr) {
    var Mini = window.App.SessionMiniPlayer;
    function _syncPlayer(sid, timerState) {
      if (!Mini) return;
      Mini.setContext({ dateStr: dateStr, weekId: weekData.week_id,
                        onRefresh: callbacks.onRefresh });
      // After the list repaints, so the card's .te dataset is already fresh.
      setTimeout(function () { Mini.sync(sid, timerState); }, 60);
    }
    el.querySelectorAll(".btn-play").forEach(function (b) {
      b.addEventListener("click", async function (e) {
        e.stopPropagation();
        await AgendaTimer.patchSession(b.dataset.sid, "in_progress", "running");
        if (callbacks.onRefresh) callbacks.onRefresh();
        _syncPlayer(b.dataset.sid, "running");
      });
    });
    el.querySelectorAll(".btn-pause").forEach(function (b) {
      b.addEventListener("click", async function (e) {
        e.stopPropagation();
        await AgendaTimer.patchSession(b.dataset.sid, "in_progress", "paused");
        if (callbacks.onRefresh) callbacks.onRefresh();
        _syncPlayer(b.dataset.sid, "paused");
      });
    });
    el.querySelectorAll(".btn-stop").forEach(function (b) {
      b.addEventListener("click", async function (e) {
        e.stopPropagation();
        await AgendaTimer.patchSession(b.dataset.sid, "completed", "stopped");
        if (callbacks.onRefresh) callbacks.onRefresh();
        if (Mini) Mini.hide();
      });
    });
  }

  // ── Wire delete buttons (separate so the function stays under 30L) ─
  function _wireDeleteButtons(el, callbacks, dateStr) {
    el.querySelectorAll(".btn-del").forEach(function (b) {
      b.addEventListener("click", async function (e) {
        e.stopPropagation();
        var sid = b.dataset.sid;
        if (!confirm("🗑️ ¿Borrar esta sesión?")) return;
        var scat = b.dataset.scat || "";
        var stitle = b.dataset.stitle || "";
        var isRitual = scat === "mind" && stitle.trim() === "🧠 Ritual Matinal";
        try {
          await API.del("/agenda/session/" + sid);
          if (isRitual && window.App.AgendaMind && typeof window.App.AgendaMind.forgetDate === "function") {
            window.App.AgendaMind.forgetDate(dateStr);
          }
          if (callbacks.onRefresh) callbacks.onRefresh();
        } catch (err) {
          alert("Error al borrar la sesión");
        }
      });
    });
  }

  // ── Wire move / copy buttons on each card ────────────────────────
  function _wireMoveCopyButtons(el, callbacks, weekData, dateStr) {
    el.querySelectorAll(".btn-move").forEach(function (b) {
      b.addEventListener("click", function (e) {
        e.stopPropagation();
        AgendaCalendar.openMoveModal(b.dataset.sid, {
          onMoved: callbacks.onRefresh, weekData: weekData, dateStr: dateStr,
        });
      });
    });
    el.querySelectorAll(".btn-copy").forEach(function (b) {
      b.addEventListener("click", async function (e) {
        e.stopPropagation();
        try {
          await API.post("/agenda/session/" + b.dataset.sid + "/copy");
          if (callbacks.onRefresh) callbacks.onRefresh();
        } catch (err) { alert("Error al copiar la sesión"); }
      });
    });
  }

  // ── Wire expand/collapse buttons (note preview inside card) ──────
  function _wireExpandButtons(el) {
    el.querySelectorAll(".btn-expand").forEach(function (b) {
      b.addEventListener("click", function (e) {
        e.stopPropagation();
        var card = b.closest(".s-card");
        var sub = card && card.querySelector(".sc-sub");
        if (!sub) return;
        sub.classList.toggle("collapsed");
        b.textContent = sub.classList.contains("collapsed") ? "▶" : "▼";
        b.title = sub.classList.contains("collapsed") ? "Expandir nota" : "Contraer nota";
      });
    });
  }

  // ── Open external markdown links in a new tab (popup-blocker safe) ─
  function _wireListLinks(sessionList) {
    sessionList.addEventListener("click", function (e) {
      var a = e.target.closest("a[href]");
      if (!a) return;
      if (a.target === "_blank") return;
      var href = a.getAttribute("href") || "";
      if (href.startsWith("#")) return;
      e.preventDefault();
      e.stopPropagation();
      window.open(a.href, "_blank", "noopener");
    });
  }

  // ── Persist the new session order to the backend (full day PATCH) ─
  async function _reorderAndSave(weekId, dateStr, newOrder, onRefresh) {
    var fresh = await API.get("/agenda/week/" + weekId);
    var dayData = fresh.days && fresh.days[dateStr]
      ? fresh.days[dateStr] : { date: dateStr, sessions: [] };
    var sessionMap = {};
    (dayData.sessions || []).forEach(function (s) { sessionMap[s.id] = s; });
    var reordered = newOrder.map(function (id) { return sessionMap[id]; }).filter(Boolean);
    (dayData.sessions || []).forEach(function (s) {
      if (!reordered.find(function (r) { return r.id === s.id; })) reordered.push(s);
    });
    await API.patch("/agenda/week/" + weekId + "/day/" + dateStr, { sessions: reordered });
    if (onRefresh) onRefresh();
  }

  // ── Wire session list link clicks + DnD reorder ─────────────────
  function _wireListDnD(el, callbacks, weekData, dateStr) {
    var sessionList = el.querySelector(".session-list");
    if (!sessionList) return;
    _wireListLinks(sessionList);
    AgendaDnd.bindSessionList(sessionList, {
      onReorder: async function (newOrder) {
        try {
          await _reorderAndSave(weekData.week_id, dateStr, newOrder, callbacks.onRefresh);
        } catch (err) { if (callbacks.onRefresh) callbacks.onRefresh(); }
      },
    });
  }

  // ── Wire view toggle, add-session, calendars buttons ────────────
  function _wireCenterButtons(el, dateStr, weekData, callbacks) {
    el.querySelectorAll(".vt-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var mode = btn.dataset.view;
        if (mode === _viewMode) return;
        setViewMode(mode);
        if (callbacks.onRefresh) callbacks.onRefresh();
      });
    });
    el.querySelectorAll('[data-action="add-session"]').forEach(function (btn) {
      btn.addEventListener("click", function () {
        AgendaSession.openSessionOverlay(dateStr, weekData.week_id, null, callbacks.onRefresh);
      });
    });
    el.querySelectorAll('[data-action="open-calendars"]').forEach(function (btn) {
      btn.addEventListener("click", function () {
        window.App.AgendaCalendars.openOverlay(callbacks.onRefresh);
      });
    });
  }

  // ── Start ticking clocks for running/paused sessions ────────────
  function _startTimerTicks(daySessions) {
    daySessions.forEach(function (s) {
      if (s.timer_state === "running" || s.timer_state === "paused") {
        AgendaTimer.startTick(s.id);
      }
    });
  }

  // ── Render center column: session cards + habits table ───────────
async function renderAgendaCenter(dateStr, weekData, callbacks) {
      callbacks = callbacks || {};
      var onRefresh = callbacks.onRefresh;
      _curDateStr = dateStr;
      _curWeekId = (weekData && weekData.week_id) || "";
      _curRefresh = onRefresh;

      var el = document.getElementById("agenda-center");
    if (!el) return;
    var d = new Date(dateStr + "T12:00:00");
    var dayName = d.toLocaleDateString("es-ES", { weekday: "long" });
var dayNum = d.toLocaleDateString("es-ES", { day: "numeric", month: "long" });
      var dayNameCapitalized = dayName.charAt(0).toUpperCase() + dayName.slice(1);

      _registerClickOpen(el);

      // Month view is delegated to its own module
    if (_viewMode === "month") {
      var monthMod = window.App.AgendaMonthView;
      if (monthMod) return monthMod.render(el, dateStr, callbacks);
      el.innerHTML = '<div class="empty-state">📅 Vista mes (sub-fase C)</div>';
      return;
    }

    var daySessions = (weekData.days && weekData.days[dateStr] && weekData.days[dateStr].sessions) || [];
    _maybeAutoCreateRitual(dateStr, daySessions, callbacks);

    var mindPillHtml = _buildMindPillHtml(dateStr);
    var sessionsHtml = _buildSessionsHtml(daySessions);
    var headerHtml = _buildCenterHeaderHtml(dayNameCapitalized, dayNum, daySessions, mindPillHtml);

    var centerHtml = headerHtml;
    if (_viewMode === "timeline") {
      centerHtml += window.App.AgendaTimeline.renderDay(dateStr, daySessions, { onRefresh: onRefresh });
    } else if (_viewMode === "week") {
      var weekId = STATE.currentWeek;
      centerHtml += window.App.AgendaTimeline.renderWeek(weekId, weekData, { onRefresh: onRefresh });
    } else {
      centerHtml += _buildListBodyHtml(sessionsHtml);
    }
    centerHtml += '<div class="habits-wrapper"><div id="agenda-habits-table"></div></div>';
    el.innerHTML = centerHtml;

    AgendaHabits.renderTable({ onDataChange: function () {}, onDayClick: function () {} });
    _wireCardActions(el, callbacks, weekData, dateStr);
    _wireDeleteButtons(el, callbacks, dateStr);
    _wireMoveCopyButtons(el, callbacks, weekData, dateStr);
    _wireExpandButtons(el);
_wireListDnD(el, callbacks, weekData, dateStr);
      _wireCenterButtons(el, dateStr, weekData, callbacks);
      _wireTimelineDrag(el, onRefresh);
    _startTimerTicks(daySessions);
  }

  // ── Attach drag & drop to the timeline views ─────────────────────
    // bindWeekDrag / bindDrag existed and were exported by the timeline
    // modules but nothing ever called them, so dragging a session was a
    // no-op in the day and week views (only the month view was wired, in
    // agenda-month-view.js). Wire them here, next to the other post-render
    // hooks, so every render re-attaches to the fresh container.
    function _wireTimelineDrag(el, onRefresh) {
      var TL = window.App.AgendaTimeline;
      if (!TL) return;
      var weekCont = el.querySelector(".tlw-container");
      if (weekCont && typeof TL.bindWeekDrag === "function") {
        TL.bindWeekDrag(weekCont, { onRefresh: onRefresh });
        return;
      }
      var dayCont = el.querySelector(".tl-container");
      if (dayCont && typeof TL.bindDrag === "function") {
        TL.bindDrag(dayCont, { onRefresh: onRefresh });
      }
    }

    // ─── Public API ──────────────────────────────────────────────────
  return {
    renderAgendaCenter: renderAgendaCenter,
    setViewMode: setViewMode,
    getViewMode: getViewMode,
  };
})();
