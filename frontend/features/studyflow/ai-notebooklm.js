// ─── AI Generation — NotebookLM task launchers (v3) ───────────────
// Namespace: window.App.AI.Generation
// Dependencies: window.API, window.App.AI.Tasks, window.App.AI._showStatus
//
// Phase 7.7 — port from v2 ai-generation.js. Only generators backed by
// v3 backend endpoints are included (OpenZEN/Ollama flows deferred).
// Each launcher POSTs the task and hands the poll to App.AI.Tasks.

window.App = window.App || {};
window.App.AI = window.App.AI || {};

window.App.AI.Generation = (function () {
  "use strict";

  const Tasks = () => window.App.AI.Tasks;
  const Status = (id, msg, persistent) => {
    const ai = window.App.AI;
    if (ai && ai._showStatus) ai._showStatus(id, msg, persistent);
  };

  // ─── NotebookLM PDF → Markdown (with template/language/length) ──
  async function generateNbMd(blockId, topicId, params = {}) {
    Status(blockId, "⏳ Generando Markdown con NotebookLM…");
    try {
      const resp = await window.API.post("/ai/notebooklm/pdf-to-markdown", {
        block_id: blockId,
        topic_id: topicId || "",
        template_id: params.template_id || null,
        language: params.language || "auto",
        length: params.length || "standard",
      });
      const tasks = Tasks();
      if (tasks) {
        tasks.showStreamModal("✨ NotebookLM generando Markdown", "NotebookLM");
        tasks.startStreamPoll(resp.task_id, blockId, "markdown", topicId || "");
      }
    } catch (err) {
      Status(blockId, `❌ Error: ${err.message}`, true);
    }
  }

  // ─── NotebookLM PDF → HTML ─────────────────────────────────────
  async function generateNbHtml(blockId, topicId) {
    Status(blockId, "⏳ Generando HTML con NotebookLM…");
    try {
      const resp = await window.API.post("/ai/notebooklm/pdf-to-html", {
        block_id: blockId,
        topic_id: topicId || "",
      });
      const tasks = Tasks();
      if (tasks) {
        tasks.showStreamModal("✨ NotebookLM generando HTML", "NotebookLM");
        tasks.startStreamPoll(resp.task_id, blockId, "html", topicId || "");
      }
    } catch (err) {
      Status(blockId, `❌ Error: ${err.message}`, true);
    }
  }

  // ─── Enhance existing Markdown via Gemini ──────────────────────
  async function generateNbMdFromContent(blockId, topicId, params = {}) {
    Status(blockId, "⏳ Mejorando Markdown con Gemini…");
    try {
      const resp = await window.API.post("/ai/notebooklm/enhance-markdown", {
        block_id: blockId,
        topic_id: topicId || "",
        template_id: params.template_id || null,
        language: params.language || "auto",
        length: params.length || "standard",
      });
      const tasks = Tasks();
      if (tasks) {
        tasks.showStreamModal("✨ Mejorando Markdown", "NotebookLM");
        tasks.startStreamPoll(resp.task_id, blockId, "md", topicId || "");
      }
    } catch (err) {
      Status(blockId, `❌ Error: ${err.message}`, true);
    }
  }

  // ─── Convert Markdown → HTML via Gemini ────────────────────────
  async function generateNbHtmlFromContent(blockId, topicId) {
    Status(blockId, "⏳ Convirtiendo Markdown a HTML con Gemini…");
    try {
      const resp = await window.API.post("/ai/notebooklm/markdown-to-html", {
        block_id: blockId,
        topic_id: topicId || "",
      });
      const tasks = Tasks();
      if (tasks) {
        tasks.showStreamModal("✨ Markdown → HTML", "NotebookLM");
        tasks.startStreamPoll(resp.task_id, blockId, "html", topicId || "");
      }
    } catch (err) {
      Status(blockId, `❌ Error: ${err.message}`, true);
    }
  }

  // ─── YouTube → Markdown (native NotebookLM ingestion) ──────────
  async function youtubeToMd(blockId, topicId, url) {
    Status(blockId, "⏳ Procesando YouTube…");
    try {
      const resp = await window.API.post("/ai/notebooklm/youtube-to-markdown", {
        url,
        block_id: blockId || "",
        topic_id: topicId || "",
      });
      const tasks = Tasks();
      if (tasks) {
        tasks.showStreamModal("🎬 YouTube → Markdown", "NotebookLM");
        tasks.startStreamPoll(resp.task_id, blockId, "markdown", topicId || "");
      }
    } catch (err) {
      Status(blockId, `❌ Error: ${err.message}`, true);
    }
  }

  // ─── YouTube → HTML (native NotebookLM ingestion) ──────────────
  async function youtubeToHtml(blockId, topicId, url) {
    Status(blockId, "⏳ Procesando YouTube…");
    try {
      const resp = await window.API.post("/ai/notebooklm/youtube-to-html", {
        url,
        block_id: blockId || "",
        topic_id: topicId || "",
      });
      const tasks = Tasks();
      if (tasks) {
        tasks.showStreamModal("🎬 YouTube → HTML", "NotebookLM");
        tasks.startStreamPoll(resp.task_id, blockId, "html", topicId || "");
      }
    } catch (err) {
      Status(blockId, `❌ Error: ${err.message}`, true);
    }
  }

  // ─── YouTubeZen — local yt-dlp + OpenZEN structuring (Phase 8) ──────

  /**
   * Single-URL YouTubeZen: yt-dlp extracts subtitles locally, OpenZEN
   * structures them into markdown. Legacy flow → stream modal + insert.
   *
   * @param {string} blockId    — block to update ("" for a new one)
   * @param {string} topicId    — topic to associate
   * @param {string} url        — YouTube video URL
   * @param {string} fmt        — 'markdown' | 'html'
   * @param {string} depth      — 'concise' | 'standard' | 'detailed'
   * @param {string} mode       — 'unitema' | 'por_tema'
   * @param {string} language   — 'es' | 'en'
   * @param {string} templateId — optional MD_TEMPLATES template id
   */
  async function youtubeZen(blockId, topicId, url, fmt, depth, mode, language, templateId) {
    Status(blockId, "⏳ YouTubeZen: extrayendo subtítulos…");
    try {
      const resp = await window.API.post("/ai/notebooklm/youtube-zen", {
        url,
        block_id: blockId || "",
        topic_id: topicId || "",
        format: fmt || "markdown",
        depth: depth || "standard",
        mode: mode || "unitema",
        language: language || "es",
        template_id: templateId || null,
      });
      const tasks = Tasks();
      if (tasks) {
        const label = fmt === "html" ? "🤖 YouTubeZen → HTML" : "🤖 YouTubeZen → Markdown";
        tasks.showStreamModal(label, "OpenZEN");
        tasks.startStreamPoll(resp.task_id, blockId, "ytd_zen", topicId || "", depth);
      }
    } catch (err) {
      Status(blockId, `❌ Error: ${err.message}`, true);
    }
  }

  /**
   * Enqueue multiple YouTube URLs as FIFO tasks. A single backend worker
   * processes them one at a time; the floating queue panel
   * (youtube-queue.js) shows progress and inserts each block on completion.
   *
   * @param {string[]} urls — up to 20 YouTube video URLs
   */
  async function youtubeZenQueue(urls, topicId, fmt, depth, mode, language, templateId) {
    try {
      const resp = await window.API.post("/ai/notebooklm/youtube-zen/queue", {
        urls,
        topic_id: topicId || "",
        format: fmt || "markdown",
        depth: depth || "standard",
        mode: mode || "unitema",
        language: language || "es",
        template_id: templateId || null,
      });
      const queue = window.App.YoutubeQueue;
      if (queue && queue.open) queue.open(resp.task_ids || []);
    } catch (err) {
      Status("", `❌ Error encolando: ${err.message}`, true);
    }
  }

  // ─── NotebookLM Test (Gemini Chat API) ─────────────────────────
  async function generateNbTest(blockIds, topicId, numQuestions = 10) {
    const ids = Array.isArray(blockIds) ? blockIds : [blockIds];
    const firstId = ids[0] || "";
    Status(firstId, `⏳ Generando test (${numQuestions} preguntas)…`);
    try {
      const resp = await window.API.post("/ai/notebooklm/generate-test", {
        block_ids: ids,
        topic_id: topicId || "",
        num_questions: numQuestions,
      });
      const tasks = Tasks();
      if (tasks) {
        tasks.showStreamModal("❓ Generando test", "NotebookLM");
        tasks.startStreamPoll(resp.task_id, firstId, "test", topicId || "");
      }
    } catch (err) {
      Status(firstId, `❌ Error: ${err.message}`, true);
    }
  }

  // ─── Infographic (NotebookLM artifact → PNG) ───────────────────
  async function generateInfographic(blockIds, topicId, params = {}) {
    const ids = Array.isArray(blockIds) ? blockIds : [blockIds];
    const firstId = ids[0] || "";
    Status(firstId, "⏳ Generando infografía…");
    try {
      const resp = await window.API.post("/ai/notebooklm/infographic", {
        block_ids: ids,
        topic_id: topicId || "",
        orientation: params.orientation || "landscape",
        detail_level: params.detail_level || "standard",
        style: params.style || "auto",
        instructions: params.instructions || "",
        language: params.language || "auto",
      });
      const tasks = Tasks();
      if (tasks) {
        tasks.showStreamModal("📊 Generando Infografía", "NotebookLM");
        tasks.startStreamPoll(resp.task_id, firstId, "infographic", topicId || "");
      }
    } catch (err) {
      Status(firstId, `❌ Error: ${err.message}`, true);
    }
  }

  // ─── Audio (OpenZEN script + edge-tts) ─────────────────────────
  async function generateAudio(blockIds, topicId, language = "es", duration = "complete") {
    const ids = Array.isArray(blockIds) ? blockIds : [blockIds];
    const firstId = ids[0] || "";
    const langLabel = language === "en" ? "English" : "Español";
    const durLabel = duration === "complete" ? "Completo" : `${duration} min`;
    Status(firstId, `⏳ Generando audio (${durLabel}, ${langLabel})…`);
    try {
      const resp = await window.API.post("/ai/notebooklm/generate-audio", {
        block_ids: ids,
        topic_id: topicId || "",
        language,
        duration,
        provider: "opencode-acp",
      });
      const tasks = Tasks();
      if (tasks) {
        tasks.showStreamModal("🎵 Generando Audio", "OpenZEN + TTS");
        tasks.startStreamPoll(resp.task_id, firstId, "audio", topicId || "");
      }
    } catch (err) {
      Status(firstId, `❌ Error: ${err.message}`, true);
    }
  }

  // ─── Audio (NotebookLM/Gemini script + edge-tts) ───────────────
  async function generateNbAudio(blockIds, topicId, language = "es", duration = "complete") {
    const ids = Array.isArray(blockIds) ? blockIds : [blockIds];
    const firstId = ids[0] || "";
    const langLabel = language === "en" ? "English" : "Español";
    const durLabel = duration === "complete" ? "Completo" : `${duration} min`;
    Status(firstId, `⏳ Generando audio (${durLabel}, ${langLabel})…`);
    try {
      const resp = await window.API.post("/ai/notebooklm/generate-audio", {
        block_ids: ids,
        topic_id: topicId || "",
        language,
        duration,
        provider: "notebooklm",
      });
      const tasks = Tasks();
      if (tasks) {
        tasks.showStreamModal("🎵 Generando Audio", "NotebookLM + TTS");
        tasks.startStreamPoll(resp.task_id, firstId, "audio", topicId || "");
      }
    } catch (err) {
      Status(firstId, `❌ Error: ${err.message}`, true);
    }
  }

  // ─── English Grammar Exercises (NotebookLM) ────────────────────
  // v3 port of the v2 ai-generation.js launcher. Provider is NOT sent:
  // the v3 backend only registers 'notebooklm' for grammar tasks.
  async function generateGrammarExercises(blockId, topicId, sourceType = "markdown", perType = 10, provider = "") {
    Status(blockId, "⏳ Generando English Exercises…");
    try {
      const resp = await window.API.post("/ai/generate-grammar-exercises", {
        source_id: blockId,
        source_type: sourceType === "pdf" ? "content" : "markdown",
        topic_id: topicId || "",
        per_type: perType,
      });
      const tasks = Tasks();
      if (tasks) {
        tasks.showStreamModal("✏️ Generando English Exercises", "NotebookLM");
        tasks.startStreamPoll(resp.task_id, blockId, "grammar", topicId || "");
      }
    } catch (err) {
      Status(blockId, `❌ Error: ${err.message}`, true);
    }
  }

  // ─── Public exports ────────────────────────────────────────────
  return {
    generateNbMd,
    generateNbHtml,
    generateNbMdFromContent,
    generateNbHtmlFromContent,
    youtubeToMd,
    youtubeToHtml,
    youtubeZen,
    youtubeZenQueue,
    generateNbTest,
    generateInfographic,
    generateAudio,
    generateNbAudio,
    generateGrammarExercises,
  };
})();

console.log("[AI.Generation] loaded");