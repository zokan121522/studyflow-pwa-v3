# backend/database.py
"""Connection manager for StudyFlow PWA v3.

Serves two engines behind one set of helper functions:

- **SQLite** — the local-first default. One file on the user's disk, no
  server, no credentials. Selected when there is no ``DATABASE_URL``.
- **PostgreSQL** — the v3 server deployment, still what the integration
  tests against a live database use.

The choice is made once in ``init_db()`` and recorded in ``engine.py``.
Nothing below this docstring changes behaviour for the ~539 call sites:
they import ``execute``/``fetchall``/``fetchone``/``query`` and never see
which engine answered.

Dialect differences (Postgres ``%s`` placeholders, ``SERIAL``, ``DEFAULT
NOW()``, ``ADD COLUMN IF NOT EXISTS``, ``to_regclass``) are translated in
``sqlite_compat``. Keeping that out of the feature modules is what makes
the SQLite port a change to this file plus one new module, rather than a
change to 32 of them.
"""

import logging
import os
import threading
from contextlib import contextmanager
from typing import Optional, List, Dict, Any

import engine as engine_mod
from engine import ENGINE_POSTGRES, ENGINE_SQLITE

logger = logging.getLogger(__name__)

# psycopg2 is only imported on the Postgres path. Under SQLite the
# dependency is absent from the install, and an import-time reference
# would make the whole app unstartable on a machine that never had it.
try:  # pragma: no cover - exercised by whichever engine the test uses
    import psycopg2
    from psycopg2 import pool
    from psycopg2.extras import RealDictCursor
    _HAVE_PSYCOPG2 = True
except ImportError:  # pragma: no cover
    psycopg2 = None
    pool = None
    RealDictCursor = None
    _HAVE_PSYCOPG2 = False

import sqlite_compat


# Global connection pool. A ThreadedConnectionPool on Postgres; a single
# sqlite3 connection on SQLite, where the driver has no pool and the
# database is one local file.
_connection_pool = None

#: SQLite connections, keyed by thread.
#:
#: One connection per thread, not one per process. A single shared connection
#: opened with check_same_thread=False deadlocks: measured against the running
#: app, 36 of 40 concurrent requests timed out and the rest returned errors
#: like "error return without exception set". Sharing a handle across threads
#: is only safe when something serialises it, and a Flask dev server hands each
#: request to its own thread, so there is no such thing here.
#:
#: Per-thread also turns out to be the *correct* transaction semantics rather
#: than merely a safe one. With one connection shared by the process, a second
#: thread's statements landed inside the first thread's open transaction and
#: could roll back work that had nothing to do with it. One connection per
#: thread means one transaction per thread, which is what every caller already
#: assumes when it commits and closes its own handle.
#:
#: Keyed by thread ident because Flask's dev server creates a thread per
#: request and gunicorn reuses a pool of them, so the count is bounded by the
#: worker count rather than by traffic.
_SQLITE_CONNS: "dict[int, Any]" = {}

#: Held only while _SQLITE_CONNS is mutated: creating a connection is not
#: thread-safe, and two threads must not both decide to create the schema.
_SQLITE_REGISTRY_LOCK = threading.RLock()

#: Whether this process has already built the schema in the SQLite file.
#:
#: This is per *process*, never per thread, and getting that wrong was the
#: source of the last round of "database is locked" failures. get_db() used to
#: call init_db() whenever the calling thread had no registered connection,
#: which is every request thread a Flask dev server spawns. So five concurrent
#: SELECT 1 requests re-ran the entire CREATE TABLE sequence five times, at
#: once, on the same file. DDL contention also fails with SQLITE_BUSY in a
#: form the busy handler does not wait out, so it surfaced as errors rather
#: than as slowness: 5 of 6 threads lost outright.
#:
#: Building the schema is a boot-time concern. Once per process is the
#: correct number, and it is also what init_db() has always meant everywhere
#: else in this codebase.
_SCHEMA_READY = False

#: Set once the schema is fully built, and waited on by any thread that
#: arrives while another one is still running the DDL. Without it such a
#: thread would carry straight on and query tables that do not exist yet,
#: which is the same class of bug as the per-thread init this replaced.
_SCHEMA_DONE = threading.Event()

#: Thread ident of whoever is currently running the DDL, so that thread can
#: re-enter through get_db() (the schema builder uses the same helpers as
#: every other caller) without waiting on itself.
_SCHEMA_OWNER: "int | None" = None


def _ensure_schema_ready() -> None:
    """Build the schema once per process, from whichever thread gets there first."""
    global _SCHEMA_OWNER
    if _SCHEMA_DONE.is_set():
        return
    me = threading.get_ident()
    if _SCHEMA_OWNER == me:
        return  # our own DDL calling back in
    with _SQLITE_REGISTRY_LOCK:
        if _SCHEMA_DONE.is_set():
            return
        if _SCHEMA_OWNER is None:
            _SCHEMA_OWNER = me
            try:
                _init_db_sqlite()
            except BaseException:
                # Stay retryable: a schema half-built by a failed migration
                # must not be treated as a finished one.
                _SCHEMA_OWNER = None
                raise
            _SCHEMA_OWNER = None
            _SCHEMA_DONE.set()
            return
    # Another thread is building it. Waiting is the honest answer -- starting
    # anyway would mean querying a schema that does not exist yet.
    _SCHEMA_DONE.wait(timeout=60)


def _sqlite_conn_for_thread():
    """The calling thread's SQLite connection, creating it if needed."""
    key = threading.get_ident()
    conn = _SQLITE_CONNS.get(key)
    if conn is None:
        # Create outside the registry lock: opening a file is slow and holding
        # a lock across it would serialise every request behind the first.
        # A benign race just means two connections are made and one is closed.
        fresh = sqlite_compat.connect(str(engine_mod.sqlite_path()))
        with _SQLITE_REGISTRY_LOCK:
            existing = _SQLITE_CONNS.setdefault(key, fresh)
            if existing is not fresh:
                sqlite_compat.close(fresh)
        conn = _SQLITE_CONNS[key]
    return conn


def init_db() -> None:
    """Open the database and create the schema if it does not exist.

    Engine selection lives in engine.select_engine(): no ``DATABASE_URL``
    means SQLite (the local-first default), which is what lets the app
    start on a clean machine with no configuration at all.
    """
    global _connection_pool

    global _SCHEMA_READY

    engine = engine_mod.select_engine()
    if engine == ENGINE_SQLITE:
        _init_db_sqlite()
        _SCHEMA_READY = True
        _SCHEMA_DONE.set()
        return

    _init_db_postgres()


def _init_db_sqlite() -> None:
    """Open (or create) the local SQLite file and build the schema.

    No pool: the stdlib driver has none, and a single local file is not a
    contended resource the way a shared Postgres is. The connection is
    opened with ``check_same_thread=False`` because gunicorn's gevent
    worker serves requests as greenlets off one OS thread.

    Schema creation is the same sequence ``_init_db_postgres`` runs, minus
    the advisory lock — SQLite takes an exclusive lock on the file itself
    for the duration of the write transaction, which is the equivalent
    guarantee when there is one process.
    """
    if not _HAVE_PSYCOPG2 or sqlite_compat is None:  # pragma: no cover
        pass  # sqlite needs no third-party driver; presence of psycopg2 is irrelevant

    # Re-init must release the previous handle first. The old connection is
    # left holding an open transaction (the schema transaction above is
    # committed, but a failed or abandoned one is not), and an open
    # transaction keeps a write lock on the file — so the second init_db()
    # would hit "database is locked" on the very database it just wrote.
    # Close, do not merely drop: a handle left open still holds its file
    # locks, so the second init_db() would hit "database is locked" on the
    # very database it just wrote. That was the reason the previous handle was
    # closed here in the first place, and it still is.
    previous = _SQLITE_CONNS.pop(threading.get_ident(), None)
    if previous is not None:
        sqlite_compat.close(previous)
    conn = _sqlite_conn_for_thread()

    # Not a `with` block: the wrapper's __exit__ closes the connection, but
    # this thread's handle stays open and is reused by the rest of its
    # requests. Schema creation is one transaction, ended explicitly.
    schema = sqlite_compat.wrap_connection(conn)
    _create_tables(schema.cursor())
    schema.commit()

    # Seed local user (id=1) and the addon catalog, same as Postgres. The
    # seed is idempotent so a restart is a no-op.
    _seed_local_user()

    from addons_seed import seed_addon_catalog
    seed_addon_catalog()

    # Feature-owned DDL. These live in their feature packages (Phase 9
    # out-of-scope note in the Postgres path applies here too).
    from calendar_import.schema import create_calendar_import_tables
    with get_db() as conn:
        with sqlite_compat.cursor(conn) as cur:
            create_calendar_import_tables(cur)
        conn.commit()

    from images.schema import create_images_tables
    with get_db() as conn:
        with sqlite_compat.cursor(conn) as cur:
            create_images_tables(cur)
        conn.commit()


def _init_db_postgres() -> None:
    """Open the Postgres pool and build the schema (the v3 path)."""
    global _connection_pool

    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        raise RuntimeError('DATABASE_URL environment variable not set')
    if not _HAVE_PSYCOPG2:  # pragma: no cover
        raise RuntimeError(
            'psycopg2 is not installed but the postgres engine was selected. '
            'Install it, or unset DATABASE_URL to use the local SQLite file.'
        )

    # maxconn=20: the pool is per-process, so each gunicorn worker can hold up
    # to 20. Sized well above the concurrency one worker serves so a burst of
    # parallel requests has headroom instead of queueing on getconn().
    #
    # On worker count: this used to say "2 workers x 4 threads". That stopped
    # being true when the Dockerfile moved to
    # `--worker-class gevent_ws_worker.GeventWebSocketWorker --workers 1` so the
    # noVNC WebSocket could upgrade (the NotebookLM login depends on it). Under
    # the gevent worker, requests are served as greenlets off one OS thread, so
    # there is no fixed thread count to reason about and the pool must be sized
    # as margin rather than as a computed bound.
    #
    # What this number is NOT: a fix. The exhaustion bug was a connection being
    # closed instead of returned to the pool — that is fixed in
    # calendar_import/scheduler.py. maxconn=20 is headroom on top of that fix.
    # Raising it to paper over a leak would just delay the failure.
    #
    # The invariant is pinned by tests/test_pool_exhaustion.py
    # (TestPoolHasHeadroom), which derives the demand from the real gunicorn
    # config rather than from a hardcoded number that can drift.
    _connection_pool = pool.ThreadedConnectionPool(
        minconn=1,
        maxconn=20,
        dsn=database_url,
        cursor_factory=RealDictCursor
    )

    # Create tables (serialised across workers)
    # We need a connection before the pool is returned; use a one-off connection
    # to hold the advisory lock while creating/migrating tables.
    conn = psycopg2.connect(dsn=database_url, cursor_factory=RealDictCursor)
    try:
        cur = conn.cursor()
        try:
            try:
                cur.execute("SELECT pg_advisory_lock(0)")
            except Exception:
                pass
            _create_tables(cur)
            conn.commit()
        finally:
            try:
                cur.execute("SELECT pg_advisory_unlock(0)")
            except Exception:
                pass
            cur.close()
    finally:
        conn.close()

    # Seed local user (id=1) for single-user mode
    _seed_local_user()

    # Sub-phase SA — seed the addon catalog (idempotent upsert).
    from addons_seed import seed_addon_catalog
    seed_addon_catalog()

    # Phase 9 — calendar import notices. The DDL lives in the feature
    # package; database.py is already over the size limit and must not grow
    # a table definition (see Phase 9 out-of-scope).
    from calendar_import.schema import create_calendar_import_tables
    with get_db() as c2:
        with c2.cursor() as cur2:
            create_calendar_import_tables(cur2)
        c2.commit()

    # R4 — image blocks. Same arrangement as the calendar import above: the
    # DDL belongs to the feature package, database.py only wires it up.
    from images.schema import create_images_tables
    with get_db() as c3:
        with c3.cursor() as cur3:
            create_images_tables(cur3)
        c3.commit()


def _seed_local_user() -> None:
    """Ensure the implicit local user (id=1) exists so single-user mode works.

    Uses ON CONFLICT for idempotency. Resets the users.id sequence to avoid
    colliding with id=1 on future inserts.

    On SQLite there is no sequence to reset: AUTOINCREMENT is driven by
    ``sqlite_sequence``, and inserting the explicit id=1 updates that
    counter automatically. The equivalent Postgres call is skipped.
    """
    with get_db() as conn:
        cur = _cursor_for(conn)
        cur.execute(
            """
            INSERT INTO users (id, email, password_hash, name)
            VALUES (1, 'local@studyflow.app', 'local-no-auth', 'Usuario Local')
            ON CONFLICT (id) DO NOTHING
            """
        )
        if engine_mod.active_engine() != ENGINE_SQLITE:
            cur.execute(
                """
                SELECT setval(
                    pg_get_serial_sequence('users', 'id'),
                    GREATEST((SELECT MAX(id) FROM users), 1)
                )
                """
            )
        conn.commit()


def _create_tables(cur) -> None:
    """Create all tables if they don't exist.

    Takes an already-open cursor — a ``CompatCursor`` on SQLite, a
    RealDictCursor on Postgres — because both the feature DDL helpers and
    the migration helpers below are written against that one interface.
    """
    for ddl in _TABLE_DDL:
        cur.execute(ddl)
    _create_ai_tasks(cur)
    _create_ai_usage_log(cur)
    _create_user_config(cur)
    _migrate_user_config_v2(cur)
    _create_notebooklm_owned_profiles(cur)
    _migrate_sessions(cur)
    _migrate_habits(cur)
    _migrate_topic_notes(cur)
    _migrate_ai_tasks_v2(cur)
    _migrate_quiz_block_id(cur)
    _migrate_courses_favorites_order(cur)
    for stmt in _POST_INDEXES:
        cur.execute(stmt)


def _migrate_courses_favorites_order(cur) -> None:
    """Issue #12: port de las favoritas de v2 + orden manual de cursos.

    v2 marcaba asignaturas como favoritas y pintaba una sección propia en
    el nav y una fila de tarjetas en el dashboard (fase 72 F1/F2). v3 no
    tenía ni la columna ni el icon, así que la migración desde v2 las
    estaba descartando en silencio: seis asignaturas del backup real
    (SERVIDOR, CLIENTE, INTERFACES, DESPLIEGUE, IP2, CIBERSEGURIDAD).

    `order_index` replica el patrón que ya usan topics y blocks, de modo
    que el drag&drop de cursos no inventa un segundo criterio de orden.
    Idempotente, patrón _migrate_quiz_block_id.
    """
    for col, ddl in (
        ("is_favorite", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ("icon", "TEXT"),
        ("order_index", "INTEGER NOT NULL DEFAULT 0"),
    ):
        cur.execute(f"ALTER TABLE courses ADD COLUMN IF NOT EXISTS {col} {ddl}")
    # Un índice parcial: solo las favoritas, que es lo que consulta la UI.
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_courses_favorite "
        "ON courses(user_id, order_index) WHERE is_favorite"
    )


def _migrate_quiz_block_id(cur) -> None:
    """Issue #10 (parity v2): quiz por bloque. v2 liga tests a bloques
    `exercise` vía block_id; el quiz v3 solo conocía course/topic. Añade
    block_id (nullable) a quiz_questions y quiz_results en instalaciones
    existentes (idempotente, patrón _migrate_topic_notes).
    """
    cur.execute(
        "ALTER TABLE quiz_questions ADD COLUMN IF NOT EXISTS "
        "block_id INTEGER REFERENCES blocks(id) ON DELETE CASCADE"
    )
    cur.execute(
        "ALTER TABLE quiz_results ADD COLUMN IF NOT EXISTS "
        "block_id INTEGER REFERENCES blocks(id) ON DELETE SET NULL"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_quiz_questions_block "
        "ON quiz_questions(block_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_quiz_results_block "
        "ON quiz_results(block_id)"
    )


def _migrate_topic_notes(cur) -> None:
    """Sub-phase S4: add `notes` column to topics for the per-topic
    notes drawer (course-blocks frontend port). Idempotent so existing
    installs upgrade in-place without losing topics.
    """
    cur.execute(
        "ALTER TABLE topics ADD COLUMN IF NOT EXISTS notes TEXT DEFAULT ''"
    )


def _create_ai_tasks(cur) -> None:
    """Lightweight async-task table for the agenda Mind ritual (and any
    future async jobs the frontend polls via /api/ai/tasks/<id>).

    Replaces the v2 NotebookLM-backed pipeline with a tiny contract:
    coverage_data holds JSON like ``{"date":"...","step":"..."}`` and
    the frontend polls status until it's done|error|cancelled.
    """
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ai_tasks (
            id              TEXT PRIMARY KEY,
            user_id         INTEGER REFERENCES users(id) ON DELETE CASCADE,
            task_type       TEXT NOT NULL DEFAULT 'morning_mind',
            status          TEXT NOT NULL DEFAULT 'pending',
            coverage_data   TEXT DEFAULT '',
            error_message   TEXT DEFAULT '',
            result_content  TEXT DEFAULT '',
            created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            completed_at    TIMESTAMP WITH TIME ZONE
        )
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_ai_tasks_user_type "
        "ON ai_tasks(user_id, task_type)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_ai_tasks_user_status "
        "ON ai_tasks(user_id, status)"
    )


def _migrate_ai_tasks_v2(cur) -> None:
    """Widen `ai_tasks` to the v2 NotebookLM schema (Phase 7 — AI Hub).

    v3 created a minimal table for the agenda Mind ritual; v2's task runner
    stores richer metadata per job (source type/id, format, model, template,
    language, length). ADD COLUMN IF NOT EXISTS upgrades in place, matching
    the _migrate_topic_notes convention (no migration tool in this repo).
    """
    for col, ddl in [
        ("topic_id", "INTEGER"),
        ("format", "TEXT DEFAULT ''"),
        ("source_type", "TEXT DEFAULT ''"),
        ("source_id", "TEXT DEFAULT ''"),
        ("model_used", "TEXT DEFAULT ''"),
        ("template_id", "TEXT DEFAULT ''"),
        ("language", "TEXT DEFAULT ''"),
        ("length", "TEXT DEFAULT ''"),
    ]:
        cur.execute(
            f"ALTER TABLE ai_tasks ADD COLUMN IF NOT EXISTS {col} {ddl}"
        )


def _create_ai_usage_log(cur) -> None:
    """Per-request AI usage/cost log (v2 `ai_usage_log`).

    One row per model call; the /api/ai/usage endpoint aggregates by
    task_type for the daily-limit counters shown in the ✨ toolbar.
    """
    cur.execute("""
              CREATE TABLE IF NOT EXISTS ai_usage_log (
                  id          SERIAL PRIMARY KEY,
                  user_id     INTEGER REFERENCES users(id) ON DELETE CASCADE,
                  task_type   TEXT DEFAULT '',
                  source      TEXT DEFAULT '',
                  model_id    TEXT DEFAULT '',
                  provider_id TEXT DEFAULT '',
                  tokens      INTEGER DEFAULT 0,
                  cost        REAL DEFAULT 0,
                  created_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                  -- audio.py registra el consumo de TTS y v2_domains.py el de
                  -- OpenZen. Ambas escribían columnas que no existían aquí, así
                  -- que el INSERT fallaba al final de la tarea —con el audio ya
                  -- generado— y el consumo se perdía.
                  task_id            TEXT,
                  duration           TEXT,
                  input_tokens       INTEGER DEFAULT 0,
                  output_tokens      INTEGER DEFAULT 0,
                  reasoning_tokens   INTEGER DEFAULT 0,
                  cache_read_tokens  INTEGER DEFAULT 0,
                  cache_write_tokens INTEGER DEFAULT 0,
                  total_tokens       INTEGER DEFAULT 0
              )
          """)
    # ALTER por si la tabla ya existía de una versión anterior: el
    # CREATE TABLE de arriba no cambia nada si la tabla está creada.
    cur.execute("""
        ALTER TABLE ai_usage_log
            ADD COLUMN IF NOT EXISTS task_id            TEXT,
            ADD COLUMN IF NOT EXISTS duration           TEXT,
            ADD COLUMN IF NOT EXISTS input_tokens       INTEGER DEFAULT 0,
            ADD COLUMN IF NOT EXISTS output_tokens      INTEGER DEFAULT 0,
            ADD COLUMN IF NOT EXISTS reasoning_tokens   INTEGER DEFAULT 0,
            ADD COLUMN IF NOT EXISTS cache_read_tokens  INTEGER DEFAULT 0,
            ADD COLUMN IF NOT EXISTS cache_write_tokens INTEGER DEFAULT 0,
            ADD COLUMN IF NOT EXISTS total_tokens       INTEGER DEFAULT 0
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_ai_usage_user_date "
        "ON ai_usage_log(user_id, created_at)"
    )


def _create_user_config(cur) -> None:
    """Per-user AI configuration (v2 `user_config`).

    The DB row is the source of truth: active provider, voice/personality,
    the encrypted API-key blob (Fernet via secret_box, SECRET_KEY-derived)
    and the NotebookLM profile email attached to this user. Column names
    mirror v2 exactly (`provider`, `key_index`).
    """
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_config (
            user_id            INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            provider           TEXT DEFAULT 'notebooklm',
            personality        TEXT DEFAULT 'alvaro',
            voice_mode         TEXT DEFAULT '',
            vault_path         TEXT DEFAULT '',
            priority_context   TEXT DEFAULT '',
            api_keys_encrypted TEXT DEFAULT '',
            key_index          INTEGER DEFAULT 0,
            notebooklm_profile TEXT DEFAULT '',
            updated_at         TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)


def _migrate_user_config_v2(cur) -> None:
    """Phase 7: align a 7.0-created user_config with the v2 schema.

    7.0 bootstrapped the table with `active_provider`; v2 calls the column
    `provider` and also carries `key_index` (active API-key slot per user).
    Rename in place only if the old name still exists (idempotent), then
    ADD COLUMN IF NOT EXISTS for the remaining v2 fields.
    """
    cur.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'user_config' AND column_name = 'active_provider'"
    )
    if cur.fetchone():
        cur.execute(
            "ALTER TABLE user_config RENAME COLUMN active_provider TO provider"
        )
    for col, ddl in [
        ("key_index", "INTEGER DEFAULT 0"),
    ]:
        cur.execute(
            f"ALTER TABLE user_config ADD COLUMN IF NOT EXISTS {col} {ddl}"
        )


def _create_notebooklm_owned_profiles(cur) -> None:
    """Profile ownership map (v2 `notebooklm_owned_profiles`).

    Per-user isolation for NotebookLM cookie profiles: a profile email can
    only be switched/disconnected by the user who uploaded/created it, which
    prevents cross-user IDOR via /api/settings/notebooklm/profile/<email>.
    """
    cur.execute("""
        CREATE TABLE IF NOT EXISTS notebooklm_owned_profiles (
            user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            email      TEXT NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            PRIMARY KEY (user_id, email)
        )
    """)


def _migrate_habits(cur) -> None:
    """Drop legacy v3-shape `habits` + `habit_entries` tables and create the
    v2-shape columnar tables (habit_columns / habit_entries / habit_notes).

    Sub-phase D ported the Hábitos feature from v2 which uses polymorphic
    key/value entries per day instead of one row per habit. The legacy tables
    were created by an earlier v3 init and are now stale; CREATE TABLE IF NOT
    EXISTS can't replace them so we drop first.
    """
    cur.execute("DROP TABLE IF EXISTS habit_entries_old CASCADE")
    if _habits_is_legacy(cur):
        cur.execute("DROP TABLE IF EXISTS habit_entries CASCADE")
        cur.execute("DROP TABLE IF EXISTS habits CASCADE")
    _create_v2_habits(cur)


def _habits_is_legacy(cur) -> bool:
    """Return True if the existing `habit_entries` table has the v3 shape
    (SERIAL id, habit_id FK) rather than the v2 shape (date+key+user_id PK).
    """
    cur.execute(
        "SELECT to_regclass('public.habit_entries') AS reg"
    )
    row = cur.fetchone()
    if not row or not row["reg"]:
        return False  # table doesn't exist — nothing to migrate
    cur.execute(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name = 'habit_entries' AND column_name = 'id'"
    )
    id_row = cur.fetchone()
    return id_row is not None and id_row["data_type"] == "integer"


def _create_v2_habits(cur) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS habit_columns (
            key        TEXT NOT NULL,
            user_id    INTEGER REFERENCES users(id) ON DELETE CASCADE,
            label      TEXT NOT NULL,
            type       TEXT NOT NULL DEFAULT 'checkbox',
            "order"    INTEGER DEFAULT 0,
            note       TEXT DEFAULT '',
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            PRIMARY KEY (key, user_id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS habit_entries (
            date       TEXT NOT NULL,
            key        TEXT NOT NULL,
            user_id    INTEGER REFERENCES users(id) ON DELETE CASCADE,
            value      TEXT DEFAULT '',
            PRIMARY KEY (date, key, user_id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS habit_notes (
            date       TEXT NOT NULL,
            key        TEXT NOT NULL,
            user_id    INTEGER REFERENCES users(id) ON DELETE CASCADE,
            note       TEXT DEFAULT '',
            PRIMARY KEY (date, key, user_id)
        )
    """)


_TABLE_DDL = [
    """
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        email VARCHAR(255) UNIQUE NOT NULL,
        password_hash VARCHAR(255) NOT NULL,
        name VARCHAR(100),
        avatar_url VARCHAR(500),
        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS weeks (
        week_id        TEXT NOT NULL,
        user_id        INTEGER REFERENCES users(id) ON DELETE CASCADE,
        schema_version INTEGER DEFAULT 2,
        created_at     TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        updated_at     TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        PRIMARY KEY (week_id, user_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS days (
        date       TEXT NOT NULL,
        week_id    TEXT,
        user_id    INTEGER REFERENCES users(id) ON DELETE CASCADE,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        PRIMARY KEY (date, user_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS custom_categories (
        key        TEXT NOT NULL,
        user_id    INTEGER REFERENCES users(id) ON DELETE CASCADE,
        label      TEXT NOT NULL,
        icon       TEXT DEFAULT '📌',
        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        PRIMARY KEY (key, user_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quick_notes (
        user_id    INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        content    TEXT DEFAULT '',
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_settings (
        user_id        INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        calendars_json TEXT DEFAULT '[]',
        updated_at     TIMESTAMP WITH TIME ZONE DEFAULT NOW()
    )
    """,
    # S7b-B: Moodle credentials for the SCORM scraper. password_enc is a
    # Fernet token produced by backend/secret_box.py — never stored in clear.
    """
    CREATE TABLE IF NOT EXISTS scorm_credentials (
        user_id      INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        username     VARCHAR(255) NOT NULL,
        password_enc TEXT NOT NULL,
        updated_at   TIMESTAMP WITH TIME ZONE DEFAULT NOW()
    )
    """,
    # OpenZen (opencode-acp) subscription — the personal key that lets the
    # YouTubeZen / Gen. Contenido generators reach the sidecar. Same shape
    # as scorm_credentials: api_key_enc is a Fernet token from
    # backend/secret_box.py, never stored in clear. server_url / model
    # are NULLable so "not set" is distinguishable from "set to default".
    """
    CREATE TABLE IF NOT EXISTS openzen_credentials (
        user_id      INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        api_key_enc  TEXT NOT NULL,
        server_url   VARCHAR(255),
        model        VARCHAR(128),
        updated_at   TIMESTAMP WITH TIME ZONE DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS courses (
        id SERIAL PRIMARY KEY,
        user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
        title VARCHAR(255) NOT NULL,
        description TEXT,
        color VARCHAR(20),
        progress INTEGER DEFAULT 0,
        is_favorite BOOLEAN NOT NULL DEFAULT FALSE,
        icon TEXT,
        order_index INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS topics (
        id SERIAL PRIMARY KEY,
        course_id INTEGER REFERENCES courses(id) ON DELETE CASCADE,
        user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
        title VARCHAR(255) NOT NULL,
        description TEXT,
        notes TEXT DEFAULT '',
        order_index INTEGER DEFAULT 0,
        status VARCHAR(20) DEFAULT 'pending',
        estimated_minutes INTEGER,
        actual_minutes INTEGER DEFAULT 0,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pdfs (
        id SERIAL PRIMARY KEY,
        user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
        course_id INTEGER REFERENCES courses(id) ON DELETE SET NULL,
        topic_id INTEGER REFERENCES topics(id) ON DELETE SET NULL,
        filename VARCHAR(255) NOT NULL,
        original_name VARCHAR(255) NOT NULL,
        file_size BIGINT,
        page_count INTEGER,
        storage_path VARCHAR(500),
        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pdf_annotations (
        id SERIAL PRIMARY KEY,
        user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
        pdf_id INTEGER REFERENCES pdfs(id) ON DELETE CASCADE,
        page INTEGER NOT NULL,
        data JSONB NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        UNIQUE (pdf_id, page)
    )
    """,
    """
    -- Sub-phase S3: content blocks within topics (v2-shaped fields + url/color/collapsed).
    -- MUST be declared before quiz_questions / quiz_results: both carry a
    -- block_id FK, so creating them first breaks a fresh install with
    -- 'relation blocks does not exist'. On an existing DB the order was
    -- invisible because blocks was already there from an older release.
    CREATE TABLE IF NOT EXISTS blocks (
        id SERIAL PRIMARY KEY,
        user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
        course_id INTEGER REFERENCES courses(id) ON DELETE CASCADE,
        topic_id INTEGER REFERENCES topics(id) ON DELETE CASCADE,
        type TEXT NOT NULL DEFAULT 'markdown',
        title TEXT DEFAULT '',
        content TEXT DEFAULT '',
        url TEXT DEFAULT '',
        done BOOLEAN NOT NULL DEFAULT FALSE,
        order_index INTEGER NOT NULL DEFAULT 0,
        color TEXT DEFAULT '',
        collapsed BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quiz_questions (
        id SERIAL PRIMARY KEY,
        user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
        course_id INTEGER REFERENCES courses(id) ON DELETE CASCADE,
        topic_id INTEGER REFERENCES topics(id) ON DELETE SET NULL,
        block_id INTEGER REFERENCES blocks(id) ON DELETE CASCADE,
        question TEXT NOT NULL,
        options JSONB NOT NULL,
        correct_answer INTEGER NOT NULL,
        explanation TEXT,
        difficulty VARCHAR(20) DEFAULT 'medium',
        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quiz_results (
        id SERIAL PRIMARY KEY,
        user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
        course_id INTEGER REFERENCES courses(id) ON DELETE CASCADE,
        topic_id INTEGER REFERENCES topics(id) ON DELETE SET NULL,
        block_id INTEGER REFERENCES blocks(id) ON DELETE SET NULL,
        question_id INTEGER REFERENCES quiz_questions(id) ON DELETE CASCADE,
        selected_answer INTEGER,
        is_correct BOOLEAN,
        time_taken_ms INTEGER,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
    )
    """,
    """
    -- S5 (quíntesis del ecosistema Quiz v2): pool de falladas.
    -- v2 lo guardaba en el sidecar con claves TEXT (topic/tpl = títulos),
    -- sin block_id, lo que dejó 12 filas huérfanas al migrar (Engram
    -- migration/v2-quiz-history-deferred). Aquí se ancla a question_id,
    -- que es lo único estable, y se heredan course/topic/block de la
    -- pregunta (igual que quiz_results) para no depender de joins.
    --
    -- resolved_at en vez de DELETE: v2 borraba la fila al acertar, lo que
    -- destruía el histórico. Acertar la marca resuelta (sale de la pool,
    -- se puede reabrir) y el badge "falladas" conserva la trazabilidad.
    CREATE TABLE IF NOT EXISTS quiz_errors (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        question_id INTEGER NOT NULL REFERENCES quiz_questions(id) ON DELETE CASCADE,
        course_id INTEGER REFERENCES courses(id) ON DELETE CASCADE,
        topic_id INTEGER REFERENCES topics(id) ON DELETE SET NULL,
        block_id INTEGER REFERENCES blocks(id) ON DELETE CASCADE,
        wrong_count INTEGER NOT NULL DEFAULT 1,
        last_wrong_answer INTEGER,
        resolved_at TIMESTAMP WITH TIME ZONE,
        last_failed_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS todos (
        id SERIAL PRIMARY KEY,
        user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
        title VARCHAR(255) NOT NULL,
        description TEXT,
        completed BOOLEAN DEFAULT FALSE,
        priority VARCHAR(20) DEFAULT 'medium',
        due_date TIMESTAMP WITH TIME ZONE,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS audio_files (
        id SERIAL PRIMARY KEY,
        user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
        title VARCHAR(255) NOT NULL,
        text_content TEXT,
        audio_url VARCHAR(500),
        duration_seconds INTEGER,
        voice VARCHAR(50),
        language VARCHAR(10) DEFAULT 'es',
        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
    )
    """,
    # Sub-phase SA — Addons Foundation. Catalog + per-row installed/enabled/
    # hidden state (single-user v3: one row per slug). `installed`/`hidden`
    # are seeded by INSERT (so hidden-by-default rows aren't visible until the
    # IA phase ships); ON CONFLICT updates only name/description/version
    # so user choices survive re-seeding (mirrors v2 addons_seed.py).
    """
    CREATE TABLE IF NOT EXISTS addons (
        slug        TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        description TEXT DEFAULT '',
        version     TEXT DEFAULT '0.0.0',
        installed   BOOLEAN NOT NULL DEFAULT FALSE,
        enabled     BOOLEAN NOT NULL DEFAULT FALSE,
        hidden      BOOLEAN NOT NULL DEFAULT FALSE,
        url_prefix  TEXT,
        created_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        updated_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW()
    )
    """,
]


# DROP old v3-shape `sessions` (SERIAL id) if it exists, then recreate the
# v2-shape table that _TABLE_DDL declared above. Done in two steps so the
# legacy v3 schema isn't left around on already-initialized DBs.
_POST_DDL = [
    "DROP TABLE IF EXISTS sessions_old CASCADE",  # placeholder, see _migrate_sessions
]


def _migrate_sessions(cur) -> None:
    """DROP legacy v3 sessions (SERIAL id) and ensure v2-shape exists.

    CREATE TABLE IF NOT EXISTS cannot replace an existing table with a
    different schema, so the v3-shape legacy `sessions` (SERIAL) must be
    dropped before the v2-shape (TEXT) CREATE.
    """
    if _sessions_is_legacy(cur):
        cur.execute("DROP TABLE sessions CASCADE")
    _create_v2_sessions(cur)


def _sessions_is_legacy(cur) -> bool:
    cur.execute(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name = 'sessions' AND column_name = 'id'"
    )
    row = cur.fetchone()
    return row is not None and row["data_type"] != "text"


def _create_v2_sessions(cur) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id                   TEXT PRIMARY KEY,
            day_date             TEXT,
            user_id              INTEGER REFERENCES users(id) ON DELETE CASCADE,
            week_id              TEXT,
            category             TEXT NOT NULL DEFAULT 'formal_study',
            state                TEXT NOT NULL DEFAULT 'pending',
            start_time           TEXT,
            end_time             TEXT,
            title                TEXT DEFAULT '',
            notes                TEXT DEFAULT '',
            timer_state          TEXT,
            timer_started_at     TEXT,
            timer_paused_at      TEXT,
            timer_paused_duration REAL DEFAULT 0,
            timer_elapsed        REAL,
            timer_total          REAL,
            timer_paused         REAL,
            position             INTEGER DEFAULT 0,
            created_at           TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at           TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_day ON sessions(day_date)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user_day ON sessions(user_id, day_date)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user_week ON sessions(user_id, week_id)")


_POST_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_days_week ON days(week_id)",
    "CREATE INDEX IF NOT EXISTS idx_habit_entries_user_date ON habit_entries(user_id, date)",
    "CREATE INDEX IF NOT EXISTS idx_habit_notes_user_date ON habit_notes(user_id, date)",
    "CREATE INDEX IF NOT EXISTS idx_topics_course_order ON topics(course_id, order_index)",
    "CREATE INDEX IF NOT EXISTS idx_todos_user_completed ON todos(user_id, completed)",
    "CREATE INDEX IF NOT EXISTS idx_quiz_results_user_time ON quiz_results(user_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_blocks_course ON blocks(course_id)",
    "CREATE INDEX IF NOT EXISTS idx_blocks_topic_order ON blocks(topic_id, order_index)",
    "CREATE INDEX IF NOT EXISTS idx_blocks_user_course ON blocks(user_id, course_id)",
    # Pool de falladas. El UNIQUE parcial es la garantía de integridad que
    # le faltaba a v2 (append-only duplicaba la misma fallada cada intento):
    # como mucho UNA fila abierta por pregunta. Por eso el ON CONFLICT del
    # upsert debe repetir el predicado `WHERE resolved_at IS NULL`.
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_quiz_errors_open "
    "ON quiz_errors(user_id, question_id) WHERE resolved_at IS NULL",
    # Índice de la consulta que la UI ejecuta siempre: la pool abierta.
    "CREATE INDEX IF NOT EXISTS idx_quiz_errors_open "
    "ON quiz_errors(user_id, last_failed_at DESC) WHERE resolved_at IS NULL",
    "CREATE INDEX IF NOT EXISTS idx_quiz_errors_block "
    "ON quiz_errors(block_id, resolved_at)",
]


def _resolved_engine() -> str:
    """The engine both connection paths branch on, resolved in one place.

    ``get_db()`` and ``get_connection()`` have to agree even on a cold
    import, before anything has called ``select_engine()`` (the first
    ``/api/status`` poll races the app's own init). ``get_db()`` used to
    keep its own "no engine, no DSN -> SQLite" fallback while
    ``get_connection()`` only matched an explicit ``ENGINE_SQLITE``, so it
    fell into the Postgres branch where the pool is still ``None`` and
    raised ``AttributeError: 'NoneType' object has no attribute
    'getconn'`` — surfacing as ``db: "disconnected"`` on that first poll
    and a transient 500 on cold-start routes.

    One helper, no second fallback to drift: when nothing is recorded yet
    it defers to ``engine.select_engine()``, which applies exactly the
    rules ``init_db()`` would apply (forced engine, then DSN presence) and
    records the decision. Genuine connection errors below still raise.
    """
    active = engine_mod.active_engine()
    if active is not None:
        return active
    return engine_mod.select_engine()


@contextmanager
def get_db():
    """Yield a database connection, whichever engine is active.

    On Postgres this borrows from the pool and returns it afterwards. On
    SQLite it yields the single connection: the stdlib driver has no pool
    and there is one local file, so there is nothing to borrow from.
    The ``with``/``cursor()``/``commit()`` sequence the callers already
    use works unchanged on both — ``CompatCursor`` translates the dialect.
    """
    global _connection_pool
    engine = _resolved_engine()

    if engine == ENGINE_SQLITE:
        if not _SCHEMA_READY:
            _ensure_schema_ready()
        # The wrapper, not the raw handle. This is the whole point of the
        # function: the callers write ``with conn.cursor() as cur`` and
        # psycopg2's cursor is a context manager while sqlite3's is not, so
        # handing back the raw handle makes every one of those fail with
        # "does not support the context manager protocol" -- on SQLite
        # only, which is exactly where nobody tests by hand.
        conn = get_connection()
        try:
            yield conn
        except BaseException:
            _close_sqlite_txn(conn, 'get_db', commit=False)
            raise
        else:
            _close_sqlite_txn(conn, 'get_db', commit=True)
        return


    if _connection_pool is None:
        init_db()
    conn = _connection_pool.getconn()
    try:
        yield conn
    finally:
        _connection_pool.putconn(conn)


def _cursor_for(conn):
    """A dialect-translating cursor on SQLite, the raw one on Postgres.

    The Postgres pool is already configured with ``cursor_factory=
    RealDictCursor``, so its cursors need no wrapping. SQLite cursors do,
    which is what lets the feature modules keep issuing ``%s`` and
    Postgres DDL unchanged.
    """
    if engine_mod.active_engine() == ENGINE_SQLITE:
        return sqlite_compat.cursor(conn)
    return conn.cursor()


def execute(query: str, params: tuple = None) -> int:
    """Execute a query (INSERT, UPDATE, DELETE) and return row count."""
    with get_db() as conn:
        cur = _cursor_for(conn)
        cur.execute(query, params)
        conn.commit()
        return cur.rowcount


def fetchone(query: str, params: tuple = None) -> Optional[Dict[str, Any]]:
    """Fetch a single row as a dictionary."""
    with get_db() as conn:
        cur = _cursor_for(conn)
        cur.execute(query, params)
        return cur.fetchone()


def fetchall(query: str, params: tuple = None) -> List[Dict[str, Any]]:
    """Fetch all rows as a list of dictionaries."""
    with get_db() as conn:
        cur = _cursor_for(conn)
        cur.execute(query, params)
        return cur.fetchall()


def fetchone_raw(query: str, params: tuple = None):
    """Fetch a single row as a raw tuple (for counts, etc)."""
    with get_db() as conn:
        cur = _cursor_for(conn)
        cur.execute(query, params)
        return cur.fetchone()


def close_pool() -> None:
    """Close the database connection(s).

    On SQLite the shared connection may have an open transaction left by a
    caller that never committed or rolled back — the v2 importer's failure
    path does exactly that. Rolling back first matters: an open transaction
    holds a write lock on the file, and a later init_db() would fail with
    "database is locked".
    """
    global _connection_pool, _SCHEMA_READY
    if _connection_pool:
        _connection_pool.closeall()
        _connection_pool = None
    with _SQLITE_REGISTRY_LOCK:
        pending = list(_SQLITE_CONNS.values())
        _SQLITE_CONNS.clear()
        # The file behind those handles may be gone or replaced (tests point
        # it at a fresh temp directory), so the next request must be allowed
        # to build the schema again rather than assume it is there.
        _SCHEMA_READY = False
        _SCHEMA_DONE.clear()
    for conn in pending:
        # Ask the compat layer, not the driver: the connection runs with
        # isolation_level=None, so sqlite3's own in_transaction is always
        # False and would silently skip the rollback. The compat layer
        # tracks the implicit BEGIN it opens.
        sqlite_compat.close(conn)


# ─── v2-compatible helpers (port) ─────────────────────────────────
def get_connection():
    """Borrow a connection for callers that open their own cursors.

    On Postgres this borrows from the pool. On SQLite it returns the
    single shared connection **wrapped** in a CompatConnection, so the
    ~21 sites that call ``conn.cursor()`` directly — the v2 backup
    importer is the biggest cluster — get dialect translation without
    being rewritten. ``put_connection()`` is then a no-op rather than an
    error, which is what lets the importer's try/finally stay as it is.
    """
    global _connection_pool
    if _resolved_engine() == ENGINE_SQLITE:
        if threading.get_ident() not in _SQLITE_CONNS:
            init_db()
        # borrow(), not wrap_connection(): the caller hands this back, and
        # three of them do it with conn.close() out of pool habit. A borrowed
        # handle's close() ends the transaction and leaves the shared
        # connection open.
        return sqlite_compat.borrow(_sqlite_conn_for_thread())
    if _connection_pool is None:
        init_db()
    return _connection_pool.getconn()


def put_connection(conn) -> None:
    """Return a connection borrowed via get_connection() to the pool.

    On SQLite the connection is shared, not pooled, so there is nothing to
    return it to -- but the transaction still has to be closed, because the
    Postgres branch closes it as a side effect of resetting the pooled
    connection. Leaving it open was the single most damaging bug in the port:
    the leftover transaction held a RESERVED lock, so the next writer died
    with "database is locked" and every route behind it returned 500 from a
    healthy database. Fourteen call sites across seven modules share this
    seam, so the fix belongs here rather than in the callers.
    """
    if engine_mod.active_engine() == ENGINE_SQLITE:
        # Conservative: this is reached from a finally, so the transaction is
        # closed by rollback whether or not the request succeeded. Callers
        # that intend to keep their writes commit inside the block.
        _close_sqlite_txn(conn, 'put_connection', commit=False)
        return
    if _connection_pool is not None:
        _connection_pool.putconn(conn)


def _close_sqlite_txn(conn, source: str, *, commit: bool) -> None:
    """End the shared connection's transaction. Never leave it open.

    Leaving it open was the single most damaging bug in the port: the
    leftover transaction held a RESERVED lock, so the next writer died with
    "database is locked" and every route behind it returned 500 from a
    perfectly healthy database. A read-only request could trigger it.

    ``commit`` decides what happens to work the block left behind, and the
    two callers want opposite answers:

    * ``get_db`` commits on a clean exit, because the helpers built on it --
      ``query``, ``fetchone``, ``fetchall`` -- are used for INSERT ...
      RETURNING as well as SELECT, and none of them commits. That is not an
      oversight on their part: on Postgres the write simply stayed pending on
      the pooled connection and was committed by whatever ran next on it.
      Rollback there was equally arbitrary, in the other direction. Making it
      explicit is what lets the same code be correct on both engines.
    * ``put_connection`` rolls back, because it runs from a ``finally`` and
      cannot tell a completed request from one that just raised. Discarding is
      the safe reading of "I'm done with this connection": no partial work is
      ever persisted. Callers that mean to keep their writes commit inside
      the block, which is what the module's own docstring says they do.

    The warning fires only when writes are actually lost, so a read-only
    request -- which also leaves a transaction open, because the compat layer
    begins one on the first statement of any kind -- stays quiet, and the one
    warning that matters is not buried under it.
    """
    try:
        if not getattr(conn, 'in_transaction', False):
            return
        raw = getattr(conn, '_conn', conn)
        had_writes = sqlite_compat.txn_has_writes(raw)
        if commit and had_writes:
            conn.commit()
            return
        conn.rollback()
        if had_writes:
            logger.warning(
                '%s: rolled back a transaction left open with uncommitted '
                'writes -- the caller did not commit inside the block',
                source,
            )
    except Exception:
        logger.exception('%s: failed to close the transaction', source)


def query(sql: str, params: tuple = None) -> List[Dict[str, Any]]:
    """Run a SELECT and return all rows as dicts."""
    with get_db() as conn:
        cur = _cursor_for(conn)
        cur.execute(sql, params)
        return cur.fetchall()


def query_one(sql: str, params: tuple = None):
    """Run a SELECT and return the first row as a dict (or None)."""
    with get_db() as conn:
        cur = _cursor_for(conn)
        cur.execute(sql, params)
        return cur.fetchone()


def execute_returning(sql: str, params: tuple = None):
    """Run INSERT/UPDATE/DELETE … RETURNING and return the first row.

    Returns None when no row was returned (e.g. zero rows affected).
    Works on both engines: SQLite supports the RETURNING clause natively
    from 3.35, which sqlite_compat enforces.
    """
    with get_db() as conn:
        cur = _cursor_for(conn)
        cur.execute(sql, params)
        row = cur.fetchone()
        conn.commit()
        return row