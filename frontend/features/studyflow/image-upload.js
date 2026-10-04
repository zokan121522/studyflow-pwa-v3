/* ============================================================================
 * STUDYFLOW — IMAGE BLOCKS (R4)
 * ============================================================================
 *
 * Picks an image from disk, uploads it, and creates the block that shows it.
 * Split from courses-blocks.js because the flow is not rendering: it is a
 * fetch with a progress state, a native picker, and two failure modes worth
 * naming. Rendering stays where it belongs.
 *
 * Order matters: upload FIRST, then create the block. The reverse would leave
 * an image row nobody references if the block creation failed, and nothing in
 * the UI can reach such a row afterwards.
 * ========================================================================== */

window.App = window.App || {};

window.App.ImageUpload = (function () {
  "use strict";

  // Mirrors MAX_IMAGE_SIZE in backend/routes/image.py. The server is the
  // authority — this only exists so a 40 MB photo fails instantly instead of
  // after a long upload on a metered connection.
  const MAX_BYTES = 15 * 1024 * 1024;

  // The server sniffs magic bytes and accepts PNG/JPEG only; `accept` on the
  // picker is a convenience filter, not the check.
  const OK_TYPES = ["image/png", "image/jpeg"];

  const ACCEPT_ATTR = "image/png,image/jpeg,.png,.jpg,.jpeg";

  function _apiBase() {
    if (typeof window.API_URL === "string" && window.API_URL) {
      return window.API_URL.replace(/\/$/, "");
    }
    return "/api";
  }

  function _authHeaders() {
    const h = { Accept: "application/json" };
    const A = window.App && window.App.Auth;
    if (A && typeof A.authHeaders === "function") {
      Object.assign(h, A.authHeaders());
    } else if (A && typeof A.getHeaders === "function") {
      Object.assign(h, A.getHeaders());
    } else if (window.Auth && typeof window.Auth.getHeaders === "function") {
      Object.assign(h, window.Auth.getHeaders());
    }
    // Strip anything that would break a multipart body: the browser has to
    // generate the boundary itself. See the long note in pdf-import.js.
    delete h["Content-Type"];
    return h;
  }

  function _toast(msg, isError) {
    let host = document.getElementById("sf-image-toast");
    if (!host) {
      host = document.createElement("div");
      host.id = "sf-image-toast";
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
    host.style.borderColor = isError ? "#c0392b" : "#3a4a6e";
    clearTimeout(host._sfToastTimer);
    host._sfToastTimer = setTimeout(() => {
      host.style.display = "none";
    }, 5000);
  }

  // ── _pickFile() → Promise<File|null> ───────────────────────────
  // Native picker via a throwaway <input type=file>.
  //
  // The 'cancel' event is not universal (older Safari lacks it), so a focus
  // fallback closes the promise too. Without it, dismissing the dialog leaves
  // this function pending forever and the chip looks frozen.
  function _pickFile() {
    return new Promise((resolve) => {
      const input = document.createElement("input");
      input.type = "file";
      input.accept = ACCEPT_ATTR;
      input.style.display = "none";
      document.body.appendChild(input);

      let settled = false;
      const finish = (file) => {
        if (settled) return;
        settled = true;
        input.remove();
        resolve(file || null);
      };

      input.addEventListener("change", () => {
        finish(input.files && input.files[0]);
      });
      input.addEventListener("cancel", () => finish(null));
      window.addEventListener(
        "focus",
        () => setTimeout(() => finish(input.files && input.files[0]), 500),
        { once: true }
      );
      input.click();
    });
  }

  // ── _validate(file) → string|null ──────────────────────────────
  // Returns a user-facing error message, or null when the file is fine.
  function _validate(file) {
    if (!file) return "No se seleccionó ningún archivo.";
    // Some browsers report an empty type for a valid file picked from a
    // volume it does not recognise, so the extension gets a say here. The
    // server still decides.
    const byExt = /\.(png|jpe?g)$/i.test(file.name || "");
    if (!OK_TYPES.includes(file.type) && !(file.type === "" && byExt)) {
      return "Solo se admiten imágenes PNG o JPEG.";
    }
    if (file.size === 0) return "El archivo está vacío.";
    if (file.size > MAX_BYTES) {
      const mb = (MAX_BYTES / (1024 * 1024)).toFixed(0);
      return `La imagen supera el límite de ${mb} MB.`;
    }
    return null;
  }

  // ── _upload(file, courseId, topicId) → image dict ──────────────
  async function _upload(file, courseId, topicId) {
    const fd = new FormData();
    fd.append("file", file);
    if (courseId) fd.append("course_id", String(courseId));
    if (topicId) fd.append("topic_id", String(topicId));

    const r = await fetch(_apiBase() + "/image/upload", {
      method: "POST",
      body: fd,
      headers: _authHeaders(),
      credentials: "include",
    });

    let data = null;
    try {
      data = await r.json();
    } catch (_) {
      /* non-JSON error page; handled by the status check below */
    }
    if (!r.ok) {
      throw new Error(
        (data && data.error) || `Error ${r.status} al subir la imagen`
      );
    }
    if (!data || !data.image) throw new Error("Respuesta inesperada del servidor");
    return data.image;
  }

  // ── pickAndUpload({ courseId, topicId, title }) → block|null ──
  // The entry point the 🖼 chip calls. Resolves to the created block, or null
  // if the user cancelled or something failed (both already reported).
  async function pickAndUpload(opts) {
    const o = opts || {};
    const file = await _pickFile();
    if (!file) return null; // Cancelled: not an error, stay silent.

    const problem = _validate(file);
    if (problem) {
      _toast(problem, true);
      return null;
    }

    _toast(`📤 Subiendo ${file.name}…`);
    let image;
    try {
      image = await _upload(file, o.courseId, o.topicId);
    } catch (err) {
      _toast(`❌ ${err.message}`, true);
      return null;
    }

    // Goes through CoursesAPI.addBlock rather than a raw fetch so the
    // per-session course cache is dropped and the new block shows up without
    // a manual reload.
    try {
      const block = await window.App.CoursesAPI.addBlock(o.courseId, {
        topic_id: o.topicId || undefined,
        type: "image",
        image_id: image.id,
        // Spec: the uploaded filename becomes the title. The server derives
        // its own title from the sanitised name; prefer it, fall back to the
        // raw filename so an edge case never leaves the block untitled.
        title: image.title || o.title || "",
      });
      _toast("🖼 Imagen añadida");
      return block || null;
    } catch (err) {
      // The image exists but nothing points at it, and the UI has no way to
      // reach an orphan row. Say so instead of pretending it worked.
      _toast(
        `⚠️ Imagen subida pero no se pudo crear el bloque: ${err.message}`,
        true
      );
      return null;
    }
  }

  return { pickAndUpload, _validate, _pickFile, MAX_BYTES };
})();

console.log("[Studyflow] image-upload.js loaded");