"""Per-user backup — exports only what belongs to the logged-in user.

This is what the "Backup" button now calls. The global backup in backup.py
stays available as an endpoint for disaster recovery, but it is no longer
wired to any button.

Two invariants shape everything here:

  1. The user id comes from the @token_required decorator and nothing else.
     There is no user_id parameter on the endpoint, so it cannot be pointed
     at another account by crafting a request. v3 keys users by integer
     where hub used a UUID, and token_required falls back to the single local
     user when no token is sent.

  2. Import is ADDITIVE. It inserts rows and writes files, but it never
     drops the database and never overwrites a row that already exists.
     Restoring someone's data must not be able to wipe the instance.

Export uses COPY ... TO STDOUT, the same text format pg_dump emits, so
values round-trip exactly without hand-rolled escaping.

Tables are emitted in foreign-key order, so user_data.sql replays with a
plain `psql -f`. Replaying it does still require the instance's own global
reference data to exist (e.g. the `addons` catalogue that user_addons
points at) — that belongs to the app, not to a user's backup, so it is
deliberately not exported here.
"""
import io
import json
import os
import re
import tempfile
import time
import zipfile
from datetime import datetime
from io import BytesIO

from flask import Blueprint, jsonify, request, send_file
from routes.auth import token_required

import backup_db as bdb
import database as db

# Package-absolute imports, not bare ones. server.py puts backend/ on
# sys.path so `import backup_files` resolves, and it did work -- but
# backup_files.py reaches its siblings as `backend.backup_selection`, which
# loads the same file a SECOND time under a second module name. Two copies of
# a module means two distinct classes, so an isinstance check across the
# boundary fails on a type error instead of returning False. One style,
# everywhere.
from backup_files import (  # noqa: F401  (re-exported)
    AUDIO_RE, CATEGORY_OF, FILE_CATEGORIES, PDF_RE, resolve_files,
    _category_of_disk_file, archive_rel,
)
from backup_resolve import (
    _resource_fragment, lit, resolve_scope, resolve_tree,
)
from backup_selection import Selection

# No url_prefix here on purpose. server.py registers every route blueprint
# with url_prefix='/api', and the argument it passes overrides a prefix
# declared on the blueprint itself. Leaving "/api/backup" in place would
# therefore be silently ignored and the routes would mount at /api/mine --
# reachable, wrong, and easy to mistake for a routing bug elsewhere.
from engine import data_dir as _engine_data_dir

bp = Blueprint("backup_user", __name__)

# DATA_DIR kept for the container (the launcher mounts it at /data), but the
# local default has to come from engine.data_dir(), which resolves to
# ~/Library/Application Support/studyflow on macOS. Hardcoding "/data" made
# every backup/restore fail on a Mac with
# "[Errno 30] Read-only file system: '/data'", because the root of the
# filesystem is read-only under SIP.
DATA_DIR = os.environ.get("DATA_DIR") or str(_engine_data_dir())

# Session tokens are deliberately never exported. Replaying them would
# resurrect live sessions, and a backup is the wrong place to carry
# credentials.
#
# The v3 table that mattered for hub, `auth_tokens`, does not exist here. Two
# others do, and they are the same class of thing: passwords. A backup is a
# file the user downloads and can lose in a cloud folder, so an OpenZen or
# SCORM secret riding along in it is a real disclosure, and restoring it would
# quietly overwrite a password the user has since changed. Both are rebuilt
# by re-authenticating in the target install, which takes one click.
EXCLUDED_USER_TABLES = {"auth_tokens",
                        "openzen_credentials",
                        "scorm_credentials"}

# Tables that hold a user's data but carry no user_id of their own, so
# filtering by user_id would silently drop them. Each links to a parent
# that does have one.
#
# Found by auditing every table without a user_id for a foreign key back
# to one that has it — jsp_cells and agent_tools alone would have lost
# Jaime's JS Playground notebooks and 4213 agent-tool rows.
CHILD_TABLE_SPECS = [
    ("jsp_cells", [("notebook_id", "jsp_notebooks")]),
    ("agent_tools", [("agent_id", "agents")]),
    ("match_scores", [("deck_id", "flashcards_decks")]),
    ("match_failed_cards", [("deck_id", "flashcards_decks"),
                            ("card_id", "flashcards_cards")]),
]

# FILE_CATEGORIES, PDF_RE, AUDIO_RE and DATA_DIR now live in
# backup_files.py and are imported at the top of this file.

# ═══════════════════════════════════════════════════════════════════
# DB export
# ═══════════════════════════════════════════════════════════════════

def _username(conn, user_id: int) -> str:
    """The display name, for the manifest and the download filename.

    Read from the database rather than off the token. The JWT carries only the
    id, and a filename built from a guessed or missing name would either lose
    the user's identity or carry a stale one into every future backup.

    v3 calls the column `name`, not `username` as hub did. Both are checked
    rather than one being assumed: which name a schema uses is exactly the
    kind of detail that differs between installs, and a missing column here
    costs the whole export, since this runs before anything is written.
    """
    row = bdb.query_dicts_on_conn(conn, """
        SELECT name AS name, email AS email FROM users WHERE id = %s
    """, (user_id,))
    if not row:
        return "user"
    label = (row[0]["name"] or "").strip() or (row[0]["email"] or "").strip()
    return label or "user"


def _user_tables(conn) -> list[str]:
    """Every public table carrying a user_id, minus the excluded ones."""
    rows = bdb.query_dicts_on_conn(conn, """
        SELECT c.table_name
        FROM information_schema.columns c
        JOIN information_schema.tables t
          ON t.table_schema = c.table_schema AND t.table_name = c.table_name
        WHERE c.table_schema = 'public'
          AND c.column_name = 'user_id'
          AND t.table_type = 'BASE TABLE'
        ORDER BY c.table_name
    """)
    return [r["table_name"] for r in rows
            if r["table_name"] not in EXCLUDED_USER_TABLES]


def _copy_out(conn, sql_text: str) -> str:
    """Run a COPY ... TO STDOUT and return the text payload."""
    buf = io.StringIO()
    cur = conn.cursor()
    try:
        cur.copy_expert(sql_text, buf)
    finally:
        cur.close()
    return buf.getvalue()


def _table_columns(conn, table: str) -> list[str]:
    """Column names in ordinal order — COPY needs the exact list to replay."""
    rows = bdb.query_dicts_on_conn(conn, """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position
    """, (table,))
    return [r["column_name"] for r in rows]


def _order_tables(conn, tables: set) -> list:
    """Order tables parents-first so a plain `psql -f` satisfies the FKs.

    The in-app importer can switch off constraint checks, but the exported
    user_data.sql has to replay on a stock psql connection, where FKs are
    enforced. Ordering is derived from the live schema rather than
    hardcoded, so it stays correct if the schema changes.
    """
    if len(tables) < 2:
        return sorted(tables)
    # Aliased, and read through query_on_conn rather than conn.cursor().
    #
    # Two independent reasons, both of which showed up as
    # "not enough values to unpack (expected 2, got 1)":
    #
    #   1. Both selected columns are named table_name. v3's pooled connections
    #      carry a dict row factory, and a RealDictRow is keyed by column
    #      name, so the second column overwrote the first and every row
    #      unpacked to one value. The aliases make the keys distinct, which is
    #      what a dict row needs.
    #   2. query_on_conn forces a plain cursor, so this does not depend on how
    #      the connection was obtained. hub's psycopg2 had no dict factory, so
    #      its positional unpacking was correct there and only here.
    rows = bdb.query_on_conn(conn, """
        SELECT tc.table_name AS child, ccu.table_name AS parent
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.constraint_schema = kcu.constraint_schema
        JOIN information_schema.constraint_column_usage ccu
          ON ccu.constraint_name = tc.constraint_name
         AND ccu.constraint_schema = tc.constraint_schema
        WHERE tc.constraint_type = 'FOREIGN KEY'
          AND tc.table_name = ANY(%s)
    """, (list(tables),))
    edges = [(r[1], r[0]) for r in rows if r[0] in tables and r[1] in tables]

    parents = {t: set() for t in tables}      # table -> tables that must go first
    for parent, child in edges:
        if parent != child:
            parents[child].add(parent)

    ordered, placed = [], set()
    while len(placed) < len(tables):
        ready = sorted(t for t in tables
                       if t not in placed and parents[t] <= placed)
        if not ready:            # cycle: fall back to alphabetical for the rest
            ready = sorted(tables - placed)[:1]
        ordered.extend(ready)
        placed.update(ready)
    return ordered


def _export_user_db(conn, user_id: str, scope: dict = None) -> dict:
    """Produce {table: {"columns": [...], "data": COPY-text}} for the user.

    Covers their users row, every user_id table, and the child tables a
    naive user_id filter would miss. Tables come back in FK order.

    `scope` maps table -> extra WHERE fragment, produced by
    backup_resolve. An absent scope is a full backup and is byte-identical
    to the behaviour before selection existed. A fragment of "FALSE" means
    the table is out of scope, so it ships zero rows rather than being
    skipped — the manifest still lists it, which is the honest record.
    """
    # Interpolated rather than bound because guard() composes SQL fragments
    # per table and a bound parameter cannot be threaded through that. Safe
    # precisely because user_id is checked as an int by the caller: no quote,
    # no string, nothing to inject.
    uid = str(user_id)
    scope = scope or {}
    out = {}
    unclassified = []

    def grab(table, select_sql):
        out[table] = {
            "columns": _table_columns(conn, table),
            "data": _copy_out(conn, select_sql),
        }

    def guard(table: str, base: str) -> str:
        """AND the selection fragment onto an existing WHERE clause.

        The table name is resolved through the same rename map the scope was
        built with, and both directions are tried. Without this, `quiz_questions`
        was classified under hub's name `questions` and so matched no scope key
        at all: it fell into the unclassified branch, shipped unfiltered, and
        carried all 272 question rows into an archive whose blocks held 10. A
        downstream replay then failed on the foreign key to block 2551, which
        the archive did not contain. The rename is why the export looked
        complete -- every table was present -- while being wrong.
        """
        if not scope:
            return base
        frag = scope.get(table)
        if frag is None:
            # Scope keys are DECLARED names (hub's), so a renamed table has to
            # be matched through the inverse map. TABLE_RENAMES reads
            # declared -> real, the opposite direction from what is needed
            # here. Deriving the inverse instead of hand-writing it keeps a
            # future rename from having to be remembered in two places.
            for declared, real in bdb.TABLE_RENAMES.items():
                if real == table:
                    frag = scope.get(declared)
                    break
        if frag is None:
            # A table nobody classified. Shipping it would ignore whatever
            # the user unchecked; dropping it would lose their data
            # silently. Keep it, and name it in the manifest.
            unclassified.append(table)
            return base
        if not frag:
            return base
        return "FALSE" if frag == "FALSE" else f"({base}) AND {frag}"

    grab("users", f"COPY (SELECT * FROM users WHERE id = {uid}) TO STDOUT")
    _grab_user_tables(conn, grab, guard, uid)
    _grab_child_tables(conn, grab, guard, uid)

    ordered = _order_tables(conn, set(out))
    if unclassified:
        print(f"  · tablas sin ámbito: {', '.join(sorted(unclassified))}",
              flush=True)
    return {t: out[t] for t in ordered}


def _grab_user_tables(conn, grab, guard, uid) -> None:
    for table in _user_tables(conn):
        where = guard(table, f"user_id = {uid}")
        try:
            grab(table, f"COPY (SELECT * FROM {table} WHERE {where}) TO STDOUT")
        except Exception as e:
            # One unreadable table must not cost the user every other table.
            # The rollback is not optional: a failed COPY leaves the
            # transaction aborted and every later query on this connection
            # would fail in turn, so without it the archive would silently
            # contain only the tables that happened to sort before the
            # failure.
            conn.rollback()
            print(f"  · tabla {table} omitida: "
                  f"{str(e).splitlines()[0]}", flush=True)


def _grab_child_tables(conn, grab, guard, uid) -> None:
    """Tables with no user_id of their own, reached through a parent.

    Skipped when the table is absent. CHILD_TABLE_SPECS describes hub, where a
    notebook's cells and an agent's tools hang off their parent; v3 has none of
    those tables, and COPY against a relation that does not exist aborts the
    whole export rather than skipping one entry. Absence is already reported
    through resolve_tables(), so dropping it here loses no information.
    """
    available = bdb.available_tables(conn)
    for table, links in CHILD_TABLE_SPECS:
        real = bdb.real_name(table, available)
        if real is None:
            continue
        parents = [(col, bdb.real_name(p, available) or p)
                   for col, p in links]
        cond = " OR ".join(
            f'"{col}" IN (SELECT id FROM "{parent}" WHERE user_id = {uid})'
            for col, parent in parents if bdb.real_name(parent, available)
        )
        if not cond:
            continue
        try:
            grab(real,
                 f"COPY (SELECT * FROM {real} WHERE {guard(table, cond)}) "
                 f"TO STDOUT")
        except Exception as e:
            # A child table may be absent on an older schema; that must not
            # take the whole backup down.
            print(f"· child table {table} omitida: {e}", flush=True)


def _count_rows(entry: dict) -> int:
    """Data lines in a COPY payload (the trailing '\\.' is not a row)."""
    return sum(1 for line in entry["data"].splitlines() if line != "\\.")


def _table_sql(table: str, entry: dict) -> str:
    """Emit replayable SQL for one table.

    The COPY data alone is not a script: `COPY ... TO STDOUT` writes data
    lines only. Reconstructing the header — table, column list, FROM
    stdin — is what makes the file restorable by psql.
    """
    if _count_rows(entry) == 0:
        return ""
    cols = ", ".join(f'"{c}"' for c in entry["columns"])
    # psycopg2's COPY TO STDOUT does NOT emit the terminating "\." line,
    # but psql requires it to know where the data ends. Without it the
    # generated SQL swallows the following tables and never replays.
    body = entry["data"].rstrip("\n")
    return (f'\n-- ===== {table} =====\n'
            f'COPY "{table}" ({cols}) FROM stdin;\n'
            f'{body}\n\\.\n')


# ═══════════════════════════════════════════════════════════════════
# File resolution
# ═══════════════════════════════════════════════════════════════════

def _topic_filter(sel, tree):
    """Row-scoping fragments for file resolution, keyed by table.

    Returns None for a full backup, which must reproduce the previous
    behaviour exactly — including the rows whose topic does not resolve,
    which the verified full backup does contain.

    Keyed by TABLE, not by alias, and with unqualified column names. The
    two sources have different columns: `ai_tasks` has no `course_id` at
    all, so a single shared condition would either fail on ai_tasks or
    have to be weakened enough to leak other subjects' audio.

    `pdfs` and `images` are here for the same reason, and were missing at
    first. _collect_pdfs scopes itself with _frag(scope, "pdfs"), so with no
    entry under that key the fragment is None and the predicate degrades to
    `user_id = %s` — every PDF the user owns, regardless of the tick. The
    symptom was a 37 MB archive for one course where 5 PDFs were selected,
    with 238 files correctly reported as excluded: the exclusion accounting
    ran against the real selection while the collection had already ignored
    it. Scope the SELECTION and the ACCOUNTING have to come from the same
    place, or the manifest reports a restraint the archive did not perform.

    Reusing the predicates from backup_resolve rather than writing new ones
    keeps the SQL that decides what ships in one place; a copy here would be
    free to drift the moment the pdf-ref block convention changes.
    """
    if sel is None or sel.is_everything or not sel.has_tree_pick:
        return None
    c, t, b = tree["courses"], tree["topics"], tree["blocks"]
    return {
        "blocks": (f"(id IN {lit(b)} OR topic_id IN {lit(t)} "
                   f"OR course_id IN {lit(c)})"),
        "ai_tasks": f"(topic_id IN {lit(t)})",
        "pdfs": _resource_fragment("pdfs", sel, tree),
        "images": _resource_fragment("images", sel, tree),
    }


# ═══════════════════════════════════════════════════════════════════
# MANIFEST
# ═══════════════════════════════════════════════════════════════════

def _build_manifest(user, db_data, files, missing, unattributed,
                    selection=None, excluded=None) -> dict:
    counts, sizes = {}, {}
    for abs_path, _arc, cat in files:
        size = os.path.getsize(abs_path)
        counts[cat] = counts.get(cat, 0) + 1
        sizes[cat] = sizes.get(cat, 0) + size
    manifest = {
        "format": "studyflow-user-backup",
        "version": 1,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "user": {"id": user["id"], "username": user.get("username"),
                 "email": user.get("email")},
        "selection": (selection or Selection(None)).as_manifest(),
        "database": {
            "rows_by_table": {t: _count_rows(e) for t, e in db_data.items()},
            "child_tables_resolved": [t for t, _ in CHILD_TABLE_SPECS],
            "excluded_tables": sorted(EXCLUDED_USER_TABLES),
        },
        "files": {
            "categories": {c: FILE_CATEGORIES.get(c, c) for c in counts},
            "counts": counts,
            "bytes": sizes,
            "total_files": len(files),
            "total_bytes": sum(sizes.values()),
        },
        "missing_references": sorted(set(missing)),
        # Listed per category against storage_paths -- never against a root
        # that may not exist on the machine that reads the manifest.
        "unattributed_files": [
            archive_rel(_category_of_disk_file(p), p) for p in unattributed
        ],
    }
    # A partial backup is a subset on purpose. The restore reads this and
    # warns, because restoring it does not reproduce a whole account and
    # anyone assuming otherwise will be surprised.
    if excluded:
        manifest["files"]["excluded_by_selection"] = len(excluded)
    if manifest["selection"].get("mode") == "partial":
        manifest["partial"] = True
    return manifest


# ═══════════════════════════════════════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════════════════════════════════════

@bp.route("/backup/mine", methods=["POST"])
@token_required
def backup_mine(user_id):
    """Build a backup containing only the authenticated user's data.

    The request body is optional. Absent, empty or meaningless means a full
    backup, which is the behaviour that shipped first and is what the
    button did before selection existed.
    """
    # v3 keys users by integer, where hub used a UUID, so the id arrives as an
    # int from token_required and the UUID regex that used to guard this
    # function would have rejected every legitimate request.
    if not isinstance(user_id, int):
        return jsonify(error="Invalid user identity"), 400

    sel = Selection(request.get_json(silent=True))
    started = time.monotonic()

    tmp = tempfile.mkdtemp()
    try:
        conn = db.get_connection()
        try:
            username = _username(conn, user_id)
            db_data, files, missing, unattributed, excluded = _gather(
                conn, user_id, sel)
        finally:
            conn.close()

        manifest = _build_manifest({"id": user_id, "username": username},
                                   db_data, files, missing, unattributed,
                                   sel, excluded)
        db_sql = "".join(_table_sql(t, e) for t, e in db_data.items())
        data = _zip_bytes(tmp, manifest, db_sql, files)

        print(f"✅ backup personal: {username} -> {len(data)/1e6:.1f} MB en "
              f"{time.monotonic() - started:.1f}s", flush=True)
        return _respond(data, username)
    except Exception as e:
        print(f"✗ backup personal: {e}", flush=True)
        return jsonify(error=f"Backup failed: {e}"), 500
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def _gather(conn, user_id: str, sel: Selection):
    """Resolve the selection, export the rows, resolve the files."""
    scope = resolve_scope(conn, sel, user_id)
    tree = resolve_tree(conn, sel, user_id)
    db_data = _export_user_db(conn, user_id, scope)
    files, missing, unattributed, excluded = resolve_files(
        conn, user_id, sel, _topic_filter(sel, tree))
    total_rows = sum(_count_rows(e) for e in db_data.values())
    print(f"  · {total_rows} filas en {len(db_data)} tablas, {len(files)} "
          f"ficheros, {len(missing)} referencias ausentes, "
          f"{len(excluded)} excluidos por la selección", flush=True)
    return db_data, files, missing, unattributed, excluded


def _zip_bytes(tmp: str, manifest: dict, db_sql: str, files) -> bytes:
    """Assemble the archive and read it back ready to send."""
    path = f"{tmp}/backup.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
        zf.writestr("manifest.json",
                    json.dumps(manifest, indent=2, ensure_ascii=False))
        zf.writestr("user_data.sql", db_sql)
        for abs_path, arc, _cat in files:
            zf.write(abs_path, arcname=arc)
    with open(path, "rb") as f:
        return f.read()


def _respond(data: bytes, username: str):
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    safe = re.sub(r"[^A-Za-z0-9_-]", "", username) or "user"
    return send_file(
        BytesIO(data),
        as_attachment=True,
        download_name=f"studyflow_{safe}_{stamp}.zip",
        mimetype="application/zip",
    )


# NOTE: _table_sql is defined once, near _count_rows, and emits a
# replayable COPY header (table + column list + FROM stdin). A COPY TO
# STDOUT payload is data lines only — wrapping it without that header
# produces a file psql cannot replay.
