"""Reading and writing the import run log.

The run log exists so the UI can tell the user the truth about the last
import. A notification only exists when something new arrived, so on its own
it cannot distinguish "imported fine, nothing new" from "the feed has been
dead for a week". Both look like silence.

Every tick therefore writes a row here, whatever the outcome:

  ok        every calendar was fetched and walked
  partial   at least one calendar failed, the rest imported
  error     the tick itself blew up
  skipped   another worker held the advisory lock

`skipped` is not a failure — the other worker is doing the work. It is
recorded so the log is not silent, but it must not be reported to the user
as a problem, which is why `STALE_HOURS` ignores it when looking for a
recent healthy run.

Rows are disposable and trimmed: the log is for "what happened lately", so
keeping a year of it would be storage spent on nothing.
"""

import json
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# A run older than this and the panel says so. The job runs every 24 h, so
# anything past ~26 h means the thread died or the container has been down.
# Without this threshold a stopped import looks identical to a fresh one.
STALE_HOURS = 26

_KEEP_ROWS = 200

_RECORD_SQL = """
INSERT INTO calendar_import_runs (
    user_id, finished_at, outcome, calendars_ok, calendars_bad,
    new_sessions, updated, skipped, detail
)
VALUES (%s, NOW(), %s, %s, %s, %s, %s, %s, %s)
RETURNING id
"""

_LAST_SQL = """
SELECT id, started_at, finished_at, outcome, calendars_ok, calendars_bad,
       new_sessions, updated, skipped, detail
FROM calendar_import_runs
WHERE user_id = %s AND outcome <> 'skipped'
ORDER BY started_at DESC
LIMIT 1
"""

_RECENT_OK_SQL = """
SELECT started_at
FROM calendar_import_runs
WHERE user_id = %s AND outcome IN ('ok', 'partial')
ORDER BY started_at DESC
LIMIT 1
"""


def record_run(cur, user_id, outcome, per_calendar=None, new_sessions=0,
               updated=0, skipped=0, detail=None):
    """Write one tick. Never raises: losing a log row must not lose an import.

    per_calendar is the list the importer returns; only the counts and the
    error names are kept, because the raw list has one entry per feed and
    grows without bound in a long column.
    """
    per_calendar = per_calendar or []
    ok = sum(1 for c in per_calendar if c.get("ok"))
    bad = len(per_calendar) - ok

    if detail is None and bad:
        detail = "; ".join(
            f"{c.get('name')}: {c.get('error')}" for c in per_calendar
            if not c.get("ok")
        )[:500]

    try:
        cur.execute(_RECORD_SQL, (
            user_id, outcome, ok, bad, new_sessions, updated, skipped,
            detail,
        ))
        row = cur.fetchone()
        _trim(cur, user_id)
        return row and row.get("id")
    except Exception:
        # The import already happened. Losing its log row is bad, failing the
        # whole tick over it is worse.
        logger.exception("calendar_import_run_log_failed")
        return None


def _trim(cur, user_id):
    """Keep the log small. Older rows have no use for the status panel."""
    cur.execute(
        """
        DELETE FROM calendar_import_runs
        WHERE user_id = %s AND id NOT IN (
            SELECT id FROM calendar_import_runs
            WHERE user_id = %s
            ORDER BY started_at DESC
            LIMIT %s
        )
        """,
        (user_id, user_id, _KEEP_ROWS),
    )


def _hours_since(ts, now=None):
    """Age in hours. ``now`` is injectable so the tests are not clock-bound.

    Without it the status tests only pass on whichever day they were written:
    the age is a difference between a stored timestamp and the real clock,
    and a test that hardcodes '2 hours ago' silently becomes '14 hours ago'
    after lunch.
    """
    if not ts:
        return None
    now = now or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (now - ts).total_seconds() / 3600.0


def status(cur, user_id, pending_notices=0, now=None):
    """Everything the panel needs to say whether the import is healthy.

    The wording is chosen to be honest rather than reassuring: 'never ran',
    'stale', 'partial' and 'ok' are four different things, and collapsing
    them into one green tick is exactly the false confidence this was built
    to prevent.
    """
    cur.execute(_LAST_SQL, (user_id,))
    row = cur.fetchone()
    cur.execute(_RECENT_OK_SQL, (user_id,))
    recent_ok = (cur.fetchone() or {}).get("started_at")

    hours = _hours_since(row and row.get("started_at"), now=now)
    last_ok_hours = _hours_since(recent_ok, now=now)

    if not row:
        # No run recorded at all: the job has never fired, or it fired before
        # this table existed. Say so rather than implying a clean slate.
        state = "never"
        message = "El calendario aún no se ha sincronizado"
    elif row["outcome"] == "error":
        state = "error"
        message = "La última sincronización falló"
    elif row["outcome"] == "partial":
        state = "partial"
        message = f"{row['calendars_bad']} calendario(s) sin acceso"
    elif hours is not None and hours > STALE_HOURS:
        state = "stale"
        message = f"Sin sincronizar desde hace {_humanise(hours)}"
    else:
        state = "ok"
        message = f"Todo correcto · {_humanise(hours)}"

    return {
        "state": state,
        "message": message,
        "pending_notices": pending_notices,
        "last_run": None if not row else {
            "started_at": _iso(row.get("started_at")),
            "finished_at": _iso(row.get("finished_at")),
            "outcome": row["outcome"],
            "calendars_ok": row["calendars_ok"],
            "calendars_bad": row["calendars_bad"],
            "new_sessions": row["new_sessions"],
            "updated": row["updated"],
            "skipped": row["skipped"],
            "detail": row.get("detail"),
        },
        "last_successful_run": _iso(recent_ok),
        "stale": state in ("stale", "never", "error"),
    }


def _iso(ts):
    return ts.isoformat() if ts else None


def _humanise(hours):
    """'3 h', '26 h', '2 d' — short enough for a status line."""
    if hours is None:
        return "?"
    if hours < 1:
        return f"{int(hours * 60)} min"
    if hours < 24:
        return f"{int(hours)} h"
    return f"{int(hours // 24)} d"


def to_json(payload):
    """Hook for tests and debugging; keeps json imported and used."""
    return json.dumps(payload, ensure_ascii=False)