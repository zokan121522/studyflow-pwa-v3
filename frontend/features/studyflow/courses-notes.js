// ─── Courses Notes — per-topic notes drawer (S4) ───────────────
// Namespace: window.App.CoursesNotes
// Dependencies: window.App.UI (escHtml), window.App.CoursesAPI,
//               window.App.ContentBlocks._renderMd.
//
// SCOPE:
//   • render() — fetch + inject a collapsible "📝 Notas" panel inside
//     a host element (the topic-detail center panel).
//   • Debounced autosave (600 ms after last keystroke, like agenda
//     habit-notes) + instant save on textarea blur.
//   • toggle()  — open/close the panel.
//   • Mounts only once per host element (idempotent — safe across
//     re-renders driven by studyflow:blocks-changed).
//
// All exported functions are pure DOM — no module-level state.

window.App = window.App || {};
window.App.CoursesNotes = (function () {
  "use strict";

  const { escHtml } = window.App.UI;
  const { getTopicNotes, saveTopicNotes } = window.App.CoursesAPI;
  const { _renderMd } = window.App.ContentBlocks;

  const SAVE_DEBOUNCE_MS = 600;

  // ── _panelHTML(courseId, topicId, content) ─────────────────────
  function _panelHTML(courseId, topicId, content) {
    const safe = escHtml(content || "");
    return `
      <section class="sf-notes-panel" data-course-id="${courseId}"
               data-topic-id="${topicId}">
        <header class="sf-notes-head">
          <span class="sf-notes-title">📝 Notas</span>
          <span class="sf-notes-status" data-role="status">—</span>
        </header>
        <div class="sf-notes-preview md-view" data-role="preview"></div>
        <textarea class="sf-notes-textarea" data-role="textarea"
                  rows="6" placeholder="Apuntes, ideas, resúmenes… (markdown)">${safe}</textarea>
        <div class="sf-notes-actions">
          <button class="sf-notes-toggle ht-btn-mini" data-role="toggle"
                  title="Mostrar / ocultar el editor">✏️ Editar</button>
          <button class="sf-notes-save ht-btn-mini" data-role="save"
                  title="Guardar ahora">💾 Guardar</button>
        </div>
      </section>`;
  }

  // ── _setStatus(panel, label) ───────────────────────────────────
  function _setStatus(panel, label) {
    const el = panel.querySelector('[data-role="status"]');
    if (el) el.textContent = label;
  }

  // ── _applyPreview(panel, content) ─────────────────────────────
  function _applyPreview(panel, content) {
    const prev = panel.querySelector('[data-role="preview"]');
    if (!prev) return;
    const trimmed = (content || "").trim();
    if (!trimmed) {
      prev.innerHTML = '<span class="sf-empty">Sin notas todavía.</span>';
      prev.style.display = "block";
    } else {
      prev.innerHTML = _renderMd(content || "");
      prev.style.display = "block";
    }
  }

  // ── _attachPanelHandlers(panel, courseId, topicId) ────────────
  // Debounced save: every input clears the pending timer and schedules
  // a fresh save in SAVE_DEBOUNCE_MS. blur fires an instant save.
  function _attachPanelHandlers(panel, courseId, topicId) {
    if (!panel || panel.dataset._sfNotesHandlers === "1") return;
    panel.dataset._sfNotesHandlers = "1";
    const ta = panel.querySelector('[data-role="textarea"]');
    const toggleBtn = panel.querySelector('[data-role="toggle"]');
    const saveBtn = panel.querySelector('[data-role="save"]');
    let debounceTimer = null;
    let inflight = null;

    async function persist(immediate) {
      if (!ta) return;
      const content = ta.value || "";
      if (immediate) {
        if (debounceTimer) {
          clearTimeout(debounceTimer);
          debounceTimer = null;
        }
      }
      _setStatus(panel, "⏳ Guardando…");
      try {
        inflight = saveTopicNotes(courseId, topicId, content);
        await inflight;
        _setStatus(panel, "✅ Guardado");
        _applyPreview(panel, content);
      } catch (err) {
        _setStatus(panel, "❌ Error: " + (err.message || err));
      } finally {
        inflight = null;
      }
    }

    if (ta) {
      ta.addEventListener("input", () => {
        if (debounceTimer) clearTimeout(debounceTimer);
        _setStatus(panel, "✍️ Escribiendo…");
        debounceTimer = setTimeout(() => persist(false), SAVE_DEBOUNCE_MS);
      });
      ta.addEventListener("blur", () => persist(true));
    }
    if (saveBtn) {
      saveBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        persist(true);
      });
    }
    if (toggleBtn) {
      toggleBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        const editing = panel.classList.toggle("is-editing");
        if (editing) {
          if (ta) ta.focus();
          toggleBtn.textContent = "👁 Vista previa";
        } else {
          // Switching back to preview → make sure the latest text is saved.
          persist(true);
          toggleBtn.textContent = "✏️ Editar";
        }
      });
    }
  }

  // ── render(hostEl, courseId, topicId) ──────────────────────────
  // Mount the panel inside hostEl. If a panel for this (courseId,
  // topicId) already exists, just refresh its preview; otherwise build
  // a new one and fetch the initial content.
  async function render(hostEl, courseId, topicId) {
    if (!hostEl) return;
    if (!courseId || !topicId) return;
    const key = `${courseId}:${topicId}`;
    let panel = hostEl.querySelector(
      `.sf-notes-panel[data-course-id="${courseId}"][data-topic-id="${topicId}"]`
    );
    if (!panel) {
      // Drop any panel for a DIFFERENT topic first (course switches).
      hostEl.querySelectorAll(".sf-notes-panel").forEach((p) => {
        if (p.dataset._sfNotesKey !== key) p.remove();
      });
      panel = document.createElement("div");
      panel.dataset._sfNotesKey = key;
      panel.innerHTML = _panelHTML(courseId, topicId, "");
      hostEl.appendChild(panel);
      _attachPanelHandlers(panel, courseId, topicId);
    }
    const ta = panel.querySelector('[data-role="textarea"]');
    try {
      const content = await getTopicNotes(courseId, topicId);
      if (ta && document.activeElement !== ta) ta.value = content || "";
      _applyPreview(panel, content || "");
      _setStatus(panel, content ? "✅ Guardado" : "—");
    } catch (err) {
      _setStatus(panel, "❌ Error al cargar");
    }
  }

  // ── toggle(hostEl, courseId, topicId) ─────────────────────────
  // Convenience wrapper: just flips the .is-editing class.
  function toggle(hostEl, courseId, topicId) {
    const panel = hostEl && hostEl.querySelector(
      `.sf-notes-panel[data-course-id="${courseId}"][data-topic-id="${topicId}"]`
    );
    if (!panel) return;
    panel.classList.toggle("is-editing");
  }

  // ── Public API ───────────────────────────────────────────────
  return { render, toggle, _panelHTML };
})();

console.log("[Studyflow] courses-notes.js loaded");