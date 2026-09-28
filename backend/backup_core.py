# backend/backup_core.py
"""Core dump / parse / import machinery for local backup & restore.

Consumed by routes/backup.py. No Flask route logic lives here.

Import rules (issue #7 spec)
  - Always insert with fresh ids (never reuse SERIAL).
  - Orphan child (block/topic selected without its course) auto-creates
    parents from the backup titles.
  - Same-title conflicts resolved per item: rename (" (N)"), replace
    (UPDATE existing), cancel (skip).
  - Secrets never exported (password hashes, scorm password_enc,
    user_config.api_keys_encrypted, NotebookLM cookies).
"""

import io
import json
import uuid
import zipfile

from database import fetchall, fetchone

BACKUP_VERSION = 1


# ─── serialization helpers ────────────────────────────────────────────
def load_zip(file_storage):
    """Validate an uploaded ZIP; return parsed backup dict (or None)."""
    try:
        buf = io.BytesIO(file_storage.read())
        with zipfile.ZipFile(buf, "r") as zf:
            names = zf.namelist()
            if not names:
                return None
            with zf.open(names[0]) as f:
                payload = json.loads(f.read().decode("utf-8"))
        if not isinstance(payload, dict) or payload.get("version") != BACKUP_VERSION:
            return None
        return payload
    except Exception:
        return None


def make_zip(backup: dict) -> io.BytesIO:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("backup.json", json.dumps(backup, ensure_ascii=False))
    buf.seek(0)
    return buf


# ─── same-title lookups (conflict detection) ──────────────────────────
def existing_course_id(user_id: int, title: str):
    row = fetchone(
        "SELECT id FROM courses WHERE user_id = %s AND LOWER(TRIM(title)) = LOWER(TRIM(%s))",
        (user_id, title),
    )
    return row["id"] if row else None


def existing_topic_id(course_id, title: str):
    row = fetchone(
        "SELECT id FROM topics WHERE course_id = %s AND LOWER(TRIM(title)) = LOWER(TRIM(%s))",
        (course_id, title),
    )
    return row["id"] if row else None


def existing_block_id(topic_id, btype: str, title: str):
    if topic_id is None:
        # Course-level block: must be scoped by course, else titles collide
        # across courses. Callers pass (None, course_id) via the helper below.
        raise ValueError("use existing_course_block_id(course_id, ...) when topic_id is None")
    row = fetchone(
        "SELECT id FROM blocks WHERE topic_id = %s AND type = %s "
        "AND LOWER(TRIM(title)) = LOWER(TRIM(%s))",
        (topic_id, btype, title),
    )
    return row["id"] if row else None


def existing_course_block_id(course_id, btype: str, title: str):
    row = fetchone(
        "SELECT id FROM blocks WHERE course_id = %s AND topic_id IS NULL "
        "AND type = %s AND LOWER(TRIM(title)) = LOWER(TRIM(%s))",
        (course_id, btype, title),
    )
    return row["id"] if row else None


def _title_taken(table: str, col: str, user_id: int, title: str, extra: str = ""):
    row = fetchone(
        f"SELECT 1 FROM {table} WHERE user_id = %s AND LOWER(TRIM({col})) = LOWER(TRIM(%s)) {extra}",
        (user_id, title),
    )
    return row is not None


def find_free_title(table: str, col: str, user_id: int, base: str, extra: str = ""):
    candidate, suffix = base, 2
    while _title_taken(table, col, user_id, candidate, extra):
        candidate = f"{base} ({suffix})"
        suffix += 1
    return candidate


# ─── selection tree (upload) ──────────────────────────────────────────
def build_selection_tree(backup: dict, user_id: int) -> dict:
    """Temp ids + existing_id conflict detection per studyflow item."""
    courses_out = []
    for ci, c in enumerate(backup.get("studyflow", {}).get("courses", [])):
        c_tid = f"c{ci}"
        existing_course = existing_course_id(user_id, c["title"])
        topics_out = []
        for ti, t in enumerate(c.get("topics", [])):
            t_tid = f"{c_tid}t{ti}"
            existing_topic = (
                existing_topic_id(existing_course, t["title"])
                if existing_course is not None else None
            )
            blocks_out = [
                {
                    "temp_id": f"{t_tid}b{bi}",
                    "title": b.get("title", ""),
                    "type": b.get("type", "markdown"),
                    "existing_id": (
                        existing_block_id(existing_topic, b.get("type", "markdown"),
                                          b.get("title", ""))
                        if existing_topic is not None else None
                    ),
                }
                for bi, b in enumerate(t.get("blocks", []))
            ]
            topics_out.append({
                "temp_id": t_tid, "title": t["title"],
                "existing_id": existing_topic, "blocks": blocks_out,
            })
        course_blocks_out = [
            {
                "temp_id": f"{c_tid}cb{bi}",
                "title": cb.get("title", ""),
                "type": cb.get("type", "markdown"),
                "existing_id": (
                    existing_course_block_id(existing_course, cb.get("type", "markdown"),
                                             cb.get("title", ""))
                    if existing_course is not None else None
                ),
            }
            for bi, cb in enumerate(c.get("course_blocks", []))
        ]
        courses_out.append({
            "temp_id": c_tid, "title": c["title"],
            "existing_id": existing_course, "topics": topics_out,
            "course_blocks": course_blocks_out,
        })

    return {
        "courses": courses_out,
        "agenda": {"count": len(backup.get("agenda", {}).get("sessions", []))},
        "habits": {
            "columns": len(backup.get("habits", {}).get("columns", [])),
            "entries": len(backup.get("habits", {}).get("entries", [])),
        },
        "todos": {"count": len(backup.get("todos", []))},
        "quiz": {"questions": len(backup.get("quiz", {}).get("questions", []))},
        "quick_notes": bool(backup.get("quick_notes")),
        "user_settings": backup.get("user_settings") is not None,
    }


def collect_conflicts(tree: dict) -> list:
    """Flatten conflicted items (existing_id set) for the review screen."""
    out = []
    for c in tree["courses"]:
        if c["existing_id"] is not None:
            out.append({"temp_id": c["temp_id"], "title": c["title"],
                        "level": "course", "existing_id": c["existing_id"]})
        for cb in c.get("course_blocks", []):
            if cb["existing_id"] is not None:
                out.append({"temp_id": cb["temp_id"], "title": cb["title"],
                            "level": "block", "existing_id": cb["existing_id"]})
        for t in c["topics"]:
            if t["existing_id"] is not None:
                out.append({"temp_id": t["temp_id"], "title": t["title"],
                            "level": "topic", "existing_id": t["existing_id"]})
            for b in t["blocks"]:
                if b["existing_id"] is not None:
                    out.append({"temp_id": b["temp_id"], "title": b["title"],
                                "level": "block", "existing_id": b["existing_id"]})
    return out


# ─── apply helpers (import) ───────────────────────────────────────────
def _apply_course(conn, user_id, c, existing, action):
    """Insert/rename/replace a course. Returns new or existing id."""
    cur = conn.cursor()
    if action == "replace" and existing is not None:
        # Replace = wipe the existing subtree (FK cascade removes children)
        # and re-insert fresh, keeping the original title.
        cur.execute("DELETE FROM courses WHERE id = %s", (existing,))
        existing = None
    title = c["title"]
    if existing is not None:
        title = find_free_title("courses", "title", user_id, c["title"])
    cur.execute(
        "INSERT INTO courses (user_id, title, description, color, progress) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (user_id, title, c.get("description"), c.get("color"), c.get("progress", 0)),
    )
    new_id = cur.fetchone()['id']
    cur.close()
    return new_id


def _apply_topic(conn, user_id, course_id, t, existing, action):
    """Insert/rename/replace a topic. Returns new or existing id."""
    cur = conn.cursor()
    if action == "replace" and existing is not None:
        cur.execute("DELETE FROM topics WHERE id = %s", (existing,))
        existing = None
    title = t["title"]
    if existing is not None:
        title = find_free_title("topics", "title", user_id, t["title"],
                                extra="AND course_id = %s" % course_id)
    cur.execute(
        "INSERT INTO topics (course_id, user_id, title, description, notes, "
        "order_index, status, estimated_minutes, actual_minutes) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (course_id, user_id, title, t.get("description"), t.get("notes", ""),
         t.get("order_index", 0), t.get("status", "pending"),
         t.get("estimated_minutes"), t.get("actual_minutes", 0)),
    )
    new_id = cur.fetchone()['id']
    cur.close()
    return new_id


def _apply_block(conn, user_id, course_id, topic_id, b, existing, action):
    """Insert/rename/replace a block. Returns new or existing id."""
    cur = conn.cursor()
    if action == "replace" and existing is not None:
        cur.execute("DELETE FROM blocks WHERE id = %s", (existing,))
        existing = None
    title = b.get("title", "") or b.get("content", "")[:40]
    if existing is not None:
        title = find_free_title("blocks", "title", user_id, title,
                                extra="AND topic_id = %s" % topic_id)
    cur.execute(
        "INSERT INTO blocks (user_id, course_id, topic_id, type, title, content, "
        "url, done, order_index, color, collapsed) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (user_id, course_id, topic_id, b.get("type", "markdown"), title,
         b.get("content", ""), b.get("url", ""), bool(b.get("done")),
         b.get("order_index", 0), b.get("color", ""), bool(b.get("collapsed"))),
    )
    new_id = cur.fetchone()['id']
    cur.close()
    return new_id


def _apply_course_block(conn, user_id, course_id, b, existing, action):
    """Insert/rename/replace a course-level block (topic_id = NULL)."""
    cur = conn.cursor()
    if action == "replace" and existing is not None:
        cur.execute("DELETE FROM blocks WHERE id = %s", (existing,))
        existing = None
    title = b.get("title", "") or b.get("content", "")[:40]
    if existing is not None:
        title = find_free_title("blocks", "title", user_id, title,
                                extra="AND course_id = %s AND topic_id IS NULL"
                                      % course_id)
    cur.execute(
        "INSERT INTO blocks (user_id, course_id, topic_id, type, title, content, "
        "url, done, order_index, color, collapsed) "
        "VALUES (%s, %s, NULL, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (user_id, course_id, b.get("type", "markdown"), title,
         b.get("content", ""), b.get("url", ""), bool(b.get("done")),
         b.get("order_index", 0), b.get("color", ""), bool(b.get("collapsed"))),
    )
    new_id = cur.fetchone()['id']
    cur.close()
    return new_id


def import_studyflow_tree(conn, user_id, backup, selection, conflicts):
    """Import courses/topics/blocks with orphan parents + conflict policy."""
    selected = {
        "courses": set(selection.get("courses", []) or []),
        "topics": set(selection.get("topics", []) or []),
        "blocks": set(selection.get("blocks", []) or []),
    }
    for ci, c in enumerate(backup.get("studyflow", {}).get("courses", [])):
        c_tid = f"c{ci}"
        topics_data = c.get("topics", [])
        topic_tids = {f"{c_tid}t{ti}" for ti in range(len(topics_data))}
        block_tids = {
            f"{c_tid}t{ti}b{bi}"
            for ti, t in enumerate(topics_data)
            for bi in range(len(t.get("blocks", [])))
        }
        cb_tids = {f"{c_tid}cb{bi}" for bi in range(len(c.get("course_blocks", [])))}
        child_picked = selected["topics"] & topic_tids or selected["blocks"] & block_tids \
            or selected["blocks"] & cb_tids
        if c_tid not in selected["courses"] and not child_picked:
            continue  # nothing selected under this course

        if conflicts.get(c_tid) == "cancel":
            continue
        existing_c = existing_course_id(user_id, c["title"])
        course_id = _apply_course(conn, user_id, c, existing_c,
                                  conflicts.get(c_tid))

        for ci_, cb in enumerate(c.get("course_blocks", [])):
            cb_tid = f"{c_tid}cb{ci_}"
            if cb_tid not in selected["blocks"] and c_tid not in selected["courses"]:
                continue
            if conflicts.get(cb_tid) == "cancel":
                continue
            existing_cb = existing_course_block_id(course_id, cb.get("type", "markdown"),
                                                   cb.get("title", ""))
            _apply_course_block(conn, user_id, course_id, cb, existing_cb,
                                conflicts.get(cb_tid))

        for ti, t in enumerate(topics_data):
            t_tid = f"{c_tid}t{ti}"
            if t_tid not in selected["topics"] and c_tid not in selected["courses"]:
                continue  # only its children (if any) are picked
            if conflicts.get(t_tid) == "cancel":
                continue
            existing_t = existing_topic_id(course_id, t["title"])
            topic_id = _apply_topic(conn, user_id, course_id, t, existing_t,
                                    conflicts.get(t_tid))

            for bi, b in enumerate(t.get("blocks", [])):
                b_tid = f"{t_tid}b{bi}"
                if b_tid not in selected["blocks"] and t_tid not in selected["topics"] \
                        and c_tid not in selected["courses"]:
                    continue
                if conflicts.get(b_tid) == "cancel":
                    continue
                existing_b = existing_block_id(topic_id, b.get("type", "markdown"),
                                               b.get("title", ""))
                _apply_block(conn, user_id, course_id, topic_id, b, existing_b,
                             conflicts.get(b_tid))
    return True


# ─── rest of domains (agenda/habits/todos/notes) ──────────────────────
def import_rest(conn, user_id, backup, rest: list):
    """Import agenda / habits / todos / quick notes / settings (idempotent)."""
    sel = set(rest or [])
    if "agenda" in sel:
        for w in backup.get("agenda", {}).get("weeks", []):
            conn.cursor().execute(
                "INSERT INTO weeks (week_id, user_id, schema_version) "
                "VALUES (%s, %s, %s) ON CONFLICT (week_id, user_id) DO NOTHING",
                (w["week_id"], user_id, w.get("schema_version", 2)),
            )
        for d in backup.get("agenda", {}).get("days", []):
            conn.cursor().execute(
                "INSERT INTO days (date, week_id, user_id) "
                "VALUES (%s, %s, %s) ON CONFLICT (date, user_id) DO NOTHING",
                (d["date"], d.get("week_id"), user_id),
            )
        for s in backup.get("agenda", {}).get("sessions", []):
            # Idempotent: skip when a same (day,title,start) session exists,
            # otherwise every restore would insert a fresh uuid duplicate.
            dup = fetchone(
                "SELECT 1 FROM sessions WHERE user_id = %s AND day_date = %s "
                "AND COALESCE(title,'') = COALESCE(%s,'') "
                "AND COALESCE(start_time,'') = COALESCE(%s,'') LIMIT 1",
                (user_id, s.get("day_date"), s.get("title", ""), s.get("start_time")),
            )
            if dup:
                continue
            conn.cursor().execute(
                "INSERT INTO sessions (id, day_date, week_id, user_id, category, "
                "state, start_time, end_time, title, notes, timer_state, "
                "timer_started_at, timer_paused_at, timer_paused_duration, "
                "timer_elapsed, timer_total, timer_paused, position) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                "%s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
                (str(uuid.uuid4()), s.get("day_date"), s.get("week_id"), user_id,
                 s.get("category", "formal_study"), s.get("state", "pending"),
                 s.get("start_time"), s.get("end_time"), s.get("title", ""),
                 s.get("notes", ""), s.get("timer_state", "stopped"),
                 s.get("timer_started_at"), s.get("timer_paused_at"),
                 s.get("timer_paused_duration", 0), s.get("timer_elapsed"),
                 s.get("timer_total"), s.get("timer_paused"), s.get("position", 0)),
            )
        for cc in backup.get("agenda", {}).get("custom_categories", []):
            conn.cursor().execute(
                "INSERT INTO custom_categories (key, user_id, label, icon) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (key, user_id) DO NOTHING",
                (cc["key"], user_id, cc["label"], cc.get("icon", "📌")),
            )
    if "habits" in sel:
        for h in backup.get("habits", {}).get("columns", []):
            conn.cursor().execute(
                "INSERT INTO habit_columns (key, user_id, label, type, \"order\", note) "
                "VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (key, user_id) DO NOTHING",
                (h["key"], user_id, h["label"], h.get("type", "checkbox"),
                 h.get("order", 0), h.get("note", "")),
            )
        for e in backup.get("habits", {}).get("entries", []):
            conn.cursor().execute(
                "INSERT INTO habit_entries (date, key, user_id, value) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (date, key, user_id) DO NOTHING",
                (e["date"], e["key"], user_id, e.get("value", "")),
            )
        for n in backup.get("habits", {}).get("notes", []):
            conn.cursor().execute(
                "INSERT INTO habit_notes (date, key, user_id, note) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (date, key, user_id) DO NOTHING",
                (n["date"], n["key"], user_id, n.get("note", "")),
            )
    if "todos" in sel:
        for t in backup.get("todos", []):
            conn.cursor().execute(
                "INSERT INTO todos (user_id, title, description, completed, "
                "priority, due_date) VALUES (%s, %s, %s, %s, %s, %s)",
                (user_id, t.get("title", ""), t.get("description"),
                 bool(t.get("completed")), t.get("priority", "medium"),
                 t.get("due_date") or None),
            )
    if "quick_notes" in sel and backup.get("quick_notes"):
        conn.cursor().execute(
            "INSERT INTO quick_notes (user_id, content, updated_at) "
            "VALUES (%s, %s, NOW()) ON CONFLICT (user_id) DO UPDATE SET "
            "content = EXCLUDED.content, updated_at = NOW()",
            (user_id, backup.get("quick_notes")),
        )
    if "user_settings" in sel and backup.get("user_settings") is not None:
        conn.cursor().execute(
            "INSERT INTO user_settings (user_id, calendars_json, updated_at) "
            "VALUES (%s, %s, NOW()) ON CONFLICT (user_id) DO UPDATE SET "
            "calendars_json = EXCLUDED.calendars_json, updated_at = NOW()",
            (user_id, backup.get("user_settings")),
        )
    return True