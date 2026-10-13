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

  POST /api/backup/reset    → destructive: snapshot the SQLite file, wipe
                              every content table (users/addons preserved)
                              and uploaded media, report what was removed

Each format has its own endpoint; they are not interchangeable. `/backup/import`
  opens `backup.json` (v3), and the v2 endpoints open `user_data.sql` (v2
  PostgreSQL COPY dump). `detect_format` tells them apart. An earlier version of
  this docstring claimed both endpoints took both formats, which they do not.

Logic lives in backup_core.py / v2_import.py; this module wires routes.
"""

import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

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

import backup_db as bdb
import engine
import storage_paths

bp = Blueprint("backup", __name__)

#: Tables a full reset must NEVER touch: identity + the global addon
#: catalogue that user rows point at, and SQLite's own bookkeeping. Everything
#: else in the public schema is content and gets wiped.
_RESET_SAFELIST = {"users", "addons", "sqlite_sequence"}

#: Media categories whose on-disk contents are erased by a reset.
_RESET_MEDIA_CATEGORIES = ("pdfs", "audio", "infographics", "images",
                           "scraped_pdfs")


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


# ─── Full destructive reset ───────────────────────────────────────────
# "Borrar todo": wipe every content table and every uploaded media file so
# the user can then restore a clean backup. Destructive on purpose, so it
# takes a timestamped safety snapshot of the SQLite file FIRST and reports
# its path -- a reset is only acceptable if it is recoverable.


def _base_tables(conn) -> list:
    """Every public BASE TABLE, lowercased order. Cross-engine: SQLite
    answers through the information_schema temp views sqlite_compat builds."""
    rows = bdb.query_on_conn(
        conn,
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' "
        "ORDER BY table_name",
    )
    return [r[0] for r in rows]


def _delete_order(conn, tables: set) -> list:
    """Children first, so a foreign-key-enforcing engine (Postgres) accepts
    the deletes. Reuses the same live-schema ordering the export uses."""
    try:
        from backup_user import _order_tables
        return list(reversed(_order_tables(conn, set(tables))))
    except Exception:
        # Ordering is a nicety where FKs are off (SQLite); never let it
        # block the wipe itself.
        return sorted(tables, reverse=True)


def _snapshot_sqlite() -> str | None:
    """Timestamped safety copy of the SQLite file, next to it.

    Uses the sqlite3 online backup API rather than a file copy so a live WAL
    is captured consistently. Returns None when there is no SQLite file
    (a Postgres deploy has nothing local to snapshot)."""
    try:
        db_file = Path(engine.sqlite_path())
    except Exception:
        return None
    if not db_file.is_file():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dest = db_file.with_name(
        f"{db_file.stem}.reset-{stamp}{db_file.suffix or '.db'}")
    src = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return str(dest)


def _dir_size_and_clear(path: str) -> tuple:
    """(files, bytes) removed from a directory's contents, then empty it.

    Counts before unlinking so the report can say how much was erased rather
    than merely that the folder is empty."""
    if not os.path.isdir(path):
        return 0, 0
    files = 0
    size = 0
    for root, _dirs, names in os.walk(path):
        for name in names:
            try:
                size += os.path.getsize(os.path.join(root, name))
                files += 1
            except OSError:
                continue
    for entry in os.listdir(path):
        target = os.path.join(path, entry)
        try:
            if os.path.isdir(target):
                shutil.rmtree(target, ignore_errors=True)
            else:
                os.remove(target)
        except OSError:
            continue
    return files, size


def _clear_media() -> dict:
    """Erase uploaded media + leftover restore-job scratch, cross-engine."""
    dirs = []
    for category in _RESET_MEDIA_CATEGORIES:
        try:
            dirs.append(storage_paths.category_dir(category))
        except KeyError:
            continue
    base = os.environ.get("DATA_DIR") or str(engine.data_dir())
    dirs.append(os.path.join(base, "restore-jobs"))

    files = 0
    size = 0
    for path in dirs:
        f, s = _dir_size_and_clear(path)
        files += f
        size += s
    return {"files_removed": files, "bytes_removed": size, "dirs": dirs}


def _integrity_check(conn) -> str:
    """PRAGMA integrity_check on SQLite; reported as unavailable elsewhere."""
    try:
        cur = conn.cursor()
        try:
            cur.execute("PRAGMA integrity_check")
            row = cur.fetchone()
        finally:
            cur.close()
    except Exception as exc:
        return f"unavailable: {exc}"
    if row is None:
        return "unknown"
    value = row[0] if not isinstance(row, dict) else list(row.values())[0]
    return str(value)


@bp.post("/backup/reset")
@token_required
def backup_reset(user_id):
    """Wipe ALL content (safekeeper: users/addons/sqlite_sequence).

    Returns a report: the safety snapshot path, rows deleted per table, media
    removed and the post-wipe integrity result, so the UI can show what the
    user just did and offer Restore as the next step."""
    try:
        snapshot = _snapshot_sqlite()
    except Exception as exc:
        return jsonify({"error": f"snapshot failed: {exc}"}), 500

    media = _clear_media()

    conn = get_connection()
    try:
        tables = set(_base_tables(conn))
        wipe = tables - _RESET_SAFELIST
        rows_deleted = {}
        cur = conn.cursor()
        try:
            for table in _delete_order(conn, wipe):
                cur.execute(f'DELETE FROM "{table}"')
                rows_deleted[table] = cur.rowcount
        finally:
            cur.close()
        conn.commit()
        integrity = _integrity_check(conn)
    except Exception as exc:
        conn.rollback()
        return jsonify({
            "error": f"reset failed: {exc}",
            "snapshot": snapshot,
            "media": media,
        }), 500
    finally:
        put_connection(conn)

    return jsonify({
        "ok": True,
        "snapshot": snapshot,
        "tables": rows_deleted,
        "tables_count": len(rows_deleted),
        "preserved": sorted(_RESET_SAFELIST),
        "media": media,
        "integrity_check": integrity,
    })