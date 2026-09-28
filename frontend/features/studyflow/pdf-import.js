/* ============================== UNIFIED PDF + SCORM IMPORT (S7b) ==============================
 * Single «📥 Importar» control in the PDF-block flow.
 *
 * Opens a small popover anchored to the block being edited (or the
 * newly-created chip block) with two source tabs:
 *
 *   • Archivo  — local PDF → POST /api/pdf/import (multipart)
 *   • SCORM    — Moodle URL or .zip path → POST /api/pdf/import (JSON)
 *
 * The SCORM tab is only shown when /api/pdf/import/status reports that
 * at least one SCORM branch is available; the URL-input sub-tab is hidden
 * when moodle_scraping is false (zip import still works).
 *
 * Public: App.PdfImport.open(block, opts)
 *   block  = block dict {id, type, title, url, ...}
 *   opts   = { courseId, topicId, anchor? }
 *
 * On success we PATCH the block (url + title) so the viewer mounts the
 * freshly-imported PDF and dispatch studyflow:blocks-changed so the topic
 * panel re-renders the new SCORM blocks.
 * ========================================================================== */

window.App = window.App || {};

window.App.PdfImport = (function () {
  "use strict";

  // ── Auth headers + URL helpers (mirrors patterns in pdf-viewer.js) ──
  function _apiBase() {
    if (typeof window.API_URL === "string" && window.API_URL) {
      return window.API_URL.replace(/\/$/, "");
    }
    return "/api";
  }
  function _authHeaders() {
    const h = { "Accept": "application/json" };
    const A = window.App && window.App.Auth;
    if (A && typeof A.authHeaders === "function") {
      Object.assign(h, A.authHeaders());
    } else if (A && typeof A.getHeaders === "function") {
      Object.assign(h, A.getHeaders());
    } else if (window.Auth && typeof window.Auth.getHeaders === "function") {
      Object.assign(h, window.Auth.getHeaders());
    }
    return h;
  }
  // Multipart-safe headers.
  //
  // The shared Auth.getHeaders() (app.js) ALWAYS injects
  // `Content-Type: application/json`. Sending that with a FormData body is
  // fatal: the browser then skips the `multipart/form-data; boundary=…`
  // header, Werkzeug refuses to parse the body, `request.files` stays empty
  // and POST /pdf/import answers
  //   400 "Body must be multipart 'file' or JSON {mode:'scorm',…}"
  // — even though a perfectly valid file was attached. cURL never showed it
  // because `curl -F` sets the boundary itself.
  //
  // Rule: on FormData uploads send ONLY Accept (+ Authorization). Never
  // Content-Type — the browser owns the boundary.
  function _uploadHeaders() {
    const h = { Accept: "application/json" };
    const auth = _authHeaders();
    if (auth && auth.Authorization) h.Authorization = auth.Authorization;
    return h;
  }
  async function _uploadForm(path, fd) {
    const r = await fetch(_apiBase() + path, {
      method: "POST", body: fd, headers: _uploadHeaders(),
      credentials: "include",
    });
    if (!r.ok && r.status !== 400 && r.status !== 413) {
      throw new Error(`HTTP ${r.status} al importar PDF`);
    }
    return r.json();
  }
  async function _postJson(path, body) {
    const h = _authHeaders();
    h["Content-Type"] = "application/json";
    const r = await fetch(_apiBase() + path, {
      method: "POST", headers: h, credentials: "include",
      body: JSON.stringify(body),
    });
    if (!r.ok && r.status !== 400) {
      throw new Error(`HTTP ${r.status} al importar SCORM`);
    }
    return r.json();
  }
  async function _patchBlock(courseId, blockId, payload) {
    const h = _authHeaders();
    h["Content-Type"] = "application/json";
    const r = await fetch(
      _apiBase() + `/courses/${courseId}/blocks/${blockId}`,
      { method: "PUT", headers: h, credentials: "include",
        body: JSON.stringify(payload) }
    );
    if (!r.ok) throw new Error(`HTTP ${r.status} al guardar bloque`);
    return r.json();
  }

  // ── Capability snapshot (cached per-session, refreshed on demand) ──
  let _cap = null;
  let _capLoading = null;

  async function _loadCap() {
    if (_cap) return _cap;
    if (_capLoading) return _capLoading;
    _capLoading = (async () => {
      try {
        const h = _authHeaders();
        const r = await fetch(_apiBase() + "/pdf/import/status", {
          headers: h, credentials: "include",
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = await r.json();
        _cap = {
          pdf: !!(data && data.pdf),
          scorm_zip: !!(data && data.scorm_zip),
          moodle_scraping: !!(data && data.moodle_scraping),
        };
        return _cap;
      } catch (_) {
        _cap = { pdf: true, scorm_zip: false, moodle_scraping: false };
        return _cap;
      }
    })();
    return _capLoading;
  }

  // ── DOM helpers ─────────────────────────────────────────────────
  function _el(tag, attrs = {}, children = []) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") node.className = v;
      else if (k === "html") node.innerHTML = v;
      else if (k.startsWith("on") && typeof v === "function") {
        node.addEventListener(k.slice(2).toLowerCase(), v);
      } else {
        node.setAttribute(k, v);
      }
    }
    for (const c of children) {
      if (c == null) continue;
      node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    }
    return node;
  }

  function _close() {
    const existing = document.querySelector(".sf-pdf-import-popover");
    if (existing) existing.remove();
  }

  // ── Tab switcher ───────────────────────────────────────────────
  function _setActiveTab(popover, name) {
    popover.querySelectorAll(".sf-pdfi-tab").forEach((b) => {
      b.classList.toggle("is-active", b.dataset.tab === name);
    });
    popover.querySelectorAll(".sf-pdfi-panel").forEach((p) => {
      p.style.display = p.dataset.panel === name ? "" : "none";
    });
  }

  // ── Archivo tab: file input → multipart POST ────────────────────
  function _renderArchivoPanel(popover, ctx) {
    const fileInput = _el("input", {
      type: "file", accept: "application/pdf,.pdf",
    });
    const status = _el("div", { class: "sf-pdfi-status" });
    const submit = _el("button", {
      type: "button", class: "ht-btn p",
      onclick: async () => {
        if (!fileInput.files || !fileInput.files[0]) {
          status.textContent = "⚠ Selecciona un PDF primero.";
          return;
        }
        submit.disabled = true;
        status.textContent = "Subiendo…";
        try {
          const fd = new FormData();
          fd.append("file", fileInput.files[0]);
          if (ctx.courseId) fd.append("course_id", String(ctx.courseId));
          if (ctx.topicId)  fd.append("topic_id", String(ctx.topicId));
          const data = await _uploadForm("/pdf/import", fd);
          if (!data || !data.ok) {
            status.textContent = "❌ " + ((data && data.reason) || "Error");
          } else {
            status.textContent = "✅ PDF subido";
            _onFileImported(ctx, data);
          }
        } catch (err) {
          status.textContent = "❌ " + (err.message || err);
        } finally {
          submit.disabled = false;
        }
      },
    }, ["📤 Subir PDF"]);

    const panel = _el("div", { class: "sf-pdfi-panel", "data-panel": "file" }, [
      _el("div", { class: "sf-pdfi-row" }, [
        _el("label", { class: "sf-pdfi-label" }, ["Archivo PDF"]),
        fileInput,
      ]),
      _el("div", { class: "sf-pdfi-row" }, [submit, status]),
    ]);
    return panel;
  }

  // ── SCORM tab: URL or zip path → JSON POST ─────────────────────
  function _renderScormPanel(popover, ctx) {
    const cap = _cap || { moodle_scraping: false, scorm_zip: true };
    const urlInput = _el("input", {
      type: "text", placeholder: "https://aulavirtual…/course/view.php?id=…",
    });
    const zipInput = _el("input", {
      type: "text", placeholder: "/ruta/al/paquete.zip",
    });
    const status = _el("div", { class: "sf-pdfi-status" });
    const submit = _el("button", {
      type: "button", class: "ht-btn p",
      onclick: async () => {
        const urlVal = urlInput.value.trim();
        const zipVal = zipInput.value.trim();
        if (!urlVal && !zipVal) {
          status.textContent = "⚠ Indica URL o ruta al ZIP.";
          return;
        }
        submit.disabled = true;
        status.textContent = "Importando…";
        try {
          const body = {
            mode: "scorm",
            url: urlVal,
            path: zipVal,
            title: "",
          };
          if (ctx.courseId) body.course_id = ctx.courseId;
          if (ctx.topicId)  body.topic_id  = ctx.topicId;
          const data = await _postJson("/pdf/import", body);
          if (!data || !data.ok) {
            status.textContent = "❌ " + ((data && data.reason) || "Error");
          } else if (data.count === 0) {
            status.textContent = "ℹ " + (data.note || "Importado sin bloques.");
          } else {
            status.textContent = `✅ ${data.count} bloque(s) importado(s)`;
            _onScormImported(ctx, data);
          }
        } catch (err) {
          status.textContent = "❌ " + (err.message || err);
        } finally {
          submit.disabled = false;
        }
      },
    }, ["📥 Importar SCORM"]);

    const rows = [
      _el("div", { class: "sf-pdfi-row" }, [
        _el("label", { class: "sf-pdfi-label" }, ["Moodle URL"]),
        urlInput,
      ]),
    ];
    if (cap.scorm_zip) {
      rows.push(_el("div", { class: "sf-pdfi-row" }, [
        _el("label", { class: "sf-pdfi-label" }, ["Ruta al ZIP"]),
        zipInput,
      ]));
    }
    if (!cap.moodle_scraping && cap.scorm_zip) {
      rows.push(_el("div", {
        class: "sf-pdfi-hint",
      }, [
        "ℹ Scraping Moodle no disponible en este equipo "
        + "(instalar selenium/Chrome o activar SCRAPING_ENABLED). "
        + "El importador por ZIP sí funciona.",
      ]));
    } else if (!cap.moodle_scraping && !cap.scorm_zip) {
      rows.push(_el("div", { class: "sf-pdfi-hint" }, [
        "⚠ Ningún importador SCORM está disponible.",
      ]));
    }
    rows.push(_el("div", { class: "sf-pdfi-row" }, [submit, status]));
    return _el("div", {
      class: "sf-pdfi-panel", "data-panel": "scorm",
    }, rows);
  }

  // ── Success handlers ───────────────────────────────────────────
  async function _onFileImported(ctx, data) {
    if (ctx.block && ctx.block.id) {
      try {
await _patchBlock(
        ctx.courseId, ctx.block.id,
        { url: data.url, title: data.title || data.pdf.original_name }
      );
      } catch (_) { /* non-fatal; the topic refresh below still works */ }
    }
    _close();
    window.dispatchEvent(new CustomEvent("studyflow:blocks-changed", {
      detail: { courseId: ctx.courseId, topicId: ctx.topicId },
    }));
  }

  function _onScormImported(ctx, data) {
    _close();
    window.dispatchEvent(new CustomEvent("studyflow:blocks-changed", {
      detail: { courseId: ctx.courseId, topicId: ctx.topicId },
    }));
  }

  // ── Public: open(block, opts) ──────────────────────────────────
  async function open(block, opts) {
    opts = opts || {};
    _close();
    const ctx = {
      block: block || null,
        courseId: opts.courseId || (block && block.course_id) || null,
        topicId:  opts.topicId  || (block && block.topic_id)  || null,
      anchor: opts.anchor || null,
    };
    const cap = await _loadCap();
    const showScorm = !!(cap.scorm_zip || cap.moodle_scraping);

    const tabButtons = [
      _el("button", {
        type: "button", class: "sf-pdfi-tab is-active",
        "data-tab": "file",
        onclick: (e) => {
          e.stopPropagation();
          _setActiveTab(popover, "file");
        },
      }, ["📄 Archivo"]),
    ];
    if (showScorm) {
      tabButtons.push(_el("button", {
        type: "button", class: "sf-pdfi-tab",
        "data-tab": "scorm",
        onclick: (e) => {
          e.stopPropagation();
          _setActiveTab(popover, "scorm");
        },
      }, ["📦 SCORM"]));
    }

    const popover = _el("div", {
      class: "sf-pdf-import-popover", role: "dialog",
      "aria-label": "Importar PDF o SCORM",
    }, [
      _el("div", { class: "sf-pdfi-tabs" }, tabButtons),
      _renderArchivoPanel(null, ctx),
      ...(showScorm ? [_renderScormPanel(null, ctx)] : []),
      _el("button", {
        type: "button", class: "sf-pdfi-close",
        title: "Cerrar",
        onclick: () => _close(),
      }, ["✕"]),
    ]);

    document.body.appendChild(popover);

    // Position next to anchor (best-effort; falls back to centered)
    if (ctx.anchor && ctx.anchor.getBoundingClientRect) {
      const r = ctx.anchor.getBoundingClientRect();
      popover.style.position = "fixed";
      popover.style.top = (r.bottom + 6) + "px";
      popover.style.left = Math.min(r.left, window.innerWidth - 360) + "px";
      popover.style.zIndex = 9999;
    } else {
      popover.style.position = "fixed";
      popover.style.top = "50%";
      popover.style.left = "50%";
      popover.style.transform = "translate(-50%, -50%)";
      popover.style.zIndex = 9999;
    }

    // Outside-click closes the popover
    setTimeout(() => {
      const onDoc = (e) => {
        if (!popover.contains(e.target)) {
          popover.remove();
          document.removeEventListener("click", onDoc, true);
        }
      };
      document.addEventListener("click", onDoc, true);
    }, 0);
  }

  // ── Inject a "📥 Importar" button next to the Save/Cancel row of a pdf-ref form ──
  // Returns true if injected, false if no pdf-ref form was found.
  function injectIntoEditForm(formEl, block, ctx) {
    if (!formEl) return false;
    if (formEl.querySelector(".sf-pdfi-import-btn")) return true;
    const actions = formEl.querySelector(".sf-td-md-actions");
    if (!actions) return false;
    const btn = _el("button", {
      type: "button", class: "ht-btn sf-pdfi-import-btn",
      title: "Importar PDF o SCORM",
      onclick: (e) => {
        e.preventDefault();
        e.stopPropagation();
        open(block, Object.assign({}, ctx, { anchor: btn }));
      },
    }, ["📥 Importar"]);
    actions.insertBefore(btn, actions.firstChild);
    return true;
  }

  return {
    open, injectIntoEditForm, _close, _loadCap,
  };
})();

console.log("[Studyflow] pdf-import.js loaded");