// ─── Courses Blocks — block CRUD/edit UI for the topic detail ─────
// Namespace: window.App.CoursesBlocks
// Dependencies: window.App.UI (escHtml), window.App.CoursesAPI,
//               window.App.ContentBlocks._renderMd, window.STATE,
//               window.App.MarkdownEditor (S4.5),
//               window.App.PdfImport (S7b — unified PDF + SCORM import).
//
// SCOPE (S3):
//   • render blocks for a topic (read-only preview by type)
//   • add block via "+ Añadir bloque" affordance (chip menu)
//
// SCOPE (S4 — additive, SF-Card visuals):
//   • _renderBlockCard(b, courseId) — richer card with category color
//     strip, type icon, title, collapsible body, up/down/done/edit/
//     delete affordances.
//   • _attachCardHandlers(host, courseId, topicId) — collapse (with
//     backend persistence via PUT {collapsed}) + up/down reorder.
//
// SCOPE (S4.5 — full v2 markdown editor):
//   • Edit form is now delegated to App.MarkdownEditor.editForm(block):
//     title input + toolbar (13 buttons + 🎨 color panel) + SPLIT
//     editor (textarea | live preview) wired by attachLivePreview().
//   • "+ Añadir bloque" bar becomes the v2 chip menu: 📝 Markdown,
//     📄 Texto, 📕 PDF, ▶️ YouTube, 🖼 Imagen, 🌐 Web, ❓ Ejercicio,
//     ➖ Separador. Types backed by v3 backend (markdown/content/
//     pdf-ref/youtube) create real blocks; types whose viewers land
//     in S7/S9 (exercise/interactive) create a markdown placeholder
//     with the right title so the block renders.
//
// Block types supported: markdown, content (plain text), separator,
// pdf-ref (url only), youtube (url only), image, exercise,
// interactive. Unknown types fall back to a generic read-only card.

window.App = window.App || {};
window.App.CoursesBlocks = (function () {
  "use strict";

  const { escHtml } = window.App.UI;
  const {
addBlock, updateBlock, deleteBlock,
      listTopicBlocks, fetchCourseDetail, moveBlock,
  } = window.App.CoursesAPI;
  const { _renderMd } = window.App.ContentBlocks;
  const MdEditor = window.App.MarkdownEditor;

  function _showBlockNotice(blockEl, msg) {
    // This view has no [data-ai-status] node (that lives in ai.js's own
    // renderer), so the card's edit slot doubles as the message area.
    const host = blockEl && (blockEl.closest(".sf-block-card") || blockEl);
    const form = host && host.querySelector(".sf-td-edit-form");
    if (!form) return;
    form.innerHTML =
      `<p style="margin:0;color:var(--danger,#e5484d);font-size:13px">${escHtml(msg)}</p>`;
    form.style.display = "block";
  }

  // ── _ctxFrom(btn, fallbackCourseId, fallbackTopicId) ──────────
  // Resolve the course/topic a clicked control belongs to, from the DOM
  // rather than from the listener's closure.
  //
  // Why: _attachBlockHandlers is idempotent — it attaches ONE listener per
  // container and closes over the courseId/topicId that were current at that
  // moment. Navigate to another course and the listener is not re-attached,
  // so every id it holds is stale: the pencil looks the block up in the wrong
  // topic, doesn't find it, and returns silently. Worse, save and delete
  // would then PATCH/DELETE against the *previous* course.
  //
  // The fallback ids are only used if the DOM cannot answer, which for a
  // rendered card it always can.
  function _ctxFrom(btn, fallbackCourseId, fallbackTopicId) {
    const card = btn.closest(".sf-block-card");
    const blockEl =
      btn.closest(".sf-td-block") ||
      (card && card.querySelector(".sf-td-block")) ||
      card;
    if (!blockEl) return null;
    const scope = blockEl.closest(".sf-topic-detail") || centerScope(btn);
    const domCourse =
      Number(blockEl.dataset.courseId) ||
      Number(card && card.dataset.courseId) ||
      Number(scope && scope.dataset.courseId);
    const domTopic = Number(scope && scope.dataset.topicId);
    return {
      blockEl,
      card: card || blockEl,
      courseId: domCourse || fallbackCourseId,
      topicId: domTopic || fallbackTopicId,
    };
  }

  function centerScope(el) {
    return el.closest(".sf-topic-detail, .sf-td-blocks, .col-center") || null;
  }

  // ── Block-type → icon + label + strip + per-type create defaults ──
  // defaults drive the create-block payload (content/url/title seed).
  const TYPE_META = {
    markdown:    { icon: "📝", label: "Markdown",  strip: "var(--primary)",
      defaults: { content: "",  url: "", title: "" } },
    content:     { icon: "📄", label: "Texto",     strip: "#3b82f6",
      defaults: { content: "",  url: "", title: "" } },
    separator:   { icon: "➖", label: "Separador", strip: "var(--border-light)",
      defaults: { content: "",  url: "", title: "Separador" } },
    "pdf-ref":   { icon: "📕", label: "PDF",       strip: "#ef4444",
      defaults: { content: "",  url: "", title: "Nuevo PDF" } },
    youtube:     { icon: "▶️", label: "YouTube",   strip: "#f59e0b",
      defaults: { content: "",  url: "", title: "Nuevo YouTube" } },
    image:       { icon: "🖼", label: "Imagen",    strip: "#10b981",
      defaults: { content: "",  url: "", title: "Nueva imagen" } },
    exercise:    { icon: "❓", label: "Ejercicio", strip: "#a855f7",
      defaults: { content: "## Ejercicio\n\nEnunciado…",
                  url: "", title: "Nuevo ejercicio" } },
    interactive: { icon: "🌐", label: "Página web",strip: "#06b6d4",
      defaults: { content: "<h1>Hola</h1>",
                  url: "", title: "Nueva página web" } },
  };

  function meta(type) {
    return TYPE_META[type]
      || { icon: "📌", label: type || "Bloque", strip: "var(--border-light)" };
  }

  // ── _looksLikeHtml(str) → boolean ──────────────────────────────
  // Conservative heuristic: true when the string contains an opening or
  // closing tag (e.g. `<img`, `</p>`, `<audio`). Plain prose without tags
  // returns false so legacy text-only content keeps rendering as text.
  function _looksLikeHtml(s) {
    // A real tag needs a full name: <audio …>, </audio>, <img …>. The old
    // pattern only matched ONE letter (so <p> worked but <audio> never did)
    // and media blocks silently degraded to escaped text.
    return /<\/?[a-z][a-z0-9]*(?:\s[^>]*)?\/?>/i.test(String(s || ""));
  }

  // Peels the embedded media tags off the head of a mixed markdown block so
  // the player renders as a real element and the prose keeps its markdown.
  // Only a leading run of <audio>/<img>/<video>/<iframe>/<source> is taken;
  // everything after the first prose character is returned as `rest`.
  const _MEDIA_RE =
    /^\s*(?:<(audio|img|video|iframe|source)\b[^>]*>(?:[\s\S]*?<\/\1\s*>)?|<(?:audio|img|video|iframe|source)\b[^>]*\/?>)(?:\s*)/i;

  function _splitLeadingMedia(s) {
    const raw = String(s || "");
    let rest = raw;
    const collected = [];
    // Loop: several adjacent media tags may precede the prose.
    for (let i = 0; i < 8; i++) {
      const m = rest.match(_MEDIA_RE);
      if (!m) break;
      collected.push(m[0].trim());
      rest = rest.slice(m[0].length);
    }
    return { media: collected.join(""), rest };
  }

  // ── _renderBlock(block, courseId) → HTML string ──────────────
  // Renders ONE block in read mode. Markdown uses _renderMd; content
  // is plain text; separator is a thin rule; pdf-ref/youtube show the
  // url as a clickable link.
  //
  // S4.7 — when wrapped in a .sf-block-card (see _renderBlockCard),
  // the inner .sf-td-block-head must NOT render its own ✏️ / 🗑️
  // buttons — those live in the card header (sf-bc-head) along with
  // ⬆️ / ⬇️ / ▼. The duplication produced two ugly button rows per
  // block. We keep the checkbox (the only done affordance) + icon
  // + title in the inner head; actions are owned by the card head.
  function _renderBlock(b, courseId) {
    const type = b.type || "markdown";
    const title = escHtml(b.title || "Bloque");
    const done = !!b.done;
    const m = meta(type);
    let bodyHtml = "";
    if (type === "markdown") {
      // Orca blocks interleave an embedded <audio>/<img> with markdown prose.
      // Running the whole string through the sanitizer would emit the raw
      // markdown (##, **) unrendered, and running it through _renderMd
      // escapes the media into literal text. So: split the leading media
      // tags out, render them via the allowlist, and markdown-render the rest.
      const raw = b.content || "";
      const split = _splitLeadingMedia(raw);
      if (split.media) {
        const mediaHtml = window.App.UI.sanitizeHtml
          ? window.App.UI.sanitizeHtml(split.media)
          : "";
        const restHtml = split.rest.trim()
          ? `<div class="md-view">${_renderMd(split.rest)}</div>`
          : "";
        bodyHtml = `<div class="sf-html-body">${mediaHtml}</div>${restHtml}`;
      } else {
        bodyHtml = `<div class="md-view">${_renderMd(raw)}</div>`;
      }
    } else if (type === "content") {
      // Issue #9 — v2 content blocks embed HTML (infografía <img>,
      // audio <audio>, NotebookLM HTML). Detect real markup and render it
      // through the allowlist sanitizer; fall back to plain text.
      const raw = b.content || "";
      if (_looksLikeHtml(raw) && window.App.UI.sanitizeHtml) {
        bodyHtml = `<div class="sf-html-body">${
          window.App.UI.sanitizeHtml(raw)
        }</div>`;
      } else {
        const txt = escHtml(raw).replace(/\n/g, "<br>");
        bodyHtml = `<div class="sf-text-body">${txt
          || '<span class="sf-empty">Sin contenido</span>'}</div>`;
      }
    } else if (type === "separator") {
      bodyHtml = `<div class="sf-sep-line"></div>`;
    } else if (type === "pdf-ref") {
      // S7: mount a real PDF viewer (App.PdfViewer.init reads
      // data-url + data-pdf-id from the container). Falls back to a
      // bare link if the viewer module hasn't loaded yet.
      const url = b.url || "";
      const pdfIdMatch = url.match(/\/api\/pdf\/(\d+)/);
      const pdfId = pdfIdMatch ? pdfIdMatch[1] : "";
      if (url) {
        bodyHtml = `<div class="pdf-container" data-url="${escHtml(url)}" data-pdf-id="${escHtml(pdfId)}"></div>`;
      } else {
        bodyHtml = `<div class="sf-empty">Sin URL</div>`;
      }
    } else if (type === "youtube") {
      const url = escHtml(b.url || "");
      bodyHtml = url
        ? `<div class="sf-link-body">▶️ <a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a></div>`
        : `<div class="sf-empty">Sin URL</div>`;
    } else if (type === "exercise") {
      // Issue #10 — quiz por bloque: the exercise stem renders as
      // markdown and the questions live in quiz_questions.block_id.
      // The placeholder bin is filled asynchronously by App.QuizEmbed
      // (same mount pattern as PdfViewer).
      bodyHtml = `<div class="sf-quiz-bin"
        data-block-id="${b.id}"
        data-course-id="${courseId}"
        data-topic-id="${b.topic_id || ""}"
        data-stem="${escHtml(b.content || "")}"></div>`;
    } else if (type === "interactive") {
      // Issue #11 — interactive blocks embed a full HTML document
      // (AI-generated English practice exercises). The HTML is stored
      // inert inside a JSON-typed <script> (escaped `</script>` via
      // JSON-safe encoding) so nothing executes at render time;
      // App.ContentBlocks.renderInteractive mounts it in a sandboxed
      // iframe (allow-scripts, no same-origin/top-navigation).
      const raw = b.content || "";
      const enc = JSON.stringify(raw).replace(/</g, "\\u003c");
      bodyHtml = `<div class="sf-it-bin"
        data-block-id="${b.id}"
        data-course-id="${courseId}">
        <script type="application/json" class="sf-it-src">${enc}<\/script>
      </div>`;
    } else {
      bodyHtml = `<div class="sf-empty">Tipo ${escHtml(type)} no soportado</div>`;
    }

    // Phase 7.7 — AI ✨ Generate toolbar (v2 port). Rendered inside
    // markdown/content blocks (scope md → improve/convert/Gemini flows)
    // and pdf-ref blocks (scope pdf → NotebookLM PDF→MD/HTML, test,
    // infographic, audio). The toolbar is omitted when the AI module is
    // absent or when the block carries no content worth generating.
    let aiToolbarHtml = "";
    const aiMod = window.App.AI;
    if (aiMod && (type === "markdown" || type === "content" || type === "pdf-ref")) {
      try {
        aiToolbarHtml = type === "pdf-ref"
          ? aiMod.renderPdfAiButtonsHtml(b.id, b.topic_id || "")
          : aiMod.renderMdAiButtonsHtml(b.id, b.topic_id || "");
      } catch (_) {
        aiToolbarHtml = "";
      }
    }

    return `<div class="sf-td-block ${
      done ? "is-done" : ""
    }" data-block-id="${b.id}" data-block-type="${escHtml(type)}"
       data-course-id="${courseId}">
      <div class="sf-td-block-head sf-td-block-head-inner">
        <span class="sf-td-block-icon">${m.icon}</span>
        <span class="sf-td-block-title">${title}</span>
      </div>
      ${aiToolbarHtml}
      <div class="sf-td-block-body">${bodyHtml}</div>
      <div class="sf-td-edit-form" style="display:none;"></div>
    </div>`;
  }

  // ── _renderAddBar(courseId, topicId) → HTML ──────────────────
  // v2-style "+ Añadir bloque" bar. The toggle reveals a horizontal
  // row of type-specific chips (markdown / content / pdf / youtube /
  // image / exercise / interactive / separator). Each chip posts a
  // new block via App.CoursesAPI.addBlock using the type's defaults
  // (see TYPE_META.defaults) so the new block renders immediately.
  // Block types hidden from the "Añadir bloque" menu. The type stays in
  // TYPE_META so existing blocks (28 separators in the DB) keep rendering.
  const HIDDEN_ADD_TYPES = new Set(["separator"]);

  function _renderAddBar(courseId, topicId) {
    const topicAttr = topicId ? ` data-topic-id="${topicId}"` : "";
    let chips = "";
    for (const [type, m] of Object.entries(TYPE_META)) {
      if (HIDDEN_ADD_TYPES.has(type)) continue;
      chips += `<button type="button" class="sf-td-add-chip"`
        + ` data-type="${escHtml(type)}"`
        + ` title="${escHtml(m.label)}">`
        + `<span class="sf-td-add-chip-icon">${m.icon}</span>`
        + `<span>${escHtml(m.label)}</span></button>`;
    }
    return `<div class="sf-td-add-bar" data-course-id="${courseId}"${topicAttr}>
      <button type="button" class="sf-td-add-toggle ht-btn">
        <span class="sf-td-add-toggle-icon">➕</span>Añadir bloque
      </button>
      <div class="sf-td-add-chips">${chips}</div>
    </div>`;
  }

  // ── _renderEditForm(block, courseId) → HTML ──────────────────
  // S4.5: delegate to App.MarkdownEditor.editForm. That module owns the
  // toolbar + 🎨 + split live preview markup. After injecting the form
  // we call attachLivePreview() to wire the textarea → preview sync.
  function _renderEditForm(b, courseId) {
    const html = MdEditor.editForm(b);
    // Reset any pending color selection from a previous form so the
    // 🎨 button in the new form captures the right selection.
    MdEditor.resetPending();
    return html;
  }

  // ── _embedPdfBlock(courseId, topicId, sourceBlockId, pdf, title) ─
// Create a real `pdf-ref` block right below the source markdown, the same
// shape as the PDFs the user added by hand (url = /api/pdf/<id>). That is what
// makes it open in the embedded viewer and be annotatable — a bare <a> in the
// note body would only ever download the file.
//
// Re-exporting the same note reuses the block that is already there instead of
// adding a second one, so repeated exports do not pile up identical entries.
async function _embedPdfBlock(domCourse, domTopic, sourceBlockId, pdf, title) {
    if (!domCourse || !domTopic || !pdf) throw new Error("Faltan datos del PDF");

    const url = `/api/pdf/${pdf.id}`;
    const blocks = await listTopicBlocks(domCourse, domTopic);
    const list = blocks || [];
    const sourceIdx = list.findIndex((b) => Number(b.id) === Number(sourceBlockId));

    // A ref generated by this button sits immediately after its source; that
    // is the one to refresh, not a PDF the user added by hand.
    const existing = sourceIdx !== -1 ? list[sourceIdx + 1] : null;
    if (existing && existing.type === "pdf-ref" && existing.url === url) {
      await updateBlock(domCourse, existing.id, { title });
      _flashRef(existing.id);
    } else {
      const created = await addBlock(domCourse, {
        topic_id: domTopic,
        type: "pdf-ref",
        title,
        url,
      });
      // addBlock appends to the end of the topic, and the move endpoint only
      // sets an ABSOLUTE order_index — it does not shift its neighbours, so
      // writing sourceIdx + 1 blindly lands on top of whatever already sits
      // there and the two then tie (resolved only by id). Advance past the
      // occupied slots instead: the result is still directly below the
      // markdown, without rewriting rows the user owns.
      let target = sourceIdx === -1 ? -1 : sourceIdx + 1;
      if (target > -1 && moveBlock) {
        while (list.some((b) => Number(b.order_index) === target
                         && Number(b.id) !== Number(created.id))) {
          target += 1;
        }
        try {
          await moveBlock(created.id, {
            target_topic_id: domTopic,
            index: target,
          });
        } catch (_) { /* position is cosmetic; the block exists either way */ }
      }
    }

    window.dispatchEvent(new CustomEvent("studyflow:blocks-changed", {
      detail: { domCourse, domTopic },
    }));
  }

  // Brief highlight so the user sees where the PDF landed after the re-render.
  function _flashRef(blockId) {
    try {
      const el = document.querySelector(`.sf-block-card[data-block-id="${blockId}"]`);
      if (!el) return;
      el.scrollIntoView({ block: "nearest" });
      el.animate(
        [{ outlineColor: "rgba(56,139,253,.9)" }, { outlineColor: "transparent" }],
        { duration: 1400, easing: "ease-out" }
      );
    } catch (_) { /* highlight is optional */ }
  }

  // ── _attachBlockHandlers(centerEl, courseId, topicId) ─────────
  // Delegate clicks for edit / save / cancel / done / delete / add.
  // IDEMPOTENT: re-renders call this repeatedly but only the first call
  // attaches a listener (subsequent calls are no-ops). Without this
  // guard, every CRUD would compound handlers and cause N×click → N×API
  // calls (observed: 4 PATCH /done for 1 toggle, N× DELETE → 404s).
  function _attachBlockHandlers(centerEl, courseId, topicId) {
    if (!centerEl) return;
    if (centerEl.dataset._sfBlocksHandlers === "1") return;
    centerEl.dataset._sfBlocksHandlers = "1";

    centerEl.addEventListener("click", async (e) => {
      // NOTE: done-toggle checkbox was removed from the block body (v2
      // parity — done lives only in the sidebar block checkbox). Keep
      // this listener purely for edit / save / delete / add actions.

      // Edit
      const editBtn = e.target.closest(".sf-td-edit");
      if (editBtn) {
        e.stopPropagation();
        const ctx = _ctxFrom(editBtn, courseId, topicId);
        if (!ctx) {
          _showBlockNotice(editBtn.closest(".sf-block-card") || editBtn,
            "⚠️ No encuentro la tarjeta del bloque.");
          return;
        }
        const { blockEl, card: cardEl, courseId: domCourseId, topicId: domTopicId } = ctx;
        const bid = Number(blockEl.dataset.blockId);
        // A folded card hides its whole body (`.is-collapsed .sf-bc-body
        // {display:none}`) and the editor is rendered inside that body, so
        // editing a folded block produced a form of height 0: the pencil did
        // nothing, the same dead-button report as the stale-closure bug above.
        // Unfold first, then edit. `is-collapsed` lives on the .sf-block-card,
        // not on the inner .sf-td-block that blockEl points at — toggling the
        // wrong one is a no-op that looks like the fix did not take.
        const wasCollapsed = !!(cardEl && cardEl.classList.contains("is-collapsed"));
        if (wasCollapsed) {
          cardEl.classList.remove("is-collapsed");
          const arrow = cardEl.querySelector(".sf-bc-collapse");
          if (arrow) arrow.textContent = "▼";
        }
        // Pull fresh block data from cache (or listTopicBlocks)
        try {
          let block = null;
          const blocks = await listTopicBlocks(domCourseId, domTopicId);
          block = (blocks || []).find((x) => x.id === bid);
          if (!block) {
            // The card is on screen but its id is gone from the topic list —
            // a concurrent delete, or a block whose topic_id disagrees with
            // the topic it is rendered under. Try the course's flat list
            // before giving up; if that misses too, the block really is
            // gone. Previously this was a bare `return`, so the pencil did
            // nothing at all and the only cure was a full reload.
            const detail = await fetchCourseDetail(domCourseId);
            block = (detail.blocks || []).find((x) => x.id === bid);
          }
          if (!block) {
            // Say so. A silent no-op here is indistinguishable from a broken
            // button, which is how "it won't let me edit this" went
            // unreported for so long.
            _showBlockNotice(blockEl, "⚠️ Este bloque ya no existe. Recarga la vista para actualizar.");
            return;
          }
          const form = blockEl.querySelector(".sf-td-edit-form");
          if (!form) {
            _showBlockNotice(blockEl, "⚠️ Este tipo de bloque no se puede editar aquí.");
            return;
          }
          form.innerHTML = _renderEditForm(block, domCourseId);
          form.style.display = "block";
          // Wire the live preview (input → .md-preview) and the
          // editor↔preview scroll sync. attachLivePreview is idempotent
          // (safe to call again if the form is re-rendered).
          MdEditor.attachLivePreview(form);
          // S7b: for pdf-ref blocks, inject the unified Importar button
          // so users get one place to either upload a PDF or import a
          // SCORM package / Moodle URL.
          if (block.type === "pdf-ref" && window.App.PdfImport) {
            window.App.PdfImport.injectIntoEditForm(
              form, block,
              { courseId, topicId, anchor: blockEl }
            );
          }
          // Hide read-only body
          const body = blockEl.querySelector(".sf-td-block-body");
          if (body) body.style.display = "none";
          blockEl.classList.add("is-editing");
          // Focus the title input for keyboard-driven editing.
          const titleEl = form.querySelector(".sf-td-md-title");
          if (titleEl) titleEl.focus();
          // Persist the unfold we just did, so the card does not snap back
          // to folded on the next render. Silent like the other persistence
          // here: the edit itself must not fail because a flag did not save.
          if (wasCollapsed) {
            try {
              await updateBlock(domCourseId, bid, { collapsed: false });
            } catch (_) { /* keep editing; the flag re-syncs on next render */ }
          }
        } catch (err) {
          alert("❌ Error: " + (err.message || err));
        }
        return;
      }

      // Save
      const saveBtn = e.target.closest(".sf-td-save");
      if (saveBtn) {
        e.stopPropagation();
        const ctxSave = _ctxFrom(saveBtn, courseId, topicId);
        if (!ctxSave) return;
        const blockEl = ctxSave.blockEl;
        const bid = Number(blockEl.dataset.blockId);
        const titleInput = blockEl.querySelector(".sf-td-md-title");
        const editor = blockEl.querySelector(".md-editor");
        const plain = blockEl.querySelector(".sf-td-md-plain");
        const urlInput = blockEl.querySelector(".sf-td-md-url");
        const payload = {
          title: (titleInput && titleInput.value || "").trim(),
        };
        if (editor) payload.content = editor.value;
        if (plain) payload.content = plain.value;
        if (urlInput) payload.url = urlInput.value.trim();
        try {
          await updateBlock(ctxSave.courseId, bid, payload);
          // Force the topic panel to re-render via the center-updater.
          const evt = new CustomEvent("studyflow:blocks-changed", {
            detail: { courseId: ctxSave.courseId, topicId: ctxSave.topicId }
          });
          window.dispatchEvent(evt);
        } catch (err) {
          alert("❌ Error al guardar: " + (err.message || err));
        }
        return;
      }

      // MD → PDF: send this block's markdown to the server, which renders it
      // with the same parser this view uses and stores the result in the pdfs
      // table. The link is appended below the note instead of replacing the
      // block, so the markdown the user wrote stays editable afterwards.
      const pdfBtn = e.target.closest(".sf-md-pdf");
      if (pdfBtn) {
        e.stopPropagation();
        const ctxPdf = _ctxFrom(pdfBtn, courseId, topicId);
        if (!ctxPdf) {
          _showBlockNotice(pdfBtn.closest(".sf-block-card") || pdfBtn,
            "⚠️ No encuentro la tarjeta del bloque.");
          return;
        }
        const cardEl = ctxPdf.card;
        const bidPdf = Number(cardEl.dataset.blockId);

        // In read mode there is no textarea to read from: .sf-td-md-plain only
        // exists inside the edit form. The markdown has to come from the API,
        // the same way ai.js reads a source block before moving it.
        let md = "";
        let titleMd = (cardEl.querySelector(".sf-bc-title") || {}).textContent || "";
        try {
          const blocks = await listTopicBlocks(
            ctxPdf.courseId, ctxPdf.topicId);
          const src = (blocks || []).find((b) => Number(b.id) === bidPdf);
          if (src) {
            md = src.content || "";
            if (src.title) titleMd = src.title;
          }
        } catch (_) {
          md = "";
        }
        titleMd = String(titleMd).trim();

        if (!md || !md.trim()) {
          _showBlockNotice(cardEl, "⚠️ Este bloque no tiene texto para convertir.");
          return;
        }

        const oldLabel = pdfBtn.textContent;
        pdfBtn.textContent = "⏳";
        pdfBtn.disabled = true;
        try {
          const res = await apiRequest("/pdf/md2pdf", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              markdown: md,
              title: titleMd,
              course_id: ctxPdf.courseId,
              topic_id: ctxPdf.topicId,
            }),
          });
          const pdf = res.pdf;
          await _embedPdfBlock(ctxPdf.courseId, ctxPdf.topicId,
            bidPdf, pdf, titleMd);
          pdfBtn.textContent = "📄→PDF";
        } catch (err) {
          _showBlockNotice(cardEl, "⚠️ " + (err.message || "No se pudo generar el PDF"));
          pdfBtn.textContent = oldLabel;
        } finally {
          pdfBtn.disabled = false;
        }
        return;
      }

      // Cancel edit
      const cancelBtn = e.target.closest(".sf-td-cancel");
      if (cancelBtn) {
        e.stopPropagation();
        const ctxCancel = _ctxFrom(cancelBtn, courseId, topicId);
        if (!ctxCancel) return;
        const blockEl = ctxCancel.blockEl;
        const form = blockEl.querySelector(".sf-td-edit-form");

        if (form) {
          form.style.display = "none";
          form.innerHTML = "";
        }
        MdEditor.resetPending();
        const body = blockEl.querySelector(".sf-td-block-body");
        if (body) body.style.display = "";
        blockEl.classList.remove("is-editing");
        return;
      }

      // Delete
      const delBtn = e.target.closest(".sf-td-del");
      if (delBtn) {
        e.stopPropagation();
        const ctxDel = _ctxFrom(delBtn, courseId, topicId);
        if (!ctxDel) return;
        const blockEl = ctxDel.blockEl;
        const bid = Number(blockEl.dataset.blockId);
        if (!confirm("¿Borrar este bloque?")) return;
        try {
          await deleteBlock(ctxDel.courseId, bid);
          const evt = new CustomEvent("studyflow:blocks-changed", {
            detail: { courseId: ctxDel.courseId, topicId: ctxDel.topicId }
          });
          window.dispatchEvent(evt);
        } catch (err) {
          alert("❌ Error al borrar: " + (err.message || err));
        }
        return;
      }

      // "+ Añadir bloque" toggle (open chip row)
      const toggle = e.target.closest(".sf-td-add-toggle");
      if (toggle) {
        e.stopPropagation();
        const bar = toggle.closest(".sf-td-add-bar");
        if (bar) bar.classList.toggle("is-open");
        return;
      }

      // "+ Añadir bloque" chip (create block of the matching type).
      // Works in BOTH contexts: inside the global .sf-td-add-bar (empty
      // topics) and inside the per-block ➕ Añadir toolbar group (ai.js
      // _renderGenerateSectionHtml). course/topic ids come from the bar
      // when present, otherwise from the closest block toolbar + card.
      const chip = e.target.closest(".sf-td-add-chip");
      if (chip) {
        e.stopPropagation();
        const bar = chip.closest(".sf-td-add-bar");
        let cid = Number(bar ? bar.dataset.courseId : 0);
        let tid = bar && bar.dataset.topicId ? Number(bar.dataset.topicId) : null;
        if (!cid) {
          cid = Number((chip.closest("[data-course-id]") || {}).dataset
            && chip.closest("[data-course-id]").dataset.courseId) || 0;
        }
        if (tid === null) {
          const toolbar = chip.closest(".block-toolbar");
          const t = toolbar ? toolbar.dataset.topicId
            : (chip.closest(".toolbar-group") || {}).dataset
            && chip.closest(".toolbar-group").dataset.topicId;
          tid = t ? Number(t) : null;
        }
        if (!cid) return;
        const type = chip.dataset.type;
        const meta = TYPE_META[type] || TYPE_META.markdown;
        const def = meta.defaults || { content: "", url: "", title: "" };
        try {
          const created = await addBlock(cid, {
            topic_id: tid, type,
            title: def.title || meta.label,
            content: def.content || "",
            url: def.url || "",
          });
          const evt = new CustomEvent("studyflow:blocks-changed", {
            detail: { courseId: cid, topicId: tid }
          });
          window.dispatchEvent(evt);
          // S7b: for the 📕 PDF chip, open the unified import popover
          // IMMEDIATELY so the user can drop a file or paste a SCORM
          // URL without a second click. The popover PATCHes the block
          // on success so the viewer mounts the freshly-imported PDF.
          if (type === "pdf-ref" && window.App.PdfImport
              && created && created.id) {
            window.App.PdfImport.open(created, {
              courseId: cid, topicId: tid, anchor: chip,
            });
          }
        } catch (err) {
          alert("❌ Error al crear bloque: " + (err.message || err));
        }
        return;
      }
    });

    // Close chip row when clicking outside (one-time binding per element).
    if (!centerEl.dataset._sfAddChipsClose) {
      centerEl.dataset._sfAddChipsClose = "1";
      document.addEventListener("click", (ev) => {
        centerEl.querySelectorAll(".sf-td-add-bar.is-open").forEach((b) => {
          if (!b.contains(ev.target)) b.classList.remove("is-open");
        });
      });
    }
  }

  // ============================== S4 (SF-Card) ==============================
  // ── _renderBlockCard(b, courseId) ─────────────────────────────
  // v2-style "block-detail" wrapper around the S3 block body. Adds:
  //   • a left-edge color strip derived from the block type
  //   • a collapsed/expanded toggle on the header (▼ / ▶)
  //   • keeps the existing done / edit / delete affordances
  // NOTE: reorder happens via drag&drop in the sidebar, so there are
  // no up/down buttons here (removed per Señor).
  // The body is the same S3 markup so edit/save handlers wired by
  // _attachBlockHandlers keep working without changes.
  function _renderBlockCard(b, courseId) {
    const type = b.type || "markdown";
    const title = escHtml(b.title || "Bloque");
    const done = !!b.done;
    const collapsed = !!b.collapsed;
    const m = meta(type);
    // SF-Card inner body — reuse the S3 block markup but inside the new
    // card chrome so we get color-strip / collapse / reorder.
    const innerBody = _renderBlock(b, courseId);
    return `<div class="sf-block-card sf-block-card-type-${escHtml(type)} ${
      done ? "is-done" : ""
    } ${collapsed ? "is-collapsed" : ""}"
        data-block-id="${b.id}" data-block-type="${escHtml(type)}"
        data-course-id="${courseId}">
      <div class="sf-bc-strip" style="background:${m.strip};"></div>
      <header class="sf-bc-head">
        <button class="sf-bc-collapse ht-btn-mini" title="Plegar / desplegar">${
          collapsed ? "▶" : "▼"
        }</button>
        <span class="sf-bc-icon">${m.icon}</span>
        <span class="sf-bc-title">${title}</span>
        <span class="sf-bc-actions">
          <button class="sf-td-edit ht-btn-mini" title="Editar">✏️</button>
          <button class="sf-td-del ht-btn-mini" title="Borrar">🗑️</button>
        </span>
      </header>
      <div class="sf-bc-body">${innerBody}</div>
    </div>`;
  }

  // ── _attachCardHandlers(host, courseId, topicId) ──────────────
  // Idempotent: delegates collapse clicks on .sf-block-card.
  // Reorder is handled via drag&drop in the sidebar (no up/down
  // buttons on the card).
  function _attachCardHandlers(host, courseId, topicId) {
    if (!host) return;
    if (host.dataset._sfCardHandlers === "1") return;
    host.dataset._sfCardHandlers = "1";

    host.addEventListener("click", async (e) => {
      // Collapse toggle
      const collapseBtn = e.target.closest(".sf-bc-collapse");
      if (collapseBtn) {
        e.stopPropagation();
        const card = collapseBtn.closest(".sf-block-card");
        if (!card) return;
        const bid = Number(card.dataset.blockId);
        const isCollapsed = card.classList.toggle("is-collapsed");
        collapseBtn.textContent = isCollapsed ? "▶" : "▼";
        // The visual body in .sf-bc-body contains the inner block, which
        // already has its own .sf-td-block-body. Hide it on collapse.
        const body = card.querySelector(".sf-bc-body");
        if (body) body.style.display = isCollapsed ? "none" : "";
        // S4.5 — persist the collapsed state on the server. Silent
        // failure: the local visual state already updated, so a network
        // hiccup just means the next re-render shows the old state.
        if (Number.isFinite(bid)) {
          try {
            // Same stale-closure trap as the other handlers: derive the
            // course from the card that was actually clicked.
            const domCourse =
              Number(card.dataset.courseId) ||
              Number((card.closest(".sf-topic-detail") || {}).dataset?.courseId) ||
              courseId;
            await updateBlock(domCourse, bid, { collapsed: isCollapsed });
          } catch (_) { /* best-effort */ }
        }
        return;
      }

      return;
    });
  }

  // ── Public API ───────────────────────────────────────────────
  return {
    _renderBlock,
    _renderAddBar,
    _renderEditForm,
    _attachBlockHandlers,
    // S4 (SF-Card visuals + reorder)
    _renderBlockCard,
    _attachCardHandlers,
    TYPE_META,
  };
})();

console.log("[Studyflow] courses-blocks.js loaded");