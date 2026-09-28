/* ============================== PDF VIEWER (S7) ==============================
 * Min-viable port of the v2 pdf-viewer.js:
 *   - fetch + parse PDF with pdf.js (vendored under /vendor/pdfjs/)
 *   - render current page into a <canvas> at fit-width scale
 *   - prev/next + zoom + page counter + open-external link
 *   - graceful loading / error states
 *
 * Annotations (text only) live in pdf-viewer-annots.js and are wired
 * after the first page renders; this module only owns mount/render.
 *
 * Public: App.PdfViewer.init(container)
 * ========================================================================== */
(function () {
  "use strict";

  const Auth = window.App && window.App.Auth ? window.App.Auth : null;
  const Annots = window.App && window.App.PdfAnnots
    ? window.App.PdfAnnots : null;

  // ─── per-container state (WeakMap so containers can be GC'd) ─────
  const _states = new WeakMap();

  function _stateOf(container) {
    if (!_states.has(container)) {
      _states.set(container, {
        pdfDoc: null,
        totalPages: 0,
        currentPage: 1,
        // fit-width scale factor; 1.0 means scale=cssWidth/pageWidth.
        fitZoom: 1.0,
        // user-chosen zoom multiplier (1.0 = fit-width, 1.25 = 125%, etc.)
        zoom: 1.0,
        pdfBytes: null,
        pdfUrl: null,
        busy: false,           // render-in-flight guard
        blockId: null,         // optional backend lookup for annotations
      });
    }
    return _states.get(container);
  }

  // ─── Resolve a block.url to a fetchable /api/pdf/<id> URL ───────
  // v3 stores uploads in the pdfs table; blocks reference /api/pdf/<id>.
  // We must hit the API origin (http://localhost:8082 in dev) — not the
  // static server on :3000 — so any relative /api/pdf/... is rewritten
  // against window.API_URL. Absolute URLs (http/https) are kept as-is.
  function _pdfUrl(url) {
    if (!url) return null;
    const trimmed = String(url).trim();
    if (!trimmed) return null;
    if (/^https?:\/\//i.test(trimmed)) return trimmed;
    if (trimmed.startsWith("/api/")) {
      // window.API_URL is ".../api" in dev, "/api" in prod; we strip the
      // leading /api from the input to avoid ".../api/api/pdf/<id>".
      const base = (typeof window.API_URL === "string" && window.API_URL)
        ? window.API_URL
        : "/api";
      return base.replace(/\/$/, "") + trimmed.replace(/^\/api/, "");
    }
    return null;
  }

  // ─── PDF.js loader (single-shot) ────────────────────────────────
  // Vendored UMD build exposes window.pdfjsLib; we lazily resolve so
  // pages without pdf-ref blocks never block on the worker fetch.
  let _pdfjsLoading = null;
  function _loadPdfJs() {
    if (window.pdfjsLib) return Promise.resolve(window.pdfjsLib);
    if (_pdfjsLoading) return _pdfjsLoading;
    _pdfjsLoading = new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = "/vendor/pdfjs/pdf.min.js";
      s.onload = () => {
        if (!window.pdfjsLib) {
          reject(new Error("pdfjsLib missing after script load"));
          return;
        }
        // Worker — same vendored bundle (offline). pdf.js reaches it via
        // Worker.postMessage on the URL string.
        window.pdfjsLib.GlobalWorkerOptions.workerSrc =
          "/vendor/pdfjs/pdf.worker.min.js";
        resolve(window.pdfjsLib);
      };
      s.onerror = () => reject(new Error("Failed to load /vendor/pdfjs/pdf.min.js"));
      document.head.appendChild(s);
    });
    return _pdfjsLoading;
  }

  // ─── Auth headers helper ───────────────────────────────────────
  // Reads window.App.Auth LAZILY (every call) because agenda-glue.js
  // populates App.Auth *after* pdf-viewer.js loads in index.html.
  function _authHeaders() {
    const h = { "Accept": "application/pdf" };
    const A = (window.App && window.App.Auth) || Auth;
    if (A && typeof A.authHeaders === "function") {
      Object.assign(h, A.authHeaders());
    } else if (A && typeof A.getHeaders === "function") {
      Object.assign(h, A.getHeaders());
    } else if (window.Auth && typeof window.Auth.getHeaders === "function") {
      Object.assign(h, window.Auth.getHeaders());
    }
    return h;
  }

  // ─── Build viewer markup ───────────────────────────────────────
  function _injectTemplate(container, pdfUrl) {
    container.innerHTML = `
      <div class="pdf-toolbar">
        <div class="pdf-tb-left">
          <button class="pdf-page-prev" title="Página anterior">◀</button>
          <span class="pdf-page-info">Pág. <span class="pdf-current-page">1</span> / <span class="pdf-total-pages">--</span></span>
          <button class="pdf-page-next" title="Página siguiente">▶</button>
          <span class="pdf-sep"></span>
          <a class="pdf-open-external" href="${pdfUrl}" target="_blank" rel="noopener" title="Abrir PDF en nueva pestaña">↗ Abrir</a>
        </div>
        <div class="pdf-tb-center">
          <select class="pdf-zoom-select" title="Zoom">
            <option value="fit" selected>Fit</option>
            <option value="0.5">50%</option>
            <option value="0.75">75%</option>
            <option value="1">100%</option>
            <option value="1.25">125%</option>
            <option value="1.5">150%</option>
            <option value="2">200%</option>
          </select>
        </div>
        <div class="pdf-tb-right">
          <button class="pdf-text-toggle" title="Modo texto (anotaciones)">T</button>
          <button class="pdf-undo-btn" title="Deshacer última anotación de texto" disabled>↩️</button>
          <span class="pdf-sep"></span>
          <button class="pdf-save-btn" title="Guardar anotaciones">💾</button>
          <span class="pdf-save-status"></span>
        </div>
      </div>
      <div class="pdf-canvas-wrap">
        <canvas class="pdf-render-canvas"></canvas>
        <div class="pdf-text-overlay"></div>
        <div class="pdf-loading">Cargando PDF…</div>
      </div>
      <details class="pdf-annots-panel">
        <summary>📝 Anotaciones</summary>
        <ol class="pdf-annots-list"></ol>
      </details>
    `;
  }

  // ─── Update toolbar / counter / zoom UI ────────────────────────
  function _refreshToolbar(container) {
    const s = _stateOf(container);
    container.querySelector(".pdf-current-page").textContent = s.currentPage;
    container.querySelector(".pdf-total-pages").textContent = s.totalPages || "--";
    const prev = container.querySelector(".pdf-page-prev");
    const next = container.querySelector(".pdf-page-next");
    if (prev) prev.disabled = s.currentPage <= 1;
    if (next) next.disabled = s.currentPage >= s.totalPages;
    const zsel = container.querySelector(".pdf-zoom-select");
    if (zsel && document.activeElement !== zsel) {
      zsel.value = s.zoom === 1.0 && s.fitZoom === 1.0
        ? "fit" : String(s.zoom);
    }
  }

  // ─── Render current page to canvas at fit-width * zoom ──────────
  async function _renderPage(container) {
    const s = _stateOf(container);
    if (!s.pdfDoc) return;
    if (s.busy) return;
    s.busy = true;
    const canvas = container.querySelector(".pdf-render-canvas");
    const overlay = container.querySelector(".pdf-text-overlay");
    const loading = container.querySelector(".pdf-loading");
    if (!canvas) { s.busy = false; return; }

    const wrap = container.querySelector(".pdf-canvas-wrap");
    const wrapWidth = (wrap.clientWidth || 600) - 16; // padding buffer
    if (wrapWidth <= 0) {
      // Hidden container → try again on resize.
      s.busy = false;
      return;
    }

    const page = await s.pdfDoc.getPage(s.currentPage);
    const unscaledViewport = page.getViewport({ scale: 1 });
    // fit-width scale = wrapWidth / pageWidth (CSS px per PDF pt).
    s.fitZoom = wrapWidth / unscaledViewport.width;
    const effectiveScale = s.fitZoom * s.zoom;
    const viewport = page.getViewport({ scale: effectiveScale });

    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.floor(viewport.width * dpr);
    canvas.height = Math.floor(viewport.height * dpr);
    canvas.style.width = viewport.width + "px";
    canvas.style.height = viewport.height + "px";

    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    try {
      await page.render({ canvasContext: ctx, viewport }).promise;
    } catch (err) {
      console.error("[PDF render]", err);
      if (loading) {
        loading.textContent = "❌ Error al renderizar página: " + err.message;
        loading.classList.add("is-error");
        loading.style.display = "block";
      }
      s.busy = false;
      return;
    }

    // Position overlay exactly over the canvas.
    if (overlay) {
      const cRect = canvas.getBoundingClientRect();
      const wRect = wrap.getBoundingClientRect();
      overlay.style.left = (cRect.left - wRect.left) + "px";
      overlay.style.top = (cRect.top - wRect.top) + "px";
      overlay.style.width = canvas.offsetWidth + "px";
      overlay.style.height = canvas.offsetHeight + "px";
    }

    if (loading) loading.style.display = "none";
    _refreshToolbar(container);

    // Hand off to the annotation layer for the current page.
    if (Annots && typeof Annots.onPageRendered === "function") {
      Annots.onPageRendered(container, s.pdfDoc, s.currentPage);
    }

    s.busy = false;
  }

  // ─── Navigation / zoom handlers ────────────────────────────────
  function _attachToolbar(container) {
    const s = _stateOf(container);
    const prev = container.querySelector(".pdf-page-prev");
    const next = container.querySelector(".pdf-page-next");
    const zsel = container.querySelector(".pdf-zoom-select");
    const saveBtn = container.querySelector(".pdf-save-btn");
    const textBtn = container.querySelector(".pdf-text-toggle");
    const undoBtn = container.querySelector(".pdf-undo-btn");
    const overlay = container.querySelector(".pdf-text-overlay");

    if (prev) prev.addEventListener("click", async () => {
      if (s.currentPage > 1) {
        s.currentPage -= 1;
        await _renderPage(container);
      }
    });
    if (next) next.addEventListener("click", async () => {
      if (s.currentPage < s.totalPages) {
        s.currentPage += 1;
        await _renderPage(container);
      }
    });
    if (zsel) zsel.addEventListener("change", async () => {
      const v = zsel.value;
      s.zoom = v === "fit" ? 1.0 : parseFloat(v) || 1.0;
      // Re-render; if user picks a numeric scale (not fit), the next
      // resize handler should NOT clamp back to fit.
      await _renderPage(container);
    });

    if (textBtn && Annots) {
      textBtn.addEventListener("click", () => {
        const active = textBtn.classList.toggle("active");
        if (overlay) overlay.classList.toggle("active", active);
        if (Annots.setTextMode) Annots.setTextMode(container, active);
      });
    }
    if (undoBtn && Annots) {
      undoBtn.addEventListener("click", () => {
        if (Annots.undoLast) Annots.undoLast(container);
        undoBtn.disabled = !(Annots.canUndo && Annots.canUndo(container));
      });
    }
    if (saveBtn && Annots) {
      saveBtn.addEventListener("click", async () => {
        const status = container.querySelector(".pdf-save-status");
        if (status) status.textContent = "Guardando…";
        try {
          const out2 = await Annots.saveAll(container, s.blockId);
          if (status) status.textContent = out2 && out2.saved > 0
            ? `✅ ${out2.saved}` : "✅ vacío";
        } catch (err) {
          if (status) status.textContent = "❌ " + (err.message || err);
        }
      });
    }

    // Re-fit on window resize so the canvas stays inside the wrap.
    let raf = null;
    window.addEventListener("resize", () => {
      if (raf) cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => _renderPage(container));
    });
  }

  // ─── Public: init(container) ───────────────────────────────────
  // container must be a `.pdf-container` element with data-url set
  // (set by courses-blocks.js render). Optional data-pdf-id lets the
  // annotation layer read existing rows.
  async function init(container) {
    if (!container) return;
    const url = container.dataset.url || container.dataset.pdfPath || "";
    const pdfId = container.dataset.pdfId
      ? parseInt(container.dataset.pdfId, 10) : null;
    const pdfUrl = _pdfUrl(url);
    const state = _stateOf(container);
    state.blockId = pdfId;

    if (!pdfUrl) {
      container.innerHTML = `<div class="pdf-box">
        <span style="font-size:24px;">📕</span>
        <span>PDF sin URL válida</span>
      </div>`;
      return;
    }
    state.pdfUrl = pdfUrl;

    // If the caller already injected markup, skip re-injection.
    if (!container.querySelector(".pdf-render-canvas")) {
      _injectTemplate(container, pdfUrl);
    }

    const loading = container.querySelector(".pdf-loading");
    if (loading) {
      loading.style.display = "block";
      loading.textContent = "Cargando PDF…";
      loading.classList.remove("is-error");
    }

    try {
      const pdfjsLib = await _loadPdfJs();
      // Fetch the PDF bytes (auth header so :8082 backend accepts us).
      const resp = await fetch(pdfUrl, {
        headers: _authHeaders(),
        credentials: "include",
      });
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status} al descargar PDF`);
      }
      const buf = await resp.arrayBuffer();
      state.pdfBytes = buf;
      if (loading) loading.textContent = "Procesando PDF…";

      const task = pdfjsLib.getDocument({ data: buf.slice(0) });
      const doc = await task.promise;
      state.pdfDoc = doc;
      state.totalPages = doc.numPages;
      state.currentPage = 1;

      // Load any existing annotations BEFORE the first render so the
      // annotation layer can paint overlays on page 1 immediately.
      if (Annots && pdfId) {
        try { await Annots.loadAll(container, pdfId); }
        catch (e) { console.warn("[PDF annots load]", e); }
      }

      _attachToolbar(container);
      await _renderPage(container);
    } catch (err) {
      console.error("[PdfViewer]", err);
      if (loading) {
        loading.textContent = "❌ " + (err.message || err);
        loading.classList.add("is-error");
        loading.style.display = "block";
      }
    }
  }

  window.App = window.App || {};
  window.App.PdfViewer = {
    init,
    _pdfUrl,
    _renderPage,
    _stateOf,
  };

  console.log("[Studyflow] pdf-viewer.js loaded");
})();