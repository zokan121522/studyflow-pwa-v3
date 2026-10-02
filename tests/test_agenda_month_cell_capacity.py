"""Month grid: how many sessions a day cell can actually show.

The cell used to render at most three sessions and hide the rest behind a
"+N" badge. Growing the box alone fixed nothing, because the cap — not the
height — was what hid them: the cell stayed `overflow: hidden`, so a busy
day just looked empty. An intermediate attempt raised the cap to six and
added a scrollbar, which was worse than useless: with the cap in place
there was never more content than the cell's height, so the scrollbar was
dead code and the seventh session was unreachable.

The rule that holds now: render every session and let the cell's own
height decide the cut-off, scrolling for the rest.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MONTH_JS = ROOT / "frontend" / "features" / "agenda" / "agenda-timeline-month.js"
TIMELINE_CSS = ROOT / "frontend" / "features" / "agenda" / "agenda-timeline.css"

JS = MONTH_JS.read_text(encoding="utf-8")
CSS = TIMELINE_CSS.read_text(encoding="utf-8")


def _render_cell_sessions() -> str:
    start = JS.index("function renderCellSessions")
    return JS[start:start + 700]


def test_every_session_is_rendered():
    """A cap makes the scrollbar dead code and strands the overflow."""
    body = _render_cell_sessions()
    assert "slice(" not in body, (
        "renderCellSessions must not cap the list — capping is what made the "
        "scrollbar unreachable and hid the 7th session of a day"
    )
    assert "sessions.length" in body, "all sessions must be rendered"


def test_no_hidden_count_badge():
    """The "+N" badge reported content that could not be reached."""
    body = _render_cell_sessions()
    assert "tlm-overflow" not in body, (
        "the badge is redundant now: the cell scrolls instead of hiding rows"
    )


def test_cell_is_tall_enough_for_six_rows():
    """Six rows at ~14px + gaps + the day number needs ~110px."""
    rule = re.search(r"\.tlm-cell \{([^}]*)\}", CSS)
    assert rule, ".tlm-cell rule must exist"
    match = re.search(r"min-height:\s*(\d+)px", rule.group(1))
    assert match, ".tlm-cell must declare a min-height"
    assert int(match.group(1)) >= 110, (
        "the cell has to fit six rows; the old 80px clipped them"
    )


def test_session_list_scrolls_instead_of_clipping():
    """`overflow: hidden` on the list is what silently ate the extra rows."""
    rule = re.search(r"\.tlm-sessions \{([^}]*)\}", CSS)
    assert rule, ".tlm-sessions rule must exist"
    body = rule.group(1)
    assert re.search(r"overflow-y:\s*auto", body), (
        "the list must scroll — hidden is what made busy days look empty"
    )
    assert "overflow: hidden" not in body.replace("overflow-x: hidden", "")


def test_scrolling_does_not_steal_the_page_scroll():
    """The month grid sits inside a scrolling body; a cell that chains the
    scroll upward makes the whole page jump when you reach its end."""
    rule = re.search(r"\.tlm-sessions \{([^}]*)\}", CSS)
    assert re.search(r"overscroll-behavior:\s*contain", rule.group(1)), (
        "overscroll-behavior: contain keeps the scroll inside the day cell"
    )


def test_scrollbar_is_thin():
    """A default-width scrollbar steals space from an already narrow cell."""
    assert re.search(r"::-webkit-scrollbar \{ width: \d+px", CSS), (
        "the scrollbar must be styled thin — the cells are only a few columns wide"
    )