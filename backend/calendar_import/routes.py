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