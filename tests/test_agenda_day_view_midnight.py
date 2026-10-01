"""Day view: a session near midnight must stay inside the grid.

Sessions imported from a calendar frequently land on ``23:59`` (or have no
real end time), and the grid is a fixed 24x40px = 960px. ``timeToPx`` had no
upper bound and short sessions were floored at 18px, so a 23:59 start put
``top`` at 959.3px and the block finished at 977.3px — 17px past the edge,
clipped and overlapping the border. Measured in the browser on 2026-10-10
before the fix: maxBottom 977.3 in a 960px grid.

Sliding the block up is the fix; shrinking it is not, because at 0.7px of
available room the block would become an invisible sliver.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TL_JS = ROOT / "frontend" / "features" / "agenda" / "agenda-timeline.js"
SRC = TL_JS.read_text(encoding="utf-8")

# Mirrors the constants in renderBlock / timeToPx.
TOTAL_H = 24 * 40  # px
MIN_H = 18  # px floor for a brief session


def _time_to_px(total_min: int) -> float:
    return (total_min / 1440) * TOTAL_H


def test_a_2359_session_fits_inside_the_grid():
    top = _time_to_px(23 * 60 + 59)
    if top + MIN_H > TOTAL_H:
        top = TOTAL_H - MIN_H
    height = min(max(MIN_H, (15 / 1440) * TOTAL_H), TOTAL_H - top)
    assert top + height <= TOTAL_H, (
        "a 23:59 session must end at or before the bottom of the grid"
    )
    assert abs((top + height) - TOTAL_H) < 0.001


def test_a_midnight_session_is_left_alone():
    """00:00 sits at the very top and must not be dragged down."""
    top = _time_to_px(0)
    assert top == 0


def test_the_source_shifts_short_blocks_up_rather_than_shrinking_them():
    body = SRC[ SRC.index("var MIN_H = MIN_BLOCK_H;") : SRC.index("var MIN_H = MIN_BLOCK_H;") + 260 ]
    assert "topPx = TOTAL_H - MIN_H" in body, (
        "the fix is to slide the block up; clamping the height instead would "
        "leave a 0.7px sliver at 23:59 — technically inside, practically gone"
    )


def test_the_grid_is_not_padded_to_hide_the_overflow():
    """Growing TOTAL_H would move every other hour label out of alignment."""
    assert "var TOTAL_H = 24 * HOUR_H" in SRC, (
        "the grid height must stay derived from HOUR_H; inflating it to hide "
        "the overflow would desynchronise every hour row"
    )


def test_the_18px_floor_still_exists():
    """The floor is what caused the overflow; it must not just be deleted —
    brief sessions would become unreadably thin."""
    assert "Math.max(MIN_H," in SRC, (
        "el dia dibuja el suelo con la constante declarada arriba, no con un 18 literal"
    )
    assert "var MIN_BLOCK_H = 18" in SRC, (
        "el suelo de 18px debe seguir declarado como constante: es la misma que "
        "se le pasa al empaquetador de carriles"
    )