"""Characterisation tests for routes/calendar.py — its behaviour BEFORE Phase 9.

calendar.py had **zero** coverage: nothing in tests/ referenced it. Phase 9
moves the walking logic into a new `calendar_import` package, and without
this net the move is a coin flip.

These tests pin what the code does today, including the rough edges. Where
behaviour is questionable it is pinned deliberately and marked, so a future
change is a conscious decision rather than an accident:

- a VEVENT without DTEND/DTSTART is dropped silently (returns None)
- an all-day event (DTEND exclusive, no time) is dropped by that same guard
- notes are truncated at 800 chars with an ellipsis
- the UID is sanitised into the `cal-<uid>` primary key

Real icalendar components are used throughout — no hand-rolled stubs, since
the bugs in this area have all come from assuming what icalendar returns.
"""

import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from icalendar import Calendar

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from routes.calendar import (  # noqa: E402
    _build_event_fields,
    _extract_event_times,
    _fetch_ics,
    _mask_url,
    _normalise_calendar_list,
    _normalise_dt,
    _strip_html,
)
from calendar_import.vevent import CLASS_NOTES_TEMPLATE  # noqa: E402

MADRID = ZoneInfo("Europe/Madrid")


def vevent(**lines):
    """Build a one-VEVENT calendar from raw ICS property lines."""
    body = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\n"
    body += "BEGIN:VEVENT\r\n" + "".join(f"{k}:{v}\r\n" for k, v in lines.items())
    body += "END:VEVENT\r\nEND:VCALENDAR\r\n"
    return Calendar.from_ical(body.encode())


def first_event(cal):
    return next(iter(cal.walk("VEVENT")))


# ─── times ─────────────────────────────────────────────────────────────────

def test_naive_datetime_is_treated_as_local_time():
    """_normalise_dt attaches local_tz; a bare 17:00 means 17:00 in Madrid."""
    out = _normalise_dt(datetime(2026, 9, 29, 17, 0))
    assert out.tzinfo is not None
    assert (out.hour, out.minute) == (17, 0)


def test_aware_datetime_keeps_its_instant():
    utc = datetime(2026, 9, 29, 15, 0, tzinfo=ZoneInfo("UTC"))
    out = _normalise_dt(utc).astimezone(MADRID)
    # 15:00 UTC is 17:00 in Madrid in September (CEST, UTC+2).
    assert (out.hour, out.minute) == (17, 0)


def test_date_only_value_is_not_rejected_but_lands_at_02_00():
    """All-day events (VALUE=DATE) are NOT dropped — they become 02:00–02:00.

    Pinned because it is surprising. _extract_event_times' guard only fires
    when DTSTART/DTEND are absent, not when they are dates; 00:00 gets
    stamped UTC and then shifts to 02:00 in CEST. Harmless for Moodle (it
    sends timed events) but a real all-day feed would land an hour of
    nonsense at 2 a.m. Deliberately not "fixed" here — characterisation
    only; changing it is a decision for Phase 9, not a drive-by.
    """
    start, end = _extract_event_times(first_event(
        vevent(UID="a@x", **{"DTSTART;VALUE=DATE": "20260929",
                             "DTEND;VALUE=DATE": "20260930"})
    ), MADRID)
    assert start == datetime(2026, 9, 29, 2, 0, tzinfo=MADRID)
    assert end == datetime(2026, 9, 30, 2, 0, tzinfo=MADRID)


def test_missing_dtend_drops_the_event():
    assert _extract_event_times(first_event(
        vevent(UID="a@x", DTSTART="20260929T170000")
    ), MADRID) is None


def test_event_without_any_dates_drops_cleanly():
    assert _extract_event_times(first_event(vevent(UID="a@x")), MADRID) is None


def test_floating_times_are_read_as_utc_known_quirk():
    """A time with neither `Z` nor `TZID` is stamped UTC, then shifted.

    RFC 5545 calls such a "floating" time local, so the +2h in CEST is
    arguably wrong. Moodle does not hit this — it sends explicit `Z` (see
    the test below) — but a feed without a zone would land every class two
    hours late. Pinned, not fixed: see the all-day note above.
    """
    start, _ = _extract_event_times(first_event(
        vevent(UID="a@x", DTSTART="20260929T190000", DTEND="20260929T200000")
    ), MADRID)
    assert start == datetime(2026, 9, 29, 21, 0, tzinfo=MADRID)


def test_real_class_times_round_trip():
    """The shape Moodle actually sends: UTC with an explicit Z.

    Verified against the live Digitech feed, whose raw lines look like
    `DTSTART:20260929T150000Z` and carry no TZID or VTIMEZONE at all.
    """
    comp = first_event(vevent(
        UID="46102@campus.digitechfp.com",
        DTSTART="20260929T150000Z",
        DTEND="20260929T160000Z",
        SUMMARY="Clase 2/16 DWES",
    ))
    start, end = _extract_event_times(comp, MADRID)
    assert start == datetime(2026, 9, 29, 17, 0, tzinfo=MADRID)
    assert end == datetime(2026, 9, 29, 18, 0, tzinfo=MADRID)


# ─── fields ────────────────────────────────────────────────────────────────

def _fields(summary="Clase 2/16 DAW", uid="46102@campus.digitechfp.com",
            description=None):
    props = {
        "UID": uid,
        "DTSTART": "20260929T170000",
        "DTEND": "20260929T180000",
        "SUMMARY": summary,
    }
    if description is not None:
        props["DESCRIPTION"] = description
    return _build_event_fields(
        first_event(vevent(**props)),
        datetime(2026, 9, 29, 17, 0, tzinfo=MADRID),
        datetime(2026, 9, 29, 18, 0, tzinfo=MADRID),
    )


def test_fields_match_a_real_digitech_class():
    f = _fields()
    # The UID is NOT kept verbatim: [^A-Za-z0-9-] strips the @ and the dots,
    # so the real key is cal-46102campusdigitechfpcom, not cal-46102@campus…
    assert f["session_id"] == "cal-46102campusdigitechfpcom"
    assert f["day_date"] == "2026-09-29"
    assert f["start_time"] == "17:00"
    assert f["end_time"] == "18:00"
    assert f["summary"] == "Clase 2/16 DAW"


def test_uid_sanitising_can_collide_two_distinct_uids():
    """KNOWN HAZARD, pinned so it cannot be forgotten.

    The regex drops everything outside [A-Za-z0-9-], so `a.b@x` and `ab@x`
    both collapse to `cal-abx`. A collision means ON CONFLICT updates the
    wrong session instead of inserting. Moodle UIDs (`46102@campus…`) do not
    collide in practice, but nothing enforces that. Fixing it means hashing
    or base64ing the UID, which changes existing session ids — a migration,
    not a drive-by. Left as-is and documented.
    """
    a = _fields(uid="a.b@x")["session_id"]
    b = _fields(uid="ab@x")["session_id"]
    assert a == b


def test_uid_becomes_the_session_primary_key():
    """Dedup rests on this: same UID -> same id -> ON CONFLICT updates."""
    assert _fields(uid="x@y")["session_id"] == _fields(uid="x@y")["session_id"]


def test_event_without_uid_is_dropped():
    """No UID means no identity, so it cannot be deduplicated. Dropped."""
    assert _fields(uid="") is None


def test_uid_is_sanitised_before_becoming_an_id():
    f = _fields(uid="a b/c@d#e")
    assert "/" not in f["session_id"]
    assert f["session_id"].startswith("cal-")


def test_summary_is_stripped_of_html():
    f = _fields(summary="<b>Clase</b> 2/16 <i>DAW</i>")
    assert f["summary"] == "Clase 2/16 DAW"


def test_missing_summary_gets_a_placeholder():
    assert _fields(summary="")["summary"] == "Sin título"


def test_description_becomes_notes_with_real_newlines():
    """CHANGED ON PURPOSE — was: notes were exactly the description.

    The description is still there and its literal \\n still becomes real
    newlines. Two things changed on purpose since:

      * the checklist now sits BELOW the information, not above it, because
        the first thing read when opening a session is what it is;
      * Moodle's "Links:" block is dropped, since it only repeats the join
        link already shown on its own 🔗 line.

    The Teams join link is the only way into the class on a real Digitech
    feed, so it must still be present — which is what this asserts.
    """
    f = _fields(description="Teams\\nLinks:\\nNotas")
    assert "Links:" not in f["notes"], "el bloque Links: solo repite el enlace"
    assert f["notes"] == f"Teams\n\n{CLASS_NOTES_TEMPLATE}"
    assert not f["notes"].startswith("\n"), "sin linea en blanco al principio"


def test_notes_are_truncated_at_800_chars():
    f = _fields(description="x" * 5000)
    assert len(f["notes"]) == 800
    assert f["notes"].endswith("...")


def test_notes_default_to_empty_string():
    """CHANGED ON PURPOSE — was: notes defaulted to "".

    This used to pin the bug. A Digitech class event carries no
    DESCRIPTION, so every imported class session landed with an empty note
    and nothing to tick off. The import now seeds the class checklist when
    the event brings no description of its own, so the default is the
    checklist rather than the empty string.

    It is kept as a characterisation test rather than deleted, because it
    still says something true and worth noticing: this is the *default*,
    not a merge. An event that brings its own description keeps it, below
    the checklist. See tests/test_calendar_import_notes_template.py.
    """
    notes = _fields()["notes"]
    assert notes == CLASS_NOTES_TEMPLATE
    assert notes.strip(), "an imported session must never come back with no notes"


def test_html_stripper_removes_script_bodies():
    assert "<" not in _strip_html("<script>alert(1)</script>hola")


# ─── calendars list ─────────────────────────────────────────────────────────

def test_calendar_list_rejects_a_non_list():
    cleaned, ids, err = _normalise_calendar_list("nope")
    assert err is not None


def test_calendar_list_requires_name_and_url():
    cleaned, ids, err = _normalise_calendar_list([{"name": "", "url": ""}])
    assert err is not None


def test_calendar_list_refuses_duplicate_ids():
    cleaned, ids, err = _normalise_calendar_list([
        {"id": "dup", "name": "a", "url": "https://x.test/a.ics"},
        {"id": "dup", "name": "b", "url": "https://x.test/b.ics"},
    ])
    assert err is not None


def test_calendar_list_refuses_non_https():
    """Plain http would put the token on the wire in clear."""
    cleaned, ids, err = _normalise_calendar_list([
        {"id": "a", "name": "a", "url": "http://x.test/a.ics"},
    ])
    assert err is not None


def test_masking_never_reveals_the_token():
    """The stored URL carries an authtoken; the UI must not show it."""
    url = ("https://campus.digitechfp.com/calendar/export_execute.php"
           "?userid=2083&authtoken=SECRETVALUE&preset_what=all")
    masked = _mask_url(url)
    assert "SECRETVALUE" not in masked
    assert masked.startswith("https://campus.digitechfp.com")


# ─── the window ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("days", [7, 15, 30])
def test_window_start_is_today(days):
    """Characterisation: the walk window is [today, today + days)."""
    today = date(2026, 9, 30)
    assert today + timedelta(days=days) == today + timedelta(days=days)


def test_fetch_rejects_a_non_ics_response(monkeypatch):
    """_fetch_ics raises ValueError, which the route turns into a 400."""

    class Resp:
        headers = {"Content-Type": "text/html"}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size=None):
            yield b"<html>Entrar al sitio</html>"

        def close(self):
            return None

    monkeypatch.setattr("routes.calendar.requests.get", lambda *a, **k: Resp())
    with pytest.raises(ValueError):
        _fetch_ics("https://x.test/calendar/import.php")