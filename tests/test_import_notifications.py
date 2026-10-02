"""Tests for the notification text — the part the user reads and copies.

build_message is pure, so it is tested directly rather than through the
database. The store helpers are checked for their SQL shape and their
no-op behaviour, because "notify when nothing happened" is the failure mode
that would actually annoy.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from calendar_import import notifications as N  # noqa: E402


def session(day="2026-09-29", start="17:00", end="18:00", title="Clase 2/16 DAW"):
    return {"day_date": day, "start_time": start, "end_time": end, "title": title}


# ─── build_message ─────────────────────────────────────────────────────────

def test_nothing_imported_produces_no_message():
    """The caller stores nothing at all, so the panel stays quiet."""
    assert N.build_message("Digitech", []) is None
    assert N.build_message("Digitech", None) is None


def test_single_session_is_singular():
    msg = N.build_message("Digitech", [session()])
    assert "1 sesión nueva" in msg
    assert "sesiones nuevas" not in msg


def test_several_sessions_are_plural():
    msg = N.build_message("Digitech", [session(), session(day="2026-09-30")])
    assert "2 sesiones nuevas" in msg


def test_message_carries_the_calendar_name():
    assert "Digitech" in N.build_message("Digitech", [session()])


def test_one_line_holds_day_time_and_title():
    msg = N.build_message("Digitech", [session()])
    assert "mar 29 sep" in msg
    assert "17:00-18:00" in msg
    assert "Clase 2/16 DAW" in msg


def test_output_is_plain_text_with_no_markdown():
    """It gets pasted into notes and chat — no headings, no bold."""
    msg = N.build_message("Digitech", [session()])
    for marker in ("#", "**", "```", "|", "_"):
        assert marker not in msg


def test_missing_end_time_leaves_no_dangling_dash():
    msg = N.build_message("Digitech", [session(end=None)])
    assert "17:00-" not in msg
    assert "17:00" in msg


def test_missing_start_time_degrades_gracefully():
    msg = N.build_message("Digitech", [session(start=None)])
    assert "?" in msg


def test_title_falls_back_through_summary():
    s = session(title=None)
    s["summary"] = "Clase 9/16 DIW"
    assert "Clase 9/16 DIW" in N.build_message("Digitech", [s])


def test_no_title_at_all_still_renders():
    s = session(title=None)
    assert "Sin título" in N.build_message("Digitech", [s])


def test_unparseable_date_is_kept_verbatim():
    """A bad date must not crash the notice — show it and move on."""
    assert "no-es-fecha" in N.build_message("Digitech", [session(day="no-es-fecha")])


def test_each_session_gets_its_own_line():
    msg = N.build_message("Digitech", [
        session(day="2026-09-29", title="A"),
        session(day="2026-09-30", title="B"),
        session(day="2026-10-01", title="C"),
    ])
    body = [ln for ln in msg.splitlines() if ln.startswith("·")]
    assert len(body) == 3
    assert "A" in body[0] and "B" in body[1] and "C" in body[2]


def test_weekday_names_are_spanish():
    assert N._human_day("2026-09-28") == "lun 28 sep"   # lunes
    assert N._human_day("2026-10-04") == "dom 04 oct"   # domingo


# ─── store helpers ─────────────────────────────────────────────────────────

class FakeCursor:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows or []
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))

    def fetchone(self):
        return dict(self.row) if self.row else None

    def fetchall(self):
        return [dict(r) for r in self.rows]


def test_storing_nothing_writes_no_row():
    cur = FakeCursor()
    assert N.store_pending(cur, 1, "Digitech", None) is None
    assert cur.executed == []


def test_storing_returns_the_new_id():
    cur = FakeCursor(row={"id": 7})
    assert N.store_pending(cur, 1, "Digitech", "msg") == 7


def test_storing_scopes_to_the_user():
    cur = FakeCursor(row={"id": 7})
    N.store_pending(cur, 42, "Digitech", "msg")
    assert cur.executed[0][1][0] == 42


def test_pending_only_returns_unread():
    cur = FakeCursor()
    N.fetch_pending(cur, 1)
    sql = cur.executed[0][0]
    assert "read_at IS NULL" in sql


def test_pending_is_scoped_and_limited():
    cur = FakeCursor()
    N.fetch_pending(cur, 3, limit=2)
    assert cur.executed[0][1] == (3, 2)


def test_pending_reads_dict_rows():
    """RealDictCursor yields dicts; tuple indexing here would KeyError."""
    cur = FakeCursor(rows=[{"id": 1, "message": "hola"}])
    got = N.fetch_pending(cur, 1)
    assert got == [{"id": 1, "message": "hola"}]


def test_marking_read_is_scoped_to_the_user():
    cur = FakeCursor()
    N.mark_read(cur, 5, 99)
    assert cur.executed[0][1] == (99, 5)


def test_marking_read_twice_reports_false():
    cur = FakeCursor(row=None)
    assert N.mark_read(cur, 5, 99) is False