"""
Agenda · week/day endpoints — port from studyflow-hub v2.

Blueprints register at /api prefix (see server.py), so routes are declared
WITHOUT the /api prefix (e.g. `/agenda/week/<week_id>`).

Storage model mirrors v2: weeks + days are thin metadata rows; the
canonical session list lives in `sessions` keyed by (user_id, day_date).
"""
import uuid
from datetime import datetime
from flask import Blueprint, jsonify, request

from backend import database as db
from backend.models import SessionCategory, iso_week_key
from backend.routes.auth import token_required


bp = Blueprint("agenda", __name__)


SESSION_FIELDS = [
    "id", "category", "state", "start_time", "end_time",
    "title", "notes",
    "timer_state", "timer_started_at", "timer_paused_at",
    "timer_paused_duration", "timer_elapsed", "timer_total", "timer_paused",
]


_INSERT_SESSION_SQL = """
INSERT INTO sessions (
    id, day_date, week_id, user_id, category, state,
    start_time, end_time, title, notes,
    timer_state, timer_started_at, timer_paused_at,
    timer_paused_duration, timer_elapsed,
    timer_total, timer_paused, position, updated_at
) VALUES (
    %(id)s, %(day_date)s, %(week_id)s, %(user_id)s,
    %(category)s, %(state)s,
    %(start_time)s, %(end_time)s, %(title)s, %(notes)s,
    %(timer_state)s, %(timer_started_at)s, %(timer_paused_at)s,
    %(timer_paused_duration)s, %(timer_elapsed)s,
    %(timer_total)s, %(timer_paused)s, %(position)s, NOW()
)
ON CONFLICT (id) DO UPDATE SET
    category      = EXCLUDED.category,
    state         = EXCLUDED.state,
    start_time    = EXCLUDED.start_time,
    end_time      = EXCLUDED.end_time,
    title         = EXCLUDED.title,
    notes         = EXCLUDED.notes,
    timer_state   = EXCLUDED.timer_state,
    timer_started_at  = EXCLUDED.timer_started_at,
    timer_paused_at   = EXCLUDED.timer_paused_at,
    timer_paused_duration = EXCLUDED.timer_paused_duration,
    timer_elapsed = EXCLUDED.timer_elapsed,
    timer_total   = EXCLUDED.timer_total,
    timer_paused  = EXCLUDED.timer_paused,
    position      = EXCLUDED.position,
    updated_at    = NOW()
"""


# ─── Helpers ──────────────────────────────────────────────────────

def _builtin_categories() -> dict:
    icons = {
        "formal_study": "🎓", "self_study": "📚", "work": "💼",
        "language": "🌐", "health": "🏋️", "mind": "🧠", "project": "⚡",
    }
    labels = {
        "formal_study": "Formal Study", "self_study": "Self Study", "work": "Work",
        "language": "Language", "health": "Health", "mind": "Mind", "project": "Project",
    }
    return {
        cat.value: {
            "label": labels.get(cat.value, cat.name.replace("_", " ").title()),
            "icon": icons.get(cat.value, "📌"),
            "builtin": True,
        }
        for cat in SessionCategory
    }


def _reconstruct_week(week_id: str, user_id: int) -> dict | None:
    week = db.query_one(
        "SELECT * FROM weeks WHERE week_id = %s AND user_id = %s",
        (week_id, user_id),
    )
    if not week:
        return None

    days_rows = db.query(
        "SELECT date FROM days WHERE week_id = %s AND user_id = %s ORDER BY date",
        (week_id, user_id),
    )
    days = {}
    for d in days_rows:
        sessions = db.query(
            "SELECT * FROM sessions WHERE day_date = %s AND user_id = %s "
            "ORDER BY position, start_time",
            (d["date"], user_id),
        )
        days[d["date"]] = {"date": d["date"], "sessions": sessions}

    return {
        "week_id": week_id,
        "schema_version": week["schema_version"],
        "days": days,
    }


def _upsert_week(week_id: str, user_id: int) -> None:
    db.execute(
        "INSERT INTO weeks (week_id, user_id, schema_version, updated_at) "
        "VALUES (%s, %s, 2, NOW()) "
        "ON CONFLICT (week_id, user_id) DO UPDATE SET updated_at = NOW()",
        (week_id, user_id),
    )


def _upsert_day(date_str: str, week_id: str, user_id: int) -> None:
    db.execute(
        "INSERT INTO days (date, week_id, user_id, updated_at) "
        "VALUES (%s, %s, %s, NOW()) "
        "ON CONFLICT (date, user_id) DO UPDATE SET "
        "  week_id = EXCLUDED.week_id, updated_at = NOW()",
        (date_str, week_id, user_id),
    )


def _replace_day_sessions(date_str: str, week_id: str, user_id: int, sessions: list) -> None:
    """Atomically replace all sessions for a single day (delete + insert)."""
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM sessions WHERE day_date = %s AND user_id = %s",
                (date_str, user_id),
            )
            for idx, s in enumerate(sessions):
                _insert_one_session(cur, date_str, week_id, user_id, idx, s)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        db.put_connection(conn)


def _insert_one_session(cur, date_str: str, week_id: str,
                        user_id: int, idx: int, s: dict) -> None:
    fields = {k: s.get(k) for k in SESSION_FIELDS}
    fields["id"] = fields.get("id") or str(uuid.uuid4())
    fields["day_date"] = date_str
    fields["week_id"] = week_id
    fields["user_id"] = user_id
    fields["position"] = idx
    cur.execute(_INSERT_SESSION_SQL, fields)


# ─── GET /api/agenda/week/<week_id> ──────────────────────────────
@bp.get("/agenda/week/<week_id>")
@token_required
def get_week(current_user_id: int, week_id: str):
    data = _reconstruct_week(week_id, current_user_id)
    if data is None:
        _upsert_week(week_id, current_user_id)
        data = {"week_id": week_id, "schema_version": 2, "days": {}}
    return jsonify(data)


# ─── PUT /api/agenda/week/<week_id> ──────────────────────────────
@bp.put("/agenda/week/<week_id>")
@token_required
def save_week(current_user_id: int, week_id: str):
    body = request.get_json(silent=True) or {}
    _upsert_week(week_id, current_user_id)

    days = body.get("days") or {}
    for date_str, day_data in days.items():
        _upsert_day(date_str, week_id, current_user_id)
        _replace_day_sessions(
            date_str, week_id, current_user_id,
            day_data.get("sessions", []) if isinstance(day_data, dict) else [],
        )

    data = _reconstruct_week(week_id, current_user_id)
    return jsonify(data or {"week_id": week_id, "schema_version": 2, "days": {}})


# ─── PATCH /api/agenda/week/<week_id>/day/<date> ─────────────────
@bp.patch("/agenda/week/<week_id>/day/<date>")
@token_required
def patch_day(current_user_id: int, week_id: str, date: str):
    body = request.get_json(silent=True) or {}
    _upsert_week(week_id, current_user_id)
    _upsert_day(date, week_id, current_user_id)

    if "sessions" in body:
        _replace_day_sessions(date, week_id, current_user_id, body["sessions"])

    data = _reconstruct_week(week_id, current_user_id) or {"days": {}}
    return jsonify(data.get("days", {}).get(date, {"date": date, "sessions": []}))


# ─── GET /api/agenda/current ─────────────────────────────────────
@bp.get("/agenda/current")
@token_required
def get_current_week(current_user_id: int):
    week_id = iso_week_key()
    data = _reconstruct_week(week_id, current_user_id)
    if data is None:
        _upsert_week(week_id, current_user_id)
        data = {"week_id": week_id, "schema_version": 2, "days": {}}
    return jsonify(data)