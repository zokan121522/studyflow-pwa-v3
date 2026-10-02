// ─── YouTube embed — click-to-play + floating PiP ──────────────────
// Namespace: window.App.YouTubeEmbed
//
// Consumes the .yt-card elements emitted by content-blocks.js renderer.link.
// Clicking a card swaps it for a youtube-nocookie iframe (click-to-load,
// R2/R4) with a control bar: ⧉ Flotar · ↗ YouTube · ✕. The Float button
// (or dragging the player) floats it as a fixed PiP on document.body (R5),
// with position/size persisted to localStorage (R8) so a reload restores a
// floating video.
//
// Event delegation uses composedPath() so clicks inside shadow-DOM block
// previews are captured too. All CSS for the card itself is inline (survives
// shadow boundaries); only the PiP chrome lives in youtube-embed.css.

window.App = window.App || {};
window.App.YouTubeEmbed = (function () {
  "use strict";

  const LS_KEY = "sf_youtube_pip";
  const CARD_SEL = ".yt-card";
  const PLAYER_SEL = ".yt-player";
  const RATIO = 9 / 16;

  // Punto de anclaje del último PiP (para restaurar inline), y caché local
  // de metadatos por URL (evita refetches en re-renders repetidos).
  let _active = null; // { pip, anchor }
  const _metaLoaded = new Set();

  // ── helpers ───────────────────────────────────────────────────────

  function _ytIdFromUrl(url) {
    const m = /[?&]v=([a-zA-Z0-9_-]{11})/.exec(url)
      || /youtu\.be\/([a-zA-Z0-9_-]{11})/.exec(url)
      || /youtube\.com\/(?:embed|shorts|live)\/([a-zA-Z0-9_-]{11})/.exec(url);
    return m ? m[1] : "";
  }

  function _fmtDur(secs) {
    const m = Math.floor(secs / 60);
    const s = secs % 60;
    return `${m}:${String(s).padStart(2, "0")}`;
  }

  // ── metadata (Opción A: backend /api/yt/meta, fallback silencioso) ─

  async function loadMeta(card) {
    const url = card.dataset.ytUrl;
    if (!url || _metaLoaded.has(url)) return;
    _metaLoaded.add(url);
    try {
      const res = await fetch(`/api/yt/meta?url=${encodeURIComponent(url)}`);
      if (!res.ok) return;
      const data = await res.json();
      if (!data || data.videoId !== (card.dataset.ytId || _ytIdFromUrl(url))) return;
      if (data.title) {
        const tt = card.querySelector(".yt-card-title");
        if (tt) tt.textContent = data.title;
      }
      if (data.duration) {
        const meta = card.querySelector(".yt-card-meta");
        if (meta) {
          meta.firstChild.textContent = `▶ YouTube · ${_fmtDur(data.duration)} · reproducir `;
        }
      }
    } catch (_) { /* fallback silencioso — la card ya es usable sin título real */ }
  }

  function _deferMeta(card) {
    if (typeof requestIdleCallback === "function") {
      requestIdleCallback(() => loadMeta(card), { timeout: 2000 });
    } else {
      setTimeout(() => loadMeta(card), 0);
    }
  }

  // ── play: click-to-load card → iframe player with control bar ─────

  const _BAR_BTN_STYLE =
    "border:0;background:rgba(255,255,255,.14);color:#fff;font-size:12px;" +
    "padding:4px 10px;border-radius:6px;cursor:pointer;text-decoration:none;" +
    "line-height:1.4;";

  function _buildBar(player, id) {
    const bar = document.createElement("div");
    bar.className = "yt-player-bar";
    bar.style.cssText =
      "display:flex;align-items:center;gap:6px;padding:6px 10px;background:#111;";

    const f = document.createElement("button");
    f.type = "button";
    f.className = "yt-bar-btn yt-float";
    f.textContent = "⧉ Flotar";
    f.title = "Poner el vídeo en modo flotante";
    f.style.cssText = _BAR_BTN_STYLE;
    f.addEventListener("click", () => float(player));

    const ext = document.createElement("a");
    ext.className = "yt-bar-ext";
    ext.href = `https://youtu.be/${id}`;
    ext.target = "_blank";
    ext.rel = "noopener noreferrer";
    ext.textContent = "↗ YouTube";
    ext.title = "Abrir en YouTube";
    ext.style.cssText = _BAR_BTN_STYLE;

    const x = document.createElement("button");
    x.type = "button";
    x.className = "yt-bar-btn yt-close";
    x.textContent = "✕";
    x.title = "Volver a la tarjeta";
    x.style.cssText = _BAR_BTN_STYLE;
    x.addEventListener("click", () => _restoreCard(player));

    bar.appendChild(f);
    bar.appendChild(ext);
    bar.appendChild(x);
    return bar;
  }

  function _buildPlayer(id, url, iframe, opt) {
    const holder = document.createElement("div");
    holder.className = "yt-player";
    holder.dataset.ytId = id;
    holder.dataset.ytUrl = url;
    if (opt?.cardHtml) holder.dataset.cardHtml = opt.cardHtml;
    holder.style.cssText =
      "margin:12px 0;border-radius:12px;overflow:hidden;background:#000;" +
      "box-shadow:0 2px 8px rgba(0,0,0,.12);";

    const wrap = document.createElement("div");
    wrap.style.cssText = "position:relative;width:100%;aspect-ratio:16/9;background:#000;";
    iframe.style.cssText = "position:absolute;inset:0;width:100%;height:100%;border:0;display:block;";
    wrap.appendChild(iframe);

    holder.appendChild(wrap);
    holder.appendChild(_buildBar(holder, id));
    return holder;
  }

  function _restoreCard(player) {
    const cardHtml = player.dataset.cardHtml;
    if (cardHtml) {
      const tmp = document.createElement("div");
      tmp.innerHTML = cardHtml;
      player.replaceWith(tmp.firstElementChild);
    } else {
      player.remove();
    }
  }

  function play(card) {
    const id = card.dataset.ytId || _ytIdFromUrl(card.dataset.ytUrl);
    if (!id) return;
    const iframe = document.createElement("iframe");
    iframe.src = `https://www.youtube-nocookie.com/embed/${id}?rel=0&modestbranding=1&autoplay=1`;
    iframe.allow = "autoplay; encrypted-media; picture-in-picture; fullscreen";
    iframe.allowFullscreen = true;
    const t = card.querySelector(".yt-card-title");
    iframe.title = (t && t.textContent) ? t.textContent : "Vídeo de YouTube";
    const holder = _buildPlayer(id, card.dataset.ytUrl, iframe, { cardHtml: card.outerHTML });
    card.replaceWith(holder);
  }

  // ── PiP: mover a document.body, arrastrar, redimensionar ──────────

  function float(el) {
    if (document.querySelector(".yt-pip")) return;
    const id = el.dataset.ytId || _ytIdFromUrl(el.dataset.ytUrl);
    if (!id) return;

    const anchor = document.createElement("div");
    anchor.className = "yt-pip-anchor";
    anchor.style.display = "none";
    el.replaceWith(anchor);

    const cardHtml = el.dataset.cardHtml
      || (el.matches && el.matches(CARD_SEL) ? el.outerHTML : "");
    const pip = _buildPip(id, el.dataset.ytUrl, el);
    if (cardHtml) pip.dataset.cardHtml = cardHtml;
    document.body.appendChild(pip);
    _active = { pip, anchor };
    _attachPipControllers(pip);
  }

  function _buildPip(id, url, sourceEl) {
    const saved = _loadState();
    const w = Math.min(Math.max(saved?.w || 400, 240), 720);
    const h = Math.round(w * RATIO);
    const x = Math.max(0, saved?.x ?? 20);
    const y = Math.max(0, saved?.y ?? 20);

    const pip = document.createElement("div");
    pip.className = "yt-pip";
    pip.dataset.ytId = id;
    pip.dataset.ytUrl = url;
    pip.style.cssText =
      `position:fixed;z-index:2147483000;left:${x}px;top:${y}px;width:${w}px;height:${h}px;` +
      "display:flex;flex-direction:column;background:#000;border-radius:12px;overflow:hidden;" +
      "box-shadow:0 8px 30px rgba(0,0,0,.4);";

    const bar = document.createElement("div");
    bar.className = "yt-pip-bar";
    bar.innerHTML =
      '<span style="font-size:13px;opacity:.85;">≡</span>' +
      '<span class="yt-pip-title">YouTube</span>' +
      '<button class="yt-pip-close" title="Volver al lugar original">✕</button>';

    const body = document.createElement("div");
    body.style.cssText = "flex:1;position:relative;min-height:0;background:#000;";

    // Reutiliza el iframe del player (no reinicia el vídeo), o crea uno
    // nuevo con autoplay cuando se flota desde la card sin reproducir.
    const existing = sourceEl ? sourceEl.querySelector("iframe") : null;
    const iframe = existing || document.createElement("iframe");
    if (!existing) {
      iframe.src = `https://www.youtube-nocookie.com/embed/${id}?rel=0&modestbranding=1&autoplay=1`;
      iframe.allow = "autoplay; encrypted-media; picture-in-picture; fullscreen";
      iframe.allowFullscreen = true;
    }
    iframe.style.cssText = "position:absolute;inset:0;width:100%;height:100%;border:0;display:block;";
    body.appendChild(iframe);

    const resize = document.createElement("div");
    resize.className = "yt-pip-resize";

    pip.appendChild(bar);
    pip.appendChild(body);
    pip.appendChild(resize);
    return pip;
  }

  function _attachPipControllers(pip) {
    const bar = pip.querySelector(".yt-pip-bar");
    const close = pip.querySelector(".yt-pip-close");
    const resize = pip.querySelector(".yt-pip-resize");

    bar.addEventListener("pointerdown", (e) => {
      if (e.target.closest(".yt-pip-close")) return;
      e.preventDefault();
      const startX = e.clientX, startY = e.clientY;
      const rect = pip.getBoundingClientRect();
      const move = (ev) => {
        pip.style.left = Math.max(0, rect.left + ev.clientX - startX) + "px";
        pip.style.top = Math.max(0, rect.top + ev.clientY - startY) + "px";
      };
      const up = () => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
        _saveState(pip);
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
    });

    close.addEventListener("click", () => restoreInline(pip));

    resize.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      e.stopPropagation();
      const startX = e.clientX, startY = e.clientY;
      const rect = pip.getBoundingClientRect();
      const move = (ev) => {
        const w = Math.min(Math.max(rect.width + ev.clientX - startX, 240), 720);
        pip.style.width = w + "px";
        pip.style.height = Math.round(w * RATIO) + "px";
      };
      const up = () => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
        _saveState(pip);
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
    });
  }

  function restoreInline(pip) {
    const anchor = _active?.anchor;
    if (!anchor || !anchor.isConnected) {
      pip.remove();
      _active = null;
      _saveState(null);
      return;
    }
    const iframe = pip.querySelector("iframe");
    const holder = _buildPlayer(pip.dataset.ytId, pip.dataset.ytUrl, iframe, {
      cardHtml: pip.dataset.cardHtml,
    });
    anchor.replaceWith(holder);
    pip.remove();
    _active = null;
    _saveState(null);
  }

  // ── persistencia (R8) ─────────────────────────────────────────────

  function _loadState() {
    try {
      return JSON.parse(localStorage.getItem(LS_KEY) || "null");
    } catch (_) {
      return null;
    }
  }

  function _saveState(pip) {
    try {
      if (!pip) {
        localStorage.removeItem(LS_KEY);
        return;
      }
      const rect = pip.getBoundingClientRect();
      localStorage.setItem(LS_KEY, JSON.stringify({
        videoId: pip.dataset.ytId,
        url: pip.dataset.ytUrl,
        x: Math.round(rect.left), y: Math.round(rect.top),
        w: Math.round(rect.width),
      }));
    } catch (_) { /* quota/serialization — non-fatal */ }
  }

  function _restorePip() {
    const saved = _loadState();
    if (!saved || !saved.videoId) return;
    const pip = _buildPip(saved.videoId, saved.url);
    document.body.appendChild(pip);
    _active = { pip, anchor: null };
    _attachPipControllers(pip);
  }

  // ── descubrimiento de cards (documento + shadow roots) ────────────

  const _observedRoots = new WeakSet();

  function _scanCards(root) {
    const cards = root.querySelectorAll ? root.querySelectorAll(CARD_SEL) : [];
    cards.forEach(_deferMeta);
  }

  function _observeRoot(root) {
    if (_observedRoots.has(root)) return;
    _observedRoots.add(root);
    const mo = new MutationObserver((muts) => {
      for (const m of muts) {
        for (const n of m.addedNodes) {
          if (n.nodeType !== 1) continue;
          if (n.matches && n.matches(CARD_SEL)) _deferMeta(n);
          if (n.querySelectorAll) n.querySelectorAll(CARD_SEL).forEach(_deferMeta);
        }
      }
    });
    mo.observe(root, { childList: true, subtree: true });
  }

  function _scanDeep(root) {
    _observeRoot(root);
    _scanCards(root);
    const hosts = root.querySelectorAll ? root.querySelectorAll("*") : [];
    hosts.forEach((h) => {
      if (h.shadowRoot) _scanDeep(h.shadowRoot);
    });
  }

  // ── API ───────────────────────────────────────────────────────────

  function init() {
    // Delegación de clicks: composedPath() cruza shadow roots (R4)
    document.addEventListener("click", (e) => {
      const path = e.composedPath ? e.composedPath() : [];
      for (const el of path) {
        if (!el || typeof el.matches !== "function") continue;
        if (el.matches && el.matches(".yt-ext")) return; // dejar abrir en pestaña
        if (el.matches && el.matches(".yt-float-card")) {
          e.preventDefault();
          const card = el.closest ? el.closest(CARD_SEL) : null;
          if (card) float(card);
          return;
        }
        if (el.matches && el.matches(CARD_SEL)) {
          e.preventDefault();
          play(el);
          return;
        }
      }
    });

    // Teclado: Enter/Espacio sobre card o chip flotar → accion (R7)
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Enter" && e.key !== " ") return;
      const t = e.target;
      if (!t || !t.matches) return;
      if (t.matches(".yt-float-card")) {
        e.preventDefault();
        const card = t.closest ? t.closest(CARD_SEL) : null;
        if (card) float(card);
        return;
      }
      if (t.matches(CARD_SEL)) {
        e.preventDefault();
        play(t);
      }
    });

    // Scan inicial + barrido periódico para cards dentro de shadow roots
    _scanDeep(document);
    setInterval(() => _scanDeep(document), 1500);

    _restorePip();
  }

  // arranque
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  return { init, play, float, restoreInline, loadMeta, scan: _scanDeep };
})();