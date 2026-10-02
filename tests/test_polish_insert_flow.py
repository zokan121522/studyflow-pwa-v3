r"""Tests for the five polish items reported after the #13 NotebookLM controls.

None of these five failures showed up as an exception. They were all
"the UI is technically working but visibly wrong" bugs, which is exactly
the class a syntax check and the existing suite miss:

1. **Nav not refreshed after insert.** `blocks-changed` only called
   `updateCenter()`. The outline is what makes an inserted block
   discoverable, so the course looked unchanged until something else
   happened to re-render the tree. Worse, the tree does not read blocks
   from `fetchCourses()` — it reads `STATE._expandedCourseTopics`, filled
   only by `fetchCourseDetail()` — so refetching the course list alone
   re-renders the SAME stale block list.

2. **Status banner stuck on screen.** Success messages were written with
   `isPersistent = true` and nothing ever removed them.

3. **Generic block title.** A launch from the topic toolbar has no source
   block, so the title fell back to the tool name ("🤖 NotebookLM").

4. **Double click to rename a separator** — fired by accident while
   selecting text, and duplicated an action the ⋮ menu already offers.

5. **Preview blank on the second edit of a block.** `attachLivePreview`
   guarded idempotency with a marker on the WRAPPER, but the caller
   replaces the wrapper's innerHTML on every entry into edit mode. The
   fresh textarea/preview were never wired and never rendered, and the
   scroll sync silently died with them. The first edit of a block works;
   every later one of the SAME block comes up blank.

These are static/wiring tests. They read the source and assert the shape of
the fix; #5's runtime behaviour is additionally covered by a DOM test.
"""

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

COURSES_JS = ROOT / "frontend" / "features" / "studyflow" / "courses.js"
SIDEBAR_JS = ROOT / "frontend" / "features" / "studyflow" / "courses-sidebar.js"
AI_JS = ROOT / "frontend" / "features" / "studyflow" / "ai.js"
AI_TASKS_JS = ROOT / "frontend" / "features" / "studyflow" / "ai-tasks.js"
MD_EDITOR_JS = ROOT / "frontend" / "features" / "studyflow" / "markdown-editor.js"


# ── 1. the nav must refresh on blocks-changed ───────────────────────


def test_blocks_changed_refreshes_the_sidebar():
    src = COURSES_JS.read_text()
    handler = src.split("function _onBlocksChanged", 1)[1].split("\n  }", 1)[0]
    assert "_refreshSidebarNav" in handler, "sidebar not refreshed on blocks-changed"
    # Both halves must be kicked off, and neither may gate the other. Comments
    # are stripped first — the prose in this handler talks about awaiting.
    code = re.sub(r"//[^\n]*", "", handler)
    assert "updateCenter()" in code
    assert "await" not in code, "handler must stay fire-and-forget"


def test_sidebar_refresh_refetches_the_course_detail():
    """fetchCourses() alone re-renders the tree with the SAME stale blocks."""
    src = COURSES_JS.read_text()
    fn = src.split("async function _refreshSidebarNav", 1)[1].split("\n  }", 1)[0]

    # The tree reads STATE._expandedCourseTopics, so that is what must be
    # refilled — from fetchCourseDetail, with the cache invalidated first.
    assert "fetchCourseDetail(" in fn
    assert "clearDetailCache()" in fn
    assert "_expandedCourseTopics" in fn
    assert "renderCourseTree" in fn


def test_sidebar_refresh_keeps_the_scroll_position():
    src = COURSES_JS.read_text()
    fn = src.split("async function _refreshSidebarNav", 1)[1].split("\n  }", 1)[0]
    assert "scrollTop" in fn, "re-rendering the tree resets scroll to the top"


def test_sidebar_refresh_only_runs_for_the_open_course():
    src = COURSES_JS.read_text()
    fn = src.split("async function _refreshSidebarNav", 1)[1].split("\n  }", 1)[0]
    assert "expandedCourseId" in fn, "must not refetch every course on every change"


def test_update_selection_is_called_with_the_element():
    """updateSelection(leftEl, STATE) — STATE alone silently no-ops."""
    src = COURSES_JS.read_text()
    fn = src.split("async function _refreshSidebarNav", 1)[1].split("\n  }", 1)[0]
    assert re.search(r"updateSelection\(\s*leftEl\s*,", fn), \
        "updateSelection needs the leftEl as first argument"


# ── 2. the status banner must not stick ─────────────────────────────


def test_clear_status_exists_and_is_exported():
    src = AI_JS.read_text()
    assert "function _clearStatus" in src
    exported = src.split("return {", 1)[1]
    assert "_clearStatus" in exported, "_clearStatus must reach window.App.AI"


def test_clear_status_cancels_the_pending_hide_timer():
    src = AI_JS.read_text()
    fn = src.split("function _clearStatus", 1)[1].split("\n  }", 1)[0]
    assert "clearTimeout" in fn
    assert 'style.display = "none"' in fn


def test_success_banner_is_cleared_after_insert():
    src = AI_JS.read_text()
    fn = src.split("async function _onContentSuccess", 1)[1].split("\n  async function", 1)[0]
    assert "_clearStatus" in fn, "the 'generado' banner is left up forever"


def test_stream_banner_is_cleared_too():
    """ai-tasks.js writes its own persistent banner on the same block."""
    src = AI_TASKS_JS.read_text()
    assert "_clearStatus" in src, "the stream 'Generado' banner never goes away"


# ── 3. the block title must describe the content ────────────────────


def test_title_prefers_the_stored_video_title():
    src = AI_JS.read_text()
    fn = src.split("async function _blockTitleForTask", 1)[1].split("\n  }", 1)[0]
    assert "coverage" in fn and "video_title" in fn, \
        "the YouTube worker's stored video_title is ignored"


def test_title_falls_back_to_the_first_heading():
    src = AI_JS.read_text()
    fn = src.split("async function _blockTitleForTask", 1)[1].split("\n  }", 1)[0]
    assert re.search(r"match\(.*#\{1,2\}", fn), "no first-heading fallback"


def test_insert_uses_the_content_title():
    src = AI_JS.read_text()
    fn = src.split("async function _onContentSuccess", 1)[1].split("\n  async function", 1)[0]
    assert "_blockTitleForTask" in fn
    assert "let sourceTitle = label;" not in fn, "generic label is still the default"


def test_title_helper_receives_the_course_id():
    """It reads the source block off the course, so it needs the id."""
    src = AI_JS.read_text()
    sig = src.split("async function _blockTitleForTask", 1)[1].split(")", 1)[0]
    assert "courseId" in sig
    call = src.split("const sourceTitle = await _blockTitleForTask", 1)[1].split("\n", 1)[0]
    assert call.count(",") >= 3, "call must pass (task, courseId, blockId, fallback)"


# ── 4. no double click to rename ─────────────────────────────────────


def test_no_dblclick_in_the_block_item():
    src = SIDEBAR_JS.read_text()
    item = src.split("function _renderBlockItem", 1)[1].split("\n  }", 1)[0]
    assert "ondblclick" not in item, "double click still renames a separator"
    assert "dblClick" not in item, "dead dblClick variable left behind"


def test_rename_is_still_reachable_from_the_menu():
    """Removing the double click must not remove the ability to rename."""
    src = SIDEBAR_JS.read_text()
    item = src.split("function _renderBlockItem", 1)[1].split("\n  }", 1)[0]
    assert "_inlineRenameBlockTitle" in item, "separator title can no longer be renamed"


def test_no_dblclick_anywhere_in_the_frontend():
    for path in (ROOT / "frontend" / "features" / "studyflow").glob("*.js"):
        assert "ondblclick" not in path.read_text(), f"{path.name} still uses ondblclick"


# ── 5. the preview must survive a re-edit ───────────────────────────


def test_attach_live_preview_does_not_guard_on_the_wrapper():
    """The guard must be per NODE PAIR, not on the reused form element."""
    src = MD_EDITOR_JS.read_text()
    fn = src.split("function attachLivePreview", 1)[1].split("\n  }", 1)[0]
    assert 'formEl.dataset.sfMdLive === "1"' not in fn, \
        "wrapper-level guard blanks the preview on every re-edit"
    assert "_sfMdWired" in fn, "no per-node wiring guard"


def test_attach_live_preview_still_renders_initially():
    src = MD_EDITOR_JS.read_text()
    fn = src.split("function attachLivePreview", 1)[1].split("\n  }", 1)[0]
    assert "renderMd()" in fn


def test_scroll_sync_still_wired_both_ways():
    src = MD_EDITOR_JS.read_text()
    fn = src.split("function attachLivePreview", 1)[1].split("\n  }", 1)[0]
    assert fn.count('addEventListener("scroll"') == 2, \
        "editor↔preview scroll sync must survive in both directions"
    assert "_mdSyncing" in fn, "anti-loop guard lost"


def test_split_view_markup_still_two_columns():
    src = MD_EDITOR_JS.read_text()
    form = src.split("function editForm", 1)[1].split("\n  }", 1)[0]
    assert "md-edit-split" in form
    assert "md-editor" in form and "md-preview" in form


def test_split_css_is_a_real_two_column_flex():
    css = (ROOT / "frontend" / "features" / "studyflow" / "studyflow-editor.css").read_text()
    split = css.split(".md-edit-split {", 1)[1].split("}", 1)[0]
    assert "display: flex" in split
    for sel in (".md-edit-split .md-editor", ".md-edit-split .md-preview"):
        block = css.split(sel + " {", 1)[1].split("}", 1)[0]
        assert "flex: 1" in block, f"{sel} does not take half the width"


def test_the_container_owns_the_height_not_the_panes():
    """Both panes must scroll independently, or the scroll sync is dead code.

    The preview is a flex sibling, so when the panes sized themselves to
    their content the preview got stretched to the textarea's full height.
    Neither pane then ever overflowed, which left the ratio-based sync in
    attachLivePreview() with nothing to sync.
    """
    css = (ROOT / "frontend" / "features" / "studyflow" / "studyflow-editor.css").read_text()
    split = css.split(".md-edit-split {", 1)[1].split("}", 1)[0]
    assert "height:" in split, "the split container must cap the pane height"

    for sel in (".md-edit-split .md-editor", ".md-edit-split .md-preview"):
        block = css.split(sel + " {", 1)[1].split("}", 1)[0]
        assert "height: 100%" in block, f"{sel} must fill the container, not grow it"
        assert "overflow-y: auto" in block, f"{sel} needs its own scrollbar"


def test_the_textarea_cannot_be_resized_away_from_the_container():
    """resize:vertical regrows the whole split and re-breaks the fixed height."""
    css = (ROOT / "frontend" / "features" / "studyflow" / "studyflow-editor.css").read_text()
    editor = css.split(".md-edit-split .md-editor {", 1)[1].split("}", 1)[0]
    assert "resize: none" in editor


def test_stacked_mobile_split_keeps_both_panes_scrollable():
    css = (ROOT / "frontend" / "features" / "studyflow" / "studyflow-editor.css").read_text()
    mobile = css.split("@media (max-width: 720px)", 1)[1]
    for sel in (".md-edit-split .md-editor", ".md-edit-split .md-preview"):
        block = mobile.split(sel + " {", 1)[1].split("}", 1)[0]
        assert "height: 50%" in block, f"{sel} would push the preview off-screen"
