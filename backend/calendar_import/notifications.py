"""The in-app notice for newly imported sessions.

build_message() is pure and tested, because it is the thing the user
actually reads and copies. It is deliberately plain text: the point of the
Copiar button is to paste it into a notes app or a chat, where markdown
headings and bullets render badly.

The store/fetch/mark_read half needs a cursor, so it takes one and returns
plain rows — no database module import, no globals. That keeps it testable
and keeps the connection handling in one place.
"""

from datetime import date

# Spanish weekday/month abbreviations, written out rather than relying on
# the server locale, which differs between the container and the host.
_WEEKDAYS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")
_MONTHS = ("ene", "feb", "mar", "abr", "may", "jun",
           "jul", "ago", "sep", "oct", "nov", "dic")


def _human_day(iso_day):
    """'2026-09-29' -> 'mar 29 sep'. Returns the input if unparseable."""
    try:
        d = date.fromisoformat(str(iso_day)[:10])
    except (TypeError, ValueError):
        return str(iso_day)
    return f"{_WEEKDAYS[d.weekday()]} {d.day:02d} {_MONTHS[d.month - 1]}"


def _one_line(session):
    """'mar 29 sep · 17:00-18:00 · Clase 2/16 DAW'"""
    day = _human_day(session.get("day_date"))
    start = session.get("start_time") or "?"
    end = session.get("end_time") or ""
    span = f"{start}-{end}" if end else str(start)
    title = (session.get("title") or session.get("summary") or "Sin título")
    return f"{day} · {span} · {title}"


def build_message(calendar_name, sessions):
    """Plain-text notice for the sessions a run just created.

    Returns None when nothing new was imported, so the caller can skip
    storing an empty notice — the user only ever sees it when something
    actually happened.
    """
    if not sessions:
        return None
    count = len(sessions)
    plural = "sesión nueva" if count == 1 else "sesiones nuevas"
    lines = [f"📅 {calendar_name}: {count} {plural}"]
    lines.extend(f"· {_one_line(s)}" for s in sessions)
    return "\n".join(lines)


_PENDING_SQL = """
SELECT id, calendar_name, message, created_at, read_at
FROM calendar_import_notifications
WHERE user_id = %s AND read_at IS NULL
ORDER BY created_at DESC
LIMIT %s
"""

_MARK_READ_SQL = """
UPDATE calendar_import_notifications SET read_at = NOW()
WHERE id = %s AND user_id = %s AND read_at IS NULL
"""

_STORE_SQL = """
INSERT INTO calendar_import_notifications (user_id, calendar_name, message)
VALUES (%s, %s, %s)
RETURNING id
"""


def store_pending(cur, user_id, calendar_name, message):
    """Persist one notice and return its id. No-op if message is falsy."""
    if not message:
        return None
    cur.execute(_STORE_SQL, (user_id, calendar_name, message))
    row = cur.fetchone()
    return (row or {}).get("id")


def fetch_pending(cur, user_id, limit=5):
    """Unread notices, newest first. Rows are dicts — RealDictCursor."""
    cur.execute(_PENDING_SQL, (user_id, limit))
    return [dict(r) for r in (cur.fetchall() or [])]


def mark_read(cur, user_id, notification_id):
    """Dismiss one notice. Idempotent; returns True when a row changed."""
    cur.execute(_MARK_READ_SQL, (notification_id, user_id))
    return bool((cur.fetchone() or {}).get("id"))