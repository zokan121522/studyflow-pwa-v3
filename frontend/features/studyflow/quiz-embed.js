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
// page, per-page results table, final score). R5 moved the owner side out:
// editing lives in App.QuizJsonEditor (the questions as JSON, behind the
// "Gestionar preguntas" disclosure), because editing a question is block
// authoring and not part of taking a test. This module only renders the bin
// and hands the disclosure to the editor.
//
// Contract:
//   window.App.QuizEmbed = {
//     mountBins(centerEl)   — mount every .sf-quiz-bin in the panel
//     _renderBin(bin)       — async: fetch questions, mount runner
//   }

(function () {
  if (window.App === undefined) window.App = {};
  if (window.App.QuizEmbed !== undefined) return;

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

  function _emptyHtml(blockId) {
    return `<div class="sf-q-empty">
        <span>❓ Sin preguntas todavía — escribe un test en JSON o genera uno con IA.</span>
        <button class="sf-btn sf-q-manage-open" data-block-id="${blockId}">✏️ Editar preguntas (JSON)</button>
      </div>`;
  }

  // ── manager ──────────────────────────────────────────────────
  // R5 — the per-question CRUD behind this disclosure was four chained
  // prompt() dialogs per question and is now the JSON editor, which covers
  // the same ground and can also show what the test looks like. The host is
  // a placeholder: the editor mounts on first open, so a topic full of
  // exercise blocks does not fetch two question lists up front.
  function _managerHtml(blockId, count) {
    return `<details class="sf-q-manager" data-block-id="${blockId}">
        <summary>⚙️ Gestionar preguntas (${count})</summary>
        <div class="sf-q-host" data-loaded="0"></div>
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

  // ── remount after a save ─────────────────────────────────────
  function _remount(blockId) {
    const bin = document.querySelector(`.sf-quiz-bin[data-block-id="${blockId}"]`);
    if (!bin) return Promise.resolve();
    // A save rebuilds the whole bin; keep the manager open so the owner does
    // not lose their place, and so the count in the summary refreshes.
    const wasOpen = !!bin.querySelector(".sf-q-manager[open]");
    return _renderBin(bin).then(() => {
      if (wasOpen) {
        const det = bin.querySelector(".sf-q-manager");
        if (det) { det.open = true; det.dispatchEvent(new Event("toggle")); }
      }
    });
  }

  // ── manager bindings ─────────────────────────────────────────
  function _bindOpenManager(root) {
    root.querySelectorAll(".sf-q-manage-open").forEach((btn) => {
      btn.addEventListener("click", () => {
        const det = root.querySelector(".sf-q-manager");
        if (det) { det.open = true; det.dispatchEvent(new Event("toggle")); }
      });
    });
  }

  function _bindManager(bin, blockId, courseId, topicId) {
    _bindOpenManager(bin);
    const det = bin.querySelector(".sf-q-manager");
    if (!det || det.dataset.bound) return;
    det.dataset.bound = "1";
    det.addEventListener("toggle", () => {
      if (!det.open) return;
      const host = det.querySelector(".sf-q-host");
      if (!host || host.dataset.loaded === "1") return;
      const editor = window.App.QuizJsonEditor;
      if (!editor) {
        host.innerHTML = '<div class="sf-q-error">Módulo del editor JSON no cargado.</div>';
        return;
      }
      host.dataset.loaded = "1";
      editor.mount(host, {
        blockId, courseId, topicId,
        onSaved: () => _remount(blockId),
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
