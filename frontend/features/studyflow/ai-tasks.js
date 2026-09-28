// ─── AI Tasks — polling + streaming panel (v3) ────────────────────
// Namespace: window.App.AI.Tasks
// Dependencies: window.API (global), window.App.AI (callbacks)
//
// Phase 7.7 — port from v2 ai-tasks.js (ai.js split). Backend format
// values in v3 differ from v2: tasks store format 'markdown'|'html'|
// 'md'|'test'|'audio'|'infographic' (not the v2 'nb_md'/'nb_html').
// The insert callbacks resolve the final block type from these formats.
//
// Phase 7.7c — compact floating panel instead of a blocking overlay.
//  - Small panel pinned bottom-right: the app stays usable behind it.
//  - Terminal log showing what the task is doing (deduped checklist).
//  - NEVER closes on outside click (only close/cancel/collapse buttons).
//  - Persistent spinner + last-state line while processing.

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

  // ─── Stream panel state ──────────────────────────────────────────
  const _streamState = {
    taskId: null,
    startTime: 0,
    fullContent: "",
    pollTimer: null,
    done: false,        // completion reached (close = plain close, no cancel)
    lastChecklist: "",  // dedupe terminal lines from task.error_message
  };

  function _esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  // ─── Panel construction ──────────────────────────────────────────
  function _ensureStreamModal() {
    const existing = document.getElementById("sf-ai-panel");
    if (existing) return existing;
    const panel = document.createElement("div");
    panel.id = "sf-ai-panel";
    panel.className = "sf-ai-panel";
    panel.innerHTML = `
      <div class="sf-ai-p-head">
        <span class="sf-ai-p-title" id="stream-title"></span>
        <span class="sf-ai-spinner" id="stream-spinner" hidden></span>
        <span class="sf-ai-p-btns">
          <button class="sf-ai-p-btn" id="stream-collapse-btn" title="Colapsar">—</button>
          <button class="sf-ai-p-btn" id="stream-close-btn" title="Cerrar">✕</button>
        </span>
      </div>
      <div class="sf-ai-p-body">
        <div class="sf-ai-last" id="sf-ai-last"></div>
        <div class="sf-ai-log" id="sf-ai-log"></div>
        <div class="sf-ai-actions">
          <button class="sf-ai-btn sf-ai-btn-danger" id="stream-cancel-btn">⏹ Cancelar</button>
          <span class="sf-ai-actions-right">
            <span class="sf-ai-timer" id="stream-timer">⏱️ 0:00</span>
            <button class="sf-ai-btn sf-ai-btn-primary" id="stream-insert-btn" style="display:none">📥 Insertar en el curso</button>
          </span>
        </div>
      </div>`;
    document.body.appendChild(panel);

    // Collapse → keep header (spinner + title) visible, body hidden.
    panel.querySelector("#stream-collapse-btn").addEventListener("click", () => {
      panel.classList.toggle("collapsed");
    });

    // Close: during a running task asks for confirmation (cancel + close).
    panel.querySelector("#stream-close-btn").addEventListener("click", async () => {
      if (!_streamState.done && _streamState.taskId) {
        if (!window.confirm("¿Cancelar la tarea en curso y cerrar?")) return;
        await _cancelTask(_streamState.taskId);
        _log("⚠️", "Tarea cancelada — panel cerrado");
      }
      _hideStreamModal();
    });

    // Cancel button → explicit backend cancel (poll confirms "cancelled").
    panel.querySelector("#stream-cancel-btn").addEventListener("click", () => {
      _log("⚠️", "Cancelando…");
      _setLast("Cancelando…", "warn");
      _cancelTask(_streamState.taskId);
    });

    // Insert → runs caller callback, then closes the panel briefly.
    panel.querySelector("#stream-insert-btn").addEventListener("click", () => {
      const insertBtn = panel.querySelector("#stream-insert-btn");
      const cb = insertBtn._onInsert;
      insertBtn.style.display = "none";
      if (!cb) return;
      _log("📥", "Insertando en el curso…");
      _setLast("Insertando…", "running");
      cb().then(() => {
        _log("✅", "Contenido insertado");
        _setLast("Insertado en el curso", "ok");
        setTimeout(_hideStreamModal, 900);
      }).catch((err) => {
        console.error("[AI] insert failed:", err);
        _log("❌", "Error al insertar: " + (err && err.message ? err.message : err));
        _setLast("Error al insertar", "err");
        insertBtn.style.display = "inline-block";
      });
    });
    return panel;
  }

  // ─── Terminal helpers ────────────────────────────────────────────
  function _log(icon, text) {
    const logEl = document.getElementById("sf-ai-log");
    if (!logEl) return;
    const line = document.createElement("div");
    line.className = "sf-ai-log-line";
    const t = new Date();
    const ts = [t.getHours(), t.getMinutes(), t.getSeconds()]
      .map((n) => String(n).padStart(2, "0")).join(":");
    line.innerHTML = `<span class="sf-ai-log-t">${ts}</span><span class="sf-ai-log-i">${_esc(icon)}</span><span class="sf-ai-log-x">${_esc(text)}</span>`;
    logEl.appendChild(line);
    while (logEl.childElementCount > 200) logEl.removeChild(logEl.firstChild);
    logEl.scrollTop = logEl.scrollHeight;
  }

  function _setLast(text, kind) {
    const lastEl = document.getElementById("sf-ai-last");
    if (!lastEl) return;
    lastEl.textContent = text;
    lastEl.className = "sf-ai-last " + (kind || "");
  }

  function _showStreamModal(title, modelName) {
    _ensureStreamModal();
    const panel = document.getElementById("sf-ai-panel");
    if (!panel) return;
    panel.classList.remove("collapsed");
    const titleEl = document.getElementById("stream-title");
    if (titleEl) titleEl.textContent = title || "";
    const lastEl = document.getElementById("sf-ai-last");
    if (lastEl) _setLast(modelName ? `Modelo: ${modelName}` : "Iniciando…", "running");
    const logEl = document.getElementById("sf-ai-log");
    if (logEl) logEl.innerHTML = "";
    _log("▸", title || "Tarea iniciada");
    const spinner = document.getElementById("stream-spinner");
    if (spinner) spinner.hidden = false;
    const cancelBtn = document.getElementById("stream-cancel-btn");
    if (cancelBtn) cancelBtn.style.display = "inline-block";
    const insBtn = document.getElementById("stream-insert-btn");
    if (insBtn) { insBtn.style.display = "none"; insBtn._onInsert = null; }
    const timerEl = document.getElementById("stream-timer");
    if (timerEl) timerEl.textContent = "⏱️ 0:00";
    _streamState.done = false;
    _streamState.lastChecklist = "";
    _streamState.startTime = Date.now();
    panel.style.display = "block";
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
    _streamState.done = false;
    _streamState.lastChecklist = "";
    const panel = document.getElementById("sf-ai-panel");
    if (panel) panel.style.display = "none";
    if (_streamState.pollTimer) {
      clearInterval(_streamState.pollTimer);
      _streamState.pollTimer = null;
    }
    _streamState.taskId = null;
  }

  /** Cancel a running task via the backend. */
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

  /** Legacy generic poll (inline status only — no panel). */
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
   * Streaming poll: shows the floating panel, polls until done/error/
   * cancelled, logs steps to the terminal and offers the Insert button
   * that runs the caller-supplied callback.
   *
   * The panel NEVER closes on outside click — only via Cancel / ✕ /
   * collapse, so a running task always shows visible feedback.
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
        const titleEl = document.getElementById("stream-title");
        const spinner = document.getElementById("stream-spinner");
        const cancelBtn = document.getElementById("stream-cancel-btn");

        if (task.status === "cancelled") {
          clearInterval(_streamState.pollTimer);
          _streamState.pollTimer = null;
          if (spinner) spinner.hidden = true;
          if (cancelBtn) cancelBtn.style.display = "none";
          _streamState.done = true; // already cancelled — close without confirm
          if (titleEl) titleEl.textContent = "⚠️ Cancelado";
          _log("⚠️", "Tarea cancelada");
          _setLast("Cancelado", "warn");
          return;
        }
        if (task.status === "error") {
          clearInterval(_streamState.pollTimer);
          _streamState.pollTimer = null;
          const errMsg = task.error_message || "Error desconocido";
          if (spinner) spinner.hidden = true;
          if (cancelBtn) cancelBtn.style.display = "none";
          _streamState.done = true;
          if (titleEl) titleEl.textContent = "❌ Error en la tarea";
          _log("❌", errMsg);
          _setLast("Error en la tarea", "err");
          if (ai && ai._showStatus) ai._showStatus(blockId, `❌ ${errMsg}`, true);
          return;
        }
        if (task.status === "processing") {
          // Backend feeds the current step in error_message (checklist) —
          // log each NEW step once, keep the last-state line updated.
          const checklist = task.error_message || "";
          if (checklist && checklist !== _streamState.lastChecklist) {
            _streamState.lastChecklist = checklist;
            _log("⏳", checklist);
          }
          _setLast(checklist || "Procesando…", "running");
          return; // keep polling
        }
        if (task.status === "done") {
          clearInterval(_streamState.pollTimer);
          _streamState.pollTimer = null;
          _streamState.done = true;
          if (spinner) spinner.hidden = true;
          if (cancelBtn) cancelBtn.style.display = "none";

          // Large content → fetch separately via content_url
          _log("⬇️", "Descargando contenido…");
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
          _log("✅", "Tarea completada" + (finalContent ? ` (${finalContent.length} caracteres)` : ""));

          if (titleEl) {
            const old = meta.title.replace(/^[^ ]+ /, "").replace(/generando/gi, "generado");
            titleEl.textContent = `✅ ${old}`;
          }
          _setLast("Listo — revisa y pulsa Insertar", "ok");

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