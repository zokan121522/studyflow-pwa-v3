"""HTTP surface for the import notices.

Two endpoints, both scoped to the token's user: read what is pending, dismiss
it. Neither can touch another user's notices — the user id comes from the
token, never from the request body.

Kept separate from routes/calendar.py, which is at the size limit.
"""

from flask import Blueprint, jsonify, request

from calendar_import import notifications
from routes.auth import token_required

bp = Blueprint("calendar_import", __name__)


@bp.get("/calendar/notifications/pending")
@token_required
def pending_notifications(current_user_id: int):
    """Unread notices, newest first, for the banner."""
    import database as db

    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            rows = notifications.fetch_pending(cur, current_user_id, limit=5)
        return jsonify(notifications=rows)
    finally:
        db.put_connection(conn)


@bp.get("/calendar/status")
@token_required
def import_status(current_user_id: int):
    """Is the import healthy, and is anything waiting for the user?

    Called when the agenda opens. Deliberately cheap: one indexed lookup for
    the last run, one for the last good run, one for the unread count. The
    agenda must not wait on the network to render.
    """
    import database as db

    from calendar_import import runs

    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            pending = notifications.count_pending(cur, current_user_id)
            payload = runs.status(cur, current_user_id, pending)
        return jsonify(payload)
    finally:
        db.put_connection(conn)


@bp.post("/calendar/notifications/<int:notification_id>/read")
@token_required
def mark_notification_read(current_user_id: int, notification_id: int):
    """Dismiss one notice. Idempotent: marking twice is not an error."""
    import database as db

    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            changed = notifications.mark_read(cur, current_user_id, notification_id)
        conn.commit()
        return jsonify(ok=True, dismissed=changed)
    finally:
        db.put_connection(conn)

@bp.post("/calendar/sync")
@token_required
def sync_now(current_user_id: int):
    """Run one import pass right now, instead of waiting for the daily tick.

    Synchronous on purpose: the user clicked a button and is looking at the
    result. _run_once is the same function the scheduler calls, so this cannot
    drift from the scheduled behaviour, and it takes the same advisory lock, so
    clicking twice or clicking while the nightly run fires is safe.
    """
    import database as db
    from calendar_import import runs, scheduler

    days = int(request.args.get("days", 60))
    days = max(1, min(days, 365))

    conn_factory = db.get_connection
    new_sessions = scheduler._run_once(conn_factory, days=days)

    # Re-read the status so the chip reflects the run that just happened rather
    # than whatever it cached before the click.
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            pending_count = notifications.count_pending(
                cur, current_user_id
            )
            status = runs.status(cur, current_user_id, pending_count)
            pending = notifications.fetch_pending(
                cur, current_user_id, limit=5
            )
        return jsonify(new_sessions=new_sessions, status=status, notifications=pending)
    finally:
        db.put_connection(conn)
