"""Tests for the chunk-recovery bar of a failed YouTubeZen detailed task.

A detailed youtube_zen task runs one chunk per plan section. When a chunk
returns an empty response the backend keeps the healthy parts, the plan and
the failed index in coverage_data (state="chunk_error") and offers
retry-chunk / continue-without. Those endpoints existed but nothing in the
frontend called them, so the user got a red error and silently lost every
chunk that had already succeeded.

These tests pin the contract on both sides of the wire: the shape the
backend persists, the URL the frontend posts to, and the parsing that turns
a failed task into "chunk 7 of 32" plus a row of buttons.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "frontend/features/studyflow/ai-tasks.js"
CSS = ROOT / "frontend/features/studyflow/ai.css"
ZEN = ROOT / "backend/ai/notebooklm/youtube_zen.py"
ROUTES = ROOT / "backend/routes/notebooklm_content.py"

js = JS.read_text(encoding="utf-8")
css = CSS.read_text(encoding="utf-8")
zen = ZEN.read_text(encoding="utf-8")
routes = ROUTES.read_text(encoding="utf-8")


# ── the backend contract the UI depends on ────────────────────────────

def test_backend_marks_recoverable_failures_with_the_prefix():
    """_persist_chunk_failure writes CHUNK_ERROR|<idx>|<msg>; the UI keys on it."""
    assert 'f"CHUNK_ERROR|{chunk_idx}|{message}"' in zen


def test_backend_persists_everything_a_resume_needs():
    """A resume must not have to redo the plan or the finished chunks."""
    body = re.search(
        r"def _persist_chunk_failure\(.*?coverage\.update\(\{(.*?)\}\)", zen, re.S
    )
    assert body, "no se encuentra el cuerpo de _persist_chunk_failure"
    blob = body.group(1)
    for field in ("state", "failed_chunk", "num_chunks", "retry_count",
                  "max_retries", "parts", "plan_text", "sections"):
        assert f'"{field}"' in blob, f"coverage_data no guarda {field}"


def test_poll_exposes_coverage_data_so_the_ui_can_read_it():
    """Without coverage_data in the poll payload the bar cannot show X of N."""
    tasks = (ROOT / "backend/ai/tasks.py").read_text(encoding="utf-8")
    assert '"coverage_data": coverage_data' in tasks


def test_resume_clears_the_stale_error_message():
    """A processing task must not still carry CHUNK_ERROR|... as its checklist.

    The poll reads error_message as the step checklist while processing, so
    a stale failure line would be logged as if it were a live step.
    """
    fn = re.search(r"def _resume_detailed_task\(.*?row = db\.query_one", zen, re.S)
    assert fn, "no se encuentra _resume_detailed_task"
    assert "error_message = NULL" in fn.group(0)


# ── the two endpoints the buttons must hit ─────────────────────────────

@pytest.mark.parametrize("endpoint", ["retry-chunk", "continue-without"])
def test_endpoint_exists_and_is_posted_to(endpoint):
    assert f'route("/ai/notebooklm/youtube-zen/<task_id>/{endpoint}", methods=["POST"])' in routes
    # the URL is built from the action id the button carries, so both the
    # route and the data-chunk attribute must agree on the exact string
    assert f'data-chunk="{endpoint}"' in js
    assert '`/ai/notebooklm/youtube-zen/${taskId}/${action}`' in js


def _stream_error_branch() -> str:
    """The error branch of the STREAM poll, not the legacy one.

    startPoll (legacy) also has an `if (task.status === "error")` and comes
    first in the file, so a bare search silently asserts against the wrong
    function — which is how the first version of these tests "passed" while
    proving nothing about the panel.
    """
    poll = re.search(r"function startStreamPoll\(.*", js, re.S).group(0)
    branch = re.search(r'if \(task\.status === "error"\) \{(.*?)\n          \}', poll, re.S)
    assert branch, "no se encuentra la rama de error de startStreamPoll"
    return branch.group(1)


# ── the parser, executed for real ──────────────────────────────────────
#
# String assertions cannot catch the mistakes that actually happen here:
# failed_chunk is 0-based in the DB and must read 1-based, and the reason
# has to be lifted out of the "CHUNK_ERROR|2|msg" pipe-delimited prefix. A
# test that greps for "_showChunkBar(chunkInfo)" passes just as happily when
# chunkInfo is hardcoded to null — which is exactly what a mutation check
# caught. So the function is extracted and run.

_PARSER_JS = r"""
const _CHUNK_PREFIX_SRC = %s;
%s
const cases = [
  ["structured", {status: "error", error_message: "CHUNK_ERROR|6|respuesta vacía",
    coverage_data: {state: "chunk_error", failed_chunk: 6, num_chunks: 32,
                    retry_count: 1, max_retries: 5, parts: ["a","b","c","d","e","f"]}}],
  ["prefix only", {status: "error", error_message: "CHUNK_ERROR|0|vacio"}],
  ["fatal", {status: "error", error_message: "DB connection refused"}],
  ["no message", {status: "error"}],
  ["null task", null],
  ["reason with pipes", {status: "error",
    error_message: "CHUNK_ERROR|2|timeout | de disco | o algo así",
    coverage_data: {state: "chunk_error", failed_chunk: 2, num_chunks: 5, parts: []}}],
];
console.log(JSON.stringify(cases.map(([name, t]) => [name, _chunkFailureInfo(t)])));
"""


def _run_parser(cases_js: str) -> list:
    """Execute the real _chunkFailureInfo in node and return its results."""
    import json
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node no disponible")
    src = re.search(
        r"( {4}const _CHUNK_PREFIX.*?\n {4}function _chunkFailureInfo\(task\) \{.*?\n {4}\})",
        js,
        re.S,
    )
    assert src, "no se puede extraer _chunkFailureInfo"
    block = src.group(1)
    # the extracted block is indented inside the IIFE
    block = "\n".join(line[4:] if line.startswith("    ") else line
                      for line in block.splitlines())
    program = _PARSER_JS % (json.dumps("CHUNK_ERROR|"), block)
    out = subprocess.run([node, "-e", program], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, f"node falló: {out.stderr}"
    return json.loads(out.stdout)


def test_parser_reports_chunk_position_one_based():
    """failed_chunk is stored 0-based; the user thinks in "chunk 7 of 32"."""
    results = dict(_run_parser(""))
    info = results["structured"]
    assert info is not None
    assert info["idx"] == 7, "el índice debe mostrarse en base 1"
    assert info["total"] == 32
    assert info["kept"] == 6
    assert info["tries"] == 1
    assert info["maxTries"] == 5


def test_parser_strips_the_prefix_and_keeps_the_reason():
    results = dict(_run_parser(""))
    assert results["structured"]["reason"] == "respuesta vacía"
    # a reason containing pipes must survive intact
    assert results["reason with pipes"]["reason"] == "timeout | de disco | o algo así"


def test_parser_works_without_coverage_data():
    """A task resumed from another tab may only carry the marker."""
    results = dict(_run_parser(""))
    info = results["prefix only"]
    assert info is not None
    assert info["idx"] == 1
    assert info["total"] == 0
    assert info["reason"] == "vacio"


def test_parser_returns_null_for_fatal_errors():
    """A fatal error must NOT offer retry — there is nothing to resume."""
    results = dict(_run_parser(""))
    assert results["fatal"] is None
    assert results["no message"] is None
    assert results["null task"] is None


def test_resume_action_maps_to_retry_and_skip():
    """retry-chunk -> action='retry', continue-without -> action='skip'."""
    assert 'action="retry"' in routes
    assert 'action="skip"' in routes


# ── frontend: detection ────────────────────────────────────────────────

def test_chunk_prefix_is_declared_once():
    assert js.count('_CHUNK_PREFIX = "CHUNK_ERROR|"') == 1


def test_parser_prefers_structured_coverage_data():
    """failed_chunk is 0-based in the DB and must be shown 1-based."""
    assert "cov.state === \"chunk_error\"" in js
    assert "(cov.failed_chunk || 0) + 1" in js


def test_parser_falls_back_to_the_prefix_alone():
    """A task resumed from another tab may not carry coverage_data."""
    assert 'msg.indexOf(_CHUNK_PREFIX) === 0' in js


def test_fatal_errors_are_not_offered_a_retry():
    """Only recoverable failures get the bar — never a dead task.

    Asserts the parser is actually consulted and the bar is gated behind its
    result: with `chunkInfo = null` hardcoded, the bar would appear on every
    fatal error (and never on a recoverable one).
    """
    branch = _stream_error_branch()
    assert "const chunkInfo = _chunkFailureInfo(task);" in branch
    bar_call = branch.index("_showChunkBar(chunkInfo)")
    assert "if (chunkInfo)" in branch[:bar_call]
    # the generic error path must stay reachable after the chunk check
    assert bar_call < branch.index("Error en la tarea")


# ── frontend: the bar itself ───────────────────────────────────────────

def test_bar_exists_in_the_panel_markup():
    assert 'id="sf-ai-chunkbar"' in js


def test_bar_offers_all_three_choices():
    for action, label in (("retry-chunk", "Reintentar"),
                          ("continue-without", "Saltar"),
                          ("discard", "Descartar")):
        assert f'data-chunk="{action}"' in js
        assert label in js


def test_bar_tells_the_user_the_work_survives():
    """The whole point: 'restarting the video' must not be implied."""
    assert "se conservan" in js
    assert "no se vuelve a empezar" in js.lower() or "no volver a empezar" in js.lower()


def test_discard_does_not_post_anything():
    """'Descartar' is local — it must not call a resume endpoint."""
    handler = re.search(r"async function _onChunkAction\(action\) \{(.*?)\n    \}", js, re.S)
    assert handler
    body = handler.group(1)
    discard = body.split('if (action === "discard")')[1].split("return;")[0]
    assert "API.post" not in discard


def test_actions_are_locked_while_the_request_is_in_flight():
    """Without a guard, a double click fires two resumes of the same chunk."""
    assert "b.disabled = off" in js
    assert "lock(true)" in js
    assert "lock(false)" in js  # re-enabled when the request fails


# ── frontend: resuming the poll ────────────────────────────────────────

def test_resume_rearms_the_same_poll_closure():
    """The task_id is unchanged, so the same closure must keep polling.

    Rebuilding the poll instead would drop the captured courseId, the
    format and the insert callback captured at generation start.
    """
    assert "_streamState.restartPoll = null" in js
    assert "if (_streamState.restartPoll)" in js
    assert "_streamState.restartPoll();" in js
    assert "setInterval(poll, 2500);" in js


def test_poll_does_not_cancel_the_task_on_a_chunk_failure():
    """The bar path must leave Cancelar hidden and never POST a cancel.

    done=true is set before the branch (the task really is in error), so what
    matters is that resuming is still possible and the task is not destroyed
    while the user is reading which chunk failed.
    """
    before_bar = _stream_error_branch().split("_showChunkBar(chunkInfo)")[0]
    assert 'cancelBtn.style.display = "none"' in before_bar
    assert "_cancelTask" not in before_bar


def test_closing_the_panel_resets_the_resume_hook():
    """A stale restartPoll would re-arm a poll for a task from a previous run."""
    hide = re.search(r"function _hideStreamModal\(\) \{(.*?)\n    \}", js, re.S).group(1)
    assert "_streamState.restartPoll = null" in hide
    assert "_clearChunkBar()" in hide


def test_new_task_hides_a_stale_bar():
    show = re.search(r"function _showStreamModal\(title, modelName\) \{(.*?)\n    \}", js, re.S).group(1)
    assert "_clearChunkBar()" in show
    assert "_streamState.restartPoll = null" in show


# ── css ────────────────────────────────────────────────────────────────

def test_bar_is_styled():
    for selector in (".sf-ai-chunkbar {", ".sf-ai-chunkbar-btns {",
                     ".sf-ai-chunkbar-kept {", ".sf-ai-chunkbar-msg {"):
        assert selector in css, f"falta el estilo {selector}"


def test_disabled_buttons_look_disabled():
    assert ".sf-ai-chunkbar .sf-ai-btn:disabled" in css
