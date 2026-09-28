// ─── Phase 7.7 — Markdown Config Modal (v3 port of md-config-modal.js) ─
// Namespace: window.App.AiConfigModal
// One responsibility: present a single-instance modal that lets the user
// pick a prompt template (live preview), output length and language, and
// deliver the chosen {template_id, language, length} via onSubmit().
//
// Port of the v2 Phase-62 modal (template grid + preview, radio length,
// dropdown language, close on overlay/×/cancel) minus the Phase-72
// multi-markdown block selector (v3 inserts a single block per task).
//
// Public API:
//   window.App.AiConfigModal.open({
//     title:        string,            // modal title
//     submitLabel:  string,            // generate button label
//     errorStatus?: (msg) => void,     // optional error reporter
//     onSubmit:     (params) => void,  // receives {template_id, language, length}
//   })
//   window.App.AiConfigModal.close()   // closes any open instance

window.App = window.App || {};
window.App.AiConfigModal = (function () {
  "use strict";

  const OVERLAY_ID = "aiconf-overlay";
  const LENGTHS = [
    { id: "concise",  emoji: "⚡", label: "Concisa",  desc: "Idea esencial, pocos callouts" },
    { id: "standard", emoji: "🔤", label: "Estándar", desc: "Equilibrado · recomendado" },
    { id: "detailed", emoji: "📚", label: "Extensa",  desc: "Máximo detalle y ejemplos" },
  ];
  const LANGS = [
    { id: "auto", label: "🌐 Auto",      desc: "Mismo idioma que el contenido" },
    { id: "es",   label: "🇪🇸 Español", desc: "Forzar salida en español" },
    { id: "en",   label: "🇬🇧 English", desc: "Forzar salida en inglés" },
  ];

  function escHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function buildLengthRadios() {
    return LENGTHS.map((d) =>
      `<label class="inf-config-radio aiconf-radio">
        <input type="radio" name="aiconf-length" value="${d.id}" ${d.id === "standard" ? "checked" : ""}>
        <span class="inf-config-radio-label aiconf-radio-label">${d.emoji} ${d.label}</span>
        <span class="inf-config-radio-desc aiconf-radio-desc">${d.desc}</span>
      </label>`
    ).join("");
  }

  function buildLangDropdown() {
    return LANGS.map((l) =>
      `<button class="inf-lang-option ${l.id === "auto" ? "selected" : ""}" data-lang="${l.id}">${l.label} <span class="check">✓</span></button>`
    ).join("");
  }

  function buildOverlayHtml(title, submitLabel) {
    return `
      <div class="inf-config-overlay aiconf-overlay" id="${OVERLAY_ID}">
        <div class="inf-config-modal ozmd-modal aiconf-modal">
          <div class="inf-config-header aiconf-header">
            <span class="inf-config-title aiconf-title">${escHtml(title)}</span>
            <button class="inf-config-close aiconf-close" id="aiconf-close" title="Cerrar">✕</button>
          </div>
          <div class="inf-config-body aiconf-body">
            <div class="inf-config-section aiconf-section">
              <div class="inf-config-section-title aiconf-section-title">🎛️ Plantilla de prompt</div>
              <div class="ozmd-grid">
                <div class="ozmd-templates" id="aiconf-templates">
                  <div style="color:#aaa;padding:6px 2px;">Cargando plantillas…</div>
                </div>
                <div class="ozmd-preview">
                  <div class="ozmd-preview-title">👁️ Vista previa</div>
                  <div class="ozmd-preview-body" id="aiconf-preview-body">
                    <div style="color:#aaa;padding:6px 2px;">Selecciona una plantilla…</div>
                  </div>
                </div>
              </div>
            </div>
            <div class="inf-config-row aiconf-row">
              <div class="inf-config-section aiconf-section" style="flex:1;">
                <div class="inf-config-section-title aiconf-section-title">📐 Extensión</div>
                <div class="inf-config-radios aiconf-radios">${buildLengthRadios()}</div>
              </div>
              <div class="inf-config-section aiconf-section" style="flex:1;">
                <div class="inf-config-section-title aiconf-section-title">🌐 Idioma</div>
                <div class="inf-lang-group">
                  <button class="inf-lang-btn" id="aiconf-lang-toggle" title="Cambiar idioma de salida">🌐 <span class="lang-arrow">▾</span></button>
                  <div class="inf-lang-dropdown" id="aiconf-lang-dropdown">
                    ${buildLangDropdown()}
                  </div>
                </div>
              </div>
            </div>
          </div>
          <div class="inf-config-footer aiconf-footer">
            <button class="ai-btn" id="aiconf-cancel">Cancelar</button>
            <button class="ai-btn aiconf-submit" id="aiconf-submit" style="background:linear-gradient(135deg,#1a73e8,#0d47a1);color:#fff;font-weight:600;">${escHtml(submitLabel)}</button>
          </div>
        </div>
      </div>`;
  }

  function renderPreview(previewBody, tpl) {
    const cb = window.App.ContentBlocks;
    const mock = tpl && tpl.mock ? tpl.mock : "";
    previewBody.innerHTML = cb && cb._renderMd
      ? cb._renderMd(mock)
      : `<pre>${escHtml(mock)}</pre>`;
  }

  function mountTemplates(templates, templatesEl, previewBody) {
    if (!templates.length) {
      templatesEl.innerHTML = `<div style="color:#e57373;padding:6px 2px;">No hay plantillas disponibles.</div>`;
      return;
    }
    templatesEl.innerHTML = templates.map((t) =>
      `<div class="inf-config-style-opt ozmd-tpl-opt aiconf-tpl-opt" data-template-id="${escHtml(t.id)}" title="${escHtml(t.description)}">
        <div class="inf-config-style-label">${escHtml(t.emoji)} ${escHtml(t.name)}</div>
        <div class="inf-config-style-desc">${escHtml(t.description)}</div>
      </div>`
    ).join("");

    const cardEls = templatesEl.querySelectorAll(".ozmd-tpl-opt");
    cardEls.forEach((el) => {
      el.addEventListener("click", () => {
        cardEls.forEach((o) => o.classList.remove("selected"));
        el.classList.add("selected");
        const tpl = templates.find((t) => t.id === el.dataset.templateId);
        if (tpl) renderPreview(previewBody, tpl);
      });
    });
    cardEls[0].classList.add("selected");
    renderPreview(previewBody, templates[0]);
  }

  function wireCloseHandlers(overlay, close) {
    document.getElementById("aiconf-close").addEventListener("click", close);
    document.getElementById("aiconf-cancel").addEventListener("click", close);
    overlay.addEventListener("click", (e) => {
      if (e.target === e.currentTarget) close();
    });
  }

  function wireLangDropdown() {
    const langBtn = document.getElementById("aiconf-lang-toggle");
    const langDd = document.getElementById("aiconf-lang-dropdown");
    if (!langBtn || !langDd) return;
    const langOpts = langDd.querySelectorAll(".inf-lang-option");
    langBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      langDd.classList.toggle("open");
    });
    langOpts.forEach((opt) => {
      opt.addEventListener("click", (e) => {
        e.stopPropagation();
        langOpts.forEach((o) => o.classList.remove("selected"));
        opt.classList.add("selected");
        langBtn.innerHTML = `${opt.textContent.trim().split(" ")[0]} <span class="lang-arrow">▾</span>`;
        langDd.classList.remove("open");
      });
    });
    document.addEventListener("click", () => langDd.classList.remove("open"));
  }

  function wireSubmit(onSubmit, close, errorStatus) {
    document.getElementById("aiconf-submit").addEventListener("click", () => {
      const selTpl = document.querySelector(".ozmd-tpl-opt.selected");
      const lengthInp = document.querySelector('input[name="aiconf-length"]:checked');
      const selLang = document.querySelector("#aiconf-lang-dropdown .inf-lang-option.selected");
      const params = {
        template_id: selTpl ? selTpl.dataset.templateId : null,
        language: selLang ? selLang.dataset.lang : "auto",
        length: lengthInp ? lengthInp.value : "standard",
      };
      close();
      onSubmit(params);
      if (typeof errorStatus === "function") errorStatus("");
    });
  }

  function close() {
    const overlay = document.getElementById(OVERLAY_ID);
    if (overlay) overlay.remove();
  }

  async function open(config) {
    if (!config || typeof config.onSubmit !== "function") {
      throw new Error("[AiConfigModal] open() requires { onSubmit }");
    }
    const title = config.title || "Configurar Markdown";
    const submitLabel = config.submitLabel || "Generar";
    const errorStatus = typeof config.errorStatus === "function"
      ? config.errorStatus
      : () => {};

    // Single-instance guard
    close();

    document.body.insertAdjacentHTML(
      "beforeend",
      buildOverlayHtml(title, submitLabel)
    );
    const overlay = document.getElementById(OVERLAY_ID);

    wireCloseHandlers(overlay, close);
    wireLangDropdown();

    const templatesEl = document.getElementById("aiconf-templates");
    const previewBody = document.getElementById("aiconf-preview-body");

    try {
      const resp = await window.API.get("/ai/md-templates");
      mountTemplates(resp.templates || [], templatesEl, previewBody);
    } catch (err) {
      templatesEl.innerHTML = `<div style="color:#e57373;padding:6px 2px;">❌ No se pudieron cargar las plantillas: ${escHtml(err.message || err)}</div>`;
      errorStatus("❌ Error cargando plantillas");
    }

    wireSubmit(config.onSubmit, close, errorStatus);
  }

  return { open, close };
}());