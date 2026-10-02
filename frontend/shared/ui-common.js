/* ============================== SHARED UI UTILITIES ============================== */

// ─── HTML Escaping ──────────────────────────────────────────────────
// Escapes the 5 characters that can break out of an HTML attribute or
// text context. Must use entity NAMES (&amp; / &lt; / &gt; / &quot; /
// &#39;), NOT the raw characters — otherwise user input with `<script>`
// would be inserted verbatim into the DOM.
function escHtml(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// ─── Date / Week Helpers ────────────────────────────────────────────
// Ported verbatim from v2 studyflow-hub/tierra/frontend/shared/ui-common.js
// so the side-panel day-item rendering (`{date,name,num}`) and the week-
// label range ("Semana N · DD/MM–DD/MM") match the v2 reference byte-for-
// byte. The earlier v3 port used a Jan-1 based week number (not ISO 8601)
// and returned raw date strings, which broke d.name/d.num and made
// weekData.days?.[d.date] undefined → "undefined undefined" day items,
// "Invalid Date–Invalid Date" header, and an empty centre column.

function todayStr() {
  return new Date().toISOString().slice(0, 10);
}

function getISOWeek(d) {
  const tmp = new Date(d.valueOf());
  const dayNum = (d.getDay() + 6) % 7;
  tmp.setDate(tmp.getDate() - dayNum + 3);
  const firstThursday = tmp.valueOf();
  tmp.setMonth(0, 1);
  if (tmp.getDay() !== 4) {
    tmp.setMonth(0, 1 + ((4 - tmp.getDay()) + 7) % 7);
  }
  const week = 1 + Math.ceil((firstThursday - tmp) / 604800000);
  const year = new Date(firstThursday).getFullYear();
  return { year, week };
}

function getWeekId(dateStr) {
  const d = dateStr ? new Date(dateStr + 'T12:00:00') : new Date();
  const iso = getISOWeek(d);
  return `${iso.year}-W${String(iso.week).padStart(2, '0')}`;
}

function getDaysOfWeek(weekId) {
  const [year, wn] = weekId.split('-W').map(Number);
  const jan4 = new Date(year, 0, 4, 12, 0, 0);
  const start = new Date(jan4);
  start.setDate(jan4.getDate() - ((jan4.getDay() + 6) % 7));
  start.setDate(start.getDate() + (wn - 1) * 7);
  const days = [];
  const names = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom'];
  for (let i = 0; i < 7; i++) {
    const d = new Date(start);
    d.setDate(start.getDate() + i);
    const ds = d.toISOString().slice(0, 10);
    days.push({ date: ds, name: names[i], num: d.getDate() });
  }
  return days;
}

function formatDateShort(dateStr) {
  const d = new Date(dateStr + 'T12:00:00');
  return `${String(d.getDate()).padStart(2, '0')}/${String(d.getMonth() + 1).padStart(2, '0')}`;
}

function formatDateLabel(dateStr) {
  const d = new Date(dateStr + 'T12:00:00');
  const weekdays = ['Dom', 'Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb'];
  return `${weekdays[d.getDay()]} ${d.getDate()}`;
}

// ─── Timer Helpers ──────────────────────────────────────────────────
function formatTimer(seconds) {
  if (!seconds || seconds < 0) return '00:00:00';
  // Postgres EXTRACT(EPOCH ...) returns fractional seconds, so this is often
  // handed 9.354299999999995 — and `seconds % 60` printed that verbatim as
  // "00:02:9.354299999999995". Floor once, here, so no caller can leak a
  // fraction into the UI no matter which source the number came from.
  const total = Math.floor(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

function _setTimerFields(h, m, s) {
  const he = document.getElementById('so-te-h');
  const me = document.getElementById('so-te-m');
  const se = document.getElementById('so-te-s');
  if (he) he.value = h;
  if (me) me.value = m;
  if (se) se.value = s;
}

function _getTimerSeconds() {
  const h = parseInt(document.getElementById('so-te-h')?.value) || 0;
  const m = parseInt(document.getElementById('so-te-m')?.value) || 0;
  const s = parseInt(document.getElementById('so-te-s')?.value) || 0;
  return h * 3600 + m * 60 + s;
}

function _updateTimerEffective() {
  const ef = document.getElementById('so-tef');
  if (ef) ef.textContent = formatTimer(_getTimerSeconds());
}

// ─── UI Helpers ─────────────────────────────────────────────────────
function showError(id, msg) {
  const el = document.getElementById(id);
  if (el) {
    el.textContent = msg;
    el.style.display = 'block';
  }
}

function clearError(id) {
  const el = document.getElementById(id);
  if (el) el.style.display = 'none';
}

function show(element) {
  if (element) element.classList.remove('hidden');
}

function hide(element) {
  if (element) element.classList.add('hidden');
}

function toggle(element) {
  if (element) element.classList.toggle('hidden');
}

// ── colorAlpha(color, alpha) ─────────────────────────────────────
// Returns `color` at the given alpha, for category colours that arrive
// as `var(--cat-mind)` rather than as a hex value.
//
// This exists because of a real bug, not tidiness. The timeline used to
// build its backgrounds by pasting the alpha onto the end of the colour:
//
//     'background:' + 'var(--cat-mind)' + '15'   →  "var(--cat-mind)15"
//
// That is not valid CSS. The browser *stores* the token stream and then
// throws it away when painting, so `background-color` fell back to its
// initial value, transparent. Every class block rendered with no fill at
// all, which read as "the sessions are gone" when they were in fact all
// there, just unfilled.
//
// The reason it is worth a helper rather than an inline fix: CSS custom
// properties are substituted as opaque token sequences, never re-lexed.
// So `var(--cat-mind)15` can never work, no matter what follows it. The
// only fixes are to resolve the variable to a concrete colour first
// (which needs the computed value at paint time) or to stop using a
// custom property here. Resolving it in JS and emitting rgba() does it
// once, for every timeline view, instead of three times.
function colorAlpha(color, alpha) {
  if (!color) return "transparent";

  // Already a concrete colour: alpha compositing works directly. Named
  // colours and rgb()/hsl() are passed through too, but only after the
  // string is left intact — compositing needs the browser to parse it,
  // and it already knows how.
  if (/^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(color)) {
    var hex = color.slice(1);
    if (hex.length === 3) {
      hex = hex[0] + hex[0] + hex[1] + hex[1] + hex[2] + hex[2];
    }
    var n = parseInt(hex, 16);
    return (
      "rgba(" +
      ((n >> 16) & 255) + ", " +
      ((n >> 8) & 255) + ", " +
      (n & 255) + ", " +
      alpha + ")"
    );
  }

  if (/^rgba?\(/i.test(color) || /^hsla?\(/i.test(color)) {
    return color;
  }

  // A var() reference, a named colour, or anything else we do not parse:
  // let the browser resolve it. color-mix() is the supported way to fade
  // an unresolved colour without having to know what it resolves to.
  return "color-mix(in srgb, " + color + " " + Math.round(alpha * 100) + "%, transparent)";
}

// Export to window.App.UI
window.App = window.App || {};
window.App.UI = {
  escHtml,
  getWeekId,
  getISOWeek,
  getDaysOfWeek,
  todayStr,
  formatTimer,
  _setTimerFields,
  _getTimerSeconds,
  _updateTimerEffective,
  formatDateShort,
  formatDateLabel,
  showError,
  clearError,
  show,
  hide,
  toggle,
  colorAlpha
};

console.log('[Shared] ui-common.js loaded');
