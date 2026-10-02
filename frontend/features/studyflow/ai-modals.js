// ─── Phase 7.7 — Test / Audio / Infographic config modals (v3 port) ──
// Namespace: window.App.AiModals
// Single-instance overlays following the v2 ai.js patterns:
//   • Test config  — number of questions (10/20/30)
//   • Audio config — duration (complete/5/10/20) + language (es/en)
//   • Infographic  — style grid + language (auto/es/en)
// Closed by overlay click / ✕ / Cancel. No multi-markdown selector
// (v3 tasks run on the source block the toolbar belongs to).

window.App = window.App || {};
window.App.AiModals = (function () {
  "use strict";

  function escHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  // Removes any open AiModals overlay (idempotent close).
  function close() {
    document.querySelectorAll(".aimodal-overlay").forEach((el) => el.remove());
  }

  function _shell(title, bodyHtml, submitLabel) {
    return `
      <div class="inf-config-overlay aimodal-overlay">
        <div class="inf-config-modal aimodal-modal">
          <div class="inf-config-header aimodal-header">
            <span class="inf-config-title aimodal-title">${escHtml(title)}</span>
            <button class="inf-config-close aimodal-close" data-aimodal-close title="Cerrar">✕</button>
          </div>
          <div class="inf-config-body aimodal-body">${bodyHtml}</div>
          <div class="inf-config-footer aimodal-footer">
            <button class="ai-btn" data-aimodal-cancel>Cancelar</button>
            <button class="ai-btn aimodal-submit" data-aimodal-submit style="background:linear-gradient(135deg,#1a73e8,#0d47a1);color:#fff;font-weight:600;">${escHtml(submitLabel)}</button>
          </div>
        </div>
      </div>`;
  }

  function _wireShell(overlay, onClose) {
    const closeIt = () => { overlay.remove(); if (onClose) onClose(); };
    overlay.querySelector("[data-aimodal-close]").addEventListener("click", closeIt);
    overlay.querySelector("[data-aimodal-cancel]").addEventListener("click", closeIt);
    overlay.addEventListener("click", (e) => {
      if (e.target === e.currentTarget) closeIt();
    });
    return closeIt;
  }

  // ── Language dropdown wiring (shared by audio + infographic) ──
  function _wireLang(langBtnId, ddId, onPick) {
    const btn = document.getElementById(langBtnId);
    const dd = document.getElementById(ddId);
    if (!btn || !dd) return;
    const opts = dd.querySelectorAll(".inf-lang-option");
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      dd.classList.toggle("open");
    });
    opts.forEach((opt) => {
      opt.addEventListener("click", (e) => {
        e.stopPropagation();
        opts.forEach((o) => o.classList.remove("selected"));
        opt.classList.add("selected");
        btn.innerHTML = `${opt.textContent.trim().split(" ")[0]} <span class="lang-arrow">▾</span>`;
        dd.classList.remove("open");
        if (onPick) onPick(opt.dataset.lang);
      });
    });
    document.addEventListener("click", () => dd.classList.remove("open"));
  }

  function _selectedLang(ddId) {
    const sel = document.querySelector(`#${ddId} .inf-lang-option.selected`);
    return sel ? sel.dataset.lang : "auto";
  }

  // ─── Test config ──────────────────────────────────────────────
  function openTestConfig(config) {
    const { onSubmit, title = "🤖 Generar Test", submitLabel = "✨ Generar Test" } = config || {};
    if (typeof onSubmit !== "function") throw new Error("[AiModals] openTestConfig requires onSubmit");
    close();

    const qtyHtml = [10, 20, 30].map((q) =>
      `<button type="button" class="test-qty-btn ${q === 10 ? "selected" : ""}" data-qty="${q}"
        style="${q === 10 ? "border-color:#1a73e8;background:rgba(26,115,232,.18);color:#fff;" : "border-color:#444;background:#1e1e1e;color:#ccc;"}">
        ${q} preguntas
      </button>`
    ).join("");

    const html = _shell(title, `
      <div class="inf-config-section aimodal-section">
        <div class="inf-config-section-title">❓ Número de preguntas</div>
        <div class="test-qty-grid">${qtyHtml}</div>
        <div style="color:#aaa;font-size:12px;margin-top:10px;">Se generará a partir del bloque en el que pulsaste el botón.</div>
      </div>`, submitLabel);

    document.body.insertAdjacentHTML("beforeend", html);
    const overlay = document.querySelector(".aimodal-overlay");
    const closeIt = _wireShell(overlay);

    const qtyBtns = overlay.querySelectorAll(".test-qty-btn");
    qtyBtns.forEach((btn) => {
      btn.addEventListener("click", () => {
        qtyBtns.forEach((b) => {
          b.classList.remove("selected");
          b.style.borderColor = "#444";
          b.style.background = "#1e1e1e";
          b.style.color = "#ccc";
        });
        btn.classList.add("selected");
        btn.style.borderColor = "#1a73e8";
        btn.style.background = "rgba(26,115,232,.18)";
        btn.style.color = "#fff";
      });
    });

    overlay.querySelector("[data-aimodal-submit]").addEventListener("click", () => {
      const numQuestions = parseInt(overlay.querySelector(".test-qty-btn.selected")?.dataset?.qty || "10", 10);
      closeIt();
      onSubmit(numQuestions);
    });
  }

  // ─── Audio config ─────────────────────────────────────────────
  const AUDIO_DURATIONS = [
    { id: "complete", emoji: "📖", label: "Completo", desc: "Lee todo el contenido tal cual, sin IA" },
    { id: "5",  emoji: "⚡", label: "5 min",  desc: "Resumen rápido · ~750 palabras" },
    { id: "10", emoji: "🎧", label: "10 min", desc: "Resumen medio · ~1500 palabras" },
    { id: "20", emoji: "🎙️", label: "20 min", desc: "Resumen extenso · ~3000 palabras" },
  ];

  function openAudioConfig(config) {
    const { onSubmit, title = "🎵 Configurar Audio", submitLabel = "🎵 Generar Audio", language = "es" } = config || {};
    if (typeof onSubmit !== "function") throw new Error("[AiModals] openAudioConfig requires onSubmit");
    close();

    const durOpts = AUDIO_DURATIONS.map((d) =>
      `<div class="inf-config-style-opt ${d.id === "complete" ? "selected" : ""}" data-duration="${d.id}">
        <div class="inf-config-style-label">${d.emoji} ${d.label}</div>
        <div class="inf-config-style-desc">${d.desc}</div>
      </div>`
    ).join("");

    // NOTE: duration 5/10/20 requires the opencode-acp provider (OpenZEN);
    // "complete" is verbatim (no AI rewrite). Backend validates this.
    const html = _shell(title, `
      <div class="inf-config-section aimodal-section theme-warning" id="audio-zen-note" style="display:none;">
        <div style="font-size:12px;color:#ffa502;">⚠️ Las duraciones 5/10/20 min requieren el provider OpenZEN (opencode-acp). Si no está activo, usa «Completo».</div>
      </div>
      <div class="inf-config-section aimodal-section">
        <div class="inf-config-section-title">⏱️ Duración del audio</div>
        <div class="inf-config-style-grid" id="audio-duration-grid">${durOpts}</div>
      </div>
      <div class="inf-config-section aimodal-section">
        <div class="inf-config-section-title">🌐 Language / Idioma</div>
        <div class="inf-lang-group">
          <button class="inf-lang-btn" id="audio-lang-toggle" title="Cambiar idioma del audio">${language === "en" ? "🇬🇧" : "🇪🇸"} <span class="lang-arrow">▾</span></button>
          <div class="inf-lang-dropdown" id="audio-lang-dropdown">
            <button class="inf-lang-option ${language === "es" ? "selected" : ""}" data-lang="es">🇪🇸 Español <span class="check">✓</span></button>
            <button class="inf-lang-option ${language === "en" ? "selected" : ""}" data-lang="en">🇬🇧 English <span class="check">✓</span></button>
          </div>
        </div>
      </div>`, submitLabel);

    document.body.insertAdjacentHTML("beforeend", html);
    const overlay = document.querySelector(".aimodal-overlay");
    const closeIt = _wireShell(overlay);

    _wireLang("audio-lang-toggle", "audio-lang-dropdown");

    const durEls = overlay.querySelectorAll("#audio-duration-grid .inf-config-style-opt");
    durEls.forEach((el) => {
      el.addEventListener("click", () => {
        durEls.forEach((o) => o.classList.remove("selected"));
        el.classList.add("selected");
        const isZen = el.dataset.duration !== "complete";
        document.getElementById("audio-zen-note").style.display = isZen ? "block" : "none";
      });
    });

    overlay.querySelector("[data-aimodal-submit]").addEventListener("click", () => {
      const dur = overlay.querySelector("#audio-duration-grid .inf-config-style-opt.selected")?.dataset?.duration || "complete";
      const lang = _selectedLang("audio-lang-dropdown");
      closeIt();
      onSubmit({ language: lang, duration: dur });
    });
  }

  // ─── Infographic config ───────────────────────────────────────
  const INFO_STYLES = [
    { id: "auto",         emoji: "🎨", label: "Auto",        desc: "El modelo elige el mejor estilo" },
    { id: "sketch-note",  emoji: "✏️", label: "Sketch note", desc: "Apuntes dibujados a mano" },
    { id: "professional", emoji: "💼", label: "Profesional", desc: "Corporativo, sobrio y limpio" },
    { id: "bento-grid",   emoji: "🧱", label: "Bento grid",  desc: "Celdas modulares tipo dashboards" },
    { id: "editorial",    emoji: "🗞️", label: "Editorial",   desc: "Estilo revista / prensa" },
    { id: "instructional", emoji: "📋", label: "Instruccional", desc: "Pasos y guías didácticas" },
    { id: "bricks",       emoji: "🧱", label: "Bricks",      desc: "Bloques apilados de colores" },
    { id: "clay",         emoji: "🧸", label: "Clay",        desc: "3D suave estilo plastilina" },
    { id: "anime",        emoji: "🌸", label: "Anime",       desc: "Estilo manga japonés" },
    { id: "kawaii",       emoji: "🐻", label: "Kawaii",      desc: "Tierno y colorido" },
    { id: "scientific",   emoji: "🔬", label: "Scientific",  desc: "Formal, datos y gráficos" },
  ];

  // Same ids the backend speaks (md_templates/tasks_pdf validate
  // 'concise' | 'standard' | 'detailed'), so the value travels unchanged from
  // this modal to the NotebookLM prompt.
  const INFO_DEPTHS = [
    { id: "concise",  emoji: "⚡", label: "Resumido",  desc: "Lo esencial, sin rodeos" },
    { id: "standard", emoji: "📊", label: "Estándar",  desc: "Equilibrio de detalle" },
    { id: "detailed", emoji: "🔬", label: "Detallado", desc: "Máximo desarrollo" },
  ];

  function openInfographicConfig(config) {
    const { onSubmit, title = "📊 Configurar Infografía", submitLabel = "📊 Generar Infografía" } = config || {};
    if (typeof onSubmit !== "function") throw new Error("[AiModals] openInfographicConfig requires onSubmit");
    close();

    const styleOpts = INFO_STYLES.map((s) =>
      `<div class="inf-config-style-opt" data-style="${s.id}" title="${escHtml(s.desc)}">
        <div class="inf-config-style-label">${s.emoji} ${s.label}</div>
        <div class="inf-config-style-desc">${escHtml(s.desc)}</div>
      </div>`
    ).join("");

    // The backend has always taken a detail level (generateInfographic sends
    // detail_level, defaulting to "standard") — the modal just never offered
    // it, so the choice NotebookLM itself asks for was unreachable. Same three
    // values the rest of the app speaks: concise | standard | detailed.
    const depthOpts = INFO_DEPTHS.map((d) =>
      `<div class="inf-config-style-opt ${d.id === "standard" ? "selected" : ""}" data-depth="${d.id}">
        <div class="inf-config-style-label">${d.emoji} ${d.label}</div>
        <div class="inf-config-style-desc">${escHtml(d.desc)}</div>
      </div>`
    ).join("");

    const html = _shell(title, `
      <div class="inf-config-section aimodal-section">
        <div class="inf-config-section-title">🎨 Estilo visual</div>
        <div class="inf-config-style-grid" id="inf-style-grid">${styleOpts}</div>
      </div>
      <div class="inf-config-section aimodal-section">
        <div class="inf-config-section-title">📏 Nivel de detalle</div>
        <div class="inf-config-style-grid" id="inf-depth-grid">${depthOpts}</div>
      </div>
      <div class="inf-config-section aimodal-section">
        <div class="inf-config-section-title">🌐 Idioma</div>
        <div class="inf-lang-group">
          <button class="inf-lang-btn" id="inf-lang-toggle" title="Cambiar idioma de la infografía">🌐 <span class="lang-arrow">▾</span></button>
          <div class="inf-lang-dropdown" id="inf-lang-dropdown">
            <button class="inf-lang-option selected" data-lang="auto">🌐 Auto-detect <span class="check">✓</span></button>
            <button class="inf-lang-option" data-lang="es">🇪🇸 Español <span class="check">✓</span></button>
            <button class="inf-lang-option" data-lang="en">🇬🇧 English <span class="check">✓</span></button>
          </div>
        </div>
      </div>`, submitLabel);

    document.body.insertAdjacentHTML("beforeend", html);
    const overlay = document.querySelector(".aimodal-overlay");
    const closeIt = _wireShell(overlay);

    _wireLang("inf-lang-toggle", "inf-lang-dropdown");

    function _wireExclusive(sel) {
      const els = overlay.querySelectorAll(`${sel} .inf-config-style-opt`);
      els.forEach((el) => {
        el.addEventListener("click", () => {
          els.forEach((o) => o.classList.remove("selected"));
          el.classList.add("selected");
        });
      });
      return els;
    }

    const styleEls = _wireExclusive("#inf-style-grid");
    _wireExclusive("#inf-depth-grid");
    // Pre-select "auto" style card
    const autoEl = overlay.querySelector('#inf-style-grid .inf-config-style-opt[data-style="auto"]');
    if (autoEl) autoEl.classList.add("selected");

    overlay.querySelector("[data-aimodal-submit]").addEventListener("click", () => {
      const style = overlay.querySelector("#inf-style-grid .inf-config-style-opt.selected")?.dataset?.style || "auto";
      const detailLevel = overlay.querySelector("#inf-depth-grid .inf-config-style-opt.selected")?.dataset?.depth || "standard";
      const lang = _selectedLang("inf-lang-dropdown");
      closeIt();
      onSubmit({ style, language: lang, detail_level: detailLevel });
    });
  }

  return { openTestConfig, openAudioConfig, openInfographicConfig, close };
}());