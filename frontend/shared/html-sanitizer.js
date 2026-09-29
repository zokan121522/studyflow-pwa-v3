/* ============================== HTML SANITIZER ============================== */
// window.App.UI.sanitizeHtml(html) → safe HTML string.
//
// Issue #9 — v2 bloques `content` embeben HTML (infografía <img>, audio
// <audio>, HTML de NotebookLM). v3 debe renderizarlo SIN ejecutar scripts.
// This module is a dependency-free allowlist sanitizer:
//   • parses with DOMParser (no regex-on-raw-html)
//   • walks elements, keeps only allowlisted tags/attributes
//   • removes event handlers (on*), javascript:/data: URLs, <script>,
//     <style> tags, forms, objects, embeds
//   • iframes restricted to known-safe embeds (YouTube, Vimeo) + /api/
//   • inline style whitelist (subset of CSS properties)

(function () {
  "use strict";

  // ── Policy ───────────────────────────────────────────────────────
  // Tags kept as-is. Everything else is either unwrapped (children
  // promoted) or removed entirely (dangerous tags).
  const ALLOWED_TAGS = new Set([
    "p", "br", "strong", "em", "b", "i", "u", "s",
    "ul", "ol", "li", "blockquote", "h1", "h2", "h3",
    "h4", "h5", "h6",
    "table", "thead", "tbody", "tr", "th", "td",
    "span", "div", "figure", "figcaption", "hr",
    "code", "pre",
    "a", "img", "audio", "video", "source",
    "iframe",
  ]);

  // Tags nuked wholesale (node + children removed).
  const REMOVE_TAGS = new Set([
    "script", "style", "link", "meta", "base", "title",
    "object", "embed", "form", "input", "button",
    "textarea", "select", "option", "label", "template",
    "svg", "math", "noscript", "iframe" /* iframe handled specially below */,
  ]);

  // Attributes allowed on any element.
  const COMMON_ATTRS = new Set([
    "title", "alt", "width", "height",
    "loading", "playsinline", "poster", "controls",
    "colspan", "rowspan", "border", "cellpadding",
    "cellspacing", "align", "dir", "lang",
    "style", // handled specially below (CSS property whitelist)
  ]);

  // Per-tag extra attributes.
  const TAG_ATTRS = {
    a: ["href", "target", "rel"],
    img: ["src"],
    audio: ["src", "autoplay", "loop", "preload"],
    video: ["src", "loop", "preload", "muted"],
    source: ["src", "type"],
    iframe: ["src", "allow", "allowfullscreen", "sandbox", "referrerpolicy", "frameborder", "scrolling", "class"],
  };

  // Inline style properties allowed (defense-in-depth: never allow
  // url()/expression()/position tricks; positional/overlay props
  // (position/fixed/float/z-index) are excluded so embedded content
  // cannot break out of the block layout).
  const STYLE_WHITELIST = /^(max-width|width|height|min-width|min-height|max-height|border-radius|display|margin|margin-top|margin-right|margin-bottom|margin-left|padding|padding-top|padding-right|padding-bottom|padding-left|color|background|background-color|text-align|font-size|font-weight|font-style|line-height|vertical-align|border|border-top|border-right|border-bottom|border-left|box-shadow|text-decoration|opacity|overflow|flex|flex-direction|justify-content|align-items|gap|object-fit|object-position|white-space|word-break|overflow-wrap)*$/i;

  const SAFE_PROTOCOLS = /^(https?:|mailto:|tel:|#)/i;
  const RELATIVE_URL = /^\/[^/]/; // /api/... app-relative
  const DATA_IMAGE = /^data:image\/(png|jpe?g|gif|webp|svg\+xml);/i;

  // iframe embeds allowed: YouTube / Vimeo / app-relative.
  const IFRAME_SAFE = [
    /^https:\/\/www\.youtube\.com\/embed\//i,
    /^https:\/\/www\.youtube-nocookie\.com\/embed\//i,
    /^https:\/\/player\.vimeo\.com\//i,
  ];

  // ── URL guard helpers ────────────────────────────────────────────
  function _isSafeUrl(value) {
    if (!value) return true; // e.g. <img> without src → harmless
    const v = value.trim();
    if (RELATIVE_URL.test(v)) return true;
    if (SAFE_PROTOCOLS.test(v)) return true;
    if (DATA_IMAGE.test(v)) return true;
    return false;
  }

  function _isSafeIframeSrc(value) {
    if (!value) return false;
    const v = value.trim();
    if (RELATIVE_URL.test(v)) return true;
    return IFRAME_SAFE.some((re) => re.test(v));
  }

  function _sanitizeStyle(cssText) {
    if (!cssText) return "";
    const out = [];
    for (const decl of String(cssText).split(";")) {
      const idx = decl.indexOf(":");
      if (idx === -1) continue;
      const prop = decl.slice(0, idx).trim().toLowerCase();
      const val = decl.slice(idx + 1).trim();
      if (!prop || !val) continue;
      if (!STYLE_WHITELIST.test(prop)) continue;
      if (/url\(/i.test(val) || /expression\s*\(/i.test(val) || /javascript:/i.test(val)) continue;
      out.push(`${prop}:${val}`);
    }
    return out.join(";");
  }

  // ── Main sanitizer ───────────────────────────────────────────────
  function sanitizeHtml(html) {
    if (!html) return "";
    const doc = new DOMParser().parseFromString(
      String(html), "text/html"
    );
    // Collect elements up-front (we mutate the tree while walking).
    const elements = [];
    const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_ELEMENT);
    while (walker.nextNode()) elements.push(walker.currentNode);

    for (const el of elements) {
      if (!el.parentNode) continue; // already removed
      const tag = el.tagName.toLowerCase();

      // 1) iframes: only known-safe embeds, else remove entirely.
      if (tag === "iframe") {
        const src = el.getAttribute("src") || "";
        if (!_isSafeIframeSrc(src)) {
          el.remove();
          continue;
        }
        el.setAttribute("sandbox", el.getAttribute("sandbox")
          || "allow-scripts allow-same-origin allow-presentation");
        el.setAttribute("loading", "lazy");
        _keepAttrs(el, TAG_ATTRS.iframe);
        continue;
      }

      // 2) Dangerous tags → remove node + children.
      if (REMOVE_TAGS.has(tag)) {
        el.remove();
        continue;
      }

      // 3) Unknown/not-allowed tags → unwrap (promote children).
      if (!ALLOWED_TAGS.has(tag)) {
        while (el.firstChild) el.parentNode.insertBefore(el.firstChild, el);
        el.remove();
        continue;
      }

      // 4) Allowed tag → filter attributes.
      _keepAttrs(el, TAG_ATTRS[tag]);
    }
    return doc.body.innerHTML;
  }

  // Filters an element's attributes to allowed + safe values only.
  function _keepAttrs(el, tagAttrs) {
    const allowed = new Set(COMMON_ATTRS);
    if (tagAttrs) for (const a of tagAttrs) allowed.add(a);

    const attrs = Array.from(el.attributes);
    for (const attr of attrs) {
      const name = attr.name.toLowerCase();

      // Event handlers / anything with a URL-triggering name.
      if (name.startsWith("on")) { el.removeAttribute(attr.name); continue; }

      // Whitelist check.
      if (!allowed.has(name)) { el.removeAttribute(attr.name); continue; }

      // URL-bearing attributes.
      if (name === "src" || name === "href") {
        if (name === "src" && el.tagName.toLowerCase() === "iframe") {
          if (!_isSafeIframeSrc(attr.value)) { el.removeAttribute(attr.name); continue; }
        } else if (!_isSafeUrl(attr.value)) {
          el.removeAttribute(attr.name); continue;
        }
      }

      // Deny javascript: anywhere it could slip.
      if (/javascript:/i.test(attr.value)) { el.removeAttribute(attr.name); continue; }

      // Inline style → whitelist of CSS properties.
      if (name === "style") {
        const clean = _sanitizeStyle(attr.value);
        if (clean) el.setAttribute("style", clean);
        else el.removeAttribute("style");
      }
    }
  }

  // Export.
  window.App = window.App || {};
  window.App.UI = window.App.UI || {};
  window.App.UI.sanitizeHtml = sanitizeHtml;
})();

console.log("[Shared] html-sanitizer.js loaded");