"""Walking an ICS feed into session rows, and telling us what was new.

The route and the scheduled job share this, so the two cannot drift. The
important contract change from the old inline version: it now also reports
the sessions it *created*, because that is the only thing the user should be
told about. An update means the class moved; the user does not need a notice
for that, and notifying on every daily run would train them to ignore it.

Deduplication is unchanged and unchanged in spirit: the sanitised UID becomes
the primary key and ON CONFLICT updates, so re-running is idempotent. The
`is_insert` flag from RETURNING is what separates "new" from "updated" —
that is why a row alias bug here was so expensive.
"""

import logging
from datetime import date

from backend.models import iso_week_key
from backend.routes.agenda import _upsert_day, _upsert_week

from calendar_import.vevent import build_event_fields, extract_event_times

logger = logging.getLogger(__name__)

_UPSERT_ICS_SESSION_SQL = """
INSERT INTO sessions (
    id, day_date, week_id, user_id, category, state,
    start_time, end_time, title, notes
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (id) DO UPDATE SET
    day_date = EXCLUDED.day_date,
    start_time = EXCLUDED.start_time,
    end_time = EXCLUDED.end_time,
    title = EXCLUDED.title,
    notes = EXCLUDED.notes
RETURNING (xmax = 0) AS is_insert
"""


def process_one_vevent(cur, component, window_start, window_end,
                       local_tz, user_id, counters, new_sessions):
    """Insert or update a single VEVENT. Returns True when it was new."""
    times = extract_event_times(component, local_tz)
    if times is None:
        return False
    dtstart_local, dtend_local = times
    event_day = dtstart_local.date()
    if not (window_start <= event_day < window_end):
        counters["skipped_out_of_range"] += 1
        return False

    fields = build_event_fields(component, dtstart_local, dtend_local)
    if fields is None:
        return False

    week_id = iso_week_key(event_day)
    _upsert_week(week_id, user_id)
    _upsert_day(fields["day_date"], week_id, user_id)

    cur.execute(_UPSERT_ICS_SESSION_SQL, (
        fields["session_id"], fields["day_date"], week_id, user_id,
        "formal_study", "pending",
        fields["start_time"], fields["end_time"],
        fields["summary"], fields["notes"],
    ))
    row = cur.fetchone()
    # RealDictCursor: the row is a dict keyed by the RETURNING alias.
    is_insert = bool(row and row.get("is_insert"))
    counters["imported" if is_insert else "updated"] += 1
    if is_insert:
        new_sessions.append(dict(fields, title=fields["summary"]))
    return is_insert


def walk_calendar_into_sessions(cur, cal, window_start, window_end,
                                local_tz, user_id):
    """Import every VEVENT in the window. Returns (counters, new_sessions)."""
    counters = {"imported": 0, "updated": 0, "skipped_out_of_range": 0}
    new_sessions = []
    for component in cal.walk("VEVENT"):
        process_one_vevent(cur, component, window_start, window_end,
                           local_tz, user_id, counters, new_sessions)
    return counters, new_sessions


def run_import_for_user(cur, days, user_id=1, calendar_loader=None,
                        feed_fetcher=None, today=None):
    """Import every calendar registered for a user, via the supplied hooks.

    calendar_loader and feed_fetcher are injected rather than imported so
    this module stays free of Flask and of the routes package, and so a test
    can run the whole flow with no database rows. ``today`` is injected for
    the same reason: the window starts today, so a test that hardcodes a
    past date would silently test nothing.

    Returns (user_id, calendar_name, new_sessions, per_calendar).

    ``per_calendar`` is the part that matters for the status panel. This
    function used to log a failed feed and carry on, which was right for
    importing but made a dead Moodle indistinguishable from a quiet one:
    both produced zero new sessions. A status line built on that would tell
    the user everything is fine while nothing has been imported for a week.
    Each entry here is {name, ok, new, updated, skipped, error}, so the
    caller can say "2 calendars ok, 1 unreachable" instead of guessing.
    """
    from calendar_import.window import import_window

    calendar_loader = calendar_loader or _default_calendar_loader
    feed_fetcher = feed_fetcher or _default_feed_fetcher

    window_start, window_end = import_window(days=days, today=today)
    local_tz = _local_zone()

    calendars = calendar_loader(user_id)
    all_new = []
    names = []
    per_calendar = []
    for calendar in calendars:
        url = calendar.get("url")
        name = calendar.get("name") or "calendario"
        if not url:
            continue
        entry = {"name": name, "ok": False, "new": 0, "updated": 0,
                 "skipped": 0, "error": None}
        try:
            cal = feed_fetcher(url)
        except Exception as exc:
            # One unreachable feed must not stop the others from importing —
            # but it must be reported, or a dead feed reads as a quiet one.
            entry["error"] = f"{type(exc).__name__}: {exc}"[:300]
            logger.warning(
                "calendar_import_skip name=%s err=%s",
                name, type(exc).__name__,
            )
            per_calendar.append(entry)
            continue
        if cal is None:
            entry["error"] = "feed no parseable"
            per_calendar.append(entry)
            continue
        counters, new_sessions = walk_calendar_into_sessions(
            cur, cal, window_start, window_end, local_tz, user_id
        )
        entry["ok"] = True
        entry["new"] = counters["imported"]
        entry["updated"] = counters["updated"]
        entry["skipped"] = counters["skipped_out_of_range"]
        if new_sessions:
            names.append(name)
        all_new.extend(new_sessions)
        per_calendar.append(entry)

    label = names[0] if names else (calendars[0].get("name") if calendars else "")
    return user_id, label or "calendario", all_new, per_calendar


def _local_zone():
    """The user's configured zone, falling back to the host default."""
    from routes.calendar import _resolve_zone

    return _resolve_zone()


def _default_calendar_loader(user_id):
    from routes.calendar import _load_calendars

    return _load_calendars(user_id)


def _default_feed_fetcher(url):
    """Fetch and parse a feed. Returns None instead of raising on bad input."""
    from icalendar import Calendar

    from routes.calendar import _fetch_ics

    try:
        return Calendar.from_ical(_fetch_ics(url))
    except Exception:
        logger.warning("calendar_import_fetch_failed")
        return None