// ─── AI Generation — OpenZen (OpenCode) task launchers (v3) ─────────
// Namespace: window.App.AI.Generation (extendido)
// Dependencies: window.API, window.App.AI.Tasks, window.App.AI._showStatus
//
// Why a separate module: OpenZen splits the PDF into sections and generates
// each one on its own, so it can report WHICH section failed and regenerate
// just that one. NotebookLM cannot — it hands the whole document over in a
// single call.
//
// The PDF is the source of truth. "Extensa" here means more of the document,
// never material the model made up.

window.App = window.App || {};
window.App.AI = window.App.AI || {};

(function () {
  "use strict";

  const Gen = window.App.AI.Generation;
  const Tasks = () => window.App.AI.Tasks;
  const Status = (id, msg, persistent) => {
    const ai = window.App.AI;
    if (ai && ai._showStatus) ai._showStatus(id, msg, persistent);
  };

  // The route was renamed from pdf-to-markdown to source-to-markdown when the
  // backend started accepting Markdown blocks too. A browser can hold the new
  // JS against a server that has not reloaded yet, so fall back to the old
  // path instead of failing the click with a 405. Remove once every deployed
  // server answers on the new path.
  const SOURCE_ROUTES = ["/ai/openzen/source-to-markdown", "/ai/openzen/pdf-to-markdown"];

  // window.API surfaces the status as prose ("METHOD NOT ALLOWED"), not as a
  // number, so match both forms or the fallback silently never fires.
  const ROUTE_MISSING = /\b(404|405)\b|not found|not allowed/i;

  async function postSource(body) {
    let lastErr;
    for (const route of SOURCE_ROUTES) {
      try {
        return await window.API.post(route, body);
      } catch (err) {
        lastErr = err;
        if (!ROUTE_MISSING.test(err.message || String(err))) throw err;
      }
    }
    throw lastErr;
  }

  // ─── OpenZen source → Markdown (chunked per section) ─────────────
  // Works on a PDF block or a Markdown block; the backend picks the right
  // splitting strategy and treats the source as the only material.
  async function generateOpenzenMd(blockId, topicId, params = {}) {
    Status(blockId, "⏳ Preparando el material para OpenZen…");
    try {
      const resp = await postSource({
        block_id: blockId,
        topic_id: topicId || "",
        template_id: params.template_id || null,
        language: params.language || "auto",
        length: params.length || "detailed",
      });
      const tasks = Tasks();
      if (tasks) {
        // No showStreamModal() here: startStreamPoll hides and reopens the
        // panel from _STREAM_META["markdown"], which is NotebookLM's title.
        // The override is what actually decides the label.
        tasks.startStreamPoll(resp.task_id, blockId, "markdown", topicId || "",
          undefined, undefined,
          { title: "✍️ OpenZen generando Markdown", model: "OpenZen" });
      }
    } catch (err) {
      Status(blockId, `❌ Error: ${err.message}`, true);
    }
  }

  // ─── Retry ONE failed section ────────────────────────────────────
  // The warning offers this per section: re-run just that one, or leave it
  // as is. Everything else in the document is already generated and stays.
  async function retryOpenzenChunk(blockId, taskId, chunkNum, onDone) {
    Status(blockId, `♻️ Regenerando la sección ${chunkNum}…`);
    try {
      const resp = await window.API.post(
        `/ai/openzen/tasks/${encodeURIComponent(taskId)}/chunk/${chunkNum}/retry`,
        {}
      );
      const left = resp.remaining_failed || 0;
      Status(
        blockId,
        left
          ? `✅ Sección ${chunkNum} regenerada · ⚠️ quedan ${left} por reintentar`
          : `✅ Sección ${chunkNum} regenerada · documento completo`,
        true
      );
      if (typeof onDone === "function") onDone(resp);
      // Re-read the document so the regenerated section is actually shown.
      const tasks = Tasks();
      if (tasks && typeof tasks.refreshContent === "function") {
        tasks.refreshContent(taskId, blockId);
      }
    } catch (err) {
      Status(blockId, `❌ No se pudo regenerar la sección ${chunkNum}: ${err.message}`, true);
    }
  }

  // ─── Warning banner for sections that exhausted their retries ────
  //
  // Both buttons are non-blocking on purpose. The backend already moved past
  // the failed section, so "continue without it" needs no server round-trip:
  // it just means accepting the document that is already stored. Regenerating
  // is the only action that calls the API, and it only touches that one
  // section.
  function renderChunkWarning(taskId, state, blockId) {
    const failed = (state && state.failed_chunks) || [];
    if (!failed.length) return;

    const wrap = document.createElement("div");
    wrap.className = "ai-chunk-warning";
    // Spanish plural: "sección" → "secciones" (the accent is dropped), and the
    // verb agrees: "no se pudo generar" / "no se pudieron generar".
    const many = failed.length > 1;
    wrap.innerHTML =
      `<div class="ai-chunk-warning__head">⚠️ ${failed.length} ` +
      `${many ? "secciones no se pudieron" : "sección no se pudo"} generar. ` +
      `El resto del documento está completo.</div>`;

    // "Keep going without them" — the default outcome of the backend run.
    const skipAll = document.createElement("button");
    skipAll.className = "ai-btn ai-chunk-warning__skip";
    skipAll.textContent = "Continuar sin estas secciones";
    skipAll.addEventListener("click", () => {
      wrap.remove();
      const tasks = Tasks();
      if (tasks && typeof tasks.dismissChunkWarning === "function") {
        tasks.dismissChunkWarning(blockId);
      }
      Status(blockId, "ℹ️ Documento guardado sin esas secciones", true);
    });
    wrap.appendChild(skipAll);

    failed.forEach((f) => {
      const row = document.createElement("div");
      row.className = "ai-chunk-warning__row";
      row.innerHTML =
        `<span class="ai-chunk-warning__title">Sección ${f.chunk} · ${f.title}</span>`;

      const skip = document.createElement("button");
      skip.className = "ai-btn ai-chunk-warning__skip";
      skip.textContent = "Omitir esta";
      skip.addEventListener("click", () => row.remove());

      const btn = document.createElement("button");
      btn.className = "ai-btn ai-chunk-warning__retry";
      btn.textContent = "♻️ Regenerar esta sección";
      btn.addEventListener("click", () => {
        btn.disabled = true;
        skip.disabled = true;
        btn.textContent = "⏳ Regenerando…";
        retryOpenzenChunk(blockId, taskId, f.chunk, () => {
          row.remove();
          if (!wrap.querySelector(".ai-chunk-warning__row")) wrap.remove();
        });
      });
      row.appendChild(btn);
      row.appendChild(skip);
      wrap.appendChild(row);
    });

    return wrap;
  }

  // ─── Public exports ──────────────────────────────────────────────
  Gen.generateOpenzenMd = generateOpenzenMd;
  Gen.retryOpenzenChunk = retryOpenzenChunk;
  Gen.renderChunkWarning = renderChunkWarning;
})();
