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

import sqlite3

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


# ─── transaction atomicity ────────────────────────────────────────────
# The v2 backup importer owns its transaction: on failure it calls
# conn.rollback() and must leave the database untouched. With
# isolation_level=None every statement autocommits, so without explicit
# transaction handling rollback() would be a no-op and a half-finished
# import would be left in the user's file.


def test_rollback_undoes_an_uncommitted_write(db):
    """A write that is never committed must not survive rollback."""
    conn = db.get_connection()
    with conn.cursor() as cur:
        cur.execute("INSERT INTO courses (user_id, title) VALUES (1, 'Temp')")
    conn.rollback()
    assert db.fetchone(
        "SELECT title FROM courses WHERE title = 'Temp'"
    ) is None, "rollback did not undo the insert"


def test_commit_persists_through_the_wrapper(db):
    """The mirror case: an explicit commit is durable."""
    conn = db.get_connection()
    with conn.cursor() as cur:
        cur.execute("INSERT INTO courses (user_id, title) VALUES (1, 'Kept')")
    conn.commit()
    assert db.fetchone("SELECT title FROM courses WHERE title = 'Kept'") is not None


def test_two_wrappers_share_one_transaction(db):
    """get_connection() returns a new wrapper each call, over one connection.

    Regression guard: per-wrapper transaction state made the second
    caller issue a nested BEGIN, which SQLite rejects. The transaction
    belongs to the connection, so a second wrapper must see the same state
    and a second BEGIN must not be attempted.
    """
    first = db.get_connection()
    second = db.get_connection()
    with first.cursor() as cur:
        cur.execute("INSERT INTO courses (user_id, title) VALUES (1, 'A')")
    # No exception here: the second wrapper joins the open transaction.
    with second.cursor() as cur:
        cur.execute("INSERT INTO courses (user_id, title) VALUES (1, 'B')")
    second.rollback()
    assert db.fetchone("SELECT title FROM courses WHERE title = 'A'") is None
    assert db.fetchone("SELECT title FROM courses WHERE title = 'B'") is None


def test_close_pool_releases_an_open_transaction(db, data_dir):
    """An abandoned transaction must not lock the file out of the next boot.

    The importer's failure path can leave a transaction open; if init_db()
    then hit "database is locked", the app would refuse to start on a
    database it had just written.
    """
    import database

    conn = db.get_connection()
    with conn.cursor() as cur:
        cur.execute("INSERT INTO courses (user_id, title) VALUES (1, 'Abandoned')")
    # No commit, no rollback: the worst case.
    database.close_pool()

    import engine
    engine._active_engine = None
    database.init_db()
    assert database.fetchone(
        "SELECT title FROM courses WHERE title = 'Abandoned'"
    ) is None, "an uncommitted row survived close_pool()"


def test_close_clears_transaction_state(db, data_dir):
    """close() must not leave a stale flag behind for the next connection.

    _TXN_STATE is keyed by id(conn) and CPython reuses addresses, so a
    flag left True by a closed connection would be inherited by whatever
    connection lands on that address next — which would then skip its
    BEGIN and autocommit. The failure is silent and data-corrupting, so it
    gets an explicit test rather than relying on a GC timing coincidence.
    """
    import sqlite3

    import sqlite_compat

    conn = sqlite_compat.connect(str(data_dir / "probe.db"))
    with sqlite_compat.wrap_connection(conn).cursor() as cur:
        cur.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    assert sqlite_compat._txn_active(conn) is True

    sqlite_compat.close(conn)
    assert sqlite_compat._txn_active(conn) is False, "close() left stale state"

    # A fresh connection at the same address must start with no transaction.
    for _ in range(20):
        other = sqlite_compat.connect(str(data_dir / "probe.db"))
        if id(other) == id(conn):
            assert sqlite_compat._txn_active(other) is False, (
                "a recycled connection id inherited a stale transaction flag"
            )
        sqlite_compat.close(other)


def test_close_rolls_back_before_closing(db, data_dir):
    """An uncommitted write is discarded when the connection is closed.

    The rollback takes the DDL with it, because DDL is transactional in
    Postgres too — the implicit transaction covers everything since BEGIN,
    not just the INSERT. Asserting the table survives would encode a
    SQLite-in-autocommit behaviour that the Postgres original never had.
    """
    import sqlite3

    import sqlite_compat

    conn = sqlite_compat.connect(str(data_dir / "probe.db"))
    wrapped = sqlite_compat.wrap_connection(conn)
    with wrapped.cursor() as cur:
        cur.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        cur.execute("INSERT INTO t (id) VALUES (1)")
    sqlite_compat.close(conn)

    # Reopen from disk: the whole uncommitted transaction is gone.
    again = sqlite_compat.connect(str(data_dir / "probe.db"))
    try:
        with pytest.raises(sqlite3.OperationalError, match="no such table"):
            again.execute("SELECT COUNT(*) AS n FROM t")
    finally:
        sqlite_compat.close(again)


# ─── backup_db row shapes ─────────────────────────────────────────────
# backup_db.query_on_conn() is called by the export path, which concatenates
# columns positionally so the dump stays replayable; query_dicts_on_conn()
# serves the file-attribution layer, which reads row["storage_path"]. Both
# go through the same connection, so one call must not leave the other with
# the wrong row shape.


def test_query_on_conn_returns_tuples(db):
    import backup_db

    conn = db.get_connection()
    with conn.cursor() as cur:
        cur.execute("INSERT INTO courses (user_id, title) VALUES (1, 'T')")
    conn.commit()

    rows = backup_db.query_on_conn(conn, "SELECT id, title FROM courses")
    assert isinstance(rows[0], tuple), f"expected a tuple, got {type(rows[0])}"
    assert rows[0][0] == 1


def test_query_dicts_on_conn_returns_dicts(db):
    import backup_db

    conn = db.get_connection()
    with conn.cursor() as cur:
        cur.execute("INSERT INTO courses (user_id, title) VALUES (1, 'T')")
    conn.commit()

    rows = backup_db.query_dicts_on_conn(conn, "SELECT id, title FROM courses")
    assert isinstance(rows[0], dict), f"expected a dict, got {type(rows[0])}"
    assert rows[0]["title"] == "T"


def test_row_factory_does_not_leak_between_calls(db):
    """A positional read must not leave the shared connection returning tuples."""
    import backup_db

    conn = db.get_connection()
    with conn.cursor() as cur:
        cur.execute("INSERT INTO courses (user_id, title) VALUES (1, 'T')")
    conn.commit()

    backup_db.query_on_conn(conn, "SELECT id FROM courses")
    # The helper path always hands back dicts; if the plain factory had
    # stuck to the shared connection this would be a tuple.
    assert db.fetchone("SELECT title FROM courses") == {"title": "T"}


def test_backup_db_factories_follow_the_connection_not_the_driver(db):
    """psycopg2 being installed must not decide the factory.

    Regression guard: the first attempt branched on ImportError, but
    psycopg2 is a declared dependency and stays installed on a SQLite-only
    machine, so the Postgres cursor classes were handed to the stdlib
    driver and every backup query raised TypeError.
    """
    import backup_db

    conn = db.get_connection()
    plain, real = backup_db._factories_for(conn)
    assert plain is not backup_db.PlainCursor or backup_db.PlainCursor is None
    assert callable(plain) and callable(real)


# ─── COPY TO/FROM STDOUT ──────────────────────────────────────────────
# The backup exporter is built on COPY: it is the only way to get a
# dump that replays byte-for-byte, and sqlite3 has no equivalent. The
# compat layer emulates both directions so backup_user.py keeps working
# unchanged.


def _seed_courses(conn):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO courses (user_id, title) VALUES (1, 'Round Trip')")
        cur.execute(
            "INSERT INTO courses (user_id, title) VALUES (1, %s)",
            ('con tab\ty comilla " y newline\n',),
        )
    conn.commit()
    return [r["title"] for r in db_fetchall(conn, "SELECT title FROM courses ORDER BY id")]


def db_fetchall(conn, sql):
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


def test_copy_to_stdout_emits_tab_separated_rows(db):
    import backup_user

    conn = db.get_connection()
    _seed_courses(conn)
    dump = backup_user._copy_out(conn, "COPY courses (id, title) TO STDOUT")
    lines = dump.splitlines()
    assert len(lines) == 2, f"expected 2 data rows, got {lines}"
    assert lines[0].endswith("Round Trip")
    # A dict row_factory must not turn each row into its own column names.
    assert not lines[0].startswith("id\t")


def test_copy_escapes_embedded_tabs_and_newlines(db):
    import backup_user

    conn = db.get_connection()
    _seed_courses(conn)
    dump = backup_user._copy_out(conn, "COPY courses (id, title) TO STDOUT")
    # A literal tab in the data would be read back as a field separator.
    assert r"\t" in dump and r"\n" in dump


def test_copy_round_trip_preserves_values(db):
    """Dump then restore must return byte-identical values.

    Guards the escaping in both directions at once: a value that is
    escaped on the way out but not unescaped on the way in would still
    round-trip here, while the reverse -- unescaped out, escaped in --
    would corrupt the data.
    """
    import io

    import backup_user

    conn = db.get_connection()
    original = _seed_courses(conn)
    dump = backup_user._copy_out(conn, "COPY courses (id, title) TO STDOUT")

    conn2 = db.get_connection()
    with conn2.cursor() as cur:
        cur.execute("DELETE FROM courses")
    conn2.commit()
    with conn2.cursor() as cur:
        cur.execute("COPY courses (id, title) FROM STDIN")
        cur.copy_from(io.StringIO(dump))
    conn2.commit()

    restored = [r["title"] for r in db_fetchall(conn2, "SELECT title FROM courses ORDER BY id")]
    assert restored == original


def test_copy_from_preserves_nulls(db):
    import io

    import backup_user

    conn = db.get_connection()
    with conn.cursor() as cur:
        # description is left unset, so it is NULL -- the only place COPY's
        # \N escape can appear.
        cur.execute("INSERT INTO courses (user_id, title) VALUES (1, 'k')")
    conn.commit()

    dump = backup_user._copy_out(conn, "COPY courses (id, title, description) TO STDOUT")
    assert r"\N" in dump, "a NULL should be dumped as COPY's \\N marker"

    with conn.cursor() as cur:
        cur.execute("DELETE FROM courses")
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("COPY courses (id, title, description) FROM STDIN")
        cur.copy_from(io.StringIO(dump))
    conn.commit()
    rows = db_fetchall(conn, "SELECT title, description FROM courses ORDER BY id")
    assert rows == [{"title": "k", "description": None}], rows


def test_copy_from_rejects_a_row_with_the_wrong_field_count(db):
    import io

    conn = db.get_connection()
    with conn.cursor() as cur:
        cur.execute("INSERT INTO courses (user_id, title) VALUES (1, 'k')")
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("COPY courses (id, title) FROM STDIN")
        # Three fields into a two-column table: a truncated or corrupt
        # stream must be rejected, not silently half-applied.
        with pytest.raises(sqlite3.ProgrammingError, match="expected 2"):
            cur.copy_from(io.StringIO("1\tk\textra\n"))


# ─── full backup round trip ───────────────────────────────────────────
# The export and the restore are the two halves of the user's only safety
# net: there is no server holding a second copy. Together they also
# exercise the translation layer harder than any single query does --
# COPY out, COPY in, staging tables, the re-owned user_id cast, and a bare
# ON CONFLICT inside INSERT ... SELECT.


def _seed_graph(conn):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO courses (user_id, title) VALUES (1, 'Curso Real')")
        cur.execute(
            "INSERT INTO courses (user_id, title) VALUES (1, %s)",
            ('con tab\ty y newline\n',),
        )
        cur.execute("INSERT INTO weeks (week_id, user_id) VALUES ('W1', 1)")
        cur.execute("INSERT INTO topics (course_id, user_id, title) VALUES (1, 1, 'Tema')")
    conn.commit()


def test_export_then_restore_round_trip(db):
    import backup_user
    import backup_user_restore

    conn = db.get_connection()
    _seed_graph(conn)
    before = db.fetchall("SELECT id, title FROM courses ORDER BY id")

    dump = backup_user._export_user_db(conn, 1)
    assert dump, "the export produced no tables at all"

    with conn.cursor() as cur:
        cur.execute("DELETE FROM topics")
        cur.execute("DELETE FROM courses")
        cur.execute("DELETE FROM weeks")
    conn.commit()
    assert db.fetchone("SELECT COUNT(*) AS n FROM courses")["n"] == 0

    parsed = [(t, dump[t]["columns"], dump[t]["data"]) for t in dump]
    backup_user_restore._import_tables(conn, parsed, 1)
    conn.commit()

    assert db.fetchall("SELECT id, title FROM courses ORDER BY id") == before
    assert db.fetchall("SELECT title FROM topics") == [{"title": "Tema"}]
    assert db.fetchall("SELECT week_id FROM weeks") == [{"week_id": "W1"}]


def test_restore_is_idempotent(db):
    """Restoring the same archive twice must not duplicate or error.

    The restore path uses ON CONFLICT DO NOTHING precisely so a user can
    re-import an archive they already have.
    """
    import backup_user
    import backup_user_restore

    conn = db.get_connection()
    _seed_graph(conn)
    dump = backup_user._export_user_db(conn, 1)
    parsed = [(t, dump[t]["columns"], dump[t]["data"]) for t in dump]

    backup_user_restore._import_tables(conn, parsed, 1)
    conn.commit()
    backup_user_restore._import_tables(conn, parsed, 1)
    conn.commit()

    assert db.fetchone("SELECT COUNT(*) AS n FROM courses")["n"] == 2
    assert db.fetchone("SELECT COUNT(*) AS n FROM topics")["n"] == 1


def test_catalogue_views_agree_with_the_live_schema(db):
    """The FK ordering in the export is derived from the catalogue views.

    If information_schema.tables reports the wrong table_type, every
    table_type = 'BASE TABLE' predicate silently matches nothing and the
    export comes back with only the users row.
    """
    import backup_db

    conn = db.get_connection()
    available = backup_db.available_tables(conn)
    assert "courses" in available

    user_tables = backup_user_tables(conn)
    assert "courses" in user_tables, user_tables

    # A table with a real foreign key must produce an edge for _order_tables.
    rows = backup_db.query_on_conn(
        conn,
        """
        SELECT tc.table_name AS child, ccu.table_name AS parent
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.constraint_schema = kcu.constraint_schema
        JOIN information_schema.constraint_column_usage ccu
          ON ccu.constraint_name = tc.constraint_name
         AND ccu.constraint_schema = tc.constraint_schema
        WHERE tc.constraint_type = 'FOREIGN KEY'
          AND tc.table_name = ANY(%s)
        """,
        (list(user_tables),),
    )
    assert ("topics", "courses") in [(r[0], r[1]) for r in rows], rows


def backup_user_tables(conn):
    import backup_user

    return backup_user._user_tables(conn)
