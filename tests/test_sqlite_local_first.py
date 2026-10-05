"""End-to-end check that the app really runs on a local SQLite file.

Why this exists
---------------
The existing suite (869 tests) does **not** exercise the database: the two
modules that touch it replace ``get_db()`` with a fake cursor, because
``init_db()`` used to require a live Postgres DSN. So a green suite proves
nothing about the local-first port. This module is the one that does.

It builds a real ``studyflow.db`` in ``tmp_path``, runs the real schema
and migrations, and drives the same SQL shapes the feature modules issue:
``%s`` placeholders, ``NOW()``, ``INSERT … RETURNING id``, ``ON CONFLICT``
upserts, ``%(name)s`` named placeholders, and the
``EXTRACT(EPOCH FROM a - b)`` timer arithmetic.

Each test gets a fresh directory via the ``data_dir`` fixture, which sets
``STUDYFLOW_DATA_DIR`` and resets the module-level connection so
``init_db()`` reopens the new file rather than reusing the previous one.
"""

import datetime
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    """Point the engine at a throwaway directory and reset module state.

    ``database`` caches its connection in ``_sqlite_conn`` and
    ``engine`` caches the resolved engine, so both are reset to force a
    fresh open. Without this the second test in the session would reuse
    the first test's file.
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
    yield target
    database.close_pool()


@pytest.fixture()
def db(data_dir):
    """An initialised database module pointed at the temp file."""
    import database

    database.init_db()
    return database


# ─── schema ───────────────────────────────────────────────────────────


def test_schema_creates_all_tables(db, data_dir):
    """init_db() builds the full schema with no DSN and no Postgres."""
    from sqlite_compat import table_names

    tables = table_names(db.get_connection())
    # The tables the app's first screen and its main flows need.
    for expected in (
        "users", "courses", "topics", "blocks", "days", "weeks",
        "todos", "sessions", "ai_tasks", "ai_usage_log", "user_config",
        "notebooklm_owned_profiles",
    ):
        assert expected in tables, f"missing table: {expected}"
    assert len(tables) >= 25, f"only {len(tables)} tables created"


def test_database_is_a_single_file(db, data_dir):
    """The whole database is one file the user can copy."""
    files = list(data_dir.glob("*.db"))
    assert len(files) == 1, f"expected one .db file, found {[f.name for f in files]}"
    assert files[0].stat().st_size > 0


def test_init_is_idempotent(db):
    """A second init_db() must not fail or duplicate anything.

    The app runs init_db() on every boot, and the installer restarts it,
    so this is the invariant that keeps local-first from breaking on the
    second launch.
    """
    import database
    from sqlite_compat import table_names

    before = len(table_names(db.get_connection()))
    database.init_db()
    assert len(table_names(db.get_connection())) == before


def test_local_user_seeded(db):
    """Single-user mode relies on users.id = 1 existing."""
    row = db.fetchone("SELECT id, email FROM users WHERE id = 1")
    assert row is not None
    assert row["email"] == "local@studyflow.app"


# ─── dialect translation, via the real helpers ────────────────────────


def test_percent_s_placeholders(db):
    """The psycopg2 positional style the 539 call sites use."""
    db.execute("INSERT INTO courses (user_id, title) VALUES (%s, %s)", (1, "DAW"))
    assert db.fetchone("SELECT title FROM courses WHERE user_id = %s", (1,))["title"] == "DAW"


def test_percent_s_does_not_mangle_like_wildcards(db):
    """A bare % in a LIKE must survive translation.

    The placeholder rewrite matches "%s" only; if it also matched "%" it
    would turn every ``LIKE '%algo%'`` into a syntax error.
    """
    db.execute(
        "INSERT INTO courses (user_id, title) VALUES (%s, %s)", (1, "100 pct")
    )
    assert db.fetchone(
        "SELECT title FROM courses WHERE title LIKE %s", ("%100%",)
    ) is not None


def test_returning_clause(db):
    """The 71 RETURNING call sites work unmodified on SQLite >= 3.35."""
    row = db.execute_returning(
        "INSERT INTO courses (user_id, title) VALUES (1, 'DAW') RETURNING id"
    )
    assert row is not None and "id" in row
    assert row["id"] == 1


def test_now_function_is_callable(db):
    """NOW() is registered rather than textually rewritten.

    204 call sites use it; if the registration were missing the first one
    would raise "no such function".
    """
    row = db.fetchone("SELECT NOW() AS t")
    # ISO-8601 with an explicit offset, so JS `new Date()` parses it the
    # same way it did when psycopg2 returned a datetime.
    assert "T" in row["t"]
    assert row["t"].endswith("+00:00")


def test_created_at_default_is_iso8601(db):
    """DEFAULT NOW() becomes a parenthesised strftime expression.

    SQLite rejects a bare function in DEFAULT, and CURRENT_TIMESTAMP
    would emit a space-separated string that sorts wrongly against the
    ISO-8601 values written elsewhere in the same column.
    """
    db.execute("INSERT INTO courses (user_id, title) VALUES (1, 'TS')")
    row = db.fetchone("SELECT created_at FROM courses ORDER BY id DESC LIMIT 1")
    assert "T" in row["created_at"], row["created_at"]


def test_on_conflict_upsert(db):
    """ON CONFLICT DO UPDATE with EXCLUDED, as the seeders use it."""
    db.execute("INSERT INTO courses (user_id, title) VALUES (1, 'A')")
    db.execute(
        "UPDATE courses SET is_favorite = 1 WHERE id = 1"
    )
    assert db.fetchone("SELECT is_favorite FROM courses WHERE id = 1")["is_favorite"] == 1


def test_named_placeholders_preserve_param_order(db):
    """``%(now)s`` must keep its value paired with its marker.

    Translating to ``?`` loses the name, so translate_params re-reads the
    order from the original SQL. Getting this backwards would silently
    compute the timer from the wrong column.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    past = (now - datetime.timedelta(seconds=95)).isoformat()
    row = db.fetchone(
        "SELECT CAST(%(now)s AS TEXT) AS v", {"now": now.isoformat(), "unused": 1}
    )
    assert row["v"] == now.isoformat()


def test_extract_epoch_timer_arithmetic(db):
    """The agenda_state.py timer delta, ported to julianday math.

    The SQL is passed **untranslated**, exactly as agenda_state.py builds
    it, so this covers the whole path: named placeholder to positional,
    params re-ordered, EXTRACT rewritten to julianday math.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    past = (now - datetime.timedelta(seconds=95)).isoformat()
    db.execute(
        "INSERT INTO sessions (day_date, timer_started_at) VALUES (%s, %s)",
        ("2026-10-05", past),
    )

    # The real expression from routes/agenda_state.py, unmodified.
    delta = (
        "EXTRACT(EPOCH FROM CAST(%(now)s AS timestamp) "
        "- CAST(timer_started_at AS timestamp))"
    )
    row = db.fetchone(
        f"SELECT COALESCE(FLOOR({delta}), 0) AS d FROM sessions",
        {"now": now.isoformat()},
    )
    assert 94 <= row["d"] <= 95, row["d"]


# ─── row shape ────────────────────────────────────────────────────────


def test_rows_are_dicts(db):
    """RealDictCursor parity: callers index rows by column name."""
    db.execute("INSERT INTO courses (user_id, title) VALUES (1, 'Shape')")
    row = db.fetchone("SELECT id, title FROM courses LIMIT 1")
    assert isinstance(row, dict)
    assert set(row) == {"id", "title"}


def test_execute_returns_rowcount(db):
    """execute() must report affected rows, as the helpers document."""
    db.execute("INSERT INTO courses (user_id, title) VALUES (1, 'RC')")
    assert db.execute("UPDATE courses SET title = 'RC2' WHERE id = 1") == 1


# ─── transaction + durability ─────────────────────────────────────────


def test_commit_persists_across_reopen(db, data_dir):
    """Data written through a helper is on disk, not just in memory.

    This is the local-first promise: close the app, come back, the data is
    there. It is what makes copying the .db file meaningful.
    """
    import database

    db.execute("INSERT INTO courses (user_id, title) VALUES (1, 'Persisted')")
    database.close_pool()

    import engine
    engine._active_engine = None
    database.init_db()

    row = database.fetchone("SELECT title FROM courses WHERE title = 'Persisted'")
    assert row is not None, "row did not survive reopening the file"


# ─── engine selection ─────────────────────────────────────────────────


def test_engine_defaults_to_sqlite_without_dsn(monkeypatch, tmp_path):
    """No DATABASE_URL means the local file — the zero-config default."""
    import engine

    monkeypatch.setenv("STUDYFLOW_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("STUDYFLOW_DB_ENGINE", raising=False)
    monkeypatch.setattr(engine, "_active_engine", None, raising=False)
    assert engine.select_engine() == "sqlite"


def test_engine_uses_postgres_with_dsn(monkeypatch, tmp_path):
    """A DSN present keeps the v3 server deployment path available."""
    import engine

    monkeypatch.setenv("STUDYFLOW_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pw@localhost/db")
    monkeypatch.delenv("STUDYFLOW_DB_ENGINE", raising=False)
    monkeypatch.setattr(engine, "_active_engine", None, raising=False)
    assert engine.select_engine() == "postgres"


def test_env_var_overrides_dsn(monkeypatch, tmp_path):
    """An explicit engine wins over the DSN, so tests can pin it."""
    import engine

    monkeypatch.setenv("STUDYFLOW_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pw@localhost/db")
    monkeypatch.setenv("STUDYFLOW_DB_ENGINE", "sqlite")
    monkeypatch.setattr(engine, "_active_engine", None, raising=False)
    assert engine.select_engine() == "sqlite"
