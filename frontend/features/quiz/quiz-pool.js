// frontend/features/quiz/quiz-pool.js
// S5 F3 — the failed pool ("Preguntas Falladas" in v2).
//
// The list, its checkboxes and the practice runner that v2 kept in
// quiz.js:254-326 and :792-851. The data now comes from
// GET /quiz/errors, which joins the question text so a practice session
// can be launched straight from the payload.
//
// Rule that changed: v2 only deleted a pooled error when you got it right
// in *practice*, so a question you aced in a normal test stayed in your
// to-review list. Here answering correctly resolves it from anywhere
// (the server does it in /quiz/answers) and resolution is reversible
// instead of a DELETE.

(function () {
  if (window.App === undefined) window.App = {};
  if (window.App.QuizPool !== undefined) return;

  const _selected = new Set();
  let _scope = "open";

  function _esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  // Pool rows already carry question + options, which is exactly what the
  // runner needs to page and grade a practice session.
  function _asQuestions(rows) {
    return rows.map((r) => ({
      id: r.question_id,
      question: r.question,
      options: r.options || [],
    }));
  }

  function _origin(r) {
    return [r.course_title, r.topic_title, r.block_title]
      .filter(Boolean).join(" › ") || "Sin origen";
  }

  // ── table ────────────────────────────────────────────────────
  function _rowHtml(r) {
    const checked = _selected.has(r.question_id) ? " checked" : "";
    const times = r.wrong_count > 1 ? `<span class="sf-qp-times">×${r.wrong_count}</span>` : "";
    const state = r.is_open
      ? '<span class="sf-qp-badge open">pendiente</span>'
      : '<span class="sf-qp-badge done">resuelta</span>';
    return `<tr data-qid="${r.question_id}" class="${r.is_open ? "" : "resolved"}">
        <td class="sf-qp-check"><input type="checkbox" class="sf-qp-pick" value="${r.question_id}"${checked}></td>
        <td class="sf-qp-q">${_esc(r.question)}</td>
        <td class="sf-qp-origin">${_esc(_origin(r))}</td>
        <td class="sf-qp-count">${times}</td>
        <td class="sf-qp-state">${state}</td>
        <td class="sf-qp-act">
          ${r.is_open
            ? `<button class="sf-btn-ghost sf-qp-resolve" title="Marcar como revisada">✓</button>`
            : `<button class="sf-btn-ghost sf-qp-reopen" title="Volver a pendientes">↩</button>`}
        </td>
      </tr>`;
  }

  function _tableHtml(rows) {
    if (!rows.length) {
      const msg = _scope === "open"
        ? "No tienes preguntas falladas. 🎉"
        : "Nada en esta vista.";
      return `<div class="sf-qp-empty">${msg}</div>`;
    }
    return `<table class="sf-qp-table">
        <thead><tr>
          <th></th><th>Pregunta</th><th>Origen</th>
          <th>Fallos</th><th>Estado</th><th></th>
        </tr></thead>
        <tbody>${rows.map(_rowHtml).join("")}</tbody>
      </table>`;
  }

  function _toolbarHtml(openCount, hasRows) {
    return `<div class="sf-qp-toolbar">
        <span class="sf-qp-count-badge">${openCount} sin repasar</span>
        <button class="sf-btn sf-qp-practice-all" ${hasRows ? "" : "disabled"}>📝 Repasar todas</button>
        <button class="sf-btn sf-qp-practice-sel" disabled>Practicar seleccionadas</button>
        <button class="sf-btn-ghost sf-qp-resolve-sel" disabled>✓ Marcar revisadas</button>
        <button class="sf-btn-ghost sf-qp-clear" ${openCount ? "" : "disabled"}>🗑️ Vaciar</button>
      </div>`;
  }

  // ── practice session ─────────────────────────────────────────
  function _practice(container, rows, courseId) {
    const stage = document.createElement("div");
    stage.className = "sf-qp-stage";
    container.innerHTML = "";
    container.appendChild(stage);

    const back = document.createElement("button");
    back.className = "sf-btn-ghost sf-qp-back";
    back.textContent = "← Volver a falladas";
    back.addEventListener("click", () => render(container, { courseId }));
    stage.appendChild(back);

    const runnerEl = document.createElement("div");
    stage.appendChild(runnerEl);
    window.App.QuizRunner.mount(runnerEl, {
      questions: _asQuestions(rows),
      mode: "practice",
    });
  }

  // ── actions ──────────────────────────────────────────────────
  function _syncSelButtons(container, rows) {
    const n = _selected.size;
    const sel = container.querySelector(".sf-qp-practice-sel");
    const res = container.querySelector(".sf-qp-resolve-sel");
    if (sel) sel.disabled = n === 0;
    if (res) res.disabled = n === 0;
  }

  function _bind(container, rows, courseId) {
    container.querySelectorAll(".sf-qp-pick").forEach((box) => {
      box.addEventListener("change", () => {
        const id = Number(box.value);
        if (box.checked) _selected.add(id); else _selected.delete(id);
        _syncSelButtons(container, rows);
      });
    });

    const practice = (ids) => {
      const subset = rows.filter((r) => ids.includes(r.question_id));
      if (subset.length) _practice(container, subset, courseId);
    };
    const all = container.querySelector(".sf-qp-practice-all");
    if (all) all.addEventListener("click", () => practice(rows.map((r) => r.question_id)));
    const sel = container.querySelector(".sf-qp-practice-sel");
    if (sel) sel.addEventListener("click", () => practice([..._selected]));

    const refresh = () => render(container, { courseId });

    const resolveSel = container.querySelector(".sf-qp-resolve-sel");
    if (resolveSel) {
      resolveSel.addEventListener("click", () => {
        const ids = [..._selected];
        _selected.clear();
        window.App.QuizAPI.resolvePool(ids).then(refresh).catch(() => {});
      });
    }
    container.querySelectorAll(".sf-qp-resolve").forEach((b) => {
      b.addEventListener("click", () => {
        const id = Number(b.closest("tr").dataset.qid);
        window.App.QuizAPI.resolvePool([id]).then(refresh).catch(() => {});
      });
    });
    container.querySelectorAll(".sf-qp-reopen").forEach((b) => {
      b.addEventListener("click", () => {
        const id = Number(b.closest("tr").dataset.qid);
        window.App.QuizAPI.reopenPool([id]).then(refresh).catch(() => {});
      });
    });
    const clear = container.querySelector(".sf-qp-clear");
    if (clear) {
      clear.addEventListener("click", () => {
        if (!confirm("¿Vaciar todo el historial de falladas? Esto no se puede deshacer.")) return;
        window.App.QuizAPI.clearPool().then(() => { _selected.clear(); refresh(); })
          .catch(() => {});
      });
    }
    container.querySelectorAll(".sf-qp-scope").forEach((btn) => {
      btn.addEventListener("click", () => {
        _scope = btn.dataset.scope;
        _selected.clear();
        render(container, { courseId });
      });
    });
  }

  // ── entry point ──────────────────────────────────────────────
  function render(container, opts) {
    if (!container) return;
    const o = opts || {};
    container.innerHTML = '<div class="sf-qp-loading">Cargando falladas…</div>';
    const scopes = [["open", "Sin repasar"], ["resolved", "Resueltas"], ["all", "Todas"]];
    const tabs = scopes.map(([s, label]) => `<button class="sf-qp-scope${s === _scope ? " active" : ""}"
        data-scope="${s}">${label}</button>`).join("");

    window.App.QuizAPI.pool(_scope, { course_id: o.courseId })
      .then((j) => {
        const rows = j.errors || [];
        // Prune the selection against what is actually on screen. _selected
        // is module state, and render() is also reached by switching course,
        // where stale ids would silently make "Practicar seleccionadas" a
        // no-op (it filters the new rows by ids that are not in them).
        const present = new Set(rows.map((r) => r.question_id));
        [..._selected].forEach((id) => { if (!present.has(id)) _selected.delete(id); });
        container.innerHTML = `<div class="sf-qp">
            <div class="sf-qp-head">
              <h3>📊 Preguntas Falladas</h3>
              <div class="sf-qp-tabs">${tabs}</div>
            </div>
            ${_toolbarHtml(j.open_count || 0, rows.length)}
            ${_tableHtml(rows)}
          </div>`;
        _bind(container, rows, o.courseId);
        _syncSelButtons(container, rows);
      })
      .catch((e) => {
        container.innerHTML = `<div class="sf-qp-error">⚠️ ${_esc(e.message || e)}</div>`;
      });
  }

  window.App.QuizPool = { render, _asQuestions, _rowHtml, _tableHtml };
})();
