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
    audio:       { title: "🎵 Generando Audio",               model: "NotebookLM + TTS" },
    infographic: { title: "📊 Generando Infografía",          model: "NotebookLM" },
    flashcards:  { title: "💳 Generando Flashcards",          model: "NotebookLM" },
    knowledge_pipeline: { title: "🧠 Generador Contenido",    model: "NotebookLM" },
    grammar:     { title: "✏️ Generando English Exercises",   model: "NotebookLM" },
  };

  // ─── Stream panel state ──────────────────────────────────────────
  const _streamState = {
    taskId: null,
    startTime: 0,
    fullContent: "",
    pollTimer: null,
      done: false,        // completion reached (close = plain close, no cancel)
      lastChecklist: "",  // dedupe terminal lines from task.error_message
      restartPoll: null,  // re-arms the poll of the CURRENT task (chunk resume)
      chunkPending: false,// a recoverable chunk failure awaits a decision
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
          <div class="sf-ai-chunkbar" id="sf-ai-chunkbar" style="display:none"></div>
          <div class="sf-ai-preview" id="sf-ai-preview" style="display:none"></div>
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

  /**
   * Clear the thumbnail area. Called when a new stream task starts, because
   * _showStreamModal reuses one panel for every task and the previous
   * thumbnail would otherwise be read as this task's result.
   */
  function _resetResultPreview() {
    const box = document.getElementById("sf-ai-preview");
    if (!box) return;
    box.innerHTML = "";
    box.style.display = "none";
  }

  /**
   * Show a thumbnail of a finished image task, so the user can look at it
   * before choosing Insert.
   *
   * Only images. An infographic's result_content is a bare URL like
   * "/api/ai/notebooklm/infographic/xxx.png"; markdown/audio results are
   * prose and would be nonsense inside an <img>. Same-origin relative path
   * only — the value comes from a task row, so it is not interpolated blind.
   */
  function _showResultPreview(finalContent, format) {
    const box = document.getElementById("sf-ai-preview");
    if (!box) return;
    const value = String(finalContent || "").trim();
    const isImageUrl = format === "infographic"
      && /^\/api\/[\w/-]+\.(png|jpg|jpeg|gif|webp)$/i.test(value);
    if (!isImageUrl) return;
    box.innerHTML = "";
    const img = document.createElement("img");
    img.src = value;
    img.alt = "Vista previa de la infografía generada";
    img.loading = "lazy";
    // A broken preview must not look like a rendering failure of the whole
    // task: the image itself is already saved and Insertar still works.
    img.addEventListener("error", () => {
      box.innerHTML = '<div class="sf-ai-preview-err">No se pudo cargar la vista previa — la imagen sigue guardada.</div>';
    });
    box.appendChild(img);
    box.style.display = "block";
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
      _clearChunkBar();
      _streamState.restartPoll = null;
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

  /**
   * Freeze the timer where it stands. Called when the task reaches a terminal
   * state, not when the modal closes: until now the only clearInterval lived in
   * _hideStreamModal, so the clock kept counting for as long as the user took
   * to decide about the Insert button — and the number on screen was the time
   * they had spent *deciding*, not the time the generation took.
   */
  function _stopTimer(finished) {
    if (_timerInterval) { clearInterval(_timerInterval); _timerInterval = null; }
    const timerEl = document.getElementById("stream-timer");
    if (!timerEl || !finished) return;
    const secs = _streamState.startTime
      ? Math.floor((Date.now() - _streamState.startTime) / 1000) : 0;
    timerEl.textContent = `✅ ${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")}`;
    timerEl.classList.add("done");
    timerEl.title = "Tiempo de generación";
  }

    function _hideStreamModal() {
    if (_timerInterval) { clearInterval(_timerInterval); _timerInterval = null; }
    _streamState.startTime = 0;
    _streamState.fullContent = "";
    _streamState.done = false;
    _streamState.lastChecklist = "";
    _streamState.restartPoll = null;
    _streamState.chunkPending = false;
    _clearChunkBar();
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

    // ─── Chunk recovery (YouTubeZen detailed mode) ────────────────────
    // A detailed youtube_zen task generates one chunk per plan section.
    // When a chunk comes back empty the backend keeps the healthy parts,
    // the plan and the failed index (coverage_data.state === "chunk_error")
    // and exposes retry-chunk / continue-without. Without this the user saw
    // a red error box and silently lost the 20 chunks that already worked.
    // The backend has had both endpoints all along; nothing called them.

    const _CHUNK_PREFIX = "CHUNK_ERROR|";

    /**
     * Read the recoverable-chunk details out of a failed task.
     * @returns {?{idx:number, total:number, tries:number, maxTries:number,
     *             kept:number, reason:string}} null when the failure is fatal
     */
    function _chunkFailureInfo(task) {
      if (!task) return null;
      const msg = task.error_message || "";
      const cov = task.coverage_data;
      if (cov && cov.state === "chunk_error") {
        const reason = msg.indexOf(_CHUNK_PREFIX) === 0
          ? msg.split("|").slice(2).join("|")
          : msg;
        return {
          idx: (cov.failed_chunk || 0) + 1,   // stored 0-based, shown 1-based
          total: cov.num_chunks || 0,
          tries: cov.retry_count || 0,
          maxTries: cov.max_retries || 0,
          kept: (cov.parts || []).length,
          reason: reason || "respuesta vacía",
        };
      }
      // Fallback: the marker alone is enough to know it is resumable, e.g.
      // a task resumed from another tab whose coverage_data we did not get.
      if (msg.indexOf(_CHUNK_PREFIX) === 0) {
        const bits = msg.split("|");
        return {
          idx: (parseInt(bits[1], 10) || 0) + 1,
          total: 0, tries: 0, maxTries: 0, kept: 0,
          reason: bits.slice(2).join("|") || "respuesta vacía",
        };
      }
      return null;
    }

    function _clearChunkBar() {
      const bar = document.getElementById("sf-ai-chunkbar");
      if (bar) bar.style.display = "none";
      _streamState.chunkPending = false;
    }

    /** Render the retry / skip / discard row for a failed chunk. */
    function _showChunkBar(info) {
      const bar = document.getElementById("sf-ai-chunkbar");
      if (!bar) return;
      const where = info.total
        ? `chunk ${info.idx} de ${info.total}`
        : `chunk ${info.idx}`;
      const tries = info.maxTries
        ? ` <span class="sf-ai-chunkbar-tries">(intento ${info.tries + 1} de ${info.maxTries})</span>`
        : "";
      const kept = info.kept
        ? `<div class="sf-ai-chunkbar-kept">✅ ${info.kept === 1
            ? "La sección ya generada se conserva"
            : `Las ${info.kept} secciones ya generadas se conservan`} — no se vuelve a empezar el vídeo.</div>`
        : "";
      bar.innerHTML = `
        <div class="sf-ai-chunkbar-msg">
          <strong>⚠️ Falló el ${where}${tries}</strong>
          <span class="sf-ai-chunkbar-reason">${_esc(info.reason)}</span>
        </div>
        ${kept}
        <div class="sf-ai-chunkbar-btns">
          <button class="sf-ai-btn sf-ai-btn-primary" data-chunk="retry-chunk">🔁 Reintentar el chunk</button>
          <button class="sf-ai-btn" data-chunk="continue-without">⏭ Saltar y continuar</button>
          <button class="sf-ai-btn sf-ai-btn-danger" data-chunk="discard">✕ Descartar</button>
        </div>`;
      bar.style.display = "block";
      _streamState.chunkPending = true;
      bar.querySelectorAll("[data-chunk]").forEach((btn) => {
        btn.addEventListener("click", () => _onChunkAction(btn.dataset.chunk));
      });
    }

    /** Retry a single chunk, skip it, or abandon the task. */
    async function _onChunkAction(action) {
      const taskId = _streamState.taskId;
      if (!taskId) return;
      const bar = document.getElementById("sf-ai-chunkbar");
      const lock = (off) => {
        if (!bar) return;
        bar.querySelectorAll("button").forEach((b) => { b.disabled = off; });
      };
      lock(true);

      try {
        if (action === "discard") {
          _log("🚫", "Tarea descartada");
          _setLast("Descartado — nada se ha insertado en el curso", "warn");
          _clearChunkBar();
          return;
        }
        const isRetry = action === "retry-chunk";
        _log("▶", isRetry
          ? "🔁 Reintentando el chunk (se reutiliza el plan ya generado)…"
          : "⏭ Saltando el chunk y continuando con el resto…");
        _setLast(isRetry ? "Reintentando el chunk…" : "Continuando sin ese chunk…", "running");
        const titleEl = document.getElementById("stream-title");
        if (titleEl) titleEl.textContent = "▶ Reanudando la tarea…";
        const spinner = document.getElementById("stream-spinner");
        if (spinner) spinner.hidden = false;

        await window.API.post(`/ai/notebooklm/youtube-zen/${taskId}/${action}`);

        // The SAME task_id went back to "processing": reuse the original poll
        // closure so the captured courseId, format and insert callback survive.
        _clearChunkBar();
        if (_streamState.restartPoll) {
          _streamState.restartPoll();
        } else {
          _log("⚠️", "No se pudo reanudar el seguimiento; recarga el panel");
        }
      } catch (err) {
        console.error("[AI] chunk action failed:", err);
        const reason = err && err.message ? err.message : err;
        _log("❌", "No se pudo reanudar: " + reason);
        _setLast("No se pudo reanudar: " + reason, "err");
        lock(false);
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
   * @param {object} [opts] — {title, model} to override the label for this
   *   run. Needed because `_STREAM_META` is keyed by `format`, and two
   *   providers share the "markdown" format: without an override, OpenZen's
   *   own task is announced as "✨ NotebookLM generando Markdown".
   */
  function startStreamPoll(taskId, blockId, format, topicId, courseIdHint, onInsert, opts) {
    _hideStreamModal();
    // The modal is reused across tasks, so the previous task's thumbnail has
    // to go: left in place it would sit under the new task's log showing the
    // *old* result, and a stale "could not load" note would blame a task that
    // never had a preview.
    _resetResultPreview();

    const fallback = _STREAM_META[format] || {
      title: "✨ Task en progreso", model: ""
    };
    const meta = {
      title: (opts && opts.title) || fallback.title,
      model: (opts && opts.model) || fallback.model,
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
      } else if (format === "grammar" && window.App.EnglishGrammar &&
                 window.App.EnglishGrammar.onGrammarSuccess) {
        // English Grammar → interactive block (english-practice.html CONFIG)
        await window.App.EnglishGrammar.onGrammarSuccess(task, blockId, topicId);
      } else if (format === "vocabulary" && window.App.Vocabulary &&
                 window.App.Vocabulary.onVocabularySuccess) {
        // Vocabulary Suite → interactive block (vocabulary-suite.html CONFIG)
        await window.App.Vocabulary.onVocabularySuccess(task, blockId, topicId);
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
          _stopTimer(true);
          return;
        }
          if (task.status === "error") {
            clearInterval(_streamState.pollTimer);
            _streamState.pollTimer = null;
            const errMsg = task.error_message || "Error desconocido";
            if (spinner) spinner.hidden = true;
            _streamState.done = true;
            _stopTimer(true);

            // Recoverable chunk failure → offer retry / skip instead of a
            // dead end. The healthy chunks survive in coverage_data.
            const chunkInfo = _chunkFailureInfo(task);
            if (chunkInfo) {
              if (cancelBtn) cancelBtn.style.display = "none";
              if (titleEl) titleEl.textContent = "⚠️ Chunk fallido — decide cómo continuar";
              _log("⚠️", `El ${chunkInfo.total ? `chunk ${chunkInfo.idx} de ${chunkInfo.total}` : `chunk ${chunkInfo.idx}`} devolvió una respuesta vacía`);
              _showChunkBar(chunkInfo);
              _setLast("Chunk fallido — reinténtalo o sáltalo", "warn");
              if (ai && ai._showStatus) {
                ai._showStatus(blockId, `⚠️ Chunk ${chunkInfo.idx} sin respuesta — reintenta o sáltalo`, true);
              }
              return;
            }

            if (cancelBtn) cancelBtn.style.display = "none";
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
          // The generation is over: freeze the clock now, while the number is
          // still the generation time and not the user's thinking time.
          _stopTimer(true);

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

          // An infographic's result is a bare image URL, so there is nothing to
          // read in the log — only the "done" line. Show the image itself next
          // to the Insert button, before the user commits to it: generating
          // costs a NotebookLM run, and a thumbnail is the only way to judge
          // whether it was worth it.
          _showResultPreview(finalContent, format);

          // A task that is "done" may still be missing sections: the OpenZen
          // path skips a section that exhausted its retries and finishes with
          // the rest, because losing 8 of 9 sections beats losing all 9. The
          // user has to be told, and offered both ways out — regenerate the
          // section, or accept the document without it.
          const gen = ai && ai.Generation;
          if (gen && typeof gen.renderChunkWarning === "function") {
            let state = task.coverage_data;
            if (typeof state === "string") {
              try { state = JSON.parse(state); } catch (_) { state = null; }
            }
            if (state && (state.failed_chunks || []).length) {
              const bar = gen.renderChunkWarning(taskId, state, blockId);
              if (bar) {
                const host = document.getElementById("stream-log") ||
                             document.querySelector(".stream-modal__log");
                if (host) {
                  host.appendChild(bar);
                  bar.scrollIntoView({ block: "nearest" });
                }
                const n = state.failed_chunks.length;
                _log("⚠️", `${n} ${n > 1 ? "secciones no se pudieron" : "sección no se pudo"} generar — el resto del documento está completo`);
                _setLast(`⚠️ ${n} ${n > 1 ? "secciones" : "sección"} pendiente${n > 1 ? "s" : ""} — regenerar o continuar`, "warn");
              }
            }
          }

          // Resolve insert on this specific task
          const onDone = onInsert || defaultInsert;
          const insBtn = document.getElementById("stream-insert-btn");
          if (insBtn) {
            insBtn.style.display = "inline-block";
            insBtn._onInsert = () => onDone(task);
          }
          if (ai && ai._showStatus) {
            ai._showStatus(blockId, "✅ Generado", true);
            // Not left up forever: the user has the stream modal open with an
            // Insert button, so the banner only has to survive until they act.
            // _onContentSuccess clears it outright once the block lands.
            if (ai._clearStatus) setTimeout(() => ai._clearStatus(blockId), 6000);
          }
        }
      } catch (err) {
        /* transient — keep polling */
      }
    };

      // Re-arm the poll of THIS task. A retry-chunk / continue-without puts
      // the same task_id back into "processing", so resuming the same closure
      // keeps the captured courseId, the insert callback and the format — and
      // the checklist dedupe restarts, since the backend writes a fresh one.
      _streamState.restartPoll = () => {
        if (_streamState.pollTimer) {
          clearInterval(_streamState.pollTimer);
          _streamState.pollTimer = null;
        }
        _streamState.lastChecklist = "";
        _streamState.done = false;
        _streamState.chunkPending = false;
        const sp = document.getElementById("stream-spinner");
        if (sp) sp.hidden = false;
        _streamState.pollTimer = setInterval(poll, 2500);
        poll(); // first tick immediately
      };
      _streamState.restartPoll();
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