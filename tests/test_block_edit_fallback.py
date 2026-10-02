"""Guards for the block editor's not-found path.

The editor used to bail out on a bare `return` when a block's id was missing
from its topic's list. From the user's side that is a pencil button that
does nothing, which reads as "the app won't let me edit this" — and the only
way out was a full page reload. The fallback to the course's flat block list
and the visible notice are both load-bearing, so both are pinned here.

Static source assertions, in line with the rest of the suite: CI has no
browser, and what regressed was the *source*, not the runtime behaviour.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

BLOCKS_JS = ROOT / "frontend" / "features" / "studyflow" / "courses-blocks.js"
API_JS = ROOT / "frontend" / "features" / "studyflow" / "courses-api.js"


def test_editor_falls_back_to_the_course_block_list():
    """A block missing from its topic list is looked up course-wide."""
    src = BLOCKS_JS.read_text(encoding="utf-8")
    assert re.search(r"fetchCourseDetail\(\s*domCourseId\s*\)", src), (
        "the editor must fall back to the course's flat block list before "
        "giving up — that is the only way a block whose topic_id disagrees "
        "with the topic it renders under can still be edited. It must use the "
        "DOM-derived course id, not the stale captured one."
    )
    assert re.search(r"detail\.blocks\s*\|\|\s*\[\]", src), (
        "the fallback must read detail.blocks defensively; a missing key here "
        "throws a TypeError instead of showing the notice"
    )


def test_missing_block_shows_a_notice():
    """No bare return: the user has to be told, not left guessing."""
    src = BLOCKS_JS.read_text(encoding="utf-8")
    assert "_showBlockNotice" in src, (
        "the not-found path must surface a message; silently doing nothing "
        "is what made this bug so hard to report"
    )
    helper = re.search(r"function _showBlockNotice\b.*?\n  \}", src, re.S)
    assert helper, "_showBlockNotice must exist as a named helper, not inline"
    body = helper.group(0)
    assert "escHtml(msg)" in body, (
        "the notice interpolates a message; escape it like the rest of the "
        "renderer does"
    )
    assert 'querySelector(".sf-td-edit-form")' in body, (
        "this view has no [data-ai-status] node (that lives in ai.js), so the "
        "card's edit slot is the message area"
    )


def test_the_notice_helper_is_not_ai_status():
    """Guard the trap: _showStatus in ai.js is a different, unreachable one."""
    src = BLOCKS_JS.read_text(encoding="utf-8")
    # Comments legitimately mention data-ai-status to explain why it is not
    # used, so match an actual selector call rather than the bare name.
    assert not re.search(r"""querySelector\(\s*[\['"]\[data-ai-status""", src), (
        "ai.js's _showStatus targets [data-ai-status], which this view never "
        "renders — it would fall through to a toast that does not exist here"
    )
    assert not re.search(r"(?<![\w.])_showStatus\s*\(", src), (
        "_showStatus is private to ai.js and not on window.App; calling it "
        "here would be a ReferenceError"
    )
    # Negative lookbehind for `function ` matters: the helper's own signature
    # is `function _showBlockNotice(blockEl, msg)`, which a bare call-site
    # regex would happily match — so deleting the real call would still pass.
    assert re.search(r"(?<!function )_showBlockNotice\(\s*blockEl\s*,", src), (
        "the notice helper must be *called* from the not-found path; matching "
        "the name alone is satisfied by its own definition, so removing the "
        "call site would go unnoticed"
    )

    assert not re.search(r"function\s+_showBlockNotice\s*\(\s*blockEl\b[^)]*\)\s*\{\s*\n\s*return", src), (
        "the helper must actually render something, not return immediately"
    )


def test_handlers_derive_course_from_the_dom_not_the_closure():
    """The actual root cause: one listener per container, stale captured ids.

    _attachBlockHandlers is idempotent — it attaches a single delegated
    listener and closes over the courseId/topicId current at that moment.
    Navigate to another course and the listener is not re-attached, so every
    id it holds points at the previous course: the pencil looks the block up
    in the wrong topic, misses, and returns silently. Save and delete would
    then PATCH/DELETE against the wrong course.
    """
    src = BLOCKS_JS.read_text(encoding="utf-8")
    assert "function _ctxFrom(" in src, (
        "there must be a helper that resolves the course/topic from the DOM at "
        "click time; without it the stale closure silently breaks every block "
        "action after the first navigation"
    )
    for name in ("editBtn", "saveBtn", "cancelBtn", "delBtn"):
        assert f"_ctxFrom({name}" in src, (
            f"the {name} handler must resolve its context from the DOM"
        )
    # The two writes are the ones that can damage data.
    assert not re.search(r"updateBlock\(courseId,", src), (
        "updateBlock with the captured courseId would write to the previous "
        "course after navigating away"
    )
    assert not re.search(r"deleteBlock\(courseId,", src), (
        "deleteBlock with the captured courseId would delete from the previous "
        "course after navigating away"
    )
    # Same trap, one helper further down: _embedPdfBlock writes through
    # updateBlock and addBlock, so it must be handed the DOM-derived ids
    # rather than reaching for the closure.
    embed = re.search(r"async function _embedPdfBlock\(.*?\n  \}", src, re.S)
    assert embed, "_embedPdfBlock must exist"
    body = embed.group(0)
    for call in ("updateBlock(", "addBlock("):
        for match in re.finditer(re.escape(call) + r"([A-Za-z_][A-Za-z0-9_]*)", body):
            arg = match.group(1)
            assert arg.startswith("courseId") is False or arg != "courseId", (
                f"{call} inside _embedPdfBlock must use the courseId parameter "
                "that is passed in from the DOM-resolved context, not the "
                "captured closure variable"
            )
    assert "_embedPdfBlock(ctxPdf.courseId, ctxPdf.topicId" in src, (
        "_embedPdfBlock must receive the DOM-derived course/topic ids"
    )


def test_scope_search_never_falls_back_to_the_whole_document():
    """A document-wide fallback picks up an unrelated card's block element."""
    src = BLOCKS_JS.read_text(encoding="utf-8")
    assert "||document).querySelector" not in src, (
        "`(btn.closest('.sf-block-card')||document).querySelector(...)` searches "
        "the entire page when the button is not inside a card, and silently "
        "resolves another block's element"
    )


def test_topic_detail_container_exposes_its_ids():
    """_ctxFrom reads topic/course from the container, so they must be there."""
    courses = (ROOT / "frontend" / "features" / "studyflow" / "courses.js").read_text(
        encoding="utf-8"
    )
    assert re.search(r'class="sf-topic-detail"\s+data-topic-id="\$\{topicId\}"', courses), (
        ".sf-topic-detail must carry data-topic-id/data-course-id, otherwise "
        "_ctxFrom falls back to the stale closure values it exists to replace"
    )


def test_fetch_course_detail_is_exported():
    """The fallback is useless if the import is a silent undefined."""
    api = API_JS.read_text(encoding="utf-8")
    assert re.search(r"^\s*fetchCourseDetail,\s*$", api, re.M), (
        "CoursesAPI must export fetchCourseDetail for the editor fallback"
    )
    blocks = BLOCKS_JS.read_text(encoding="utf-8")
    assert re.search(
        r"listTopicBlocks,\s*fetchCourseDetail,", blocks
    ), "courses-blocks.js must import fetchCourseDetail from CoursesAPI"
