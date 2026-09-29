/**
 * Knowledge Pipeline — modal + API + polling + block insertion (v3 port).
 *
 * Phase 38: "🧠 Gen. Contenido" button in the NotebookLM toolbar opens
 * a modal with textarea + template/depth/mode/language selectors. Sends
 * topic text to the v3 NotebookLM provider via POST /api/ai/knowledge-pipeline,
 * polls for completion, and inserts the result as a new markdown block
 * in the current course.
 *
 * v3 port deltas vs v2 (studyflow-hub/luna):
 *   - Plantillas: GET /ai/md-templates (v3 catalog) instead of
 *     /ai/openzen-md-templates (v2).
 *   - startStreamPoll con format "knowledge_pipeline" (v3 ai-tasks).
 *   - Insert vía ai._onContentSuccess (mismo contrato v3 que markdown/html).
 */
(() => {
  "use strict";

  // API and STATE are globals defined in app.js / agenda-glue.js.

  // ─── Input Modal ────────────────────────────────────────────────

  /**
   * Show the Knowledge Pipeline input modal.
   * Returns a Promise resolving to { topicText, depth, ... } or null if cancelled.
   * v3 renders a "NotebookLM" provider badge (only provider registered).
   */
  function _showInputModal() {
    return new Promise((resolve) => {
      // Remove any existing modal
      const existing = document.getElementById("kp-input-overlay");
      if (existing) existing.remove();

      const overlay = document.createElement("div");
      overlay.id = "kp-input-overlay";
      overlay.className = "kp-overlay";
      overlay.innerHTML = `
        <div class="kp-modal kp-modal-yt-wide">
          <div class="kp-modal-header">
            <span class="kp-modal-icon">🧠</span>
            <span class="kp-modal-title">Generador Contenido · <span class="kp-provider-badge">NotebookLM</span></span>
            <button class="kp-modal-close" id="kp-close" title="Cerrar">✕</button>
          </div>
          <div class="kp-modal-body">
            <label class="kp-label" for="kp-topic-text">
              Describe el tema que quieres generar:
            </label>
            <textarea
              id="kp-topic-text"
              class="kp-textarea"
              placeholder="Ej: Components vs Directives en Angular, Lifecycle hooks, Data binding patterns..."
              rows="5"
            ></textarea>
            <label class="kp-label">🎛️ Plantilla de prompt (opcional):</label>
            <div class="ozmd-grid">
              <div class="ozmd-templates" id="kp-templates">
                <div style="color:#aaa;padding:6px 2px;">Cargando plantillas…</div>
              </div>
              <div class="ozmd-preview">
                <div class="ozmd-preview-title">👁️ Vista previa</div>
                <div class="ozmd-preview-body" id="kp-preview-body">
                  <div style="color:#aaa;padding:6px 2px;">Sin plantilla — prompt estándar</div>
                </div>
              </div>
            </div>
            <label class="kp-label">Profundidad:</label>
            <div class="kp-depth-row">
              <button class="kp-depth-btn" data-depth="concise">
                <span class="kp-depth-icon">📄</span>
                <span class="kp-depth-name">Conciso</span>
                <span class="kp-depth-desc">1-3 párrafos</span>
              </button>
              <button class="kp-depth-btn selected" data-depth="standard">
                <span class="kp-depth-icon">📝</span>
                <span class="kp-depth-name">Estándar</span>
                <span class="kp-depth-desc">Def + ejemplos</span>
              </button>
              <button class="kp-depth-btn" data-depth="detailed">
                <span class="kp-depth-icon">📚</span>
                <span class="kp-depth-name">Detallado</span>
                <span class="kp-depth-desc">Curso completo</span>
              </button>
            </div>
            <label class="kp-label">Modo:</label>
            <div class="kp-mode-row">
              <button class="kp-mode-btn selected" data-mode="unitema">
                <span class="kp-mode-icon">📄</span>
                <span class="kp-mode-name">Un tema</span>
                <span class="kp-mode-desc">Todo en un bloque</span>
              </button>
              <button class="kp-mode-btn" data-mode="por_tema">
                <span class="kp-mode-icon">📑</span>
                <span class="kp-mode-name">Por tema</span>
                <span class="kp-mode-desc">Sección = tema nuevo</span>
              </button>
            </div>
            <label class="kp-label">Idioma:</label>
            <div class="kp-lang-row">
              <button class="kp-lang-btn selected" data-lang="es">🇪🇸 Español</button>
              <button class="kp-lang-btn" data-lang="en">🇬🇧 English</button>
            </div>
          </div>
          <div class="kp-modal-footer">
            <button class="kp-btn kp-btn-cancel" id="kp-cancel">Cancelar</button>
            <button class="kp-btn kp-btn-submit" id="kp-submit">🧠 Generar</button>
          </div>
        </div>
      `;
      document.body.appendChild(overlay);

      const textarea = document.getElementById("kp-topic-text");
      const submitBtn = document.getElementById("kp-submit");
      let selectedDepth = "standard";
      let selectedLang = "es";
      let selectedMode = "unitema";
      let selectedTemplate = ""; // '' = generic prompt (backward compatible)

      // ── Templates grid + live preview (same pattern as YouTube Zen) ──
      // Same 4 templates as YouTube Zen so both generators share the
      // catalog/look: architecture, tutorial, visual density, baselines.
      const KP_TEMPLATE_IDS = ["arquitectura-tecnica", "tutorial", "infografia-textual", "notas-estandar"];

      (async () => {
        const templatesEl = document.getElementById("kp-templates");
        const previewBody = document.getElementById("kp-preview-body");
        let templates = [];
        try {
          // v3 endpoint: /ai/md-templates (catalog module) — same shape.
          const resp = await API.get("/ai/md-templates");
          templates = (resp.templates || [])
            .filter((t) => KP_TEMPLATE_IDS.includes(t.id))
            .sort((a, b) => KP_TEMPLATE_IDS.indexOf(a.id) - KP_TEMPLATE_IDS.indexOf(b.id));
        } catch (err) {
          templatesEl.innerHTML = `<div style="color:#e57373;padding:6px 2px;">❌ No se pudieron cargar plantillas</div>`;
          return;
        }
        const renderPreview = (tpl) => {
          const cb = window.App?.ContentBlocks;
          previewBody.innerHTML = cb && cb._renderMd
            ? cb._renderMd(tpl.mock || "")
            : `<pre>${tpl.mock || ""}</pre>`;
        };

        const cards = templates.map((t, i) => {
          const elId = `kp-tpl-${i}`;
          return `<div class="inf-config-style-opt ozmd-tpl-opt" id="${elId}" data-template-id="${t.id}" title="${t.description || ""}" data-tpl-idx="${i}">
            <div class="inf-config-style-label">${t.emoji || ""} ${t.name || t.id}</div>
            <div class="inf-config-style-desc">${t.description || ""}</div>
          </div>`;
        }).join("");

        templatesEl.innerHTML =
          `<div class="inf-config-style-opt ozmd-tpl-opt ozmd-tpl-none selected" data-template-id="" data-tpl-idx="-1">
            <div class="inf-config-style-label">⚡ Sin plantilla</div>
            <div class="inf-config-style-desc">Prompt estándar (comportamiento actual)</div>
          </div>${cards}`;

        templatesEl.querySelectorAll(".ozmd-tpl-opt").forEach((el) => {
          el.addEventListener("click", () => {
            templatesEl.querySelectorAll(".ozmd-tpl-opt").forEach((o) => o.classList.remove("selected"));
            el.classList.add("selected");
            selectedTemplate = el.dataset.templateId || "";
            const idx = parseInt(el.dataset.tplIdx, 10);
            const tpl = idx >= 0 ? templates[idx] : null;
            if (tpl) renderPreview(tpl);
            else previewBody.innerHTML = `<div style="color:#aaa;padding:6px 2px;">Sin plantilla — prompt estándar</div>`;
          });
        });
      })();

      // Depth button selection
      overlay.querySelectorAll(".kp-depth-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
          overlay.querySelectorAll(".kp-depth-btn").forEach((b) => b.classList.remove("selected"));
          btn.classList.add("selected");
          selectedDepth = btn.dataset.depth;
        });
      });

      // Language button selection
      overlay.querySelectorAll(".kp-lang-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
          overlay.querySelectorAll(".kp-lang-btn").forEach((b) => b.classList.remove("selected"));
          btn.classList.add("selected");
          selectedLang = btn.dataset.lang;
        });
      });

      // Mode button selection
      overlay.querySelectorAll(".kp-mode-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
          overlay.querySelectorAll(".kp-mode-btn").forEach((b) => b.classList.remove("selected"));
          btn.classList.add("selected");
          selectedMode = btn.dataset.mode;
        });
      });

      // Close handlers
      const close = () => { overlay.remove(); resolve(null); };
      document.getElementById("kp-close").addEventListener("click", close);
      document.getElementById("kp-cancel").addEventListener("click", close);
      overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });

      // Submit
      submitBtn.addEventListener("click", () => {
        const text = textarea.value.trim();
        if (!text) {
          textarea.focus();
          textarea.style.borderColor = "#ef4444";
          setTimeout(() => { textarea.style.borderColor = ""; }, 1500);
          return;
        }
        overlay.remove();
        resolve({ topicText: text, depth: selectedDepth, lang: selectedLang, mode: selectedMode, templateId: selectedTemplate });
      });

      // Auto-focus textarea
      setTimeout(() => textarea.focus(), 100);
    });
  }

  // ─── Main Entry Point ───────────────────────────────────────────

  /**
   * Open the Knowledge Pipeline flow (v3 — always NotebookLM provider).
   * Called from the NotebookLM "Gen. Contenido" toolbar button (ai.js).
   * @param {string} courseId - Course ID from the toolbar context
   * @param {string} topicId - Topic ID from the toolbar context
   */
  async function openKnowledgePipeline(courseId, topicId) {
    const input = await _showInputModal();
    if (!input) return; // cancelled

    const { topicText, depth, lang, mode, templateId } = input;
    // Use passed params first, fallback to STATE
    const resolvedCourseId = courseId || STATE?.currentCourseId || "";
    const resolvedTopicId = topicId || STATE?.selectedTopicId || "";

    // Show status in the bar area (if available)
    _showKpStatus("🧠 Generando contenido…");

    try {
      const body = {
        topic_text: topicText,
        depth: depth,
        topic_id: resolvedTopicId,
        course_id: resolvedCourseId,
        language: lang,
        mode: mode,
        template_id: templateId || null,
      };
      const resp = await API.post("/ai/knowledge-pipeline", body);

      const tasks = window.App?.AI?.Tasks;
      if (tasks) {
        tasks.showStreamModal(
          `🧠 Generador Contenido · ${_depthLabel(depth)}`,
          "NotebookLM"
        );
        tasks.startStreamPoll(
          resp.task_id,
          "kp-placeholder",    // no source block — handled via task.source_id
          "knowledge_pipeline",
          resolvedTopicId
        );
      } else {
        _showKpStatus("⚠️ Módulo de tareas no disponible", true);
      }
    } catch (err) {
      _showKpStatus(`❌ Error: ${err.message}`, true);
    }
  }

  // ─── Helpers ────────────────────────────────────────────────────

  function _depthLabel(depth) {
    return { concise: "Conciso", standard: "Estándar", detailed: "Detallado" }[depth] || depth;
  }

  function _showKpStatus(msg, isError) {
    // Try to show status in the add-block-bar area
    const bar = document.querySelector(".add-block-bar.body-bar");
    if (!bar) return;
    let statusEl = bar.querySelector(".kp-status");
    if (!statusEl) {
      statusEl = document.createElement("span");
      statusEl.className = "kp-status";
      bar.appendChild(statusEl);
    }
    statusEl.textContent = msg;
    statusEl.style.color = isError ? "#ef4444" : "#6b7280";
    if (!isError) {
      setTimeout(() => { if (statusEl.parentNode) statusEl.remove(); }, 5000);
    }
  }

  // ─── Public API ─────────────────────────────────────────────────

  window.App = window.App || {};
  window.App.KnowledgePipeline = { open: openKnowledgePipeline };
})();

console.log("[KnowledgePipeline] loaded");