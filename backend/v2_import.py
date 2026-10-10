# backend/v2_import.py
"""Entry point for the v2 → v3 restore (issue #8).

    ZIP ──v2_parser──▶ tables ──v2_import──▶ v3 schema
            │                    │
            └── extract_files ──┘  (filesystem, before the DB rows)

Order matters and is not negotiable:

  1. `extract_files` puts the media in its final folder, so the rows
     created below can point at paths that already exist.
  2. weeks → days → sessions, because a session derives its `week_id`
     from the `days` row of its date.
  3. courses → topics → blocks → questions, because each step needs the
     ids produced by the previous one.

Everything DB-side happens in ONE transaction owned by the caller. The
route commits on success and rolls back on any error, so a failed import
leaves no half-imported coursework behind.
"""

import logging
import zipfile

import v2_domains
from storage_paths import category_dir
from v2_migrate import extract_files, migrate_studies, plan

logger = logging.getLogger(__name__)


def _storage_dirs():
    """Where v3 keeps each kind of imported media.

    Delegates to storage_paths, so a v2 restore lands in exactly the
    folders routes/pdf.py, the TTS route and the infographics route serve
    from. This used to re-state their env-or-fallback resolution by hand --
    the comment even promised "PDF_UPLOAD_FOLDER mirrors routes/pdf.py" --
    and a copy-pasted promise is how the five folders drifted apart in the
    first place.
    """
    return {c: category_dir(c) for c in ("pdfs", "audio", "infographics")}


def migrate_v2(conn, user_id, zf: zipfile.ZipFile, tables: dict) -> dict:
    """Import a parsed v2 backup into v3. Returns a report dict.

    The caller owns the transaction: this function never commits or rolls
    back. `conn` is expected to be a dictcursor connection.
    """
    report = {"counts": {}, "warnings": [], "skipped_tables": {}}

    # 1. Media first: rows below reference these paths.
    extracted = extract_files(zf, _storage_dirs(), report["warnings"])
    report["files"] = {k: len(v) for k, v in extracted.items()}

    # 2. Agenda before coursework: it has no dependencies on the courses.
    counts = report["counts"]
    counts["weeks"] = v2_domains._import_weeks(conn, user_id, tables, report["warnings"])
    counts["days"] = v2_domains._import_days(conn, user_id, tables, report["warnings"])
    counts["sessions"] = v2_domains._import_sessions(conn, user_id, tables)
    counts["custom_categories"] = v2_domains._import_categories(
        conn, user_id, tables)

    # 3. Coursework. Its id maps are needed by the AI history below.
    study_counts, ids = migrate_studies(conn, user_id, tables, extracted)
    counts.update(study_counts)

    # 4. Everything else that hangs off the imported graph.
    counts["habit_columns"] = v2_domains._import_habit_columns(
        conn, user_id, tables)
    entries, notes = v2_domains._import_habit_rows(conn, user_id, tables)
    counts["habit_entries"] = entries
    counts["habit_notes"] = notes
    counts["quick_notes"] = v2_domains._import_quick_notes(
        conn, user_id, tables)
    counts["ai_tasks"] = v2_domains._import_ai_tasks(
        conn, user_id, tables, ids["topics"])
    counts["ai_usage_log"] = v2_domains._import_ai_usage(
        conn, user_id, tables)
    counts["notebooklm_profiles"] = v2_domains._import_notebooklm_profiles(
        conn, user_id, tables)
    counts["user_config"] = v2_domains._import_user_config(
        conn, user_id, tables)
    counts["user_addons_dropped"] = v2_domains._import_addons(
        conn, user_id, tables)

    # 5. v2 tables with no v3 counterpart, for transparency in the UI.
    present = {k: len(v) for k, v in tables.items()
               if k not in ("_meta",) and isinstance(v, list)}
    report["skipped_tables"] = {
        k: v for k, v in present.items()
        if k not in _MIGRATED_TABLES and v
    }
    report["deferred"] = {
        "quiz_errors": counts.get("quiz_results_deferred", 0),
        "quiz_summaries": len(tables.get("quiz_summaries", [])),
    }
    return report


# Tables this importer actually writes. Used to report what was left out.
_MIGRATED_TABLES = {
    "weeks", "days", "sessions", "custom_categories", "courses", "topics",
    "blocks", "questions", "habit_columns", "habit_entries", "habit_notes",
    "quick_notes", "ai_tasks", "ai_usage_log", "notebooklm_owned_profiles",
    "user_config", "user_addons",
}
