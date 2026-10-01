"""Regression tests for the day timeline's single-scroll layout.

The day grid used to be capped with `.tl-body { max-height: 60vh; overflow-y:
auto }`. That nested a second scroll box inside the page's own scroll, so only
8.6 of the 24 hours were reachable: every session from 09:00 onwards sat below
the visible edge and the hour gutter stopped at 08.

These tests pin the fixed contract so the cap cannot quietly come back.
"""

import re
from pathlib import Path

import pytest

CSS = (
    Path(__file__).resolve().parents[1]
    / "frontend"
    / "features"
    / "agenda"
    / "agenda-timeline.css"
)
JS = (
    Path(__file__).resolve().parents[1]
    / "frontend"
    / "features"
    / "agenda"
    / "agenda-timeline.js"
)

HOUR_H = 40
TOTAL_H = 24 * HOUR_H  # 960px: the full day, no cropping


def rule_body(css_text, selector):
    """Return the declarations of the first rule matching `selector`."""
    pattern = re.escape(selector) + r"\s*\{([^}]*)\}"
    match = re.search(pattern, css_text)
    assert match, "regla %s no encontrada en %s" % (selector, CSS.name)
    return match.group(1)


@pytest.fixture(scope="module")
def css():
    return CSS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def js():
    return JS.read_text(encoding="utf-8")


def test_tl_body_does_not_cap_its_height(css):
    """.tl-body must not reintroduce a max-height scroll cap."""
    body = rule_body(css, ".tl-body")
    assert "max-height" not in body, (
        ".tl-body no debe tener max-height: reintroduce el scroll anidado que "
        "dejaba fuera de vista las 24 horas. Encontrado: %s" % body.strip()
    )


def test_tl_body_does_not_scroll_on_its_own(css):
    """.tl-body must not be its own scroll container."""
    body = rule_body(css, ".tl-body")
    assert "overflow-y" not in body, (
        ".tl-body no debe declarar overflow-y: el scroll único debe vivir en la "
        "página, no en la línea temporal. Encontrado: %s" % body.strip()
    )


def test_tl_body_is_still_a_flex_row(css):
    """Dropping the cap must not drop the row layout the grid relies on."""
    body = rule_body(css, ".tl-body")
    assert "display: flex" in body
    assert "position: relative" in body


def test_hour_gutter_never_compresses(css):
    """The gutter must keep its full height so hour labels stay aligned."""
    gutter = rule_body(css, ".tl-gutter")
    assert "flex-shrink: 0" in gutter, (
        ".tl-gutter necesita flex-shrink:0; si encoge, las horas se cortan. "
        "Encontrado: %s" % gutter.strip()
    )


def test_grid_height_covers_the_whole_day(js):
    """The grid is still emitted with an explicit full-day height."""
    assert "var HOUR_H = %d" % HOUR_H in js
    assert "var TOTAL_H = 24 * HOUR_H" in js
    assert "style=\"height:' + TOTAL_H + 'px;\">'" in js


def test_grid_is_marked_with_the_full_day_height(css):
    """The grid takes its natural height; only the gutter pins a width."""
    grid = rule_body(css, ".tl-grid")
    assert "max-height" not in grid
    assert "flex: 1" in grid


def test_semana_tambien_usa_el_scroll_unico(css):
    """The week view had the identical 60vh cap and is now fixed too.

    It was scoped out of the original day-view fix; the same reasoning applies
    because the structure is the same (gutter + columns inside a flex row).
    """
    body = rule_body(css, ".tlw-body")
    assert "max-height" not in body, (
        ".tlw-body no debe tener max-height: mismo scroll anidado que sufria "
        "la vista Dia. Encontrado: %s" % body.strip()
    )
    assert "overflow-y" not in body
    assert "display: flex" in body


def test_ninguna_vista_deja_scroll_anidado(css):
    """No timeline view may reintroduce a vh-based cap."""
    offenders = [
        line.strip()
        for line in css.splitlines()
        if "max-height: 60vh" in line
    ]
    assert not offenders, (
        "scroll anidado reintroducido en alguna vista: %s" % offenders
    )
