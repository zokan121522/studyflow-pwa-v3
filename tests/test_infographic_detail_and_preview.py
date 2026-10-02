"""Tests for the infographic config's detail level and its result thumbnail.

Two things a NotebookLM infographic could not do, both for the same reason:
the plumbing already existed and the user just could not reach it.

**Depth.** `generateInfographic` has always posted a `detail_level`, and
`notebooklm_content.py` has always validated it against exactly three values
(`concise` | `standard` | `detailed`). But `openInfographicConfig` only offered
a style and a language, so the prompt NotebookLM itself asks the user for was
unreachable from the UI and every request silently fell back to "standard".
Adding a third option to a modal is the kind of change that breaks nothing
loudly — it just quietly does the wrong thing if the ids drift from the
backend's, which is why the ids are pinned here against the backend's own
table rather than against a copy of it.

**Thumbnail.** An infographic's `result_content` is a bare image URL, so the
progress modal had nothing to show but "✅ Tarea completada": the user was
asked to trust a NotebookLM run they could not look at before committing it to
a block. The preview only fires for `format === "infographic"` and only for a
same-origin image path — an unfiltered interpolation of a task-row value into
`innerHTML` would be the obvious mistake, and markdown/audio results are prose
that would be nonsense inside an `<img>`.

The error path matters more than it looks: a broken thumbnail must never look
like a failed task, because the image is already saved and Insert still works.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MODALS = ROOT / "frontend/features/studyflow/ai-modals.js"
TASKS = ROOT / "frontend/features/studyflow/ai-tasks.js"
CSS = ROOT / "frontend/features/studyflow/ai.css"
CALLER = ROOT / "frontend/features/studyflow/ai-notebooklm.js"
ROUTES = ROOT / "backend/routes/notebooklm_content.py"

modals = MODALS.read_text(encoding="utf-8")
tasks = TASKS.read_text(encoding="utf-8")
css = CSS.read_text(encoding="utf-8")
caller = CALLER.read_text(encoding="utf-8")
routes = ROUTES.read_text(encoding="utf-8")


# ── depth: the modal's ids must be the backend's ids ──────────────────────

def _modal_depth_ids() -> set[str]:
    block = modals.split("const INFO_DEPTHS = [", 1)[1].split("]", 1)[0]
    return set(re.findall(r'id:\s*"([^"]+)"', block))


def _backend_depth_ids() -> set[str]:
    m = re.search(r'"detail_level":\s*"([a-z]+)"\s*\|\s*"([a-z]+)"\s*\|\s*"([a-z]+)"', routes)
    assert m, "could not find the detail_level union in notebooklm_content.py"
    return set(m.groups())


def test_modal_depths_are_exactly_the_backends():
    """The ids travel unchanged from the modal to the prompt, so they must match."""
    assert _modal_depth_ids() == _backend_depth_ids()


def test_default_depth_is_standard_everywhere():
    """'standard' is the backend's own default, so the card must start on it."""
    assert 'd.id === "standard" ? "selected"' in modals
    assert '(data.get("detail_level") or "standard")' in routes
    assert 'params.detail_level || "standard"' in caller


def test_modal_submits_detail_level():
    assert "onSubmit({ style, language: lang, detail_level: detailLevel })" in modals
    # The submit handler is the *last* data-aimodal-submit in the file: the
    # first one is the button in the modal template.
    handler = modals.rsplit("data-aimodal-submit", 1)[1]
    assert '|| "standard"' in handler, "depth must fall back to the backend default"


def test_depth_grid_is_rendered_and_prefilled():
    assert 'id="inf-depth-grid"' in modals
    assert "Nivel de detalle" in modals
    # The style grid is exclusive per-grid, so a shared helper replaced the two
    # near-identical loops; both grids must still be wired.
    assert modals.count('_wireExclusive("#') == 2


# ── thumbnail: narrow, safe, and non-blocking on failure ──────────────────

def test_preview_is_hooked_at_completion():
    assert "_showResultPreview(finalContent, format)" in tasks
    # Must be before the insert button is resolved, so the user can see it.
    body = tasks.split("_showResultPreview(finalContent, format)", 1)[1]
    assert "stream-insert-btn" in body.split("const onDone", 1)[0] or "insBtn" in body


def _preview_body() -> str:
    """Just the _showResultPreview function, so assertions cannot leak into
    unrelated format handling elsewhere in ai-tasks.js."""
    return tasks.split("function _showResultPreview", 1)[1].split("\n  }", 1)[0]


def test_preview_only_fires_for_infographics():
    body = _preview_body()
    assert 'format === "infographic"' in body
    # Other formats' results are prose: rendering them in an <img> is nonsense.
    for other in ("audio", "markdown", "summary", "test", "grammar", "video"):
        assert f'format === "{other}"' not in body


def test_preview_url_must_be_a_same_origin_image():
    """The value is interpolated into innerHTML, so the guard is load-bearing."""
    guard = re.search(r"const isImageUrl = .*?;", tasks, re.S)
    assert guard, "isImageUrl guard not found"
    # The JS regex literal escapes its slashes: /^\/api\/[\w/-]+\.(png|...)$/i
    assert r"^\/api\/" in guard.group(0), "URL must be anchored to a same-origin path"
    assert "png|jpg|jpeg|gif|webp" in guard.group(0)
    # Anchored at both ends: no prefix tricks, no query/hash smuggling.
    # The closing anchor sits immediately before the flags: ...webp)$/i
    assert re.search(r"test\(value\)", guard.group(0))
    assert re.search(r"\$/i\.test", guard.group(0)), "pattern must be end-anchored"
    assert re.search(r"\^\\\/api", guard.group(0)), "pattern must be start-anchored"


def test_preview_builds_img_element_not_markup():
    assert 'document.createElement("img")' in tasks
    assert "box.appendChild(img)" in tasks
    # innerHTML must only ever be the static error string.
    for line in tasks.split("_showResultPreview", 1)[1].split("\nfunction", 1)[0].splitlines():
        if "innerHTML" in line:
            assert "value" not in line and "finalContent" not in line, (
                f"untrusted value reaches innerHTML: {line.strip()}"
            )


def test_broken_preview_does_not_hide_the_image():
    """A dead thumbnail must not read as a failed task — Insert still works."""
    assert 'img.addEventListener("error"' in tasks
    assert "la imagen sigue guardada" in tasks


def test_preview_is_styled_and_bounded():
    assert ".sf-ai-preview" in css
    assert "max-height" in css.split(".sf-ai-preview img", 1)[1].split("}", 1)[0]
    assert ".sf-ai-preview-err" in css


def test_preview_is_reset_when_a_new_task_starts():
    """The modal is reused, so a stale thumbnail would be read as the new result.

    Regression test for a real bug: generating an infographic and then starting
    an unrelated task left the previous image sitting under the new task's log,
    so a still-running task looked already finished. A leftover "could not load"
    note was worse — it blamed a task that never had a preview at all.
    """
    assert "function _resetResultPreview" in tasks
    body = tasks.split("function _resetResultPreview", 1)[1].split("\n  }", 1)[0]
    assert 'box.innerHTML = ""' in body
    assert 'box.style.display = "none"' in body
    # ...and it must be called on task start, before the new task shows anything.
    start = tasks.split("function startStreamPoll", 1)[1].split("const meta", 1)[0]
    assert "_resetResultPreview()" in start


def test_preview_container_exists_in_the_modal():
    assert 'id="sf-ai-preview"' in tasks
