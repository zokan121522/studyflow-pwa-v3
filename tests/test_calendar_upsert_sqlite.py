"""The calendar UPSERTs must actually RUN on SQLite — not just read right.

The regression this pins: both UPSERT statements (the manual route in
``routes.calendar`` and the scheduled importer in ``calendar_import.importer``)
were written in Postgres idiom — ``btrim(...)`` and
``RETURNING (xmax = 0) AS is_insert`` — and both die on the local SQLite
engine with ``sqlite3.OperationalError: no such function: btrim`` and then,
once that was fixed, ``no such column: xmax`` (see launcher.log 16:05). The
text-assertion tests never caught it because they only compare SQL strings.

These tests drive the REAL functions through the REAL engine:
``database.init_db()`` builds the full schema on a temp SQLite file, then each
import path inserts one synthetic VEVENT and re-imports it (the re-import is
the update branch, where the ``btrim`` guard is evaluated). Any
"no such function"/"no such column" failure surfaces here for real.

Imports use the ``backend.`` prefix on purpose: the routes import
``from backend import database``, so the test must reach that same module
object. Importing a second copy as plain ``database`` would give the test its
own connection, and two connections to one file mean "database is locked".
"""

import sys
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from icalendar import Calendar

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from backend import database as db  # noqa: E402
from backend.calendar_import.importer import process_one_vevent  # noqa: E402
from backend.routes.calendar import _process_one_vevent  # noqa: E402

MADRID = ZoneInfo("Europe/Madrid")


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Point the engine at a throwaway directory and reset module state."""
    target = tmp_path / "data"
    target.mkdir()
    monkeypatch.setenv("STUDYFLOW_DATA_DIR", str(target))
    monkeypatch.setenv("STUDYFLOW_DB_ENGINE", "sqlite")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    import engine

    db.close_pool()
    monkeypatch.setattr(engine, "_active_engine", None, raising=False)
    yield target
    db.close_pool()


@pytest.fixture
def database(data_dir):
    """An initialised backend.database module pointed at the temp file."""
    db.init_db()
    return db


def _vevent():
    """One real icalendar VEVENT component, as a feed would hand it over."""
    body = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\n"
        "BEGIN:VEVENT\r\n"
        "UID:46113@campus.digitechfp.com\r\n"
        "DTSTART:20260929T150000Z\r\n"
        "DTEND:20260929T160000Z\r\n"
        "SUMMARY:Clase 2/16 DIW\r\n"
        "DESCRIPTION:Unirse a la reunion en Teams\r\n"
        "END:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    return next(iter(Calendar.from_ical(body.encode()).walk("VEVENT")))


def _fresh_counters():
    return {
        "skipped_out_of_range": 0,
        "imported": 0,
        "updated": 0,
    }


# ─── the scheduled path (calendar_import.importer) ────────────────────────

def test_scheduled_upsert_inserts_and_reimports_on_sqlite(database):
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            counters = _fresh_counters()
            new_sessions = []
            window_start = date(2026, 9, 28)
            window_end = date(2026, 11, 30)

            assert process_one_vevent(
                cur, _vevent(), window_start, window_end, MADRID, 1,
                counters, new_sessions,
            ) is True
            assert counters["imported"] == 1

            assert process_one_vevent(
                cur, _vevent(), window_start, window_end, MADRID, 1,
                counters, new_sessions,
            ) is False
            assert counters["updated"] == 1
        conn.commit()

        row = db.query_one("SELECT COUNT(*) AS n FROM sessions")
        assert row["n"] == 1, "the upsert must dedupe on id, not insert twice"
    finally:
        db.put_connection(conn)


# ─── the manual path (routes.calendar POST /api/calendar/import) ──────────

def test_manual_upsert_inserts_and_reimports_on_sqlite(database):
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            counters = _fresh_counters()
            today = date(2026, 9, 28)
            window_end = date(2026, 11, 30)

            _process_one_vevent(
                cur, _vevent(), today, window_end, MADRID, 1, counters,
            )
            assert counters["imported"] == 1

            _process_one_vevent(
                cur, _vevent(), today, window_end, MADRID, 1, counters,
            )
            assert counters["updated"] == 1
        conn.commit()

        row = db.query_one("SELECT COUNT(*) AS n FROM sessions")
        assert row["n"] == 1
    finally:
        db.put_connection(conn)


# ─── the DUAL-import path (top-level ``database``, as server.py/scheduler) ──

def _calendar(*events):
    """A real ICS feed carrying the given VEVENT field dicts."""
    body = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\n"
    for event in events:
        body += "BEGIN:VEVENT\r\n" + "".join(
            f"{k}:{v}\r\n" for k, v in event.items()
        ) + "END:VEVENT\r\n"
    return Calendar.from_ical((body + "END:VCALENDAR\r\n").encode())


# Two events in two different weeks, so BOTH week rows are genuine inserts
# (the weeks upsert really writes, rather than short-circuiting on a hit).
TWO_FRESH_WEEKS = (
    {"UID": "46102@campus.digitechfp.com", "DTSTART": "20260929T150000Z",
     "DTEND": "20260929T160000Z", "SUMMARY": "Clase 2/16 DAW"},
    {"UID": "46103@campus.digitechfp.com", "DTSTART": "20261006T170000Z",
     "DTEND": "20261006T180000Z", "SUMMARY": "Clase 3/16 DWES"},
)


def test_top_level_database_cursor_does_not_lock_on_two_new_weeks(data_dir):
    """The production dual-import shape, and the lock it used to hit.

    ``server.py`` starts the scheduler with ``import database as db`` — the
    top-level module — while ``routes.agenda`` reaches SQLite through
    ``from backend import database``. Two distinct module objects mean two
    connection registries and two connections to one file. Before the fix,
    ``_upsert_week`` ignored the importer's cursor and wrote through
    ``backend.database``: event one's session insert left the top-level
    connection holding a RESERVED lock, and event two's week upsert through
    the other connection died with ``database is locked``. TWO events (one
    per week) is the minimal shape that reproduces it — with a single event
    the other connection always commits before the first write collides.

    The fix threads the caller's cursor all the way down, so every write of
    a run rides the one connection the scheduler opened. This test drives
    that exact path: a cursor borrowed from the TOP-LEVEL ``database``
    (what ``server.py``/``scheduler`` use) through the real walker.
    """
    import database as top_db  # noqa: E402 — exactly what server.py imports

    assert top_db is not db, "the dual import must be two module objects"

    db.init_db()          # backend.database schema (the agenda side)
    top_db.close_pool()
    top_db.init_db()      # top-level database schema (the scheduler side)

    from calendar_import.importer import walk_calendar_into_sessions

    conn = top_db.get_connection()
    try:
        with conn.cursor() as cur:
            counters, new_sessions = walk_calendar_into_sessions(
                cur, _calendar(*TWO_FRESH_WEEKS),
                date(2026, 9, 28), date(2026, 11, 30), MADRID, 1,
            )
        conn.commit()

        # No OperationalError("database is locked") reaches here, and both
        # freshly-created week rows exist: the writes shared one connection.
        assert counters["imported"] == 2, counters
        assert len(new_sessions) == 2
        assert top_db.query_one("SELECT COUNT(*) AS n FROM weeks")["n"] == 2

        # Re-import the same feed: idempotent update, never a duplicate row.
        with conn.cursor() as cur:
            again, _ = walk_calendar_into_sessions(
                cur, _calendar(*TWO_FRESH_WEEKS),
                date(2026, 9, 28), date(2026, 11, 30), MADRID, 1,
            )
        conn.commit()
        assert again["updated"] == 2, again
        assert top_db.query_one("SELECT COUNT(*) AS n FROM sessions")["n"] == 2
        assert top_db.query_one("SELECT COUNT(*) AS n FROM weeks")["n"] == 2
    finally:
        top_db.put_connection(conn)
        top_db.close_pool()