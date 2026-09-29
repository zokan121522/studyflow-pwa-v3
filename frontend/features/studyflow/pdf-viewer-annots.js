/* ============================== PDF ANNOTATIONS (S7) ==========================
 * Minimal text-annotation layer for App.PdfViewer.
 *
 * Scope:
 *   - click an empty spot in the overlay → spawn a contenteditable <div>
 *     (a "text item") at that position
 *   - edit, drag, list all annotations for the current page
 *   - "Save" POSTs every page's text items to /api/pdf/annotate
 *   - on init, GET /api/pdf/<id>/annotations and re-render
 *
 * NOT in scope (kept for later phases):
 *   - freehand strokes / eraser marks
 *   - shape tools (circle / square / triangle / line)
 *   - bake-into-PDF export (would need pdf-lib)
 *
 * Stored shape per page (data field on the row):
 *   { items: [ { id, x, y, w, h, text } ] }
 *
 * Conventions:
 *   - x / y are CSS pixels RELATIVE TO THE TEXT OVERLAY (which is
 *     positioned over the canvas at viewport scale).
 *   - The overlay size is reset by the viewer on each page change; we
 *     store positions in CSS px so a re-render at any zoom just remaps
 *     them onto the new overlay rect.
 * ============================================================================ */
(function () {
  "use strict";

  const Auth = window.App && window.App.Auth ? window.App.Auth : null;

  // Per-container state beyond what PdfViewer owns.
  // pdfId → annotations keyed by page number (1-based).
  const _annotsByPdf = new Map(); // pdfId → Map<pageNum, Array<item>>

  // ─── Auth / API helpers ─────────────────────────────────────────
  // Same lazy App.Auth read as pdf-viewer.js: the namespace may not
  // exist at module load time (agenda-glue.js populates it later).
  function _authHeaders() {
    const h = { "Content-Type": "application/json" };
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

  function _apiBase() {
    return (typeof window.API_URL === "string" && window.API_URL)
      || "/api";
  }

  // ─── Get per-container state from PdfViewer (which keeps WeakMap) ──
  function _getViewerState(container) {
    const PV = window.App && window.App.PdfViewer;
    if (PV && typeof PV._stateOf === "function") return PV._stateOf(container);
    return null;
  }

  function _overlay(container) {
    return container.querySelector(".pdf-text-overlay");
  }

  function _wrap(container) {
    return container.querySelector(".pdf-canvas-wrap");
  }

  function _listEl(container) {
    return container.querySelector(".pdf-annots-list");
  }

  // ─── Storage accessors ─────────────────────────────────────────
  function _bucketFor(pdfId) {
    if (!_annotsByPdf.has(pdfId)) {
      _annotsByPdf.set(pdfId, new Map());
    }
    return _annotsByPdf.get(pdfId);
  }

  function _itemsForPage(pdfId, page) {
    const bucket = _bucketFor(pdfId);
    if (!bucket.has(page)) bucket.set(page, []);
    return bucket.get(page);
  }

  // ─── onPageRendered(container, pdfDoc, page) ───────────────────
  // Called by PdfViewer after each page finishes rendering. Repaints
  // the overlay with stored text items for the current page and
  // refreshes the annotation list summary.
  function onPageRendered(container, _pdfDoc, page) {
    const state = _getViewerState(container);
    if (!state) return;
    const pdfId = state.blockId;
    if (!pdfId) return;
    const overlay = _overlay(container);
    if (!overlay) return;

    overlay.innerHTML = "";
    const items = _itemsForPage(pdfId, page);
    for (const it of items) {
      _spawnItem(container, it, /*focus=*/false);
    }
    _refreshList(container);
  }

  // ─── setTextMode(container, on) ────────────────────────────────
  // Toggle click-to-add on the overlay.
  function setTextMode(container, on) {
    const overlay = _overlay(container);
    if (!overlay) return;
    overlay.classList.toggle("active", !!on);
    overlay.style.pointerEvents = on ? "auto" : "none";
    if (on) {
      overlay.addEventListener("click", _onOverlayClick);
    } else {
      overlay.removeEventListener("click", _onOverlayClick);
    }
  }

  function _onOverlayClick(ev) {
    const overlay = ev.currentTarget;
    const container = overlay.closest(".pdf-container");
    if (!container) return;
    if (ev.target !== overlay) return; // ignore clicks on existing items
    const rect = overlay.getBoundingClientRect();
    const x = ev.clientX - rect.left;
    const y = ev.clientY - rect.top;
    const state = _getViewerState(container);
    if (!state || !state.blockId) return;
    const item = {
      id: "a_" + Math.random().toString(36).slice(2, 10),
      x: Math.round(x),
      y: Math.round(y),
      w: 0, h: 0,
      text: "",
    };
    const items = _itemsForPage(state.blockId, state.currentPage);
    items.push(item);
    _spawnItem(container, item, /*focus=*/true);
    _refreshList(container);
  }

  // ─── _spawnItem(container, item, focus) ───────────────────────
  // Build the contenteditable div for an item at its (x,y).
  function _spawnItem(container, item, focus) {
    const overlay = _overlay(container);
    if (!overlay) return;
    const el = document.createElement("div");
    el.className = "pdf-text-item";
    el.dataset.id = item.id;
    el.contentEditable = "true";
    el.style.left = item.x + "px";
    el.style.top = item.y + "px";
    el.style.minWidth = "40px";
    el.style.minHeight = "24px";
    el.textContent = item.text || "";
    overlay.appendChild(el);

    el.addEventListener("input", () => {
      item.text = el.textContent || "";
      _refreshList(container);
    });
    el.addEventListener("blur", () => {
      // Cache the size on blur so we can persist w/h on save.
      const r = el.getBoundingClientRect();
      item.w = Math.round(r.width);
      item.h = Math.round(r.height);
    });
    // Drag-to-move (only when not editing).
    let drag = null;
    el.addEventListener("mousedown", (ev) => {
      if (ev.button !== 0) return;
      // Don't start a drag while the user is selecting text inside.
      const sel = window.getSelection && window.getSelection();
      if (sel && sel.toString().length > 0) return;
      drag = { dx: ev.clientX - el.offsetLeft, dy: ev.clientY - el.offsetTop };
      ev.preventDefault();
    });
    document.addEventListener("mousemove", (ev) => {
      if (!drag) return;
      const ov = _overlay(container);
      if (!ov) return;
      const r = ov.getBoundingClientRect();
      let nx = ev.clientX - drag.dx;
      let ny = ev.clientY - drag.dy;
      nx = Math.max(0, Math.min(nx, r.width - 20));
      ny = Math.max(0, Math.min(ny, r.height - 16));
      el.style.left = nx + "px";
      el.style.top = ny + "px";
      item.x = Math.round(nx);
      item.y = Math.round(ny);
    });
    document.addEventListener("mouseup", () => { drag = null; });
    el.addEventListener("keydown", (ev) => {
      if (ev.key === "Backspace" && (ev.metaKey || ev.ctrlKey)) {
        ev.preventDefault();
        _deleteItem(container, item);
      }
    });

    if (focus) {
      requestAnimationFrame(() => {
        el.focus();
        const sel = window.getSelection && window.getSelection();
        if (sel) {
          const range = document.createRange();
          range.selectNodeContents(el);
          range.collapse(false);
          sel.removeAllRanges();
          sel.addRange(range);
        }
      });
    }
  }

  function _deleteItem(container, item) {
    const state = _getViewerState(container);
    if (!state || !state.blockId) return;
    const items = _itemsForPage(state.blockId, state.currentPage);
    const idx = items.findIndex((x) => x.id === item.id);
    if (idx >= 0) items.splice(idx, 1);
    const el = container.querySelector(
      `.pdf-text-item[data-id="${CSS.escape(item.id)}"]`
    );
    if (el) el.remove();
    _refreshList(container);
  }

  // ─── Refresh the 📝 Anotaciones list ───────────────────────────
  function _refreshList(container) {
    const list = _listEl(container);
    if (!list) return;
    const state = _getViewerState(container);
    if (!state || !state.blockId) {
      list.innerHTML = "<li><em>(sin PDF vinculado)</em></li>";
      return;
    }
    const bucket = _bucketFor(state.blockId);
    const pages = Array.from(bucket.keys()).sort((a, b) => a - b);
    if (!pages.length) {
      list.innerHTML = "<li><em>(sin anotaciones todavía)</em></li>";
      return;
    }
    const rows = [];
    for (const p of pages) {
      const items = bucket.get(p) || [];
      for (const it of items) {
        const snippet = (it.text || "(vacío)").slice(0, 60);
        rows.push(`<li>p.${p}: ${escapeHtml(snippet)}</li>`);
      }
    }
    list.innerHTML = rows.join("");
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // ─── canUndo / undoLast ────────────────────────────────────────
  function canUndo(container) {
    const state = _getViewerState(container);
    if (!state || !state.blockId) return false;
    const items = _itemsForPage(state.blockId, state.currentPage);
    return items.length > 0;
  }

  function undoLast(container) {
    const state = _getViewerState(container);
    if (!state || !state.blockId) return;
    const items = _itemsForPage(state.blockId, state.currentPage);
    const last = items.pop();
    if (!last) return;
    const el = container.querySelector(
      `.pdf-text-item[data-id="${CSS.escape(last.id)}"]`
    );
    if (el) el.remove();
    _refreshList(container);
  }

  // ─── loadAll(container, pdfId) ─────────────────────────────────
  // GET /api/pdf/<id>/annotations and seed the in-memory bucket.
  async function loadAll(container, pdfId) {
    if (!pdfId) return;
    const url = `${_apiBase()}/pdf/${pdfId}/annotations`;
    let resp;
    try {
      resp = await fetch(url, {
        headers: _authHeaders(),
        credentials: "include",
      });
    } catch (err) {
      console.warn("[PdfAnnots] offline / network error:", err.message);
      return;
    }
    if (!resp.ok) {
      console.warn("[PdfAnnots] load HTTP", resp.status);
      return;
    }
    const body = await resp.json();
    const bucket = _bucketFor(pdfId);
    bucket.clear();
    for (const ann of (body.annotations || [])) {
      const data = ann.data || {};
      const items = Array.isArray(data.items) ? data.items : [];
      bucket.set(ann.page, items);
    }
  }

  // ─── saveAll(container, pdfId) ─────────────────────────────────
  // POST every page's items as { data: { items: [...] } }. Returns
  // { saved: N } for the toolbar status.
  async function saveAll(container, pdfId) {
    if (!pdfId) throw new Error("PDF sin id — no se puede guardar");
    const bucket = _bucketFor(pdfId);
    const pages = Array.from(bucket.keys()).sort((a, b) => a - b);
    let saved = 0;
    for (const page of pages) {
      const items = bucket.get(page) || [];
      const payload = {
        pdf_id: pdfId,
        page,
        data: { items: items.map((it) => ({
          id: it.id, x: it.x, y: it.y, w: it.w || 0, h: it.h || 0,
          text: it.text || "",
        })) },
      };
      const resp = await fetch(`${_apiBase()}/pdf/annotate`, {
        method: "POST",
        headers: _authHeaders(),
        credentials: "include",
        body: JSON.stringify(payload),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ error: resp.statusText }));
        throw new Error(err.error || `HTTP ${resp.status} en página ${page}`);
      }
      saved += 1;
    }
    _refreshList(container);
    return { saved };
  }

  window.App = window.App || {};
  window.App.PdfAnnots = {
    onPageRendered,
    setTextMode,
    canUndo,
    undoLast,
    loadAll,
    saveAll,
  };

  console.log("[Studyflow] pdf-viewer-annots.js loaded");
})();