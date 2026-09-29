"""Layout guards for the studyflow sidebar's drag-to-reorder (V3-DND #12).

Static assertions, like the rest of this suite: there is no browser in CI,
so these check the CSS/JS *source* rather than computed layout. What they can
catch is the regression that actually happened — a declaration being dropped
or a template changing shape — which is what bit us.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

COURSES_CSS = ROOT / "frontend" / "features" / "studyflow" / "courses.css"
BLOCKS_CSS = ROOT / "frontend" / "features" / "studyflow" / "courses-sidebar-blocks.css"
SIDEBAR_JS = ROOT / "frontend" / "features" / "studyflow" / "courses-sidebar.js"
SW_JS = ROOT / "frontend" / "sw.js"


def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _declarations(css: str, selector: str) -> str:
    """Return the declaration block of an EXACT selector, comments stripped.

    Exact match on `selector {` so `.course-item` never picks up
    `.course-item:hover` or `.course-item.dragging`.
    """
    flat = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", flat)
    assert m, f"selector not found: {selector}"
    return m.group(1)


def test_course_card_is_a_flex_row():
    """The regression: the grip is a direct child of the course card.

    `.drag-handle` is an inline <span> and `.ci-body` is a block <div>, so in
    a non-flex card the div forced a line break and the grip rendered on its
    own line ABOVE the title — making every course card about a line taller
    than its topic/block siblings, which were already flex rows.
    """
    decls = _declarations(_src(COURSES_CSS), ".course-item")
    assert re.search(r"display:\s*flex", decls), (
        "el grip quedaría en su propia línea sobre el título"
    )
    # flex-start, not the default stretch nor center: on an expanded card the
    # body is several lines tall and `center` would float the grip down the
    # middle of the card instead of keeping it on the title's line.
    assert re.search(r"align-items:\s*flex-start", decls)


def test_topic_and_block_rows_stay_flex():
    """The rows that were already correct — the course card was the odd one
    out. Pin them so the asymmetry cannot silently return."""
    blocks = _src(BLOCKS_CSS)
    for selector in (".topic-item", ".block-item"):
        decls = _declarations(blocks, selector)
        assert re.search(r"display:\s*flex", decls), f"{selector} perdió el flex"


def test_course_body_can_shrink_so_long_titles_ellipsise():
    """Once the card is a flex row, `.ci-body` becomes a flex item. Without
    `flex: 1` it sizes to content and `.ci-title-text` cannot ellipsise."""
    decls = _declarations(_src(COURSES_CSS), ".ci-body")
    assert re.search(r"flex:\s*1", decls)
    assert re.search(r"min-width:\s*0", decls)


def test_grip_alignment_nudge_is_scoped_to_the_course_card():
    """The grip is a 10px glyph next to a ~19.5px title line, so it needs a
    few px of optical offset. It must be scoped to `.course-item`: the grips in
    `.topic-header` and `.block-item` are already vertically centred by their
    flex parents, and a global margin-top would knock those out of line."""
    blocks = _src(BLOCKS_CSS)
    assert re.search(
        r"\.course-item\s*>\s*\.drag-handle\s*\{[^}]*margin-top",
        re.sub(r"/\*.*?\*/", "", blocks, flags=re.S),
    )
    # A bare `.drag-handle { margin-top }` would hit all three rows.
    grip = _declarations(blocks, ".drag-handle")
    assert "margin-top" not in grip, "afecta a los grips ya centrados"


def test_grip_and_body_are_siblings_in_the_template():
    """Documents WHY the card must be flex: the grip is a sibling of the
    block-level body, not nested inside the flex `.ci-title` row."""
    js = re.sub(r"//.*", "", _src(SIDEBAR_JS))
    m = re.search(r'class="course-item[^"]*"[^>]*>(.*?)class="ci-body"', js, re.S)
    assert m, "no se encuentra el grip como hermano de .ci-body"
    assert "drag-handle" in m.group(1)


def test_course_css_reaches_installed_pwas():
    """Both files are precached, so without a cache bump an installed PWA
    keeps serving the broken layout even though the server has the fix."""
    sw = _src(SW_JS)
    precache = sw.split("PRECACHE_ASSETS = [")[1].split("\n];")[0]
    for path in ("/features/studyflow/courses.css",
                 "/features/studyflow/courses-sidebar-blocks.css"):
        assert f"'{path}'" in precache, f"{path} fuera del precache"
