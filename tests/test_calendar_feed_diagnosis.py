"""The calendar importer must explain WHY a URL is not an ICS feed.

A Moodle `import.php` URL answers 200 with an HTML *login page*. The importer
was right to reject it — it is not a calendar — but it only ever said
"the response is not a valid ICS feed", so the user had no way to know the
problem was an authentication wall rather than a bad URL.

These tests pin the classification of non-feed responses and the actionable
message that comes out of it. No network, no database.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from routes.calendar import _classify_non_feed  # noqa: E402


# Real shape of the Moodle login page we were actually served.
MOODLE_LOGIN_HTML = b"""<!DOCTYPE html>
<html dir="ltr" lang="es">
<head><title>Entrar al sitio | Campus Virtual</title></head>
<body><form action="/login/index.php" method="post">
<input name="username" /><input type="password" name="password" />
<input type="submit" value="Entrar" /></form></body></html>"""

GOOGLE_ICS = b"""BEGIN:VCALENDAR\r
VERSION:2.0\r
PRODID:-//Google Inc//Google Calendar 70.9054//EN\r
BEGIN:VEVENT\r
UID:abc123@campus.digitechfp.com\r
SUMMARY:Clase 3/16 DAW\r
END:VEVENT\r
END:VCALENDAR\r
"""


def test_real_ics_is_not_flagged():
    assert _classify_non_feed({"content-type": "text/calendar; charset=utf-8"},
                              GOOGLE_ICS) is None


def test_ics_without_calendar_content_type_is_still_accepted():
    """Some servers send text/plain. The BEGIN:VCALENDAR marker decides."""
    assert _classify_non_feed({"content-type": "text/plain"},
                              GOOGLE_ICS) is None


def test_moodle_login_page_is_named_as_such():
    reason = _classify_non_feed({"content-type": "text/html; charset=utf-8"},
                                MOODLE_LOGIN_HTML)
    assert "inicio de sesion" in reason, (
        f"expected the login page to be recognised, got {reason!r} — "
        "without this the user is told only that it is 'not an ICS feed'"
    )


def test_login_page_reason_tells_the_user_what_url_to_use():
    reason = _classify_non_feed({"content-type": "text/html"},
                                MOODLE_LOGIN_HTML)
    assert "export.php?token=" in reason
    assert "calendar.google.com" in reason


def test_plain_html_is_not_mislabelled_as_a_login_page():
    reason = _classify_non_feed({"content-type": "text/html"},
                                b"<html><body>Hola</body></html>")
    assert "pagina web" in reason
    assert "inicio de sesion" not in reason


def test_empty_body_is_reported_distinctly():
    assert "vacia" in _classify_non_feed({"content-type": "text/plain"}, b"")


def test_login_form_below_the_head_is_still_found():
    """The real Moodle login form sits ~15 KB in, behind the stylesheet.

    An earlier version only scanned the first 8 KB and so mislabelled the
    genuine login page as a plain web page — the exact wrong advice.
    """
    padding = b"<!-- stylesheet noise -->" * 700  # ~15 KB before the form
    page = b"<html><head>" + padding + b"</head><body>" \
        + MOODLE_LOGIN_HTML + b"</body></html>"
    reason = _classify_non_feed({"content-type": "text/html; charset=utf-8"}, page)
    assert "inicio de sesion" in reason, (
        f"deep login form misread as: {reason!r}"
    )


def test_reason_never_leaks_the_url_or_credentials():
    """The reason goes to the UI; it must not echo back secrets."""
    reason = _classify_non_feed({"content-type": "text/html"},
                                MOODLE_LOGIN_HTML)
    assert "campus.digitechfp.com" not in reason
    assert "logintoken" not in reason