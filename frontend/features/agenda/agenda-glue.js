/* ============================== AGENDA GLUE (v2 → v3) ============================== */
// Bridges the v2 agenda modules (copied verbatim from studyflow-hub/tierra) to
// v3's runtime. Defines the globals those modules expect (STATE, App.Auth,
// App.Courses, App.ContentBlocks, App.UI.displayTitle) and registers no-op
// stubs for deferred sub-phases (Habits, AgendaTimeline, AgendaMind).
//
// Load order: this file FIRST, then agenda-timer.js, agenda-quicknote.js,
// agenda-habits.js, agenda-dnd.js, agenda-session.js, agenda-calendar.js,
// agenda-core.js, agenda-calendars.js, agenda.js.
//
// v2 modules expect uuid session ids, day_date/week_id on each session, and
// the full-day PATCH contract — v3 backend matches that exactly.

"use strict";

// ─── STATE (v2 modules read this as a bare global) ───────────────
// v3 has App.state with the same shape; we alias it so v2 modules can keep
// writing `STATE.currentWeek = …` without changes. SelectedHabitDay mirrors
// currentDay because habits table and agenda day move together.
(function () {
  if (typeof window.STATE === "undefined") {
    window.STATE = {
      currentWeek: null,
      currentDay: null,
      selectedHabitDay: null,
      activeTab: "agenda",
      habits: { showAll: false, expanded: false, weekData: null, weekDays: null },
    };
  }
})();

// ─── App.Auth (v2 calls App.Auth.authHeaders() + hideDashboard()) ──
window.App = window.App || {};
window.App.Auth = Object.assign(window.App.Auth || {}, {
  // v3 already puts auth headers on every API call; expose the same shape so
  // v2 modules that do raw `fetch(... { headers: { ...authHeaders() } })`
  // still send the bearer token (single-user mode in backend ignores it).
  authHeaders: function () {
    return (window.Auth && typeof window.Auth.getHeaders === "function")
      ? window.Auth.getHeaders()
      : { "Content-Type": "application/json" };
  },
  // v2 uses this when the dashboard side-panel navigates back to the agenda
  // tab. v3 PWA has no dashboard view, so this is a no-op (the tab switch
  // is handled by `_navigateToAgendaDay` itself).
  hideDashboard: function () { /* noop */ },
});

// ─── App.UI.displayTitle (v2 timeline + core call it) ────────────
// Joins icon + title without doubling emojis when the generator already
// prefixed its own icon (e.g. "🧠 Ritual Matinal" + 🧠 = "🧠 Ritual Matinal").
window.App.UI = window.App.UI || {};
if (!window.App.UI.displayTitle) {
  window.App.UI.displayTitle = function (icon, title) {
    var t = title || "Sesión";
    if (!icon) return t;
    // Collapse when the title already starts with the same icon (with or
    // without space) so cards never render "🧠 🧠 Ritual Matinal".
    if (t.indexOf(icon) === 0) return t;
    return icon + " " + t;
  };
}

// ─── App.ContentBlocks._renderMd (minimal safe markdown→html) ─────
// v2 modules call this on every session-card note + overlay preview. We
// don't ship the full marked/dompurify pipeline in v3 sub-phase B, so this
// is a deliberately small safe subset that handles headings, bold, italic,
// code, lists, links, blockquotes, hr. Output is HTML-escaped first so
// user input can't inject scripts.
(function () {
  function _escMd(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }
  function _inline(s) {
    // Process code spans first so their content is not re-processed.
    var codes = [];
    s = s.replace(/`([^`]+)`/g, function (_, c) {
      codes.push("<code>" + _escMd(c) + "</code>");
      return "\u0000" + (codes.length - 1) + "\u0000";
    });
    s = _escMd(s);
    // Bold then italic (order matters).
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/\*([^*]+)\*/g, "<em>$1</em>");
    // Links: [text](url) — only http(s)/mailto safe.
    s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, function (_, t, u) {
      var safe = /^(https?:|mailto:|#)/i.test(u) ? u : "#";
      return '<a href="' + safe + '">' + t + "</a>";
    });
    // Restore code spans.
    s = s.replace(/\u0000(\d+)\u0000/g, function (_, i) { return codes[+i]; });
    return s;
  }
  function _renderMd(md) {
    if (!md) return "";
    var lines = String(md).split("\n");
    var out = [];
    var i = 0;
    while (i < lines.length) {
      var ln = lines[i];
      // Horizontal rule
      if (/^\s*-{3,}\s*$/.test(ln)) { out.push("<hr>"); i++; continue; }
      // Headings
      var h = /^\s*(#{1,3})\s+(.+)$/.exec(ln);
      if (h) { out.push("<h" + h[1].length + ">" + _inline(h[2]) + "</h" + h[1].length + ">"); i++; continue; }
      // Blockquote (consume contiguous)
      if (/^\s*>\s?/.test(ln)) {
        var bq = [];
        while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
          bq.push(lines[i].replace(/^\s*>\s?/, ""));
          i++;
        }
        out.push("<blockquote>" + _inline(bq.join(" ")) + "</blockquote>");
        continue;
      }
      // Unordered list
      if (/^\s*[-*]\s+/.test(ln)) {
        var ul = [];
        while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
          ul.push("<li>" + _inline(lines[i].replace(/^\s*[-*]\s+/, "")) + "</li>");
          i++;
        }
        out.push("<ul>" + ul.join("") + "</ul>");
        continue;
      }
      // Ordered list
      if (/^\s*\d+\.\s+/.test(ln)) {
        var ol = [];
        while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
          ol.push("<li>" + _inline(lines[i].replace(/^\s*\d+\.\s+/, "")) + "</li>");
          i++;
        }
        out.push("<ol>" + ol.join("") + "</ol>");
        continue;
      }
      // Blank line → paragraph break
      if (/^\s*$/.test(ln)) { i++; continue; }
      // Paragraph (consume until blank)
      var p = [ln];
      i++;
      while (i < lines.length && !/^\s*$/.test(lines[i]) &&
             !/^\s*(#{1,3})\s+/.test(lines[i]) &&
             !/^\s*>\s?/.test(lines[i]) &&
             !/^\s*[-*]\s+/.test(lines[i]) &&
             !/^\s*\d+\.\s+/.test(lines[i]) &&
             !/^\s*-{3,}\s*$/.test(lines[i])) {
        p.push(lines[i]); i++;
      }
      out.push("<p>" + _inline(p.join(" ")) + "</p>");
    }
    return out.join("");
  }
  window.App.ContentBlocks = window.App.ContentBlocks || {};
  window.App.ContentBlocks._renderMd = _renderMd;
})();

// ─── App.Courses.applyMarkdown (md toolbar in session overlay) ────
// v2's session overlay wires `onclick="window.App.Courses.applyMarkdown(this,'bold')"`
// etc. on every md toolbar button. The full v2 implementation lives in
// studyflow-hub/tierra/frontend/features/studyflow/courses.js (~50 LOC).
// We embed it here so the overlay works without pulling the 2300-line
// courses.js in sub-phase B (Courses module is ported in a later phase).
(function () {
  function applyMarkdown(btn, type) {
    var ta = btn.closest(".md-toolbar");
    if (ta) ta = ta.parentElement.querySelector("textarea");
    if (!ta) return;
    var start = ta.selectionStart, end = ta.selectionEnd;
    var sel = ta.value.substring(start, end);
    var text = ta.value;
    var nl = start > 0 && text[start - 1] !== "\n" ? "\n" : "";
    var before = "", after = "", placeholder = "", insert, cursor;
    switch (type) {
      case "bold":       before = "**"; after = "**"; placeholder = "texto"; break;
      case "italic":     before = "*";  after = "*";  placeholder = "texto"; break;
      case "code":       before = "`";  after = "`";  placeholder = "código"; break;
      case "link":       before = "[";  after = "](url)"; placeholder = "texto"; break;
      case "h1":         before = nl + "# ";          placeholder = "título"; break;
      case "h2":         before = nl + "## ";         placeholder = "título"; break;
      case "h3":         before = nl + "### ";        placeholder = "título"; break;
      case "ul":         before = nl + "- ";          placeholder = "elemento"; break;
      case "ol":         before = nl + "1. ";         placeholder = "elemento"; break;
      case "blockquote": before = nl + "> ";          placeholder = "cita"; break;
      case "codeblock":  before = nl + "```\n"; after = "\n```"; placeholder = "código"; break;
      case "table":      before = nl + "| Header 1 | Header 2 |\n| --- | --- |\n| Cell 1 | Cell 2 |"; break;
      case "hr":         before = nl + "---"; break;
      default: return;
    }
    if (sel) {
      insert = before + sel + after;
      cursor = start + insert.length;
    } else if (placeholder) {
      insert = before + placeholder + after;
      cursor = start + before.length;
    } else {
      insert = before;
      cursor = start + insert.length;
    }
    ta.value = text.substring(0, start) + insert + text.substring(end);
    ta.focus();
    if (sel) {
      ta.selectionStart = ta.selectionEnd = cursor;
    } else if (placeholder) {
      ta.selectionStart = start + before.length;
      ta.selectionEnd = start + before.length + placeholder.length;
    } else {
      ta.selectionStart = ta.selectionEnd = cursor;
    }
  }
  window.App.Courses = window.App.Courses || {};
  window.App.Courses.applyMarkdown = applyMarkdown;
})();

// ─── Stubs for deferred sub-phases ────────────────────────────────
// agenda-habits.js (sub-phase D) needs App.Habits.{getHabitColumns,
// loadHabitColumns, openColumnsOverlay, closeColumnsOverlay, _habitRowsHtml,
// updateHabitRow, noteOpen}. Until the habits port lands we render an empty
// table so the agenda list view is fully usable.
// agenda-timeline.js (sub-phase C) needs App.AgendaTimeline.{renderDay,
// renderWeek, renderMonth, bindDrag, bindWeekDrag, bindMonthDrag}. The
// ported core.js calls these only when the user picks the timeline view —
// we return empty markup so the page never throws.
window.App.Habits = window.App.Habits || {
  _cols: [],
  getHabitColumns: function () { return window.App.Habits._cols; },
  loadHabitColumns: async function () { return window.App.Habits._cols; },
  openColumnsOverlay: function () { alert("Columnas: pendiente de portar (sub-fase D)"); },
  closeColumnsOverlay: function () {},
  _habitRowsHtml: function () { return ""; },
  updateHabitRow: function () {},
  noteOpen: function () {},
};

window.App.AgendaTimeline = window.App.AgendaTimeline || {
  renderDay: function () { return '<div class="empty-state">📅 Vista día (sub-fase C)</div>'; },
  renderWeek: function () { return '<div class="empty-state">🗓️ Vista semana (sub-fase C)</div>'; },
  renderMonth: function () { return '<div class="empty-state">📅 Vista mes (sub-fase C)</div>'; },
  bindDrag: function () {},
  bindWeekDrag: function () {},
  bindMonthDrag: function () {},
};

console.log("[Glue] agenda-glue.js loaded");
