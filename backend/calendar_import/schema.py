"""DDL for the import notices and the run log.

Kept out of database.py on purpose: that file is already over the size
limit. init_db() imports this and calls one function rather than absorbing
another table definition.

IF NOT EXISTS throughout, so booting against a database that already has the
tables is a no-op.

Two tables, because they answer different questions:

- `notifications` asks "what is the user waiting to read?" and only ever
  holds something when there was something new.
- `calendar_import_runs` asks "did the import work?" and records every tick
  including the boring and the broken ones. Without it, a failed import and
  an import with no new classes look identical — both leave no notification
  behind — so there is no way to tell the user everything is fine. That is
  the whole reason this table exists.
"""

_NOTICE_TABLE = """
CREATE TABLE IF NOT EXISTS calendar_import_notifications (
    id            SERIAL PRIMARY KEY,
    user_id       INTEGER     NOT NULL,
    calendar_name TEXT        NOT NULL,
    message       TEXT        NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    read_at       TIMESTAMPTZ
)
"""

_NOTICE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_calendar_notifications_pending
ON calendar_import_notifications (user_id, read_at, created_at DESC)
"""

_RUN_TABLE = """
CREATE TABLE IF NOT EXISTS calendar_import_runs (
    id            SERIAL PRIMARY KEY,
    user_id       INTEGER     NOT NULL,
    started_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at   TIMESTAMPTZ,
    outcome       TEXT        NOT NULL DEFAULT 'running',
    calendars_ok  INTEGER     NOT NULL DEFAULT 0,
    calendars_bad INTEGER     NOT NULL DEFAULT 0,
    new_sessions  INTEGER     NOT NULL DEFAULT 0,
    updated       INTEGER     NOT NULL DEFAULT 0,
    skipped       INTEGER     NOT NULL DEFAULT 0,
    detail        TEXT
)
"""

_RUN_INDEX = """
CREATE INDEX IF NOT EXISTS idx_calendar_runs_recent
ON calendar_import_runs (user_id, started_at DESC)
"""


def create_calendar_import_tables(cur):
    """Create the tables and their indexes. Safe to call repeatedly."""
    cur.execute(_NOTICE_TABLE)
    cur.execute(_NOTICE_INDEX)
    cur.execute(_RUN_TABLE)
    cur.execute(_RUN_INDEX)