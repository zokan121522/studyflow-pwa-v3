// ─── Courses Blocks — block CRUD/edit UI for the topic detail ─────
// Namespace: window.App.CoursesBlocks
// Dependencies: window.App.UI (escHtml), window.App.CoursesAPI,
//               window.App.ContentBlocks._renderMd, window.STATE.
//
// SCOPE (S3):
//   • render blocks for a topic (read-only preview by type)
//   • inline-edit markdown / text blocks
//   • add block via "+ Añadir bloque" affordance with type menu
//   • toggle done / delete / reorder (basic move buttons)
//
// SCOPE (S4 — additive, SF-Card visuals):
//   • _renderBlockCard(b, courseId) — richer card with category color
//     strip, type icon, title, collapsible body, up/down/done/edit/
//     delete affordances. Renders same fields as _renderBlock but
//     wraps them in a more v2-shaped "block-detail" structure so the
//     notes drawer / future cards can dock underneath.
//   • _attachCardHandlers(host, courseId, topicId) — extension that
//     handles up/down reorder (PATCH /courses/<cid>/blocks with
//     reordered ids) + collapse toggle. Composes on top of
//     _attachBlockHandlers (which still owns edit/save/cancel/done/
//     delete/add).
//
// Block types supported: markdown, content (plain text), separator,
// pdf-ref (url only), youtube (url only). Other types fall back to a
// generic read-only card.

window.App = window.App || {};
window.App.CoursesBlocks = (function () {
  "use strict";

  const { escHtml } = window.App.UI;
  const {
    addBlock, updateBlock, deleteBlock,
    toggleBlockDone, reorderBlocks, listTopicBlocks,
  } = window.App.CoursesAPI;
  const { _renderMd } = window.App.ContentBlocks;

  // ── Block-type → icon + label + color strip (UI affordances) ──
  // color maps each type to a thin gradient strip on the left edge of
  // the card (S4 SF-Card look).
  const TYPE_META = {
    markdown: { icon: "📝", label: "Markdown", strip: "var(--primary)" },
    content:  { icon: "📄", label: "Texto",    strip: "#3b82f6" },
    separator:{ icon: "➖", label: "Separador",strip: "var(--border-light)" },
    "pdf-ref":{ icon: "📕", label: "PDF-link", strip: "#ef4444" },
    youtube:  { icon: "▶️", label: "YouTube",  strip: "#f59e0b" },
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
  // "+ Añadir bloque" affordance: a single button that reveals a
  // dropdown of supported block types.
  function _renderAddBar(courseId, topicId) {
    const topicAttr = topicId ? ` data-topic-id="${topicId}"` : "";
    let html = `<div class="sf-td-add-bar" data-course-id="${courseId}"${topicAttr}>`;
    html += `<button class="sf-td-add-toggle ht-btn">`
      + `➕ Añadir bloque</button>`;
    html += `<div class="sf-td-add-menu">`;
    for (const [type, m] of Object.entries(TYPE_META)) {
      html += `<div class="sf-td-add-item" data-type="${escHtml(type)}">`
        + `<span class="sf-td-add-icon">${m.icon}</span>`
        + `<span>${escHtml(m.label)}</span></div>`;
    }
    html += `</div></div>`;
    return html;
  }

  // ── _renderEditForm(block, courseId) → HTML ──────────────────
  // Inline edit form. Markdown + content get a textarea; pdf-ref and
  // youtube get a URL input; separator gets a label input only.
  function _renderEditForm(b, courseId) {
    const type = b.type || "markdown";
    const title = escHtml(b.title || "");
    let bodyField = "";
    if (type === "markdown") {
      bodyField = `<textarea class="sf-td-editor" rows="8" placeholder="Markdown…">${
        escHtml(b.content || "")
      }</textarea>`;
    } else if (type === "content") {
      bodyField = `<textarea class="sf-td-editor" rows="6" placeholder="Texto…">${
        escHtml(b.content || "")
      }</textarea>`;
    } else if (type === "pdf-ref" || type === "youtube") {
      bodyField = `<input class="sf-td-url" type="text" value="${
        escHtml(b.url || "")
      }" placeholder="${
        type === "pdf-ref" ? "Ruta o URL del PDF" : "URL de YouTube"
      }" />`;
    } else if (type === "separator") {
      bodyField = `<div class="sf-empty">Los separadores solo tienen etiqueta</div>`;
    }
    return `<div class="sf-td-edit-inner">
      <input class="sf-td-title-input" type="text" value="${title}" placeholder="Título" />
      ${bodyField}
      <div class="sf-td-edit-actions">
        <button class="sf-td-save ht-btn">💾 Guardar</button>
        <button class="sf-td-cancel ht-btn ht-btn-ghost">Cancelar</button>
      </div>
    </div>`;
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
        const blockEl = doneCb.closest(".sf-td-block");
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
        const blockEl = editBtn.closest(".sf-td-block");
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
          // Hide read-only body
          const body = blockEl.querySelector(".sf-td-block-body");
          if (body) body.style.display = "none";
          blockEl.classList.add("is-editing");
        } catch (err) {
          alert("❌ Error: " + (err.message || err));
        }
        return;
      }

      // Save
      const saveBtn = e.target.closest(".sf-td-save");
      if (saveBtn) {
        e.stopPropagation();
        const blockEl = saveBtn.closest(".sf-td-block");
        if (!blockEl) return;
        const bid = Number(blockEl.dataset.blockId);
        const titleInput = blockEl.querySelector(".sf-td-title-input");
        const editor = blockEl.querySelector(".sf-td-editor");
        const urlInput = blockEl.querySelector(".sf-td-url");
        const payload = { title: (titleInput && titleInput.value || "").trim() };
        if (editor) payload.content = editor.value;
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
        const blockEl = cancelBtn.closest(".sf-td-block");
        if (!blockEl) return;
        const form = blockEl.querySelector(".sf-td-edit-form");
        if (form) {
          form.style.display = "none";
          form.innerHTML = "";
        }
        const body = blockEl.querySelector(".sf-td-block-body");
        if (body) body.style.display = "";
        blockEl.classList.remove("is-editing");
        return;
      }

      // Delete
      const delBtn = e.target.closest(".sf-td-del");
      if (delBtn) {
        e.stopPropagation();
        const blockEl = delBtn.closest(".sf-td-block");
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

      // "+ Añadir bloque" toggle (open menu)
      const toggle = e.target.closest(".sf-td-add-toggle");
      if (toggle) {
        e.stopPropagation();
        const bar = toggle.closest(".sf-td-add-bar");
        const menu = bar && bar.querySelector(".sf-td-add-menu");
        if (menu) menu.classList.toggle("open");
        return;
      }

      // "+ Añadir bloque" item (create block)
      const item = e.target.closest(".sf-td-add-item");
      if (item) {
        e.stopPropagation();
        const bar = item.closest(".sf-td-add-bar");
        if (!bar) return;
        const cid = Number(bar.dataset.courseId);
        const tid = bar.dataset.topicId
          ? Number(bar.dataset.topicId) : null;
        const type = item.dataset.type;
        try {
          await addBlock(cid, {
            topic_id: tid, type,
            title: type === "separator" ? "Separador" : "",
            content: "",
            url: "",
          });
          const menu = bar.querySelector(".sf-td-add-menu");
          if (menu) menu.classList.remove("open");
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

    // Close add-menu when clicking outside (one-time binding per element).
    if (!centerEl.dataset._sfAddMenuClose) {
      centerEl.dataset._sfAddMenuClose = "1";
      document.addEventListener("click", () => {
        centerEl.querySelectorAll(".sf-td-add-menu.open").forEach((m) =>
          m.classList.remove("open")
        );
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
        const isCollapsed = card.classList.toggle("is-collapsed");
        collapseBtn.textContent = isCollapsed ? "▶" : "▼";
        // The visual body in .sf-bc-body contains the inner block, which
        // already has its own .sf-td-block-body. Hide it on collapse.
        const body = card.querySelector(".sf-bc-body");
        if (body) body.style.display = isCollapsed ? "none" : "";
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