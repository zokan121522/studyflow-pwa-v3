"""
Agenda · session state / timer updates — port from studyflow-hub v2 routes/sessions.py.

POST /api/agenda/session/state
Body: { session_id, state, timer_state? }

- timer_state omitted → derived from state.
- running: resume from pause keeps elapsed; fresh start resets.
- paused: freeze wall time, set timer_paused_at.
- stopped: accumulate paused duration, store total wall time, clear.
"""
from datetime import datetime
from flask import Blueprint, jsonify, request

from backend import database as db
from backend.routes.auth import token_required


bp = Blueprint("agenda_state", __name__)


VALID_STATES = ("pending", "in_progress", "completed", "cancelled")
VALID_TIMER_STATES = ("running", "paused", "stopped", None)

# Seconds since timer_paused_at (0 if NULL).
# FLOOR: EXTRACT(EPOCH ...) returns fractional seconds (9.354299999999995),
# which reached the UI and printed as "00:02:9.354299999999995". Truncate at
# the source so the stored column stays whole seconds. formatTimer() floors
# too, as a second line of defence.
_PAUSED_DELTA = (
    "COALESCE(FLOOR("
    "EXTRACT(EPOCH FROM CAST(%(now)s AS timestamp) - CAST(timer_paused_at AS timestamp))"
    "), 0)"
)
# Total wall time since timer_started_at (0 if NULL).
_WALL_DELTA = (
    "COALESCE(FLOOR("
    "EXTRACT(EPOCH FROM CAST(%(now)s AS timestamp) - CAST(timer_started_at AS timestamp))"
    "), 0)"
)


# ─── POST /api/agenda/session/state ───────────────────────────────
@bp.post("/agenda/session/state")
@token_required
def set_session_state(current_user_id: int):
    body = request.get_json(silent=True) or {}
    err = _validate_state_body(body)
    if err:
        return jsonify(error=err), 400

    session_id = body["session_id"]
    state = body["state"]
    timer_state = body.get("timer_state") or _default_timer_for(state)
    now_str = datetime.utcnow().isoformat()
    set_parts, params = _build_state_update(
        session_id, current_user_id, state, timer_state, now_str,
    )
    set_parts.append("updated_at = NOW()")

    result = db.execute(
        f"UPDATE sessions SET {', '.join(set_parts)} "
        "WHERE id = %(session_id)s AND user_id = %(user_id)s",
        params,
    )
    if result == 0:
        return jsonify(error="Session not found"), 404

    return jsonify({
        "ok": True,
        "session_id": session_id,
        "state": state,
        "timer_state": timer_state,
    })


def _validate_state_body(body):
    if not body.get("session_id"):
        return "session_id required"
    state = body.get("state", "pending")
    if state not in VALID_STATES:
        return f"Invalid state: {state}"
    timer_state = body.get("timer_state")
    if timer_state is not None and timer_state not in VALID_TIMER_STATES:
        return f"Invalid timer_state: {timer_state}"
    return None


def _default_timer_for(state):
    if state == "in_progress":
        return "running"
    return "stopped"


def _build_state_update(session_id, user_id, state, timer_state, now_str):
    set_parts = ["state = %(state)s", "timer_state = %(timer_state)s"]
    params = {
        "state": state,
        "timer_state": timer_state,
        "session_id": session_id,
        "user_id": user_id,
    }
    if timer_state == "running":
        _apply_running(set_parts, params, session_id, user_id, now_str)
    elif timer_state == "paused":
        _apply_paused(set_parts, params, now_str)
    elif timer_state == "stopped":
        _apply_stopped(set_parts, params, now_str)
    return set_parts, params


def _apply_running(set_parts, params, session_id, user_id, now_str):
    if _was_timer_paused(session_id, user_id):
        set_parts.append(
            "timer_paused_duration = COALESCE(timer_paused_duration, 0) + "
            + _PAUSED_DELTA
        )
        set_parts.append("timer_paused_at = NULL")
    else:
        set_parts.extend([
            "timer_started_at = %(now)s",
            "timer_elapsed = 0",
            "timer_paused_duration = 0",
            "timer_paused_at = NULL",
        ])
    params["now"] = now_str


def _apply_paused(set_parts, params, now_str):
    set_parts.append("timer_paused_at = %(now)s")
    set_parts.append("timer_elapsed = GREATEST(0, " + _WALL_DELTA + ")")
    params["now"] = now_str


def _apply_stopped(set_parts, params, now_str):
    set_parts.append(
        "timer_paused_duration = COALESCE(timer_paused_duration, 0) + "
        + _PAUSED_DELTA
    )
    set_parts.append("timer_elapsed = GREATEST(0, " + _WALL_DELTA + ")")
    set_parts.append("timer_started_at = NULL")
    set_parts.append("timer_paused_at = NULL")
    params["now"] = now_str


def _was_timer_paused(session_id, user_id):
    cur = db.query_one(
        "SELECT timer_state FROM sessions WHERE id = %s AND user_id = %s",
        (session_id, user_id),
    )
    return cur is not None and cur["timer_state"] == "paused"