# backend/routes/backup.py
"""Backup / Restore routes for StudyFlow PWA v3.

  GET  /api/backup/export   → per-user backup.json inside a ZIP download
  POST /api/backup/upload   → multipart ZIP → selection tree (temp ids)
                              + same-title conflict detection
  POST /api/backup/import   → multipart ZIP + selection + conflicts →
                              transactional restore

Logic lives in backup_core.py; this module only wires routes.
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
)

bp = Blueprint("backup", __name__)


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