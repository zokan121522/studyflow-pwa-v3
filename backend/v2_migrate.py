# backend/v2_migrate.py
"""v2 → v3 data migration (issue #8).

Takes the parsed COPY-TSV tables (v2_parser) and writes them into the v3
schema, remapping every uuid/slug key to a fresh SERIAL id.

Policy (documented on issue #8):
  - Data lands on the *authenticated* v3 user; the v2 user row is not
    recreated (the backup belongs to the same person) and the v2 password
    hash is never touched.
  - Secrets never migrate: user_config.api_keys_encrypted,
    scorm_creds_enc and the encrypted calendars blob are dropped.
  - Orphans (topics/blocks whose course no longer exists in v2) are
    skipped and counted — they are leftovers of deleted v2 courses.
  - Every insert uses the caller's transaction; the route commits/rolls back.
"""

import json
import logging
import os
import shutil
import uuid
import zipfile

logger = logging.getLogger(__name__)

# v2 block type → v3 block type. v3 calls it "pdf-ref" and stores the
# document in the pdfs table, addressed by /api/pdf/<id>.
BLOCK_TYPE_MAP = {
    "markdown": "markdown",
    "content": "content",
    "exercise": "exercise",
    "interactive": "interactive",
    "separator": "separator",
    "pdf": "pdf-ref",
}

# Which v2 tables have a home in v3, and how (see summarize()).
V2_TABLES = [
    "users", "courses", "topics", "blocks", "questions", "quiz_errors",
    "sessions", "weeks", "days", "custom_categories", "habit_columns",
    "habit_entries", "habit_notes", "quick_notes", "ai_tasks",
    "ai_usage_log", "user_addons", "user_config", "notebooklm_owned_profiles",
]

# v2-only tables: kept in the ZIP but with no v3 counterpart.
V2_ONLY_TABLES = [
    "agent_scripts", "agents", "agent_tools", "cards", "chat_messages",
    "chat_sessions", "column_visibility", "flashcards_cards",
    "flashcards_decks", "jsp_cells", "jsp_notebooks", "match_failed_cards",
    "match_scores", "quiz_summaries", "notices", "personality_overrides",
    "planteamientos", "custom_tools", "agent_skills",
]


# ─── planning / summary (no DB writes) ────────────────────────────────
def _int(value, default=None):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _bool(value) -> bool:
    return str(value).lower() in ("t", "true", "1", "yes")


def _warn(message: str) -> None:
    """Log a recoverable problem. The route surfaces these to the user."""
    logger.warning("v2_migrate: %s", message)


def plan(tables: dict) -> dict:
    """Compute what the migration will do, without touching the DB."""
    courses = tables.get("courses", [])
    topics = tables.get("topics", [])
    blocks = tables.get("blocks", [])
    questions = tables.get("questions", [])

    course_ids = {c.get("id") for c in courses}
    topic_ids = {tp.get("id") for tp in topics}
    block_ids = {b.get("id") for b in blocks}

    orphan_topics = [tp for tp in topics if tp.get("course_id") not in course_ids]
    live_topic_ids = topic_ids - {tp.get("id") for tp in orphan_topics}
    orphan_blocks = [
        b for b in blocks
        if (b.get("course_id") not in course_ids)
        or (b.get("topic_id") and b.get("topic_id") not in topic_ids)
    ]
    live_block_ids = block_ids - {b.get("id") for b in orphan_blocks}
    orphan_questions = [q for q in questions if q.get("block_id") not in live_block_ids]

    by_type = {}
    for b in blocks:
        by_type[b.get("type", "markdown")] = by_type.get(b.get("type", "markdown"), 0) + 1

    return {
        "counts": {
            "courses": len(courses),
            "topics": len(topics) - len(orphan_topics),
            "blocks": len(blocks) - len(orphan_blocks),
            "questions": len(questions) - len(orphan_questions),
            "sessions": len(tables.get("sessions", [])),
            "weeks": len(tables.get("weeks", [])),
            "days": len(tables.get("days", [])),
            "custom_categories": len(tables.get("custom_categories", [])),
            "habit_columns": len(tables.get("habit_columns", [])),
            "habit_entries": len(tables.get("habit_entries", [])),
            "habit_notes": len(tables.get("habit_notes", [])),
            "quick_notes": len(tables.get("quick_notes", [])),
            "ai_tasks": len(tables.get("ai_tasks", [])),
            "ai_usage_log": len(tables.get("ai_usage_log", [])),
        },
        "block_types": by_type,
        "skipped": {
            "orphan_topics": len(orphan_topics),
            "orphan_blocks": len(orphan_blocks),
            "orphan_questions": len(orphan_questions),
            "v2_only_tables": sorted(
                t for t in V2_ONLY_TABLES if tables.get(t)
            ),
            "malformed_lines": tables.get("_skipped", 0),
        },
        "secrets_dropped": [
            "user_config.api_keys_encrypted",
            "user_config.scorm_creds_enc",
            "user_config.calendars_enc (encrypted blob, no v3 counterpart)",
        ],
        # Read but not imported, on purpose. Listed separately so the modal
        # can say so instead of implying these are being migrated.
        "deferred": {
            "quiz_errors": len(tables.get("quiz_errors", [])),
            "user_addons": len(tables.get("user_addons", [])),
        },
        "live_topic_ids": len(live_topic_ids),
    }


# ─── files extraction ─────────────────────────────────────────────────
def extract_files(zf: zipfile.ZipFile, destinations: dict, report: list) -> dict:
    """Extract files/{pdfs,audio,infographics} into their v3 storage dirs.

    Files land in their final location *before* the DB rows are created so
    pdf rows can point at the real path. Copies are idempotent (UUID names)
    and filesystem problems are collected in `report` instead of aborting the
    DB import — a missing image should not cost you the whole restore.

    Returns {folder: {basename: final_path}} for the folders that succeeded.
    """
    extracted = {}
    for folder, dest_dir in destinations.items():
        prefix = f"files/{folder}/"
        names = [n for n in zf.namelist()
                 if n.startswith(prefix) and not n.endswith("/")]
        if not names:
            continue
        try:
            os.makedirs(dest_dir, exist_ok=True)
        except OSError as exc:
            report.append({"warning": f"{folder}: cannot create {dest_dir}: {exc}"})
            continue
        written = {}
        for name in names:
            base = os.path.basename(name)
            if not base or base.startswith("."):
                continue
            target = os.path.join(dest_dir, base)
            if os.path.exists(target):
                written[base] = target  # already there: reuse it
                continue
            try:
                with zf.open(name) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
                written[base] = target
            except Exception as exc:
                report.append({"warning": f"{folder}/{base}: {exc}"})
        extracted[folder] = written
    return extracted


# ─── course / topic / block migration ────────────────────────────────
def _free_title(cur, table, col, user_id, base):
    """A non-colliding title, resolved on the caller's own connection.

    NOT backup_core.find_free_title: that one goes through database.fetchone,
    which borrows a connection from the global pool. The import holds a
    transaction open, so a second connection on the same tables can stall
    or deadlock. Same rule as issue #7 (rename with " (N)"), same cursor.
    """
    candidate, suffix = base, 2
    while True:
        cur.execute(
            f"SELECT 1 FROM {table} WHERE {col} = %s AND user_id = %s LIMIT 1",
            (candidate, user_id),
        )
        if cur.fetchone() is None:
            return candidate
        candidate = f"{base} ({suffix})"
        suffix += 1


def _import_courses(conn, user_id, tables):
    """Insert v2 courses. Returns {v2_id: v3_id}.

    Issue #12: carries is_favorite, icon and notes across. v2 marked
    favourites in the sidebar and on the dashboard (fase 72 F1/F2), and
    the real backup has six of them flagged — dropping the column here is
    what made the migration silently lose them.
    """
    mapping = {}
    cur = conn.cursor()
    for order, c in enumerate(tables.get("courses", [])):
        title = _free_title(cur, "courses", "title", user_id,
                            c.get("title") or "Sin título")
        # COPY exports booleans as t/f; be liberal in what you accept.
        fav = c.get("is_favorite")
        is_favorite = fav in (True, "t", "true", "T", "1", 1)
        cur.execute(
            "INSERT INTO courses (user_id, title, description, color, "
            "progress, is_favorite, icon, order_index, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, "
            "COALESCE(%s::timestamptz, NOW())) RETURNING id",
            (user_id, title, c.get("description"), c.get("color"), 0,
             is_favorite, c.get("icon") or None, order,
             c.get("created_at")),
        )
        mapping[c["id"]] = cur.fetchone()["id"]
    cur.close()
    return mapping


def _import_topics(conn, user_id, tables, course_map):
    """Insert v2 topics whose course survived. Returns {v2_id: v3_id}."""
    mapping = {}
    cur = conn.cursor()
    for tp in tables.get("topics", []):
        course_id = course_map.get(tp.get("course_id"))
        if course_id is None:
            continue  # orphan: course was deleted in v2
        cur.execute(
            "INSERT INTO topics (course_id, user_id, title, order_index, status, "
            "notes, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, 'pending', %s, %s, %s) RETURNING id",
            (course_id, user_id, tp.get("title") or "Sin título",
             _int(tp.get("order"), 0) or 0, tp.get("notes") or "",
             tp.get("created_at"), tp.get("updated_at")),
        )
        mapping[tp["id"]] = cur.fetchone()["id"]
    cur.close()
    return mapping


def _pdf_basename(pdf_path: str):
    """v2 pdf_path variants → bare filename we can look up in files/.

    Accepts "uuid.pdf", "/api/pdf/serve/uuid.pdf" and plain paths.
    Returns "" when the value cannot be a real file reference.
    """
    if not pdf_path:
        return ""
    base = os.path.basename(pdf_path.strip())
    return base if base.lower().endswith(".pdf") else ""


def _import_blocks(conn, user_id, tables, course_map, topic_map, extracted):
    """Insert v2 blocks. Returns {v2_id: v3_id} for quiz linkage."""
    mapping = {}
    pdf_files = extracted.get("pdfs") or {}
    cur = conn.cursor()
    for b in tables.get("blocks", []):
        course_id = course_map.get(b.get("course_id"))
        if course_id is None:
            continue  # orphan block (deleted course)
        topic_id = topic_map.get(b.get("topic_id")) if b.get("topic_id") else None
        btype = BLOCK_TYPE_MAP.get(b.get("type") or "markdown")
        if btype is None:
            continue  # unknown v2 type → skip rather than guess
        title = b.get("title") or (b.get("content") or "")[:40]
        content = b.get("content") or ""
        url = ""
        done = _bool(b.get("done"))

        if btype == "pdf-ref":
            # v3 stores the document in `pdfs` and links /api/pdf/<id>.
            base = _pdf_basename(b.get("pdf_path") or "")
            storage = pdf_files.get(base)
            if storage and os.path.exists(storage):
                cur.execute(
                    "INSERT INTO pdfs (user_id, course_id, topic_id, filename, "
                    "original_name, file_size, storage_path) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                    (user_id, course_id, topic_id, base,
                     os.path.splitext(base)[0][:255],
                     os.path.getsize(storage), storage),
                )
                pdf_id = cur.fetchone()["id"]
                url = f"/api/pdf/{pdf_id}"
                content = ""
            else:
                # No file in the ZIP: keep the block so the title and its
                # place in the topic survive, and flag the missing document.
                content = (f"[PDF no disponible en el backup v2: "
                           f"{b.get('pdf_path') or 'sin ruta'}]")

        cur.execute(
            "INSERT INTO blocks (user_id, course_id, topic_id, type, title, "
            "content, url, done, order_index, color, collapsed, "
            "created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "RETURNING id",
            (user_id, course_id, topic_id, btype, title, content, url, done,
             _int(b.get("order"), 0) or 0, "", False,
             b.get("created_at"), b.get("updated_at")),
        )
        mapping[b["id"]] = cur.fetchone()["id"]
    cur.close()
    return mapping


def _import_questions(conn, user_id, tables, block_map, course_map, topic_map):
    """v2 questions → v3 quiz_questions (issue #10 made block_id native).

    Returns (imported_count, {question_text: v3_question_id}) so the quiz
    error log can be re-linked to the questions it answered.
    """
    cur = conn.cursor()
    by_text = {}
    imported = 0
    for q in tables.get("questions", []):
        block_id = block_map.get(q.get("block_id"))
        if block_id is None:
            continue  # orphan question
        options = q.get("options")
        if isinstance(options, str):
            try:
                options = json.loads(options)
            except (TypeError, ValueError):
                continue
        if not isinstance(options, list) or not options:
            continue
        correct = _int(q.get("correct"))
        if correct is None or not (0 <= correct < len(options)):
            correct = 0
        # The question inherits course/topic from its parent block, so the
        # quiz screen can filter by them the way the block list does.
        course_id = course_map.get(q.get("course_id"))
        topic_id = topic_map.get(q.get("topic_id")) if q.get("topic_id") else None
        cur.execute(
            "INSERT INTO quiz_questions (user_id, course_id, topic_id, block_id, "
            "question, options, correct_answer, explanation, difficulty) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (user_id, course_id, topic_id, block_id, q.get("question") or "",
             json.dumps(options, ensure_ascii=False), correct,
             q.get("explanation") or "", "medium"),
        )
        imported += 1
        stem = (q.get("question") or "").strip()
        if stem and stem not in by_text:
            by_text[stem] = cur.fetchone()["id"]
        else:
            cur.fetchone()
    cur.close()
    return imported, by_text


def _import_quiz_errors(conn, user_id, tables, block_map, question_ids,
                        course_map, topic_map):
    """v2 quiz_errors → v3 quiz_results. DEFERRED, on purpose.

    Nothing is imported yet, and that is a decision, not an oversight
    (agreed 2026-09-29; see Engram "v2→v3: aplazar migración de
    quiz_errors y quiz_summaries"). Fix it once the migration has a test
    suite to catch the regressions.

    Why it is not a one-liner: v2 `quiz_errors` carries
        (id, topic, tpl, question, user_answer, correct_answer,
         explanation, options, created_at, user_id)
    — there is NO block_id, course_id or topic_id column. The only link
    back to a question is the stem text, scoped by `tpl`/`topic` slags.
    v2 `questions` has block_id but no course_id/topic_id either, so
    course/topic must be INHERITED from the parent block.

    Matching on the bare stem is not safe: the same question text can
    appear in several templates. The fix should key on (tpl, question),
    resolve the block from the question, and inherit course/topic from it.
    """
    rows = len(tables.get("quiz_errors", []))
    if rows:
        _warn(f"quiz_errors: {rows} filas NO migradas (aplazado por decisión)")
    return 0


# ─── orchestrator ─────────────────────────────────────────────────────
def migrate_studies(conn, user_id, tables, extracted):
    """Courses → topics → blocks → questions → quiz errors.

    All five run in the caller's transaction, in that order: the question
    importer needs block ids, and the error log needs question ids.

    Returns (counts, ids) where ids carries the v2→v3 maps — the AI
    history needs the topic map to remap `ai_tasks.topic_id`.
    """
    counts = {}
    course_map = _import_courses(conn, user_id, tables)
    counts["courses"] = len(course_map)
    topic_map = _import_topics(conn, user_id, tables, course_map)
    counts["topics"] = len(topic_map)
    block_map = _import_blocks(conn, user_id, tables, course_map, topic_map,
                               extracted)
    counts["blocks"] = len(block_map)
    n_questions, question_ids = _import_questions(
        conn, user_id, tables, block_map, course_map, topic_map)
    counts["questions"] = n_questions
    counts["quiz_results"] = _import_quiz_errors(
        conn, user_id, tables, block_map, question_ids, course_map, topic_map)
    counts["quiz_results_deferred"] = len(tables.get("quiz_errors", []))
    return counts, {"courses": course_map, "topics": topic_map,
                    "blocks": block_map, "questions": question_ids}
