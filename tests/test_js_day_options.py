"""The panel's day options must match the backend's VALID_DAYS.

calendar_import.window.VALID_DAYS and routes/calendar.py._VALID_DAYS must
agree, or the panel offers a value the API rejects — the user clicks "2
meses" and gets a bare 400. The JS keeps its own copy as a literal to avoid
a round trip, so this test is what stops the three copies drifting.

Asserted against the real files, not against a re-declared tuple: a test that
repeats the constant proves nothing.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from calendar_import.window import VALID_DAYS  # noqa: E402

JS = ROOT / "frontend" / "features" / "agenda" / "agenda-calendars.js"
HTML = ROOT / "frontend" / "index.html"
CALENDAR_ROUTE = ROOT / "backend" / "routes" / "calendar.py"


def route_valid_days():
    src = CALENDAR_ROUTE.read_text()
    m = re.search(r"_VALID_DAYS\s*=\s*\(([^)]*)\)", src)
    assert m, "_VALID_DAYS not found in routes/calendar.py"
    return tuple(int(x) for x in m.group(1).split(",") if x.strip())


def js_valid_days():
    src = JS.read_text()
    m = re.search(r"var VALID_DAYS\s*=\s*\[([^\]]*)\]", src)
    assert m, "VALID_DAYS literal not found in the panel JS"
    return tuple(int(x) for x in m.group(1).split(",") if x.strip())


def html_day_values():
    src = HTML.read_text()
    return tuple(
        int(v) for v in re.findall(r'name="cal-days"\s+value="(\d+)"', src)
    )


def test_sixty_is_offered():
    assert 60 in VALID_DAYS


def test_backend_window_and_route_agree():
    assert route_valid_days() == VALID_DAYS, (
        f"routes/calendar.py offers {route_valid_days()} but the window "
        f"module allows {VALID_DAYS}"
    )


def test_panel_js_matches_the_backend():
    assert js_valid_days() == VALID_DAYS, (
        f"the panel would offer {js_valid_days()} and the API accepts "
        f"{VALID_DAYS} — a mismatch means a click turns into a 400"
    )


def test_panel_html_radio_values_match():
    assert html_day_values() == VALID_DAYS


def test_the_panel_has_a_two_months_label():
    """A bare 60 means nothing to the user; the tooltip must say it."""
    assert 'title="Dos meses"' in HTML.read_text()


def test_no_day_option_is_missing_from_the_markup():
    for days in VALID_DAYS:
        assert f'name="cal-days" value="{days}"' in HTML.read_text()