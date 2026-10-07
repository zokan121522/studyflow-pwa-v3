#!/usr/bin/env python3
"""Walkthrough narrado: TTS (edge-tts) + Playwright (Chrome) + ffmpeg.

Sincronización determinista: la voz se genera PRIMERO y se mide; el vídeo se
conduce en tiempo real anclando cada acción a la palabra exacta (WordBoundary).
El montaje coloca cada audio en su offset y lo mezcla con el vídeo.
"""
import asyncio, json, os, re, shutil, subprocess, sys, time, unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fixtures

HERE = os.path.dirname(os.path.abspath(__file__))
STORY = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "storyboards", "agenda.json")
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
        fr["seg"] = fr["dur"] + gap
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
    // Scroll only when the target is off-screen. `nearest` is a no-op for an
    // element already in view, which is every cue in the dashboard videos, so
    // this leaves those framings untouched. Without it a landing-page story
    // highlights a card nobody can see: the class lands, the viewport stays
    // at the top, and the narration describes something absent from the frame.
    el.scrollIntoView({block:'nearest',inline:'nearest'});
    el.classList.add('sf-focus');},
  focusSel(sel){this.focus(document.querySelector(sel));},
  center(sel){const e=document.querySelector(sel); if(!e)return null;
    const r=e.getBoundingClientRect(); return {x:r.left+r.width/2,y:r.top+r.height/2};}
};
})();"""


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

        pg.route("**/api/**", _interceptar)
        pg.goto(story["base_url"], wait_until="domcontentloaded")
        # The dashboard grid is the app's ready signal, but a storyboard that
        # documents the landing page never renders it: waiting the full 15s
        # there is dead time that lands as 15s of silence at the head of the
        # finished video. Anything not rooted at the app waits on <body>.
        is_dashboard = story["base_url"].rstrip("/") in ("", "http://127.0.0.1:8081")
        try:
            pg.wait_for_selector(
                ".dashboard-nav-grid" if is_dashboard else "body",
                state="visible", timeout=15000 if is_dashboard else 5000)
        except Exception:
            pass
        pg.wait_for_timeout(400)
        pg.add_style_tag(content=INJECT_CSS)
        redact = dict.fromkeys(GLOBAL_REDACT + (story.get("redact") or []))
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
        pg.evaluate(INJECT_JS)
        T0 = time.time()                       # grabación arranca aquí
        for fr in frames:
            cue = fr["cue"]
            sleep_until(fr["start"])
            if not cue["action"] and cue.get("focus"):
                pg.evaluate("s => window.__sf.focusSel(s)", cue["focus"])
            if cue["action"]:
                # esperar al inicio de la secuencia (mover cursor) ya dentro de do_action
                do_action(pg, cue, fr["anchor_t"], story["click_lead"])
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

    # 1) silencio inicial ajustado
    lead = max(0.05, story["leadin"] + shift)
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
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", webm, "-i", narr,
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


def main():
    run_start = time.time()
    story = json.load(open(STORY))
    # Default is the dashboard; a storyboard that documents the landing
    # page (the install guide lives there) names its own URL.
    story["base_url"] = story.get("base_url") or "http://127.0.0.1:8081/"
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
    main()
