r"""Regression tests for the Phase 8 YouTubeZen port (v2 -> v3).

Covers the two classes of defect that actually broke during the port, both of
which were invisible to a syntax check:

1. `create_youtube_zen_task` wrote `topic_id` / `block_id` straight from the
   JSON body into `ai_tasks`. v3's topics and blocks are SERIAL *integers*,
   while the frontend legitimately sends `""` when the user launches from the
   topic toolbar with no block selected. Postgres then raised
   `InvalidTextRepresentation: invalid input syntax for type integer: ""`,
   surfacing as a 500. `_coerce_id` maps ""/None -> None before the INSERT.

2. The six `/ai/notebooklm/youtube-zen*` routes and the `youtubeZen` /
   `youtubeZenQueue` client functions must stay wired: the routes are the
   only way the PWA reaches the queue worker, and the client functions are
   what the Phase 8 dialog calls. A missing export shows up as
   "gen.youtubeZen is not a function" in the browser console, never in a
   Python test run.

No test here touches the network or the database.
"""

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from ai.notebooklm.utils import _coerce_id  # noqa: E402

ROUTES_PY = ROOT / "backend" / "routes" / "notebooklm_content.py"
AI_PY = ROOT / "backend" / "routes" / "ai.py"
AI_JS = ROOT / "frontend" / "features" / "studyflow" / "ai-notebooklm.js"
AI_JS_MAIN = ROOT / "frontend" / "features" / "studyflow" / "ai.js"
INDEX_HTML = ROOT / "frontend" / "index.html"
YT_ZEN_PY = ROOT / "backend" / "ai" / "notebooklm" / "youtube_zen.py"
YT_EMBED_CSS = ROOT / "frontend" / "features" / "studyflow" / "youtube-embed.css"


# ── 1. Integer coercion of topic_id / block_id ────────────────────────


def test_coerce_id_maps_empty_to_none():
    """The exact payload that used to raise InvalidTextRepresentation."""
    assert _coerce_id("") is None
    assert _coerce_id(None) is None
    assert _coerce_id("   ") is None


def test_coerce_id_accepts_numeric_strings():
    assert _coerce_id("42") == 42
    assert _coerce_id(42) == 42


def test_coerce_id_rejects_garbage_with_clean_error():
    with pytest.raises(ValueError, match="topic_id"):
        _coerce_id("not-an-id", "topic_id")


def test_youtube_zen_coerces_ids_before_insert():
    """The INSERT must not receive a raw "" for an integer column."""
    src = YT_ZEN_PY.read_text()
    coerce_idx = src.index('topic_id = _coerce_id(topic_id, "topic_id")')
    insert_idx = src.index("INSERT INTO ai_tasks")
    assert coerce_idx < insert_idx, "coercion must happen before the INSERT"

    # `block_id or ""` would re-introduce the empty string right after coercion.
    assert 'block_id or ""' not in src, "block_id must not be re-defaulted to ''"


# ── 2. Route wiring ───────────────────────────────────────────────────

YOUTUBE_ZEN_ROUTES = [
    "/ai/notebooklm/youtube-zen",
    "/ai/notebooklm/youtube-zen/queue",
    "/ai/notebooklm/youtube-zen/queue/status",
    "/ai/notebooklm/youtube-zen/<task_id>/retry-chunk",
    "/ai/notebooklm/youtube-zen/<task_id>/continue-without",
    "/ai/notebooklm/youtube-zen/<task_id>/retry-now",
]


@pytest.mark.parametrize("route", YOUTUBE_ZEN_ROUTES)
def test_youtube_zen_route_is_registered(route):
    assert f'@bp.route("{route}"' in ROUTES_PY.read_text()


def test_youtube_zen_routes_require_auth():
    """Every new endpoint must sit behind @token_required, not just some."""
    src = ROUTES_PY.read_text()
    for route in YOUTUBE_ZEN_ROUTES:
        idx = src.index(f'@bp.route("{route}"')
        window = src[idx : idx + 400]
        assert "@token_required" in window, f"{route} is unauthenticated"


def test_youtube_zen_routes_use_v3_import_style():
    """v3 imports notebooklm modules flat (no `backend.` prefix)."""
    src = ROUTES_PY.read_text()
    assert "from ai.notebooklm.youtube_zen import" in src
    assert "from ai.notebooklm.youtube_queue import" in src
    assert "from backend.ai.notebooklm.youtube_zen" not in src


def test_queue_endpoint_keeps_flood_guards():
    """Per-user (50) and global (500) caps are an audit fix; keep them."""
    src = ROUTES_PY.read_text()
    assert "status = 'queued'" in src
    assert "> 50" in src, "per-user queue cap missing"
    assert "> 500" in src, "global queue cap missing"


def test_openzen_md_templates_endpoint_exists():
    """The Phase 8 dialog fetches the template grid from here."""
    assert '@bp.route("/ai/openzen-md-templates", methods=["GET"])' in AI_PY.read_text()


def test_yt_meta_route_uses_full_path():
    """server.py forces url_prefix='/api', so the blueprint path must be
    '/yt/meta' — a url_prefix on the blueprint is silently discarded."""
    src = (ROOT / "backend" / "routes" / "yt_meta.py").read_text()
    assert '@bp.get("/yt/meta")' in src
    assert 'url_prefix="/yt"' not in src


def test_yt_meta_keeps_ssrf_containment():
    src = (ROOT / "backend" / "routes" / "yt_meta.py").read_text()
    assert "parsed.scheme == \"https\"" in src
    assert "_ALLOWED_YOUTUBE_HOSTS" in src


# ── 3. Frontend wiring ────────────────────────────────────────────────


def test_generation_exports_youtube_zen_functions():
    src = AI_JS.read_text()
    assert "async function youtubeZen(" in src
    assert "async function youtubeZenQueue(" in src
    for name in ("youtubeZen", "youtubeZenQueue"):
        assert re.search(rf"^    {name},$", src, re.M), f"{name} not exported"


def test_generation_posts_to_youtube_zen_endpoints():
    src = AI_JS.read_text()
    assert 'API.post("/ai/notebooklm/youtube-zen"' in src
    assert 'API.post("/ai/notebooklm/youtube-zen/queue"' in src


def test_youtube_dialog_is_not_the_prompt_stub():
    """Phase 8 replaced the prompt()-based placeholder with the real dialog."""
    src = AI_JS_MAIN.read_text()
    assert 'prompt("🎬 Pega la URL de YouTube:")' not in src
    assert "youtube-dialog-overlay" in src


def test_zen_dialog_is_reachable_from_the_ui():
    """v2 had TWO entries: `notebooklm-youtube` (native) and `youtube` (Zen).
    Porting only the first left the zen path unreachable from the toolbar."""
    src = AI_JS_MAIN.read_text()
    assert 'action === "youtube"' in src, "no hay rama que abra el dialogo Zen"
    # The zen call must NOT force the notebooklm provider.
    zen_call = re.search(
        r'action === "youtube"\)\s*\{.{0,300}?_showYoutubeDialog\(([^)]*)\)', src, re.S
    )
    assert zen_call, "la rama youtube no llama a _showYoutubeDialog"
    assert "notebooklm" not in zen_call.group(1), (
        "la rama Zen no debe forzar provider notebooklm (oculta los controles)"
    )


def test_zen_action_is_rendered_by_a_manifest():
    """A handler with no manifest entry is dead code: nothing ever emits
    `data-ai-action="youtube"`, so the button never appears in the toolbar."""
    src = AI_JS_MAIN.read_text()
    entry = re.search(r'\{\s*id:\s*"youtube-zen".{0,220}?md:\s*"youtube"', src, re.S)
    assert entry, "el manifest no declara una entrada que emita la accion Zen"


def test_zen_action_lives_under_the_openzen_addon():
    """YouTubeZen runs `get_provider("opencode-acp")` — it is an OpenZen
    feature, not a NotebookLM one. Grouping it under NotebookLM would both
    mislabel it and hide it from the 🤖 OpenZen group the user looks at."""
    src = AI_JS_MAIN.read_text()
    idx_entry = src.index('id: "youtube-zen"')
    # the opencode manifest must be the nearest preceding register() call
    opencode_idx = src.rindex('slug: "opencode"', 0, idx_entry)
    notebooklm_idx = src.rindex('slug: "notebooklm"', 0, idx_entry)
    assert opencode_idx > notebooklm_idx, (
        "youtube-zen quedo registrado antes del manifest de opencode "
        "(o sea, en el grupo de NotebookLM)"
    )


def test_zen_manifest_entry_maps_the_usage_counter_to_youtube_zen():
    """The (0/10) counter reads ai_tasks.task_type, so a wrong mapping shows
    an always-zero counter next to the button."""
    src = AI_JS_MAIN.read_text()
    entry = re.search(r'\{\s*id:\s*"youtube-zen".{0,260}?\},\n', src, re.S)
    assert entry, "no se encuentra la entrada del manifest"
    assert "youtube_zen" in entry.group(0), (
        "la entrada no mapea task: youtube_zen — el contador quedaria a 0"
    )


def test_zen_modal_width_beats_the_base_modal():
    """`.kp-modal` sets `width: 480px` in knowledge-pipeline.css, which loads
    AFTER youtube-embed.css. A single-class override ties on specificity
    (0,1,0) and loses on source order, so the width was silently dropped and
    the dialog rendered at 480px. The override must use two classes."""
    css = YT_EMBED_CSS.read_text()
    rule = re.search(r"(\S*kp-modal\S*)\s*\{[^}]*width:", css)
    assert rule, "no hay regla de ancho para el modal Zen"
    assert rule.group(1).count(".") >= 2, (
        f"el override '{rule.group(1)}' tiene especificidad 0,1,0: "
        "empata con .kp-modal y pierde por orden de carga"
    )
    # the dialog must be visibly wider than the 480px base
    m = re.search(r"kp-modal-yt-wide\s*\{[^}]*width:\s*(\d+)px", css)
    assert m and int(m.group(1)) > 480, "el modal Zen no es mas ancho que la base"


def test_zen_template_list_is_a_grid_not_a_narrow_column():
    """The template list is `.ozmd-templates`, which ai.css lays out as a
    single 1-column flex with max-height 300px. Inside a 900px dialog that
    leaves the sides empty and forces scrolling through 11 stacked rows.
    A selector like `.ozmd-tpl-grid` matches nothing — verify against the
    class the template actually renders with."""
    ai_js = AI_JS_MAIN.read_text()
    assert 'class="ozmd-templates"' in ai_js or 'ozmd-templates' in ai_js, (
        "el contenedor de plantillas cambio de clase"
    )
    css = YT_EMBED_CSS.read_text()
    grid = re.search(
        r"kp-modal-yt-wide\s+\.(ozmd-[\w-]+)\s*\{[^}]*display:\s*grid", css, re.S
    )
    assert grid, "la lista de plantillas no se maqueta como rejilla en el modal Zen"
    assert grid.group(1) == "ozmd-templates", (
        f"la rejilla apunta a '.{grid.group(1)}' pero el contenedor real es "
        "'.ozmd-templates' — la regla no se aplica a nada"
    )


def test_zen_insert_resolves_the_course_that_owns_the_topic():
    """`addBlock` posts to /courses/<cid>/topics/<tid>/blocks. The zen queue
    calls _onContentSuccess with no courseIdHint, so it used to fall back to
    STATE.currentCourseId — the course open at INSERT time. With a different
    course open the backend answers 404 "Topic not found in this course" and
    the user gets "Error al guardar el bloque"."""
    src = AI_JS_MAIN.read_text()
    assert "_resolveCourseIdForTopic" in src, "no hay resolutor de curso por topic"
    body = src[src.index("async function _onContentSuccess") :][:600]
    assert "_resolveCourseIdForTopic(topicId, courseIdHint)" in body, (
        "_onContentSuccess sigue usando STATE.currentCourseId — el insert "
        "falla con 404 si el curso abierto no es el dueño del topic"
    )
    # the resolver must look the topic up among the courses, not just guess
    resolver = src[src.index("async function _resolveCourseIdForTopic") :][:900]
    assert "fetchCourses()" in resolver, "el resolutor no consulta los cursos"
    assert "course.topics" in resolver, "el resolutor no busca el topic dueño"


def test_zen_format_has_its_own_label():
    """youtube-queue.js passes format="ytd_zen", which matched no branch and
    fell through to the generic one, titling the block "🤖 Markdown"."""
    src = AI_JS_MAIN.read_text()
    branch = re.search(
        r'format === "ytd_zen"\)\s*\{(.{0,400})', src, re.S
    )
    assert branch, "el formato ytd_zen no tiene rama propia"
    assert "YouTube Zen" in branch.group(1), (
        "la rama ytd_zen no etiqueta el bloque como YouTube Zen — "
        "cae en la genérica y se titula '🤖 Markdown'"
    )


def test_zen_queue_passes_the_zen_format():
    """The queue must keep sending ytd_zen, and the insert must tolerate a
    missing courseIdHint (it resolves the course from the topic instead)."""
    q = (ROOT / "frontend" / "features" / "studyflow" / "youtube-queue.js").read_text()
    call = re.search(r"_onContentSuccess\(([^)]*)\)", q, re.S)
    assert call, "la cola no llama a _onContentSuccess"
    assert "ytd_zen" in call.group(1), "la cola no pasa el formato ytd_zen"


def _split_args(text):
    """Split a call's arguments on top-level commas only.

    A plain str.split(",") also cuts inside ``{ title: x, model: y }``, which
    silently turns one argument into two.
    """
    args, depth, buf = [], 0, ""
    for ch in text:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            args.append(buf.strip())
            buf = ""
        else:
            buf += ch
    if buf.strip():
        args.append(buf.strip())
    return args


def test_start_stream_poll_call_does_not_pass_depth_as_the_course():
    """startStreamPoll(taskId, blockId, format, topicId, courseIdHint, onInsert).
    The zen flow passed `depth` in the courseIdHint slot, so the insert
    POSTed to /courses/standard/topics/<id>/blocks and failed."""
    src = AI_JS.read_text()
    # target the zen call specifically: many generators call startStreamPoll
    call = re.search(
        r'startStreamPoll\((?:[^()]|\([^()]*\))*?"ytd_zen"(?:[^()]|\([^()]*\))*\)', src, re.S
    )
    assert call, "no se encuentra la llamada a startStreamPoll con formato ytd_zen"
    args = _split_args(call.group(0)[len("startStreamPoll("): -1])
    # Arity grew when startStreamPoll gained an optional opts override, so the
    # invariant to protect is the SLOT, not the count.
    assert len(args) >= 5, f"faltan argumentos en la llamada: {args}"
    assert "depth" not in args[4], (
        f"el 5º argumento es courseIdHint pero se le pasa {args[4]!r} "
        "(depth) -> POST /courses/standard/topics/<id>/blocks -> 405"
    )
    # the hint must be a real course id captured from state, not a literal
    assert re.search(r"const\s+courseId\s*=[^;]*currentCourseId", src), (
        "la llamada no captura el curso actual en una variable courseId"
    )
    assert src.index("const courseId") < src.index('"ytd_zen"'), (
        "courseId debe calcularse ANTES de la llamada a startStreamPoll"
    )


def _function_body(src, header):
    """Return the function starting at `header`, brace-matched. A fixed-length
    slice silently truncates as the function grows, which reads as a missing
    branch rather than as a test bug."""
    start = src.index(header)
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start : i + 1]
    raise AssertionError(f"no se pudo cerrar la funcion {header!r}")


def test_course_resolver_rejects_a_non_numeric_hint():
    """Course ids are numeric. A hint like "standard" (the depth) must never be
    trusted, or the insert targets a course that does not exist."""
    resolver = _function_body(AI_JS_MAIN.read_text(),
                              "async function _resolveCourseIdForTopic")
    assert re.search(r"isCourseId\(hint\)", resolver), (
        "el resolutor no valida que el hint sea numerico — un depth colado "
        "produciria /courses/standard/..."
    )
    assert re.search(r"\\d\+", resolver), "el resolutor no comprueba el formato numerico"


def test_course_resolver_prefers_the_topic_owner_over_the_hint():
    """Even a numeric hint can point at the wrong course (the user navigated
    before the task finished). The topic decides, so the owner must win."""
    resolver = _function_body(AI_JS_MAIN.read_text(),
                              "async function _resolveCourseIdForTopic")
    i_owner = resolver.find("fetchCourses()")
    i_hint = resolver.find("isCourseId(hint)")
    assert i_owner != -1 and i_hint != -1, "faltan las dos ramas del resolutor"
    assert i_owner < i_hint, (
        "el hint se evalua antes que el dueno del topic: un hint numerico "
        "equivocado gana y el bloque cae en el curso que no es"
    )


def test_index_html_loads_yt_modules_after_ai_js():
    """youtube-queue.js reads App.AI._onContentSuccess at insert time, so it
    must be loaded after ai.js defines it (v2's ordering does not apply)."""
    src = INDEX_HTML.read_text()
    assert src.index('src="/features/studyflow/youtube-queue.js') > src.index(
        'src="/features/studyflow/ai.js"'
    )
    assert "youtube-embed.css" in src
