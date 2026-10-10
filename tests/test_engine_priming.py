"""The first status poll must not race engine selection.

On a cold import ``engine._active_engine`` is ``None`` until something
calls ``select_engine()`` — historically only ``init_db()``/``describe()``
did. ``get_db()`` kept its own "None -> SQLite when no DSN" fallback, but
``get_connection()`` matched only an explicit ``ENGINE_SQLITE``, so on the
very first request it took the Postgres branch, found ``_connection_pool``
still ``None`` and raised ``AttributeError: 'NoneType' object has no
attribute 'getconn'`` (database.py:1158).

``_db_status()`` swallows exceptions, so with the portable build
(``STUDYFLOW_DB_ENGINE=sqlite``) ``/api/status`` reported
``db: "disconnected"`` on the FIRST poll and recovered on the second; the
same window transiently 500'd cold-start routes such as ``/api/pdf/...``.

These tests pin the priming: with the engine deliberately unset, the first
``_db_status()`` call is already ``"connected"``, and both connection
entry points answer a ``SELECT 1`` against a throwaway temp-dir database —
never the developer's real data.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path[:0] = [str(BACKEND), str(ROOT)]


@pytest.fixture()
def cold_engine(tmp_path, monkeypatch):
    """A fresh SQLite data dir with the engine reset to "not chosen yet".

    Mirrors the ``data_dir`` fixture in test_sqlite_local_first.py: the
    engine caches its decision and the database caches its connections, so
    both are reset to reproduce a cold import against a brand-new file.
    """
    target = tmp_path / "data"
    target.mkdir()
    monkeypatch.setenv("STUDYFLOW_DATA_DIR", str(target))
    monkeypatch.setenv("STUDYFLOW_DB_ENGINE", "sqlite")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    import database
    import engine

    database.close_pool()
    monkeypatch.setattr(engine, "_active_engine", None, raising=False)
    assert engine.active_engine() is None, "engine must start unprimed"

    yield engine, database

    database.close_pool()


def test_first_status_poll_reports_connected(cold_engine):
    """FIRST ``_db_status()`` after a cold import says "connected"."""
    engine, _database = cold_engine

    # Importing the blueprint must not prime anything: the race is only
    # real if the engine is still unset when _db_status() runs.
    from routes.health import _db_status

    assert engine.active_engine() is None
    assert _db_status() == "connected"
    # The call itself primes, so the next poll agrees instead of flipping.
    assert engine.active_engine() == "sqlite"
    assert _db_status() == "connected"


def test_select_one_through_get_db_from_cold_start(cold_engine):
    """``get_db()`` builds the schema and answers SELECT 1 when unprimed."""
    engine, database = cold_engine

    with database.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 AS n")
            row = cur.fetchone()
            assert row is not None and row["n"] == 1
    assert engine.active_engine() == "sqlite"


def test_select_one_through_get_connection_from_cold_start(cold_engine):
    """``get_connection()`` must not fall into the empty Postgres pool."""
    engine, database = cold_engine

    conn = database.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 AS n")
            row = cur.fetchone()
            assert row is not None and row["n"] == 1
    finally:
        database.put_connection(conn)
    assert engine.active_engine() == "sqlite"
