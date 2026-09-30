"""DDL for the import notices.

Kept out of database.py on purpose: that file is already 802 lines and over
the size limit. init_db() imports this and calls one function — three lines
— rather than absorbing another table definition.

IF NOT EXISTS throughout, so booting against a database that already has the
table is a no-op. No user data lives here; notices are disposable.
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


def create_calendar_import_tables(cur):
    """Create the notice table and its index. Safe to call repeatedly."""
    cur.execute(_NOTICE_TABLE)
    cur.execute(_NOTICE_INDEX)