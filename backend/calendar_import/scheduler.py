"""The daily import job.

Three properties matter more than the threading itself:

1. **Only one worker runs it.** gunicorn starts 2 workers, each importing
   this module, so a naive thread would import twice and double every
   notice. `pg_try_advisory_lock` lets exactly one of them win; the other
   returns immediately. The lock is released when the connection closes, so
   a crashed holder does not wedge the job forever.

2. **A failing tick must not kill the thread.** A transient network error
   from Moodle would otherwise take out the schedule permanently, and the
   only symptom would be a silent app. Each tick is wrapped; the loop
   survives.

3. **The first tick waits.** Running during boot competes with everything
   else starting up, and a slow feed would delay the first request.

No external scheduler dependency: a daemon thread per worker plus the
advisory lock is enough for one job in a two-worker container, and avoids
adding APScheduler to the dependency tree.
"""

import logging
import threading
import time

logger = logging.getLogger(__name__)

# Stable lock id for this job. Any constant works as long as it does not
# collide with another advisory lock in the app.
_LOCK_ID = 918273645

_STARTUP_DELAY_S = 60
_TICK_INTERVAL_S = 24 * 60 * 60

_thread = None
_lock = threading.Lock()


def _acquire(conn):
    """pg_try_advisory_lock: True for exactly one of the racing workers."""
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s) AS got", (_LOCK_ID,))
        return bool((cur.fetchone() or {}).get("got"))


def _run_once(conn_factory, days):
    """One import pass for one user. Returns the number of new sessions.

    Separated from the loop so it can be exercised directly in tests without
    threads, timers or a real scheduler.
    """
    import database as db
    from calendar_import import notifications
    from calendar_import.importer import run_import_for_user

    conn = conn_factory()
    try:
        if not _acquire(conn):
            logger.info("calendar_import_skipped reason=lock_held_by_other_worker")
            return 0
        with conn.cursor() as cur:
            user_id, calendar_name, new_sessions = run_import_for_user(
                cur, days=days
            )
            if new_sessions:
                message = notifications.build_message(
                    calendar_name, new_sessions
                )
                notifications.store_pending(
                    cur, user_id, calendar_name, message
                )
        conn.commit()
        if new_sessions:
            logger.info(
                "calendar_import_done calendar=%s new=%d",
                calendar_name, len(new_sessions),
            )
        else:
            logger.info("calendar_import_noop calendar=%s", calendar_name)
        return len(new_sessions)
    finally:
        # Closing releases the advisory lock.
        conn.close()


def _loop(conn_factory, days, interval, startup_delay):
    """The daemon body. Never exits on error — that is the whole point."""
    if startup_delay:
        logger.info("calendar_scheduler_waiting delay_s=%d", startup_delay)
        time.sleep(startup_delay)
    while True:
        try:
            _run_once(conn_factory, days)
        except Exception:
            # Swallowed on purpose: an unhandled exception here would end the
            # thread and silently stop every future import.
            logger.exception("calendar_import_tick_failed")
        try:
            time.sleep(interval)
        except Exception:
            logger.exception("calendar_scheduler_sleep_failed")


def start(conn_factory, days, interval=_TICK_INTERVAL_S,
          startup_delay=_STARTUP_DELAY_S):
    """Start the daemon thread once per process. Safe to call repeatedly."""
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return _thread
        _thread = threading.Thread(
            target=_loop,
            args=(conn_factory, days, interval, startup_delay),
            name="calendar-import",
            daemon=True,
        )
        _thread.start()
        logger.info(
            "calendar_scheduler_started interval_s=%d days=%s",
            interval, days,
        )
        return _thread