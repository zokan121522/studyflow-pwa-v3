// ─── Courses Blocks — block CRUD/edit UI for the topic detail ─────
// Namespace: window.App.CoursesBlocks
// Dependencies: window.App.UI (escHtml), window.App.CoursesAPI,
//               window.App.ContentBlocks._renderMd, window.STATE,
//               window.App.MarkdownEditor (S4.5).
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
    toggleBlockDone, reorderBlocks, listTopicBlocks,
  } = window.App.CoursesAPI;
  const { _renderMd } = window.App.ContentBlocks;
  const MdEditor = window.App.MarkdownEditor;

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

  // ── _renderBlock(block, courseId) → HTML string ──────────────
  // Renders ONE block in read mode. Markdown uses _renderMd; content
  // is plain text; separator is a thin rule; pdf-ref/youtube show the
  // url as a clickable link.
  function _renderBlock(b, courseId) {
    const type = b.type || "markdown";
    const title = escHtml(b.title || "Bloque");
    const done = !!b.done;
    const m = meta(type);
    let bodyHtml = "";
    if (type === "markdown") {
      bodyHtml = `<div class="md-view">${
        _renderMd(b.content || "")
      }</div>`;
    } else if (type === "content") {
      const txt = escHtml(b.content || "").replace(/\n/g, "<br>");
      bodyHtml = `<div class="sf-text-body">${txt
        || '<span class="sf-empty">Sin contenido</span>'}</div>`;
    } else if (type === "separator") {
      bodyHtml = `<div class="sf-sep-line"></div>`;
    } else if (type === "pdf-ref") {
      const url = escHtml(b.url || "");
      bodyHtml = url
        ? `<div class="sf-link-body">📕 <a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a></div>`
        : `<div class="sf-empty">Sin URL</div>`;
    } else if (type === "youtube") {
      const url = escHtml(b.url || "");
      bodyHtml = url
        ? `<div class="sf-link-body">▶️ <a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a></div>`
        : `<div class="sf-empty">Sin URL</div>`;
    } else {
      bodyHtml = `<div class="sf-empty">Tipo ${escHtml(type)} no soportado</div>`;
    }

    return `<div class="sf-td-block ${
      done ? "is-done" : ""
    }" data-block-id="${b.id}" data-block-type="${escHtml(type)}"
       data-course-id="${courseId}">
      <div class="sf-td-block-head">
        <label class="sf-td-done" title="Marcar como hecho">
          <input type="checkbox" class="sf-td-done-cb" ${
            done ? "checked" : ""
          }/>
        </label>
        <span class="sf-td-block-icon">${m.icon}</span>
        <span class="sf-td-block-title">${title}</span>
        <span class="sf-td-block-actions">
          <button class="sf-td-edit ht-btn-mini" title="Editar">✏️</button>
          <button class="sf-td-del ht-btn-mini" title="Borrar">🗑️</button>
        </span>
      </div>
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
  function _renderAddBar(courseId, topicId) {
    const topicAttr = topicId ? ` data-topic-id="${topicId}"` : "";
    let chips = "";
    for (const [type, m] of Object.entries(TYPE_META)) {
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
      // Done toggle
      const doneCb = e.target.closest(".sf-td-done-cb");
      if (doneCb) {
        e.stopPropagation();
        const blockEl = doneCb.closest(".sf-td-block") || (doneCb.closest(".sf-block-card")||document).querySelector(".sf-td-block");
        if (!blockEl) return;
        const bid = Number(blockEl.dataset.blockId);
        try {
          await toggleBlockDone(courseId, bid);
          blockEl.classList.toggle("is-done", doneCb.checked);
        } catch (err) {
          alert("❌ Error: " + (err.message || err));
        }
        return;
      }

      // Edit
      const editBtn = e.target.closest(".sf-td-edit");
      if (editBtn) {
        e.stopPropagation();
        const blockEl = editBtn.closest(".sf-td-block") || (editBtn.closest(".sf-block-card")||document).querySelector(".sf-td-block");
        if (!blockEl) return;
        const bid = Number(blockEl.dataset.blockId);
        // Pull fresh block data from cache (or listTopicBlocks)
        try {
          const blocks = await listTopicBlocks(courseId, topicId);
          const block = (blocks || []).find((x) => x.id === bid);
          if (!block) return;
          const form = blockEl.querySelector(".sf-td-edit-form");
          if (!form) return;
          form.innerHTML = _renderEditForm(block, courseId);
          form.style.display = "block";
          // Wire the live preview (input → .md-preview) and the
          // editor↔preview scroll sync. attachLivePreview is idempotent
          // (safe to call again if the form is re-rendered).
          MdEditor.attachLivePreview(form);
          // Hide read-only body
          const body = blockEl.querySelector(".sf-td-block-body");
          if (body) body.style.display = "none";
          blockEl.classList.add("is-editing");
          // Focus the title input for keyboard-driven editing.
          const titleEl = form.querySelector(".sf-td-md-title");
          if (titleEl) titleEl.focus();
        } catch (err) {
          alert("❌ Error: " + (err.message || err));
        }
        return;
      }

      // Save
      const saveBtn = e.target.closest(".sf-td-save");
      if (saveBtn) {
        e.stopPropagation();
        const blockEl = saveBtn.closest(".sf-td-block") || (saveBtn.closest(".sf-block-card")||document).querySelector(".sf-td-block");
        if (!blockEl) return;
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
          await updateBlock(courseId, bid, payload);
          // Force the topic panel to re-render via the center-updater.
          const evt = new CustomEvent("studyflow:blocks-changed", {
            detail: { courseId, topicId }
          });
          window.dispatchEvent(evt);
        } catch (err) {
          alert("❌ Error al guardar: " + (err.message || err));
        }
        return;
      }

      // Cancel edit
      const cancelBtn = e.target.closest(".sf-td-cancel");
      if (cancelBtn) {
        e.stopPropagation();
        const blockEl = cancelBtn.closest(".sf-td-block") || (cancelBtn.closest(".sf-block-card")||document).querySelector(".sf-td-block");
        if (!blockEl) return;
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
        const blockEl = delBtn.closest(".sf-td-block") || (delBtn.closest(".sf-block-card")||document).querySelector(".sf-td-block");
        if (!blockEl) return;
        const bid = Number(blockEl.dataset.blockId);
        if (!confirm("¿Borrar este bloque?")) return;
        try {
          await deleteBlock(courseId, bid);
          const evt = new CustomEvent("studyflow:blocks-changed", {
            detail: { courseId, topicId }
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

      // "+ Añadir bloque" chip (create block of the matching type)
      const chip = e.target.closest(".sf-td-add-chip");
      if (chip) {
        e.stopPropagation();
        const bar = chip.closest(".sf-td-add-bar");
        if (!bar) return;
        const cid = Number(bar.dataset.courseId);
        const tid = bar.dataset.topicId
          ? Number(bar.dataset.topicId) : null;
        const type = chip.dataset.type;
        const meta = TYPE_META[type] || TYPE_META.markdown;
        const def = meta.defaults || { content: "", url: "", title: "" };
        try {
          await addBlock(cid, {
            topic_id: tid, type,
            title: def.title || meta.label,
            content: def.content || "",
            url: def.url || "",
          });
          const evt = new CustomEvent("studyflow:blocks-changed", {
            detail: { courseId: cid, topicId: tid }
          });
          window.dispatchEvent(evt);
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
  //   • up / down reorder buttons in the header
  //   • keeps the existing done / edit / delete affordances
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
          <button class="sf-bc-up ht-btn-mini" title="Subir">⬆️</button>
          <button class="sf-bc-down ht-btn-mini" title="Bajar">⬇️</button>
          <button class="sf-td-edit ht-btn-mini" title="Editar">✏️</button>
          <button class="sf-td-del ht-btn-mini" title="Borrar">🗑️</button>
        </span>
      </header>
      <div class="sf-bc-body">${innerBody}</div>
    </div>`;
  }

  // ── _attachCardHandlers(host, courseId, topicId) ──────────────
  // Idempotent: delegates collapse / up / down clicks on .sf-block-card.
  // Reorder: reads the current DOM order of block ids and calls the
  // existing reorderBlocks API, then dispatches studyflow:blocks-changed
  // so updateCenter re-renders.
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
            await updateBlock(courseId, bid, { collapsed: isCollapsed });
          } catch (_) { /* best-effort */ }
        }
        return;
      }

      // Up / Down reorder
      const bump = e.target.closest(".sf-bc-up, .sf-bc-down");
      if (bump) {
        e.stopPropagation();
        const card = bump.closest(".sf-block-card");
        if (!card) return;
        const direction = bump.classList.contains("sf-bc-up") ? -1 : 1;
        const sibling = direction === -1
          ? card.previousElementSibling
          : card.nextElementSibling;
        if (!sibling
            || !sibling.classList.contains("sf-block-card")) return;
        const parent = card.parentElement;
        if (direction === -1) parent.insertBefore(card, sibling);
        else parent.insertBefore(sibling, card);
        // Collect the new order from the DOM and PATCH.
        const ids = Array.from(
          parent.querySelectorAll(".sf-block-card")
        ).map((el) => Number(el.dataset.blockId));
        try {
          await reorderBlocks(courseId, ids);
          const evt = new CustomEvent("studyflow:blocks-changed", {
            detail: { courseId, topicId }
          });
          window.dispatchEvent(evt);
        } catch (err) {
          alert("❌ Error al reordenar: " + (err.message || err));
        }
        return;
      }
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