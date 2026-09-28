// ─── Agenda Month View — center column month grid ─────────────────
// Namespace: window.App.AgendaMonthView
// PORT NOTE (sub-phase B): extracted from the v2 594-line agenda-core.js so
// the day rendering stays readable. Returns the month grid as innerHTML
// and wires the navigation + cell-click + drag handlers. Kept under 30
// lines per function.
//
// Delegates the actual grid drawing to App.AgendaTimeline.renderMonth (a
// stub until sub-phase C lands the real timeline). All other behaviour
// (header, nav, cell-click, drag binding, habits) is here so the agenda
// module is fully usable today.

window.App = window.App || {};
window.App.AgendaMonthView = (function () {
  "use strict";

  var AgendaHabits = window.App.AgendaHabits;
  var AgendaTimeline = window.App.AgendaTimeline;

  var _monthOffset = 0;

  var MONTH_NAMES = [
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
  ];

  // ── Compute the (year, month) the offset resolves to ─────────────
  function _resolveMonth(dateStr, offset) {
    var d = new Date(dateStr + "T12:00:00");
    var y = d.getFullYear();
    var m = d.getMonth() + 1 + offset;
    while (m > 12) { m -= 12; y++; }
    while (m < 1)  { m += 12; y--; }
    return { year: y, month: m };
  }

  // ── Build the H2 + view-toggle row for the month header ─────────
  function _buildHeaderHtml(year, month) {
    var listActive  = _viewMode("list");
    var dayActive   = _viewMode("timeline");
    var weekActive  = _viewMode("week");
    var monthActive = "active"; // current view
    var toggle = '<div class="view-toggle">' +
      '<button class="vt-btn ' + listActive  + '" data-view="list">📋 Lista</button>' +
      '<button class="vt-btn ' + dayActive   + '" data-view="timeline">📅 Día</button>' +
      '<button class="vt-btn ' + weekActive  + '" data-view="week">🗓️ Semana</button>' +
      '<button class="vt-btn ' + monthActive + '" data-view="month">📅 Mes</button>' +
    '</div>';
    return '' +
      '<div style="display:flex;align-items:baseline;justify-content:space-between;margin-bottom:12px;">' +
        '<h2 style="font-size:19px;font-weight:700;">📅 ' + MONTH_NAMES[month - 1] +
        ' <span style="font-weight:400;font-size:13px;color:var(--text-secondary);margin-left:6px;">' + year + '</span></h2>' +
        '<div style="display:flex;gap:6px;align-items:center;">' + toggle + '</div>' +
      '</div>';
  }

  // ── Tiny helper to read view-mode state from AgendaCore ──────────
  function _viewMode(name) {
    return window.App.AgendaCore.getViewMode() === name ? "active" : "";
  }

  // ── Wire the four view-toggle buttons (3 are real transitions; "month"
  // is the current view and is a no-op).
  function _wireViewToggle(el, dateStr, callbacks) {
    el.querySelectorAll(".vt-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var mode = btn.dataset.view;
        if (mode === "month") return;
        _monthOffset = 0;
        window.App.AgendaCore.setViewMode(mode);
        if (callbacks.onRefresh) callbacks.onRefresh();
      });
    });
  }

  // ── Wire the ◀ / ▶ month navigation buttons (delegated) ─────────
  function _wireMonthNav(container, el, dateStr, callbacks) {
    container.querySelectorAll(".tlm-nav").forEach(function (btn) {
      btn.addEventListener("click", function (e) {
        e.stopPropagation();
        var offset = parseInt(btn.dataset.offset, 10);
        if (isNaN(offset)) return;
        _monthOffset += offset;
        render(el, dateStr, callbacks);
      });
    });
  }

  // ── Wire cell-click: month cell → day timeline for that date ────
  function _wireCellClicks(container, dateStr, callbacks) {
    container.querySelectorAll(".tlm-cell:not(.tlm-empty)").forEach(function (cell) {
      cell.addEventListener("click", function (e) {
        if (e.target.closest(".tlm-session")) return; // drag handles blocks
        var date = cell.dataset.date;
        if (!date) return;
        STATE.currentDay = date;
        STATE.selectedHabitDay = date;
        _monthOffset = 0;
        window.App.AgendaCore.setViewMode("timeline");
        if (callbacks.onRefresh) callbacks.onRefresh();
      });
    });
  }

  // ── Fetch month data (delegates to API; uses /agenda/month?date=)
  async function _loadMonthData(year, month) {
    var monthStr = year + "-" + (month < 10 ? "0" + month : month) + "-01";
    try {
      return await API.get("/agenda/month?date=" + monthStr);
    } catch (err) {
      return { year: year, month: month, days: {} };
    }
  }

  // ── Render a placeholder shell before async month data arrives ──
  function _renderLoadingShell(el, year, month) {
    var header = _buildHeaderHtml(year, month);
    el.innerHTML = header +
      '<div id="agenda-month-container">' +
      '<div class="tlm-container" style="padding:24px;text-align:center;color:var(--text-muted);">Cargando...</div>' +
      '</div>';
  }

  // ── Render the month grid into the existing shell ───────────────
  function _renderMonthGrid(el, year, month, monthData, callbacks) {
    var container = el.querySelector("#agenda-month-container");
    if (!container) return;
    var gridHtml = AgendaTimeline.renderMonth(year, month, monthData, {
      onRefresh: function () { render(el, dateStrRef, callbacks); },
    });
    container.innerHTML = gridHtml;
    _wireMonthNav(container, el, dateStrRef, callbacks);
    _wireCellClicks(container, dateStrRef, callbacks);
    AgendaTimeline.bindMonthDrag(container, {
      onRefresh: function () { render(el, dateStrRef, callbacks); },
    });
  }

  // Module-scoped ref so renderMonthGrid's onRefresh closure can re-enter.
  var dateStrRef = "";

  async function render(el, dateStr, callbacks) {
    callbacks = callbacks || {};
    dateStrRef = dateStr;
    var ym = _resolveMonth(dateStr, _monthOffset);
    _renderLoadingShell(el, ym.year, ym.month);
    _wireViewToggle(el, dateStr, callbacks);

    var monthData = await _loadMonthData(ym.year, ym.month);
    _renderMonthGrid(el, ym.year, ym.month, monthData, callbacks);

    // Habits table sits below the month grid (re-renders inside container)
    var habitsContainer = document.createElement("div");
    el.appendChild(habitsContainer);
    habitsContainer.id = "agenda-habits-table";
    AgendaHabits.renderTable({
      onDataChange: function () { render(el, dateStr, callbacks); },
      onDayClick: function () {},
    });
  }

  return { render: render };
})();
