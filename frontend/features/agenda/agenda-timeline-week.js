// ─── Agenda Timeline — Week view + cross-day drag (sub-phase C) ───
// Attach onto window.App.AgendaTimeline (created by agenda-timeline.js).
// 7-column grid (one column per weekday), each column is a 24h mini-day
// with hour rows. Pointer-drag moves a block vertically (snap 10 min)
// and, if dropped onto a different column, also moves the session to
// that date via POST /api/agenda/session/<id>/move.
//
// DEPENDENCIES:
//   window.App.AgendaTimeline — core namespace with helpers + constants
//   window.App.UI.getDaysOfWeek, todayStr — week layout
// GLOBALS:
//   API — JSON helpers
// LOAD ORDER: agenda-timeline.js FIRST, then this file (the namespace
// must exist before we attach our renderWeek / bindWeekDrag).

(function () {
  "use strict";

  var TL = window.App.AgendaTimeline;
  if (!TL) {
    console.error("[Timeline] agenda-timeline.js must load before agenda-timeline-week.js");
    return;
  }

  var HOUR_H     = TL.HOUR_H;
  var TOTAL_H    = TL.TOTAL_H;
  var SNAP_MIN   = TL.SNAP_MIN;
  var DRAG_THR   = TL.DRAG_THRESHOLD;
  var parseHHMM  = TL.parseHHMM;
  var timeToPx   = TL.timeToPx;
  var pxToTime   = TL.pxToTime;
  var formatTime = TL.formatTime;
  var catColors  = TL.catColors;
  var catIcons   = TL.catIcons;
  var displayTitle = TL.displayTitle;
  var updateFloatTip = TL.updateFloatTip;
  var computeNewTimes = TL.computeNewTimes;
  var bindPointerEvents = TL.bindPointerEvents;

  // ── Short weekday label for the floating drag tip ───────────────
  function _dayNameFromDate(dateStr) {
    if (!dateStr) return "";
    var d = new Date(dateStr + "T12:00:00");
    return d.toLocaleDateString("es-ES", { weekday: "short" });
  }

  // ── Find the .tlw-col whose horizontal extent contains clientX ──
  function _getTargetCol(clientX) {
    var cols = document.querySelectorAll(".tlw-col");
    for (var i = 0; i < cols.length; i++) {
      var r = cols[i].getBoundingClientRect();
      if (clientX >= r.left && clientX <= r.right) return cols[i];
    }
    return null;
  }

  // ── Build the .tlw-block class string (done/running/paused) ──────
  function _weekBlockCls(session) {
    var cls = "";
    if (session.state === "completed") cls += " tlw-block-done";
    if (session.timer_state === "running") cls += " tlw-block-running";
    if (session.timer_state === "paused")  cls += " tlw-block-paused";
    return cls;
  }

  // ── One week-column block (.tlw-block) ──────────────────────────
  function renderWeekBlock(session) {
    var start = parseHHMM(session.start_time);
    if (!start) return null;
    var end = parseHHMM(session.end_time);
    var durationMin = end
      ? Math.max(15, end.totalMin - start.totalMin)
      : 60;
    var topPx = timeToPx(start);
    var heightPx = Math.max(14, (durationMin / 1440) * TOTAL_H);
    var color = catColors[session.category] || "var(--border)";
    var icon = catIcons[session.category] || "";
    var timeStr = session.start_time || "";
    var cls = _weekBlockCls(session);
    return '<div class="tlw-block' + cls + '"' +
      ' data-sid="' + session.id + '"' +
      ' data-date="' + (session.day_date || "") + '"' +
      ' data-start="' + (session.start_time || "") + '"' +
      ' data-end="'   + (session.end_time   || "") + '"' +
      ' style="top:' + topPx + 'px;height:' + heightPx + 'px;' +
      'background:' + color + '15;border-left-color:' + color + ';"' +
      ' title="' + (session.title || "Sesión") + ' · ' + timeStr + '">' +
      '<div class="tlw-block-bar" style="background:' + color + ';"></div>' +
      '<div class="tlw-block-body">' +
        '<span class="tlw-block-title">' + displayTitle(icon, session.title) + '</span>' +
        '<span class="tlw-block-time">' + timeStr + '</span>' +
      '</div>' +
    '</div>';
  }

  // ── Build the .tlw-container HTML ───────────────────────────────
  function renderWeek(weekId, weekData, callbacks) {
    callbacks = callbacks || {};
    var days = window.App.UI.getDaysOfWeek(weekId);
    var today = window.App.UI.todayStr();
    var totalSessions = countWeekSessions(days, weekData);
    var html = '<div class="tlw-container">';
    html += renderWeekHeader(days, today);
    html += renderWeekBody(days, today, weekData);
    html += renderWeekUnscheduled(days, today, weekData);
    html += '</div>';
    return html;
  }

  function countWeekSessions(days, weekData) {
    var n = 0;
    for (var i = 0; i < days.length; i++) {
      var ds = weekData.days && weekData.days[days[i].date]
        && weekData.days[days[i].date].sessions || [];
      n += ds.length;
    }
    return n;
  }

  function renderWeekHeader(days, today) {
    var html = '<div class="tlw-header">';
    html += '<div class="tlw-gutter-spacer"></div>';
    for (var i = 0; i < days.length; i++) {
      var d = days[i];
      var isToday = d.date === today ? " today" : "";
      html += '<div class="tlw-day-header' + isToday + '">' +
        '<span class="tlw-day-name">' + d.name + '</span>' +
        '<span class="tlw-day-num">' + d.num + '</span>' +
      '</div>';
    }
    html += '</div>';
    return html;
  }

  function renderWeekBody(days, today, weekData) {
    var html = '<div class="tlw-body">';
    html += '<div class="tlw-gutter">' + TL.renderHourLabels() + '</div>';
    for (var i = 0; i < days.length; i++) {
      html += renderWeekColumn(days[i], today, weekData);
    }
    html += '</div>';
    return html;
  }

  function renderWeekColumn(d, today, weekData) {
    var daySessions = (weekData.days && weekData.days[d.date]
      && weekData.days[d.date].sessions) || [];
    var colClass = "tlw-col" + (d.date === today ? " today" : "");
    var html = '<div class="' + colClass + '" data-date="' + d.date + '">';
    html += '<div class="tlw-grid" style="height:' + TOTAL_H + 'px;">';
    for (var h = 0; h < 24; h++) {
      html += '<div class="tlw-row" style="height:' + HOUR_H + 'px;"></div>';
    }
    html += '<div class="tlw-blocks">';
    for (var j = 0; j < daySessions.length; j++) {
      var b = renderWeekBlock(daySessions[j]);
      if (b) html += b;
    }
    html += '</div></div></div>'; // .tlw-blocks / .tlw-grid / .tlw-col
    return html;
  }

  function renderWeekUnscheduled(days, today, weekData) {
    var html = '<div class="tlw-unscheduled">' +
      '<div class="tlw-unscheduled-label">⏰ Sin programar</div>' +
      '<div class="tlw-unscheduled-days">';
    var hadAny = false;
    for (var i = 0; i < days.length; i++) {
      var d = days[i];
      var daySessions = (weekData.days && weekData.days[d.date]
        && weekData.days[d.date].sessions) || [];
      var unsched = daySessions.filter(function (s) { return !s.start_time; });
      if (!unsched.length) continue;
      hadAny = true;
      var isToday = d.date === today ? " today" : "";
      html += '<div class="tlw-unsched-day' + isToday + '">' +
        '<div class="tlw-unsched-day-label">' + d.name + ' ' + d.num + '</div>';
      for (var j = 0; j < unsched.length; j++) {
        var s = unsched[j];
        var color = catColors[s.category] || "var(--border)";
        var icon = catIcons[s.category] || "";
        html += '<div class="tlw-unsched-item" data-sid="' + s.id + '">' +
          '<span class="tlw-unsched-dot" style="background:' + color + ';"></span>' +
          displayTitle(icon, s.title) +
        '</div>';
      }
      html += '</div>';
    }
    html += '</div></div>';
    return hadAny ? html : "";
  }

  // ── WEEK DRAG ───────────────────────────────────────────────────
  // Pointer-drag on a .tlw-block. Vertical snap → PATCH start/end.
  // Cross-column drop → POST /move with target_date. State lives in a
  // ctx object so each handler stays under 30 lines.

  function weekDragDown(ctx, clientX, clientY, block) {
    var sid = block.dataset.sid;
    if (!sid) return;
    ctx.dragState = {
      sid: sid,
      date: block.dataset.date || "",
      start: block.dataset.start || "",
      end:   block.dataset.end   || "",
      offsetX: clientX - block.getBoundingClientRect().left,
      offsetY: clientY - block.getBoundingClientRect().top,
      startX: clientX, startY: clientY,
    };
    ctx.blockOrig = block;
    var floatEl = block.cloneNode(true);
    floatEl.classList.add("tlw-block-floating");
    floatEl.style.position = "fixed";
    floatEl.style.width = block.offsetWidth + "px";
    floatEl.style.pointerEvents = "none";
    floatEl.style.zIndex = 1000;
    floatEl.style.overflow = "visible";
    floatEl.style.left = (clientX - ctx.dragState.offsetX) + "px";
    floatEl.style.top  = (clientY - ctx.dragState.offsetY) + "px";
    document.body.appendChild(floatEl);
    ctx.floatEl = floatEl;
    block.classList.add("tlw-block-dragging");
  }

  function weekDragMove(ctx, clientX, clientY) {
    if (!ctx.dragState || !ctx.floatEl) return;
    ctx.floatEl.style.left = (clientX - ctx.dragState.offsetX) + "px";
    ctx.floatEl.style.top  = (clientY - ctx.dragState.offsetY) + "px";
    clearColHighlights();
    var targetCol = _getTargetCol(clientX);
    if (!targetCol) return;
    targetCol.classList.add("tlw-col-highlight");
    var gridEl = targetCol.querySelector(".tlw-grid");
    if (!gridEl) return;
    var snap = pxToTime(clientY - gridEl.getBoundingClientRect().top, SNAP_MIN);
    updateFloatTip(ctx.floatEl,
      _dayNameFromDate(targetCol.dataset.date) + " " + formatTime(snap),
      "tlw-drag-tip");
  }

  function weekDragUp(ctx, clientX, clientY) {
    if (!ctx.dragState) return;
    cleanupFloat(ctx);
    clearColHighlights();
    var dx = clientX - ctx.dragState.startX;
    var dy = clientY - ctx.dragState.startY;
    if (Math.abs(dx) < DRAG_THR && Math.abs(dy) < DRAG_THR) { resetCtx(ctx); return; }
    var sid = ctx.dragState.sid;
    var origDate  = ctx.dragState.date;
    var origStart = ctx.dragState.start;
    var origEnd   = ctx.dragState.end;
    resetCtx(ctx);
    commitWeekMove(ctx, sid, origDate, origStart, origEnd, clientX, clientY);
  }

  function weekDragCancel(ctx) {
    cleanupFloat(ctx); clearColHighlights(); resetCtx(ctx);
  }

  function cleanupFloat(ctx) {
    if (ctx.floatEl) { ctx.floatEl.remove(); ctx.floatEl = null; }
    if (ctx.blockOrig) ctx.blockOrig.classList.remove("tlw-block-dragging");
  }

  function clearColHighlights() {
    var cols = document.querySelectorAll(".tlw-col-highlight");
    for (var i = 0; i < cols.length; i++) cols[i].classList.remove("tlw-col-highlight");
  }

  function resetCtx(ctx) { ctx.dragState = null; ctx.blockOrig = null; }

  // Snap to vertical grid → compute new times → if cross-day, move
  // DOM block to the new column → PATCH time + POST move.
  function commitWeekMove(ctx, sid, origDate, origStart, origEnd, clientX, clientY) {
    var targetCol = _getTargetCol(clientX);
    if (!targetCol) return;
    var targetDate = targetCol.dataset.date;
    if (!targetDate) return;
    var gridEl = targetCol.querySelector(".tlw-grid");
    if (!gridEl) return;
    var snap = pxToTime(clientY - gridEl.getBoundingClientRect().top, SNAP_MIN);
    var t = computeNewTimes(snap, origStart, origEnd);
    relocateWeekBlock(ctx, sid, targetCol, targetDate, origDate,
                      snap, t.newStartStr, t.newEndStr);
    patchAndMove(sid, t.newStartStr, t.newEndStr, targetDate, origDate, ctx.onRefresh);
  }

  function relocateWeekBlock(ctx, sid, targetCol, targetDate, origDate,
                             snap, newStartStr, newEndStr) {
    var dom = ctx.container.querySelector('.tlw-block[data-sid="' + sid + '"]');
    if (!dom) return;
    if (targetDate !== origDate) {
      var targetBlocks = targetCol.querySelector(".tlw-blocks");
      if (targetBlocks) {
        dom.remove();
        dom.dataset.date = targetDate;
        targetBlocks.appendChild(dom);
      }
    }
    dom.style.top = timeToPx(snap) + "px";
    dom.dataset.start = newStartStr;
    dom.dataset.end   = newEndStr;
    var timeEl = dom.querySelector(".tlw-block-time");
    if (timeEl) timeEl.textContent = newStartStr;
  }

  function patchAndMove(sid, newStartStr, newEndStr, targetDate, origDate, onRefresh) {
    var seq = [];
    seq.push(API.patch("/agenda/session/" + sid, {
      start_time: newStartStr, end_time: newEndStr,
    }));
    if (targetDate !== origDate) {
      seq.push(API.post("/agenda/session/" + sid + "/move", { target_date: targetDate }));
    }
    Promise.all(seq).catch(function () { if (onRefresh) onRefresh(); });
  }

  // Thin orchestrator: build the ctx, wire mouse+touch.
  function bindWeekDrag(container, callbacks) {
    var ctx = {
      container: container,
      onRefresh: (callbacks && callbacks.onRefresh) || null,
      dragState: null, floatEl: null, blockOrig: null,
    };
    bindPointerEvents(container, {
      blockSelector: ".tlw-block",
      onDown: function (e, block) {
        weekDragDown(ctx, e.clientX, e.clientY, block); e.preventDefault();
      },
      onMove: function (clientX, clientY) { weekDragMove(ctx, clientX, clientY); },
      onUp:   function (clientX, clientY) { weekDragUp(ctx, clientX, clientY); },
      onCancel: function () { weekDragCancel(ctx); },
    });
  }

  // Attach to the same namespace the core file created.
  TL.renderWeek = renderWeek;
  TL.bindWeekDrag = bindWeekDrag;
  console.log("[Timeline] agenda-timeline-week.js loaded");
})();