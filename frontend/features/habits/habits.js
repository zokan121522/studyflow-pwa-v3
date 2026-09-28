// ─── Habits module — habit columns, notes, weekly table ───────────
// Namespace: window.App.Habits
// Dependencies: window.API (defined in app.js), window.STATE (set by
// agenda-glue.js), window.App.UI (formatDateLabel, getWeekId, todayStr),
// window.renderAgenda (called after edits to refresh the agenda list).
//
// Sub-phase D port from v2 (studyflow-hub/tierra/frontend/features/habits/
// habits.js). Adapted to v3:
//   • v2 used `window.Auth.authHeaders()` — v3 routes calls through window.API
//     (which auto-attaches headers in app.js), so we drop the headers manual.
//   • v2 read habits columns from a global `STATE.habitColumns`; v3 loads
//     them on demand and exposes them via `getHabitColumns()` for the agenda.
//   • v2 expected `renderAgenda` as a bare global; v3 also exposes it as
//     `window.renderAgenda` (alias defined in agenda.js) — both names work.

window.App = window.App || {};
window.App.Habits = (function () {
  "use strict";

  // ─── State ─────────────────────────────────────────────────────────
  let _habitColumns = [];

  // ─── Getter for live access from agenda-habits.js ─────────────────
  function getHabitColumns() { return _habitColumns; }

  // ─── Load habit columns from API ──────────────────────────────────
  async function loadHabitColumns() {
    try {
      const r = await API.get("/habits/columns");
      _habitColumns = r.columns || [];
      return _habitColumns;
    } catch {
      _habitColumns = [];
      return _habitColumns;
    }
  }

  // ─── Columns overlay ──────────────────────────────────────────────
  function openColumnsOverlay() {
    const el = document.getElementById("columns-overlay");
    if (el) el.classList.add("open");
    renderColumnList();
  }

  function closeColumnsOverlay() {
    const el = document.getElementById("columns-overlay");
    if (el) el.classList.remove("open");
  }

  // ─── Add a column from the overlay form (#clo-name, #clo-type) ──
  async function addColumn() {
    const nameEl = document.getElementById("clo-name");
    const typeEl = document.getElementById("clo-type");
    if (!nameEl) return;
    const label = (nameEl.value || "").trim();
    const type = typeEl ? typeEl.value : "checkbox";
    if (!label) return;
    const key = label.toLowerCase().replace(/\s+/g, "_");
    try {
      await API.post("/habits/columns", { key, label, type });
      nameEl.value = "";
      await renderColumnList();
      if (typeof renderAgenda === "function") renderAgenda();
    } catch (e) {
      alert("Error al añadir columna (puede que ya exista)");
    }
  }

  async function renderColumnList() {
    const el = document.getElementById("clo-list");
    if (!el) return;
    let cols = [];
    try {
      const r = await API.get("/habits/columns");
      cols = r.columns || [];
    } catch {
      cols = [];
    }
    _habitColumns = cols;

    if (!cols.length) {
      el.innerHTML =
        '<div style="color:var(--text-muted);font-size:12px;padding:6px 0;">' +
        'Sin columnas. Añade una desde arriba.</div>';
      return;
    }

    el.innerHTML = cols.map((col) => {
      const typeIcon =
        col.type === "checkbox" ? "☑" :
        col.type === "number"  ? "🔢" :
        col.type === "media"   ? "📊" :
        col.type === "progress"? "📈" : "📝";
      return `
        <div class="co-item" draggable="true" data-col-key="${col.key}">
          <span class="clo-drag-handle">⠿</span>
          <span class="clo-type-badge">${typeIcon}</span>
          <span class="clo-label">${col.label}</span>
          <span class="clo-edit" data-key="${col.key}" title="Editar columna">🖊️</span>
          <span class="clo-del"  data-key="${col.key}" title="Borrar columna">🗑️</span>
        </div>`;
    }).join("");

    // ─── Drag & Drop reorder ────────────────────────────────────
    let _dragSrcKey = null;

    function _onDragStart(e) {
      _dragSrcKey = this.dataset.colKey;
      this.style.opacity = ".4";
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", _dragSrcKey);
    }
    function _onDragOver(e) {
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      this.style.borderColor = "var(--primary)";
      this.style.background = "var(--primary-light)";
    }
    function _onDragLeave() {
      this.style.borderColor = "";
      this.style.background = "";
    }
    function _onDragEnd() {
      this.style.opacity = "";
      el.querySelectorAll(".co-item").forEach((item) => {
        item.style.borderColor = "";
        item.style.background = "";
      });
    }
    async function _onDrop(e) {
      e.preventDefault();
      this.style.borderColor = "";
      this.style.background = "";
      const targetKey = this.dataset.colKey;
      if (!_dragSrcKey || _dragSrcKey === targetKey) return;

      const items = [...el.querySelectorAll(".co-item")];
      const srcIdx = items.findIndex((it) => it.dataset.colKey === _dragSrcKey);
      const tgtIdx = items.findIndex((it) => it.dataset.colKey === targetKey);
      if (srcIdx === -1 || tgtIdx === -1) return;

      if (srcIdx < tgtIdx) {
        items[tgtIdx].insertAdjacentElement("afterend", items[srcIdx]);
      } else {
        items[tgtIdx].insertAdjacentElement("beforebegin", items[srcIdx]);
      }

      const newOrder = [...el.querySelectorAll(".co-item")].map((it) => it.dataset.colKey);
      try {
        await API.put("/habits/columns/reorder", { order: newOrder });
        const refreshed = await API.get("/habits/columns");
        _habitColumns = refreshed.columns || [];
        if (typeof renderAgenda === "function") renderAgenda();
      } catch {
        renderColumnList();
      }
    }

    el.querySelectorAll(".co-item").forEach((item) => {
      item.addEventListener("dragstart", _onDragStart);
      item.addEventListener("dragover", _onDragOver);
      item.addEventListener("dragleave", _onDragLeave);
      item.addEventListener("dragend", _onDragEnd);
      item.addEventListener("drop", _onDrop);
    });

    // ─── Edit handlers ────────────────────────────────────────
    el.querySelectorAll(".clo-edit").forEach((btn) => {
      btn.addEventListener("click", () => {
        const key = btn.dataset.key;
        const item = btn.closest(".co-item");
        if (!item || item.querySelector(".clo-edit-input")) return;

        const labelSpan = item.querySelector(".clo-label");
        const badge = item.querySelector(".clo-type-badge");
        const currentLabel = labelSpan.textContent;
        const badgeText = badge.textContent.trim();
        const currentType =
          badgeText === "☑" ? "checkbox" :
          badgeText === "🔢" ? "number"  :
          badgeText === "📊" ? "media"   :
          badgeText === "📈" ? "progress": "text";

        labelSpan.innerHTML =
          `<input type="text" class="clo-edit-input" value="${currentLabel}">`;
        badge.innerHTML =
          `<select class="clo-edit-type">
            <option value="checkbox" ${currentType === "checkbox"  ? "selected" : ""}>☑ Checkbox</option>
            <option value="text"     ${currentType === "text"      ? "selected" : ""}>📝 Texto</option>
            <option value="number"   ${currentType === "number"    ? "selected" : ""}>🔢 Número</option>
            <option value="media"    ${currentType === "media"     ? "selected" : ""}>📊 Media</option>
            <option value="progress" ${currentType === "progress"  ? "selected" : ""}>📈 %</option>
          </select>`;
        btn.style.display = "none";

        const input = labelSpan.querySelector(".clo-edit-input");
        const typeSel = badge.querySelector(".clo-edit-type");

        async function saveEdit() {
          const newLabel = input.value.trim();
          const newType = typeSel.value;
          if (!newLabel) return;
          try {
            await API.patch(`/habits/columns/${encodeURIComponent(key)}`,
                            { label: newLabel, type: newType });
            renderColumnList();
            if (typeof renderAgenda === "function") renderAgenda();
          } catch {
            alert("Error al guardar");
          }
        }
        input.addEventListener("keydown", (e) => { if (e.key === "Enter") saveEdit(); });
        input.addEventListener("blur", saveEdit);
        typeSel.addEventListener("change", saveEdit);
        input.focus();
        input.select();
      });
    });

    // ─── Delete handlers ───────────────────────────────────────
    el.querySelectorAll(".clo-del").forEach((del) => {
      del.addEventListener("click", async () => {
        const key = del.dataset.key;
        if (!confirm(`¿Borrar columna "${key}" y todos sus datos?`)) return;
        try {
          await API.del(`/habits/columns/${encodeURIComponent(key)}`);
          renderColumnList();
          if (typeof renderAgenda === "function") renderAgenda();
        } catch {
          alert("Error al borrar columna");
        }
      });
    });
  }

  // ─── Per-row note trigger (legacy note overlay, legacy v2 hook) ──
  function noteOpen(trigger) {
    if (!window.STATE) return;
    window.STATE.noteTrigger = trigger;
    const tr = trigger.closest("tr");
    const dayInput = tr?.querySelector(".hab-day-label") || tr?.querySelector('input[type="text"]');
    const date = tr?.dataset.date || "";
    const titleEl = document.getElementById("no-title");
    if (titleEl) {
      titleEl.textContent = dayInput?.value || date || "Nuevo día";
    }
    const currentText = trigger.textContent.includes("✏️")
      ? ""
      : trigger.textContent.replace("...", "");
    const textEl = document.getElementById("no-text");
    if (textEl) textEl.value = currentText;
    document.getElementById("note-overlay")?.classList.add("open");
    setTimeout(() => document.getElementById("no-text")?.focus(), 100);
  }

  function noteClose() {
    document.getElementById("note-overlay")?.classList.remove("open");
    if (window.STATE && window.STATE.noteTrigger) {
      const val = document.getElementById("no-text")?.value.trim() || "";
      window.STATE.noteTrigger.textContent = val
        ? val.substring(0, 18) + (val.length > 18 ? "..." : "")
        : "✏️ Notas";
      const tr = window.STATE.noteTrigger.closest("tr");
      const date = tr?.dataset.date;
      if (date) {
        API.patch(`/habits/${date}/note`, { key: "1", note: val }).catch(() => {});
      }
    }
  }

  // ─── Habits table row HTML (called by agenda-habits.js) ──────────
  function _habitRowsHtml(dataMap, dateList) {
    const cols = _habitColumns;
    if (!cols.length) {
      return `<tr><td colspan="${cols.length + 4}" style="text-align:center;padding:30px 10px;color:var(--text-muted);font-size:13px;">
        📋 No hay columnas de hábitos. Añade una desde el botón <strong>📋</strong> del panel de hábitos.
      </td></tr>`;
    }

    let html = "";
    dateList.forEach((dateStr, idx) => {
      const dayData = dataMap[dateStr] || {};
      const habits = dayData.habits || {};
      const notes  = dayData.notes  || {};

      // Average of number-typed cells in the row
      const numCols = cols.filter((c) => c.type === "number");
      const numVals = numCols
        .map((c) => parseFloat(habits[c.key]))
        .filter((v) => !isNaN(v));
      const avg = numVals.length
        ? Math.round(numVals.reduce((a, b) => a + b, 0) / numVals.length)
        : "—";

      let cells = "";
      cols.forEach((col) => {
        const val = habits[col.key];
        if (col.type === "checkbox") {
          const ch = val ? "checked" : "";
          cells += `<td><input type="checkbox" data-date="${dateStr}" data-key="${col.key}" ${ch}></td>`;
        } else if (col.type === "number") {
          const v = val !== undefined && val !== "" ? val : "";
          cells += `<td><input type="number" value="${v}" data-date="${dateStr}" data-key="${col.key}" class="hab-num"></td>`;
        } else if (col.type === "media") {
          cells += `<td class="hab-media">${avg}</td>`;
        } else if (col.type === "progress") {
          // rendered separately as pctCell
        } else {
          const v = val || "";
          cells += `<td><input type="text" value="${v}" data-date="${dateStr}" data-key="${col.key}" class="hab-text"></td>`;
        }
      });

      // Progress bar if any column is of type "progress"
      const hasProgress = cols.some((c) => c.type === "progress");
      const noteKey = cols[0]?.key;
      const noteText = (noteKey && notes[noteKey]) || "";
      const noteDisplay = noteText
        ? noteText.substring(0, 18) + (noteText.length > 18 ? "..." : "")
        : "✏️ Notas";

      let pctCell = "";
      if (hasProgress) {
        const checked = cols.filter((c) => c.type === "checkbox" && habits[c.key]).length;
        const total   = cols.filter((c) => c.type === "checkbox").length;
        const pct = total > 0 ? Math.round((checked / total) * 100) : 0;
        pctCell =
          `<td class="hab-progress-cell">` +
          `<div class="pbar"><div class="fill" style="width:${pct}%"></div></div>` +
          `<span class="pct-label">${pct}%</span>` +
          `</td>`;
      }

      // dateStr is a v3-style YYYY-MM-DD (string) — formatDateLabel lives in
      // window.App.UI (defined by shared/ui-common.js + agenda-glue.js).
      const fmt = (window.App && window.App.UI && window.App.UI.formatDateLabel)
        ? window.App.UI.formatDateLabel
        : (s) => s;

      html += `<tr data-date="${dateStr}">
        <td class="hab-idx">${idx + 1}</td>
        <td class="hab-day-cell">
          <input type="text" value="${fmt(dateStr)}" data-date="${dateStr}" class="hab-day-label">
        </td>
        ${cells}
        ${pctCell}
        <td><span class="note-trigger" data-date="${dateStr}">${noteDisplay}</span></td>
      </tr>`;
    });
    return html;
  }

  // ─── Update progress bar in habit row after a checkbox toggle ───
  function updateHabitRow(cb) {
    const tr = cb.closest("tr");
    if (!tr) return;
    const all = tr.querySelectorAll('input[type="checkbox"]');
    const ch  = tr.querySelectorAll('input[type="checkbox"]:checked');
    const pct = all.length ? Math.round((ch.length / all.length) * 100) : 0;
    const fill = tr.querySelector(".fill");
    const label = tr.querySelector(".pct-label");
    if (fill) {
      fill.style.width = pct + "%";
      fill.style.background = pct >= 50 ? "var(--green)" : "var(--amber)";
    }
    if (label) label.textContent = pct + "%";
  }

  // ─── Public API ──────────────────────────────────────────────────
  return {
    getHabitColumns,
    loadHabitColumns,
    openColumnsOverlay,
    closeColumnsOverlay,
    addColumn,
    renderColumnList,
    noteOpen,
    noteClose,
    _habitRowsHtml,
    updateHabitRow,
  };
})();

console.log("[Habits] module loaded");