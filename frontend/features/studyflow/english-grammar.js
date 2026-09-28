// ─── English Grammar Exercises — Phase 61 addon (v3 port) ─────────
// Namespace: window.App.EnglishGrammar
// Dependencies: API (global), STATE (global), window.App.AI (callbacks),
//               window.App.AI.Generation.generateGrammarExercises (launch),
//               window.App.Courses.renderStudyflow (re-render)
//
// The addon's ✨ Generate button ("English Exercises") runs on markdown /
// content blocks (v1, S6: PDF out of scope). A config overlay (inf-config-*)
// lets the user pick ONLY the depth (⚡ Conciso / 🔤 Estándar / 📚 Detallado);
// the amount of exercises per type stays IMPLICIT (max of the level) and is
// resolved inside the generated template. The backend returns a full HTML
// fragment (english-practice.html with a generated CONFIG injected); this
// module inserts it as an interactive block right below the source:
//   { type: "interactive", title: "✏️ Exercises <source>", content: html }
// InteractiveBlocks renders it inside a sandboxed iframe (allow-scripts).
//
// v3 port deltas: source title lookup uses the same API route as the rest
// of v3 (GET /courses/<id>); provider pinned to notebooklm at launch.

window.App = window.App || {};
window.App.EnglishGrammar = (function () {
  "use strict";

  /** Same strip rules as ai.js _cleanBlockTitle (emoji prefix + time suffix)
   *  so generated titles stay consistent with the other AI blocks. */
  function _cleanBlockTitle(title) {
    return String(title || "")
      .replace(/^[📄📝📕🎵❓🤖✨📊🧠✏️]\s*/u, "")
      .replace(/\s*·\s*\d{1,2}\/\d{1,2},\s*\d{1,2}:\d{2}$/, "")
      .trim();
  }

  /**
   * onGrammarSuccess(task, blockId, topicId)
   * Called from ai-tasks.js streaming poll (format "grammar"): builds the
   * interactive block with the generated HTML and inserts it after the
   * source block via ai.js _addBlockAfterSource.
   */
  async function onGrammarSuccess(task, blockId, topicId) {
    const ai = window.App.AI;
    const html = (task && task.result_content) || "";
    if (!html) {
      if (ai._showStatus) ai._showStatus(blockId, "❌ Ejercicios vacíos", true);
      return;
    }

    const courseId = (typeof STATE !== "undefined" && STATE.currentCourseId) || "";
    if (!courseId) {
      if (ai._showStatus) ai._showStatus(blockId, "❌ No hay curso activo", true);
      return;
    }

    // ── Source title for naming the generated block ───────────────
    // Same lookup pattern as v3 ai.js _onContentSuccess (fetchCourses +
    // course.blocks), so language/source parity is kept across generators.
    let sourceTitle = "Exercises";
    try {
      const list = await window.App.CoursesAPI.fetchCourses();
      const course = (list || []).find((c) => c.id === courseId) || {};
      const sourceBlock = (course.blocks || []).find((b) => b.id === blockId);
      if (sourceBlock && sourceBlock.title) {
        sourceTitle = _cleanBlockTitle(sourceBlock.title) || "Exercises";
      }
    } catch {
      /* fallback to "Exercises" */
    }

    const blockData = {
      type: "interactive",
      title: `✏️ Exercises ${sourceTitle}`,
      content: html,
      topic_id: topicId,
    };

    try {
      if (typeof ai._addBlockAfterSource !== "function") {
        throw new Error("_addBlockAfterSource no disponible");
      }
      await ai._addBlockAfterSource(courseId, blockId, blockData);
      if (ai._showStatus) ai._showStatus(blockId, "✅ English exercises generados", true);
      setTimeout(() => {
        if (window.App.Courses && window.App.Courses.renderStudyflow) {
          window.App.Courses.renderStudyflow();
        }
      }, 500);
    } catch {
      if (ai._showStatus) ai._showStatus(blockId, "❌ Error al guardar los ejercicios", true);
    }
  }

  // ─── Depth levels (YouTube Zen pattern) ─────────────────────────
  // El nivel lleva la cantidad implícita (máx. generado por tipo, que el
  // template ofrece como opciones del selector "Por apartado"):
  //   ⚡ Conciso   → 10 por tipo (selector: 1 · 5 · 10)
  //   🔤 Estándar  → 20 por tipo (selector: 10 · 20)
  //   📚 Detallado → 40 por tipo (selector: 20 · 40)
  const _GRAMMAR_LEVELS = {
    concise: {
      emoji: "⚡", label: "Conciso", maxPerType: 10, sizeOptions: [1, 5, 10],
      desc: "resumen rápido",
    },
    standard: {
      emoji: "🔤", label: "Estándar", maxPerType: 20, sizeOptions: [10, 20],
      desc: "equilibrado · recomendado",
    },
    detailed: {
      emoji: "📚", label: "Detallado", maxPerType: 40, sizeOptions: [20, 40],
      desc: "máxima profundidad",
    },
  };

  /**
   * showGrammarConfig(blockId, topicId, sourceType, opts)
   * Opens the depth config overlay (⚡ Conciso / 🔤 Estándar / 📚 Detallado).
   * The amount per type is implicit (max of the level); the template's own
   * "Por apartado" selector lets the user pick a smaller round. On "Generate"
   * it closes and launches window.App.AI.Generation.generateGrammarExercises(...).
   *
   * v3: provider pinned to 'notebooklm' by the ai.js handler (Phase 66 parity);
   * the title chips "· NotebookLM" to mirror the KP/YouTube badge pattern.
   */
  function showGrammarConfig(blockId, topicId, sourceType, opts = {}) {
    const existing = document.getElementById("eng-config-overlay");
    if (existing) existing.remove();

    const provider = opts && opts.provider ? String(opts.provider) : "";

    // Default: Estándar → 20 por tipo (implícito)
    let currentLevel = "standard";

    // ── Extensión seleccionada → preview del selector "Por apartado" ──
    const renderSizePreview = () => {
      const lv = _GRAMMAR_LEVELS[currentLevel] || _GRAMMAR_LEVELS.standard;
      const el = document.getElementById("eng-size-preview");
      if (el) {
        el.innerHTML =
          `🔢 Por apartado: <strong>${lv.sizeOptions.join(" · ")}</strong>` +
          ` <span class="eng-size-preview-note">(generará ${lv.maxPerType} por tipo)</span>`;
      }
    };

    // ── Build overlay skeleton ────────────────────────────────────
    const levelRadios = Object.entries(_GRAMMAR_LEVELS).map(([id, lv]) =>
      `<label class="inf-config-radio">
        <input type="radio" name="eng-length" value="${id}" ${id === "standard" ? "checked" : ""}>
        <span class="inf-config-radio-label">${lv.emoji} ${lv.label}</span>
        <span class="inf-config-radio-desc">${lv.desc}</span>
      </label>`
    ).join("");

    document.body.insertAdjacentHTML("beforeend", `
      <div class="inf-config-overlay" id="eng-config-overlay">
        <div class="inf-config-modal eng-config-modal">
          <div class="inf-config-header">
            <span class="inf-config-title">✏️ Configurar English Exercises${provider === "notebooklm" ? " · NotebookLM" : ""}</span>
            <button class="inf-config-close" id="eng-close" title="Cerrar">✕</button>
          </div>
          <div class="inf-config-body">
            <div class="inf-config-section">
              <div class="inf-config-section-title">📐 Extensión</div>
              <div class="inf-config-radios" id="eng-levels">${levelRadios}</div>
              <div class="eng-size-preview" id="eng-size-preview"></div>
            </div>
          </div>
          <div class="inf-config-footer">
            <button class="ai-btn" id="eng-cancel">Cancelar</button>
            <button class="ai-btn" id="eng-generate"
              style="background:linear-gradient(135deg,#1a73e8,#0d47a1);color:#fff;font-weight:600;">
              ✏️ Generar English Exercises
            </button>
          </div>
        </div>
      </div>`);

    const overlayEl = document.getElementById("eng-config-overlay");
    const close = () => overlayEl.remove();
    document.getElementById("eng-close").addEventListener("click", close);
    document.getElementById("eng-cancel").addEventListener("click", close);
    overlayEl.addEventListener("click", (e) => { if (e.target === e.currentTarget) close(); });

    // ── Level radios → actualiza nivel actual + preview ─────────────
    renderSizePreview();
    overlayEl.querySelectorAll('input[name="eng-length"]').forEach((radio) => {
      radio.addEventListener("change", () => {
        currentLevel = radio.value;
        renderSizePreview();
      });
    });

    // ── Generate → close + launch (cantidad implícita del nivel) ──
    document.getElementById("eng-generate").addEventListener("click", () => {
      const gen = window.App.AI && window.App.AI.Generation;
      if (!gen || typeof gen.generateGrammarExercises !== "function") {
        const ai = window.App.AI;
        if (ai && ai._showStatus) ai._showStatus(blockId, "❌ Módulo English no disponible", true);
        close();
        return;
      }
      const lv = _GRAMMAR_LEVELS[currentLevel] || _GRAMMAR_LEVELS.standard;
      close();
      gen.generateGrammarExercises(blockId, topicId, sourceType, lv.maxPerType, provider || undefined);
    });
  }

  // ─── Public API ─────────────────────────────────────────────────
  return {
    onGrammarSuccess,
    showGrammarConfig,
  };
})();

console.log("[EnglishGrammar] loaded");