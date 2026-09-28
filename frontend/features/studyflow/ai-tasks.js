// ─── AI Tasks — polling + streaming modal (v3) ─────────────────────
// Namespace: window.App.AI.Tasks
// Dependencies: window.API (global), window.App.AI (callbacks)
//
// Phase 7.7 — port from v2 ai-tasks.js (ai.js split). Backend format
// values in v3 differ from v2: tasks store format 'markdown'|'html'|
// 'md'|'test'|'audio'|'infographic' (not the v2 'nb_md'/'nb_html').
// The insert callbacks resolve the final block type from these formats.

window.App = window.App || {};
window.App.AI = window.App.AI || {};

window.App.AI.Tasks = (function () {
  "use strict";

  const _pollTimers = {};

  // ─── Stream moment meta (v3 formats) ─────────────────────────────
  const _STREAM_META = {
    markdown:    { title: "✨ NotebookLM generando Markdown", model: "NotebookLM" },
    html:        { title: "✨ NotebookLM generando HTML",     model: "NotebookLM" },
    md:          { title: "✨ Mejorando Markdown",            model: "NotebookLM" },
    test:        { title: "❓ Generando test",                model: "NotebookLM" },
    audio:       { title: "🎵 Generando Audio",               model: "OpenZEN + TTS" },
    infographic: { title: "📊 Generando Infografía",          model: "NotebookLM" },
    flashcards:  { title: "💳 Generando Flashcards",          model: "NotebookLM" },
  };

  // ─── Stream modal state ─────────────────────────────────────────
  const _streamState = {
    taskId: null,
    startTime: 0,
    fullContent: "",
    pollTimer: null,
  };

  function _esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function _ensureStreamModal() {
    if (document.getElementById("stream-overlay")) return;
    const overlay = document.createElement("div");
    overlay.id = "stream-overlay";
    overlay.style.cssText =
      "position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:9999;" +
      "display:flex;align-items:center;justify-content:center;";
    overlay.innerHTML = `
      <div id="stream-modal" style="
        width:min(680px,92vw);max-height:86vh;overflow:auto;
        background:#14141f;border:1px solid #333;border-radius:12px;
        padding:16px 20px;font-family:system-ui,sans-serif;color:#e6e6e6;">
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:4px;">
          <span id="stream-title" style="font-size:15px;font-weight:600;flex:1;"></span>
          <span id="stream-model" style="font-size:12px;color:#888;"></span>
        </div>
        <div id="stream-timer" style="font-size:12px;color:#888;margin-bottom:10px;"></div>
        <div id="stream-text" style="font-size:13px;line-height:1.5;white-space:pre-wrap;
             word-break:break-word;background:#0d0d17;border:1px solid #26263a;
             border-radius:8px;padding:12px;max-height:52vh;overflow:auto;"></div>
        <div id="stream-progress" style="margin-top:8px;"></div>
        <div style="display:flex;gap:10px;margin-top:14px;justify-content:flex-end;">
          <button id="stream-close-btn" style="padding:8px 14px;border-radius:8px;
            border:1px solid #444;background:transparent;color:#bbb;cursor:pointer;">✕ Cerrar</button>
          <button id="stream-insert-btn" style="display:none;padding:8px 14px;border-radius:8px;
            border:none;background:#4f8cff;color:#fff;cursor:pointer;font-weight:600;">📥 Insertar en el curso</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);

    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) _hideStreamModal();
    });
    overlay.querySelector("#stream-close-btn").addEventListener("click", () => {
      _hideStreamModal();
    });
    overlay.querySelector("#stream-insert-btn").addEventListener("click", () => {
      const insertBtn = overlay.querySelector("#stream-insert-btn");
      const cb = insertBtn._onInsert;
      insertBtn.style.display = "none";
      if (cb) cb().catch((err) => {
        console.error("[AI] insert failed:", err);
        const textEl = document.getElementById("stream-text");
        if (textEl) textEl.textContent = `❌ Error al insertar: ${err.message}`;
      });
    });
  }

  function _showStreamModal(title, modelName) {
    _ensureStreamModal();
    const overlay = document.getElementById("stream-overlay");
    const titleEl = document.getElementById("stream-title");
    const modelEl = document.getElementById("stream-model");
    if (titleEl) titleEl.textContent = title || "";
    if (modelEl) modelEl.textContent = modelName || "";
    const textEl = document.getElementById("stream-text");
    if (textEl) textEl.innerHTML = '<span class="stream-cursor">▊</span>';
    const progEl = document.getElementById("stream-progress");
    if (progEl) progEl.innerHTML = "";
    const timerEl = document.getElementById("stream-timer");
    if (timerEl) timerEl.textContent = "⏱️ 0:00";
    const insBtn = document.getElementById("stream-insert-btn");
    if (insBtn) { insBtn.style.display = "none"; insBtn._onInsert = null; }
    _streamState.startTime = Date.now();
    overlay.style.display = "flex";
    _tickTimer();
  }

  let _timerInterval = null;
  function _tickTimer() {
    if (_timerInterval) clearInterval(_timerInterval);
    _timerInterval = setInterval(() => {
      const timerEl = document.getElementById("stream-timer");
      if (!timerEl || !_streamState.startTime) return;
      const secs = Math.floor((Date.now() - _streamState.startTime) / 1000);
      timerEl.textContent = `⏱️ ${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")}`;
    }, 1000);
  }

  function _hideStreamModal() {
    if (_timerInterval) { clearInterval(_timerInterval); _timerInterval = null; }
    _streamState.startTime = 0;
    _streamState.fullContent = "";
    const overlay = document.getElementById("stream-overlay");
    if (overlay) overlay.style.display = "none";
    if (_streamState.pollTimer) {
      clearInterval(_streamState.pollTimer);
      _streamState.pollTimer = null;
    }
    _streamState.taskId = null;
  }

  /** Cancel a running task via the backend (fires when overlay closes). */
  async function _cancelTask(taskId) {
    if (!taskId) return;
    try {
      await window.API.post(`/ai/tasks/${taskId}/cancel`);
    } catch (err) {
      console.warn(`[AI] cancel ${taskId}:`, err.message);
    }
  }

  /** Kill any remaining poll timers (session teardown). */
  function stopAllPolls() {
    for (const [id, t] of Object.entries(_pollTimers)) {
      clearInterval(t);
      delete _pollTimers[id];
    }
    if (_streamState.pollTimer) {
      clearInterval(_streamState.pollTimer);
      _streamState.pollTimer = null;
    }
  }

  /** Legacy generic poll (inline status only — no modal). */
  function startPoll(taskId, statusKey, onSuccess) {
    if (_pollTimers[taskId]) clearInterval(_pollTimers[taskId]);
    const startTime = Date.now();
    _pollTimers[taskId] = setInterval(async () => {
      try {
        const task = await window.API.get(`/ai/tasks/${taskId}`);
        const ai = window.App.AI;
        if (task.status === "done") {
          clearInterval(_pollTimers[taskId]);
          delete _pollTimers[taskId];
          if (onSuccess) onSuccess(task);
          else if (ai && ai._showStatus) ai._showStatus(statusKey, "✅ Tarea completada", true);
        } else if (task.status === "error") {
          clearInterval(_pollTimers[taskId]);
          delete _pollTimers[taskId];
          if (ai && ai._showStatus) {
            ai._showStatus(statusKey, `❌ ${task.error_message || "Error desconocido"}`, true);
          }
        }
      } catch (err) {
        /* transient network error — keep polling */
      }
    }, 3000);
  }

  /**
   * Streaming poll: shows the modal, polls until done/error/cancelled,
   * then offers the Insert button that runs the caller-supplied callback.
   *
   * @param {string} taskId — ai_tasks UUID from the POST response
   * @param {string} blockId — source block id (insert target anchor)
   * @param {string} format — v3 task format (markdown|html|md|test|audio|infographic)
   * @param {string} topicId — topic to attach the result block to
   * @param {string} courseIdHint — course captured at generation start
   * @param {function} [onInsert] — override; defaults to ai._onContentSuccess / _onTestSuccess
   */
  function startStreamPoll(taskId, blockId, format, topicId, courseIdHint, onInsert) {
    _hideStreamModal();

    const meta = _STREAM_META[format] || {
      title: "✨ Task en progreso", model: ""
    };
    _showStreamModal(meta.title, meta.model);
    _streamState.taskId = taskId;

    // Capture courseId ONCE at poll start so the insert lands in the same
    // course where generation began, even if the user navigates.
    const capturedCourseId =
      courseIdHint ||
      (typeof STATE !== "undefined" && STATE.currentCourseId) ||
      "";

    const ai = window.App.AI;
    const defaultInsert = async (task) => {
      if (format === "test" && ai && ai._onTestSuccess) {
        await ai._onTestSuccess(task, blockId, topicId);
      } else if (ai && ai._onContentSuccess) {
        await ai._onContentSuccess(task, blockId, topicId, format, capturedCourseId);
      } else {
        throw new Error("AI module not loaded");
      }
    };

    const poll = async () => {
      try {
        const task = await window.API.get(`/ai/tasks/${taskId}`);
        const textEl = document.getElementById("stream-text");
        const titleEl = document.getElementById("stream-title");
        const progEl = document.getElementById("stream-progress");

        if (task.status === "cancelled") {
          clearInterval(_streamState.pollTimer);
          _streamState.pollTimer = null;
          if (titleEl) titleEl.textContent = "⚠️ Cancelado";
          if (textEl) textEl.textContent = "⚠️ Tarea cancelada";
          return;
        }
        if (task.status === "error") {
          clearInterval(_streamState.pollTimer);
          _streamState.pollTimer = null;
          const errMsg = task.error_message || "Error desconocido";
          if (titleEl) titleEl.textContent = "❌ Error en la tarea";
          if (textEl) textEl.innerHTML = `❌ ${_esc(errMsg)}`;
          if (ai && ai._showStatus) ai._showStatus(blockId, `❌ ${errMsg}`, true);
          return;
        }
        if (task.status === "processing") {
          if (progEl) {
            const checklist = task.error_message || "";
            progEl.innerHTML = checklist
              ? `<pre style="font-size:12px;color:#9ad;margin:0;">${_esc(checklist)}</pre>`
              : '<span style="font-size:12px;color:#888;">⏳ Procesando…</span>';
          }
          return; // keep polling
        }
        if (task.status === "done") {
          clearInterval(_streamState.pollTimer);
          _streamState.pollTimer = null;

          // Large content → fetch separately via content_url
          let finalContent = task.result_content || "";
          if (!finalContent && task.content_url) {
            try {
              const resp = await fetch(task.content_url);
              if (!resp.ok) throw new Error(`content fetch ${resp.status}`);
              finalContent = await resp.text();
              task.result_content = finalContent;
            } catch (err) {
              console.error("[AI] content fetch:", err.message);
            }
          }

          _streamState.fullContent = finalContent || "";
          if (textEl) textEl.textContent = finalContent || "(sin contenido)";

          if (titleEl) {
            const old = meta.title.replace(/^[^ ]+ /, "").replace(/generando/gi, "generado");
            titleEl.textContent = `✅ ${old}`;
          }

          // Resolve insert on this specific task
          const onDone = onInsert || defaultInsert;
          const insBtn = document.getElementById("stream-insert-btn");
          if (insBtn) {
            insBtn.style.display = "inline-block";
            insBtn._onInsert = () => onDone(task);
          }
          if (ai && ai._showStatus) ai._showStatus(blockId, "✅ Generado", true);
        }
      } catch (err) {
        /* transient — keep polling */
      }
    };

    _streamState.pollTimer = setInterval(poll, 2500);
    poll(); // first tick immediately
  }

  // ─── Public exports ─────────────────────────────────────────────
  return {
    startPoll,
    startStreamPoll,
    stopAllPolls,
    showStreamModal: _showStreamModal,
    hideStreamModal: _hideStreamModal,
    cancelTask: _cancelTask,
  };
})();

console.log("[AI.Tasks] loaded");