/* ============================== SCORM SSE PSEUDO-TERMINAL (S7b-A) ==============================
 * Companion to App.PdfImport: reads the text/event-stream produced by
 * POST /api/pdf/import?stream=1 and renders it as a timestamped terminal.
 *
 * Event protocol (mirrors v2 routes/scraping.py):
 *   {"step": "…", "ts": …}      → one terminal line
 *   {"done": true, "result": …} → success / soft-error envelope
 *   {"done": true, "error": …}  → hard failure (worker exception)
 *   ": ping"                    → SSE comment heartbeat; ignored here
 *
 * Public: App.PdfImportStream.{ createTerm, append, stream }
 * ========================================================================== */

window.App = window.App || {};

window.App.PdfImportStream = (function () {
  "use strict";

  const MAX_LINES = 200; // keep the DOM light, like v2's .scorm-term

  // ── Terminal DOM ───────────────────────────────────────────────
  function createTerm() {
    const el = document.createElement("pre");
    el.className = "sf-pdfi-term";
    el.setAttribute("aria-live", "polite");
    return el;
  }

  function _stamp() {
    const d = new Date();
    const p = (n) => String(n).padStart(2, "0");
    return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  }

  function append(term, text, cls) {
    if (!term) return;
    const line = document.createElement("div");
    if (cls) line.className = cls;
    line.textContent = `[${_stamp()}] ${text}`;
    term.appendChild(line);
    while (term.childNodes.length > MAX_LINES) term.removeChild(term.firstChild);
    term.scrollTop = term.scrollHeight;
  }

  // ── Streaming POST ─────────────────────────────────────────────
  // opts = { method?, headers, body?, signal?, onStep(msg), onDone(evt) }
  //
  // onDone fires exactly once: with {done, result} / {done, error} frames,
  // or with a synthesized error when the server answers plain JSON.
  async function stream(url, opts) {
    let delivered = false;
    const done = (evt) => {
      if (delivered) return;
      delivered = true;
      opts.onDone(evt || { done: true, error: "Stream vacío" });
    };
    const resp = await fetch(url, {
      method: opts.method || "POST",
      headers: opts.headers,
      body: opts.body,
      credentials: "include",
      signal: opts.signal,
    });
    const ctype = (resp.headers.get("content-type") || "").toLowerCase();
    if (!ctype.includes("text/event-stream")) {
      done(await _jsonOrError(resp));
      return;
    }
    if (!resp.ok) done({ done: true, error: `HTTP ${resp.status}` });
    else await _readFrames(resp, done, opts.onStep);
  }

  async function _jsonOrError(resp) {
    try {
      const data = await resp.json();
      // Soft-error envelopes ({ok:false, reason}) must reach the caller as-is.
      if (data && typeof data === "object" && "reason" in data) {
        return { done: true, result: data };
      }
      return { done: true, error: (data && data.error) || `HTTP ${resp.status}` };
    } catch (_) {
      return { done: true, error: `HTTP ${resp.status}` };
    }
  }

  async function _readFrames(resp, done, onStep) {
    if (!resp.body || !resp.body.getReader) {
      done({ done: true, error: "Este navegador no soporta streaming." });
      return;
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buf = "";
    let final = null;
    while (!final) {
      const { value, done: eof } = await reader.read();
      if (eof) break;
      buf += decoder.decode(value, { stream: true });
      const frames = buf.split("\n\n");
      buf = frames.pop() || ""; // keep the incomplete tail for the next chunk
      for (const frame of frames) final = _handleFrame(frame, done, onStep) || final;
    }
    if (!final && buf.trim()) final = _handleFrame(buf, done, onStep);
    if (!final) done({ done: true, error: "Stream cerrado sin evento final." });
  }

  // Parses one SSE frame; returns the final event when the frame carries
  // `done`, otherwise null. Heartbeat comments (": ping") are skipped.
  function _handleFrame(frame, done, onStep) {
    for (const raw of frame.split("\n")) {
      const line = raw.trim();
      if (!line || line.startsWith(":") || line.startsWith("event:")) continue;
      if (!line.startsWith("data:")) continue;
      let evt;
      try {
        evt = JSON.parse(line.slice(5).trim());
      } catch (_) {
        continue;
      }
      if (evt && evt.done === true) {
        done(evt);
        return evt;
      }
      if (evt && typeof evt.step === "string") onStep(evt.step, evt);
    }
    return null;
  }

  return { createTerm, append, stream };
})();

console.log("[Studyflow] pdf-import-stream.js loaded");
