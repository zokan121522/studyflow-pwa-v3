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

# The app is single-user local mode (see routes/auth.token_required), so there
# is exactly one user to import for. Noted here because it is the reason this
# is a constant and not a query over users.
_USER_ID = 1

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

    Every outcome is written to the run log, including the failure and the
    lock-skip. A run that fails silently is how a dead feed looks like a
    quiet one, and the status panel then lies to the user for a week.
    """
    import database as db
    from calendar_import import notifications, runs
    from calendar_import.importer import run_import_for_user

    conn = conn_factory()
    try:
        if not _acquire(conn):
            logger.info("calendar_import_skipped reason=lock_held_by_other_worker")
            with conn.cursor() as cur:
                runs.record_run(cur, _USER_ID, "skipped", detail="otro worker")
            conn.commit()
            return 0
        with conn.cursor() as cur:
            user_id, calendar_name, new_sessions, per_calendar = (
                run_import_for_user(cur, days=days)
            )
            if new_sessions:
                message = notifications.build_message(
                    calendar_name, new_sessions
                )
                notifications.store_pending(
                    cur, user_id, calendar_name, message
                )
            bad = sum(1 for c in per_calendar if not c.get("ok"))
            runs.record_run(
                cur, user_id,
                "partial" if bad else "ok",
                per_calendar=per_calendar,
                new_sessions=len(new_sessions),
                updated=sum(c.get("updated", 0) for c in per_calendar),
                skipped=sum(c.get("skipped", 0) for c in per_calendar),
            )
        conn.commit()
        logger.info(
            "calendar_import_done calendar=%s new=%d bad_calendars=%d",
            calendar_name, len(new_sessions), bad,
        )
        return len(new_sessions)
    except Exception as exc:
        # Recorded even though the import failed. Without this row the panel
        # would keep reporting the previous good run as if it were current.
        try:
            with conn.cursor() as cur:
                runs.record_run(
                    cur, _USER_ID, "error",
                    detail=f"{type(exc).__name__}: {exc}"[:500],
                )
            conn.commit()
        except Exception:
            logger.exception("calendar_import_error_log_failed")
        raise
    finally:
        # The connection came from the pool, so it has to go back to the pool.
        # Closing it here instead is what killed the pool: ThreadedConnectionPool
        # counts a connection as checked out until putconn() returns it, so
        # close() consumed one of the maxconn slots permanently. After ten
        # imports getconn() raised PoolError and the whole app failed while
        # Postgres sat idle with nothing on it.
        #
        # The advisory lock is session-scoped, so it does NOT drop just because
        # the connection is reused — it has to be released by hand first. Skip
        # that and every future tick is skipped in silence: the calendar stops
        # importing and the panel keeps showing the last good run.
        try:
            # Clear any aborted transaction first, or the unlock itself errors
            # with InFailedSqlTransaction and the lock survives the reuse.
            conn.rollback()
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_ID,))
                cur.fetchone()
        except Exception:
            # Never let cleanup stop the connection from going back: a leaked
            # slot is the failure we are here to fix.
            logger.exception("calendar_advisory_unlock_failed")
        finally:
            db.put_connection(conn)


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