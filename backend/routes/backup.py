# backend/routes/backup.py
"""Backup / Restore routes for StudyFlow PWA v3.

  GET  /api/backup/export   → per-user backup.json inside a ZIP download
  POST /api/backup/upload   → multipart ZIP → selection tree (temp ids)
                              + same-title conflict detection
  POST /api/backup/import   → multipart ZIP + selection + conflicts →
                              transactional restore

  POST /api/backup/v2/scan  → multipart v2 backup → counts + warnings,
                              so the UI can preview before committing
  POST /api/backup/v2/import→ multipart v2 backup → full migration

Two backup formats are accepted on the v2 endpoints: the v3 `backup.json`
and the v2 `user_data.sql` COPY dump. `detect_format` tells them apart.

Logic lives in backup_core.py / v2_import.py; this module wires routes.
"""

import json
from datetime import datetime

from flask import Blueprint, jsonify, request, send_file

from database import get_connection, put_connection
from routes.auth import token_required
from backup_dump import dump_user_data
from backup_core import (
    build_selection_tree,
    collect_conflicts,
    import_rest,
    import_studyflow_tree,
    load_zip,
    make_zip,
    peek_format,
)
from v2_import import migrate_v2
from v2_parser import detect_format, parse_copy_sections

bp = Blueprint("backup", __name__)


def _open_v2_backup(file_storage):
    """Parse an uploaded v2 backup ZIP. Returns (zipfile, tables) or (None, None).

    The caller owns the returned ZipFile and must close it. Kept separate so
    the ZIP is opened exactly once per request — it is up to 800 MB.
    """
    import zipfile

    zf = zipfile.ZipFile(file_storage.stream, "r")
    if detect_format(zf) != "v2":
        zf.close()
        return None, None
    try:
        raw = zf.read("user_data.sql").decode("utf-8", "replace")
    except KeyError:
        zf.close()
        return None, None
    return zf, parse_copy_sections(raw)


@bp.get("/backup/export")
@token_required
def backup_export(user_id):
    """Download studyflow-backup-YYYY-MM-DD.zip with this user's data."""
    backup = dump_user_data(user_id)
    zip_buf = make_zip(backup)
    date_str = datetime.utcnow().strftime("%Y-%m-%d")
    return send_file(
        zip_buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"studyflow-backup-{date_str}.zip",
    )


@bp.post("/backup/upload")
@token_required
def backup_upload(user_id):
    """Unzip → validate → return selection tree + conflict list."""
    file_storage = request.files.get("file")
    if not file_storage:
        return jsonify({"error": "file is required"}), 400

    # Issue #8: a v2 backup is not a v3 one. Say so explicitly so the UI can
    # offer the migration, instead of returning an empty selection tree that
    # reads as "this backup has nothing in it".
    fmt = peek_format(file_storage)
    if fmt == "v2":
        return jsonify({
            "ok": False,
            "format": "v2",
            "error": "Este es un backup de StudyFlow v2 y necesita migración",
            "use": "/backup/v2/scan",
        }), 415

    backup = load_zip(file_storage)
    if backup is None:
        return jsonify({"error": "invalid backup zip"}), 400

    tree = build_selection_tree(backup, user_id)
    return jsonify({
        "ok": True,
        "meta": {
            "version": backup.get("version"),
            "created_at": backup.get("created_at"),
            "user_email": backup.get("user_email"),
        },
        "tree": tree,
        "conflicts": collect_conflicts(tree),
    })


@bp.post("/backup/import")
@token_required
def backup_import(user_id):
    """Transactional restore: ZIP + selection + conflicts (multipart)."""
    file_storage = request.files.get("file")
    if not file_storage:
        return jsonify({"error": "file is required"}), 400
    backup = load_zip(file_storage)
    if backup is None:
        return jsonify({"error": "invalid backup zip"}), 400

    try:
        selection = json.loads(request.form.get("selection", "{}") or "{}")
        conflicts = json.loads(request.form.get("conflicts", "{}") or "{}")
    except json.JSONDecodeError:
        return jsonify({"error": "selection and conflicts must be JSON"}), 400

    conn = get_connection()
    try:
        import_studyflow_tree(conn, user_id, backup, selection, conflicts)
        import_rest(conn, user_id, backup, selection.get("rest", []))
        conn.commit()
    except Exception as exc:
        conn.rollback()
        return jsonify({"error": f"import failed: {exc}"}), 500
    finally:
        put_connection(conn)

    return jsonify({"ok": True, "restored": True})


# ─── v2 (PostgreSQL COPY dump) restore ────────────────────────────────
@bp.post("/backup/v2/scan")
@token_required
def backup_v2_scan(user_id):
    """Preview a v2 backup: what it contains, before anything is written."""
    file_storage = request.files.get("file")
    if not file_storage:
        return jsonify({"error": "file is required"}), 400
    zf, tables = _open_v2_backup(file_storage)
    if tables is None:
        return jsonify({"error": "not a v2 backup (no user_data.sql)"}), 400
    try:
        from v2_migrate import plan
        summary = plan(tables)
    finally:
        zf.close()
    return jsonify({"ok": True, "format": "v2", "plan": summary})


@bp.post("/backup/v2/import")
@token_required
def backup_v2_import(user_id):
    """Migrate a v2 backup into v3. One transaction; rolls back on error."""
    file_storage = request.files.get("file")
    if not file_storage:
        return jsonify({"error": "file is required"}), 400
    zf, tables = _open_v2_backup(file_storage)
    if tables is None:
        return jsonify({"error": "not a v2 backup (no user_data.sql)"}), 400

    conn = get_connection()
    try:
        report = migrate_v2(conn, user_id, zf, tables)
        conn.commit()
    except Exception as exc:
        conn.rollback()
        return jsonify({"error": f"import failed: {exc}"}), 500
    finally:
        zf.close()
        put_connection(conn)

    return jsonify({"ok": True, "restored": True, "report": report})