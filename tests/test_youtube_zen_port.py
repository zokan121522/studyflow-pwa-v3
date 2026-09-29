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


def test_index_html_loads_yt_modules_after_ai_js():
    """youtube-queue.js reads App.AI._onContentSuccess at insert time, so it
    must be loaded after ai.js defines it (v2's ordering does not apply)."""
    src = INDEX_HTML.read_text()
    assert src.index('src="/features/studyflow/youtube-queue.js') > src.index(
        'src="/features/studyflow/ai.js"'
    )
    assert "youtube-embed.css" in src
