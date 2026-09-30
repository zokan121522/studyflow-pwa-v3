# Opening a topic from the body must unfold it in the nav.
#
# Two independent state flags drive the sidebar, and nothing connected them:
#   selectedTopicId — which topic the body is showing (set by handleTopicClick)
#   _expandedTopics — which topics are unfolded in the nav (only ever set by
#                     the sidebar's own chevron, and by drag-and-drop)
# So clicking a topic in the body set the first and left the second untouched:
# the nav kept the ▶ and display:none, and the blocks you had just opened
# looked missing from the outline. The blocks were in the DOM all along.

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIDEBAR = ROOT / "frontend/features/studyflow/courses-sidebar.js"
COURSES = ROOT / "frontend/features/studyflow/courses.js"


def _src(path):
    return path.read_text(encoding="utf-8")


def test_nav_exposes_a_single_expansion_helper():
    """One place decides expansion, so the chevron and the body cannot diverge."""
    src = _src(SIDEBAR)
    assert "function setTopicExpanded(topicId, expanded)" in src
    assert re.search(r"return \{[^}]*\bsetTopicExpanded\b", src, re.S), \
        "setTopicExpanded must be in the module's public API"


def test_expansion_helper_writes_the_flag_and_the_markup():
    """Flag alone leaves the nav stale; markup alone is lost on re-render."""
    src = _src(SIDEBAR)
    body = re.search(
        r"function setTopicExpanded\(topicId, expanded\) \{(.*?)\n  \}", src, re.S
    )
    assert body, "setTopicExpanded body not found"
    fn = body.group(1)
    assert "_expandedTopics[topicId] = !!expanded" in fn, "must set the state flag"
    assert "blocksDiv.style.display = expanded" in fn, "must show/hide .topic-blocks"
    assert "arrow.textContent = expanded" in fn, "must flip the ▶/▼ arrow"


def test_chevron_reuses_the_helper_instead_of_its_own_copy():
    """The chevron's inline version is what let the two paths drift apart."""
    src = _src(SIDEBAR)
    assert "setTopicExpanded(topicId, !currentlyExpanded)" in src
    # The duplicated logic must be gone, not merely supplemented.
    assert 'arrow.textContent = !expanded ? "▼" : "▶"' not in src, \
        "chevron still has its own copy of the DOM flip"
    assert "s._expandedTopics[topicId] = !expanded" not in src, \
        "chevron still writes the flag directly"


def test_opening_a_topic_from_the_body_expands_it():
    """The reported bug: body opens the topic, nav does not unfold it."""
    src = _src(COURSES)
    handler = re.search(
        r"async function handleTopicClick\(courseId, topicId\) \{(.*?)\n  \}", src, re.S
    )
    assert handler, "handleTopicClick not found"
    fn = handler.group(1)
    assert "CoursesSidebar.setTopicExpanded" in fn, \
        "handleTopicClick must expand the topic in the nav"
    assert re.search(r"setTopicExpanded\(topicId, true\)", fn), \
        "it must expand (true), not toggle — a toggle would fold an open topic"


def test_expansion_happens_before_the_centre_renders():
    """updateCenter() repaints; the nav has to be right by then, not after."""
    src = _src(COURSES)
    handler = re.search(
        r"async function handleTopicClick\(courseId, topicId\) \{(.*?)\n  \}", src, re.S
    )
    fn = handler.group(1)
    assert fn.index("setTopicExpanded") < fn.index("await updateCenter()"), \
        "expand the nav before the centre repaints"


def test_helper_does_not_touch_the_flag_when_absent():
    """
    A collapsed course has no .topic-item for the topic. setTopicExpanded
    still records the flag (so the next render unfolds it) and reports that
    the node was absent instead of throwing.
    """
    src = _src(SIDEBAR)
    body = re.search(
        r"function setTopicExpanded\(topicId, expanded\) \{(.*?)\n  \}", src, re.S
    )
    fn = body.group(1)
    assert "if (item)" in fn, "must tolerate a missing .topic-item"
    assert fn.index("s._expandedTopics[topicId] = !!expanded") < fn.index("if (item)"), \
        "the flag must be set even when the node is absent"
    assert fn.rstrip().endswith("return !!item;"), "must report whether it found the node"


def test_nothing_else_silently_depends_on_the_old_removed_flag_write():
    """Guards against re-introducing a second writer for _expandedTopics."""
    courses_dnd = _src(ROOT / "frontend/features/studyflow/courses-dnd.js")
    # DnD legitimately expands the topic it drops into; it must not fake the
    # chevron's markup, which only the helper is allowed to touch.
    assert "_expandedTopics[overTopic] = true" in courses_dnd
    assert "topic-arrow" not in courses_dnd, "DnD must not flip the arrow directly"
    assert "topic-blocks" not in courses_dnd, "DnD must not show/hide blocks directly"
