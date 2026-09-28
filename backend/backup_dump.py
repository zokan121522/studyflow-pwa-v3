# backend/backup_dump.py
"""User data dump for local backup export (data only, no secrets).

Split out of backup_core.py to keep every module under 500 lines.
"""

from datetime import datetime

from database import fetchall, fetchone
from backup_core import BACKUP_VERSION


# ─── dump (export) ────────────────────────────────────────────────────
def _dump_course_tree(user_id: int):
    """courses → topics → blocks as nested dicts (serial ids dropped)."""
    courses = fetchall(
        "SELECT id, title, description, color, progress FROM courses "
        "WHERE user_id = %s ORDER BY created_at ASC",
        (user_id,),
    )
    topics = fetchall(
        "SELECT id, course_id, title, description, notes, order_index, status, "
        "estimated_minutes, actual_minutes FROM topics "
        "WHERE user_id = %s ORDER BY course_id, order_index ASC",
        (user_id,),
    )
    blocks = fetchall(
        "SELECT id, course_id, topic_id, type, title, content, url, done, "
        "order_index, color, collapsed FROM blocks "
        "WHERE user_id = %s ORDER BY topic_id, order_index ASC",
        (user_id,),
    )
    by_topic, by_course = {}, {}
    for b in blocks:
        by_topic.setdefault(b["topic_id"], []).append(
            {k: b[k] for k in ("type", "title", "content", "url", "done",
                               "order_index", "color", "collapsed")}
        )
    course_blocks = {}
    for b in blocks:
        if b["topic_id"] is None:
            course_blocks.setdefault(b["course_id"], []).append(
                {k: b[k] for k in ("type", "title", "content", "url", "done",
                                   "order_index", "color", "collapsed")}
            )
    for t in topics:
        by_course.setdefault(t["course_id"], []).append(
            {
                "title": t["title"], "description": t["description"],
                "notes": t["notes"], "order_index": t["order_index"],
                "status": t["status"], "estimated_minutes": t["estimated_minutes"],
                "actual_minutes": t["actual_minutes"],
                "blocks": by_topic.get(t["id"], []),
            }
        )
    return [
        {
            "title": c["title"], "description": c["description"],
            "color": c["color"], "progress": c["progress"],
            "topics": by_course.get(c["id"], []),
            "course_blocks": course_blocks.get(c["id"], []),
        }
        for c in courses
    ]


def dump_user_data(user_id: int) -> dict:
    """Build the full backup dict for one user (data only, no secrets)."""
    user = fetchone("SELECT email FROM users WHERE id = %s", (user_id,))
    return {
        "version": BACKUP_VERSION,
        "created_at": datetime.utcnow().isoformat() + "Z",
        "user_email": user["email"] if user else "",
        "studyflow": {"courses": _dump_course_tree(user_id)},
        "agenda": {
            "sessions": [
                {k: s[k] for k in (
                    "id", "day_date", "week_id", "category", "state",
                    "start_time", "end_time", "title", "notes", "timer_state",
                    "timer_started_at", "timer_paused_at", "timer_paused_duration",
                    "timer_elapsed", "timer_total", "timer_paused", "position",
                )}
                for s in fetchall(
                    "SELECT * FROM sessions WHERE user_id = %s "
                    "ORDER BY day_date, position ASC",
                    (user_id,),
                )
            ],
            "weeks": [
                {k: w[k] for k in ("week_id", "schema_version")}
                for w in fetchall(
                    "SELECT * FROM weeks WHERE user_id = %s ORDER BY week_id ASC",
                    (user_id,),
                )
            ],
            "days": [
                {k: d[k] for k in ("date", "week_id")}
                for d in fetchall(
                    "SELECT * FROM days WHERE user_id = %s ORDER BY date ASC",
                    (user_id,),
                )
            ],
            "custom_categories": [
                {k: c[k] for k in ("key", "label", "icon")}
                for c in fetchall(
                    "SELECT * FROM custom_categories WHERE user_id = %s "
                    "ORDER BY created_at ASC",
                    (user_id,),
                )
            ],
        },
        "habits": {
            "columns": [
                {k: h[k] for k in ("key", "label", "type", "order", "note")}
                for h in fetchall(
                    "SELECT * FROM habit_columns WHERE user_id = %s ORDER BY \"order\" ASC",
                    (user_id,),
                )
            ],
            "entries": [
                {k: e[k] for k in ("date", "key", "value")}
                for e in fetchall(
                    "SELECT * FROM habit_entries WHERE user_id = %s ORDER BY date ASC",
                    (user_id,),
                )
            ],
            "notes": [
                {k: n[k] for k in ("date", "key", "note")}
                for n in fetchall(
                    "SELECT * FROM habit_notes WHERE user_id = %s ORDER BY date ASC",
                    (user_id,),
                )
            ],
        },
        "todos": [
            {k: _iso(t[k]) for k in ("title", "description", "completed",
                                     "priority", "due_date")}
            for t in fetchall(
                "SELECT * FROM todos WHERE user_id = %s ORDER BY created_at ASC",
                (user_id,),
            )
        ],
        "quiz": {
            "questions": [
                {k: q[k] for k in (
                    "question", "options", "correct_answer", "explanation",
                    "difficulty", "course_title", "topic_title",
                )}
                for q in fetchall(
                    "SELECT q.question, q.options, q.correct_answer, q.explanation, "
                    "q.difficulty, c.title AS course_title, t.title AS topic_title "
                    "FROM quiz_questions q "
                    "LEFT JOIN courses c ON c.id = q.course_id "
                    "LEFT JOIN topics t ON t.id = q.topic_id "
                    "WHERE q.user_id = %s ORDER BY q.created_at ASC",
                    (user_id,),
                )
            ],
            "results": [
                {k: r[k] for k in (
                    "course_title", "topic_title", "selected_answer",
                    "is_correct", "time_taken_ms",
                )}
                for r in fetchall(
                    "SELECT r.selected_answer, r.is_correct, r.time_taken_ms, "
                    "c.title AS course_title, t.title AS topic_title "
                    "FROM quiz_results r "
                    "LEFT JOIN courses c ON c.id = r.course_id "
                    "LEFT JOIN topics t ON t.id = r.topic_id "
                    "WHERE r.user_id = %s ORDER BY r.created_at DESC",
                    (user_id,),
                )
            ],
        },
        "quick_notes": (
            fetchone("SELECT content FROM quick_notes WHERE user_id = %s", (user_id,))
            or {}
        ).get("content", ""),
        "user_settings": (
            fetchone(
                "SELECT calendars_json FROM user_settings WHERE user_id = %s",
                (user_id,),
            )
            or {}
        ).get("calendars_json", None),
    }


