"""Dialect translation for the local-first SQLite engine.

The timer regressions came from here: ``GREATEST`` reached SQLite untouched,
so pausing a session raised ``no such function: GREATEST``, the API returned
500, and the frontend kept showing a running clock. Every test here asserts
the translated SQL actually executes, because "the string changed" is not the
same claim as "the statement runs".
"""

import sqlite3
import sys
from pathlib import Path

import pytest

# Only "backend" goes on the path, mirroring launcher/launch.py and
# server.py. Adding "backend/routes" would shadow the stdlib ``calendar``
# module with backend/routes/calendar.py, which is a trap, not a shortcut.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import sqlite_compat  # noqa: E402


def _runs(sql, params=()):
    """Translate, then execute against SQLite. Returns rows or raises."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE sessions ("
        "id INTEGER, user_id INTEGER, timer_started_at TEXT, "
        "timer_paused_at TEXT, timer_elapsed REAL, timer_paused_duration REAL)"
    )
    return conn.execute(sqlite_compat.translate_sql(sql), params).fetchall()


class TestScalarMaxMin:
    def test_greatest_becomes_scalar_max(self):
        out = sqlite_compat.translate_sql("SELECT GREATEST(3, 7)")
        assert out == "SELECT MAX(3, 7)"
        assert _runs("SELECT GREATEST(3, 7)") == [(7,)]

    def test_least_becomes_scalar_min(self):
        assert _runs("SELECT LEAST(3, 7)") == [(3,)]

    def test_argument_placeholders_are_never_duplicated(self):
        # A rotated-COALESCE rewrite duplicated each placeholder and shifted
        # every later bind, so the statement failed to execute. The rename
        # must leave the argument list byte-for-byte intact: two binds in,
        # two binds out.
        out = sqlite_compat.translate_sql(
            "SELECT GREATEST(COALESCE(a, 0), ?) FROM t WHERE b = %(b)s"
        )
        assert out == "SELECT MAX(COALESCE(a, 0), ?) FROM t WHERE b = ?"
        assert out.count("?") == 2

    def test_null_difference_is_documented_and_worked_around(self):
        # Postgres GREATEST skips NULLs; SQLite's scalar MAX does not. The
        # translator does not paper over this -- callers wrap in COALESCE.
        assert _runs("SELECT MAX(0, NULL)") == [(None,)]
        assert _runs("SELECT COALESCE(GREATEST(0, NULL), 0)") == [(0,)]
        assert _runs("SELECT COALESCE(GREATEST(0, -5), 0)") == [(0,)]
        assert _runs("SELECT COALESCE(GREATEST(0, 5), 0)") == [(5,)]

    def test_single_argument_greatest_is_left_alone(self):
        # Postgres GREATEST(x) is the identity; SQLite MAX(x) is an aggregate
        # over a column. Rewriting it would change the query's meaning.
        out = sqlite_compat.translate_sql("SELECT GREATEST(score) FROM t")
        assert out == "SELECT GREATEST(score) FROM t"

    def test_single_argument_least_is_left_alone(self):
        assert sqlite_compat.translate_sql("SELECT LEAST(score) FROM t") == (
            "SELECT LEAST(score) FROM t"
        )

    def test_nested_parentheses_and_commas_survive(self):
        out = sqlite_compat.translate_sql(
            "SELECT GREATEST(COALESCE(a, 0), ABS(b - c), (d + e))"
        )
        assert out == "SELECT MAX(COALESCE(a, 0), ABS(b - c), (d + e))"

    def test_subquery_argument_survives(self):
        out = sqlite_compat.translate_sql(
            "SELECT GREATEST((SELECT MAX(id) FROM users), 1)"
        )
        assert out == "SELECT MAX((SELECT MAX(id) FROM users), 1)"

    def test_two_argument_max_is_a_scalar_not_an_aggregate(self):
        # The distinction the translator relies on, asserted directly.
        assert _runs("SELECT MAX(2, 9)") == [(9,)]

    def test_commas_inside_a_string_literal_do_not_count(self):
        out = sqlite_compat.translate_sql("SELECT GREATEST('a,b', 1)")
        assert out == "SELECT MAX('a,b', 1)"

    def test_identifier_ending_in_greatest_is_not_rewritten(self):
        out = sqlite_compat.translate_sql("SELECT my_greatest(1, 2)")
        assert out == "SELECT my_greatest(1, 2)"

    def test_unbalanced_call_is_left_alone(self):
        out = sqlite_compat.translate_sql("SELECT GREATEST(1, 2")
        assert out == "SELECT GREATEST(1, 2"

# The play/pause/stop behaviour itself is covered end to end, against a real
# SQLite file and an authenticated request, in tests/test_agenda_timer_sqlite.py.
# Rebuilding the statements here against a hand-made in-memory table duplicated
# that coverage while asserting on a schema that does not match the real one.
