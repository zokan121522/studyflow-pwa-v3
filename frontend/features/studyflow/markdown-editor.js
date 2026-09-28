// ─── Studyflow Markdown Editor — toolbar / color / SPLIT live preview ─────
// Sub-phase S4.5.  Namespace: window.App.MarkdownEditor.
// Dependencies (resolved lazily to avoid cycle with content-blocks.js):
//   window.App.ContentBlocks._renderMd  — markdown → safe HTML renderer.
//   window.App.UI.escHtml               — used to escape textarea content on
//                                          form hydration.
//
// Why a separate module?  v2 inlines these helpers inside a 2,300-line
// courses.js IIFE.  In v3 we want them usable from anywhere (block editor,
// session overlay, future AI previews).  The toolbar+split editor also
// appears in the topic-detail inline edit; courses-blocks.js delegates the
// heavy lifting here so its own file stays under the 500-LOC ceiling.
//
// LINE-FOR-LINE PORT FROM v2 courses.js:
//   _renderMarkdownToolbar   lines 415-455      (toolbar HTML + color panel)
//   applyMarkdown            lines 482-531      (wraps/inserts md syntax)
//   toggleColorPicker        lines 537-553      (open/close color panel)
//   pickColor / Free / Clear lines 555-566      (apply color to selection)
//   _applyPendingColor       lines 568-595      (wrap word / noop on clear)
//   _removeColorSpan         lines 597-611      (strip existing color span)
//   live preview wiring      lines 1003-1090    (input → _renderMd + sync
//                                                scroll + preview→md map)
//   md-edit-form markup      lines 1568         (collapsed wrapper around the
//                                                title input / toolbar / split)

window.App = window.App || {};
window.App.MarkdownEditor = (function () {
  "use strict";

  const escHtml = (window.App.UI && window.App.UI.escHtml)
    || function (s) {
      return String(s == null ? "" : s)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
    };

  // ── Pending color state (shared across all editors on the page) ──
  // Remembered when the user opens the 🎨 panel because clicking a swatch
  // steals focus from the textarea and would otherwise lose the selection.
  let _pendingColor = null;

  // ───────────────────────────────────────────────────────────────
  // toolbar() → HTML string with every md formatting button + 🎨
  // ───────────────────────────────────────────────────────────────
  // Calls back into the global MarkdownEditor namespace (this module) so
  // the inline onclicks remain resolvable even when courses-blocks.js
  // hasn't loaded yet. Ported verbatim from v2 courses.js L415-455.
  function toolbar() {
    const M = "window.App.MarkdownEditor";
    return `<div class="md-toolbar">
      <button type="button" class="md-tb-btn" onclick="${M}.applyMarkdown(this,'bold')" title="Negrita"><strong>B</strong></button>
      <button type="button" class="md-tb-btn" onclick="${M}.applyMarkdown(this,'italic')" title="Cursiva"><em>I</em></button>
      <span class="md-tb-sep"></span>
      <button type="button" class="md-tb-btn" onclick="${M}.applyMarkdown(this,'h1')" title="Título 1">H1</button>
      <button type="button" class="md-tb-btn" onclick="${M}.applyMarkdown(this,'h2')" title="Título 2">H2</button>
      <button type="button" class="md-tb-btn" onclick="${M}.applyMarkdown(this,'h3')" title="Título 3">H3</button>
      <span class="md-tb-sep"></span>
      <button type="button" class="md-tb-btn" onclick="${M}.applyMarkdown(this,'ul')" title="Lista">≡</button>
      <button type="button" class="md-tb-btn" onclick="${M}.applyMarkdown(this,'ol')" title="Lista numerada">#.</button>
      <span class="md-tb-sep"></span>
      <button type="button" class="md-tb-btn" onclick="${M}.applyMarkdown(this,'link')" title="Enlace">🔗</button>
      <button type="button" class="md-tb-btn" onclick="${M}.applyMarkdown(this,'code')" title="Código">&lt;/&gt;</button>
      <button type="button" class="md-tb-btn" onclick="${M}.applyMarkdown(this,'table')" title="Tabla">⊞</button>
      <span class="md-tb-sep"></span>
      <button type="button" class="md-tb-btn" onclick="${M}.applyMarkdown(this,'blockquote')" title="Cita">❝</button>
      <button type="button" class="md-tb-btn" onclick="${M}.applyMarkdown(this,'codeblock')" title="Bloque de código">&lt;/&gt;⁺</button>
      <button type="button" class="md-tb-btn" onclick="${M}.applyMarkdown(this,'hr')" title="Línea horizontal">—</button>
      <span class="md-tb-sep"></span>
      <span class="md-color-wrap">
        <button type="button" class="md-tb-btn md-color-btn" onclick="${M}.toggleColorPicker(this)" title="Color de texto">🎨</button>
        <div class="md-color-panel" style="display:none;">
          <div class="md-color-grid">
            <button type="button" class="md-color-swatch" data-color="#d32f2f" style="background:#d32f2f" onclick="${M}.pickColor(this)" title="Rojo"></button>
            <button type="button" class="md-color-swatch" data-color="#f57c00" style="background:#f57c00" onclick="${M}.pickColor(this)" title="Naranja"></button>
            <button type="button" class="md-color-swatch" data-color="#fbc02d" style="background:#fbc02d" onclick="${M}.pickColor(this)" title="Ámbar"></button>
            <button type="button" class="md-color-swatch" data-color="#388e3c" style="background:#388e3c" onclick="${M}.pickColor(this)" title="Verde"></button>
            <button type="button" class="md-color-swatch" data-color="#00897b" style="background:#00897b" onclick="${M}.pickColor(this)" title="Turquesa"></button>
            <button type="button" class="md-color-swatch" data-color="#039be5" style="background:#039be5" onclick="${M}.pickColor(this)" title="Celeste"></button>
            <button type="button" class="md-color-swatch" data-color="#1976d2" style="background:#1976d2" onclick="${M}.pickColor(this)" title="Azul"></button>
            <button type="button" class="md-color-swatch" data-color="#5e35b1" style="background:#5e35b1" onclick="${M}.pickColor(this)" title="Violeta"></button>
            <button type="button" class="md-color-swatch" data-color="#c2185b" style="background:#c2185b" onclick="${M}.pickColor(this)" title="Magenta"></button>
            <button type="button" class="md-color-swatch" data-color="#000000" style="background:#000000" onclick="${M}.pickColor(this)" title="Negro"></button>
            <input type="color" class="md-color-free" value="#1976d2" onchange="${M}.pickFreeColor(this)" title="Color personalizado" aria-label="Color personalizado">
          </div>
          <button type="button" class="md-color-clear" onclick="${M}.pickClearColor()" title="Quitar color">🚫 Sin color</button>
        </div>
      </span>
    </div>`;
  }

  // ───────────────────────────────────────────────────────────────
  // applyMarkdown(btn, type) — wrap/insert md syntax at selection.
  // Ported verbatim from v2 courses.js L482-531. type ∈
  // bold | italic | code | link | h1 | h2 | h3 | ul | ol |
  // blockquote | codeblock | table | hr.
  // ───────────────────────────────────────────────────────────────
  function applyMarkdown(btn, type) {
    const ta = btn.closest(".md-toolbar")
      ?.parentElement?.querySelector("textarea");
    if (!ta) return;
    const start = ta.selectionStart;
    const end = ta.selectionEnd;
    const sel = ta.value.substring(start, end);
    const text = ta.value;
    const nl = start > 0 && text[start - 1] !== "\n" ? "\n" : "";
    let before = "", after = "", placeholder = "";
    let insert, cursor;

    switch (type) {
      case "bold":       before = "**"; after = "**"; placeholder = "texto"; break;
      case "italic":     before = "*";  after = "*";  placeholder = "texto"; break;
      case "code":       before = "`";  after = "`";  placeholder = "código"; break;
      case "link":       before = "[";  after = "](url)"; placeholder = "texto"; break;
      case "h1":         before = nl + "# ";          placeholder = "título"; break;
      case "h2":         before = nl + "## ";         placeholder = "título"; break;
      case "h3":         before = nl + "### ";        placeholder = "título"; break;
      case "ul":         before = nl + "- ";          placeholder = "elemento"; break;
      case "ol":         before = nl + "1. ";         placeholder = "elemento"; break;
      case "blockquote": before = nl + "> ";          placeholder = "cita"; break;
      case "codeblock":  before = nl + "```\n"; after = "\n```"; placeholder = "código"; break;
      case "table":      before = nl + "| Header 1 | Header 2 |\n| --- | --- |\n| Cell 1 | Cell 2 |"; break;
      case "hr":         before = nl + "---"; break;
      default: return;
    }

    if (sel) {
      insert = before + sel + after;
      cursor = start + insert.length;
    } else if (placeholder) {
      insert = before + placeholder + after;
      cursor = start + before.length;
    } else {
      insert = before;
      cursor = start + insert.length;
    }

    ta.value = text.substring(0, start) + insert + text.substring(end);
    ta.focus();
    if (sel) {
      ta.selectionStart = ta.selectionEnd = cursor;
    } else if (placeholder) {
      ta.selectionStart = start + before.length;
      ta.selectionEnd = start + before.length + placeholder.length;
    } else {
      ta.selectionStart = ta.selectionEnd = cursor;
    }
    // Trigger a live-preview refresh — applies to the editor attached to
    // this form. No-op if the editor hasn't been wired yet (early call).
    ta.dispatchEvent(new Event("input"));
  }

  // ───────────────────────────────────────────────────────────────
  // toggleColorPicker(btn, keepPending) — ported v2 L537-553.
  // ───────────────────────────────────────────────────────────────
  function toggleColorPicker(btn, keepPending) {
    const ta = btn.closest(".md-toolbar")
      ?.parentElement?.querySelector("textarea");
    const wrap = btn.closest(".md-color-wrap");
    const panel = wrap && wrap.querySelector(".md-color-panel");
    if (!ta || !panel) return;
    const wasOpen = panel.style.display !== "none";
    const keep = (_pendingColor && _pendingColor.ta === ta
      && _pendingColor.fromPreview) || keepPending;
    if (!keep) {
      _pendingColor = {
        ta,
        start: ta._lastStart != null ? ta._lastStart : ta.selectionStart,
        end:   ta._lastEnd   != null ? ta._lastEnd   : ta.selectionEnd,
      };
    }
    // Close any other panel that may be open.
    document.querySelectorAll(".md-color-panel")
      .forEach((p) => { p.style.display = "none"; });
    panel.style.display = wasOpen ? "none" : "block";
    ta.focus();
  }

  // ───────────────────────────────────────────────────────────────
  // pickColor / pickFreeColor / pickClearColor — ported v2 L555-566.
  // ───────────────────────────────────────────────────────────────
  function pickColor(swatch) {
    _applyPendingColor(swatch.dataset.color);
  }
  function pickFreeColor(input) {
    _applyPendingColor(input.value);
    input.value = "#1976d2";
  }
  function pickClearColor() {
    _applyPendingColor("none");
  }

  // ── _applyPendingColor — ported v2 L568-595.
  // Wraps the pending selection with <span style="color:…">…</span>.
  function _applyPendingColor(color) {
    const ctx = _pendingColor;
    _pendingColor = null;
    document.querySelectorAll(".md-color-panel")
      .forEach((p) => { p.style.display = "none"; });
    if (!ctx || !ctx.ta) return;
    const ta = ctx.ta;
    const start = ctx.start;
    const end = ctx.end;
    if (color === "none") {
      _removeColorSpan(ta, start, end);
      return;
    }
    const sel = ta.value.substring(start, end);
    const open = `<span style="color:${color}">`;
    let insert, cursor;
    if (sel) {
      insert = open + sel + "</span>";
      cursor = end + open.length + 7;
      ta.value = ta.value.substring(0, start) + insert
        + ta.value.substring(end);
    } else {
      insert = open + "texto</span>";
      cursor = start + open.length;
      ta.value = ta.value.substring(0, start) + insert
        + ta.value.substring(end);
    }
    ta.focus();
    ta.selectionStart = ta.selectionEnd = cursor;
    ta.dispatchEvent(new Event("input"));
  }

  // ── _removeColorSpan — ported v2 L599-611.
  // If the selection exactly bounds a <span style="color:…">…</span>,
  // strip both tags and keep the inner text + cursor position.
  function _removeColorSpan(ta, start, end) {
    const before = ta.value.slice(0, start);
    const after = ta.value.slice(end);
    const m = before.match(/<span style="color:[^"]*">$/);
    if (m && after.startsWith("</span>")) {
      const removedOpen = m[0].length;
      const keep = ta.value.slice(start, end);
      ta.value = before.slice(0, before.length - removedOpen)
        + keep + after.slice("</span>".length);
      ta.focus();
      ta.selectionStart = ta.selectionEnd
        = before.length - removedOpen + keep.length;
      ta.dispatchEvent(new Event("input"));
    }
  }

  // ───────────────────────────────────────────────────────────────
  // editForm(block) → HTML string for the SPLIT editor form.
  // Returns the inner markup (no wrapper div). Caller injects it into
  // .sf-td-edit-form and calls attachLivePreview(form) on the wrapper.
  //
  // block: { id, type, title, content, url }
  // block.type → dispatch:
  //   markdown → title input + toolbar + split editor
  //   content  → title + plain textarea (no toolbar)
  //   pdf-ref / youtube / image → title + url input
  //   separator/exercise/interactive → title input only (placeholder)
  // ───────────────────────────────────────────────────────────────
  function editForm(block) {
    const type = block && block.type || "markdown";
    const titleVal = escHtml(block && block.title || "");
    let body = "";
    if (type === "markdown") {
      body = toolbar()
        + `<div class="md-edit-split">`
        +   `<textarea class="md-editor" rows="14" spellcheck="false" placeholder="Escribe markdown…">${
            escHtml(block && block.content || "")
          }</textarea>`
        +   `<div class="md-preview md-view"></div>`
        + `</div>`;
    } else if (type === "content") {
      body = `<textarea class="sf-td-md-plain" rows="6" spellcheck="false" placeholder="Texto…">${
        escHtml(block && block.content || "")
      }</textarea>`;
    } else if (type === "pdf-ref" || type === "youtube"
               || type === "image") {
      const ph = type === "pdf-ref" ? "Ruta o URL del PDF"
        : type === "youtube" ? "URL de YouTube"
        : "URL de la imagen";
      body = `<input class="sf-td-md-url" type="text" value="${
        escHtml(block && block.url || "")
      }" placeholder="${escHtml(ph)}" />`;
    } else {
      // separator / exercise / interactive / unknown
      body = `<div class="sf-empty">Edita el título. Contenido completo en siguientes fases.</div>`;
    }
    return `<div class="sf-td-md-edit">
      <input class="sf-td-md-title" type="text" value="${titleVal}" placeholder="Título" />
      ${body}
      <div class="sf-td-md-actions">
        <button type="button" class="sf-td-save ht-btn">💾 Guardar</button>
        <button type="button" class="sf-td-cancel ht-btn ht-btn-ghost">Cancelar</button>
      </div>
    </div>`;
  }

  // ───────────────────────────────────────────────────────────────
  // attachLivePreview(formEl) — wires the input → preview sync, the
  // trackSelection listeners (so 🎨 wraps the last selection even after
  // focus leaves the textarea), and the editor↔preview scroll sync.
  //
  // Idempotent: safe to call twice on the same node. Marks the form with
  // data-sf-md-live="1" so a second call is a no-op.
  // ───────────────────────────────────────────────────────────────
  function attachLivePreview(formEl) {
    if (!formEl || formEl.dataset.sfMdLive === "1") return;
    const editor = formEl.querySelector(".md-editor");
    const preview = formEl.querySelector(".md-preview");
    if (!editor || !preview) return;
    formEl.dataset.sfMdLive = "1";

    const renderMd = () => {
      const value = editor.value || "";
      if (!value.trim()) {
        preview.innerHTML =
          '<div class="md-preview-empty">👁️ La vista previa aparecerá aquí…</div>';
        return;
      }
      const cb = window.App.ContentBlocks;
      if (cb && typeof cb._renderMd === "function") {
        preview.innerHTML = cb._renderMd(value);
      } else {
        preview.textContent = value;
      }
    };

    // Initial render
    renderMd();

    // Input → live preview
    editor.addEventListener("input", renderMd);

    // Selection tracking so the 🎨 button still wraps the right span
    // after the toolbar steals focus.
    const trackSel = () => {
      editor._lastStart = editor.selectionStart;
      editor._lastEnd   = editor.selectionEnd;
    };
    editor.addEventListener("mouseup", trackSel);
    editor.addEventListener("keyup", trackSel);
    editor.addEventListener("click", trackSel);

    // Editor ↔ preview scroll sync (ratio-based, anti-loop).
    editor._mdSyncing = false;
    preview._mdSyncing = false;
    editor.addEventListener("scroll", () => {
      if (preview._mdSyncing) return;
      const maxTA = editor.scrollHeight - editor.clientHeight;
      const maxPV = preview.scrollHeight - preview.clientHeight;
      if (maxPV > 0) {
        preview._mdSyncing = true;
        preview.scrollTop = maxTA > 0
          ? (editor.scrollTop / maxTA) * maxPV : 0;
        // release on next frame
        requestAnimationFrame(() => { preview._mdSyncing = false; });
      }
    });
    preview.addEventListener("scroll", () => {
      if (editor._mdSyncing) return;
      const maxTA = editor.scrollHeight - editor.clientHeight;
      const maxPV = preview.scrollHeight - preview.clientHeight;
      if (maxTA > 0) {
        editor._mdSyncing = true;
        editor.scrollTop = maxPV > 0
          ? (preview.scrollTop / maxPV) * maxTA : 0;
        requestAnimationFrame(() => { editor._mdSyncing = false; });
      }
    });
  }

  // ───────────────────────────────────────────────────────────────
  // resetPending() — call when an edit form is discarded so the next
  // open doesn't inherit stale selection state.
  // ───────────────────────────────────────────────────────────────
  function resetPending() {
    _pendingColor = null;
    document.querySelectorAll(".md-color-panel")
      .forEach((p) => { p.style.display = "none"; });
  }

  // ── Public API ───────────────────────────────────────────────
  return {
    toolbar,
    editForm,
    applyMarkdown,
    toggleColorPicker,
    pickColor,
    pickFreeColor,
    pickClearColor,
    attachLivePreview,
    resetPending,
  };
})();

console.log("[Studyflow] markdown-editor.js loaded");
