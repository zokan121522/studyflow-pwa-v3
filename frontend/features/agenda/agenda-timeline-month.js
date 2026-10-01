// ─── Agenda Timeline — Month view + cross-day drag (sub-phase C) ──
// Attach onto window.App.AgendaTimeline (created by agenda-timeline.js).
// 7-column monthly grid (Lun-Dom) with up to 3 session blocks per day
// plus a "+N" overflow indicator. Pointer-drag moves a session block
// from one day-cell to another via POST /api/agenda/session/<id>/move.
//
// DEPENDENCIES:
//   window.App.AgendaTimeline — core namespace with helpers + constants
//   window.App.UI.todayStr — today highlight
// GLOBALS:
//   API — JSON helpers
// LOAD ORDER: agenda-timeline.js FIRST, then this file (the namespace
// must exist before we attach renderMonth / bindMonthDrag).

(function () {
  "use strict";

  var TL = window.App.AgendaTimeline;
  if (!TL) {
    console.error("[Timeline] agenda-timeline.js must load before agenda-timeline-month.js");
    return;
  }

  var DRAG_THR = TL.DRAG_THRESHOLD;
  var catColors = TL.catColors;
  var catIcons  = TL.catIcons;

  // ── Spanish month names (matches v2 + agenda-month-view.js) ─────
  var MONTH_NAMES = [
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
  ];

  // ── Short weekday label for the floating drag tip ───────────────
  function _dayNameFromDate(dateStr) {
    if (!dateStr) return "";
    var d = new Date(dateStr + "T12:00:00");
    return d.toLocaleDateString("es-ES", { weekday: "short" });
  }

  // ── Find the .tlm-cell under the pointer (skip empty cells) ─────
  function _getTargetMonthCell(clientX, clientY) {
    var cells = document.querySelectorAll(".tlm-cell:not(.tlm-empty)");
    for (var i = 0; i < cells.length; i++) {
      var r = cells[i].getBoundingClientRect();
      if (clientX >= r.left && clientX <= r.right &&
          clientY >= r.top  && clientY <= r.bottom) return cells[i];
    }
    return null;
  }

  // ── Build the 7-column grid rows for (year, month) ──────────────
  function _monthGrid(year, month, monthDays) {
    var first = new Date(year, month - 1, 1);
    var last  = new Date(year, month, 0);
    var totalDays = last.getDate();
    var startDow = first.getDay() - 1;
    if (startDow < 0) startDow = 6;
    var rows = [];
    var week = [];
    for (var i = 0; i < startDow; i++) week.push(null);
    for (var d = 1; d <= totalDays; d++) {
      var key = String(d);
      var dayData = (monthDays && monthDays[key]) || null;
      week.push({
        day: d,
        date: dayData ? dayData.date : null,
        sessions: dayData ? dayData.sessions : [],
      });
      if (week.length === 7) { rows.push(week); week = []; }
    }
    while (week.length > 0 && week.length < 7) week.push(null);
    if (week.length > 0) rows.push(week);
    return rows;
  }

  // ── Render the month grid (delegates header/nav/cells to helpers)
  function renderMonth(year, month, monthData, callbacks) {
    var today = window.App.UI.todayStr();
    var grid = _monthGrid(year, month, monthData && monthData.days || {});
    var html = '<div class="tlm-container">';
    html += renderMonthHead(year, month);
    html += renderDOWRow();
    html += renderMonthBody(grid, year, month, today);
    html += '</div>';
    return html;
  }

  function renderMonthHead(year, month) {
    return '<div class="tlm-head">' +
      '<span class="tlm-nav" data-offset="-1">◀</span>' +
      '<span class="tlm-title">' + MONTH_NAMES[month - 1] + ' ' + year + '</span>' +
      '<span class="tlm-nav" data-offset="1">▶</span>' +
    '</div>';
  }

  function renderDOWRow() {
    var dows = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"];
    var html = '<div class="tlm-dow-row">';
    for (var i = 0; i < dows.length; i++) {
      html += '<span class="tlm-dow">' + dows[i] + '</span>';
    }
    html += '</div>';
    return html;
  }

  function renderMonthBody(grid, year, month, today) {
    var html = '<div class="tlm-body">';
    for (var i = 0; i < grid.length; i++) {
      html += renderWeekRow(grid[i], year, month, today);
    }
    html += '</div>';
    return html;
  }

  function renderWeekRow(week, year, month, today) {
    var html = '<div class="tlm-week">';
    for (var j = 0; j < week.length; j++) {
      html += renderCell(week[j], year, month, today);
    }
    html += '</div>';
    return html;
  }

  function renderCell(cell, year, month, today) {
    if (!cell) return '<div class="tlm-cell tlm-empty"></div>';
    var dateStr = year + "-" +
      String(month).padStart(2, "0") + "-" +
      String(cell.day).padStart(2, "0");
    var isToday = dateStr === today ? " tlm-today" : "";
    var html = '<div class="tlm-cell' + isToday + '" data-date="' + dateStr + '">';
    html += '<span class="tlm-day-num">' + cell.day + '</span>';
    html += renderCellSessions(cell.sessions || [], dateStr);
    html += '</div>';
    return html;
  }

// Every session is rendered; the cell is what limits what you see. It is
    // sized to show six rows (see .tlm-cell min-height) and scrolls for the
    // rest. Capping the list at six and rendering a "+N" instead looked
    // equivalent but was not: the cap left nothing to scroll to, so the
    // seventh session of a day was simply unreachable.
    function renderCellSessions(sessions, dateStr) {
      var html = '<div class="tlm-sessions">';
      for (var i = 0; i < sessions.length; i++) {
        html += renderMonthSession(sessions[i], dateStr);
      }
      html += '</div>';
      return html;
    }

  function renderMonthSession(s, dateStr) {
    var color = catColors[s.category] || "var(--border)";
    var timeStr = s.start_time || "";
    var title = s.title || "";
    var cls = s.state === "completed" ? " tlm-s-done" : "";
    return '<div class="tlm-session' + cls + '"' +
      ' data-sid="' + s.id + '"' +
      ' data-date="' + dateStr + '"' +
      ' title="' + title + ' · ' + timeStr + '">' +
      '<span class="tlm-s-bar" style="background:' + color + ';"></span>' +
      '<span class="tlm-s-time">' + timeStr + '</span>' +
      '<span class="tlm-s-title">' + title + '</span>' +
    '</div>';
  }

  // ── MONTH DRAG ──────────────────────────────────────────────────
  // Each pointer handler <30 lines; shared state lives in ctx.

  function monthDragDown(ctx, clientX, clientY, block) {
    var sid = block.dataset.sid;
    if (!sid) return;
    ctx.dragState = {
      sid: sid,
      date: block.dataset.date || "",
      offsetX: clientX - block.getBoundingClientRect().left,
      offsetY: clientY - block.getBoundingClientRect().top,
      startX: clientX, startY: clientY,
    };
    var floatEl = block.cloneNode(true);
    floatEl.classList.add("tlm-s-floating");
    floatEl.style.position = "fixed";
    floatEl.style.pointerEvents = "none";
    floatEl.style.zIndex = 1000;
    floatEl.style.width = block.offsetWidth + "px";
    floatEl.style.overflow = "visible";
    floatEl.style.left = (clientX - ctx.dragState.offsetX) + "px";
    floatEl.style.top  = (clientY - ctx.dragState.offsetY) + "px";
    document.body.appendChild(floatEl);
    ctx.floatEl = floatEl;
    block.classList.add("tlm-s-dragging");
  }

  function monthDragMove(ctx, clientX, clientY) {
    if (!ctx.dragState || !ctx.floatEl) return;
    ctx.floatEl.style.left = (clientX - ctx.dragState.offsetX) + "px";
    ctx.floatEl.style.top  = (clientY - ctx.dragState.offsetY) + "px";
    clearHighlights();
    var targetCell = _getTargetMonthCell(clientX, clientY);
    if (!targetCell) return;
    targetCell.classList.add("tlm-cell-highlight");
    TL.updateFloatTip(ctx.floatEl, _dayNameFromDate(targetCell.dataset.date), "tlm-drag-tip");
  }

  function monthDragUp(ctx, clientX, clientY) {
    if (!ctx.dragState) return;
    cleanupFloat(ctx);
    clearHighlights();
    clearDragging(ctx);
    var dx = clientX - ctx.dragState.startX;
    var dy = clientY - ctx.dragState.startY;
    if (Math.abs(dx) < DRAG_THR && Math.abs(dy) < DRAG_THR) { resetCtx(ctx); return; }
    var sid = ctx.dragState.sid;
    var origDate = ctx.dragState.date;
    resetCtx(ctx);
    var targetCell = _getTargetMonthCell(clientX, clientY);
    if (!targetCell) return;
    var targetDate = targetCell.dataset.date;
    if (!targetDate) return;
    relocateMonthBlock(ctx, sid, targetCell, targetDate, origDate);
    if (targetDate !== origDate) {
      API.post("/agenda/session/" + sid + "/move", { target_date: targetDate })
        .catch(function () { if (ctx.onRefresh) ctx.onRefresh(); });
    }
  }

  function monthDragCancel(ctx) {
    cleanupFloat(ctx); clearHighlights(); clearDragging(ctx); resetCtx(ctx);
  }

  function cleanupFloat(ctx) {
    if (ctx.floatEl) { ctx.floatEl.remove(); ctx.floatEl = null; }
  }

  function clearHighlights() {
    var cells = document.querySelectorAll(".tlm-cell-highlight");
    for (var i = 0; i < cells.length; i++) cells[i].classList.remove("tlm-cell-highlight");
  }

  function clearDragging(ctx) {
    var dragging = ctx.container.querySelectorAll(".tlm-s-dragging");
    for (var i = 0; i < dragging.length; i++) {
      dragging[i].classList.remove("tlm-s-dragging");
    }
  }

  function resetCtx(ctx) { ctx.dragState = null; }

  // Move the DOM block to the new cell + decrement the source
  // overflow badge (if any). Real data is committed by the caller.
  function relocateMonthBlock(ctx, sid, targetCell, targetDate, origDate) {
    if (targetDate === origDate) return;
    var dom = ctx.container.querySelector('.tlm-session[data-sid="' + sid + '"]');
    if (!dom) return;
    dom.remove();
    var newCell = ctx.container.querySelector('.tlm-cell[data-date="' + targetDate + '"]');
    if (!newCell) return;
    var wrap = newCell.querySelector(".tlm-sessions");
    if (!wrap) return;
    var overflowEl = wrap.querySelector(".tlm-overflow");
    if (overflowEl) {
      var n = parseInt(overflowEl.textContent.replace("+", ""), 10);
      if (n > 1) overflowEl.textContent = "+" + (n - 1);
      else overflowEl.remove();
    }
    dom.dataset.date = targetDate;
    wrap.appendChild(dom);
  }

  // Thin orchestrator: build ctx, wire mouse+touch.
  function bindMonthDrag(container, callbacks) {
    var ctx = {
      container: container,
      onRefresh: (callbacks && callbacks.onRefresh) || null,
      dragState: null, floatEl: null,
    };
    TL.bindPointerEvents(container, {
      blockSelector: ".tlm-session",
      onDown: function (e, block) {
        monthDragDown(ctx, e.clientX, e.clientY, block); e.preventDefault();
      },
      onMove: function (clientX, clientY) { monthDragMove(ctx, clientX, clientY); },
      onUp:   function (clientX, clientY) { monthDragUp(ctx, clientX, clientY); },
      onCancel: function () { monthDragCancel(ctx); },
    });
  }

  // Attach to the same namespace the core file created.
  TL.renderMonth = renderMonth;
  TL.bindMonthDrag = bindMonthDrag;
  console.log("[Timeline] agenda-timeline-month.js loaded");
})();