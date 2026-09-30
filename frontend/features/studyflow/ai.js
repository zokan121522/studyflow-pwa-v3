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

  // ➕ Añadir — block-creation types rendered as the last toolbar group
  // (v2 parity). Mirrors CoursesBlocks.TYPE_META labels/icons; the
  // click handler is the same `.sf-td-add-chip` delegate from
  // CoursesBlocks._attachBlockHandlers so creation defaults live there.
  const ADD_TYPE_META = {
    markdown:  { icon: "📝", label: "Markdown" },
    content:   { icon: "📄", label: "Texto" },
    separator: { icon: "➖", label: "Separador" },
    "pdf-ref": { icon: "📕", label: "PDF" },
    youtube:   { icon: "▶️", label: "YouTube" },
    image:     { icon: "🖼", label: "Imagen" },
    exercise:  { icon: "❓", label: "Ejercicio" },
    interactive: { icon: "🌐", label: "Página web" },
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
  // The inline container only exists inside a rendered block toolbar. When a
  // generation is started from elsewhere (nav button, dashboard) there is no
  // [data-ai-status] node, and the old code returned silently — the user saw
  // the modal close and nothing at all, not even the error. Fall back to a
  // transient toast so progress and failures are always visible.
  function _toast(msg, isPersistent = false) {
    let host = document.getElementById("sf-ai-toast");
    if (!host) {
      host = document.createElement("div");
      host.id = "sf-ai-toast";
      host.style.cssText =
        "position:fixed;left:50%;bottom:24px;transform:translateX(-50%);"
        + "z-index:100000;max-width:min(92vw,560px);padding:10px 16px;"
        + "border-radius:10px;background:#16213e;color:#fff;font-size:14px;"
        + "line-height:1.4;box-shadow:0 8px 28px rgba(0,0,0,.45);"
        + "border:1px solid #3a4a6e;white-space:pre-wrap;word-break:break-word;";
      document.body.appendChild(host);
    }
    host.textContent = msg;
    host.style.display = "block";
    clearTimeout(host._sfToastTimer);
    if (!isPersistent) {
      host._sfToastTimer = setTimeout(() => {
        host.style.display = "none";
      }, 6000);
    }
  }

  function _showStatus(blockId, msg, isPersistent = false) {
    const container = document.querySelector(`[data-ai-status="${blockId}"]`);
    if (!container) {
      _toast(msg, isPersistent);
      return;
    }
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
        { id: "gen-content", label: "Gen. Contenido", icon: "🧠", cat: "generate", order: 42,
          md: "notebooklm-gen-content", pdf: "notebooklm-gen-content",
          task: { md: "knowledge_pipeline", pdf: "knowledge_pipeline" } },
        { id: "english", label: "English", icon: "✏️", cat: "generate", order: 44,
          md: "notebooklm-english", pdf: "",
          task: { md: "generate_grammar" } },
        { id: "audio", label: "Audio", icon: "🎵", cat: "generate", order: 50,
          md: "nb-audio", pdf: "nb-audio",
          task: { md: "notebooklm_audio", pdf: "notebooklm_audio" } },
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
        // Phase 8 — YouTubeZen: local yt-dlp extracts subtitles, then OpenZEN
        // (big-pickle via opencode-acp) structures them. It is NOT a NotebookLM
        // feature, so it belongs to this addon, next to Audio. Opens the full
        // zen dialog (template / depth / mode / language + FIFO queue).
        { id: "youtube-zen", label: "YouTube Zen", icon: "🎥", cat: "generate", order: 61,
          md: "youtube", pdf: "youtube",
          task: { md: "youtube_zen", pdf: "youtube_zen" } },
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
    // ➕ Añadir — block-creation group, rendered BELOW the addon groups
    // (v2 parity: the 📎 Add section lived inside the per-block unified
    // toolbar, after the generate groups. The global topic add-bar now
    // renders only for empty topics — see courses.js _renderTopicDetail).
    html += `<div class="toolbar-group" data-addon="add">` +
      `<span class="toolbar-chip toolbar-chip-add" title="Añadir bloque">➕ Añadir</span>`;
    for (const [type, m] of Object.entries(ADD_TYPE_META)) {
      html += `<button type="button" class="sf-td-add-chip" data-type="${escHtml(type)}"` +
        `${bidAttr}${topicAttr} title="${escHtml(m.label)}">` +
        `<span class="sf-td-add-chip-icon">${m.icon}</span>${escHtml(m.label)}</button>`;
    }
    html += `</div>`;
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
      // Phase 7.7 — open the markdown config modal first (template/length/language)
      const modal = window.App.AiConfigModal;
      if (bid && gen && modal) {
        await modal.open({
          title: "📄 NotebookLM → Markdown",
          submitLabel: "✨ Generar con NotebookLM",
          errorStatus: (msg) => _showStatus(bid, msg, true),
          onSubmit: (params) => gen.generateNbMd(bid, tid, params),
        });
      } else {
        _showStatus(bid, "⚠️ Modal de configuración no disponible", true);
      }
    } else if (action === "notebooklm-html") {
      if (bid && gen) await gen.generateNbHtml(bid, tid);
      else _showStatus(bid, "⚠️ Módulo de generación no disponible", true);
    } else if (action === "nb-md-content") {
      // Phase 7.7 — improve existing markdown: same config modal
      const modal = window.App.AiConfigModal;
      if (bid && gen && modal) {
        await modal.open({
          title: "✍️ NotebookLM → Mejorar Markdown",
          submitLabel: "✨ Mejorar con NotebookLM",
          errorStatus: (msg) => _showStatus(bid, msg, true),
          onSubmit: (params) => gen.generateNbMdFromContent(bid, tid, params),
        });
      } else {
        _showStatus(bid, "⚠️ Modal de configuración no disponible", true);
      }
    } else if (action === "nb-html-content") {
      if (bid && gen) await gen.generateNbHtmlFromContent(bid, tid);
      else _showStatus(bid, "⚠️ Módulo de generación no disponible", true);
    } else if (action === "notebooklm-youtube") {
      _showYoutubeDialog(tid, cid, { provider: "notebooklm" });
    } else if (action === "youtube") {
      // Phase 8 — YouTubeZen dialog (full zen controls: template/depth/mode).
      if (tid) {
        _showYoutubeDialog(tid, cid);
      } else {
        _showStatus(bid, "⚠️ No se pudo determinar el tema", true);
      }
    } else if (action === "nb-test" || action === "notebooklm-test") {
      // Phase 7.7 — test config modal (10/20/30 questions)
      const modals = window.App.AiModals;
      if (gen && modals) {
        await modals.openTestConfig({
          onSubmit: (numQuestions) => gen.generateNbTest([bid], tid, numQuestions),
        });
      } else {
        _showStatus(bid, "⚠️ Modal de test no disponible", true);
      }
    } else if (action === "infographic") {
      // Phase 7.7 — infographic config modal (style + language)
      const modals = window.App.AiModals;
      if (gen && modals) {
        await modals.openInfographicConfig({
          onSubmit: (params) => gen.generateInfographic([bid], tid, params),
        });
      } else {
        _showStatus(bid, "⚠️ Modal de infografía no disponible", true);
      }
    } else if (action === "notebooklm-gen-content") {
      // Phase 38 (#26) — Knowledge Pipeline modal (NotebookLM provider).
      const kp = window.App.KnowledgePipeline;
      if (kp && kp.open) {
        await kp.open(cid, tid);
      } else {
        _showStatus(bid, "⚠️ Módulo Gen. Contenido no disponible", true);
      }
    } else if (action === "notebooklm-english") {
      // Phase 61 (#257) — English Exercises config modal (markdown only).
      const eng = window.App.EnglishGrammar;
      if (eng && eng.showGrammarConfig) {
        const sourceType = btn.closest("[data-scope]")?.dataset.scope === "pdf" ? "pdf" : "markdown";
        await eng.showGrammarConfig(bid, tid, sourceType, { provider: "notebooklm" });
      } else {
        _showStatus(bid, "⚠️ Módulo English no disponible", true);
      }
    } else if (action === "audio" || action === "nb-audio") {
      // Phase 7.7 — audio config modal (duration + language)
      const modals = window.App.AiModals;
      if (gen && modals) {
        await modals.openAudioConfig({
          language: "es",
          onSubmit: (params) => {
            if (action === "nb-audio") {
              gen.generateNbAudio([bid], tid, params.language, params.duration);
            } else {
              gen.generateAudio([bid], tid, params.language, params.duration);
            }
          },
        });
      } else {
        _showStatus(bid, "⚠️ Modal de audio no disponible", true);
      }
    } else {
      _showStatus(bid, `❌ Acción "${action}" no implementada en v3`, true);
    }
  }

  /** Minimal YouTube URL dialog (native NotebookLM ingestion). */
  function _showYoutubeDialog(topicId, courseId, opts = {}) {
    // Issue #13 — the NotebookLM YouTube button now gets the full YouTubeZen
    // controls (template / depth / mode / language), the same as OpenZen.
    //
    // Phase 64 (#255) hid them because the native endpoint only read
    // url + topic_id. The native path now composes its prompt from these
    // four options, so they are real for both providers.
    //
    // nbNative survives ONLY to pick the submit path: YouTubeZen goes
    // through the FIFO queue, the native NotebookLM path posts one task
    // per URL. It no longer hides any control.
    const nbNative = !!(opts && opts.provider === "notebooklm");

    // Remove any existing panel
    const existing = document.getElementById("youtube-dialog-overlay");
    if (existing) existing.remove();

    const html = `
      <div class="kp-overlay" id="youtube-dialog-overlay">
        <div class="kp-modal kp-modal-yt-wide" id="yt-dialog-modal">
          <div class="kp-modal-header">
            <span class="kp-modal-icon">🎬</span>
            <span class="kp-modal-title">${nbNative ? "YouTube → Contenido · NotebookLM" : "YouTube → Contenido"}</span>
            <button class="kp-modal-close" id="yt-dialog-close" title="Cerrar">✕</button>
          </div>
          <div class="kp-modal-body">
            <label class="kp-label" for="yt-url-input">🔗 URLs de vídeos (una por línea):</label>
            <textarea
              id="yt-url-input"
              class="kp-textarea"
              rows="3"
              style="padding:10px 14px;"
              placeholder="https://youtube.com/watch?v=...&#10;https://youtu.be/... (máx 20)"
            ></textarea>
            <label class="kp-label">🎛️ Plantilla de prompt (opcional):</label>
            <div class="ozmd-grid">
              <div class="ozmd-templates" id="yt-templates">
                <div style="color:#aaa;padding:6px 2px;">Cargando plantillas…</div>
              </div>
              <div class="ozmd-preview">
                <div class="ozmd-preview-title">👁️ Vista previa</div>
                <div class="ozmd-preview-body" id="yt-preview-body">
                  <div style="color:#aaa;padding:6px 2px;">Sin plantilla — prompt estándar YouTube</div>
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
                <span class="kp-mode-desc">Sección = bloque nuevo</span>
              </button>
            </div>
            <label class="kp-label">Idioma:</label>
            <div class="kp-lang-row">
              <button class="kp-lang-btn selected" data-lang="es">🇪🇸 Español</button>
              <button class="kp-lang-btn" data-lang="en">🇬🇧 English</button>
            </div>
            <div id="yt-dialog-status" style="font-size:12px;color:var(--text-muted,#888);display:none;padding:8px 12px;border-radius:6px;background:var(--surface-raised,#252535);margin-top:12px;"></div>
          </div>
          <div class="kp-modal-footer">
            <button class="kp-btn kp-btn-cancel" id="yt-dialog-cancel">Cancelar</button>
            <button class="kp-btn kp-btn-submit" id="yt-dialog-generate">${nbNative ? "📥 Generar (NotebookLM)" : "📥 Encolar vídeos"}</button>
          </div>
        </div>
      </div>`;

    document.body.insertAdjacentHTML("beforeend", html);

    const overlay = document.getElementById("youtube-dialog-overlay");
    const input = document.getElementById("yt-url-input");
    const status = document.getElementById("yt-dialog-status");
    const generateBtn = document.getElementById("yt-dialog-generate");

    let selectedDepth = "standard";
    let selectedLang = "es";
    let selectedMode = "unitema";
    let selectedTemplate = ""; // '' = generic YT prompt (backward compatible)

    const escHtml = (s) => String(s == null ? "" : s);

    // ── Helpers ──────────────────────────────────────────────────
    function _setStatus(msg, isError) {
      status.textContent = msg;
      status.style.display = "block";
      status.style.color = isError ? "#e74c3c" : "#f0c040";
    }

    function _hideStatus() {
      status.style.display = "none";
    }

    // ── Templates grid + live preview (same pattern as OpenZEN md) ──
    const templatesEl = document.getElementById("yt-templates");
    const previewBody = document.getElementById("yt-preview-body");

    // The FULL backend catalog is shown (10 templates), in insertion order
    // from GET /api/ai/openzen-md-templates.
    const YT_TEMPLATE_IDS = null;

    (async () => {
      let templates = [];
      try {
        const resp = await window.API.get("/ai/openzen-md-templates");
        const src = resp.templates || [];
        // Preserve backend insertion order when no allow-list is configured.
        templates = YT_TEMPLATE_IDS
          ? src
              .filter((t) => YT_TEMPLATE_IDS.includes(t.id))
              .sort((a, b) => YT_TEMPLATE_IDS.indexOf(a.id) - YT_TEMPLATE_IDS.indexOf(b.id))
          : src.slice();
      } catch (err) {
        templatesEl.innerHTML = `<div style="color:#e57373;padding:6px 2px;">❌ No se pudieron cargar plantillas</div>`;
        return;
      }
      const renderPreview = (tpl) => {
        const cb = window.App.ContentBlocks;
        previewBody.innerHTML = cb && cb._renderMd ? cb._renderMd(tpl.mock || "") : `<pre>${escHtml(tpl.mock || "")}</pre>`;
      };

      const cardEls = [];
      const cards = templates.map((t, i) => {
        const elId = `yt-tpl-${i}`;
        cardEls.push({ elId, tpl: t });
        return `<div class="inf-config-style-opt ozmd-tpl-opt" id="${elId}" data-template-id="${t.id}" title="${escHtml(t.description)}" data-tpl-idx="${i}">
          <div class="inf-config-style-label">${t.emoji} ${escHtml(t.name)}</div>
          <div class="inf-config-style-desc">${escHtml(t.description)}</div>
        </div>`;
      }).join("");
      templatesEl.innerHTML =
        `<div class="inf-config-style-opt ozmd-tpl-opt ozmd-tpl-none selected" data-template-id="" data-tpl-idx="-1">
          <div class="inf-config-style-label">⚡ Sin plantilla</div>
          <div class="inf-config-style-desc">Prompt estándar YouTube (comportamiento actual)</div>
        </div>${cards}`;

      templatesEl.querySelectorAll(".ozmd-tpl-opt").forEach((el) => {
        el.addEventListener("click", () => {
          templatesEl.querySelectorAll(".ozmd-tpl-opt").forEach((o) => o.classList.remove("selected"));
          el.classList.add("selected");
          selectedTemplate = el.dataset.templateId || "";
          const idx = parseInt(el.dataset.tplIdx, 10);
          const tpl = idx >= 0 ? templates[idx] : null;
          if (tpl) renderPreview(tpl);
          else previewBody.innerHTML = `<div style="color:#aaa;padding:6px 2px;">Sin plantilla — prompt estándar YouTube</div>`;
        });
      });
    })();

    function _getUrls() {
      const urls = input.value
        .split("\n")
        .map((s) => s.trim())
        .filter((s) => s.length > 0);
      if (!urls.length) {
        _setStatus("⚠️ Introduce al menos una URL de YouTube", true);
        input.focus();
        return [];
      }
      const bad = urls.find((u) => !u.match(/^(https?:\/\/)?(www\.)?(youtube\.com|youtu\.be)\//));
      if (bad) {
        _setStatus(`⚠️ URL de YouTube no válida: ${bad}`, true);
        return [];
      }
      if (urls.length > 20) {
        _setStatus("⚠️ Máximo 20 URLs por lote", true);
        return [];
      }
      return urls;
    }

    function _launchGeneration() {
      const urls = _getUrls();
      if (!urls.length) return;

      _hideStatus();
      overlay.remove();

      const gen = window.App.AI.Generation;
      if (!gen) return;
        if (nbNative) {
          // Native NotebookLM: one backend task per URL (sequential).
          // Issue #13 — the dialog options now travel with the request so
          // the native path composes its prompt from them, exactly as
          // YouTubeZen does.
          //
          // The language is sent as selected, NOT mapped to 'auto'. Mapping
          // it would leave the Español button (the default selection)
          // doing nothing, i.e. a decorative control. Forcing it is the
          // parity the issue asks for, and it is a deliberate change: the
          // native default output is now pinned to Spanish like YouTubeZen.
          const opts = {
            template: selectedTemplate,
            depth: selectedDepth,
            mode: selectedMode,
            language: selectedLang,
          };
          (async () => {
            for (const url of urls) {
              try { await gen.youtubeToMd("", topicId, url, opts); }
              catch (err) { _showStatus("", `❌ YouTube: ${err.message}`, true); }
            }
          })();
          return;
        }
      if (urls.length === 1) {
        // Single URL → legacy flow (stream modal + manual insert)
        gen.youtubeZen("", topicId, urls[0], "markdown", selectedDepth, selectedMode, selectedLang, selectedTemplate);
      } else {
        // Multiple URLs → FIFO queue + floating panel
        gen.youtubeZenQueue(urls, topicId, "markdown", selectedDepth, selectedMode, selectedLang, selectedTemplate);
      }
    }

    // ── Wire events ──────────────────────────────────────────────

    // Close button
    document.getElementById("yt-dialog-close").addEventListener("click", () => overlay.remove());
    document.getElementById("yt-dialog-cancel").addEventListener("click", () => overlay.remove());

    // Click backdrop to close
    overlay.addEventListener("click", (e) => {
      if (e.target === e.currentTarget) overlay.remove();
    });

    // Depth / Mode / Language selectors (same wiring as Content Generator)
    overlay.querySelectorAll(".kp-depth-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        overlay.querySelectorAll(".kp-depth-btn").forEach((b) => b.classList.remove("selected"));
        btn.classList.add("selected");
        selectedDepth = btn.dataset.depth;
      });
    });
    overlay.querySelectorAll(".kp-mode-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        overlay.querySelectorAll(".kp-mode-btn").forEach((b) => b.classList.remove("selected"));
        btn.classList.add("selected");
        selectedMode = btn.dataset.mode;
      });
    });
    overlay.querySelectorAll(".kp-lang-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        overlay.querySelectorAll(".kp-lang-btn").forEach((b) => b.classList.remove("selected"));
        btn.classList.add("selected");
        selectedLang = btn.dataset.lang;
      });
    });

    // Generate button
    generateBtn.addEventListener("click", _launchGeneration);

    // Ctrl+Enter → enqueue (Enter alone adds a new line in the textarea)
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        _launchGeneration();
      }
    });

    // Focus input
    setTimeout(() => input.focus(), 100);
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
      .replace(/^[📄📝📕🎵❓🤖✨📊🧠✏️]\s*/u, "")
      .replace(/\s*·\s*\d{1,2}\/\d{1,2},\s*\d{1,2}:\d{2}$/, "")
      .trim();
  }

  /**
   * Resolve the course that OWNS `topicId`.
   *
   * `addBlock` posts to /courses/<cid>/topics/<tid>/blocks, so a cid that
   * does not own the topic gets a 404 "Topic not found in this course". The
   * youtube-zen queue finishes tasks long after they were enqueued and calls
   * _onContentSuccess without a courseIdHint, so the ambient
   * STATE.currentCourseId is whatever course the user happens to have open
   * at insert time. Deriving the owner from the topic makes the insert
   * correct no matter where the user navigated meanwhile.
   *
   * @returns {Promise<string|null>} course id, or null when unresolvable
   */
  async function _resolveCourseIdForTopic(topicId, hint) {
    // A course id is always numeric. The zen flow used to pass the *depth*
    // ("standard"/"detailed") in the courseIdHint slot, so trusting any
    // truthy hint produced POST /courses/standard/topics/<id>/blocks -> 405.
    const isCourseId = (v) => /^\d+$/.test(String(v == null ? "" : v).trim());
    const tid = topicId ? String(topicId) : null;

    // The topic is the authority: it decides which course the block belongs
    // to, so prefer its owner even when a hint is present. This also repairs
    // a hint that is numeric but points at the wrong course.
    if (tid) {
      try {
        const list = await window.App.CoursesAPI.fetchCourses();
        for (const course of list || []) {
          const owns = (course.topics || []).some((t) => String(t.id) === tid);
          if (owns) return String(course.id);
        }
      } catch { /* fall through to the hints */ }
    }
    if (isCourseId(hint)) return String(hint).trim();
    const current = (typeof STATE !== "undefined" && STATE.currentCourseId) || "";
    return isCourseId(current) ? String(current) : null;
  }

  /**
   * Create a block in the topic and move it right after the source block.
   * v3 has per-block POST + move (no v2 bulk PATCH with full blocks).
   */
  async function _addBlockAfterSource(courseId, sourceBlockId, blockData, indexOffset = 0) {
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
          // indexOffset keeps a multi-block insert in order: each call targets
          // the same slot (idx + 1), so without it every new block pushes the
          // previous one down and the sections come out reversed.
          await window.App.CoursesAPI.moveBlock(newBlock.id, {
            target_topic_id: blockData.topic_id,
            index: idx + 1 + (indexOffset || 0),
          });
        }
      } catch (err) {
        console.warn("[AI] move after source failed (block stays at topic end):", err.message);
      }
    }
    return newBlock.id;
  }

  /**
   * Does this task want one block per topic?
   *
   * Covers the NotebookLM YouTube path (format "markdown"/"md") alongside the
   * Knowledge Pipeline and YouTube Zen formats v2 handled, so a per-topic run
   * is split no matter which tool produced it.
   */
  function _isPorTemaTask(task, format) {
    const mode = task && task.coverage_data && task.coverage_data.mode;
    if (mode !== "por_tema") return false;
    return format === "markdown" || format === "md"
      || format === "ytd_zen" || format === "knowledge_pipeline";
  }

  /**
   * Split markdown into one section per `## ` heading.
   * Ported from v2's _parseKpSections. Only level-2 headings split, so `###`
   * sub-headings stay inside their section instead of becoming their own
   * ("bloques que no tocan").
   */
  function _parsePorTemaSections(content) {
    if (!content) return [];
    const sections = [];
    const parts = String(content).split(/(?=^##\s)/m);
    for (const part of parts) {
      const match = part.match(/^(##)\s+(.+?)\n([\s\S]*)$/);
      if (!match) continue;
      const title = match[2].replace(/\*\*/g, "").replace(/[#*]/g, "").trim();
      const body = match[3].trim();
      if (title && body && body.length > 20) sections.push({ title, body });
    }
    return sections;
  }

  // ─── Provenance header (who generated this, from what, and the source) ──
  //
  // A generated block looks like any other once it lands in a course, and a
  // 40-chunk course is impossible to audit later. Two lines travel with the
  // content: what produced it, and where it came from. Both are already on
  // the task row — coverage_data holds url / template_id / depth / video_title
  // on both the NotebookLM and the YouTube Zen paths — so this reads rather
  // than invents.

  const _PROV_TEMPLATES = {
    "notas-estandar": ["📝", "Notas estándar"],
    "transcripcion": ["🧠", "Reconstrucción de transcripción"],
    "tutorial": ["⚙️", "Tutorial / How-To"],
    "comparativa": ["🔀", "Comparativa"],
    "glosario": ["📚", "Glosario / Términos"],
    "resumen-ejecutivo": ["🎯", "Resumen ejecutivo"],
    "por-temas": ["🧩", "Por temas"],
    "faq": ["❓", "FAQ"],
    "arquitectura-tecnica": ["🏗️", "Arquitectura técnica"],
    "infografia-textual": ["📊", "Infografía textual"],
  };

  const _PROV_DEPTH = {
    concise: "resumido",
    standard: "estándar",
    detailed: "detallado",
  };

  /**
   * Identify the engine behind a finished task.
   * The notebook format only exists on the native NotebookLM path; the zen
   * formats only on YouTube Zen. `model_used` is the tiebreaker and the
   * fallback when the format is unknown.
   */
  function _provenanceProvider(task, format) {
    if (format === "ytd_zen") return { name: "OpenZen", emoji: "☁️" };
    if (format === "markdown" || format === "md" || format === "html") {
      return { name: "NotebookLM", emoji: "🧠" };
    }
    const model = String((task && task.model_used) || "");
    if (/zen|openzen/i.test(model)) return { name: "OpenZen", emoji: "☁️" };
    if (/notebook/i.test(model)) return { name: "NotebookLM", emoji: "🧠" };
    return null;
  }

  /**
   * Build the two provenance lines for a generated block.
   *
   * @returns {{chip:?string, link:?string, url:string, provider:?object}}
   *   chip — "🧠 NotebookLM · 📝 Notas estándar · 🎯 detallado"
   *   link — "[Título del vídeo](https://…)" or null when there is no URL
   */
  function _provenance(task, format) {
    const cov = (task && task.coverage_data) || {};
    const provider = _provenanceProvider(task, format);
    const url = cov.video_url || cov.url || "";

    if (provider) {
      const tpl = cov.template_id && _PROV_TEMPLATES[cov.template_id];
      const tplLabel = tpl ? `${tpl[0]} ${tpl[1]}` : "📄 Por defecto";
      const depth = _PROV_DEPTH[cov.depth] || cov.depth || "estándar";
      var chip = `${provider.emoji} ${provider.name} · ${tplLabel} · 🎯 ${depth}`;
    } else {
      var chip = null;
    }

    var link = null;
    if (url) {
      const title = (cov.video_title || cov.title || "").trim() || "Vídeo de origen";
      // Guard against a title that would break out of the markdown link
      link = `[${title.replace(/[[\]]/g, "")}](${url})`;
    }
    return { chip: chip, link: link, url: url, provider: provider };
  }

  // ─── Content success callback ──────────────────────────────────
  async function _onContentSuccess(task, blockId, topicId, format, courseIdHint) {
    // Resolve the owning course from the topic when the caller has no hint:
    // the zen queue inserts long after enqueue, so STATE.currentCourseId is
    // the course open *now*, which is often a different one -> 404 on insert.
    const courseId = await _resolveCourseIdForTopic(topicId, courseIdHint);
    if (!courseId) {
      _showStatus(blockId, "❌ No hay curso activo", true);
      return;
    }

    // Titles are left exactly as generated — no emoji prefix. The user asked for
    // this: the 🎥 was a "this came from YouTube" marker they could delete by
    // hand, and deleting it only made the sidebar's 📝 type icon reappear, so
    // the icon they could remove was trading places with the one they could
    // not. The block's origin is recorded in the content instead (see
    // _provenance), which travels with the block and can be edited.
    let blockType, label;
    if (format === "markdown" || format === "md") {
      blockType = "markdown"; label = "NotebookLM";
    } else if (format === "ytd_zen") {
      // Phase 8 — youtube-zen queue. Must be handled explicitly: without it
      // it falls through to the generic branch and the block is titled
      // "Markdown", hiding which tool produced it.
      blockType = "markdown"; label = "YouTube Zen";
    } else if (format === "html") {
      blockType = "content"; label = "NotebookLM HTML";
    } else if (format === "audio") {
      blockType = "content"; label = "Audio";
    } else if (format === "infographic") {
      blockType = "content"; label = "Infografía";
    } else if (format === "knowledge_pipeline") {
      blockType = "markdown"; label = "Gen. Contenido";
    } else {
      blockType = "markdown"; label = "Markdown";
    }

    // Title the block after WHAT WAS GENERATED, not after the generic tool
    // name. See _blockTitleForTask for the preference order.
    const sourceTitle = await _blockTitleForTask(task, courseId, blockId, label);

    // ── por_tema → one markdown block per `## ` section ──────────────
    // v2 only split for the Knowledge Pipeline and YouTube Zen formats:
    //     if ((isKp || format === "ytd_zen") && task.coverage_data?.mode === "por_tema")
    // The NotebookLM YouTube endpoint sends format "markdown", so once #13 gave
    // that dialog the real per-topic mode, the mode arrived and was ignored —
    // a "por tema" run inserted one block with every section inside it. The
    // format check was the gate, and the gate was never widened.
    //
    // The mode itself was also being lost before it got this far: the worker
    // rewrote coverage_data with just {video_title, video_url}, wiping the
    // `mode` that create_youtube_md_task had stored. See _merge_coverage in
    // backend/ai/notebooklm/youtube.py.
    if (_isPorTemaTask(task, format)) {
      const sections = _parsePorTemaSections(task.result_content || "");
      if (sections.length > 0) {
        try {
          for (let i = 0; i < sections.length; i++) {
            await _addBlockAfterSource(courseId, blockId, {
              type: "markdown",
              title: sections[i].title,
              content: sections[i].body,
              topic_id: topicId,
            }, i);
          }
          window.App.CoursesAPI.clearDetailCache(courseId);
          window.dispatchEvent(new CustomEvent("studyflow:blocks-changed", {
            detail: { courseId, topicId }
          }));
          _showStatus(blockId, `✅ ${sections.length} bloques insertados en el tema`);
        } catch (err) {
          console.error("[AI] por_tema insert failed:", err);
          _showStatus(blockId, `❌ Error al crear bloques desde el contenido: ${err.message}`, true);
        }
        return; // do not also insert the whole thing as one block
      }
      console.warn("[AI] por_tema: no `##` sections parsed, falling back to one block");
    }

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
        title: sourceTitle,
        content: blockContent,
        topic_id: topicId,
      });
      // Trigger the v3 re-render event so the block shows immediately.
      window.dispatchEvent(new CustomEvent("studyflow:blocks-changed", {
        detail: { courseId, topicId }
      }));
      // The result is in the tree now, so the "generado" banner has done its
      // job. It used to be left up (persistent) until the user re-rendered
      // the screen by hand, sitting under the very block it was announcing.
      _showStatus(blockId, `✅ ${label} generado`, true);
      setTimeout(() => _clearStatus(blockId), 4000);
      return newId;
    } catch (err) {
      console.error("[AI] insert block failed:", err);
      // Surface the real reason: a generic "Error al guardar el bloque" sent
      // debugging this through the UI nowhere.
      const why = (err && (err.message || err.statusText)) || "error desconocido";
      _showStatus(blockId, `❌ Error al guardar: ${why}`, true);
    }
  }

  // ─── clearStatus(blockId) — drop a status line immediately ─────
  // `_showStatus(..., true)` marks a message persistent so it survives long
  // enough to be read. For a generation result that outlives its usefulness:
  // the new block is in, the tree has it, and the banner is now just in the
  // way until the user re-renders something by hand.
  function _clearStatus(blockId) {
    const container = document.querySelector(`[data-ai-status="${blockId}"]`);
    if (!container) return;
    clearTimeout(container._sfStatusTimer);
    container._sfStatusTimer = null;
    container.textContent = "";
    container.style.display = "none";
  }

  // ─── blockTitleForTask(task, blockId, fallback) ──────────────
  // A generated block used to be titled after whatever it was generated FROM,
  // which for a launch from the topic toolbar is no block at all — so the user
  // got "🤖 NotebookLM" and had to open every block to tell them apart.
  //
  // Prefer, in order:
  //   1. the source video title the YouTube worker already stored in
  //      coverage_data (authoritative, and free — no extra round trip);
  //   2. the first markdown H1 of the generated content, which is what the
  //      model was told to lead with;
  //   3. the caller's fallback.
  async function _blockTitleForTask(task, courseId, blockId, fallback) {
    const content = (task && task.result_content) || "";

    // 1. video title from the task row
    const coverage = task && task.coverage_data;
    if (coverage && typeof coverage === "object") {
      const vTitle = _cleanBlockTitle(coverage.video_title || "");
      if (vTitle) return vTitle;
    }

    // 2. first H1 / H2 in the generated markdown
    const heading = content.match(/^\s{0,3}#{1,2}\s+(.+?)\s*#*\s*$/m);
    if (heading) {
      const h = _cleanBlockTitle(heading[1]);
      if (h) return h;
    }

    // 3. fall back to the source block's title, then the generic label
    try {
      const list = await window.App.CoursesAPI.fetchCourses();
      const course = (list || []).find((c) => c.id === courseId) || {};
      const sourceBlock = (course.blocks || []).find((b) => b.id === blockId);
      if (sourceBlock && sourceBlock.title) {
        const t = _cleanBlockTitle(sourceBlock.title);
        if (t) return t;
      }
    } catch { /* fallback below */ }

    return fallback;
  }

  // ─── Test success callback ─────────────────────────────────────
  // The generated test is a JSON array of {question, options, correct,
  // explanation}. It used to be dropped verbatim into a `content` block, so
  // the user got a wall of raw JSON instead of a test: only `exercise` blocks
  // are rendered by App.QuizEmbed, and that reads the questions from
  // quiz_questions. So: create an exercise block with an EMPTY stem (the
  // questions are the content) and import them where the renderer looks.
  async function _onTestSuccess(task, blockId, topicId) {
    const courseId = await _resolveCourseIdForTopic(topicId, null);
    if (!courseId) {
      _showStatus(blockId, "❌ No hay curso activo", true);
      return;
    }

    let questions = [];
    try {
      const parsed = JSON.parse(task.result_content || "[]");
      questions = Array.isArray(parsed) ? parsed : (parsed.questions || []);
    } catch {
      _showStatus(blockId, "❌ No se pudo interpretar el test generado", true);
      return;
    }
    // Drop anything the quiz API would reject instead of failing the import.
    const usable = questions.filter(
      (q) => q && q.question && Array.isArray(q.options) && q.options.length >= 2
    );
    if (!usable.length) {
      _showStatus(blockId, "❌ El test generado no tiene preguntas válidas", true);
      return;
    }

    // Name the block after its source, like v2 did.
    let sourceTitle = "Test";
    try {
      const list = await window.App.CoursesAPI.fetchCourses();
      const course = (list || []).find((c) => String(c.id) === String(courseId)) || {};
      const src = (course.blocks || []).find((b) => String(b.id) === String(blockId));
      if (src && src.title) sourceTitle = _cleanBlockTitle(src.title) || "Test";
    } catch { /* fall back to "Test" */ }

    let newId;
    try {
      newId = await _addBlockAfterSource(courseId, blockId, {
        type: "exercise",
        title: `❓ ${sourceTitle}`,
        content: "",   // no JSON as stem — QuizEmbed paints the questions
        topic_id: topicId,
      });
    } catch (err) {
      _showStatus(blockId, `❌ Error al crear el bloque: ${err.message || err}`, true);
      return;
    }

    try {
      const res = await window.API.post("/quiz/questions/bulk", {
        block_id: newId,
        course_id: Number(courseId),
        topic_id: topicId ? Number(topicId) : null,
        replace: true,
        questions: usable,
      });
      const inserted = (res && res.inserted) || usable.length;
      const skipped = usable.length - inserted;
      _showStatus(
        blockId,
        skipped
          ? `✅ Test: ${inserted} preguntas (${skipped} descartadas)`
          : `✅ Test generado (${inserted} preguntas)`,
        true
      );
    } catch (err) {
      // The block exists but has no questions: say so instead of claiming
      // the test is ready — QuizEmbed would render an empty quiz.
      _showStatus(
        blockId,
        `⚠️ Bloque creado, pero falló la importación de preguntas: ${err.message || err}`,
        true
      );
    }

    window.dispatchEvent(new CustomEvent("studyflow:blocks-changed", {
      detail: { courseId, topicId }
    }));
    return newId;
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
    _clearStatus,
    _onContentSuccess,
    _onTestSuccess,
    _addBlockAfterSource,
    _provenance,
  };
})());

console.log("[AI] loaded");