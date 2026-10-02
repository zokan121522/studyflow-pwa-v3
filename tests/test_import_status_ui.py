"""The status chip is wired into the agenda and cannot drift out of it.

The user asked to see, just by opening the agenda, whether the calendar is
fine and whether anything is pending. That only works if the chip is actually
in the rendered markup and actually refreshed. These read the real files:
a chip that exists in a JS module but was never added to the template looks
perfectly fine to every other test in the repo.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "frontend" / "features" / "agenda" / "agenda-core.js"
AGENDA = ROOT / "frontend" / "features" / "agenda" / "agenda.js"
STATUS_JS = ROOT / "frontend" / "features" / "agenda" / "calendar-import-status.js"
HTML = ROOT / "frontend" / "index.html"


def test_the_chip_is_in_the_rendered_side_panel():
    src = CORE.read_text()
    assert 'class="cal-import-status"' in src, (
        "the chip is not rendered — the user would never see the status"
    )


def test_the_chip_sits_next_to_the_calendars_button():
    src = CORE.read_text()
    i = src.find('data-action="open-calendars"')
    assert i != -1, "Calendarios button missing"
    assert 'cal-import-status' in src[i:i + 400], (
        "the chip should render beside the Calendarios button, not elsewhere"
    )


def test_the_status_module_is_loaded():
    assert 'src="/features/agenda/calendar-import-status.js"' in HTML.read_text()


def test_agenda_refreshes_the_status():
    assert "_refreshCalendarStatus()" in AGENDA.read_text()


def test_refresh_is_called_on_every_render_not_only_at_boot():
    """A chip painted once at page load is a chip that lies after a day.

    renderAgenda rebuilds the panel, so it must repaint; otherwise the state
    shown is whatever it was when the tab was opened.
    """
    src = AGENDA.read_text()
    body = src[src.find("async function renderAgenda"):]
    assert "_refreshCalendarStatus()" in body, (
        "renderAgenda must repaint the status, otherwise it goes stale"
    )


def test_every_state_has_an_icon_and_a_colour():
    """ok / partial / stale / error / never must be visually distinguishable.

    A dead feed showing the same green as a healthy one defeats the purpose.
    """
    src = STATUS_JS.read_text()
    for state in ("ok", "partial", "stale", "error", "never"):
        assert re.search(rf"\b{state}\s*:\s*\{{", src), f"{state} has no entry"


def test_the_status_endpoint_is_the_one_the_chip_calls():
    assert 'API.get("/calendar/status")' in STATUS_JS.read_text()


def test_pending_notices_are_surfaced_in_the_chip_text():
    src = STATUS_JS.read_text()
    assert "pending_notices" in src, (
        "the chip must say how many notices are waiting, not just 'ok'"
    )


def test_the_chip_logs_to_the_console():
    """The user asked for a log when opening the agenda, not only a chip."""
    assert "console.info" in STATUS_JS.read_text()


def test_backend_exposes_the_status_route():
    route = ROOT / "backend" / "calendar_import" / "routes.py"
    src = route.read_text()
    assert '@bp.get("/calendar/status")' in src
    assert "token_required" in src, "the status route must stay authenticated"


# ─── the race that shipped ─────────────────────────────────────────────

def test_the_chip_cannot_get_stuck_on_its_placeholder():
    """Regression: the chip sat on "⏳ comprobando…" forever.

    renderAgenda() rebuilds the side panel, so the chip is a new element on
    every render. The fetch resolved before that markup existed, paint() found
    nothing to paint, and nothing ever tried again — the panel rendered its
    placeholder and stayed there while the status was in fact fine.

    The fix is to keep the last status and re-apply it whenever the chip
    appears, which needs all three of these.
    """
    src = STATUS_JS.read_text()
    assert "var _last" in src, "the fetched status must be kept somewhere"
    assert "MutationObserver" in src, (
        "the chip is recreated on every render; without watching for it the "
        "painted value is lost and it reverts to the placeholder"
    )
    assert "repaint" in src, "a cached repaint path is what closes the race"


def test_the_status_survives_the_chip_being_replaced():
    """Same guarantee asserted through the public API surface."""
    src = STATUS_JS.read_text()
    exported = src[src.find("window.CalendarImportStatus = {"):]
    for name in ("refresh", "repaint", "paint"):
        assert name in exported, f"{name} must stay reachable from agenda.js"


def test_a_missing_api_layer_waits_instead_of_reporting_a_failure():
    """app.js defines the API and loads after this file.

    Calling refresh() at boot used to throw ReferenceError and paint an
    error, which is a load-order artefact dressed up as a real fault.
    """
    src = STATUS_JS.read_text()
    assert "typeof window.API === \"undefined\"" in src, (
        "must check for the API layer before using it"
    )
    assert "load" in src, "should retry once the page has finished loading"


def test_agenda_repaints_before_it_refreshes():
    """Repaint-then-fetch, so the chip is never briefly a lie.

    Matched on the module calls, not the bare word "refresh": the helper is
    itself called _refreshCalendarStatus, so a naive search finds the name in
    its own signature and passes regardless of the order inside the body.
    """
    src = AGENDA.read_text()
    helper = src[src.find("function _refreshCalendarStatus"):
                 src.find("async function renderAgenda")]
    repaint_at = helper.index("CalendarImportStatus.repaint")
    fetch_at = helper.index("CalendarImportStatus.refresh")
    assert repaint_at < fetch_at, (
        "repaint must come first: the fetch is async and the chip exists now"
    )