"""The per-session course cache must not outlive the data it holds.

Reported as: imported / generated subjects would not let you edit their
content, and only a full page reload fixed it. A reload works because it
throws the module-level cache away — so the cache was the bug.

Two writes reach the database without going through CoursesAPI, which is why
an explicit clear on every mutation is not enough:
  * pdf-import.js patches the block with a raw fetch
  * the knowledge-pipeline skill writes straight to PostgreSQL over SSH

Hence the TTL plus the revalidate-on-focus, both exercised here for real
under node rather than asserted with a grep.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
API_JS = ROOT / "frontend" / "features" / "studyflow" / "courses-api.js"
PDF_IMPORT_JS = ROOT / "frontend" / "features" / "studyflow" / "pdf-import.js"


def _node(script: str) -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    out = subprocess.run([node, "-e", script], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    # courses-api.js prints a "[Studyflow] ... loaded" banner on load, so the
    # JSON the test asked for is the last line, not the whole stdout.
    lines = [ln for ln in out.stdout.strip().splitlines() if ln.strip()]
    assert lines, f"node no imprimió nada: {out.stdout!r} {out.stderr!r}"
    return lines[-1].strip()


# Harness: load courses-api.js against a fake API whose payload the test can
# change, counting how many network reads actually happen.
# Only .replace() is used on this string (never .format()), so the JS braces
# are written literally. The body runs inside an async IIFE because `node -e`
# evaluates as CommonJS, where top-level await is a syntax error.
_HARNESS = """
global.window = { App: {}, addEventListener: () => {} };
global.document = { visibilityState: 'visible' };
let server = 'v1';
let reads = 0;
global.API = {
  get: async () => { reads++; return { course: { id: 7, title: server, topics: [] } }; }
};
require({path});
const A = window.App.CoursesAPI;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const title = async (force) => (await A.fetchCourseDetail(7, force)).title;
(async () => {
"""


def _script(body: str) -> str:
    # .format() would choke on the JS braces, so substitute the path only.
    return (_HARNESS.replace("{path}", json.dumps(str(API_JS))) + body + "\n})();\n")


def test_cached_read_is_reused_within_the_ttl():
    """The cache still does its job — this is the perf half of the fix."""
    out = json.loads(_node(_script("""
console.log(JSON.stringify({
  primera: await title(),
  lecturasTrasDos: (await title(), reads),
  mismoValor: (await title()) === 'v1',
}));
""")))
    assert out["lecturasTrasDos"] == 1, "la cache debe servir la segunda lectura"
    assert out["mismoValor"] is True


def test_a_write_from_outside_the_app_is_picked_up_after_the_ttl():
    """The reported bug. Something changes the course without CoursesAPI
    knowing (pipeline over SSH, a second tab). Before this fix the read kept
    returning the old title until a full reload."""
    out = json.loads(_node(_script("""
await title();
server = 'v2';
const rancioDentroDelTtl = await title();
await sleep(4600);                       // CACHE_TTL_MS is 4000
const frescoTrasTtl = await title();
console.log(JSON.stringify({ rancioDentroDelTtl, frescoTrasTtl }));
""")))
    assert out["frescoTrasTtl"] == "v2", (
        "una escritura externa nunca se vio: el curso quedaba rancia "
        "hasta recargar la página"
    )


def test_returning_to_the_tab_drops_the_cache():
    """The common case: the user switched away while a generation finished."""
    out = json.loads(_node(_script("""
await title();
server = 'v3';
const antes = await title();
window.App.CoursesAPI.clearDetailCache(7);
const despues = await title();
console.log(JSON.stringify({ antes, despues }));
""")))
    assert out["despues"] == "v3"


def test_clear_detail_cache_also_forgets_the_timestamps():
    """A cache entry left in place without its timestamp would be treated as
    permanently stale (or never stale), so both maps have to be dropped
    together — otherwise the TTL and the entry disagree after a clear."""
    src = API_JS.read_text(encoding="utf-8")
    body = src.split("function clearDetailCache(")[1].split("\n  }")[0]
    assert "delete _detailCacheAt[courseId]" in body
    assert "_listCacheAt = 0" in body


def test_the_pdf_scorm_importer_drops_the_cache_on_both_paths():
    """pdf-import.js patches blocks with a raw fetch, so it never reaches
    CoursesAPI.updateBlock — and the SCORM path wrote nothing client-side at
    all. Without an explicit drop the re-render repaints the same stale tree,
    which is why the import only appeared after a reload."""
    src = PDF_IMPORT_JS.read_text(encoding="utf-8")
    patch = src.split("async function _patchBlock(")[1].split("\n  }")[0]
    assert "_dropCourseCache(courseId)" in patch, "el import de PDF no invalida"

    scorm = src.split("function _onScormImported(")[1].split("\n  }")[0]
    assert "_dropCourseCache(ctx.courseId)" in scorm, "el import SCORM no invalida"

    assert "function _dropCourseCache(" in src
    assert "CoursesAPI.clearDetailCache" in src


def test_clear_detail_cache_is_reachable_from_the_importer():
    """If CoursesAPI ever stopped exporting it, the importer would silently
    degrade back to the stale-cache behaviour instead of failing loudly."""
    src = API_JS.read_text(encoding="utf-8")
    assert "clearDetailCache," in src.split("return {")[-1]
