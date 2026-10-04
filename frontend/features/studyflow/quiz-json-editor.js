// frontend/features/studyflow/quiz-json-editor.js
// R5 — the exercise questions as JSON, edited as JSON.
//
// The CRUD this replaces was four chained prompt() dialogs per question
// (question → options → correct index → explanation). It could not show what
// the test looked like, and a typo in the options silently produced a
// question with the wrong answer key.
//
// Shape, one question per entry — `correct` is 0-based, which is what the
// NotebookLM generator emits and what _prepare_bulk_rows accepts:
//
//   [{
//     "question": "¿Qué devuelve this?",
//     "options": ["undefined", "null", "this", "{}"],
//     "correct": 2,
//     "explanation": "this dentro de una función depende de la llamada."
//   }]
//
// The editor deliberately does NOT mount App.QuizRunner for the preview.
// QuizRunner is an interactive session whose "Corregir" button POSTs
// question_id to /quiz/answers: with preview ids those ids do not exist, and
// with real ids a stray click would write a bogus page into quiz_results and
// push a question into the failed pool. An editor needs a read-only preview
// that shows the answer key — which the study route hides on purpose — so
// the preview is rendered here instead.

(function () {
  if (window.App === undefined) window.App = {};
  if (window.App.QuizJsonEditor !== undefined) return;

  const LETTERS = "ABCDEFGH";
  const DEBOUNCE_MS = 250;

  function _esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  // ── serialisation ────────────────────────────────────────────

  // `id` is dropped on purpose. A save goes through bulk-replace, which
  // deletes the block's rows and re-inserts them, so every id is reassigned
  // by the sequence. Leaving the old ids in the text would imply they are
  // stable and that re-saving preserves them.
  function toJson(questions) {
    const arr = (questions || []).map((q) => {
      const out = {
        question: q.question == null ? "" : q.question,
        options: (q.options || []).map((o) => String(o)),
        // Passed through untouched rather than coerced: a value this cannot
        // read has to surface in validate(), not be silently rewritten to 0.
        correct: q.correct_answer != null ? q.correct_answer : q.correct,
      };
      if (q.explanation) out.explanation = q.explanation;
      if (q.difficulty && q.difficulty !== "medium") out.difficulty = q.difficulty;
      return out;
    });
    return JSON.stringify(arr, null, 2);
  }

  // ── validation ───────────────────────────────────────────────

  // Mirrors _prepare_bulk_rows in backend/routes/quiz.py rule for rule, so
  // the editor never spends a round trip on a payload the server will reject.
  function validate(questions) {
    if (!Array.isArray(questions)) {
      return [{ index: null, error: "la raíz debe ser una lista [...]" }];
    }
    if (!questions.length) {
      return [{ index: null, error: "la lista está vacía" }];
    }
    const errors = [];
    questions.forEach((q, i) => {
      if (!q || typeof q !== "object" || Array.isArray(q)) {
        errors.push({ index: i, error: "no es un objeto" });
        return;
      }
      _validateOne(q, i, errors);
    });
    return errors;
  }

  function _validateOne(q, i, errors) {
    const text = typeof q.question === "string" ? q.question.trim() : "";
    if (!text) errors.push({ index: i, error: "falta question" });

    const opts = q.options;
    if (!Array.isArray(opts)) {
      errors.push({ index: i, error: "falta options (lista de textos)" });
    } else if (opts.length < 2) {
      errors.push({ index: i, error: "options necesita al menos 2" });
    } else if (opts.some((o) => (o == null || String(o).trim() === ""))) {
      errors.push({ index: i, error: "options tiene una entrada vacía" });
    }
    _validateCorrect(q, i, errors);
  }

  function _validateCorrect(q, i, errors) {
    const raw = q.correct_answer != null ? q.correct_answer : q.correct;
    // Number(null) and Number("") are both 0, which would turn a missing
    // answer key into "A is correct". The empties are rejected first.
    if (raw == null || raw === "") {
      errors.push({ index: i, error: "falta correct" });
      return;
    }
    // JSON booleans are not indices. The backend would happily take
    // int(True) == 1 and store "option B is correct" from `correct: true`,
    // which is never what anyone meant to write.
    if (typeof raw === "boolean") {
      errors.push({ index: i, error: "correct debe ser un entero, no true/false" });
      return;
    }
    const n = Number(raw);
    if (!Number.isInteger(n)) {
      errors.push({ index: i, error: `correct debe ser un entero (${_esc(raw)})` });
      return;
    }
    const total = Array.isArray(q.options) ? q.options.length : 0;
    if (n < 0 || n >= total) {
      errors.push({
        index: i,
        error: `correct ${n} fuera de rango (0-${Math.max(total - 1, 0)})`,
      });
    }
  }

  // ── parse ───────────────────────────────────────────────────

  // `questions` is the parsed value whenever the JSON itself is well formed,
  // so the preview can still draw the good entries while `errors` explains
  // what is wrong. Callers must treat `errors.length > 0` as "do not save".
  function parse(text) {
    const raw = String(text == null ? "" : text).trim();
    if (!raw) return { questions: null, errors: [{ index: null, error: "vacío" }] };
    let data;
    try {
      data = JSON.parse(raw);
    } catch (e) {
      return { questions: null, errors: [{ index: null, error: "JSON inválido: " + (e.message || e) }] };
    }
    return { questions: data, errors: validate(data) };
  }

  // ── read-only preview ───────────────────────────────────────

  function previewHtml(questions) {
    if (!Array.isArray(questions)) {
      return '<div class="sf-qje-error">Corrige el JSON para ver la vista previa.</div>';
    }
    if (!questions.length) return '<div class="sf-qje-hint">Sin preguntas.</div>';
    return questions.map((q, i) => _previewCard(q, i)).join("");
  }

  function _previewCard(q, i) {
    if (!q || typeof q !== "object" || Array.isArray(q)) {
      return `<div class="sf-qje-card"><div class="sf-qje-error">#${i + 1}: no es un objeto</div></div>`;
    }
    const raw = q.correct_answer != null ? q.correct_answer : q.correct;
    const correct = Number.isInteger(Number(raw)) && raw != null && raw !== ""
      ? Number(raw) : -1;
    return `<div class="sf-qje-card">
        <div class="sf-qje-n">${i + 1}</div>
        <div class="sf-qje-q">${_esc(q.question) || '<i>sin pregunta</i>'}</div>
        <div class="sf-qje-opts">${_previewOpts(q.options, correct)}</div>
        ${q.explanation ? `<div class="sf-qje-exp">💡 ${_esc(q.explanation)}</div>` : ""}
      </div>`;
  }

  function _previewOpts(options, correct) {
    if (!Array.isArray(options)) {
      return '<div class="sf-qje-error">options no es una lista</div>';
    }
    return options.map((o, i) => {
      const cls = i === correct ? "sf-qje-opt correct" : "sf-qje-opt";
      return `<div class="${cls}">
          <span class="sf-qje-letter">${LETTERS[i] || "?"}</span>
          <span class="sf-qje-opt-text">${_esc(o)}</span>
          ${i === correct ? '<span class="sf-qje-key">correcta</span>' : ""}
        </div>`;
    }).join("");
  }

  function errorsHtml(errors) {
    if (!errors || !errors.length) return "";
    const items = errors.map((e) => `<li>${e.index == null ? "" : `#${e.index + 1}: `}${_esc(e.error)}</li>`).join("");
    return `<div class="sf-qje-errors"><strong>${errors.length}</strong> problema(s):<ul>${items}</ul></div>`;
  }

  // ── DOM ─────────────────────────────────────────────────────

  function _skeleton() {
    return `<div class="sf-qje">
        <div class="sf-qje-head">
          <span>✏️ Preguntas en JSON</span>
          <span class="sf-qje-count"></span>
        </div>
        <div class="sf-qje-panes">
          <div class="sf-qje-pane">
            <label class="sf-qje-label">JSON <span class="sf-qje-hint">(correct es 0-based: 0 = A)</span></label>
            <textarea class="sf-qje-json" spellcheck="false"
              placeholder='[{"question":"…","options":["…","…"],"correct":0}]'></textarea>
            <div class="sf-qje-actions">
              <button class="sf-btn sf-qje-save">💾 Guardar</button>
              <button class="sf-btn-ghost sf-qje-reload">↺ Recargar</button>
              <span class="sf-qje-status"></span>
            </div>
          </div>
          <div class="sf-qje-pane">
            <label class="sf-qje-label">Vista previa</label>
            <div class="sf-qje-preview"></div>
          </div>
        </div>
      </div>`;
  }

  function _paneRefs(root) {
    return {
      json: root.querySelector(".sf-qje-json"),
      preview: root.querySelector(".sf-qje-preview"),
      status: root.querySelector(".sf-qje-status"),
      save: root.querySelector(".sf-qje-save"),
      reload: root.querySelector(".sf-qje-reload"),
      count: root.querySelector(".sf-qje-count"),
    };
  }

  function _refreshPreview(refs, text) {
    const { questions, errors } = parse(text);
    refs.preview.innerHTML = errorsHtml(errors) + previewHtml(questions);
    const ok = errors.length === 0;
    refs.save.disabled = !ok;
    refs.save.title = ok ? "" : "Corrige los errores antes de guardar";
    if (refs.count) {
      const total = Array.isArray(questions) ? questions.length : 0;
      refs.count.textContent = ok
        ? `${total} pregunta${total === 1 ? "" : "s"}`
        : `${total} entrada(s), ${errors.length} error(es)`;
    }
    return ok;
  }

  function _status(refs, msg, bad) {
    refs.status.textContent = msg || "";
    refs.status.classList.toggle("bad", !!bad);
  }

  async function _load(refs, opts) {
    refs.preview.innerHTML = '<div class="sf-qje-hint">Cargando…</div>';
    try {
      const qs = await window.App.QuizAPI.questionsForManage(opts.blockId);
      refs.json.value = toJson(qs);
      _refreshPreview(refs, refs.json.value);
      _status(refs, "");
    } catch (e) {
      refs.preview.innerHTML = `<div class="sf-qje-error">⚠️ ${_esc(e.message || e)}</div>`;
    }
  }

  async function _save(refs, opts) {
    const { questions, errors } = parse(refs.json.value);
    if (errors.length) {
      _refreshPreview(refs, refs.json.value);
      _status(refs, "No guardado: corrige los errores.", true);
      return;
    }
    refs.save.disabled = true;
    _status(refs, "Guardando…");
    try {
      const res = await window.App.QuizAPI.bulkReplace(
        opts.blockId, opts.courseId, opts.topicId, questions);
      const bad = (res && res.invalid) || [];
      if (bad.length) {
        _status(refs, `Guardado con ${bad.length} pregunta(s) descartada(s).`, true);
        refs.preview.innerHTML = errorsHtml(bad);
      } else {
        _status(refs, "✅ Guardado");
      }
      if (opts.onSaved) opts.onSaved();
    } catch (e) {
      _status(refs, "❌ " + (e.message || e), true);
      refs.save.disabled = false;
    }
  }

  function _wire(root, refs, opts) {
    let timer = null;
    refs.json.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(() => _refreshPreview(refs, refs.json.value), DEBOUNCE_MS);
    });
    // A pending debounce would otherwise repaint over a just-saved preview.
    refs.save.addEventListener("click", () => {
      clearTimeout(timer);
      _save(refs, opts);
    });
    refs.reload.addEventListener("click", () => {
      clearTimeout(timer);
      _load(refs, opts);
    });
  }

  async function mount(host, opts) {
    if (!host) return null;
    host.innerHTML = _skeleton();
    const refs = _paneRefs(host);
    _wire(host, refs, opts);
    await _load(refs, opts);
    return refs;
  }

  window.App.QuizJsonEditor = {
    mount, toJson, parse, validate, previewHtml,
    _skeleton, _refreshPreview,
  };
})();