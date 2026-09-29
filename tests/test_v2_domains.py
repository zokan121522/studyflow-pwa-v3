# tests/test_v2_domains.py
"""Tests for the v2 → v3 domain mappers (issue #8).

These use a fake connection that records the SQL instead of talking to a
database, so they can assert the SHAPE of a statement — in particular that
a mapper is idempotent on tables where v3 imposes a natural key. That is
how the quick_notes crash was found: v3 makes that table a singleton
(PK on user_id alone) while v2 stored a row per note.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from v2_domains import _import_quick_notes  # noqa: E402


class FakeCursor:
    def __init__(self, recorder):
        self._rec = recorder

    def execute(self, sql, params=None):
        self._rec.append((sql, params))

    def close(self):
        pass


class FakeConn:
    def __init__(self):
        self.statements = []

    def cursor(self):
        return FakeCursor(self.statements)


def _sql(conn):
    return " ".join(s for s, _ in conn.statements).upper()


def test_quick_notes_is_upsert_not_plain_insert():
    """The import must tolerate a user who already has a v3 note."""
    conn = FakeConn()
    _import_quick_notes(conn, 1, {"quick_notes": [{"content": "hola"}]})
    sql = _sql(conn)
    assert "ON CONFLICT" in sql, "quick_notes must upsert; v3 PK is user_id alone"
    assert "DUPLICATE" not in sql


def test_quick_notes_keeps_a_single_row():
    """v2 may hold several notes; v3 holds one. Only one INSERT may run."""
    conn = FakeConn()
    n = _import_quick_notes(
        conn,
        1,
        {
            "quick_notes": [
                {"content": "vieja", "updated_at": "2026-01-01T00:00:00Z"},
                {"content": "nueva", "updated_at": "2026-09-01T00:00:00Z"},
                {"content": "intermedia", "updated_at": "2026-05-01T00:00:00Z"},
            ]
        },
    )
    inserts = [s for s, _ in conn.statements if s.strip().upper().startswith("INSERT")]
    assert len(inserts) == 1, f"expected 1 insert, got {len(inserts)}"
    assert n == 1


def test_quick_notes_keeps_the_most_recent():
    """The surviving note is the newest, not an arbitrary one."""
    conn = FakeConn()
    _import_quick_notes(
        conn,
        1,
        {
            "quick_notes": [
                {"content": "vieja", "updated_at": "2026-01-01T00:00:00Z"},
                {"content": "nueva", "updated_at": "2026-09-01T00:00:00Z"},
            ]
        },
    )
    params = conn.statements[0][1]
    assert "nueva" in params, f"expected the newest note, got {params}"


def test_quick_notes_handles_missing_updated_at():
    """A NULL updated_at must not blow up the max() comparison."""
    conn = FakeConn()
    n = _import_quick_notes(
        conn, 1, {"quick_notes": [{"content": "sin fecha", "updated_at": None}]}
    )
    assert n == 1
    assert "COALESCE" in _sql(conn), "a NULL timestamp needs a fallback to now()"


def test_quick_notes_no_rows_is_a_noop():
    """An absent table must not emit SQL at all."""
    conn = FakeConn()
    assert _import_quick_notes(conn, 1, {}) == 0
    assert conn.statements == []
    conn2 = FakeConn()
    assert _import_quick_notes(conn2, 1, {"quick_notes": []}) == 0
    assert conn2.statements == []


def test_plan_does_not_advertise_deferred_data_as_pending():
    """quiz_errors/user_addons are NOT imported, so they must not appear in
    the counts the modal shows as 'this will be migrated'."""
    from v2_migrate import plan

    tables = {
        "courses": [{"id": 1, "title": "A", "user_id": 1}],
        "topics": [{"id": 1, "course_id": 1, "title": "t", "user_id": 1}],
        "blocks": [],
        "questions": [],
        "quick_notes": [{"id": 1, "content": "x"}],
        "quiz_errors": [{"id": 1}, {"id": 2}],
        "user_addons": [{"id": 1}],
    }
    result = plan(tables)
    assert "quiz_errors" not in result["counts"]
    assert "user_addons" not in result["counts"]
    assert result["deferred"]["quiz_errors"] == 2
    assert result["deferred"]["user_addons"] == 1


def test_v2_only_tables_have_no_duplicates():
    """A table listed twice showed up twice in the modal."""
    from v2_migrate import V2_ONLY_TABLES

    assert len(V2_ONLY_TABLES) == len(set(V2_ONLY_TABLES))
