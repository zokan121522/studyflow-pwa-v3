# backend/database.py
"""PostgreSQL connection manager for StudyFlow PWA v3."""

import os
import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager
from typing import Optional, List, Dict, Any


# Global connection pool
_connection_pool: Optional[pool.ThreadedConnectionPool] = None


def init_db() -> None:
    """Initialize the connection pool and create tables if they don't exist."""
    global _connection_pool

    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        raise RuntimeError('DATABASE_URL environment variable not set')

    # maxconn=20: gunicorn runs 2 workers x 4 threads (see Dockerfile), so the
    # pool is per-process and each one can hold up to 20. Sized above the
    # concurrency it serves so a burst of parallel requests has headroom
    # instead of queueing on getconn(). The scheduler's daily import also
    # borrows one while requests are in flight.
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
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (id, email, password_hash, name)
                VALUES (1, 'local@studyflow.app', 'local-no-auth', 'Usuario Local')
                ON CONFLICT (id) DO NOTHING
                """
            )
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
    """Create all tables if they don't exist."""
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


@contextmanager
def get_db():
    """Get a database connection from the pool."""
    global _connection_pool
    if _connection_pool is None:
        init_db()

    conn = _connection_pool.getconn()
    try:
        yield conn
    finally:
        _connection_pool.putconn(conn)


def execute(query: str, params: tuple = None) -> int:
    """Execute a query (INSERT, UPDATE, DELETE) and return row count."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            conn.commit()
            return cur.rowcount


def fetchone(query: str, params: tuple = None) -> Optional[Dict[str, Any]]:
    """Fetch a single row as a dictionary."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchone()


def fetchall(query: str, params: tuple = None) -> List[Dict[str, Any]]:
    """Fetch all rows as a list of dictionaries."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchall()


def fetchone_raw(query: str, params: tuple = None):
    """Fetch a single row as a raw tuple (for counts, etc)."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchone()


def close_pool() -> None:
    """Close the connection pool."""
    global _connection_pool
    if _connection_pool:
        _connection_pool.closeall()
        _connection_pool = None


# ─── v2-compatible helpers (port) ─────────────────────────────────
def get_connection():
    """Borrow a raw connection from the pool (caller manages tx + close)."""
    if _connection_pool is None:
        init_db()
    return _connection_pool.getconn()


def put_connection(conn) -> None:
    """Return a connection borrowed via get_connection() to the pool."""
    if _connection_pool is not None:
        _connection_pool.putconn(conn)


def query(sql: str, params: tuple = None) -> List[Dict[str, Any]]:
    """Run a SELECT and return all rows as dicts."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def query_one(sql: str, params: tuple = None):
    """Run a SELECT and return the first row as a dict (or None)."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()


def execute_returning(sql: str, params: tuple = None):
    """Run INSERT/UPDATE/DELETE … RETURNING and return the first row.

    Returns None when no row was returned (e.g. zero rows affected).
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            conn.commit()
            return row