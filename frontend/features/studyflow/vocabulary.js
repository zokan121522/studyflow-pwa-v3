// ─── Vocabulary Suite addon (ported from studyflow-hub v2, Phase 70 #261) ────
// Namespace: window.App.Vocabulary
// Dependencies: API (global), STATE (global), window.App.AI (callbacks:
//               _showStatus, _addBlockAfterSource), window.App.AI.Generation
//               .generateVocabulary (launch), window.App.Courses.renderStudyflow
//               (re-render), window.App.CoursesAPI.addBlock (topic-only insert).
//
// The addon's 📚 Vocabulary button (openzen + notebooklm addons) opens
// a textarea + round-size config overlay. Submit → POST /api/ai/generate-
// vocabulary (background thread) → streaming modal → on completion the
// dispatcher (format "vocabulary") calls onVocabularySuccess, which
// inserts the generated HTML as an interactive block in the active
// course. When launched from a topic header (no source blockId),
// the block is appended to that topic via CoursesAPI.addBlock.
//
// blockId handling mirrors the Knowledge Pipeline lesson: empty
// blockId means "topic-only" → bypass _addBlockAfterSource (it needs a
// real source) and use CoursesAPI.addBlock directly.
//
// Port note: verbatim from v2 except the header comment. All dependencies
// already exist in v3 (ai.css ships the .inf-config-* overlay classes).

window.App = window.App || {};
window.App.Vocabulary = (function () {
  "use strict";

  /** Same strip rules as ai.js _cleanBlockTitle. */
  function _cleanBlockTitle(title) {
    return String(title || "")
      .replace(/^[📄📝📕🎵❓🤖✨📊📚]\s*/u, "")
      .replace(/\s*·\s*\d{1,2}\/\d{1,2},\s*\d{1,2}:\d{2}$/, "")
      .trim();
  }

  /**
   * Extract the themed title that the backend injects into the generated
   * vocabulary HTML. The marker is a JS assignment of the form
   *   var VOCAB_TITLE="<themed title>"; followed by the sentinel
   *   VOCAB_INJECT_TITLE inside a block comment.
   * The value is JSON-string encoded (inner double-quotes appear as \").
   * Returns "" if missing or if the model fell back to the generic
   * "Vocabulary Suite" sentinel — callers should treat that as no title.
   */
  function _themeTitleFromHtml(html) {
    const m = (html || "").match(/var VOCAB_TITLE="((?:[^"\\]|\\.)*)";/);
    if (!m) return "";
    let t = "";
    try { t = JSON.parse('"' + m[1] + '"'); } catch { t = m[1].replace(/\\"/g, '"'); }
    t = (t || "").trim();
    return t === "Vocabulary Suite" ? "" : t;
  }

  /** Round-size selector (matches the Match game in the template). */
  const _SIZES = [
    { value: 4, label: "4" },
    { value: 6, label: "6" },
    { value: 10, label: "10" },
    { value: "all", label: "Todas" },
  ];
  let _currentSize = 10;

  /**
   * onVocabularySuccess(task, blockId, topicId)
   * Called from ai-tasks.js (format "vocabulary"): build an interactive
   * block with the generated HTML and insert it. Two insert paths:
   *   - blockId present (per-block launch) → ai._addBlockAfterSource
   *   - blockId empty   (topic-header launch) → CoursesAPI.addBlock
   *     (creates the block in topicId; _cleanBlockTitle fallback to topic)
   *
   * Nav title: prefers the themed VOCAB_TITLE injected into the generated
   * HTML (see _themeTitleFromHtml); when missing or generic, falls back to
   * the legacy "📚 Vocabulary — <sourceTitle>" naming.
   */
  async function onVocabularySuccess(task, blockId, topicId) {
    const ai = window.App.AI;
    const html = (task && task.result_content) || "";
    if (!html) {
      if (ai && ai._showStatus) ai._showStatus(blockId || topicId || "", "❌ Vocabulary vacía", true);
      return;
    }

    const courseId = (typeof STATE !== "undefined" && STATE.currentCourseId) || "";
    if (!courseId) {
      if (ai && ai._showStatus) ai._showStatus(blockId || topicId || "", "❌ No hay curso activo", true);
      return;
    }

    // ── Themed title injected by the backend into the HTML ─────
    const themeTitle = _themeTitleFromHtml(html);

    // ── Source title for naming the generated block ─────────────
    // NOTE: the API returns numeric ids (342) while the DOM dataset gives
    // strings ("342"), so these lookups MUST compare as strings. v2 used a
    // bare `===` and therefore never matched: every topic-only block ended
    // up named "Vocabulary — Vocabulary".
    let sourceTitle = "Vocabulary";
    if (blockId) {
      try {
        const course = await API.get(`/courses/${courseId}`);
        const sourceBlock = (course.blocks || []).find((b) => String(b.id) === String(blockId));
        if (sourceBlock && sourceBlock.title) {
          sourceTitle = _cleanBlockTitle(sourceBlock.title) || "Vocabulary";
        }
      } catch { /* fallback to "Vocabulary" */ }
    }
    // If topic-only, try the topic title.
    if ((!sourceTitle || sourceTitle === "Vocabulary") && topicId && window.App.CoursesAPI) {
      try {
        const course = await API.get(`/courses/${courseId}`);
        const topic = (course.topics || []).find((t) => String(t.id) === String(topicId));
        if (topic && topic.title) sourceTitle = topic.title;
      } catch { /* keep fallback */ }
    }

    const blockData = {
      type: "interactive",
      title: themeTitle ? `📚 ${themeTitle}` : `📚 Vocabulary — ${sourceTitle}`,
      content: html,
      topic_id: topicId || null,
    };

    try {
      if (blockId && typeof ai._addBlockAfterSource === "function") {
        // Path A: block-after-source (mirrors english-grammar.onGrammarSuccess).
        await ai._addBlockAfterSource(courseId, blockId, blockData);
      } else if (window.App.CoursesAPI && typeof window.App.CoursesAPI.addBlock === "function") {
        // Path B: no source block — append to topic directly.
        const newId = await window.App.CoursesAPI.addBlock(courseId, blockData);
        if (!newId) throw new Error("CoursesAPI.addBlock no devolvió id");
      } else {
        throw new Error("No hay helper de inserción disponible");
      }
      if (ai && ai._showStatus) {
        ai._showStatus(blockId || topicId || "", "✅ Vocabulary Suite generada", true);
      }
      setTimeout(() => {
        if (window.App.Courses && window.App.Courses.renderStudyflow) {
          window.App.Courses.renderStudyflow();
        }
      }, 500);
    } catch (err) {
      if (ai && ai._showStatus) {
        ai._showStatus(blockId || topicId || "", `❌ Error al guardar: ${err.message || err}`, true);
      }
    }
  }

  /**
   * showVocabularyConfig(topicId, courseId, opts)
   * Opens the textarea + round-size overlay.
   *   - opts.provider === 'notebooklm' → title "· NotebookLM", routes
   *     through the NB provider; otherwise default opencode-acp.
   *   - On Generate → close overlay + Generation.generateVocabulary(...).
   *
   * arg shape mirrors showGrammarConfig(blockId, topicId, sourceType, opts);
   * the first two args here are topic-only (no source block) because the
   * addon can be launched from a topic header as well as from a block bar.
   */
  function showVocabularyConfig(topicId, courseId, opts = {}) {
    const existing = document.getElementById("vocab-config-overlay");
    if (existing) existing.remove();

    const provider = opts && opts.provider ? String(opts.provider) : "";
    // Label the modal by provider, mirroring english-grammar.js: OpenZen and
    // NotebookLM share this overlay and only the engine differs.
    const providerLabel = provider === "notebooklm" ? " · NotebookLM"
                        : provider === "opencode-acp" ? " · OpenZen" : "";
    _currentSize = 10; // default: 10 — mirrors "Estándar" of grammar addon

    const sizeRadios = _SIZES.map((s) =>
      `<label class="inf-config-radio">
        <input type="radio" name="vocab-size" value="${s.value}" ${s.value === _currentSize ? "checked" : ""}>
        <span class="inf-config-radio-label">${s.label}</span>
      </label>`
    ).join("");

    document.body.insertAdjacentHTML("beforeend", `
      <div class="inf-config-overlay" id="vocab-config-overlay">
        <div class="inf-config-modal eng-config-modal">
          <div class="inf-config-header">
            <span class="inf-config-title">📚 Configurar Vocabulary Suite${providerLabel}</span>
            <button class="inf-config-close" id="vocab-close" title="Cerrar">✕</button>
          </div>
          <div class="inf-config-body">
            <div class="inf-config-section">
              <div class="inf-config-section-title">📝 Palabras o frases</div>
              <textarea id="vocab-text" class="inf-config-textarea"
                placeholder="one word/phrase per line — p. ej.&#10;make up your mind&#10;run out of time&#10;turn down an offer"
                style="width:100%;min-height:160px;padding:10px;border:1px solid #d1d5db;border-radius:8px;font-family:ui-monospace,monospace;font-size:.8rem;"></textarea>
            </div>
            <div class="inf-config-section">
              <div class="inf-config-section-title">📐 Filas por ronda</div>
              <div class="inf-config-radios">${sizeRadios}</div>
            </div>
          </div>
          <div class="inf-config-footer">
            <button class="ai-btn" id="vocab-cancel">Cancelar</button>
            <button class="ai-btn" id="vocab-generate"
              style="background:linear-gradient(135deg,#1a73e8,#0d47a1);color:#fff;font-weight:600;">
              📚 Generar Vocabulary Suite
            </button>
          </div>
        </div>
      </div>`);

    const overlayEl = document.getElementById("vocab-config-overlay");
    const close = () => overlayEl.remove();
    document.getElementById("vocab-close").addEventListener("click", close);
    document.getElementById("vocab-cancel").addEventListener("click", close);
    overlayEl.addEventListener("click", (e) => { if (e.target === e.currentTarget) close(); });

    overlayEl.querySelectorAll('input[name="vocab-size"]').forEach((radio) => {
      radio.addEventListener("change", () => {
        _currentSize = radio.value === "all" ? "all" : parseInt(radio.value, 10);
      });
    });

    document.getElementById("vocab-generate").addEventListener("click", () => {
      const ta = document.getElementById("vocab-text");
      const wordsText = (ta && ta.value || "").trim();
      if (!wordsText) {
        const ai = window.App.AI;
        if (ai && ai._showStatus) ai._showStatus(topicId || "", "⚠️ Escribe al menos una palabra/frase", true);
        return;
      }
      const gen = window.App.AI && window.App.AI.Generation;
      if (!gen || typeof gen.generateVocabulary !== "function") {
        const ai = window.App.AI;
        if (ai && ai._showStatus) ai._showStatus(topicId || "", "❌ Módulo Vocabulary no disponible", true);
        close();
        return;
      }
      close();
      gen.generateVocabulary(topicId || "", wordsText, _currentSize, provider || "");
    });
  }

  return {
    onVocabularySuccess,
    showVocabularyConfig,
  };
})();
