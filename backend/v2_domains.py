# backend/v2_domains.py
"""v2 → v3 migration of everything that is not coursework (issue #8).

`v2_migrate` owns the study graph (courses → topics → blocks → quiz).
This module covers the rest of the v2 user data: the agenda (weeks, days,
sessions, custom categories), the habit tracker, quick notes, the AI
history, addon state and user preferences.

Two rules run through all of it:

  1. Secrets never migrate. v2 keeps the API keys blob, the SCORM
     credentials and the encrypted calendars in `user_config`; v3 has no
     column for calendars and the other two must stay as the *current*
     user's, not the backup's. They are dropped on purpose.
  2. Natural primary keys are preserved, not renumbered. `weeks.week_id`,
     `days.date`, `custom_categories.key` and `habit_*` keys are the row
     identity in v3 too, so a v2→v2 round trip lines up and the agenda
     keeps its week/date continuity.
"""

import logging
import uuid

from v2_migrate import _bool, _int, _warn

logger = logging.getLogger(__name__)


def _float(value, default=None):
    """Best-effort float() for the timer columns, which are `real` in v3."""
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ─── agenda ───────────────────────────────────────────────────────────
def _import_weeks(conn, user_id, tables, report):
    """v2 weeks → v3. week_id is the PK on both sides, so it is copied."""
    cur = conn.cursor()
    n = 0
    for w in tables.get("weeks", []):
        cur.execute(
            "INSERT INTO weeks (week_id, user_id, schema_version, created_at, "
            "updated_at) VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (week_id, user_id) DO NOTHING",
            (w.get("week_id"), user_id, _int(w.get("schema_version"), 1),
             w.get("created_at"), w.get("updated_at")),
        )
        n += 1
    cur.close()
    return n


def _import_days(conn, user_id, tables, report):
    """v2 days → v3. date is the PK; week_id must already exist."""
    cur = conn.cursor()
    n = 0
    skipped = 0
    for d in tables.get("days", []):
        cur.execute(
            "INSERT INTO days (date, week_id, user_id, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (date, user_id) DO NOTHING",
            (d.get("date"), d.get("week_id"), user_id,
             d.get("created_at"), d.get("updated_at")),
        )
        n += cur.rowcount or 0
        if not cur.rowcount:
            skipped += 1
    cur.close()
    if skipped:
        _warn(f"days: {skipped} con fecha ya existente")
    return n


def _import_categories(conn, user_id, tables):
    cur = conn.cursor()
    n = 0
    for c in tables.get("custom_categories", []):
        cur.execute(
            "INSERT INTO custom_categories (key, user_id, label, icon, created_at) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (key, user_id) DO NOTHING",
            (c.get("key"), user_id, c.get("label") or "", c.get("icon") or "",
             c.get("created_at")),
        )
        n += 1
    cur.close()
    return n


def _import_sessions(conn, user_id, tables):
    """v2 sessions → v3.

    v3 added `week_id`, derived here from the `days` row of the same date.
    The session keeps its own id (v2 ids are strings, v3 stores text), so
    timers, notes and position stay attached to the same session.

    The four timer fields are `real` seconds in v3 — `timer_paused` is NOT
    a boolean, which is what the v2 column name suggests.
    """
    cur = conn.cursor()
    n = 0
    for s in tables.get("sessions", []):
        cur.execute("SELECT week_id FROM days WHERE date = %s", (s.get("day_date"),))
        row = cur.fetchone()
        week_id = row["week_id"] if row else None
        cur.execute(
            "INSERT INTO sessions (id, day_date, user_id, week_id, category, "
            "state, start_time, end_time, title, notes, timer_state, "
            "timer_started_at, timer_paused_at, timer_paused_duration, "
            "timer_elapsed, timer_total, timer_paused, position, "
            "created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
            "%s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
            (s.get("id"), s.get("day_date"), user_id, week_id,
             s.get("category") or "study", s.get("state") or "planned",
             s.get("start_time"), s.get("end_time"), s.get("title") or "",
             s.get("notes") or "", s.get("timer_state") or "idle",
             s.get("timer_started_at"), s.get("timer_paused_at"),
             _float(s.get("timer_paused_duration")), _float(s.get("timer_elapsed")),
             _float(s.get("timer_total")), _float(s.get("timer_paused")),
             _int(s.get("position"), 0) or 0,
             s.get("created_at"), s.get("updated_at")),
        )
        n += cur.rowcount or 0
    cur.close()
    return n


# ─── habits ───────────────────────────────────────────────────────────
def _import_habit_columns(conn, user_id, tables):
    cur = conn.cursor()
    n = 0
    for c in tables.get("habit_columns", []):
        cur.execute(
            "INSERT INTO habit_columns (key, user_id, label, type, \"order\", "
            "note, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (key, user_id) DO NOTHING",
            (c.get("key"), user_id, c.get("label") or "", c.get("type") or "bool",
             _int(c.get("order"), 0) or 0, c.get("note") or "",
             c.get("created_at"), c.get("updated_at")),
        )
        n += 1
    cur.close()
    return n


def _import_habit_rows(conn, user_id, tables):
    """habit_entries + habit_notes. Composite PK (date, key)."""
    cur = conn.cursor()
    entries = 0
    for e in tables.get("habit_entries", []):
        cur.execute(
            "INSERT INTO habit_entries (date, key, user_id, value) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (date, key, user_id) DO NOTHING",
            (e.get("date"), e.get("key"), user_id, e.get("value") or ""),
        )
        entries += cur.rowcount or 0
    notes = 0
    for e in tables.get("habit_notes", []):
        cur.execute(
            "INSERT INTO habit_notes (date, key, user_id, note) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (date, key, user_id) DO NOTHING",
            (e.get("date"), e.get("key"), user_id, e.get("note") or ""),
        )
        notes += cur.rowcount or 0
    cur.close()
    return entries, notes


# ─── notes ────────────────────────────────────────────────────────────
def _import_quick_notes(conn, user_id, tables):
    cur = conn.cursor()
    n = 0
    for q in tables.get("quick_notes", []):
        cur.execute(
            "INSERT INTO quick_notes (user_id, content, updated_at) "
            "VALUES (%s, %s, %s)",
            (user_id, q.get("content") or "", q.get("updated_at")),
        )
        n += 1
    cur.close()
    return n


# ─── AI history ───────────────────────────────────────────────────────
def _import_ai_tasks(conn, user_id, tables, topic_map):
    """v2 ai_tasks → v3.

    `topic_id` is remapped because v2 used its own id space. `source_id`
    is left verbatim: it points at a mix of blocks, course slugs and
    free text, and rewriting it wrongly would corrupt the history. Blocks
    are matched through `block_map` when the value is a real block id.
    """
    cur = conn.cursor()
    n = 0
    for t in tables.get("ai_tasks", []):
        cur.execute(
            "INSERT INTO ai_tasks (id, user_id, topic_id, task_type, format, "
            "source_type, source_id, model_used, status, result_content, "
            "error_message, created_at, completed_at, updated_at, "
            "coverage_data, template_id, language, length) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
            "%s, %s, %s, %s)",
            (str(uuid.uuid4()), user_id,
             topic_map.get(t.get("topic_id")) if t.get("topic_id")
             else None,
             t.get("task_type") or "", t.get("format") or "",
             t.get("source_type") or "", t.get("source_id") or "",
             t.get("model_used") or "", t.get("status") or "pending",
             t.get("result_content") or "", t.get("error_message") or "",
             t.get("created_at"), t.get("completed_at"), t.get("updated_at"),
             t.get("coverage_data") or "", t.get("template_id") or "",
             t.get("language") or "", _int(t.get("length"), 0) or 0),
        )
        n += 1
    cur.close()
    return n


def _import_ai_usage(conn, user_id, tables):
    """v2 ai_usage_log → v3. v3 collapsed the six token columns into `tokens`.

    The v2 breakdown is summed into that single field; the split is not
    recoverable in v3, and keeping the total is the useful part.
    """
    cur = conn.cursor()
    n = 0
    for u in tables.get("ai_usage_log", []):
        total = _int(u.get("total_tokens"), 0) or 0
        if not total:
            total = sum(_int(u.get(c), 0) or 0 for c in (
                "input_tokens", "output_tokens", "reasoning_tokens"))
        cur.execute(
            "INSERT INTO ai_usage_log (user_id, task_type, source, model_id, "
            "provider_id, tokens, cost, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (user_id, u.get("task_type") or "", u.get("source") or "",
             u.get("model_id") or "", u.get("provider_id") or "",
             total, _int(u.get("cost"), 0) or 0, u.get("created_at")),
        )
        n += 1
    cur.close()
    return n


# ─── config / addons ──────────────────────────────────────────────────
def _import_user_config(conn, user_id, tables):
    """v2 user_config → v3, minus every secret.

    Dropped on purpose: api_keys_encrypted, key_index, calendars_enc
    (v3 has no column) and scorm_creds_enc. The v3 user keeps their own
    keys — the backup's ciphertext is not portable and must not be
    restored over a working configuration.
    """
    cur = conn.cursor()
    rows = tables.get("user_config", [])
    if not rows:
        return 0
    cfg = rows[0]
    # Only fill what v3 allows; NULL means "leave the current value".
    cur.execute(
        "INSERT INTO user_config (user_id, provider, personality, voice_mode, "
        "vault_path, priority_context, notebooklm_profile, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (user_id) DO UPDATE SET "
        "provider = COALESCE(EXCLUDED.provider, user_config.provider), "
        "personality = COALESCE(EXCLUDED.personality, user_config.personality), "
        "voice_mode = COALESCE(EXCLUDED.voice_mode, user_config.voice_mode), "
        "priority_context = COALESCE(EXCLUDED.priority_context, "
        "                          user_config.priority_context), "
        "updated_at = EXCLUDED.updated_at",
        (user_id, cfg.get("provider") or None, cfg.get("personality") or None,
         _bool(cfg.get("voice_mode")) or None, None,  # vault stays local
         cfg.get("priority_context") or None,
         cfg.get("notebooklm_profile") or None, cfg.get("updated_at")),
    )
    cur.close()
    _warn("user_config: api_keys, calendars y scorm NO migrados (decisión)")
    return 1


def _import_addons(conn, user_id, tables):
    """v2 user_addons has no v3 counterpart (v3 has no such table)."""
    rows = tables.get("user_addons", [])
    if rows:
        _warn(f"user_addons: {len(rows)} filas sin destino en v3 (descartadas)")
    return 0


def _import_notebooklm_profiles(conn, user_id, tables):
    """Owned NotebookLM profiles: an email, not a secret. Safe to carry over."""
    cur = conn.cursor()
    n = 0
    for p in tables.get("notebooklm_owned_profiles", []):
        email = (p.get("email") or "").strip()
        if not email:
            continue
        cur.execute(
            "INSERT INTO notebooklm_owned_profiles (user_id, email, created_at) "
            "VALUES (%s, %s, %s) ON CONFLICT (user_id, email) DO NOTHING",
            (user_id, email, p.get("created_at")),
        )
        n += cur.rowcount or 0
    cur.close()
    return n
