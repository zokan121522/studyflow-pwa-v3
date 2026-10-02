"""The run log and the status it produces.

This is the part that makes the status line honest. The obvious bug to guard
against is a status that says "all good" because no exception was raised —
when the real cause is that the feed has been unreachable for a week and the
importer quietly skipped it.

Each test pins one state the panel can report, and one way it can be wrong.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from calendar_import import runs  # noqa: E402

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


def ago(hours):
    return NOW - timedelta(hours=hours)


class FakeCursor:
    """Returns whatever rows the test queued, keyed like RealDictCursor."""

    def __init__(self, last=None, recent_ok=None):
        self._last = last
        self._recent_ok = recent_ok
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))

    def fetchone(self):
        sql = self.executed[-1][0] if self.executed else ""
        if "idx" in sql or "DELETE" in sql:
            return {"id": 99}
        if "SELECT started_at" in sql and "outcome IN" in sql:
            return {"started_at": self._recent_ok} if self._recent_ok else None
        return self._last

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def row(**kw):
    base = {
        "id": 1, "started_at": ago(2), "finished_at": ago(2),
        "outcome": "ok", "calendars_ok": 1, "calendars_bad": 0,
        "new_sessions": 0, "updated": 30, "skipped": 3, "detail": None,
    }
    base.update(kw)
    return base


# ─── the happy path ─────────────────────────────────────────────────────

def test_status_does_not_depend_on_the_wall_clock():
    """A status panel that only reads correctly on the day it was written.

    These tests pin a stored timestamp and compare it to the real clock, so
    '2 hours ago' quietly becomes '14 hours ago' after lunch and the whole
    file starts failing for a reason that has nothing to do with the code.
    Injecting `now` is what keeps them meaningful.
    """
    stored = row(started_at=ago(2))
    as_written = runs.status(FakeCursor(stored, ago(2)), 1, now=NOW)
    two_days_later = runs.status(
        FakeCursor(stored, ago(2)), 1, now=NOW + timedelta(days=2)
    )
    assert as_written["state"] == "ok"
    assert two_days_later["state"] == "stale", (
        "same stored data, different clock — the panel must react to the "
        "clock, and the test must not depend on which day it runs"
    )


def test_a_fresh_clean_run_reports_ok():
    s = runs.status(FakeCursor(row(), ago(2)), 1, pending_notices=0, now=NOW)
    assert s["state"] == "ok"
    assert "Todo correcto" in s["message"]
    assert s["stale"] is False


def test_pending_notices_are_surfaced_not_hidden():
    """A clean import with unread notices is still 'ok', but not 'nothing to do'."""
    s = runs.status(FakeCursor(row(), ago(1)), 1, pending_notices=4, now=NOW)
    assert s["state"] == "ok"
    assert s["pending_notices"] == 4, (
        "the panel must not claim everything is fine while 4 notices wait"
    )


# ─── the lies we are guarding against ───────────────────────────────────

def test_no_run_ever_is_not_reported_as_ok():
    s = runs.status(FakeCursor(None, None), 1, now=NOW)
    assert s["state"] == "never"
    assert s["stale"] is True


def test_a_stale_run_is_called_stale_not_ok():
    """The thread died, the container was down — a week-old 'ok' is not ok."""
    s = runs.status(FakeCursor(row(started_at=ago(50)), ago(50)), 1, now=NOW)
    assert s["state"] == "stale"
    assert s["stale"] is True
    assert "2 d" in s["message"]


def test_a_failed_run_is_an_error_not_a_quiet_success():
    s = runs.status(
        FakeCursor(row(outcome="error", detail="TimeoutError: feed"),
                   ago(1)), 1, now=NOW
    )
    assert s["state"] == "error"
    assert s["stale"] is True
    assert s["last_run"]["detail"].startswith("TimeoutError")


def test_one_dead_feed_among_working_ones_is_partial():
    s = runs.status(
        FakeCursor(row(outcome="partial", calendars_ok=1, calendars_bad=1,
                       detail="Digitech: HTTP 403"), ago(1)), 1, now=NOW
    )
    assert s["state"] == "partial"
    assert "sin acceso" in s["message"]
    assert "HTTP 403" in s["last_run"]["detail"]


def test_a_lock_skip_is_not_counted_as_the_last_run():
    """The other gunicorn worker is doing the work; it is not a problem.

    A skipped tick must not overwrite the real answer, or opening the agenda
    during the other worker's import would show 'never run'.
    """
    cur = FakeCursor(row(started_at=ago(3)), ago(3))
    runs.status(cur, 1, now=NOW)
    # the query itself must exclude skips, that is the whole mechanism
    last_sql = cur.executed[0][0]
    assert "outcome <> 'skipped'" in last_sql


# ─── wording ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("hours,expected", [
    (0.5, "30 min"), (3, "3 h"), (23.5, "23 h"), (26, "1 d"), (50, "2 d"),
])
def test_ages_are_humanised(hours, expected):
    assert runs._humanise(hours) == expected


def test_an_unknown_age_does_not_claim_zero():
    assert runs._humanise(None) == "?"


def test_naive_timestamps_from_postgres_are_treated_as_utc():
    """psycopg2 hands back naive rows for TIMESTAMP; without tzinfo the
    subtraction raises TypeError and the panel 500s on the agenda."""
    naive = datetime(2026, 9, 30, 10, 0)
    assert runs._hours_since(naive) is not None


# ─── record_run ─────────────────────────────────────────────────────────

def test_record_run_counts_calendars_from_the_importer_report():
    cur = FakeCursor()
    runs.record_run(cur, 1, "partial", per_calendar=[
        {"name": "Digitech", "ok": True, "new": 3, "updated": 20, "skipped": 1},
        {"name": "Dead", "ok": False, "new": 0, "updated": 0, "skipped": 0,
         "error": "HTTP 403"},
    ], new_sessions=3)
    sql, params = cur.executed[0]
    assert "INSERT INTO calendar_import_runs" in sql
    assert params[2] == 1 and params[3] == 1, "ok/bad calendars miscounted"
    assert "Dead: HTTP 403" in params[-1]


def test_record_run_never_raises_over_a_broken_table():
    """The import already happened; losing its log row must not lose the run.

    If this raised, the scheduler would report a failed import every time the
    runs table had a problem — turning a logging fault into a fake outage.
    """

    class BrokenCursor:
        def execute(self, *a, **k):
            raise RuntimeError("relation does not exist")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    assert runs.record_run(BrokenCursor(), 1, "ok") is None


def test_record_run_trims_the_log():
    cur = FakeCursor()
    runs.record_run(cur, 1, "ok")
    assert any("DELETE FROM calendar_import_runs" in sql
               for sql, _ in cur.executed), "log grows without bound"