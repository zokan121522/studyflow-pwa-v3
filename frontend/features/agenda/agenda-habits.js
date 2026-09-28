// ─── Agenda Habits module — habits table + notes rendering ──────────
// Namespace: window.App.AgendaHabits
// Dependencies: API (global), STATE (global), window.App.UI, window.App.Habits

window.App = window.App || {};
window.App.AgendaHabits = (function () {
  "use strict";

  const { getWeekId, todayStr } = window.App.UI;

  // ─── Render habit notes (below habits table, center column) ──────
  async function renderNotes() {
    const container = document.getElementById("agenda-habit-notes");
    if (!container) return;

    const weekId = STATE.currentWeek || getWeekId(todayStr());
    const noteDay = STATE.selectedHabitDay || STATE.currentDay || todayStr();

    let weekData;
    try {
      weekData = await API.get(`/habits/week/${weekId}`);
    } catch {
      container.innerHTML = "";
      return;
    }

    await App.Habits.loadHabitColumns();

    const dayData = weekData.days?.[noteDay];
    if (!dayData || !App.Habits.getHabitColumns().length) {
      container.innerHTML = "";
      return;
    }

    const dayNotes = dayData.notes || {};
    const d = new Date(noteDay + "T12:00:00");
    const dayName = d.toLocaleDateString("es-ES", { weekday: "long", day: "numeric", month: "long" });
    const dayNameCapitalized = dayName.charAt(0).toUpperCase() + dayName.slice(1);

    const hasNotes = App.Habits.getHabitColumns().some((col) => dayNotes[col.key]?.trim());

    let html = `
      <div class="hn-section">
        <div class="hn-head">
          <div class="hn-head-left">
            <span class="hn-icon">📝</span>
            <span class="hn-title">Notas por hábito</span>
          </div>
          <span class="hn-day-badge">${dayNameCapitalized}</span>
        </div>
        <div class="hn-body">
          ${App.Habits.getHabitColumns().map((col) => {
            const note = dayNotes[col.key] || "";
            return `
              <div class="hn-row">
                <div class="hn-row-head">
                  <span class="hn-row-label">${col.label}</span>
                  ${note.trim() ? '<span class="hn-row-dot" title="Tiene notas">●</span>' : ""}
                </div>
                <textarea class="hn-ta" data-date="${noteDay}" data-key="${col.key}"
                  placeholder="Escribe notas para «${col.label}»…"
                  rows="2">${note}</textarea>
              </div>`;
          }).join("")}
        </div>
        ${!hasNotes ? `<div class="hn-empty">💡 Haz clic en un día para añadir notas a tus hábitos</div>` : ""}
      </div>`;

    container.innerHTML = html;

    // Auto-save: on blur (instant) + on input (debounced, robust to overlay close)
    container.querySelectorAll(".hn-ta").forEach((ta) => {
      let debounceTimer = null;
      const persist = async () => {
        const date = ta.dataset.date;
        const key = ta.dataset.key;
        const note = ta.value;
        try {
          await API.patch(`/habits/${date}/note`, { key, note });
          const row = ta.closest(".hn-row");
          const dot = row?.querySelector(".hn-row-dot");
          if (dot) {
            dot.style.display = note.trim() ? "inline" : "none";
          } else if (note.trim() && row) {
            const head = row.querySelector(".hn-row-head");
            const newDot = document.createElement("span");
            newDot.className = "hn-row-dot";
            newDot.title = "Tiene notas";
            newDot.textContent = "●";
            head?.appendChild(newDot);
          }
        } catch (e) {
          console.error("Error al guardar nota:", e);
        }
      };
      // Debounced auto-save while typing
      ta.addEventListener("input", () => {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(persist, 600);
      });
      // Instant save when leaving the field
      ta.addEventListener("blur", persist);
    });
  }

  // ─── Render habits tracking table (center column) ────────────────
  async function renderTable(callbacks = {}) {
    const { onDataChange, onDayClick } = callbacks;
    const weekId = STATE.currentWeek || getWeekId(todayStr());
    const days = getDaysOfWeek(weekId);
    const el = document.getElementById("agenda-habits-table");
    if (!el) return;

    let weekData;
    try {
      weekData = await API.get(`/habits/week/${weekId}`);
    } catch {
      weekData = { week_id: weekId, days: {} };
    }

    await App.Habits.loadHabitColumns();
    const dateList = days.map((d) => d.date);
    const rows = App.Habits._habitRowsHtml(weekData.days || {}, dateList);

    el.innerHTML = `
      <div style="margin-top:16px;display:flex;align-items:baseline;justify-content:space-between;">
        <div class="section-label">📋 Seguimiento Diario</div>
        <span style="font-size:11px;color:var(--text-muted);background:var(--surface);padding:2px 10px;border-radius:10px;border:1px solid var(--border);">Semana ${weekId.split("-W")[1]}</span>
      </div>
      <div class="ht-toolbar">
        <span class="ht-label">📋 Registro</span>
        <button class="ht-btn" id="agh-columns-btn" style="font-size:10px;">📋 Columnas</button>
        <button class="ht-btn" id="agh-notes-btn" style="font-size:10px;">📝 Notas</button>
        <span style="margin-left:auto;font-size:9px;color:var(--text-muted);">Hábitos editables — clic para marcar</span>
      </div>
      <div class="ht-wrap">
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th style="text-align:left;padding-left:8px;">Día</th>
              ${App.Habits.getHabitColumns().filter((col) => col.type !== "progress").map((col) =>
                `<th>${col.label.split(' ')[0] || '—'}<br><span style="font-weight:400;">${col.label.split(' ').slice(1).join(' ') || col.label}</span></th>`
              ).join("")}
              ${App.Habits.getHabitColumns().some((c) => c.type === "progress") ? '<th>📊 %</th>' : ''}
              <th>📝 Notas</th>
            </tr>
          </thead>
          <tbody id="agh-body">${rows}</tbody>
        </table>
      </div>
      <div style="margin-top:8px;font-size:11px;color:var(--text-secondary);">
        <span>💡 <strong>Consejo:</strong> Los hábitos se guardan automáticamente al marcar.</span>
      </div>`;

    _attachHandlers(weekData, days);

    document.getElementById("agh-columns-btn").addEventListener("click", () => {
      App.Habits.openColumnsOverlay();
    });
    document.getElementById("agh-notes-btn").addEventListener("click", () => {
      document.getElementById("notes-habit-overlay").classList.add("open");
      renderNotes();
    });
  }

  // ─── Attach event handlers for habits table ──────────────────────
  function _attachHandlers(weekData, days) {
    const el = document.getElementById("agenda-habits-table");
    if (!el) return;
    // Checkbox change
    el.querySelectorAll('input[type="checkbox"]').forEach((cb) => {
      cb.addEventListener("change", async () => {
        const date = cb.dataset.date;
        const key = cb.dataset.key;
        const checked = cb.checked;
        if (date && key) {
          try {
            await API.patch(`/habits/${date}/habit`, { key, checked });
            App.Habits.updateHabitRow(cb);
            if (onDataChange) onDataChange();
          } catch {}
        }
      });
    });
    // Text / number input blur
    el.querySelectorAll('input[type="text"].hab-text, input[type="number"].hab-num').forEach((inp) => {
      inp.addEventListener("change", async () => {
        const date = inp.dataset.date;
        const key = inp.dataset.key;
        const val = inp.value;
        if (date && key) {
          try {
            await API.patch(`/habits/${date}/cell`, { key, value: val });
            if (onDataChange) onDataChange();
          } catch {}
        }
      });
    });
    // Note triggers
    el.querySelectorAll(".note-trigger").forEach((t) => {
      t.addEventListener("click", () => App.Habits.noteOpen(t));
    });
    // Day label click → update habit notes
    el.querySelectorAll(".hab-day-label").forEach((inp) => {
      inp.addEventListener("click", () => {
        const tr = inp.closest("tr");
        const date = tr?.dataset.date;
        if (date) {
          STATE.selectedHabitDay = date;
          renderNotes();
          if (onDayClick) onDayClick(date);
        }
      });
    });
  }

  // ─── Public API ──────────────────────────────────────────────────
  return {
    renderNotes,
    renderTable,
  };
})();
