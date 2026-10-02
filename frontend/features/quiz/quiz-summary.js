// frontend/features/quiz/quiz-summary.js
// S5 F4 — "Resumen por Card" (v2 quiz.js:169-249) on v3's normalised keys.
//
// v2 aggregated a counters table per "Course > Block" text slug, so renaming
// a block orphaned the row and re-attempting a test inflated the totals
// (cumulative UPSERT). This reads GET /quiz/stats, which aggregates on the
// fly from quiz_results and groups by block_id — the same numbers without
// a table that can drift from the log.
//
// The module also answers the forward-declared S5 hook in courses.js
// (`App.QuizStats.render`) that courses.js:594-607 was already calling
// behind Addons.isEnabled("quiz").

(function () {
  if (window.App === undefined) window.App = {};
  if (window.App.QuizSummary !== undefined) return;

  function _esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  // v2 painted a colour band per topic: green ≥80, amber ≥50, red below.
  function _band(pct) {
    if (pct >= 80) return "good";
    if (pct >= 50) return "mid";
    return "bad";
  }

  function _kpiHtml(s) {
    const cards = [
      ["Respondidas", s.total_answered || 0, ""],
      ["Aciertos", s.correct || 0, "ok"],
      ["Fallos", s.ko || 0, "bad"],
      ["Precisión", `${s.accuracy || 0}%`, ""],
      ["Sin repasar", s.open_errors || 0, (s.open_errors ? "bad" : "ok")],
    ];
    return `<div class="sf-qsm-kpis">${cards.map(([label, value, cls]) => `
        <div class="sf-qsm-kpi ${cls}">
          <span class="sf-qsm-kpi-val">${_esc(value)}</span>
          <span class="sf-qsm-kpi-label">${label}</span>
        </div>`).join("")}</div>`;
  }

  function _label(r) {
    return [r.course_title, r.topic_title, r.block_title]
      .filter(Boolean).join(" › ") || `Bloque ${r.block_id}`;
  }

  function _rowHtml(r) {
    const pct = r.accuracy || 0;
    const pending = r.open_errors || 0;
    return `<tr data-block-id="${r.block_id}" class="sf-qsm-${_band(pct)}">
        <td class="sf-qsm-name">${_esc(_label(r))}</td>
        <td class="sf-qsm-num">${r.total || 0}</td>
        <td class="sf-qsm-num ok">${r.ok || 0}</td>
        <td class="sf-qsm-num bad">${r.ko || 0}</td>
        <td class="sf-qsm-pct">
          <div class="sf-qsm-bar"><div class="sf-qsm-bar-fill" style="width:${pct}%"></div></div>
          <span>${pct}%</span>
        </td>
        <td class="sf-qsm-pending">${pending
          ? `<span class="sf-qsm-badge">${pending} sin repasar</span>` : "—"}</td>
      </tr>`;
  }

  function _tableHtml(rows) {
    if (!rows.length) {
      return '<div class="sf-qsm-empty">Todavía no hay tests respondidos. '
        + 'Responde un bloque <code>exercise</code> y aparecerá aquí.</div>';
    }
    return `<table class="sf-qsm-table">
        <thead><tr>
          <th>Bloque</th><th>Total</th><th>✅</th><th>❌</th>
          <th>Precisión</th><th>Pendientes</th>
        </tr></thead>
        <tbody>${rows.map(_rowHtml).join("")}</tbody>
      </table>`;
  }

  // Full center-view tab.
  function render(container, opts) {
    if (!container) return;
    const o = opts || {};
    container.innerHTML = '<div class="sf-qsm-loading">Calculando resumen…</div>';
    window.App.QuizAPI.stats(o.courseId)
      .then((s) => {
        container.innerHTML = `<div class="sf-qsm">
            <div class="sf-qsm-head"><h3>📈 Resumen por Card</h3></div>
            ${_kpiHtml(s)}
            ${_tableHtml(s.by_block || [])}
          </div>`;
      })
      .catch((e) => {
        container.innerHTML = `<div class="sf-qsm-error">⚠️ ${_esc(e.message || e)}</div>`;
      });
  }

  // courses.js _renderAddonSections hook: APPENDS a compact strip to the
  // topic detail, it must not replace the panel (the center view is owned
  // by the topic renderer at that point).
  // Ids cross the boundary as both numbers (JSON) and strings (data-*), and
  // courses.js already normalises for the same reason (_sameId). Strict === here
  // made the strip silently vanish whenever the topic came from a click
  // handler instead of a fetched list.
  function _sameId(a, b) {
    if (a == null || b == null) return false;
    return String(a) === String(b);
  }

  function renderInline(centerEl, courseId, topicId) {
    if (!centerEl) return;
    window.App.QuizAPI.stats(courseId)
      .then((s) => {
        const mine = (s.by_block || []).filter((r) => _sameId(r.topic_id, topicId));
        const total = mine.reduce((a, r) => a + (r.total || 0), 0);
        const ok = mine.reduce((a, r) => a + (r.ok || 0), 0);
        const pending = mine.reduce((a, r) => a + (r.open_errors || 0), 0);
        if (!total && !pending) return;
        const pct = total ? Math.round((ok / total) * 100) : 0;
        const strip = document.createElement("div");
        strip.className = "sf-qsm-inline";
        strip.innerHTML = `<span class="sf-qsm-inline-title">📊 Test de este tema</span>
            <span>${ok}/${total} correctas</span>
            <span class="sf-qsm-inline-pct">${pct}%</span>
            ${pending ? `<span class="sf-qsm-badge">${pending} sin repasar</span>` : ""}`;
        centerEl.appendChild(strip);
      })
      .catch(() => {});
  }

  window.App.QuizSummary = {
    render, renderInline, _sameId, _band, _kpiHtml, _rowHtml, _tableHtml,
  };
  // Name courses.js:594-607 already looks for.
  window.App.QuizStats = { render: renderInline };
})();
