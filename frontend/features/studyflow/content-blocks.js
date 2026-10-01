// ─── Content blocks engine — self-contained safe markdown renderer ──
// Namespace: window.App.ContentBlocks
// Dependencies: window.App.UI (escHtml). NO CDN libs (offline PWA).
//
// Exposes:
//   _renderMd(md)                   — markdown subset → safe HTML
//   renderContentPreview(c, html)   — full-doc iframe / snippet shadow
//   renderInteractive(c, fragment)  — sandboxed iframe for HTML+CSS+JS
//
// The markdown parser is intentionally small (no marked/DOMPurify): we
// HTML-escape FIRST, then apply only the patterns we recognise. That
// guarantees user input can never inject a tag the engine doesn't emit
// itself. Pairs of code spans/blocks are protected before escaping so
// their inner `<`/`&` survive into the output unchanged.
//
// Supported markdown subset:
//   headings (# ## ###), bold (**x**), italic (*x*), inline code (`x`),
//   fenced code blocks (```), unordered lists (- or *), ordered lists
//   (1.), blockquotes (>), horizontal rule (---), links ([t](url) — only
//   http/mailto/#), tables (| h | v |, | --- | --- |, | c | c |),
//   Obsidian-style callouts (> [!type] Title / Body), YouTube embeds
//   ([label](youtube-url) → click-to-load card).

window.App = window.App || {};
window.App.ContentBlocks = (function () {
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

  // ── Callout registry (icons + default titles) ──────────────────
  const CALLOUT_ICONS = {
    note: "📝", tip: "💡", hint: "💡",
    warning: "⚠️", caution: "⚠️",
    info: "ℹ️",
    abstract: "📋", summary: "📋", tldr: "📋",
    example: "📌",
    success: "✅", done: "✅",
    danger: "❗", attention: "❗",
    question: "❓", help: "❓",
    bug: "🐛", failure: "❌", fail: "❌",
    quote: "💬", cite: "💬",
  };
  const CALLOUT_DEFAULTS = {
    note: "Nota", tip: "Consejo", warning: "Aviso", info: "Información",
  };

  // ── YouTube helpers ────────────────────────────────────────────
  const YT_URL_RE =
    /(?:youtube\.com\/(?:watch\?v=|embed\/|shorts\/|live\/)|youtu\.be\/)[a-zA-Z0-9_-]{11}/;

  function _ytIdFromUrl(url) {
    const m = /[?&]v=([a-zA-Z0-9_-]{11})/.exec(url)
      || /youtu\.be\/([a-zA-Z0-9_-]{11})/.exec(url)
      || /youtube\.com\/(?:embed|shorts|live)\/([a-zA-Z0-9_-]{11})/.exec(url);
    return m ? m[1] : "";
  }

  function _renderYouTubeCard(href, title, text) {
    const id = _ytIdFromUrl(href);
    const t = title ? ` title="${escHtml(title)}"` : "";
    const label = (text && text.trim() && text.trim() !== href)
      ? text : "Ver vídeo";
    return `<div class="yt-card" data-yt-id="${escHtml(id)}" data-yt-url="${escHtml(href)}"${t} role="button" tabindex="0">`
      + `<span class="yt-thumb">▶</span>`
      + `<span class="yt-card-body">`
      + `<span class="yt-card-title">${escHtml(label)}</span>`
      + `<span class="yt-card-meta">▶ YouTube · `
      + `<a class="yt-ext" href="${escHtml(href)}" target="_blank" rel="noopener noreferrer">↗ abrir</a>`
      + `</span></span></div>`;
  }

  // ── Inline passes ──────────────────────────────────────────────
  // Pre-extract inline code spans and YouTube-link candidates BEFORE the
  // html-escape step so their inner characters survive untouched.
  function _extractInlines(s) {
    const stash = [];
    // inline code `...`
    s = s.replace(/`([^`\n]+)`/g, function (_, c) {
      stash.push("<code>" + escHtml(c) + "</code>");
      return "\u0000I" + (stash.length - 1) + "\u0000";
    });
    // links [text](url) — handle YouTube specially
    s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, function (_, text, href) {
      if (YT_URL_RE.test(href)) {
        stash.push(_renderYouTubeCard(href, "", text));
      } else if (/^(https?:|mailto:|#|\/)/i.test(href)) {
        stash.push(
          `<a href="${escHtml(href)}" target="_blank" rel="noopener noreferrer">${escHtml(text)}</a>`
        );
      } else {
        stash.push(escHtml(text));
      }
      return "\u0000I" + (stash.length - 1) + "\u0000";
    });
    // Now HTML-escape the rest
    s = escHtml(s);
    // Bold / italic (order matters)
    s = s.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/\*([^*\n]+)\*/g, "<em>$1</em>");
    // Restore stashed inlines
    s = s.replace(/\u0000I(\d+)\u0000/g, function (_, i) { return stash[+i]; });
    return s;
  }

  // ── Block parser ───────────────────────────────────────────────
  // Reads lines, emits HTML. Handles fenced code, tables, callouts,
  // headings, blockquotes, lists, hr, and paragraphs.
  function _parseBlocks(lines) {
    const out = [];
    let i = 0;
    while (i < lines.length) {
      const ln = lines[i];

      // Fenced code block ``` … ```
      const fence = /^(\s*)```/.exec(ln);
      if (fence) {
        const body = [];
        i++;
        while (i < lines.length && !/^\s*```/.test(lines[i])) {
          body.push(lines[i]); i++;
        }
        if (i < lines.length) i++; // closing fence
        out.push("<pre><code>" + escHtml(body.join("\n")) + "</code></pre>");
        continue;
      }

      // Horizontal rule
      if (/^\s*-{3,}\s*$/.test(ln)) {
        out.push("<hr>"); i++; continue;
      }

      // Headings (# … ######)
      const h = /^\s*(#{1,6})\s+(.+?)\s*#*\s*$/.exec(ln);
      if (h) {
        const lvl = h[1].length;
        out.push(`<h${lvl}>${_extractInlines(h[2])}</h${lvl}>`);
        i++; continue;
      }

      // Tables — header | sep | rows
      if (/\|/.test(ln) && i + 1 < lines.length
          && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
        const tlines = [];
        while (i < lines.length && /\|/.test(lines[i])) {
          tlines.push(lines[i]); i++;
        }
        out.push(_renderTable(tlines));
        continue;
      }

      // Obsidian-style callouts (> [!type] Title / body)
      const cl = /^\s*>\s*\[!(\w+)\][-+]?\s*(.*)$/.exec(ln);
      if (cl) {
        const type = cl[1].toLowerCase();
        const title = (cl[2] || "").trim();
        const body = [];
        i++;
        while (i < lines.length && /^\s*>/.test(lines[i])
               && !/^\s*>\s*\[!/.test(lines[i])) {
          body.push(lines[i].replace(/^\s*>\s?/, ""));
          i++;
        }
        const icon = CALLOUT_ICONS[type] || "📌";
        const titleStr = (title || CALLOUT_DEFAULTS[type]
          || (type.charAt(0).toUpperCase() + type.slice(1))
        ).replace(/\*\*/g, "");
        out.push(
          `<div class="callout callout-${escHtml(type)}">`
          + `<div class="callout-title">${icon} ${escHtml(titleStr)}</div>`
          + (body.length
              ? `<div class="callout-body">${
                  _parseBlocks(body).join("")
                }</div>`
              : "")
          + `</div>`
        );
        continue;
      }

      // Plain blockquote (consume contiguous > lines)
      if (/^\s*>\s?/.test(ln)) {
        const bq = [];
        while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
          bq.push(lines[i].replace(/^\s*>\s?/, ""));
          i++;
        }
        out.push("<blockquote>" + _extractInlines(bq.join(" ")) + "</blockquote>");
        continue;
      }

      // Unordered list, including GitHub-style task items (- [ ] / - [x])
      if (/^\s*[-*]\s+/.test(ln)) {
        const ul = [];
        let hasTasks = false;
        while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
          const item = lines[i].replace(/^\s*[-*]\s+/, "");
          const task = /^\[([ xX])\]\s+(.*)$/.exec(item);
          if (task) {
            hasTasks = true;
            // The preview is a read-only view of the note, so the box is
            // rendered disabled: it has to look like a checkbox without
            // pretending to be a control the user can operate from here.
            // The label is wrapped in a span on purpose: the row is a flex
            // container, and the strike-through for a ticked task needs an
            // element to land on — bare text after the input would be
            // unreachable for both.
            ul.push(
              '<li class="task-item">'
              + `<input type="checkbox" disabled${task[1] === " " ? "" : " checked"}>`
              + `<span class="task-label">${_extractInlines(task[2])}</span>`
              + "</li>"
            );
          } else {
            ul.push("<li>" + _extractInlines(item) + "</li>");
          }
          i++;
        }
        out.push(
          `<ul${hasTasks ? ' class="task-list"' : ""}>` + ul.join("") + "</ul>"
        );
        continue;
      }

      // Ordered list
      if (/^\s*\d+\.\s+/.test(ln)) {
        const ol = [];
        while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
          ol.push("<li>" + _extractInlines(
            lines[i].replace(/^\s*\d+\.\s+/, "")
          ) + "</li>");
          i++;
        }
        out.push("<ol>" + ol.join("") + "</ol>");
        continue;
      }

      // Blank line → paragraph break
      if (/^\s*$/.test(ln)) { i++; continue; }

      // Paragraph: consume until blank/structural line
      const p = [ln]; i++;
      while (i < lines.length
             && !/^\s*$/.test(lines[i])
             && !/^\s*(#{1,6})\s+/.test(lines[i])
             && !/^\s*>\s?/.test(lines[i])
             && !/^\s*[-*]\s+/.test(lines[i])
             && !/^\s*\d+\.\s+/.test(lines[i])
             && !/^\s*-{3,}\s*$/.test(lines[i])
             && !/^\s*```/.test(lines[i])
             && !/\|/.test(lines[i])) {
        p.push(lines[i]); i++;
      }
      // Join with <br>, not " ". Joining with a space was CommonMark's
      // "soft break", which collapsed every single newline a user typed into
      // one long run-on line: a note holding 📚 subject / ⏰ due date / 📄 work
      // name on separate lines came out as one unreadable sentence. A newline
      // in the textarea is a line the user meant to keep.
      out.push("<p>" + p.map(_extractInlines).join("<br>") + "</p>");
    }
    return out;
  }

  // ── Table renderer ─────────────────────────────────────────────
  // Input: ["| h1 | h2 |", "| --- | --- |", "| c | c |", …]
  // Always HTML-escapes cell contents.
  function _renderTable(tlines) {
    if (tlines.length < 2) {
      return "<p>" + escHtml(tlines.join("\n")) + "</p>";
    }
    const split = (line) => line
      .replace(/^\s*\|/, "").replace(/\|\s*$/, "")
      .split("|").map((c) => c.trim());
    const head = split(tlines[0]);
    // tlines[1] is the separator row — skip
    const rows = tlines.slice(2).map(split);
    const thead = "<thead><tr>"
      + head.map((c) => `<th>${_extractInlines(c)}</th>`).join("")
      + "</tr></thead>";
    const tbody = "<tbody>"
      + rows.map((r) => "<tr>"
        + r.map((c) => `<td>${_extractInlines(c)}</td>`).join("")
        + "</tr>").join("")
      + "</tbody>";
    return `<table>${thead}${tbody}</table>`;
  }

  // ── _renderMd(text) → safe HTML ────────────────────────────────
  function _renderMd(text) {
    if (text == null || text === "") return "";
    const lines = String(text).replace(/\r\n?/g, "\n").split("\n");
    return _parseBlocks(lines).join("");
  }

  // ── renderContentPreview(container, html) ──────────────────────
  // Full HTML document → iframe with blob URL; snippet → Shadow DOM.
  //
  // XSS: html-sanitizer.js (loaded before this file, see index.html:403)
  // allowlists img/audio/video/iframe so embedded media actually renders,
  // and drops script/on* handlers. _stripScripts is only the fallback for
  // when that module failed to load — it removes <script> but leaves every
  // other tag, which is why media used to show up as escaped text.
  function _stripScripts(s) {
    return String(s).replace(
      /<\s*script\b[^>]*>[\s\S]*?<\s*\/\s*script\s*>/gi, ""
    ).replace(/<\s*script\b[^>]*\/?>/gi, "");
  }

  // Sanitise through the shared allowlist, falling back to _stripScripts.
  function _sanitize(s) {
    try {
      const fn = window.App && window.App.UI && window.App.UI.sanitizeHtml;
      if (typeof fn === "function") return fn(String(s));
    } catch (_) { /* fall through */ }
    return _stripScripts(s);
  }

  function renderContentPreview(container, html) {
    if (!container) return;
    if (/<html[\s>]/i.test(html) && /<body[\s>]/i.test(html)) {
      const clean = _sanitize(html);
      const blob = new Blob([clean], { type: "text/html" });
      const url = URL.createObjectURL(blob);
      const iframe = document.createElement("iframe");
      iframe.src = url;
      iframe.style.cssText = "width:100%;border:none;display:block;";
      container.innerHTML = "";
      container.appendChild(iframe);
      iframe.addEventListener("load", function () {
        try {
          const doc = iframe.contentDocument
            || iframe.contentWindow.document;
          const h = doc.documentElement.scrollHeight
            || doc.body.scrollHeight;
          iframe.style.height = h + "px";
        } catch (_) { /* opaque origin guard */ }
      });
      return;
    }
    const clean = _sanitize(html);
    const shadow = container.shadowRoot
      || container.attachShadow({ mode: "open" });
    shadow.innerHTML = "<style>"
      + "table{border-collapse:collapse;width:100%;margin:8px 0;font-size:13px;}"
      + "th,td{border:1px solid #3a4a6e;padding:6px 10px;text-align:left;}"
      + "th{background:#16213e;font-weight:600;}"
      + "tr:nth-child(even){background:#1f2a4a;}"
      + "</style>" + clean;
  }

  // ── renderInteractive(container, fragmentHtml) ─────────────────
  // Sandbox iframe for user-authored HTML+CSS+JS blocks.
  // Height auto-resize via postMessage (opaque-origin safe).
  const _frames = new Set();

  function _mountMessageListener() {
    if (window.__cbMsgMounted) return;
    window.__cbMsgMounted = true;
    window.addEventListener("message", function (ev) {
      const d = ev.data;
      if (!d || d.__it !== true || d.type !== "height") return;
      const iframe = document.querySelector(
        `.it-frame[data-run-id="${d.for}"]`
      );
      if (iframe) iframe.style.height = d.height + "px";
    });
  }

  function renderInteractive(container, fragmentHtml) {
    if (!container) return;
    _mountMessageListener();
    const runId = Date.now().toString(36)
      + Math.random().toString(36).slice(2, 7);
    const probe = "var _p=function(){var h=document.documentElement"
      + "?document.documentElement.scrollHeight:0;if(document.body&&"
      + "document.body.scrollHeight>h)h=document.body.scrollHeight;"
      + "parent.postMessage({__it:true,type:'height',for:"
      + JSON.stringify(runId) + ",height:h},'*');};"
      + "if(document.readyState==='complete'||document.readyState==='interactive'){setTimeout(_p,0);}"
      + "else{document.addEventListener('DOMContentLoaded',_p);}"
      + "setTimeout(_p,600);";
    const doc = `<span aria-hidden="true" style="position:fixed;width:0;height:0;overflow:hidden;"></span>`
      + `<script>${probe}<\/script>${fragmentHtml || ""}`;
    const iframe = document.createElement("iframe");
    iframe.setAttribute("sandbox", "allow-scripts");
    iframe.dataset.runId = runId;
    iframe.className = "it-frame";
    iframe.style.cssText = "width:100%;min-height:60px;border:1px solid"
      + " var(--border);border-radius:6px;background:var(--surface);"
      + "display:block;";
    iframe.srcdoc = doc;
    container.innerHTML = "";
    container.appendChild(iframe);
    _frames.add(iframe);
    return iframe;
  }

  // ── Public API ─────────────────────────────────────────────────
  return {
    _renderMd,
    renderContentPreview,
    renderInteractive,
    // Expose helpers for tests / future extensions
    _ytIdFromUrl,
  };
})();

console.log("[Studyflow] content-blocks.js loaded");