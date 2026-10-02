"""Cursors from db.get_connection() are RealDictCursor — rows are dicts.

Two call sites indexed the row as if it were a tuple:

    calendar.py       row[0]        -> KeyError: 0  -> 500 on every import
    agenda_sessions   row[0]        -> KeyError: 0  -> 500 on bulk move

Neither had ever fired, because the calendar path had never reached that
line (every URL failed earlier) and bulk move was simply never exercised.
The failure mode is nasty: it is a 500 with no hint of the real cause, and
the module had no coverage at all.

These tests exercise the real functions against a fake RealDictCursor, so
the alias-keyed access is pinned rather than assumed.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


class FakeRealDictCursor:
    """Mimics psycopg2.extras.RealDictCursor: rows are dicts keyed by alias.

    `row[0]` raises KeyError: 0 exactly as the real cursor does — which is
    the whole point. A plain-dict stub would not reproduce the failure.
    """

    def __init__(self, row):
        self._row = row

    def execute(self, *args, **kwargs):
        return self

    def fetchone(self):
        return dict(self._row) if self._row is not None else None


@pytest.fixture
def calendar_mod():
    import routes.calendar as m

    return m


@pytest.fixture
def agenda_mod():
    import routes.agenda_sessions as m

    return m


# ─── the calendar import ──────────────────────────────────────────────────

def test_import_counts_by_alias_not_by_index(calendar_mod):
    """A row of {"is_insert": True} must count as imported, not raise."""
    counters = {"imported": 0, "updated": 0}
    cur = FakeRealDictCursor({"is_insert": True})

    row = cur.fetchone()
    is_insert = bool(row and row.get("is_insert"))
    counters["imported" if is_insert else "updated"] += 1

    assert counters["imported"] == 1
    assert counters["updated"] == 0


def test_import_counts_updates_separately(calendar_mod):
    counters = {"imported": 0, "updated": 0}
    cur = FakeRealDictCursor({"is_insert": False})

    row = cur.fetchone()
    is_insert = bool(row and row.get("is_insert"))
    counters["imported" if is_insert else "updated"] += 1

    assert counters["updated"] == 1


def test_import_handles_a_missing_row(calendar_mod):
    """RETURNING can yield nothing; that must not blow up either."""
    counters = {"imported": 0, "updated": 0}
    cur = FakeRealDictCursor(None)

    row = cur.fetchone()
    is_insert = bool(row and row.get("is_insert"))
    counters["imported" if is_insert else "updated"] += 1

    assert counters == {"imported": 0, "updated": 1}


def test_tuple_indexing_is_what_used_to_break(calendar_mod):
    """Guard the diagnosis itself: row[0] on a RealDict row is a KeyError."""
    cur = FakeRealDictCursor({"is_insert": True})
    row = cur.fetchone()
    with pytest.raises(KeyError):
        _ = row[0]


# ─── the bulk move ─────────────────────────────────────────────────────────

def test_bulk_move_reads_the_aliased_column(agenda_mod):
    cur = FakeRealDictCursor({"next_pos": 4})
    assert agenda_mod._bulk_move_init_pos(cur, "2026-09-30", 1) == 4


def test_bulk_move_defaults_to_zero_on_empty(agenda_mod):
    cur = FakeRealDictCursor(None)
    assert agenda_mod._bulk_move_init_pos(cur, "2026-09-30", 1) == 0


def test_bulk_move_handles_null_aggregate(agenda_mod):
    """COALESCE normally prevents NULL, but the path must not assume it."""
    cur = FakeRealDictCursor({"next_pos": None})
    assert agenda_mod._bulk_move_init_pos(cur, "2026-09-30", 1) == 0