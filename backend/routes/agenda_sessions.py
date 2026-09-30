"""
Agenda · per-session operations (CRUD, move, copy, bulk-move) +
month timeline + state list — port from studyflow-hub v2.

All routes use @token_required. Session ids are TEXT uuids.
"""
import uuid
from datetime import date, datetime, timedelta
from flask import Blueprint, jsonify, request

from backend import database as db
from backend.models import SessionState, iso_week_key
from backend.routes.auth import token_required
from backend.routes.agenda import _upsert_week, _upsert_day


bp = Blueprint("agenda_sessions", __name__)


SESSION_FIELDS = [
    "id", "category", "state", "start_time", "end_time",
    "title", "notes",
    "timer_state", "timer_started_at", "timer_paused_at",
    "timer_paused_duration", "timer_elapsed", "timer_total", "timer_paused",
]


_PATCHABLE_FIELDS = {
    "title", "category", "state", "start_time", "end_time", "notes",
    "timer_state", "timer_started_at", "timer_paused_at",
    "timer_paused_duration", "timer_elapsed", "timer_total", "timer_paused",
}


_COPY_INSERT_SQL = """
INSERT INTO sessions (
    id, day_date, week_id, user_id, category, state,
    start_time, end_time, title, notes,
    timer_state, timer_started_at, timer_paused_at,
    timer_paused_duration, timer_elapsed,
    timer_total, timer_paused, position, updated_at
) VALUES (
    %(id)s, %(day_date)s, %(week_id)s, %(user_id)s, %(category)s,
    'pending', %(start_time)s, %(end_time)s, %(title)s, %(notes)s,
    'stopped', NULL, NULL,
    0, 0, NULL, NULL, %(position)s, NOW()
)
"""


def _next_pos(date_str: str, user_id: int) -> int:
    row = db.query_one(
        "SELECT COALESCE(MAX(position), -1) + 1 AS next_pos "
        "FROM sessions WHERE day_date = %s AND user_id = %s",
        (date_str, user_id),
    )
    return int(row["next_pos"]) if row else 0


# ─── GET /api/agenda/session/<sid> ──────────────────────────────
@bp.get("/agenda/session/<session_id>")
@token_required
def get_session(current_user_id: int, session_id: str):
    row = db.query_one(
        "SELECT * FROM sessions WHERE id = %s AND user_id = %s",
        (session_id, current_user_id),
    )
    if not row:
        return jsonify(error="Session not found"), 404
    return jsonify(row)


# ─── PATCH /api/agenda/session/<sid> ─────────────────────────────
@bp.patch("/agenda/session/<session_id>")
@token_required
def patch_session(current_user_id: int, session_id: str):
    body = request.get_json(silent=True) or {}
    set_parts, params = _build_patch(body, current_user_id, session_id)
    if not set_parts:
        return jsonify(error="No valid fields"), 400

    set_parts.append("updated_at = NOW()")
    result = db.execute(
        f"UPDATE sessions SET {', '.join(set_parts)} "
        "WHERE id = %(session_id)s AND user_id = %(user_id)s",
        params,
    )
    if result == 0:
        return jsonify(error="Session not found"), 404
    return _return_session(session_id, current_user_id)


def _build_patch(body, user_id, session_id):
    set_parts, params = [], {"session_id": session_id, "user_id": user_id}
    for key in body:
        if key in _PATCHABLE_FIELDS:
            set_parts.append(f"{key} = %({key})s")
            params[key] = body[key]
    return set_parts, params


def _return_session(session_id, user_id):
    row = db.query_one(
        "SELECT * FROM sessions WHERE id = %s AND user_id = %s",
        (session_id, user_id),
    )
    return jsonify(row)


# ─── DELETE /api/agenda/session/<sid> ────────────────────────────
@bp.delete("/agenda/session/<session_id>")
@token_required
def delete_session(current_user_id: int, session_id: str):
    row = db.execute_returning(
        "DELETE FROM sessions WHERE id = %s AND user_id = %s RETURNING id",
        (session_id, current_user_id),
    )
    if not row:
        return jsonify(error="Session not found"), 404
    return jsonify(message="deleted")


# ─── POST /api/agenda/session/<sid>/move ─────────────────────────
@bp.post("/agenda/session/<session_id>/move")
@token_required
def move_session(current_user_id: int, session_id: str):
    body = request.get_json(silent=True) or {}
    target_date = body.get("target_date")
    if not target_date:
        return jsonify(error="target_date required"), 400
    try:
        dt = datetime.strptime(target_date, "%Y-%m-%d").date()
    except ValueError:
        return jsonify(error="Invalid date format, use YYYY-MM-DD"), 400

    target_week = iso_week_key(dt)
    _upsert_week(target_week, current_user_id)
    _upsert_day(target_date, target_week, current_user_id)

    existing = db.query_one(
        "SELECT id FROM sessions WHERE id = %s AND user_id = %s",
        (session_id, current_user_id),
    )
    if not existing:
        return jsonify(error="Session not found"), 404

    next_pos = _next_pos(target_date, current_user_id)
    db.execute(
        "UPDATE sessions SET day_date = %s, week_id = %s, "
        "position = %s, updated_at = NOW() WHERE id = %s",
        (target_date, target_week, next_pos, session_id),
    )
    return jsonify(ok=True)


# ─── POST /api/agenda/session/<sid>/copy ─────────────────────────
@bp.post("/agenda/session/<session_id>/copy")
@token_required
def copy_session(current_user_id: int, session_id: str):
    orig = db.query_one(
        "SELECT * FROM sessions WHERE id = %s AND user_id = %s",
        (session_id, current_user_id),
    )
    if not orig:
        return jsonify(error="Session not found"), 404

    new_id = str(uuid.uuid4())
    db.execute(_COPY_INSERT_SQL, {
        "id": new_id,
        "day_date": orig["day_date"],
        "week_id": orig["week_id"],
        "user_id": current_user_id,
        "category": orig["category"],
        "start_time": orig["start_time"],
        "end_time": orig["end_time"],
        "title": orig["title"],
        "notes": orig["notes"],
        "position": _next_pos(orig["day_date"], current_user_id),
    })
    return _return_session(new_id, current_user_id)


# ─── POST /api/agenda/sessions/bulk-move ──────────────────────────
@bp.post("/agenda/sessions/bulk-move")
@token_required
def bulk_move_sessions(current_user_id: int):
    body = request.get_json(silent=True) or {}
    err = _validate_bulk_move(body)
    if err:
        return jsonify(error=err), 400
    ids = body["ids"]
    target_date = body["target_date"]
    target_week = iso_week_key(datetime.strptime(target_date, "%Y-%m-%d").date())
    _upsert_week(target_week, current_user_id)
    _upsert_day(target_date, target_week, current_user_id)

    moved = _do_bulk_move_txn(ids, target_date, target_week, current_user_id)
    return jsonify(ok=True, moved=moved)


def _validate_bulk_move(body):
    ids = body.get("ids") or []
    target_date = body.get("target_date")
    if not isinstance(ids, list) or not ids:
        return "ids required"
    if not target_date:
        return "target_date required"
    try:
        datetime.strptime(target_date, "%Y-%m-%d")
    except ValueError:
        return "Invalid date format, use YYYY-MM-DD"
    return None


def _do_bulk_move_txn(ids, target_date, target_week, user_id):
    conn = db.get_connection()
    moved = 0
    try:
        with conn.cursor() as cur:
            next_pos = _bulk_move_init_pos(cur, target_date, user_id)
            for sid in ids:
                if _bulk_move_one(cur, sid, target_date, target_week, user_id, next_pos):
                    moved += 1
                    next_pos += 1
        conn.commit()
    except Exception:
        conn.rollback()
        return 0
    finally:
        db.put_connection(conn)
    return moved


def _bulk_move_one(cur, sid, target_date, target_week, user_id, next_pos):
    if not _bulk_move_check(cur, sid, target_date, user_id):
        return False
    cur.execute(
        "UPDATE sessions SET day_date = %s, week_id = %s, "
        "position = %s, updated_at = NOW() WHERE id = %s",
        (target_date, target_week, next_pos, sid),
    )
    return True


def _bulk_move_init_pos(cur, target_date, user_id):
    cur.execute(
        "SELECT COALESCE(MAX(position), -1) + 1 AS next_pos "
        "FROM sessions WHERE day_date = %s AND user_id = %s",
        (target_date, user_id),
    )
    row = cur.fetchone()
    # get_connection() sets RealDictCursor, so rows are dicts keyed by the
    # SQL alias — row[0] raises KeyError: 0 and 500s the whole bulk move.
    next_pos = (row or {}).get("next_pos")
    return int(next_pos) if next_pos is not None else 0


def _bulk_move_check(cur, sid, target_date, user_id):
    cur.execute(
        "SELECT id FROM sessions WHERE id = %s AND user_id = %s "
        "AND day_date <> %s",
        (sid, user_id, target_date),
    )
    return cur.fetchone() is not None


# ─── GET /api/agenda/month?date=YYYY-MM-DD ───────────────────────
@bp.get("/agenda/month")
@token_required
def get_month(current_user_id: int):
    date_str = request.args.get("date") or str(date.today())
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return jsonify(error="Invalid date format, use YYYY-MM-DD"), 400

    year, month = dt.year, dt.month
    first_day = date(year, month, 1)
    last_day = (date(year + 1, 1, 1) - timedelta(days=1)) if month == 12 \
        else (date(year, month + 1, 1) - timedelta(days=1))

    rows = db.query(
        """SELECT
              EXTRACT(DAY FROM to_date(day_date, 'YYYY-MM-DD')) AS day_num,
              COUNT(*) AS total,
              COUNT(*) FILTER (WHERE state = 'completed') AS completed
          FROM sessions
          WHERE day_date >= %s AND day_date <= %s AND user_id = %s
          GROUP BY day_date ORDER BY day_date""",
        (first_day.isoformat(), last_day.isoformat(), current_user_id),
    )

    days = {}
    for r in rows:
        days[str(int(r["day_num"]))] = {
            "total": r["total"],
            "completed": r["completed"],
        }
    return jsonify({"year": year, "month": month, "days": days})


# ─── GET /api/agenda/month/<y>/<m>/sessions ──────────────────────
@bp.get("/agenda/month/<int:year>/<int:month>/sessions")
@token_required
def get_month_sessions(current_user_id: int, year: int, month: int):
    first_day = date(year, month, 1)
    last_day = (date(year + 1, 1, 1) - timedelta(days=1)) if month == 12 \
        else (date(year, month + 1, 1) - timedelta(days=1))

    rows = db.query(
        "SELECT * FROM sessions WHERE day_date >= %s AND day_date <= %s "
        "AND user_id = %s ORDER BY day_date, position, start_time",
        (first_day.isoformat(), last_day.isoformat(), current_user_id),
    )

    days = {}
    for r in rows:
        raw = r["day_date"]
        day_str = str(raw) if not hasattr(raw, "isoformat") else raw.isoformat()
        day_num = str(int(day_str.split("-")[2]))
        if day_num not in days:
            days[day_num] = {"date": day_str, "day": int(day_num), "sessions": []}
        s = {k: r[k] for k in SESSION_FIELDS if k in r}
        s["day_date"] = day_str
        s["timer_elapsed"] = s.get("timer_elapsed") or 0
        s["timer_paused_duration"] = s.get("timer_paused_duration") or 0
        days[day_num]["sessions"].append(s)

    return jsonify({"year": year, "month": month, "days": days})


# ─── GET /api/agenda/states ──────────────────────────────────────
@bp.get("/agenda/states")
@token_required
def list_states(current_user_id: int):
    return jsonify([s.value for s in SessionState])