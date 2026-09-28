/* ============================== SHARED UI UTILITIES ============================== */

// ─── HTML Escaping ──────────────────────────────────────────────────
function escHtml(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&')
    .replace(/</g, '<')
    .replace(/>/g, '>')
    .replace(/"/g, '"')
    .replace(/'/g, ''');
}

// ─── Date / Week Helpers ────────────────────────────────────────────
function todayStr() {
  return new Date().toISOString().slice(0, 10);
}

function getWeekId(dateStr) {
  const date = new Date(dateStr + 'T12:00:00');
  const year = date.getFullYear();
  const week = Math.ceil(((date - new Date(year, 0, 1)) / 86400000 + new Date(year, 0, 1).getDay() + 1) / 7);
  return `${year}-W${String(week).padStart(2, '0')}`;
}

function getISOWeek(dateStr) {
  return getWeekId(dateStr);
}

function getDaysOfWeek(weekId) {
  const [year, week] = weekId.split('-W');
  const weekNum = parseInt(week, 10);
  const firstDayOfYear = new Date(parseInt(year), 0, 1);
  const firstMonday = new Date(firstDayOfYear);
  firstMonday.setDate(firstDayOfYear.getDate() + (firstDayOfYear.getDay() === 0 ? 1 : 8 - firstDayOfYear.getDay()));
  const monday = new Date(firstMonday);
  monday.setDate(firstMonday.getDate() + (weekNum - 1) * 7);
  
  const days = [];
  for (let i = 0; i < 7; i++) {
    const d = new Date(monday);
    d.setDate(monday.getDate() + i);
    days.push(d.toISOString().slice(0, 10));
  }
  return days;
}

function formatDateShort(dateStr) {
  const d = new Date(dateStr + 'T12:00:00');
  return d.toLocaleDateString('es-ES', { day: '2-digit', month: '2-digit' });
}

function formatDateLabel(dateStr) {
  const d = new Date(dateStr + 'T12:00:00');
  const weekdays = ['Dom', 'Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb'];
  return `${weekdays[d.getDay()]} ${d.getDate()}`;
}

// ─── Timer Helpers ──────────────────────────────────────────────────
function formatTimer(seconds) {
  if (!seconds || seconds < 0) return '00:00:00';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
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
  toggle
};

console.log('[Shared] ui-common.js loaded');
