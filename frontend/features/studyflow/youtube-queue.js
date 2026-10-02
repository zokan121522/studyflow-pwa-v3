// YouTube Zen FIFO queue — floating progress panel (Phase 58, issue #225)
//
// Non-blocking floating panel (bottom-right). Shows the user's queue state
// for youtube_zen tasks (⏳ queued → 🔄 processing → ✅ done / ❌ error) by
// polling GET /ai/notebooklm/youtube-zen/queue/status every 4s.
//
// When a task reaches 'done', the panel automatically inserts the generated
// block by reusing the existing pipeline (window.App.AI._onContentSuccess +
// renderStudyflow) exactly like the legacy stream modal does.
//
// Public API: window.App.YoutubeQueue = { open(taskIds), close() }
(function () {
  const POLL_MS = 4000;
  const CSS = `
    #yt-queue-panel {
      position: fixed; right: 16px; bottom: 16px; z-index: 9999;
      width: 360px; max-width: calc(100vw - 32px);
      background: var(--surface-raised, #232331);
      border: 1px solid var(--border, #3a3a4f);
      border-radius: 10px; box-shadow: 0 8px 28px rgba(0,0,0,.45);
      font: 13px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif;
      color: var(--text-primary, #e8e8f0); overflow: hidden;
    }
    #yt-queue-panel .ytq-header {
      display: flex; align-items: center; gap: 8px;
      padding: 10px 12px; cursor: pointer; user-select: none;
      background: var(--surface-hover, #2b2b3d); border-bottom: 1px solid var(--border, #3a3a4f);
    }
    #yt-queue-panel .ytq-title { font-weight: 600; font-size: 13px; }
    #yt-queue-panel .ytq-count {
      margin-left: auto; font-size: 11px; color: var(--text-muted, #9a9ab0);
      background: var(--surface-dim, #1a1a26); padding: 2px 8px; border-radius: 10px;
    }
    #yt-queue-panel .ytq-close {
      background: none; border: none; color: var(--text-muted, #9a9ab0);
      cursor: pointer; font-size: 15px; line-height: 1; padding: 2px 4px;
    }
    #yt-queue-panel .ytq-close:hover { color: #fff; }
    #yt-queue-panel .ytq-chevron { font-size: 10px; color: var(--text-muted, #9a9ab0); }
    #yt-queue-panel .ytq-body { max-height: 45vh; overflow-y: auto; padding: 8px; display: flex; flex-direction: column; gap: 6px; }
    #yt-queue-panel .ytq-item {
      display: flex; align-items: flex-start; gap: 8px;
      background: var(--surface-dim, #1a1a26);
      border: 1px solid transparent; border-radius: 8px; padding: 8px 10px;
    }
    #yt-queue-panel .ytq-item.ytq-error { border-color: rgba(231,76,60,.45); }
    #yt-queue-panel .ytq-icon { font-size: 15px; line-height: 1.3; }
    #yt-queue-panel .ytq-cancel {
      background: none; border: none; color: var(--text-muted, #9a9ab0);
      cursor: pointer; font-size: 13px; line-height: 1; padding: 2px 4px;
      border-radius: 4px; flex-shrink: 0; margin-left: auto;
    }
    #yt-queue-panel .ytq-cancel:hover { color: #e74c3c; background: rgba(231,76,60,.12); }
    #yt-queue-panel .ytq-info { flex: 1; min-width: 0; }
    #yt-queue-panel .ytq-label {
      font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-size: 12px;
    }
    #yt-queue-panel .ytq-meta { font-size: 11px; color: var(--text-muted, #9a9ab0); margin-top: 2px; }
    #yt-queue-panel .ytq-meta.ytq-errmsg { color: #e74c3c; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    #yt-queue-panel .ytq-empty { color: var(--text-muted, #9a9ab0); text-align: center; padding: 14px 8px; font-size: 12px; }
  `;

  let _timer = null;
  let _open = false;
  let _collapsed = false;
  let _firstPoll = false; // first poll after open() marks existing backlog as seen
  const _inserted = new Set(); // task ids already auto-inserted (anti-dup)

  function _injectStyles() {
    if (document.getElementById("ytq-styles")) return;
    const style = document.createElement("style");
    style.id = "ytq-styles";
    style.textContent = CSS;
    document.head.appendChild(style);
  }

  function _mount() {
    _injectStyles();
    const el = document.createElement("div");
    el.id = "yt-queue-panel";
    el.innerHTML = `
      <div class="ytq-header" id="ytq-header">
        <span class="ytq-chevron" id="ytq-chevron">▼</span>
        <span class="ytq-title">🎬 YouTube Zen · Cola FIFO</span>
        <span class="ytq-count" id="ytq-count">—</span>
        <button class="ytq-close" id="ytq-close" title="Cerrar panel (la cola sigue)">✕</button>
      </div>
      <div class="ytq-body" id="ytq-body" style="display:block;"></div>`;
    document.body.appendChild(el);

    document.getElementById("ytq-header").addEventListener("click", (e) => {
      if (e.target.closest("#ytq-close")) return;
      _collapsed = !_collapsed;
      const body = document.getElementById("ytq-body");
      body.style.display = _collapsed ? "none" : "block";
      document.getElementById("ytq-chevron").textContent = _collapsed ? "▶" : "▼";
    });
    document.getElementById("ytq-close").addEventListener("click", () => close());

    // Event delegation: ✕ cancel per item. Items are re-rendered every
    // poll, so we delegate on the persistent body element.
    document.getElementById("ytq-body").addEventListener("click", async (e) => {
      const btn = e.target.closest("[data-cancel-id]");
      if (!btn) return;
      const taskId = btn.getAttribute("data-cancel-id");
      btn.disabled = true;
      btn.textContent = "…";
      try {
        await API.post(`/ai/tasks/${taskId}/cancel`);
      } catch (err) {
        console.error("[ytq] cancel failed for", taskId, err.message);
        btn.disabled = false;
        btn.textContent = "✕";
      }
      // No re-render needed here — the next poll reflects the new state.
    });
  }

  function open(taskIds) {
    if (!_open) _mount();
    _open = true;
    _firstPoll = true;
    _poll();
    if (_timer) clearInterval(_timer);
    _timer = setInterval(_poll, POLL_MS);
  }

  function close() {
    _open = false;
    if (_timer) clearInterval(_timer);
    _timer = null;
    const el = document.getElementById("yt-queue-panel");
    if (el) el.remove();
  }

  const STATUS_ICON = { queued: "⏳", processing: "🔄", done: "✅", error: "❌" };
  const STATUS_LABEL = {
    queued: "En cola",
    processing: "Procesando",
    done: "Insertado",
    error: "Error",
  };

  function _statusLabel(item) {
    const base = STATUS_LABEL[item.status] || item.status;
    const bits = [];
    if (item.mode) bits.push(item.mode === "por_tema" ? "por tema" : "un tema");
    if (item.depth) bits.push(item.depth);
    if (item.language) bits.push(item.language.toUpperCase());
    return bits.length ? `${base} · ${bits.join(" · ")}` : base;
  }

  function _renderItems(list) {
    const body = document.getElementById("ytq-body");
    if (!body) return;
    const total = list.length;
    document.getElementById("ytq-count").textContent = total ? `${total} tarea${total > 1 ? "s" : ""}` : "vacía";

    if (!list.length) {
      body.innerHTML = `<div class="ytq-empty">🇪🇸 Cola vacía — encola vídeos desde 🎬 YouTube → Contenido</div>`;
      return;
    }

    body.innerHTML = list
      .map((item) => {
        const icon = STATUS_ICON[item.status] || "•";
        const label = item.title || item.id.slice(0, 8);
        const meta = item.status === "error"
          ? `<div class="ytq-meta ytq-errmsg">${String(item.error_message || "Error desconocido").slice(0, 90)}</div>`
          : `<div class="ytq-meta">${_statusLabel(item)}</div>`;
        // Cancel button only for live states (queued/processing) — done/error are terminal.
        const cancelBtn = (item.status === "queued" || item.status === "processing")
          ? `<button class="ytq-cancel" data-cancel-id="${item.id}" title="Cancelar tarea">✕</button>`
          : "";
        return `<div class="ytq-item${item.status === "error" ? " ytq-error" : ""}">
          <span class="ytq-icon">${icon}</span>
          <div class="ytq-info"><div class="ytq-label" title="${label}">${label}</div>${meta}</div>
          ${cancelBtn}
        </div>`;
      })
      .join("");
  }

  async function _poll() {
    if (!_open) return;
    let data;
    try {
      const resp = await API.get("/ai/notebooklm/youtube-zen/queue/status");
      data = resp;
    } catch (err) {
      console.error("[ytq] status poll failed:", err.message);
      return;
    }

    // First poll after open(): mark any already-done backlog as "seen" so a
    // fresh page load (or reconnect) never re-inserts old blocks.
    // Subsequent polls auto-insert tasks that reach 'done' for the first time.
    if (_firstPoll) {
      _firstPoll = false;
      (data.recent_done || []).forEach((item) => _inserted.add(item.id));
    } else {
      for (const item of data.recent_done || []) {
        if (_inserted.has(item.id)) continue;
        _inserted.add(item.id);
        try {
          const ai = window.App.AI;
          if (ai && typeof ai._onContentSuccess === "function") {
            await ai._onContentSuccess(item, item.block_id || "", item.topic_id || "", "ytd_zen");
          }
        } catch (err) {
          console.error("[ytq] insert failed for", item.id, err.message);
        }
      }
    }

    const list = [
      ...(data.queued || []),
      ...(data.processing || []),
      ...(data.recent_done || []),
      ...(data.recent_error || []),
    ];
    _renderItems(list);
  }

  window.App = window.App || {};
  window.App.YoutubeQueue = { open, close };
  console.log("[youtube-queue.js] panel ready — window.App.YoutubeQueue");
})();