"""Restore of a personal (per-user) backup.

Split from backup_user.py to keep both files readable.

This endpoint is deliberately ADDITIVE: it inserts rows that are missing
and writes files that do not exist yet. It never drops a table, never
deletes and never overwrites. The legacy global restore in backup.py is a
different, destructive thing and is left untouched.
"""
import io
import json
import os
import time
import zipfile
from io import BytesIO

from flask import Blueprint, jsonify, request
from routes.auth import token_required

import backup_db as bdb
import database as db
from engine import data_dir as _engine_data_dir

bp = Blueprint("backup_user_restore", __name__)

# DATA_DIR kept for the container (the launcher mounts it at /data), but the
# local default has to come from engine.data_dir(), which resolves to
# ~/Library/Application Support/studyflow on macOS. Hardcoding "/data" made
# every personal restore fail on a Mac with
# "[Errno 30] Read-only file system: '/data'", because the root of the
# filesystem is read-only under SIP.
DATA_DIR = os.environ.get("DATA_DIR") or str(_engine_data_dir())
# A hand-made zip must not be able to write outside the data directory.
BACKUP_FORMAT = "studyflow-user-backup"


def _has_unsafe_member(names: list) -> bool:
    for member in names:
        parts = member.replace("\\", "/").split("/")
        if os.path.isabs(member) or ".." in parts or member.startswith("/"):
            return True
    return False


def _read_manifest(zf) -> dict:
    if "manifest.json" not in zf.namelist():
        raise ValueError("Not a personal backup: manifest.json is missing")
    manifest = json.loads(zf.read("manifest.json"))
    if manifest.get("format") != BACKUP_FORMAT:
        raise ValueError("Not a personal backup: unknown format")
    if int(manifest.get("version", 0)) != 1:
        raise ValueError(f"Unsupported backup version: {manifest.get('version')}")
    return manifest


def _parse_data_sql(sql_text: str) -> list:
    """Recover [(table, columns, data)] from a user_data.sql payload."""
    parsed, lines, i = [], sql_text.splitlines(), 0
    while i < len(lines):
        line = lines[i]
        if not line.startswith("COPY "):
            i += 1
            continue
        header = line[line.index("(") + 1:line.rindex(")")]
        table = line[len("COPY "):line.index("(")].strip().strip('"')
        columns = [c.strip().strip('"') for c in header.split(",")]
        i += 1
        data = []
        while i < len(lines) and lines[i] != "\\.":
            data.append(lines[i])
            i += 1
        parsed.append((table, columns, "\n".join(data) + "\n" if data else ""))
        i += 1
    return parsed


def _import_tables(conn, parsed, user_id: int) -> dict:
    """Insert the parsed tables, never overwriting an existing row.

    Each table is staged through a TEMP copy, then merged with
    ON CONFLICT DO NOTHING. Foreign keys are not checked during the load
    (session_replication_role=replica) because the SQL is generated in
    table-name order, not dependency order; it is restored immediately
    afterwards. A savepoint per table keeps one bad table from aborting the
    whole import.

    Rows are claimed for the RESTORING user, not the one who wrote the backup.
    The archive keeps the original owner id, and inserting it verbatim into a
    second install would hand the data to whichever account happens to occupy
    that id there -- a real transfer of ownership triggered by nothing more
    than restoring a backup into the wrong instance. Claiming the rows is also
    what makes the import additive: every child table has a user_id, so
    rewriting it is one pass per table with no special cases, and a row the
    user already has cannot be duplicated because ON CONFLICT does nothing.
    """
    stats = {}
    cur = conn.cursor()
    try:
        cur.execute("SET session_replication_role = replica")
        for table, columns, data in parsed:
            if not data.strip():
                continue
            real = _real_table(conn, table)
            if real is None:
                # The manifest names a table this schema does not have. v3
                # renamed some of hub's tables, so anything unmatched here is
                # not an error to surface but a fact to report.
                stats[table] = -1
                print(f"  · tabla ausente en este esquema: {table}",
                      flush=True)
                continue
            cur.execute("SAVEPOINT one_table")
            try:
                cur.execute(f'CREATE TEMP TABLE _stage (LIKE "{real}")')
                # The column list is mandatory, not decorative. `COPY _stage
                # FROM STDIN` with no list reads the staging table in ITS
                # physical order, while the payload arrives in the order the
                # archive's own header declares. Those two orders differ in v3
                # -- topics carries notes/status after order_index, so a bare
                # COPY slid `pending` into order_index and died with
                # `invalid input syntax for type integer`. Naming the columns
                # on the COPY makes the mapping by name, which is the only
                # thing that survives a rename or a reordered schema.
                stage_cols = ", ".join(f'"{c}"' for c in columns)
                cur.copy_expert(f"COPY _stage ({stage_cols}) FROM STDIN",
                                io.StringIO(data))
                if "user_id" in columns:
                    # The archive's own owner is discarded, not preserved and
                    # not inherited: the row is claimed for whoever is
                    # restoring. The target list carries user_id a second
                    # time on purpose, and the source supplies it from the
                    # parameter rather than from the staged row, so the
                    # original id is dropped instead of being fought over.
                    # Both lists are built from `columns` in one pass -- an
                    # earlier version zipped them together and produced
                    # N+1 expressions for N target columns, which is how
                    # every table came back "no importada".
                    # user_id must be dropped from the target list before it
                    # is re-added. Listing it twice is a duplicate-column error
                    # from Postgres, not a silent no-op, so the whole table
                    # comes back "no importada" -- which is what happened.
                    keep = [c for c in columns if c != "user_id"]
                    insert_cols = ", ".join(f'"{c}"' for c in keep + ["user_id"])
                    source = ", ".join([f'"{c}"' for c in keep]
                                        + ["%s::int"])
                    cur.execute(
                        f'INSERT INTO "{real}" ({insert_cols}) '
                        f"SELECT {source} FROM _stage ON CONFLICT DO NOTHING",
                        (user_id,))
                else:
                    cols = ", ".join(f'"{c}"' for c in columns)
                    cur.execute(
                        f'INSERT INTO "{real}" ({cols}) '
                        f"SELECT {cols} FROM _stage ON CONFLICT DO NOTHING")
                stats[table] = cur.rowcount
            except Exception as e:
                # undo ONLY this table. conn.rollback() would discard every
                # table already imported in this transaction.
                cur.execute("ROLLBACK TO SAVEPOINT one_table")
                stats[table] = -1
                print(f"  ✗ tabla {table} no importada: {e}", flush=True)
            finally:
                cur.execute("DROP TABLE IF EXISTS _stage")
        cur.execute("SET session_replication_role = DEFAULT")
    finally:
        cur.close()
    # Without this the whole import is rolled back when the connection
    # closes: the caller would be told "restored" and lose every row.
    conn.commit()
    return stats


def _real_table(conn, table: str) -> str | None:
    """Map a manifest table name onto this schema, or None if it is gone.

    The archive is written by whichever version produced it, so the names are
    a promise about hub's schema, not v3's. Renames are resolved here and a
    genuinely absent table returns None instead of raising, so one missing
    table cannot abort the restore of everything else.
    """
    available = bdb.available_tables(conn)
    return bdb.real_name(table, available)


def _write_missing_files(zf, names) -> int:
    """Copy files/ from the archive, skipping anything already on disk."""
    written = 0
    for member in names:
        if not member.startswith("files/"):
            continue
        rel = member[len("files/"):]
        dest = os.path.normpath(os.path.join(DATA_DIR, rel))
        if not dest.startswith(os.path.abspath(DATA_DIR)):
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if os.path.exists(dest):      # never clobber what is already there
            continue
        with zf.open(member) as src, open(dest, "wb") as out:
            out.write(src.read())
        written += 1
    return written


@bp.route("/backup/mine/restore", methods=["POST"])
@token_required
def restore_mine(user_id):
    """Import a personal backup. Adds rows and files; destroys nothing."""
    if not isinstance(user_id, int):
        return jsonify(error="Invalid user identity"), 400
    if "file" not in request.files:
        return jsonify(error="No file uploaded"), 400

    upload = request.files["file"]
    if not upload.filename or not upload.filename.endswith(".zip"):
        return jsonify(error="Expected a .zip personal backup"), 400

    started = time.monotonic()
    try:
        zf = zipfile.ZipFile(BytesIO(upload.read()))
        names = zf.namelist()
        if "user_data.sql" not in names:
            return jsonify(
                error="Not a personal backup (missing user_data.sql)"), 400

        try:
            manifest = _read_manifest(zf)
        except ValueError as e:
            return jsonify(error=str(e)), 400

        owner = manifest.get("user", {}).get("id")
        # v3 ids are ints, so the manifest stores an int and comparing it
        # against a stringified one would reject the user's own backup.
        if owner is not None and owner != user_id:
            return jsonify(
                error="This backup belongs to a different user"), 403

        if _has_unsafe_member(names):
            return jsonify(error="Invalid backup: unsafe path in archive"), 400

        conn = db.get_connection()
        try:
            stats = _import_tables(
                conn, _parse_data_sql(zf.read("user_data.sql").decode("utf-8")),
                user_id)
        finally:
            conn.close()

        written = _write_missing_files(zf, names)
        rows = sum(n for n in stats.values() if n > 0)
        failed = [t for t, n in stats.items() if n < 0]

        print(f"✅ restore personal: usuario {user_id} -> {rows} filas, "
              f"{written} ficheros en {time.monotonic() - started:.1f}s", flush=True)
        return jsonify(ok=True, rows_inserted=stats, rows_total=rows,
                       files_written=written, failed_tables=failed,
                       partial=bool(manifest.get("partial")),
                       note="Existing rows and files were left untouched.")
    except zipfile.BadZipFile:
        return jsonify(error="Invalid or corrupt zip file"), 400
    except Exception as e:
        print(f"✗ restore personal: {e}", flush=True)
        return jsonify(error=f"Restore failed: {e}"), 500
