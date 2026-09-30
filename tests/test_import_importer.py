"""Tests for calendar_import.importer — the new-sessions contract.

The route and the daily job share this module, so the behaviour that matters
is: import correctly, and report *only* what was created. Reporting updates
too would make the daily job spam the user every run, which is how a
notification gets ignored within a week.

The hooks (calendar_loader, feed_fetcher) are injected, so the whole flow
runs with no database rows and no network.
"""

import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from icalendar import Calendar

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from calendar_import import importer as I  # noqa: E402

MADRID = ZoneInfo("Europe/Madrid")

# The window starts at 'today', so the fixtures need a fixed reference date.
BASE = date(2026, 9, 28)


def feed(*events):
    body = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\n"
    for lines in events:
        body += "BEGIN:VEVENT\r\n" + "".join(
            f"{k}:{v}\r\n" for k, v in lines.items()
        ) + "END:VEVENT\r\n"
    return Calendar.from_ical((body + "END:VCALENDAR\r\n").encode())


CLASS_A = {
    "UID": "46102@campus.digitechfp.com", "DTSTART": "20260929T150000Z",
    "DTEND": "20260929T160000Z", "SUMMARY": "Clase 2/16 DAW",
}
CLASS_B = {
    "UID": "46103@campus.digitechfp.com", "DTSTART": "20260929T170000Z",
    "DTEND": "20260929T180000Z", "SUMMARY": "Clase 2/16 DWES",
}


class FakeCursor:
    """Records statements and reports INSERT vs UPDATE per the returned flag.

    `inserts` decides whether RETURNING reports is_insert true or false,
    standing in for what the database would say on a conflict.
    """

    def __init__(self, inserts=True):
        self.inserts = inserts
        self.statements = []

    def execute(self, sql, params=None):
        self.statements.append((" ".join(sql.split()), params))

    def fetchone(self):
        return {"is_insert": self.inserts}


@pytest.fixture
def stub_agenda(monkeypatch):
    """The week/day upserts need a database; capture them instead."""
    weeks, days = [], []
    monkeypatch.setattr(I, "_upsert_week", lambda w, u: weeks.append((w, u)))
    monkeypatch.setattr(I, "_upsert_day", lambda d, w, u: days.append((d, w, u)))
    return weeks, days


WINDOW_START = date(2026, 9, 29)
WINDOW_END = date(2026, 10, 29)


def walk(cal, cur, stub_agenda):
    return I.walk_calendar_into_sessions(
        cur, cal, WINDOW_START, WINDOW_END, MADRID, 1
    )


# ─── counters and the new-sessions contract ────────────────────────────────

def test_a_fresh_feed_reports_every_class_as_new(stub_agenda):
    cur = FakeCursor(inserts=True)
    counters, new = walk(feed(CLASS_A, CLASS_B), cur, stub_agenda)
    assert counters["imported"] == 2
    assert len(new) == 2


def test_re_running_reports_updates_and_nothing_new(stub_agenda):
    """The daily job runs every 24 h. Day two must be silent."""
    cur = FakeCursor(inserts=False)
    counters, new = walk(feed(CLASS_A, CLASS_B), cur, stub_agenda)
    assert counters["updated"] == 2
    assert counters["imported"] == 0
    assert new == [], "a re-run must not produce a notice"


def test_a_moved_class_is_an_update_not_a_new_session(stub_agenda):
    cur = FakeCursor(inserts=False)
    counters, new = walk(feed(CLASS_A), cur, stub_agenda)
    assert counters["updated"] == 1
    assert new == []


def test_new_sessions_carry_what_the_notice_needs(stub_agenda):
    cur = FakeCursor(inserts=True)
    _counters, new = walk(feed(CLASS_A), cur, stub_agenda)
    assert new[0]["day_date"] == "2026-09-29"
    assert new[0]["start_time"] == "17:00"
    assert new[0]["end_time"] == "18:00"
    assert new[0]["title"] == "Clase 2/16 DAW"


def test_out_of_range_events_are_counted_not_reported(stub_agenda):
    late = dict(CLASS_A, DTSTART="20261201T150000Z", DTEND="20261201T160000Z")
    cur = FakeCursor(inserts=True)
    counters, new = walk(feed(late), cur, stub_agenda)
    assert counters["skipped_out_of_range"] == 1
    assert new == []


def test_events_before_the_window_are_skipped(stub_agenda):
    old = dict(CLASS_A, DTSTART="20250101T150000Z", DTEND="20250101T160000Z")
    cur = FakeCursor(inserts=True)
    counters, _new = walk(feed(old), cur, stub_agenda)
    assert counters["skipped_out_of_range"] == 1


def test_event_without_uid_is_ignored(stub_agenda):
    cur = FakeCursor(inserts=True)
    counters, new = walk(feed({"DTSTART": "20260929T150000Z",
                               "DTEND": "20260929T160000Z"}), cur, stub_agenda)
    assert counters["imported"] == 0
    assert new == []


def test_event_without_dates_is_ignored(stub_agenda):
    cur = FakeCursor(inserts=True)
    counters, _new = walk(feed({"UID": "x@y", "SUMMARY": "s"}), cur, stub_agenda)
    assert counters["imported"] == 0


def test_empty_feed_is_harmless(stub_agenda):
    cur = FakeCursor()
    counters, new = walk(feed(), cur, stub_agenda)
    assert counters == {"imported": 0, "updated": 0, "skipped_out_of_range": 0}
    assert new == []


def test_upsert_targets_the_session_primary_key(stub_agenda):
    """Dedup depends on ON CONFLICT (id) with the sanitised UID."""
    cur = FakeCursor(inserts=True)
    walk(feed(CLASS_A), cur, stub_agenda)
    session_sql = [s for s in cur.statements if "INSERT INTO sessions" in s[0]][0][0]
    assert "ON CONFLICT (id)" in session_sql
    assert "RETURNING (xmax = 0) AS is_insert" in session_sql


def test_session_id_is_the_sanitised_uid(stub_agenda):
    cur = FakeCursor(inserts=True)
    walk(feed(CLASS_A), cur, stub_agenda)
    params = [s for s in cur.statements if "INSERT INTO sessions" in s[0]][0][1]
    assert params[0] == "cal-46102campusdigitechfpcom"


# ─── run_import_for_user ───────────────────────────────────────────────────

def _wire(monkeypatch, calendars, fetcher):
    """Point run_import_for_user at fixed calendars and a feed fetcher."""
    monkeypatch.setattr(I, "_local_zone", lambda: MADRID)
    monkeypatch.setattr(
        I, "_default_calendar_loader", lambda user_id: calendars
    )
    monkeypatch.setattr(I, "_default_feed_fetcher", fetcher)


ONE = [{"id": "a", "name": "Digitech", "url": "https://x.test/a"}]


def test_run_import_reports_the_user_and_name(monkeypatch, stub_agenda):
    _wire(monkeypatch, ONE, lambda url: feed(CLASS_A))

    user_id, name, new = I.run_import_for_user(
        FakeCursor(inserts=True), days=60, user_id=1, today=BASE
    )
    assert user_id == 1
    assert name == "Digitech"
    assert len(new) == 1


def test_run_import_is_silent_when_nothing_is_new(monkeypatch, stub_agenda):
    _wire(monkeypatch, ONE, lambda url: feed(CLASS_A))

    _uid, _name, new = I.run_import_for_user(
        FakeCursor(inserts=False), days=60, user_id=1, today=BASE
    )
    assert new == []


def test_one_dead_feed_does_not_block_the_others(monkeypatch, stub_agenda):
    """A token that expires must not stop a working calendar."""
    _wire(monkeypatch, [
        {"id": "bad", "name": "Roto", "url": "https://x.test/bad"},
        {"id": "ok", "name": "Digitech", "url": "https://x.test/ok"},
    ], lambda url: (_ for _ in ()).throw(ValueError("token muerto"))
        if "bad" in url else feed(CLASS_A))

    _uid, name, new = I.run_import_for_user(
        FakeCursor(inserts=True), days=60, user_id=1, today=BASE
    )
    assert name == "Digitech"
    assert len(new) == 1


def test_no_calendars_means_no_notice(monkeypatch, stub_agenda):
    _wire(monkeypatch, [], lambda url: feed())

    _uid, name, new = I.run_import_for_user(FakeCursor(), days=60, user_id=1, today=BASE)
    assert new == []
    assert name == "calendario"


def test_sixty_day_window_covers_next_month(monkeypatch, stub_agenda):
    """The "2 meses" option must actually reach the following month."""
    october = dict(CLASS_A, DTSTART="20261020T150000Z",
                   DTEND="20261020T160000Z")

    cal = feed(october)
    counters, new = I.walk_calendar_into_sessions(
        FakeCursor(inserts=True), cal,
        BASE, BASE + timedelta(days=60), MADRID, 1
    )
    assert counters["skipped_out_of_range"] == 0
    assert len(new) == 1
    assert new[0]["day_date"] == "2026-10-20"


def test_thirty_day_window_would_miss_a_november_class(stub_agenda):
    """Why 60 exists: BASE+30 is 28 Oct, so early November falls outside."""
    november = dict(CLASS_A, DTSTART="20261110T150000Z",
                    DTEND="20261110T160000Z")

    counters, _new = I.walk_calendar_into_sessions(
        FakeCursor(inserts=True), feed(november),
        BASE, BASE + timedelta(days=30), MADRID, 1
    )
    assert counters["skipped_out_of_range"] == 1

    counters60, new60 = I.walk_calendar_into_sessions(
        FakeCursor(inserts=True), feed(november),
        BASE, BASE + timedelta(days=60), MADRID, 1
    )
    assert counters60["skipped_out_of_range"] == 0
    assert len(new60) == 1