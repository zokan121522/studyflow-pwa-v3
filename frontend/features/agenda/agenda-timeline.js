// ─── Agenda Timeline — Day view + shared core (sub-phase C port) ─
// Namespace: window.App.AgendaTimeline
// Split from v2 agenda-timeline.js (1011 lines) into 3 files so each one
// stays under the 500-line cap. This file owns the public namespace, the
// shared helpers + constants, and the day-view render + drag. The week
// and month views live in agenda-timeline-week.js and
// agenda-timeline-month.js and attach renderWeek / renderMonth /
// bindWeekDrag / bindMonthDrag onto this same namespace so agenda-core
// and agenda-month-view can keep calling App.AgendaTimeline.renderXxx
// unchanged.
//
// DEPENDENCIES (all global on `window.App.*`):
//   App.UI.displayTitle   — collapse duplicate icon prefix
//   App.UI.todayStr       — YYYY-MM-DD
// GLOBALS:
//   STATE                 — v2-style currentDay / currentWeek
//   API                   — { get, post, patch, del } JSON helpers

window.App = window.App || {};
window.App.AgendaTimeline = window.App.AgendaTimeline || (function () {
  "use strict";

  // ── Constants ───────────────────────────────────────────────────
  var HOUR_H = 40;             // px per hour row
  var TOTAL_H = 24 * HOUR_H;   // 960px
  var DRAG_THRESHOLD = 5;      // px of movement required to commit a drag
  var SNAP_MIN = 10;           // 10-minute snap grid

  // ── Helpers: HH:MM ⇄ pixel ⇄ time ───────────────────────────────
  function parseHHMM(str) {
    if (!str) return null;
    var parts = String(str).split(":");
    if (parts.length !== 2) return null;
    var h = parseInt(parts[0], 10);
    var m = parseInt(parts[1], 10);
    if (isNaN(h) || isNaN(m)) return null;
    return { hour: h, minute: m, totalMin: h * 60 + m };
  }

  function timeToPx(t) {
    return (t.totalMin / 1440) * TOTAL_H;
  }

  function pxToTime(px, snapMin) {
    snapMin = snapMin || 1;
    var ratio = Math.max(0, Math.min(1, px / TOTAL_H));
    var totalMin = ratio * 1440;
    totalMin = Math.round(totalMin / snapMin) * snapMin;
    if (totalMin >= 1440) totalMin = 1439;
    var h = Math.floor(totalMin / 60);
    var m = Math.round(totalMin % 60);
    if (m >= 60) { h += 1; m = 0; }
    if (h >= 24) { h = 23; m = 59; }
    return { hour: h, minute: m, totalMin: h * 60 + m };
  }

  function formatTime(t) {
    return String(t.hour).padStart(2, "0") + ":" + String(t.minute).padStart(2, "0");
  }

  // ── Category colour / icon maps (kept in sync with agenda-core) ─
  var catColors = {
    formal_study: "var(--cat-formal)", self_study: "var(--cat-self)",
    work: "var(--cat-work)", language: "var(--cat-lang)",
    health: "var(--cat-health)", mind: "var(--cat-mind)", project: "var(--cat-project)",
  };
  var catIcons = {
    formal_study: "🎓", self_study: "📚", work: "💼",
    language: "🌐", health: "🏋️", mind: "🧠", project: "⚡",
  };

  // displayTitle is defined in agenda-glue (v3) and studyflow-hub/tierra
  // (v2). If neither has run yet we fall back to a basic joiner so the
  // module never throws on cold load.
  var displayTitle = (window.App && window.App.UI && window.App.UI.displayTitle)
    ? window.App.UI.displayTitle
    : function (icon, title) { return icon ? (icon + " " + (title || "Sesión")) : (title || "Sesión"); };

  // ── 24 hour labels for the left gutter ──────────────────────────
  function renderHourLabels() {
    var html = "";
    for (var h = 0; h < 24; h++) {
      html += '<div class="tl-hour" style="height:' + HOUR_H + 'px;">' +
        String(h).padStart(2, "0") + '</div>';
    }
    return html;
  }

  // ── One scheduled block (.tl-block) ─────────────────────────────
  function renderBlock(session) {
    var start = parseHHMM(session.start_time);
    if (!start) return null;
    var end = parseHHMM(session.end_time);
    var durationMin = end
      ? Math.max(15, end.totalMin - start.totalMin)
      : 60;
    var topPx = timeToPx(start);
    var heightPx = Math.max(18, (durationMin / 1440) * TOTAL_H);
    var color = catColors[session.category] || "var(--border)";
    var icon = catIcons[session.category] || "";
    var timeStr = session.start_time + (session.end_time ? "–" + session.end_time : "");
    var cls = "";
    if (session.state === "completed") cls += " tl-block-done";
    if (session.timer_state === "running") cls += " tl-block-running";
    if (session.timer_state === "paused") cls += " tl-block-paused";
    return '<div class="tl-block' + cls + '"' +
      ' data-sid="' + session.id + '"' +
      ' data-start="' + session.start_time + '"' +
      ' data-end="' + (session.end_time || "") + '"' +
      ' style="top:' + topPx + 'px;height:' + heightPx + 'px;' +
      'background:' + color + '15;border-left-color:' + color + ';">' +
      '<div class="tl-block-bar" style="background:' + color + ';"></div>' +
      '<div class="tl-block-body">' +
        '<span class="tl-block-title">' + displayTitle(icon, session.title) + '</span>' +
        '<span class="tl-block-time">' + timeStr + '</span>' +
      '</div>' +
    '</div>';
  }

  // ── Render the day timeline HTML ────────────────────────────────
  function renderDay(dateStr, sessions, callbacks) {
    callbacks = callbacks || {};
    var scheduled = [];
    var unscheduled = [];
    for (var i = 0; i < sessions.length; i++) {
      var s = sessions[i];
      if (s.start_time && parseHHMM(s.start_time)) scheduled.push(s);
      else unscheduled.push(s);
    }
    scheduled.sort(function (a, b) {
      return parseHHMM(a.start_time).totalMin - parseHHMM(b.start_time).totalMin;
    });
    var blocksHtml = "";
    for (var j = 0; j < scheduled.length; j++) {
      var b = renderBlock(scheduled[j]);
      if (b) blocksHtml += b;
    }
    return buildDayHtml(dateStr, sessions, blocksHtml, unscheduled);
  }

  // ── Build the full .tl-container HTML ───────────────────────────
  function buildDayHtml(dateStr, sessions, blocksHtml, unscheduled) {
    var html = '<div class="tl-container">';
    html += '<div class="tl-header">';
    html += '<span class="tl-header-title">📅 Línea temporal</span>';
    html += '<span class="tl-header-count">' + sessions.length + ' sesiones</span>';
    html += '</div>';
    html += '<div class="tl-body">';
    html += '<div class="tl-gutter">' + renderHourLabels() + '</div>';
    html += '<div class="tl-grid" style="height:' + TOTAL_H + 'px;">';
    for (var h = 0; h < 24; h++) {
      html += '<div class="tl-row" style="height:' + HOUR_H + 'px;"></div>';
    }
    html += '<div class="tl-blocks">' + blocksHtml + '</div>';
    html += '</div></div>'; // .tl-grid / .tl-body
    html += buildUnscheduledHtml(unscheduled);
    html += '</div>'; // .tl-container
    return html;
  }

  // ── Unscheduled list under the day grid ─────────────────────────
  function buildUnscheduledHtml(unscheduled) {
    if (!unscheduled.length) return "";
    var html = '<div class="tl-unscheduled">';
    html += '<div class="tl-unscheduled-label">⏰ Sin programar</div>';
    html += '<div class="tl-unscheduled-list">';
    for (var i = 0; i < unscheduled.length; i++) {
      var s = unscheduled[i];
      var icon = catIcons[s.category] || "";
      var color = catColors[s.category] || "var(--border)";
      html += '<div class="tl-unsched-item" data-sid="' + s.id + '">' +
        '<span class="tl-unsched-dot" style="background:' + color + ';"></span>' +
        displayTitle(icon, s.title) +
      '</div>';
    }
    html += '</div></div>';
    return html;
  }

  // ── DAY DRAG ────────────────────────────────────────────────────
  // The shared drag state lives in a per-bind ctx object passed to the
  // four pointer handlers (each <30 lines). bindDrag itself just builds
  // the ctx and calls bindPointerEvents to wire the events.

  // Compute the new start/end times after a vertical drop. Snap stays
  // inside the day (max 23:59).
  function computeNewTimes(snap, origStart, origEnd) {
    var newStartStr = formatTime(snap);
    var oldStart = parseHHMM(origStart);
    var oldEnd   = parseHHMM(origEnd);
    var durationMin = oldEnd
      ? Math.max(15, oldEnd.totalMin - oldStart.totalMin)
      : 60;
    var newEndMin = snap.totalMin + durationMin;
    var newEndHour = Math.floor(newEndMin / 60);
    var newEndMinRem = Math.round(newEndMin % 60);
    if (newEndHour >= 24) { newEndHour = 23; newEndMinRem = 59; }
    return {
      newStartStr: newStartStr,
      newEndStr: formatTime({ hour: newEndHour, minute: newEndMinRem }),
    };
  }

  // Move the day block DOM node to its new top/data-* attributes.
  function moveDomBlock(container, sid, snap, newStartStr, newEndStr) {
    var dom = container.querySelector('.tl-block[data-sid="' + sid + '"]');
    if (!dom) return;
    dom.style.top = timeToPx(snap) + "px";
    dom.dataset.start = newStartStr;
    dom.dataset.end   = newEndStr;
    var timeEl = dom.querySelector(".tl-block-time");
    if (timeEl) timeEl.textContent = newStartStr + "–" + newEndStr;
  }

  // PATCH /api/agenda/session/<id> with the new time range. On error
  // we trigger onRefresh so the day re-renders from the server truth.
  function patchSessionTime(sid, newStartStr, newEndStr, onRefresh) {
    API.patch("/agenda/session/" + sid, {
      start_time: newStartStr,
      end_time:   newEndStr,
    }).catch(function () { if (onRefresh) onRefresh(); });
  }

  // Pointer-down on a .tl-block: clone the block into a floating ghost.
  function dayDragDown(ctx, clientX, clientY, block) {
    var sid = block.dataset.sid;
    if (!sid) return;
    ctx.dragState = {
      sid: sid,
      start: block.dataset.start || "",
      end:   block.dataset.end   || "",
      offsetX: clientX - block.getBoundingClientRect().left,
      offsetY: clientY - block.getBoundingClientRect().top,
      startX: clientX, startY: clientY,
    };
    ctx.blockOrig = block;
    var floatEl = block.cloneNode(true);
    floatEl.classList.add("tl-block-floating");
    floatEl.style.position = "fixed";
    floatEl.style.width  = block.offsetWidth + "px";
    floatEl.style.pointerEvents = "none";
    floatEl.style.zIndex = 1000;
    floatEl.style.overflow = "visible";
    floatEl.style.left = (clientX - ctx.dragState.offsetX) + "px";
    floatEl.style.top  = (clientY - ctx.dragState.offsetY) + "px";
    document.body.appendChild(floatEl);
    ctx.floatEl = floatEl;
    block.classList.add("tl-block-dragging");
  }

  // Pointer-move: keep the floating ghost under the cursor + update the
  // "snap to 10-min" tooltip so the user sees the drop time live.
  function dayDragMove(ctx, clientX, clientY) {
    if (!ctx.dragState || !ctx.floatEl) return;
    ctx.floatEl.style.left = (clientX - ctx.dragState.offsetX) + "px";
    ctx.floatEl.style.top  = (clientY - ctx.dragState.offsetY) + "px";
    var gridEl = ctx.container.querySelector(".tl-grid");
    if (!gridEl) return;
    var gridPx = Math.max(0, Math.min(TOTAL_H,
      clientY - gridEl.getBoundingClientRect().top));
    var snap = pxToTime(gridPx, SNAP_MIN);
    updateFloatTip(ctx.floatEl, formatTime(snap), "tl-drag-tip");
  }

  // Pointer-up: tiny drag → no-op; real drag → PATCH the new times.
  function dayDragUp(ctx, clientX, clientY) {
    if (!ctx.dragState) return;
    cleanupFloat(ctx);
    var dx = clientX - ctx.dragState.startX;
    var dy = clientY - ctx.dragState.startY;
    if (Math.abs(dx) < DRAG_THRESHOLD && Math.abs(dy) < DRAG_THRESHOLD) {
      resetCtx(ctx); return;
    }
    var sid = ctx.dragState.sid;
    var origStart = ctx.dragState.start;
    var origEnd   = ctx.dragState.end;
    resetCtx(ctx);
    commitDayMove(ctx, sid, origStart, origEnd, clientY);
  }

  // Pointer-cancel (touchcancel / ESC): drop the ghost without committing.
  function dayDragCancel(ctx) {
    cleanupFloat(ctx);
    resetCtx(ctx);
  }

  function cleanupFloat(ctx) {
    if (ctx.floatEl) { ctx.floatEl.remove(); ctx.floatEl = null; }
    if (ctx.blockOrig) ctx.blockOrig.classList.remove("tl-block-dragging");
  }

  function resetCtx(ctx) { ctx.dragState = null; ctx.blockOrig = null; }

  function commitDayMove(ctx, sid, origStart, origEnd, clientY) {
    var gridEl = ctx.container.querySelector(".tl-grid");
    if (!gridEl) return;
    var snap = pxToTime(
      Math.max(0, Math.min(TOTAL_H, clientY - gridEl.getBoundingClientRect().top)),
      SNAP_MIN
    );
    var t = computeNewTimes(snap, origStart, origEnd);
    moveDomBlock(ctx.container, sid, snap, t.newStartStr, t.newEndStr);
    patchSessionTime(sid, t.newStartStr, t.newEndStr, ctx.onRefresh);
  }

  // Thin orchestrator: build the ctx, wire mouse+touch via the shared
  // bindPointerEvents helper. The four handlers are module-scope.
  function bindDrag(container, callbacks) {
    var ctx = {
      container: container,
      onRefresh: (callbacks && callbacks.onRefresh) || null,
      dragState: null, floatEl: null, blockOrig: null,
    };
    bindPointerEvents(container, {
      blockSelector: ".tl-block",
      onDown: function (e, block) {
        dayDragDown(ctx, e.clientX, e.clientY, block); e.preventDefault();
      },
      onMove: function (clientX, clientY) { dayDragMove(ctx, clientX, clientY); },
      onUp:   function (clientX, clientY) { dayDragUp(ctx, clientX, clientY); },
      onCancel: function () { dayDragCancel(ctx); },
    });
  }

  // ── Shared drag tooltip updater (works for .tl-* / .tlw-* / .tlm-*)
  function updateFloatTip(floatEl, text, cls) {
    cls = cls || "tl-drag-tip";
    var tip = floatEl.querySelector("." + cls);
    if (!tip) {
      tip = document.createElement("div");
      tip.className = cls;
      floatEl.appendChild(tip);
    }
    tip.textContent = text;
  }

  // ── Shared pointer-event binding (mouse + touch) for day / week /
  // month drag handlers. Single helper so each bindDrag / bindWeekDrag /
  // bindMonthDrag only declares the four callbacks it needs.
  function bindPointerEvents(container, opts) {
    container.addEventListener("mousedown", function (e) {
      var block = e.target.closest(opts.blockSelector);
      if (!block) return;
      opts.onDown(e, block);
    });
    document.addEventListener("mousemove", function (e) {
      opts.onMove(e.clientX, e.clientY);
    });
    document.addEventListener("mouseup", function (e) {
      opts.onUp(e.clientX, e.clientY);
    });
    container.addEventListener("touchstart", function (e) {
      var block = e.target.closest(opts.blockSelector);
      if (!block) return;
      opts.onDown(e.touches[0], block);
      e.preventDefault();
    }, { passive: false });
    document.addEventListener("touchmove", function (e) {
      opts.onMove(e.touches[0].clientX, e.touches[0].clientY);
      e.preventDefault();
    }, { passive: false });
    document.addEventListener("touchend", function (e) {
      opts.onUp(e.changedTouches[0].clientX, e.changedTouches[0].clientY);
    });
    document.addEventListener("touchcancel", function () { opts.onCancel(); });
  }

  // ── Public API surface ──────────────────────────────────────────
  // agenda-core.js calls renderDay/renderWeek/renderMonth and
  // bindDrag/bindWeekDrag/bindMonthDrag. Week + month are attached by
  // the split files. The shared helpers below are exported so the split
  // files can reuse them (parseHHMM, timeToPx, …) — internal contract
  // within the AgendaTimeline namespace.
  return {
    HOUR_H: HOUR_H,
    TOTAL_H: TOTAL_H,
    DRAG_THRESHOLD: DRAG_THRESHOLD,
    SNAP_MIN: SNAP_MIN,
    catColors: catColors,
    catIcons: catIcons,
    parseHHMM: parseHHMM,
    timeToPx: timeToPx,
    pxToTime: pxToTime,
    formatTime: formatTime,
    displayTitle: displayTitle,
    renderHourLabels: renderHourLabels,
    renderBlock: renderBlock,
    renderDay: renderDay,
    bindDrag: bindDrag,
    bindPointerEvents: bindPointerEvents,
    computeNewTimes: computeNewTimes,
    moveDomBlock: moveDomBlock,
    patchSessionTime: patchSessionTime,
    updateFloatTip: updateFloatTip,
  };
})();

console.log("[Timeline] agenda-timeline.js loaded (core + day)");