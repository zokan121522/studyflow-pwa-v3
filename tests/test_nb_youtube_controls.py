r"""Tests for issue #13 — the NotebookLM YouTube button gets the YouTubeZen controls.

Before #13 the native NotebookLM path shipped ONE fixed prompt, so the dialog
had nothing to send: no template, no language, no depth, no mode. Phase 64
(#255) hid the controls for that reason. The native path now composes its
prompt from the four options, so the controls are real.

Two things are worth pinning down here, because neither shows up in a syntax
check or when reading the diff:

1. **The no-options path must not change.** `build_native_youtube_prompt()`
   with every value at its default has to return the *same object* as
   YOUTUBE_TO_MARKDOWN_PROMPT, not an equal-looking copy. That identity is
   what guarantees the Phase 65 citation-strip path and the existing
   NotebookLM output keep working untouched.

2. **The wiring has to survive end to end.** The dialog builds the options,
   `youtubeToMd` forwards them, the route passes them to the task factory and
   the worker reads them back off the row. A break anywhere in that chain
   shows up as a silently ignored control in the browser, never in Python.

No test here touches the network or the database.
"""

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

# `ai.notebooklm.youtube` imports the NotebookLM SDK at module level (directly
# and through ai.notebooklm.client), and that SDK is not installed outside the
# container. The composer under test is a pure function, so stub the whole SDK
# module tree before importing it.
def _stub_notebooklm_sdk() -> None:
    import types

    if "notebooklm" in sys.modules and hasattr(
        sys.modules["notebooklm"], "__path__"
    ):
        return  # the real package is installed; nothing to do

    pkg = types.ModuleType("notebooklm")
    pkg.__path__ = []  # mark it as a package so submodules can be registered
    pkg.NotebookLMClient = type("NotebookLMClient", (), {})
    exceptions = types.ModuleType("notebooklm.exceptions")
    exceptions.ChatResponseParseError = type("ChatResponseParseError", (Exception,), {})
    pkg.exceptions = exceptions
    sys.modules["notebooklm"] = pkg
    sys.modules["notebooklm.exceptions"] = exceptions


_stub_notebooklm_sdk()

from ai.notebooklm.prompts import YOUTUBE_TO_MARKDOWN_PROMPT  # noqa: E402
from ai.notebooklm.youtube import build_native_youtube_prompt  # noqa: E402

YT_PY = ROOT / "backend" / "ai" / "notebooklm" / "youtube.py"
ROUTES_PY = ROOT / "backend" / "routes" / "notebooklm_content.py"
AI_NOTEBOOKLM_JS = ROOT / "frontend" / "features" / "studyflow" / "ai-notebooklm.js"
AI_JS = ROOT / "frontend" / "features" / "studyflow" / "ai.js"


# ── the no-regression guarantee ──────────────────────────────────────


def test_defaults_return_the_very_same_object():
    """The identity — not equality — is the no-regression guarantee."""
    prompt = build_native_youtube_prompt()
    assert prompt is YOUTUBE_TO_MARKDOWN_PROMPT


def test_explicit_defaults_are_also_unchanged():
    """Passing the defaults explicitly must behave like passing nothing."""
    prompt = build_native_youtube_prompt(
        template_id=None, depth="standard", mode="unitema", language="auto"
    )
    assert prompt is YOUTUBE_TO_MARKDOWN_PROMPT


# ── each control reaches the prompt ──────────────────────────────────


def test_language_override_is_applied():
    prompt = build_native_youtube_prompt(language="en")
    assert "LANGUAGE OVERRIDE" in prompt
    assert "'en'" in prompt


def test_length_override_is_applied():
    prompt = build_native_youtube_prompt(depth="detailed")
    assert "LENGTH OVERRIDE" in prompt


def test_por_tema_mode_adds_the_organisation_rule():
    prompt = build_native_youtube_prompt(mode="por_tema")
    assert "ORGANISATION OVERRIDE" in prompt


def test_unitema_mode_adds_nothing():
    """unitema is the default layout, so it must stay silent."""
    prompt = build_native_youtube_prompt(mode="unitema")
    assert prompt is YOUTUBE_TO_MARKDOWN_PROMPT


def test_template_role_is_applied():
    prompt = build_native_youtube_prompt(template_id="tutorial")
    assert prompt != YOUTUBE_TO_MARKDOWN_PROMPT


def test_every_option_together_keeps_the_base_rules():
    """Overriding must ADD guidance, never replace the base prompt."""
    prompt = build_native_youtube_prompt(
        template_id="tutorial", depth="concise", mode="por_tema", language="en"
    )
    assert "LANGUAGE OVERRIDE" in prompt
    assert "LENGTH OVERRIDE" in prompt
    assert "ORGANISATION OVERRIDE" in prompt
    # The exhaustive YouTube rules must survive somewhere in the prompt.
    assert YOUTUBE_TO_MARKDOWN_PROMPT.rstrip() in prompt


# ── junk from the client must not break a task ──────────────────────


@pytest.mark.parametrize(
    "bad",
    ["basura", "", None, "  "],
)
def test_unknown_depth_falls_back_to_standard(bad):
    assert build_native_youtube_prompt(depth=bad) is YOUTUBE_TO_MARKDOWN_PROMPT


@pytest.mark.parametrize(
    "bad",
    ["basura", "", None, "  "],
)
def test_unknown_mode_falls_back_to_unitema(bad):
    assert build_native_youtube_prompt(mode=bad) is YOUTUBE_TO_MARKDOWN_PROMPT


def test_case_is_normalised_not_rejected():
    """The UI and the API send lowercase, but a hand-written call must not fail.

    'DETAILED'/'POR_TEMA' are the SAME option with different casing, so they
    must be honoured rather than treated as unknown junk.
    """
    assert "LENGTH OVERRIDE" in build_native_youtube_prompt(depth="DETAILED")
    assert "ORGANISATION OVERRIDE" in build_native_youtube_prompt(mode="POR_TEMA")


def test_uppercase_language_is_accepted():
    assert "LANGUAGE OVERRIDE" in build_native_youtube_prompt(language="EN")


# ── the wiring must not rot ──────────────────────────────────────────


def test_task_factory_persists_the_options():
    """The four options must reach the ai_tasks row, or the worker can't see them."""
    src = YT_PY.read_text()
    factory = src.split("def create_youtube_md_task", 1)[1].split("\ndef ", 1)[0]

    for column in ("template_id", "language", "length", "coverage_data"):
        assert column in factory, f"{column} not persisted by create_youtube_md_task"

    # mode rides inside coverage_data as JSON, exactly like YouTubeZen does.
    assert '"mode"' in factory


def test_worker_reads_the_options_back():
    """The worker runs in a thread later, so it must re-read the row."""
    src = YT_PY.read_text()
    assert "def _read_task_options" in src
    worker = src.split("def _run_youtube_task", 1)[1]
    assert "_read_task_options" in worker
    assert "build_native_youtube_prompt" in worker


def test_task_options_splat_into_the_composer():
    """The dict the worker splats must match the composer's signature.

    A textual check is not enough here: `_read_task_options` reads the
    `length` COLUMN but the composer takes a `depth` PARAMETER. Getting
    that name wrong raises TypeError inside the worker thread and fails
    every YouTube task — with no trace in the request, because the thread
    is long gone by the time the response is sent.
    """
    import inspect

    from ai.notebooklm import youtube as youtube_mod

    params = set(inspect.signature(youtube_mod.build_native_youtube_prompt).parameters)

    # Every key the composer accepts must be spelled the same way in the
    # dict literal that _read_task_options returns.
    src = YT_PY.read_text()
    body = src.split("def _read_task_options", 1)[1].split("\n\ndef ", 1)[0]
    returned = set(re.findall(r'^\s+"(\w+)":', body, re.M))
    assert returned == params, f"option keys {returned} != composer params {params}"


def test_default_row_composes_to_the_untouched_prompt(monkeypatch):
    """A row written before #13 has no options — it must still run unchanged."""
    from ai.notebooklm import youtube as youtube_mod

    monkeypatch.setattr(
        youtube_mod,
        "query_one",
        lambda *a, **k: {"template_id": "", "language": None, "length": None,
                        "coverage_data": None},
    )
    opts = youtube_mod._read_task_options("legacy-task")
    assert youtube_mod.build_native_youtube_prompt(**opts) is YOUTUBE_TO_MARKDOWN_PROMPT


def test_options_survive_a_round_trip_through_the_row():
    """Options written by create_youtube_md_task must compose the same prompt."""
    from ai.notebooklm import youtube as youtube_mod

    row = {
        "template_id": "tutorial",
        "language": "en",
        "length": "detailed",
        "coverage_data": '{"url": "https://youtu.be/x", "mode": "por_tema"}',
    }
    monkey = youtube_mod
    original = monkey.query_one
    monkey.query_one = lambda *a, **k: row
    try:
        opts = youtube_mod._read_task_options("task-1")
    finally:
        monkey.query_one = original

    prompt = youtube_mod.build_native_youtube_prompt(**opts)
    assert "ORGANISATION OVERRIDE" in prompt
    assert "LENGTH OVERRIDE" in prompt
    assert "LANGUAGE OVERRIDE" in prompt


def test_html_path_keeps_its_fixed_prompt():
    """HTML is out of scope for #13 — it must not be dragged along."""
    src = YT_PY.read_text()
    assert "def youtube_to_html(url: str) -> str:" in src
    assert "_add_youtube_and_ask(url, YOUTUBE_TO_HTML_PROMPT)" in src


def test_route_forwards_the_four_options():
    src = ROUTES_PY.read_text()
    route = src.split("def youtube_to_markdown", 1)[1].split("\n@bp.route", 1)[0]
    for field in ("template_id", "depth", "mode", "language"):
        assert field in route, f"{field} not read by the route"


def test_client_forwards_the_options():
    src = AI_NOTEBOOKLM_JS.read_text()
    fn = src.split("async function youtubeToMd", 1)[1].split("\n  }", 1)[0]
    for field in ("template_id", "depth", "mode", "language"):
        assert field in fn, f"{field} not sent by youtubeToMd"


def test_dialog_shows_the_controls_for_both_providers():
    """The Phase 64 gate that hid them for NotebookLM must be gone."""
    src = AI_JS.read_text()
    dialog = src.split("function _showYoutubeDialog", 1)[1]

    # No control may sit behind a nbNative-only condition any more.
    assert "${!nbNative ?" not in dialog

    for control in ("yt-templates", "kp-depth-btn", "kp-mode-btn", "kp-lang-btn"):
        assert control in dialog, f"{control} missing from the dialog"


def test_native_submit_still_sends_the_options():
    """Un-hiding the controls is useless unless the submit forwards them."""
    src = AI_JS.read_text()
    submit = src.split("function _launchGeneration", 1)[1]
    native = submit.split("if (nbNative)", 1)[1].split("return;", 1)[0]

    assert "gen.youtubeToMd" in native
    for opt in ("template:", "depth:", "mode:", "language:"):
        assert opt in native, f"{opt} not passed to youtubeToMd"


def test_language_is_not_silently_dropped():
    """Mapping every language to 'auto' would make the buttons decorative."""
    src = AI_JS.read_text()
    native = src.split("if (nbNative)", 1)[1].split("return;", 1)[0]
    assert 'language: selectedLang,' in native
    assert 'language: selectedLang === "en" ? "en" : "auto"' not in native
