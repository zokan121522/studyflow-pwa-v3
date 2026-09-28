// ─── AI — unified ✨ Generate toolbar + manifests + counters (v3) ──
// Namespace: window.App.AI
// Dependencies: window.App.Addons, window.App.CoursesAPI, window.API,
//               window.App.AI.Tasks / App.AI.Generation (ai-tasks.js,
//               ai-notebooklm.js — loaded BEFORE this file).
//
// Phase 7.7 — port from v2 ai.js. Scope trimmed to v3 backend reality:
//   • addon manifests: notebooklm + opencode (english/vocab/OpenZEN
//     flows deferred until OpenZEN lands).
//   • Buttons render ONLY for installed+enabled addons
//     (App.Addons.activeBlockActionsSync()).
//   • Usage counters (N/LIMIT) from GET /api/ai/usage.
//   • Result blocks insert via CoursesAPI.addBlock + moveBlock (v3
//     has no bulk PATCH-with-blocks like v2; per-block POST + move).

window.App = window.App || {};
window.App.AI = window.App.AI || {};

// IMPORTANT: merge into the existing namespace — ai-tasks.js and
// ai-notebooklm.js (loaded BEFORE this file) already attached
// App.AI.Tasks / App.AI.Generation. Replacing the object would drop
// them.
window.App.AI = Object.assign(window.App.AI, (function () {
  "use strict";

  const { escHtml } = window.App.UI || { escHtml: (s) => String(s == null ? "" : s) };

  // ─── Usage counter cache (per-day) ─────────────────────────────
  let _usageCache = { data: null, date: "" };
  const TYPE_GROUPS = {
    notebooklm_total: ["notebooklm", "notebooklm_enhance_md", "notebooklm_md_to_html"],
  };

  async function _fetchUsageData() {
    try {
      const today = new Date().toISOString().slice(0, 10);
      if (_usageCache.date === today && _usageCache.data) return _usageCache.data;
      const resp = await window.API.get("/ai/usage");
      const usage = (resp && resp.usage) || {};
      _usageCache = { data: usage, date: today };
      return usage;
    } catch (err) {
      console.warn("[AI] usage fetch:", err.message);
      return null;
    }
  }

  async function renderUsageCounters(container) {
    const usage = await _fetchUsageData();
    if (!usage) return;
    for (const [groupKey, types] of Object.entries(TYPE_GROUPS)) {
      let count = 0, limit = 0;
      for (const t of types) {
        const info = usage[t];
        if (info) { count += info.count; limit = Math.max(limit, info.limit); }
      }
      usage[groupKey] = { count, limit };
    }
    const btns = (container || document).querySelectorAll("[data-task-type]");
    for (const btn of btns) {
      const tt = btn.dataset.taskType;
      const info = usage[tt];
      if (!info) continue;
      const old = btn.nextElementSibling && btn.nextElementSibling.classList
        && btn.nextElementSibling.classList.contains("usage-counter")
        ? btn.nextElementSibling : null;
      if (old) old.remove();
      const span = document.createElement("span");
      span.className = "usage-counter";
      span.textContent = `(${info.count}/${info.limit})`;
      if (info.count === 0) span.classList.add("zero");
      else if (info.count >= info.limit * 0.8) span.classList.add("near-limit");
      btn.insertAdjacentElement("afterend", span);
    }
  }

  // ─── Status helper (per-block inline status line) ─────────────
  function _showStatus(blockId, msg, isPersistent = false) {
    const container = document.querySelector(`[data-ai-status="${blockId}"]`);
    if (!container) return;
    container.style.display = "block";
    container.textContent = msg;
    if (isPersistent) return;
    clearTimeout(container._sfStatusTimer);
    container._sfStatusTimer = setTimeout(() => {
      container.style.display = "none";
    }, 4000);
  }

  // ─── Addon block-action manifests (generic renderer-friendly) ──
  // `md`/`pdf` are the resolved data-ai-action values per block scope;
  // only actions backed by v3 endpoints are registered.
  if (window.App.Addons && window.App.Addons.register) {
    window.App.Addons.register({
      slug: "notebooklm",
      name: "NotebookLM",
      icon: "🧠",
      cls: "ai-btn-nb",
      blockActions: [
        { id: "md", label: "Markdown", icon: "✍️", cat: "generate", order: 10,
          md: "nb-md-content", pdf: "notebooklm-md",
          task: { md: "notebooklm_total", pdf: "notebooklm" } },
        { id: "youtube", label: "YouTube", icon: "🎬", cat: "generate", order: 16,
          md: "notebooklm-youtube", pdf: "notebooklm-youtube" },
        { id: "html", label: "HTML", icon: "🌐", cat: "generate", order: 20,
          md: "nb-html-content", pdf: "notebooklm-html",
          task: { md: "notebooklm_total", pdf: "notebooklm_md_to_html" } },
        { id: "test", label: "Test", icon: "❓", cat: "generate", order: 30,
          md: "nb-test", pdf: "notebooklm-test",
          task: { md: "notebooklm_test", pdf: "notebooklm_test" } },
        { id: "infographic", label: "Infographic", icon: "📊", cat: "generate", order: 40,
          md: "infographic", pdf: "infographic",
          task: { md: "notebooklm_infographic", pdf: "notebooklm_infographic" } },
      ],
    });

    window.App.Addons.register({
      slug: "opencode",
      name: "OpenZen",
      icon: "🤖",
      cls: "ai-btn-oc",
      blockActions: [
        { id: "audio", label: "Audio", icon: "🎵", cat: "generate", order: 60,
          md: "audio", pdf: "audio",
          task: { md: "opencode_audio", pdf: "opencode_audio" } },
      ],
    });
  }

  // ─── Generate section renderer (per-addon group chips) ────────
  function _renderGenerateSectionHtml(scope, blockId, topicId) {
    const bidAttr = ` data-block-id="${blockId}"`;
    const topicAttr = topicId ? ` data-topic-id="${topicId}"` : "";
    const addons = window.App.Addons;
    const actions = addons && addons.activeBlockActionsSync
      ? addons.activeBlockActionsSync() : [];
    const gen = actions.filter((a) => a.cat === "generate");

    const groups = [];
    const bySlug = new Map();
    for (const act of gen) {
      if (!bySlug.has(act.slug)) {
        bySlug.set(act.slug, []);
        groups.push(bySlug.get(act.slug));
      }
      bySlug.get(act.slug).push(act);
    }

    let html = "";
    for (const group of groups) {
      const first = group[0];
      const addonName = first.addonName || first.slug;
      const addonIcon = first.addonIcon || "🧩";
      const addonCls = first.addonCls || "";
      html += `<div class="toolbar-group" data-addon="${escHtml(first.slug)}">` +
        `<span class="toolbar-chip ${escHtml(addonCls)}" title="${escHtml(addonName)}">` +
        `${escHtml(addonIcon)} ${escHtml(addonName)}</span>`;
      for (const act of group) {
        const dbAction = act[scope] === undefined ? act.id : (act[scope] || "");
        if (dbAction === "") continue;
        const task = act.task ? act.task[scope] : null;
        html += `<button class="ai-btn ${act.cls || ""}" data-ai-action="${escHtml(dbAction)}"` +
          (task ? ` data-task-type="${escHtml(task)}"` : "") +
          `${bidAttr}${topicAttr} title="${escHtml(act.label)} (${escHtml(act.slug)})">` +
          (act.icon ? `${act.icon} ` : "") + `${escHtml(act.label)}</button>`;
      }
      html += `</div>`;
    }
    html += `<div class="ai-status" data-ai-status="${escHtml(blockId)}" style="display:none;flex-basis:100%;"></div>`;
    return html;
  }

  /** Toolbar for markdown blocks (scope md). */
  function renderMdAiButtonsHtml(blockId, topicId) {
    const topicAttr = topicId ? ` data-topic-id="${topicId}"` : "";
    return `<div class="add-block-bar block-toolbar" data-scope="md"${topicAttr}>\n` +
      `    ${_renderGenerateSectionHtml("md", blockId, topicId)}\n    </div>`;
  }

  /** Toolbar for pdf-ref blocks (scope pdf). */
  function renderPdfAiButtonsHtml(blockId, topicId) {
    const topicAttr = topicId ? ` data-topic-id="${topicId}"` : "";
    return `<div class="add-block-bar block-toolbar" data-scope="pdf"${topicAttr}>\n` +
      `    ${_renderGenerateSectionHtml("pdf", blockId, topicId)}\n    </div>`;
  }

  /** Re-render every toolbar + float in the given container. */
  function refreshBlockToolbars(container) {
    const root = container || document;
    root.querySelectorAll(".block-toolbar[data-scope]").forEach((tb) => {
      const bid = tb.querySelector("[data-block-id]")?.dataset.blockId || "";
      const tid = tb.dataset.topicId || "";
      const scope = tb.dataset.scope || "md";
      tb.innerHTML = _renderGenerateSectionHtml(scope, bid, tid);
      initSectionEvents(tb);
    });
  }

  // ─── Click wiring ──────────────────────────────────────────────
  async function _handleAiClick(e) {
    const btn = e.currentTarget;
    const action = btn.dataset.aiAction;
    const tid = btn.dataset.topicId;
    const bid = btn.dataset.blockId;
    const cid = btn.closest("[data-course-id]")?.dataset.courseId || "";
    const gen = window.App.AI.Generation;

    if (action === "notebooklm-md") {
      if (bid && gen) await gen.generateNbMd(bid, tid);
      else _showStatus(bid, "⚠️ Módulo de generación no disponible", true);
    } else if (action === "notebooklm-html") {
      if (bid && gen) await gen.generateNbHtml(bid, tid);
      else _showStatus(bid, "⚠️ Módulo de generación no disponible", true);
    } else if (action === "nb-md-content") {
      if (bid && gen) await gen.generateNbMdFromContent(bid, tid);
      else _showStatus(bid, "⚠️ Módulo de generación no disponible", true);
    } else if (action === "nb-html-content") {
      if (bid && gen) await gen.generateNbHtmlFromContent(bid, tid);
      else _showStatus(bid, "⚠️ Módulo de generación no disponible", true);
    } else if (action === "notebooklm-youtube") {
      _showYoutubeDialog(tid, cid, { provider: "notebooklm" });
    } else if (action === "nb-test") {
      if (gen) await gen.generateNbTest(bid, tid);
      else _showStatus(bid, "⚠️ Módulo de test no disponible", true);
    } else if (action === "notebooklm-test") {
      if (gen) await gen.generateNbTest([bid], tid);
      else _showStatus(bid, "⚠️ Módulo de test no disponible", true);
    } else if (action === "infographic") {
      if (gen) await gen.generateInfographic(bid, tid);
      else _showStatus(bid, "⚠️ Módulo de infografía no disponible", true);
    } else if (action === "audio") {
      if (gen) await gen.generateAudio(bid, tid);
      else _showStatus(bid, "⚠️ Módulo de audio no disponible", true);
    } else {
      _showStatus(bid, `❌ Acción "${action}" no implementada en v3`, true);
    }
  }

  /** Minimal YouTube URL dialog (native NotebookLM ingestion). */
  function _showYoutubeDialog(topicId, courseId, opts = {}) {
    const url = prompt("🎬 Pega la URL de YouTube:");
    if (!url || !url.trim()) return;
    const gen = window.App.AI.Generation;
    if (!gen) return;
    const norm = url.trim();
    // md scope when launched from md toolbar; format = markdown
    gen.youtubeToMd("", topicId, norm);
  }

  function initSectionEvents(container) {
    const btns = container.querySelectorAll("[data-ai-action]");
    btns.forEach((btn) => {
      btn.removeEventListener("click", _handleAiClick);
      btn.addEventListener("click", _handleAiClick);
    });
    // Fire-and-forget usage counters (non-blocking)
    renderUsageCounters(container);
  }

  // ─── Insert helpers (v3: addBlock + moveBlock) ─────────────────
  function _cleanBlockTitle(title) {
    return String(title || "")
      .replace(/^[📄📝📕🎵❓🤖✨📊]\s*/u, "")
      .replace(/\s*·\s*\d{1,2}\/\d{1,2},\s*\d{1,2}:\d{2}$/, "")
      .trim();
  }

  /**
   * Create a block in the topic and move it right after the source block.
   * v3 has per-block POST + move (no v2 bulk PATCH with full blocks).
   */
  async function _addBlockAfterSource(courseId, sourceBlockId, blockData) {
    const newBlock = await window.App.CoursesAPI.addBlock(courseId, {
      topic_id: blockData.topic_id || undefined,
      type: blockData.type || "markdown",
      title: blockData.title || "",
      content: blockData.content || "",
    });
    if (!newBlock || !newBlock.id) {
      throw new Error("addBlock returned no block");
    }
    // Reorder: move the new block right after its source within the topic.
    if (blockData.topic_id) {
      try {
        let blocks = [];
        if (window.App.CoursesAPI.listTopicBlocks) {
          blocks = await window.App.CoursesAPI.listTopicBlocks(
            courseId, blockData.topic_id);
        }
        const idx = (blocks || []).findIndex((b) => b.id === sourceBlockId);
        if (idx !== -1 && window.App.CoursesAPI.moveBlock) {
          await window.App.CoursesAPI.moveBlock(newBlock.id, {
            target_topic_id: blockData.topic_id,
            index: idx + 1,
          });
        }
      } catch (err) {
        console.warn("[AI] move after source failed (block stays at topic end):", err.message);
      }
    }
    return newBlock.id;
  }

  // ─── Content success callback ──────────────────────────────────
  async function _onContentSuccess(task, blockId, topicId, format, courseIdHint) {
    const courseId = courseIdHint
      || (typeof STATE !== "undefined" && STATE.currentCourseId)
      || "";
    if (!courseId) {
      _showStatus(blockId, "❌ No hay curso activo", true);
      return;
    }

    let blockType, label, emoji;
    if (format === "markdown" || format === "md") {
      blockType = "markdown"; label = "NotebookLM"; emoji = "📚";
    } else if (format === "html") {
      blockType = "content"; label = "NotebookLM HTML"; emoji = "📚";
    } else if (format === "audio") {
      blockType = "content"; label = "Audio"; emoji = "🎵";
    } else if (format === "infographic") {
      blockType = "content"; label = "Infografía"; emoji = "📊";
    } else {
      blockType = "markdown"; label = "Markdown"; emoji = "🤖";
    }

    let sourceTitle = label;
    try {
      const list = await window.App.CoursesAPI.fetchCourses();
      const course = (list || []).find((c) => c.id === courseId) || {};
      const sourceBlock = (course.blocks || []).find((b) => b.id === blockId);
      if (sourceBlock && sourceBlock.title) {
        sourceTitle = _cleanBlockTitle(sourceBlock.title) || label;
      }
    } catch { /* fallback to label */ }

    let blockContent = task.result_content || "";
    if (format === "audio") {
      blockContent = `<audio controls style="width:100%" src="${blockContent.replace(/"/g, "&quot;")}"></audio>`;
    }
    if (format === "infographic") {
      blockContent = `<img src="${blockContent.replace(/"/g, "&quot;")}" style="max-width:100%;height:auto;border-radius:8px;">`;
    }

    try {
      const newId = await _addBlockAfterSource(courseId, blockId, {
        type: blockType,
        title: `${emoji} ${sourceTitle}`,
        content: blockContent,
        topic_id: topicId,
      });
      _showStatus(blockId, `✅ ${label} generado`, true);
      // Trigger the v3 re-render event so the block shows immediately.
      window.dispatchEvent(new CustomEvent("studyflow:blocks-changed", {
        detail: { courseId, topicId }
      }));
      return newId;
    } catch (err) {
      console.error("[AI] insert block failed:", err);
      _showStatus(blockId, "❌ Error al guardar el bloque", true);
    }
  }

  // ─── Test success callback ─────────────────────────────────────
  async function _onTestSuccess(task, blockId, topicId) {
    const courseId = (typeof STATE !== "undefined" && STATE.currentCourseId) || "";
    if (!courseId) {
      _showStatus(blockId, "❌ No hay curso activo", true);
      return;
    }
    const raw = task.result_content || "";
    try {
      const newId = await _addBlockAfterSource(courseId, blockId, {
        type: "content",
        title: `❓ Test`,
        content: raw,
        topic_id: topicId,
      });
      _showStatus(blockId, "✅ Test generado", true);
      window.dispatchEvent(new CustomEvent("studyflow:blocks-changed", {
        detail: { courseId, topicId }
      }));
      return newId;
    } catch (err) {
      _showStatus(blockId, "❌ Error al guardar el test", true);
    }
  }

  // ─── Public exports ────────────────────────────────────────────
  return {
    renderMdAiButtonsHtml,
    renderPdfAiButtonsHtml,
    renderUsageCounters,
    initSectionEvents,
    refreshBlockToolbars,
    stopAllPolls: () => {
      if (window.App.AI.Tasks && window.App.AI.Tasks.stopAllPolls) {
        window.App.AI.Tasks.stopAllPolls();
      }
    },
    // Internal callbacks (used by ai-tasks.js streaming poll)
    _showStatus,
    _onContentSuccess,
    _onTestSuccess,
    _addBlockAfterSource,
  };
})());

console.log("[AI] loaded");