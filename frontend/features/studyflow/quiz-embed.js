// frontend/features/studyflow/quiz-embed.js
// Issue #10 — quiz por bloque (parity v2): renders the test of an
// `exercise` block embedded inside the block body (preguntas + opciones
// + corrección), with create/edit/delete for the block owner.
//
// Contract:
//   window.App.QuizEmbed = {
//     mountBins(centerEl)      — mount every .sf-quiz-bin in the panel
//     _renderBin(bin)          — async: fetch block questions, render
//     _askAddQuestion(bin)     — prompt-driven question creation
//   }
//
// The block renderer (courses-blocks.js) emits a placeholder container:
//   <div class="sf-quiz-bin" data-block-id="N" data-course-id="M"
//        data-topic-id="K"></div>
// This module fills it after the center panel is painted (the same
// pattern PdfViewer uses), so the UI never blocks on the API.

(function () {
  if (window.App === undefined) window.App = {};
  if (window.App.QuizEmbed !== undefined) return;

  const API = window.App.CoursesAPI;
  const __auth = () => (window.App.Auth && window.App.Auth.authHeaders)
    ? window.App.Auth.authHeaders()
    : {};

  // Exercise blocks carry the stem in `content` (markdown) — v2 parity:
  // the stem is rendered via the shared markdown engine, the questions
  // come from quiz_questions.block_id.
  function _md(s) {
    if (!s) return "";
    try {
      if (window.App.ContentBlocks && window.App.ContentBlocks._renderMd) {
        return window.App.ContentBlocks._renderMd(s);
      }
    } catch (_) { /* fall back to plain text */ }
    return String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  }

  function _esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  // ── fetch questions for a block ────────────────────────────────
  async function _fetchQuestions(blockId) {
    const base = (window.API_URL || "/api");
    const r = await fetch(`${base}/quiz/questions?block_id=${blockId}`, {
      headers: __auth(),
    });
    if (!r.ok) throw new Error("quiz/questions " + r.status);
    const j = await r.json();
    return j.questions || [];
  }

  function _questionCardHtml(q, idx, blockId) {
    const opts = (q.options || []).map((o, i) => `
        <label class="sf-q-opt" data-qid="${q.id}" data-opt="${i}">
          <input type="radio" name="sf-q-${q.id}" value="${i}">
          <span class="sf-q-opt-text">${_esc(o)}</span>
        </label>`).join("");
    return `
      <div class="sf-q-card" data-qid="${q.id}">
        <div class="sf-q-head">
          <span class="sf-q-index">${idx + 1}</span>
          <span class="sf-q-text">${_md(q.question)}</span>
          <span class="sf-q-actions">
            <button class="sf-btn-ghost sf-q-edit" title="Editar pregunta">✏️</button>
            <button class="sf-btn-ghost sf-q-del" title="Borrar pregunta">🗑️</button>
          </span>
        </div>
        <div class="sf-q-opts">${opts}</div>
        <button class="sf-btn sf-q-submit" data-qid="${q.id}"
          data-block-id="${blockId}">Responder</button>
        <div class="sf-q-feedback"></div>
      </div>`;
  }

  function _emptyHtml(blockId) {
    return `<div class="sf-q-empty">
        <span>❓ Sin preguntas todavía — añade la primera.</span>
        <button class="sf-btn sf-q-add" data-block-id="${blockId}">➕ Añadir pregunta</button>
      </div>`;
  }

  // ── render one bin ─────────────────────────────────────────────
  async function _renderBin(bin) {
    const blockId = Number(bin.dataset.blockId);
    const courseId = Number(bin.dataset.courseId);
    const topicId = Number(bin.dataset.topicId);
    const stem = bin.dataset.stem || "";
    try {
      const qs = await _fetchQuestions(blockId);
      const body = qs.length
        ? qs.map((q, i) => _questionCardHtml(q, i, blockId)).join("")
          + `<button class="sf-btn sf-q-add" data-block-id="${blockId}">➕ Añadir pregunta</button>`
        : _emptyHtml(blockId);
      bin.innerHTML = `
        <div class="sf-quiz-wrap">
          ${stem ? `<div class="sf-q-stem md-view">${_md(stem)}</div>` : ""}
          <div class="sf-q-list">${body}</div>
        </div>`;
      _bindBin(bin, blockId, courseId, topicId);
    } catch (e) {
      bin.innerHTML = `<div class="sf-q-error">⚠️ Error cargando preguntas: ${_esc(e.message || e)}</div>`;
    }
  }

  // ── submit answer ──────────────────────────────────────────────
  async function _submitAnswer(qid, optIdx, feedbackEl, blockId) {
    const base = (window.API_URL || "/api");
    const t0 = Date.now();
    try {
      const r = await fetch(`${base}/quiz/answer`, {
        method: "POST",
        headers: Object.assign({ "Content-Type": "application/json" }, __auth()),
        body: JSON.stringify({ question_id: qid, selected_answer: optIdx, time_taken_ms: Date.now() - t0, block_id: blockId }),
      });
      if (!r.ok) throw new Error("quiz/answer " + r.status);
      const j = await r.json();
      const ok = j.result && j.result.is_correct;
      const correct = ok ? (j.correct_answer != null ? j.correct_answer : optIdx) : j.correct_answer;
      feedbackEl.innerHTML = `
        <div class="sf-q-fb ${ok ? "ok" : "bad"}">
          ${ok ? "✅ ¡Correcto!" : "❌ Incorrecto"}
          ${j.explanation ? `<div class="sf-q-explain">${_md(j.explanation)}</div>` : ""}
          ${!ok && correct != null ? `<div class="sf-q-correct">Respuesta correcta: opción ${correct + 1}</div>` : ""}
        </div>`;
      // Disable options after answering
      feedbackEl.closest(".sf-q-card").querySelectorAll("input").forEach((i) => (i.disabled = true));
    } catch (e) {
      feedbackEl.innerHTML = `<div class="sf-q-error">⚠️ ${_esc(e.message || e)}</div>`;
    }
  }

  // ── create / edit / delete questions ───────────────────────────
  function _askPayload(blockId, courseId, topicId, existing) {
    const baseQ = existing ? existing.question : "";
    const baseOpts = existing ? (existing.options || []).join("\n") : "";
    const q = prompt("Texto de la pregunta:", baseQ);
    if (q == null) return null;
    const optsTxt = prompt("Opciones (una por línea):", baseOpts);
    if (optsTxt == null) return null;
    const opts = optsTxt.split("\n").map((s) => s.trim()).filter(Boolean);
    if (opts.length < 2) { alert("Necesita al menos 2 opciones."); return null; }
    const correctRaw = prompt(`Índice de la opción correcta (0-${opts.length - 1}):`,
      existing != null ? String(existing.correct_answer) : "0");
    if (correctRaw == null) return null;
    const correct = parseInt(correctRaw, 10);
    if (Number.isNaN(correct) || correct < 0 || correct >= opts.length) {
      alert("Índice de respuesta correcta inválido."); return null;
    }
    const explanation = prompt("Explicación (opcional):", existing ? (existing.explanation || "") : "") || "";
    return {
      block_id: blockId, course_id: courseId, topic_id: topicId,
      question: q, options: opts, correct_answer: correct,
      explanation, difficulty: "medium",
    };
  }

  async function _createQuestion(blockId, courseId, topicId) {
    const payload = _askPayload(blockId, courseId, topicId, null);
    if (!payload) return;
    const base = (window.API_URL || "/api");
    const r = await fetch(`${base}/quiz/questions`, {
      method: "POST",
      headers: Object.assign({ "Content-Type": "application/json" }, __auth()),
      body: JSON.stringify(payload),
    });
    if (!r.ok) {
      alert("Error creando pregunta: " + r.status);
      return;
    }
    const bin = document.querySelector(`.sf-quiz-bin[data-block-id="${blockId}"]`);
    if (bin) await _renderBin(bin);
  }

  async function _editQuestion(qid, blockId, courseId, topicId, existing) {
    const payload = _askPayload(blockId, courseId, topicId, existing);
    if (!payload) return;
    const base = (window.API_URL || "/api");
    const r = await fetch(`${base}/quiz/questions/${qid}`, {
      method: "PUT",
      headers: Object.assign({ "Content-Type": "application/json" }, __auth()),
      body: JSON.stringify(payload),
    });
    if (!r.ok) {
      alert("Error editando pregunta: " + r.status);
      return;
    }
    const bin = document.querySelector(`.sf-quiz-bin[data-block-id="${blockId}"]`);
    if (bin) await _renderBin(bin);
  }

  async function _deleteQuestion(qid, blockId) {
    if (!confirm("¿Borrar esta pregunta?")) return;
    const base = (window.API_URL || "/api");
    const r = await fetch(`${base}/quiz/questions/${qid}`, {
      method: "DELETE",
      headers: __auth(),
    });
    if (!r.ok) {
      alert("Error borrando pregunta: " + r.status);
      return;
    }
    const bin = document.querySelector(`.sf-quiz-bin[data-block-id="${blockId}"]`);
    if (bin) await _renderBin(bin);
  }

  // ── event delegation per bin ───────────────────────────────────
  function _bindBin(bin, blockId, courseId, topicId) {
    bin.querySelectorAll(".sf-q-submit").forEach((btn) => {
      btn.addEventListener("click", () => {
        const qid = Number(btn.dataset.qid);
        const card = btn.closest(".sf-q-card");
        const selected = card ? card.querySelector("input:checked") : null;
        const fb = card ? card.querySelector(".sf-q-feedback") : null;
        if (!selected || !fb) {
          if (fb) fb.innerHTML = '<div class="sf-q-error">Selecciona una opción primero.</div>';
          return;
        }
        _submitAnswer(qid, Number(selected.value), fb, Number(btn.dataset.blockId));
      });
    });
    bin.querySelectorAll(".sf-q-add").forEach((btn) => {
      btn.addEventListener("click", () => _createQuestion(blockId, courseId, topicId));
    });
    bin.querySelectorAll(".sf-q-edit").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const qid = Number(btn.closest(".sf-q-card").dataset.qid);
        const r = await fetch(
          `${window.API_URL || "/api"}/quiz/questions?block_id=${blockId}&limit=200`,
          { headers: __auth() }
        );
        const j = await r.json();
        const existing = (j.questions || []).find((q) => q.id === qid);
        if (existing) await _editQuestion(qid, blockId, courseId, topicId, existing);
      });
    });
    bin.querySelectorAll(".sf-q-del").forEach((btn) => {
      btn.addEventListener("click", () => {
        const qid = Number(btn.closest(".sf-q-card").dataset.qid);
        _deleteQuestion(qid, blockId);
      });
    });
  }

  // ── public API ─────────────────────────────────────────────────
  function mountBins(centerEl) {
    if (!centerEl) return;
    centerEl.querySelectorAll(".sf-quiz-bin").forEach((bin) => {
      if (bin.dataset.mounted) return;
      bin.dataset.mounted = "1";
      _renderBin(bin).catch((e) => console.warn("[QuizEmbed]", e));
    });
  }

  window.App.QuizEmbed = {
    mountBins,
    _renderBin,
    _fetchQuestions,
  };

  // Legacy: exercise block stem may live in `content` — the renderer
  // passes it via data-stem (see courses-blocks.js).
})();