#!/usr/bin/env python3
"""Walkthrough narrado: TTS (edge-tts) + Playwright (Chrome) + ffmpeg.

Sincronización determinista: la voz se genera PRIMERO y se mide; el vídeo se
conduce en tiempo real anclando cada acción a la palabra exacta (WordBoundary).
El montaje coloca cada audio en su offset y lo mezcla con el vídeo.

redact_text (storyboard field): lista de regex (case-insensitive) que difuminan
el texto dinámico que un selector no puede cubrir. Se valida TODO al inicio
(fail-fast) y se aplica antes del primer frame grabado.

redact_dynamic (storyboard field): lista de fuentes cuya respuesta se consulta
al arranque para registrar SUS valores como patrones redact_text extra
(re.escape'd y case-insensitive). "courses" lee /api/courses y /api/auth/me:
cada título de curso/tema + nombre/email expuestos se añade a los patrones,
de forma que un título real que se cuele por un endpoint sin mock no salga
legible en el vídeo. Fail-soft: si un endpoint falla, se avisa y se sigue.

Modo de autotesteo: `python3 render.py --selftest-redact` abre la app, inyecta
el patrón /dashboard/i con la misma lógica de blur, guarda
/tmp/redact_selftest.png, imprime el nº de elementos difuminados y cierra.
No genera TTS ni vídeo — sirve para verificar redact_text en segundos.

Tokens en modo real (--real): los storyboards no llevan ids hardcodeados sino
${COURSE_ID}, ${TOPIC_ID}, ${BLOCK_MD_ID} (alias ${BLOCK_ID}) y ${PDF_COURSE_ID},
${PDF_TOPIC_ID}, ${PDF_BLOCK_ID} (alias ${PDF_BLOCK2_ID}). resolve_real_ids()
los traduce contra el primer curso/tema/bloque real de /api/courses justo antes
de grabar; si falla, los selectores se dejan intactos (fail-soft).
"""
import asyncio, json, os, re, shutil, subprocess, sys, time, unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fixtures

HERE = os.path.dirname(os.path.abspath(__file__))
_SELFTEST_REDACT = "--selftest-redact" in sys.argv[1:]
STORY = next((a for a in sys.argv[1:] if not a.startswith("--")),
             os.path.join(HERE, "storyboards", "agenda.json"))
_SLUG = os.path.splitext(os.path.basename(STORY))[0]
OUT = os.path.join(HERE, "pilot", _SLUG)
FRAMES = os.path.join(OUT, "audio")
VIDDIR = os.path.join(OUT, "video")
CURSOR_SVG = ("<svg xmlns='http://www.w3.org/2000/svg' width='30' height='30'>"
              "<path d='M5 2 L5 23 L10.5 18 L15 27.5 L19 25.5 L14.5 16.5 L22 16.5 Z' "
              "fill='white' stroke='%23111' stroke-width='1.6' stroke-linejoin='round'/></svg>")


def norm(s):
    s = unicodedata.normalize("NFD", s.lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("  ! cmd falló:", " ".join(cmd[:4]), "…\n    ", r.stderr.strip()[:400])
    return r


def duration(path):
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path])
    return float(r.stdout.strip())


def tts(path, text, voice, rate):
    async def go():
        from edge_tts import Communicate
        c = Communicate(text, voice, rate=rate, boundary="WordBoundary")
        words = []
        with open(path, "wb") as f:
            async for ch in c.stream():
                if ch["type"] == "audio":
                    f.write(ch["data"])
                elif ch["type"] == "WordBoundary":
                    words.append((ch["text"], ch["offset"] / 1e7))
        return words
    return asyncio.run(go())


def find_anchor(words, anchor):
    """Segundos en los que empieza la palabra/frase ancla dentro del audio."""
    if not anchor:
        return 0.0
    toks = [norm(t) for t in re.split(r"\s+", anchor.strip()) if t]
    nw = [norm(w) for w, _ in words]
    for i in range(len(words) - len(toks) + 1):
        if nw[i:i + len(toks)] == toks:
            return words[i][1]
    for w, t in words:                       # fallback: palabra suelta
        if toks and (toks[0] in norm(w) or norm(w) in toks[0]):
            return t
    return 0.0


# ---------------------------------------------------------------- pre-pass TTS
def pre_pass(story):
    os.makedirs(FRAMES, exist_ok=True)
    voice, rate = story["voice"], story["rate"]
    frames = []
    for i, cue in enumerate(story["cues"]):
        mp3 = os.path.join(FRAMES, f"cue_{i:02d}.mp3")
        cache = os.path.join(FRAMES, f"cue_{i:02d}.json")
        key = f"{voice}|{rate}|{cue['say']}"
        words = None
        if os.path.exists(mp3) and os.path.exists(cache):
            try:
                blob = json.load(open(cache))
                if blob.get("key") == key:
                    words = [tuple(w) for w in blob["words"]]
            except Exception:
                words = None
        if words is None:
            words = tts(mp3, cue["say"], voice, rate)
            json.dump({"key": key, "words": [list(w) for w in words]}, open(cache, "w"))
        d = duration(mp3)
        a = find_anchor(words, cue.get("anchor"))
        frames.append({"i": i, "mp3": mp3, "dur": d, "anchor": a, "cue": cue})
        print(f"  cue {i:02d}  dur={d:5.2f}s  ancla@{a:5.2f}s  «{cue['say'][:44]}…»")
    return frames


def build_timeline(story, frames):
    cur = float(story["leadin"])
    gap = story["gap"]
    for fr in frames:
        fr["start"] = cur
        # gap por cue (opcional): da aire tras un clic que dispara carga lenta
        fr["seg"] = fr["dur"] + fr["cue"].get("gap", gap)
        fr["anchor_t"] = cur + fr["anchor"]
        cur += fr["seg"]
    total = cur + story["tail"]
    return total


# ------------------------------------------------------------------- redacted
# El árbol lateral de cursos y los títulos de tema/bloque son UI permanente:
# salen en cualquier toma hecha dentro de la app, aunque el guion no llegue a
# tocarlos. Por eso esta lista vive AQUÍ y no en cada storyboard — si depende
# de que quien grabe se acuerde, el próximo vídeo se lleva por delante la
# referencia de una asignatura entera.
#
# Un selector que no casa con nada no hace nada, así que se puede ser
# exhaustivo sin arriesgarse a romper la grabación. Lo específico de una vista
# (el canvas del PDF, los enunciados del quiz, el editor) sigue declarándose
# en su storyboard con `redact`.
GLOBAL_REDACT = [
    # Studyflow — nombres de curso, de tema y de bloque
    ".course-item",
    ".ci-title",
    ".course-group-title",
    ".topic-item",
    ".sf-cl-card-title",
    ".bi-title",
    ".sf-td-md-plain",
    ".sf-td-title",
    # Agenda — sesión, hábitos, notas y URL del calendario
    ".sc-title",
    ".d-name",
    ".ht-label",
    ".hn-title",
    ".hn-row-label",
    ".hab-text",
    "#quick-note",
    ".cal-url",
    ".tl-block-title",
    ".tlw-block-title",
]

# ------------------------------------------------------------------- injected
INJECT_CSS = f"""
#sfCursor{{position:fixed;left:0;top:0;width:30px;height:30px;z-index:2147483647;
  opacity:0;pointer-events:none;transition:transform .38s cubic-bezier(.22,.61,.36,1),opacity .2s;
  background:no-repeat center/contain url("data:image/svg+xml;utf8,{CURSOR_SVG}");}}
.sf-ripple{{position:fixed;width:16px;height:16px;margin:-8px 0 0 -8px;border-radius:50%;
  border:2.5px solid #4f8cff;z-index:2147483646;pointer-events:none;
  animation:sfRip .7s ease-out forwards;}}
@keyframes sfRip{{from{{opacity:.95;transform:scale(.35)}}to{{opacity:0;transform:scale(3.2)}}}}
.sf-focus{{outline:3px solid #4f8cff!important;outline-offset:3px!important;border-radius:9px;
  box-shadow:0 0 0 7px rgba(79,140,255,.22)!important;transition:box-shadow .2s;}}
.sf-rt-blur{{filter:blur(7px)!important;-webkit-filter:blur(7px)!important;}}
"""

INJECT_JS = """(() => {
let cur = document.getElementById('sfCursor');
if (!cur) { cur = document.createElement('div'); cur.id = 'sfCursor'; document.body.appendChild(cur); }
window.__sf = {
  move(x,y){const c=document.getElementById('sfCursor'); c.style.opacity='1'; c.style.transform=`translate(${x-6}px,${y-6}px)`;},
  ripple(x,y){const r=document.createElement('div');r.className='sf-ripple';
    r.style.left=x+'px';r.style.top=y+'px';document.body.appendChild(r);setTimeout(()=>r.remove(),720);},
  focus(el){document.querySelectorAll('.sf-focus').forEach(e=>e.classList.remove('sf-focus'));
    if(!el)return;
    el.scrollIntoView({block:'nearest',inline:'nearest'});
    el.classList.add('sf-focus');},
  focusSel(sel){this.focus(document.querySelector(sel));},
  center(sel){const e=document.querySelector(sel); if(!e)return null;
    const r=e.getBoundingClientRect(); return {x:r.left+r.width/2,y:r.top+r.height/2};},
  applyRedactText:function(patterns){
    // loop guard: el MutationObserver puede reentrar mientras se pulsa el DOM
    if(this._rtBusy){ return this._rtCount||0; }
    this._rtBusy=true;
    try {
      if(!patterns||patterns.length===0){ this._rtCount=this._rtTotal(); return this._rtCount; }
      let focusEl=null;
      const ff=document.querySelector('.sf-focus');
      if(ff) focusEl=ff;
      const activeSel=this._lastActiveSelector||null;
      if(!focusEl&&activeSel){ try{focusEl=document.querySelector(activeSel);}catch(e){} }
      const rxList=[];
      let invalid=0;
      for(let i=0;i<patterns.length;i++){ try{rxList.push(new RegExp(patterns[i],'i'));}catch(e){invalid++;} }
      if(invalid){ this._rtCount=0; return 0; }
      const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT,{acceptNode:(n)=>{const v=n.nodeValue||''; if(v.trim().length===0)return NodeFilter.FILTER_SKIP; return NodeFilter.FILTER_ACCEPT;}});
      const nodes=[];
      while(walker.nextNode()){ nodes.push(walker.currentNode); }
      for(let j=0;j<nodes.length;j++){
        const n=nodes[j];
        let matched=false;
        for(let k=0;k<rxList.length;k++){ if(rxList[k].test(n.nodeValue)){matched=true;break;} }
        if(!matched) continue;
        let target=null;
        let cur=n.parentElement;
        while(cur&&cur.tagName!=='HTML'&&cur.tagName!=='BODY'){
          let hasElem=false;
          for(let c=0;c<cur.childNodes.length;c++){ if(cur.childNodes[c].nodeType===Node.ELEMENT_NODE){hasElem=true;break;} }
          if(!hasElem){ target=cur; break; }
          cur=cur.parentElement;
        }
        if(!target) target=n.parentElement;
        if(!target||target.tagName==='BODY'||target.tagName==='HTML') continue;
        // nunca difuminar el subárbol del focus / selector activo del cue
        if(focusEl&&(target===focusEl||target.contains(focusEl))) continue;
        target.classList.add('sf-rt-blur');
      }
      this._rtCount=this._rtTotal(); return this._rtCount;
    }catch(e){ this._rtCount=0; return 0; }
    finally{ this._rtBusy=false; }
  },
  _rtTotal(){ return document.querySelectorAll('.sf-rt-blur').length; }
};

window.__sf._rtPatterns = [];
window.__sf.setRtPatterns = function(list){ window.__sf._rtPatterns = list||[]; };
window.__sf.setLastActiveSelector = function(s){ window.__sf._lastActiveSelector=s; };
try { const mo=new MutationObserver(()=>{window.__sf.applyRedactText(window.__sf._rtPatterns);}); mo.observe(document.documentElement||document.body,{childList:true,subtree:true,characterData:true}); window.__sf._rtObserver=mo; } catch(e){}

})();"""


def compile_rt_patterns(story):
    """Valida TODOS los patrones de `redact_text` con re.I — fail-fast.

    Se llama al inicio de main() (antes del pre-pass TTS) para que un guion
    mal escrito muera en segundos, no tras minutos de locución.

    Acepta tanto `dashboard` como la notación literal `/dashboard/i` (como se
    escribe en un test): los delimitadores y flags se descartan — siempre se
    compila con IGNORECASE, que es lo que exige la especificación.
    """
    pats = []
    for pat in (story.get("redact_text") or []):
        src = pat
        m = re.fullmatch(r"/(.+)/[a-z]*", pat)
        if m:
            src = m.group(1)
        try:
            re.compile(src, re.IGNORECASE)
        except re.error as e:
            raise RuntimeError(f"invalid redact_text pattern '{pat}': {e}")
        pats.append(src)
    return pats


# ------------------------------------------------------------------- dynamic
# Fuentes para `redact_dynamic`: cada entrada es (ruta, claves) y todas las
# cadenas encontradas bajo esas claves (recursivo) se registran como patrones
# redact_text. "courses" también lee /api/auth/me para capturar nombre/email
# del usuario si algún endpoint sin mock los expusiera en pantalla.
_DYNAMIC_SOURCES = {
    "courses": [
        ("/api/courses", ("title", "name", "email")),
        ("/api/auth/me", ("name", "email")),
    ],
}


def _walk_strings(obj, keys, acc):
    """Recoge en acc los strings del JSON bajo claves de interés (recursivo)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in keys and isinstance(v, str):
                acc.add(v.strip())
            _walk_strings(v, keys, acc)
    elif isinstance(obj, list):
        for item in obj:
            _walk_strings(item, keys, acc)


def collect_dynamic_redact(story):
    """Registra los valores reales de las fuentes `redact_dynamic` como
    patrones redact_text (re.escape + IGNORECASE, deduplicados contra los
    estáticos). Fail-soft: un endpoint caído nunca aborta la grabación.

    Se llama al inicio de main(), antes del fail-fast de compile_rt_patterns:
    los patrones dinámicos ya van escapados por re.escape, así que la
    validación los admite igual que los estáticos.
    """
    import urllib.request
    base = story["base_url"].rstrip("/")
    for src in (story.get("redact_dynamic") or []):
        specs = _DYNAMIC_SOURCES.get(src)
        if not specs:
            print(f"  ! redact_dynamic: fuente desconocida '{src}', se ignora")
            continue
        for path, keys in specs:
            try:
                body = json.load(urllib.request.urlopen(
                    f"{base}{path}", timeout=10))
            except Exception as e:
                print(f"  ! redact_dynamic: aviso al leer {path}: {e}")
                continue
            found = set()
            _walk_strings(body, keys, found)
            static = set(story.get("redact_text") or [])
            added = 0
            for s in sorted(found):
                if len(s) < 3:
                    continue
                pat = re.escape(s)
                if pat not in static:
                    story.setdefault("redact_text", []).append(pat)
                    static.add(pat)
                    added += 1
            print(f"  [redact_dynamic] {src} ({path}): "
                  f"{added} patrón(es) nuevo(s)")


# ------------------------------------------------------------------- ids reales
# En modo real los storyboards escriben ${TOKENS} en vez de ids de mentira.
# resolve_real_ids() traduce los tokens contra /api/courses y guarda el mapa
# en story["_idmap"]; _sub_ids() lo aplica a cada cue justo antes de ejecutarse.
_MD_TYPES = ("markdown", "text")
_PDF_TYPES = ("pdf", "pdf-ref")


def _first_block(topic, types=()):
    for b in (topic.get("blocks") or []):
        if not types or (b.get("type") or "").lower() in types:
            return b
    return None


def resolve_real_ids(story):
    """Tokens reales: primer curso, primer tema con bloques, primer pdf.

    Fail-soft: si /api/courses falla o no hay datos utilizables, se avisa y
    los selectores se graban con los tokens sin sustituir (fallarán al hacer
    click, pero el render no aborta por culpa del resolver).
    """
    if not story.get("real_mode"):
        return
    import urllib.request
    base = story["base_url"].rstrip("/")
    try:
        data = json.load(urllib.request.urlopen(f"{base}/api/courses", timeout=30))
        courses = data.get("courses") or []
    except Exception as e:
        print(f"  ! [ids] no pude leer {base}/api/courses ({e}); "
              "selectores sin sustituir")
        return

    def has_blocks(t):
        return any(b for b in (t.get("blocks") or []))

    pair = None
    for c in courses:
        for t in (c.get("topics") or []):
            if has_blocks(t):
                pair = (c, t)
                break
        if pair:
            break
    if not pair:
        print("  ! [ids] /api/courses no trae cursos con temas con bloques; "
              "selectores sin sustituir")
        return
    course, topic = pair
    bm = _first_block(topic, _MD_TYPES) or _first_block(topic)
    ids = {
        "${COURSE_ID}": str(course["id"]),
        "${TOPIC_ID}": str(topic["id"]),
        "${BLOCK_MD_ID}": str(bm["id"]),
        "${BLOCK_ID}": str(bm["id"]),
    }
    pdf_pair = next(((c, t) for c in courses for t in (c.get("topics") or [])
                     if _first_block(t, _PDF_TYPES)), None)
    if pdf_pair:
        pc, pt = pdf_pair
        pb = _first_block(pt, _PDF_TYPES)
        others = [b for b in (pt.get("blocks") or []) if b["id"] != pb["id"]]
        pb2 = next((b for b in others if (b.get("type") or "").lower() in _PDF_TYPES),
                   None) or (others[0] if others else None) or pb
        ids.update({
            "${PDF_COURSE_ID}": str(pc["id"]),
            "${PDF_TOPIC_ID}": str(pt["id"]),
            "${PDF_BLOCK_ID}": str(pb["id"]),
            "${PDF_BLOCK2_ID}": str(pb2["id"]),
        })
    else:
        print("  ! [ids] no hay bloques pdf en /api/courses; ${PDF_*} sin sustituir")
    story["_idmap"] = ids
    print("  [ids] " + " ".join(
        f"{k.strip('${}')}={v}" for k, v in ids.items()))


def _sub_ids(node, idmap):
    """Sustituye ${TOKENS} en cualquier string del cue (selectores, focus…)."""
    if not idmap:
        return node
    if isinstance(node, str):
        out = node
        for k, v in idmap.items():
            out = out.replace(k, v)
        return out
    if isinstance(node, list):
        return [_sub_ids(x, idmap) for x in node]
    if isinstance(node, dict):
        return {k: _sub_ids(v, idmap) for k, v in node.items()}
    return node


def do_action(pg, cue, anchor_t, click_lead):
    """Mueve el cursor y ejecuta la acción anclada a la palabra."""
    act = cue["action"]
    if not act:
        if cue.get("focus"):
            pg.evaluate("s => window.__sf.focusSel(s)", cue["focus"])
        return None
    sel = act["selector"]
    c = pg.evaluate("s => window.__sf.center(s)", sel)
    # mover cursor ~click_lead antes de la palabra
    if c:
        move_at = anchor_t - click_lead
        sleep_until(move_at)
        pg.evaluate("p => window.__sf.move(p.x, p.y)", c)
    # disparar justo en la palabra
    sleep_until(anchor_t - 0.05)
    kind = act["kind"]
    if c:
        pg.evaluate("p => window.__sf.ripple(p.x, p.y)", c)
    if act.get("pre"):
        pg.click(act["pre"], timeout=4000)
    if kind == "click":
        pg.click(sel, timeout=4000)
    elif kind == "fill":
        pg.click(sel, timeout=4000)
        pg.fill(sel, "")
        pg.locator(sel).press_sequentially(act["value"], delay=28)
    elif kind == "fill_pair":
        for s, v in ((act["selector"], act["value"]), (act["selector2"], act["value2"])):
            pg.click(s, timeout=4000); pg.fill(s, v)
    elif kind == "select":
        pg.select_option(sel, index=act.get("index", 1))
    if cue.get("focus") or c:
        pg.evaluate("s => window.__sf.focusSel(s)", cue.get("focus") or sel)
    return time.time()


T0 = 0.0
def sleep_until(rel_seconds):
    while True:
        rem = rel_seconds - (time.time() - T0)
        if rem <= 0:
            return
        time.sleep(min(rem, 0.05))


def record(story, frames, total):
    global T0
    from playwright.sync_api import sync_playwright
    os.makedirs(VIDDIR, exist_ok=True)
    w, h = story["viewport"]
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        # ¿El guion documenta el dashboard o la landing? Se calcula aquí porque
        # lo comparten el warmup opcional y la espera de la grabación.
        is_dashboard = story["base_url"].rstrip("/") in ("", "http://127.0.0.1:8081")
        # warmup (opcional, solo si el storyboard lo pide): una carga previa en
        # una página desechable del mismo navegador ANTES de crear el contexto
        # con record_video_dir. La primera carga (red, API, parseo) queda fuera
        # del vídeo grabado, así el arranque no sale con spinner ni muerte.
        if story.get("warmup"):
            t_w = time.time()
            wp = b.new_page()
            try:
                wp.goto(story["base_url"], wait_until="domcontentloaded")
                wp.wait_for_load_state("networkidle", timeout=15000)
                wp.wait_for_selector(
                    ".dashboard-nav-grid" if is_dashboard else "body",
                    state="visible", timeout=15000 if is_dashboard else 5000)
            except Exception:
                pass
            wp.close()
            print(f"  [warmup] preload {int((time.time() - t_w) * 1000)}ms")
        ctx = b.new_context(viewport={"width": w, "height": h},
                            record_video_dir=VIDDIR,
                            record_video_size={"width": w, "height": h},
                            service_workers="block",
                            device_scale_factor=2)
        pg = ctx.new_page()
        # Sustituye los datos personales por los de mentira ANTES de que
        # salga cualquier request a la red. Va antes del goto a propósito:
        # si espera, la primera respuesta ya habría enviado los datos reales
        # y el primer render los habría pintado. No se tocan:
        # /api/health y los estáticos, que un None deja pasar tal cual.
        def _interceptar(route):
            # Las anotaciones del PDF son un endpoint aparte que cuelga del
            # mismo id; sin esto, /api/pdf/201/annotations se va a la red real.
            if re.search(r"/api/pdf/\d+/annotations", route.request.url):
                route.fulfill(status=200, content_type="application/json",
                              body="[]")
                return
            if re.search(r"/api/pdf/\d+", route.request.url):
                route.fulfill(status=200, content_type="application/pdf",
                              body=fixtures.PDF_DEMO.read_bytes())
                return
            falso = fixtures.respuesta(route.request.url)
            if falso is None:
                route.continue_()
            else:
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(falso, ensure_ascii=False))

        # --real: passthrough total al backend real, sin interceptor (ni los
        # casos especiales del PDF). Solo se registra en modo fixtures.
        if not story.get("real_mode"):
            pg.route("**/api/**", _interceptar)
        pg.goto(story["base_url"], wait_until="domcontentloaded")
        # The dashboard grid is the app's ready signal, but a storyboard that
        # documents the landing page never renders it: waiting the full 15s
        # there is dead time that lands as 15s of silence at the head of the
        # finished video. Anything not rooted at the app waits on <body>.
        # (is_dashboard se calcula al inicio de record(), con el warmup.)
        try:
            pg.wait_for_selector(
                ".dashboard-nav-grid" if is_dashboard else "body",
                state="visible", timeout=15000 if is_dashboard else 5000)
        except Exception:
            pass
        pg.wait_for_timeout(400)
        pg.add_style_tag(content=INJECT_CSS)
        # Blurring es opcional: si el storyboard no define ninguna clave
        # redact, NO se inyecta el CSS GLOBAL_REDACT ni se aplica nada.
        redaction_on = bool(story.get("redact") or story.get("redact_text")
                            or story.get("redact_dynamic")
                            or story.get("redact_blur_px"))
        redact = (dict.fromkeys(GLOBAL_REDACT + (story.get("redact") or []))
                  if redaction_on else {})
        if redact:
            # Radius per storyboard. 7px is enough for the short strings the
            # first two videos redact (an email in an input box). It is not
            # enough for a page of A4 rendered at 720p, where the glyphs are
            # large enough to still be guessed through the blur — so the
            # ones that cover real coursework ask for more.
            px = story.get("redact_blur_px", 7)
            blur = "".join(
                f"{s}{{filter:blur({px}px)!important;-webkit-filter:blur({px}px)!important;}}"
                for s in redact
            )
            pg.add_style_tag(content=blur)
        # redact_text — patrones ya validados en main() (fail-fast)
        rt_patterns = compile_rt_patterns(story)
        pg.evaluate(INJECT_JS)   # motor del cursor/focus (siempre)
        if redaction_on:
            pg.evaluate("patterns => { try { window.__sf.setRtPatterns(patterns); } catch(e){} }", rt_patterns)
            # apply redact_text after preroll, BEFORE the first recorded frame
            try:
                rt_cnt = pg.evaluate("() => { const c = window.__sf.applyRedactText(window.__sf._rtPatterns); return window.__sf._rtCount !== undefined ? window.__sf._rtCount : c; }")
                print(f"  [redact_text] cue pre: {rt_cnt} elements blurred")
            except Exception as e:
                print(f"  [redact_text] pre apply error: {e}")
        T0 = time.time()                       # grabación arranca aquí
        for i, fr in enumerate(frames):
            cue = _sub_ids(fr["cue"], story.get("_idmap"))
            sleep_until(fr["start"])
            if not cue["action"] and cue.get("focus"):
                pg.evaluate("s => window.__sf.focusSel(s)", cue["focus"])
                # track active selector for redact guard
                try: pg.evaluate("s => window.__sf.setLastActiveSelector(s)", cue["focus"])
                except Exception: pass
                if redaction_on:
                    try:
                        rt_cnt = pg.evaluate("() => { const c = window.__sf.applyRedactText(window.__sf._rtPatterns); return window.__sf._rtCount !== undefined ? window.__sf._rtCount : c; }")
                        print(f"  [redact_text] cue {fr['i']:02d} (focus-only): {rt_cnt} elements blurred")
                    except Exception as e:
                        print(f"  [redact_text] cue {fr['i']:02d} apply error: {e}")
            if cue["action"]:
                # esperar al inicio de la secuencia (mover cursor) ya dentro de do_action
                act_sel = cue["action"].get("selector") if cue["action"] else None
                f_sel = cue.get("focus") or act_sel
                if f_sel:
                    try: pg.evaluate("s => window.__sf.setLastActiveSelector(s)", f_sel)
                    except Exception: pass
                do_action(pg, cue, fr["anchor_t"], story["click_lead"])
                # La voz ya habla del RESULTADO del click: esperar a que el
                # selector del cue sea visible (máx. 5s) para que la narración
                # no se adelante a la pantalla (listas, bloques, canvas...).
                # Acotado al inicio del cue siguiente para que un selector que
                # nunca aparezca no retrase a los demás storyboards.
                if cue.get("focus"):
                    nxt = frames[i + 1]["start"] if i + 1 < len(frames) else total
                    budget = min(5.0, nxt - (time.time() - T0) - 0.2)
                    if budget > 0:
                        try:
                            pg.wait_for_selector(cue["focus"], state="visible",
                                                 timeout=max(50, int(budget * 1000)))
                        except Exception:
                            print(f"  ! [focus] «{cue['focus']}» no visible "
                                  f"en {budget:.2f}s; sigo")
                if redaction_on:
                    try:
                        rt_cnt = pg.evaluate("() => { const c = window.__sf.applyRedactText(window.__sf._rtPatterns); return window.__sf._rtCount !== undefined ? window.__sf._rtCount : c; }")
                        print(f"  [redact_text] cue {fr['i']:02d} (post-action): {rt_cnt} elements blurred")
                    except Exception as e:
                        print(f"  [redact_text] cue {fr['i']:02d} apply error: {e}")
        sleep_until(total)
        wall = time.time() - T0
        ctx.close()                            # vídeo se finaliza aquí
        b.close()
    return wall


def assemble(story, frames, wall):
    # localizar el webm
    webm = [os.path.join(VIDDIR, f) for f in os.listdir(VIDDIR) if f.endswith(".webm")]
    webm.sort(key=os.path.getmtime)
    webm = webm[-1]
    vdur = duration(webm)
    shift = vdur - wall
    print(f"  vídeo={os.path.basename(webm)} dur={vdur:.2f}s  wall={wall:.2f}s  shift={shift:+.2f}s")

    # trim_lead (opcional, solo si el storyboard lo pide): el arranque del
    # webm incluye la carga previa a T0; se corta ese tramo del inicio para
    # que el vídeo empiece cuando arranca el reloj de las pistas. Guard: con
    # shift <= 0.2s no merece la pena tocar nada.
    cut = 0.0
    if story.get("trim_lead"):
        if shift > 0.2:
            cut = shift
            print(f"  [trim_lead] cortado {shift:.2f}s del inicio")
        else:
            print(f"  [trim_lead] sin corte (shift={shift:+.2f}s)")

    # 1) silencio inicial ajustado
    lead = max(0.05, story["leadin"] + (0.0 if cut else shift))
    leadwav = os.path.join(FRAMES, "_lead.wav")
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
         "anullsrc=r=24000:cl=mono", "-t", f"{lead:.3f}", "-c:a", "pcm_s16le", leadwav])

    # 2) cada cue rellenado a su segmento
    parts = [leadwav]
    for fr in frames:
        pad = os.path.join(FRAMES, f"_pad_{fr['i']:02d}.wav")
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", fr["mp3"], "-af", "apad",
             "-t", f"{fr['seg']:.3f}", "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", pad])
        parts.append(pad)

    # 3) concat
    listfile = os.path.join(FRAMES, "_list.txt")
    with open(listfile, "w") as f:
        for pth in parts:
            f.write(f"file '{pth}'\n")
    narr = os.path.join(OUT, "narration.wav")
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", listfile, "-c:a", "pcm_s16le", narr])

    # 4) mux
    final = os.path.join(OUT, f"{story['id']}.mp4")
    trim_in = ["-ss", f"{cut:.3f}"] if cut else []
    run(["ffmpeg", "-y", "-loglevel", "error", *trim_in, "-i", webm, "-i", narr,
         "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", final])
    return final, vdur


# ------------------------------------------------------------------- cleanup
def cleanup(story, run_start):
    """Borra SOLO las sesiones que ESTE run acaba de crear.

    Regla del usuario: limpiar lo que uno mismo crea, jamas tocar lo preexistente.
    Identifica por (title + notes) y exige created_at >= inicio del run.
    """
    cl = story.get("cleanup")
    if not cl or not cl.get("match"):
        return
    import urllib.request
    from email.utils import parsedate_to_datetime
    base = story["base_url"].rstrip("/")
    date = cl.get("date") or time.strftime("%Y-%m-%d")
    y, mo = date.split("-")[:2]

    def api(path, method="GET"):
        req = urllib.request.Request(f"{base}{path}", method=method)
        return json.load(urllib.request.urlopen(req))

    try:
        month = api(f"/api/agenda/month/{int(y)}/{int(mo)}/sessions")
    except Exception as e:
        print("  ! cleanup: no pude listar sesiones:", e)
        return

    cands = []
    def walk(x):
        if isinstance(x, list):
            for i in x: walk(i)
        elif isinstance(x, dict):
            if x.get("id") and (x.get("title") or x.get("titulo")):
                cands.append(x)
            for v in x.values(): walk(v)
    walk(month)

    match = cl["match"]
    deleted = 0
    for c in cands:
        if (c.get("title") or c.get("titulo")) != match.get("title"):
            continue
        try:                                   # detalle: notes + created_at
            d = api(f"/api/agenda/session/{c['id']}")
        except Exception:
            continue
        if match.get("notes") is not None and d.get("notes") != match.get("notes"):
            continue                           # nota distinta -> no es nuestra
        try:
            ts = parsedate_to_datetime(d["created_at"]).timestamp()
        except Exception:
            ts = 0
        if not ts or ts < run_start - 120:     # anterior al run -> preexistente
            continue
        try:
            api(f"/api/agenda/session/{c['id']}", method="DELETE")
            print(f"  borrada sesion de prueba {c['id']}")
            deleted += 1
        except Exception as e:
            print(f"  ! no pude borrar {c['id']}: {e}")
    if not deleted:
        print("  (cleanup: nada que borrar)")


def selftest_redact(pattern="/dashboard/i"):
    """Verificación rápida de redact_text SIN TTS ni vídeo.

    Abre la app, inyecta el patrón de muestra con la misma lógica de blur
    que usa la grabación real, captura /tmp/redact_selftest.png y imprime el
    nº de elementos difuminados. Termina en segundos.
    """
    from playwright.sync_api import sync_playwright
    pats = compile_rt_patterns({"redact_text": [pattern]})
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        # service_workers="block" igual que en record(): la SW en caché puede
        # recargar la página a mitad de evaluate y destruir el contexto.
        ctx = b.new_context(viewport={"width": 1280, "height": 720},
                            service_workers="block")
        pg = ctx.new_page()
        # La app puede navegar sola al arrancar (live reload): reintenta la
        # inyección en lugar de abortar por "context destroyed".
        for attempt in range(3):
            pg.goto("http://127.0.0.1:8081/", wait_until="domcontentloaded")
            try:
                pg.wait_for_selector(".dashboard-nav-grid", state="visible", timeout=8000)
            except Exception:
                pass
            pg.wait_for_timeout(500)
            try:
                pg.add_style_tag(content=INJECT_CSS)
                pg.evaluate(INJECT_JS)
                break
            except Exception as e:
                if attempt == 2:
                    raise
                print(f"[selftest-redact] retry {attempt + 1}: {e}")
        pg.evaluate("p => window.__sf.setRtPatterns(p)", pats)
        cnt = pg.evaluate("() => window.__sf.applyRedactText(window.__sf._rtPatterns)")
        pg.screenshot(path="/tmp/redact_selftest.png")
        # observer: muta el DOM y relee en la MISMA evaluación — si el live-
        # reload recargara la página entre evaluate y evaluate, __sf sería
        # undefined y el test mentiría con un falso negativo.
        cnt2 = pg.evaluate("""async () => {
            document.body.appendChild(document.createElement('div'));
            await new Promise(r => setTimeout(r, 150));
            return window.__sf ? window.__sf._rtCount : -1;
        }""")
        try:
            ctx.close()
            b.close()
        except Exception:
            pass
    print(f"[selftest-redact] patrón={pattern} difuminados={cnt} "
          f"tras-mutación={cnt2} -> /tmp/redact_selftest.png")
    if not cnt or cnt2 < 0:
        raise SystemExit("selftest FAILED: sin elementos difuminados o __sf perdido")
    print("[selftest-redact] OK")


def main():
    run_start = time.time()
    story = json.load(open(STORY))
    # Default is the dashboard; a storyboard that documents the landing
    # page (the install guide lives there) names its own URL.
    story["base_url"] = story.get("base_url") or "http://127.0.0.1:8081/"
    real_mode = "--real" in sys.argv[1:]
    story["real_mode"] = real_mode
    print(f"[mode] {'REAL DATA (mocking disabled)' if real_mode else 'FIXTURES (mocked API)'}")
    resolve_real_ids(story)              # ${TOKENS} reales -> _idmap (fail-soft)
    collect_dynamic_redact(story)       # títulos reales -> redact_text (fail-soft)
    compile_rt_patterns(story)              # fail-fast ANTES del pre-pass TTS
    shutil.rmtree(VIDDIR, ignore_errors=True)   # solo el vídeo; el audio se cachea
    os.makedirs(FRAMES, exist_ok=True)
    os.makedirs(VIDDIR, exist_ok=True)
    print("== pre-pass TTS ==")
    frames = pre_pass(story)
    total = build_timeline(story, frames)
    print(f"== timeline total = {total:.2f}s ==")
    print("== grabación ==")
    wall = record(story, frames, total)
    print("== montaje ==")
    final, vdur = assemble(story, frames, wall)
    print(f"== LISTO: {final} ==")
    print("== limpieza ==")
    cleanup(story, run_start)
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-show_entries", "stream=codec_type,width,height",
             "-of", "default=nw=1", final])
    print(r.stdout)


if __name__ == "__main__":
    if _SELFTEST_REDACT:
        selftest_redact()
    else:
        main()
