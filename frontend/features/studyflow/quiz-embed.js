// frontend/features/studyflow/quiz-embed.js
// Issue #10 — quiz por bloque (parity v2), now on the S5 paged runner.
//
// An `exercise` block paints a placeholder:
//   <div class="sf-quiz-bin" data-block-id="N" data-course-id="M"
//        data-topic-id="K" data-stem="..."></div>
// and this module fills it after the center panel is painted (the PdfViewer
// pattern), so the UI never blocks on the API.
//
// S5 changed the shape: the questions now run through App.QuizRunner (5 per
// page, per-page results table, final score). Owner CRUD stays here, behind
// a "Gestionar preguntas" disclosure, because editing a question is block
// authoring and not part of taking a test.
//
// Contract:
//   window.App.QuizEmbed = {
//     mountBins(centerEl)   — mount every .sf-quiz-bin in the panel
//     _renderBin(bin)       — async: fetch questions, mount runner
//   }

(function () {
  if (window.App === undefined) window.App = {};
  if (window.App.QuizEmbed !== undefined) return;

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

  function _questionCardHtml(q, idx) {
    const opts = (q.options || []).map((o, i) => `
        <label class="sf-q-opt" data-qid="${q.id}" data-opt="${i}">
          <input type="radio" name="sf-q-${q.id}" value="${i}" disabled>
          <span class="sf-q-opt-text">${_esc(o)}</span>
        </label>`).join("");
    const letters = "ABCDEFGH";
    const right = letters[q.correct_answer] || "?";
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
        <div class="sf-q-answer-key">Correcta: <b>${right}</b>${q.explanation
          ? ` <span class="sf-q-explain">${_md(q.explanation)}</span>` : ""}</div>
      </div>`;
  }

  function _emptyHtml(blockId) {
    return `<div class="sf-q-empty">
        <span>❓ Sin preguntas todavía — genera un test con IA o añade la primera.</span>
        <button class="sf-btn sf-q-add" data-block-id="${blockId}">➕ Añadir pregunta</button>
      </div>`;
  }

  // ── manager (owner CRUD) ─────────────────────────────────────
  // Rendered as a placeholder on purpose: the runner's questions come from
  // the study route, which strips `correct_answer` on the server, so the
  // answer key only exists in the owner's route. Fetching it lazily (on the
  // first open) keeps the key out of the page until it is actually wanted.
  function _managerHtml(blockId, count) {
    return `<details class="sf-q-manager" data-block-id="${blockId}">
        <summary>⚙️ Gestionar preguntas (${count})</summary>
        <div class="sf-q-list" data-loaded="0">
          <div class="sf-q-empty">Abre para cargar las respuestas correctas.</div>
        </div>
      </details>`;
  }

  // ── render one bin ───────────────────────────────────────────
  async function _renderBin(bin) {
    const blockId = Number(bin.dataset.blockId);
    const courseId = Number(bin.dataset.courseId);
    const topicId = Number(bin.dataset.topicId);
    const stem = bin.dataset.stem || "";
    try {
      const qs = await window.App.QuizAPI.questionsForBlock(blockId);
      const testHtml = qs.length ? "" : _emptyHtml(blockId);
      bin.innerHTML = `
        <div class="sf-quiz-wrap">
          ${stem ? `<div class="sf-q-stem md-view">${_md(stem)}</div>` : ""}
          <div class="sf-quiz-test" data-block-id="${blockId}"></div>
          ${testHtml}
          ${_managerHtml(blockId, qs.length)}
        </div>`;
      if (qs.length) {
        const slot = bin.querySelector(".sf-quiz-test");
        window.App.QuizRunner.mount(slot, { questions: qs, mode: "block" });
      }
      _bindManager(bin, blockId, courseId, topicId);
    } catch (e) {
      bin.innerHTML = `<div class="sf-q-error">⚠️ Error cargando preguntas: ${_esc(e.message || e)}</div>`;
    }
  }

  // ── create / edit / delete questions ─────────────────────────
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

  async function _postQuestion(path, payload) {
    const r = await fetch(`${window.API_URL || "/api"}${path}`, {
      method: path ? "PUT" : "POST",
      headers: Object.assign({ "Content-Type": "application/json" }, __auth()),
      body: JSON.stringify(payload),
    });
    if (!r.ok) throw new Error(`${path || "/quiz/questions"} → ${r.status}`);
  }

  function _remount(blockId) {
    const bin = document.querySelector(`.sf-quiz-bin[data-block-id="${blockId}"]`);
    if (!bin) return Promise.resolve();
    // Add/edit/delete rebuild the whole bin; keep the manager open so the
    // owner does not lose their place after every save.
    const wasOpen = !!bin.querySelector(".sf-q-manager[open]");
    return _renderBin(bin).then(() => {
      if (wasOpen) {
        const det = bin.querySelector(".sf-q-manager");
        if (det) { det.open = true; det.dispatchEvent(new Event("toggle")); }
      }
    });
  }

  function _createQuestion(blockId, courseId, topicId) {
    const payload = _askPayload(blockId, courseId, topicId, null);
    if (!payload) return;
    _postQuestion("/quiz/questions", payload)
      .then(() => _remount(blockId))
      .catch((e) => alert("Error creando pregunta: " + e.message));
  }

  function _editQuestion(qid, blockId, courseId, topicId, existing) {
    const payload = _askPayload(blockId, courseId, topicId, existing);
    if (!payload) return;
    _postQuestion(`/quiz/questions/${qid}`, payload)
      .then(() => _remount(blockId))
      .catch((e) => alert("Error editando pregunta: " + e.message));
  }

  function _deleteQuestion(qid, blockId) {
    if (!confirm("¿Borrar esta pregunta?")) return;
    fetch(`${window.API_URL || "/api"}/quiz/questions/${qid}`, { headers: __auth() })
      .then((r) => {
        if (!r.ok) throw new Error("→ " + r.status);
        return _remount(blockId);
      })
      .catch((e) => alert("Error borrando pregunta: " + e.message));
  }

  // ── manager bindings ─────────────────────────────────────────
  // Split so they can be re-applied to the list injected by the lazy load,
  // which replaces the nodes these listeners were attached to.
  function _bindAdd(root, blockId, courseId, topicId) {
    root.querySelectorAll(".sf-q-add").forEach((btn) => {
      btn.addEventListener("click", () => _createQuestion(blockId, courseId, topicId));
    });
  }

  function _bindRows(root, blockId, courseId, topicId) {
    root.querySelectorAll(".sf-q-edit").forEach((btn) => {
      btn.addEventListener("click", () => {
        const qid = Number(btn.closest(".sf-q-card").dataset.qid);
        // The owner's route carries the answer key; the study route hides it.
        window.App.QuizAPI.questionsForManage(blockId).then((qs) => {
          const existing = (qs || []).find((q) => q.id === qid);
          if (existing) _editQuestion(qid, blockId, courseId, topicId, existing);
          else alert("No se pudo cargar la pregunta para editar.");
        }).catch(() => alert("No se pudo cargar la pregunta para editar."));
      });
    });
    root.querySelectorAll(".sf-q-del").forEach((btn) => {
      btn.addEventListener("click", () => {
        _deleteQuestion(Number(btn.closest(".sf-q-card").dataset.qid), blockId);
      });
    });
  }

  function _bindManager(bin, blockId, courseId, topicId) {
    _bindAdd(bin, blockId, courseId, topicId);
    _bindRows(bin, blockId, courseId, topicId);
    const det = bin.querySelector(".sf-q-manager");
    if (!det || det.dataset.bound) return;
    det.dataset.bound = "1";
    det.addEventListener("toggle", () => {
      if (!det.open) return;
      const list = det.querySelector(".sf-q-list");
      if (!list || list.dataset.loaded === "1") return;
      list.dataset.loaded = "1";
      list.innerHTML = '<div class="sf-q-empty">Cargando…</div>';
      window.App.QuizAPI.questionsForManage(blockId).then((qs) => {
        const rows = (qs || []).map((q, i) => _questionCardHtml(q, i)).join("");
        const add = `<button class="sf-btn sf-q-add" data-block-id="${blockId}">`
                  + "➕ Añadir pregunta</button>";
        list.innerHTML = (rows || '<div class="sf-q-empty">Sin preguntas.</div>') + add;
        _bindAdd(list, blockId, courseId, topicId);
        _bindRows(list, blockId, courseId, topicId);
      }).catch((e) => {
        list.dataset.loaded = "0";
        list.innerHTML = `<div class="sf-q-error">⚠️ ${_esc(e.message || e)}</div>`;
      });
    });
  }

  // ── public API ───────────────────────────────────────────────
  function mountBins(centerEl) {
    if (!centerEl) return;
    centerEl.querySelectorAll(".sf-quiz-bin").forEach((bin) => {
      if (bin.dataset.mounted) return;
      bin.dataset.mounted = "1";
      _renderBin(bin).catch((e) => console.warn("[QuizEmbed]", e));
    });
  }

  window.App.QuizEmbed = { mountBins, _renderBin };
})();
