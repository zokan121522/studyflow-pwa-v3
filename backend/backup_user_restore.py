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
import re
import shutil
import sys
import threading
import time
import uuid
import zipfile

from flask import Blueprint, jsonify, request
from routes.auth import token_required

import backup_db as bdb
import database as db
from engine import data_dir as _engine_data_dir
from backup_files import (AUDIO_RE, CATEGORY_OF, FILE_CATEGORIES, IMAGE_RE,
                          INFOGRAPHIC_RE, PDF_RE, _safe_name)
from backup_options import (BYTES_PER_ROW_ESTIMATE, _assemble_agenda,
                            _assemble_tree)
from backup_selection import (ALWAYS_TABLES, SCOPE_TABLES, TREE_TABLES,
                              Selection)
from storage_paths import category_dir

bp = Blueprint("backup_user_restore", __name__)

# WHERE ``files/<category>/<name>`` lands is decided by
# storage_paths.category_dir -- the same resolver the export reads from and
# the serving routes point at. Guessing a base here twice is exactly how
# both past failures happened: engine.data_dir() put everything one level
# above the app's folders, then dirname(PDF_UPLOAD_FOLDER) put a Windows
# restore into C:\srv\backend\uploads -- a single "root" that only exists
# in Docker -- while the app served audio, infographics and PDFs from three
# other places. Rows came back both times; every file looked lost.
#
# DATA_DIR stays meaningful for the container (the launcher mounts it at
# /data), so it is still consulted, but only as the input the export would
# have used.
DATA_DIR = os.environ.get("DATA_DIR") or str(_engine_data_dir())
# A hand-made zip must not be able to write outside the data directory.
BACKUP_FORMAT = "studyflow-user-backup"


def _log(message: str) -> None:
    """Print without letting the console encoding finish a request for us.

    The success line of this endpoint used to be a bare print of
    "✅ restore personal …". The Windows launcher runs the child with stdout
    on a file opened in the locale encoding (cp1252), where U+2705 does not
    exist: the rows were already committed and the files already written,
    the print raised UnicodeEncodeError, the except printed "✗" and raised
    AGAIN, and the client saw a 500 for a restore that had succeeded.
    launcher/serve.py now forces UTF-8 on stdio at startup; this is the
    second line of defence, so no print in this endpoint can ever turn a
    completed restore into an error response.
    """
    try:
        print(message, flush=True)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        print(message.encode(enc, "replace").decode(enc), flush=True)


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


# ═══════════════════════════════════════════════════════════════════
# Selective import — the mirror of what the export decided
# ═══════════════════════════════════════════════════════════════════
#
# The export answers "what ships?" with the SQL fragments in
# backup_resolve. An import that re-decided membership with different
# rules would keep rows the archive never contained -- or drop ones it
# did. Every predicate below restates one of those fragments in Python,
# over the archive's own rows, so a partial restore and the partial
# export that produced it can never disagree.

_COPY_ESCAPES = {"t": "\t", "n": "\n", "r": "\r", "b": "\b",
                 "f": "\f", "v": "\v", "\\": "\\"}


def _raw_selection():
    """The optional `selection` field as a plain dict, or None.

    None means the caller asked for nothing in particular (old client,
    curl without the field) and also covers a present-but-unreadable
    value: a truncated upload or a hand-edited request must degrade to
    the historical full restore, never to a 500. The chunked job store
    keeps this dict; only the request-facing wrapper wraps it later.
    """
    raw = request.form.get("selection")
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        _log("  · selection ilegible, se importa el backup completo")
        return None
    if not isinstance(parsed, dict):
        _log("  · selection no es un objeto, se importa el backup completo")
        return None
    return parsed


def _selection_from_request():
    """The same field, wrapped for the routes that import right away."""
    parsed = _raw_selection()
    return None if parsed is None else Selection(parsed)


def _key(value):
    """Canonical membership key: row values and selection ids compared alike.

    COPY ships every value as text while the selection carries what the
    selector sent. v3 keys courses/topics/blocks with integers, so "105"
    and 105 must meet; hub's slug ids stay the strings they are.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return str(int(text))
    except ValueError:
        return text


def _unescape_cell(raw: str):
    """Decode one COPY field. `\\N` is SQL NULL; backslash escapes decode."""
    if raw == "\\N":
        return None
    if "\\" not in raw:
        return raw
    out, i = [], 0
    while i < len(raw):
        if raw[i] == "\\" and i + 1 < len(raw):
            out.append(_COPY_ESCAPES.get(raw[i + 1], raw[i + 1]))
            i += 2
        else:
            out.append(raw[i])
            i += 1
    return "".join(out)


def _row_dicts(section, cols) -> list:
    """Decoded `{col: value}` rows for the wanted columns; absent = None."""
    if not section:
        return []
    columns, data = section
    if not data:
        return []
    idx = [columns.index(c) if c in columns else -1 for c in cols]
    lines = data.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [{
        c: _unescape_cell(cells[i]) if 0 <= i < len(cells) else None
        for c, i in zip(cols, idx)
    } for line in lines
        for cells in [line.split("\t")]]


def _line_count(data: str) -> int:
    """COPY rows in a payload (a trailing newline is not a row)."""
    if not data:
        return 0
    lines = data.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return len(lines)


def _section(sections: dict, declared: str):
    """The archive's (columns, data) for a DECLARED table name, or None.

    The scope maps are keyed the way hub named things; v3 renamed some
    tables, so the rename is resolved the same way export's guard()
    resolves it -- direct hit first, inverse map second.
    """
    if declared in sections:
        return sections[declared]
    real = bdb.TABLE_RENAMES.get(declared)
    if real is not None and real in sections:
        return sections[real]
    return None


def _archive_tree(sections: dict, sel) -> dict:
    """courses/topics/blocks key sets the resolved tree asks for.

    Mirrors backup_resolve._tree_from_picks step for step: seed with an
    existence intersection (pulling each picked row's parents as the
    rows themselves report them), then children, then the orphan-block
    rule, then parents. One pass each, no fixpoint -- the original does
    not loop either, and matching its ORDER matters: a seed-pulled topic
    must be in place before children run or its blocks are missed.
    """
    all_courses = {_key(r["id"]) for r in
                   _row_dicts(sections.get("courses"), ("id",))}
    topics = {}                      # topic id -> course id (archive rows)
    for r in _row_dicts(sections.get("topics"), ("id", "course_id")):
        topics[_key(r["id"])] = _key(r["course_id"])
    blocks = []                      # (block id, topic id, course id)
    for r in _row_dicts(sections.get("blocks"),
                        ("id", "topic_id", "course_id")):
        blocks.append((_key(r["id"]), _key(r["topic_id"]),
                       _key(r["course_id"])))

    sel_c = {_key(v) for v in sel.course_ids}
    sel_t = {_key(v) for v in sel.topic_ids}
    sel_b = {_key(v) for v in sel.block_ids}
    C = sel_c & all_courses
    T = sel_t & set(topics)
    B = sel_b & {b[0] for b in blocks}

    # seed: parents of the picked rows, taken as the rows report them
    # (dangling ids included, exactly like the SQL does)
    for tid in sel_t & set(topics):
        cid = topics[tid]
        if cid is not None:
            C.add(cid)
    for bid, tid, cid in blocks:
        if bid in B:
            if tid is not None:
                T.add(tid)
            if cid is not None:
                C.add(cid)

    # children: a ticked course reaches its topics and their blocks
    for tid, cid in topics.items():
        if cid in C:
            T.add(tid)
    for bid, tid, cid in blocks:
        if cid in C or tid in T:
            B.add(bid)
    if not sel_t and not sel_b and all_courses and all_courses <= sel_c:
        # "All courses ticked" also means the blocks that hang off
        # nothing -- the selector cannot tick them, but a full backup
        # must contain them (blocks 19/20 in the original bug).
        for bid, tid, cid in blocks:
            if tid is None and cid is None:
                B.add(bid)

    # parents: a ticked block or topic brings its topic and course up
    for bid, tid, cid in blocks:
        if bid in B:
            if tid is not None:
                T.add(tid)
            if cid is not None:
                C.add(cid)
    for tid, cid in topics.items():
        if tid in T and cid is not None:
            C.add(cid)

    return {"courses": C, "topics": T, "blocks": B}


def _archive_agenda(sections: dict, sel) -> dict:
    """week/day/session key sets, mirroring backup_resolve.resolve_agenda.

    The same two soft hops the export walks: a session reaches its day
    from its own column and the week only through the day row that
    exists (a LEFT JOIN, so a missing day contributes its date but no
    week), then the forward closure fills the rest.
    """
    weeks = {_key(r["week_id"]) for r in
             _row_dicts(sections.get("weeks"), ("week_id",))}
    days = {}                        # date -> week id
    for r in _row_dicts(sections.get("days"), ("date", "week_id")):
        days[_key(r["date"])] = _key(r["week_id"])
    sessions = {}                    # session id -> day date
    for r in _row_dicts(sections.get("sessions"), ("id", "day_date")):
        sessions[_key(r["id"])] = _key(r["day_date"])

    W = {_key(v) for v in sel.week_ids} & weeks
    D = {_key(v) for v in sel.day_ids} & set(days)
    S = {_key(v) for v in sel.session_ids} & set(sessions)

    for date in list(D):
        wid = days.get(date)
        if wid is not None:
            W.add(wid)
    for sid in list(S):
        did = sessions.get(sid)
        if did is not None:
            D.add(did)
            wid = days.get(did)      # None when the day row is gone
            if wid is not None:
                W.add(wid)

    if W:
        for date, wid in days.items():
            if wid in W:
                D.add(date)
    if D:
        for sid, did in sessions.items():
            if did in D:
                S.add(sid)
    return {"weeks": W, "days": D, "sessions": S}


def _build_specs(sections: dict, sel) -> tuple:
    """({table: (cols, predicate)}, tree) for this selection.

    Built in resolve_scope's exact order: tree, agenda, scope switches,
    always-tables, then the four unconditional overrides (ai_tasks,
    pdfs, images, pdf_annotations) -- because resolve_scope assigns
    those last and clobbers whatever the scope loop wrote, quirks
    included (the agents_ai switch really does not gate ai_tasks rows).
    """
    tree = _archive_tree(sections, sel)
    C, T, B = tree["courses"], tree["topics"], tree["blocks"]
    has_tree = sel.has_tree_pick
    unat = sel.unattributed
    empty_tree = has_tree and not (C or T or B)
    agenda_on = sel.scope_on("agenda")
    agenda = _archive_agenda(sections, sel) if agenda_on \
        else {"weeks": set(), "days": set(), "sessions": set()}
    specs = {}

    # tree — _tree_fragment. With no picks the sets stay empty and every
    # predicate reads False, which is `id IN (NULL)` on the export side.
    specs["courses"] = (("id",), lambda r: _key(r["id"]) in C)
    specs["topics"] = (("id", "course_id"),
                       lambda r: _key(r["id"]) in T
                       or _key(r["course_id"]) in C)
    specs["blocks"] = (("id", "topic_id", "course_id"),
                       lambda r: _key(r["id"]) in B
                       or _key(r["topic_id"]) in T
                       or _key(r["course_id"]) in C)
    specs["cards"] = (("block_id", "topic_id", "course_id"),
                      lambda r: _key(r["block_id"]) in B
                      or _key(r["topic_id"]) in T
                      or _key(r["course_id"]) in C)
    specs["questions"] = (("block_id",),
                          lambda r: _key(r["block_id"]) in B)
    for table in ("quiz_results", "quiz_errors"):
        specs[table] = (("block_id", "topic_id", "course_id"),
                        lambda r: _key(r["block_id"]) in B
                        or _key(r["topic_id"]) in T
                        or _key(r["course_id"]) in C)

    # agenda — weeks/days/sessions/custom_categories (resolve_scope)
    W, D, S = agenda["weeks"], agenda["days"], agenda["sessions"]
    specs["weeks"] = (("week_id",),
                      lambda r: agenda_on and _key(r["week_id"]) in W)
    specs["days"] = (("date",),
                     lambda r: agenda_on and _key(r["date"]) in D)
    specs["sessions"] = (("id",),
                         lambda r: agenda_on and _key(r["id"]) in S)
    specs["custom_categories"] = ((), lambda r: agenda_on)

    # scope switches — anything not already given a dedicated rule above
    for scope, tables in SCOPE_TABLES.items():
        for table in tables:
            specs.setdefault(table, ((), lambda r, s=scope: sel.scope_on(s)))
    for table in ALWAYS_TABLES:
        specs.setdefault(table, ((), lambda r: True))

    # — the unconditional overrides of resolve_scope's tail —
    # blocks rows feed both media predicates (pdf-ref urls, image_id),
    # read once with the columns they carry
    pdfref, imgref = set(), set()
    if has_tree and not empty_tree:
        for r in _row_dicts(sections.get("blocks"),
                            ("id", "topic_id", "course_id", "type",
                             "url", "image_id")):
            if not (_key(r["id"]) in B or _key(r["topic_id"]) in T
                    or _key(r["course_id"]) in C):
                continue
            if (r["type"] or "") == "pdf-ref":
                m = re.search(r"/api/pdf/([0-9]+)", r["url"] or "")
                if m:
                    pdfref.add(str(int(m.group(1))))
            if r["image_id"] is not None:
                imgref.add(_key(r["image_id"]))

    def pdf_pred(r):
        if not has_tree:
            return True                  # _resource_fragment: "" no filter
        if empty_tree:
            return False                 # ...ticked but resolved to nothing
        if _key(r["course_id"]) in C or _key(r["topic_id"]) in T:
            return True
        if _key(r["id"]) in pdfref:
            return True
        return unat and r["course_id"] is None and r["topic_id"] is None

    def img_pred(r):
        if not has_tree:
            return True
        if empty_tree:
            return False
        if _key(r["course_id"]) in C or _key(r["topic_id"]) in T:
            return True
        if _key(r["id"]) in imgref:
            return True
        return unat and r["course_id"] is None and r["topic_id"] is None

    def ai_pred(r):
        if not has_tree:
            return True
        if _key(r["topic_id"]) in T:
            return True
        return unat and r["topic_id"] is None

    # annotations follow their PDFs: the ids the pdfs predicate keeps
    pdf_keep = {_key(r["id"]) for r in
                _row_dicts(sections.get("pdfs"),
                           ("id", "course_id", "topic_id"))
                if pdf_pred(r)}

    def ann_pred(r):
        if not has_tree:
            return True
        if empty_tree:
            return False
        if _key(r["pdf_id"]) in pdf_keep:
            return True
        return unat and r["pdf_id"] is None

    specs["ai_tasks"] = (("topic_id",), ai_pred)
    specs["pdfs"] = (("id", "course_id", "topic_id"), pdf_pred)
    specs["images"] = (("id", "course_id", "topic_id"), img_pred)
    specs["pdf_annotations"] = (("pdf_id",), ann_pred)

    # a renamed table must find its declared spec (export's guard()
    # resolves the same lookup through the inverse map)
    for declared, real in bdb.TABLE_RENAMES.items():
        if declared in specs:
            specs.setdefault(real, specs[declared])
    return specs, tree


def _filter_section(columns, data, spec) -> str:
    """COPY payload with only the lines whose decoded row passes.

    A kept line passes through byte for byte: it arrived as Postgres
    COPY text and re-encoding a decoded row would be a second writer
    for data that only ever needs to be read once. Short lines read
    their missing columns as NULL instead of dying.
    """
    if not data:
        return data
    cols, pred = spec
    idx = [columns.index(c) if c in columns else -1 for c in cols]
    lines = data.split("\n")
    trailing = bool(lines) and lines[-1] == ""
    if trailing:
        lines = lines[:-1]
    keep = []
    for line in lines:
        cells = line.split("\t")
        row = {c: _unescape_cell(cells[i]) if 0 <= i < len(cells) else None
               for c, i in zip(cols, idx)}
        if pred(row):
            keep.append(line)
    if not keep:
        return ""
    return "\n".join(keep) + ("\n" if trailing else "")


def _reachable_members(sections: dict, specs: dict, tree: dict,
                       sel) -> set:
    """`<category>/<name>` for every file a kept row points at.

    The rules are the export's collectors (_collect_pdfs, _collect_audio,
    ...) with _topic_filter's scoping on top: pdfs and images follow
    their row predicates, while ai_tasks audio and infographics scope on
    topics alone -- no NULL branch, no filter at all without a tree.
    """
    out = set()
    pdf_pred = specs["pdfs"][1]
    for r in _row_dicts(sections.get("pdfs"),
                        ("id", "course_id", "topic_id", "filename")):
        if not pdf_pred(r):
            continue
        stored = r.get("filename") or ""
        m = PDF_RE.search(os.path.basename(stored))
        name = m.group(1) if m else stored
        if _safe_name(name):
            out.add(f"pdfs/{name}")
    img_pred = specs["images"][1]
    for r in _row_dicts(sections.get("images"),
                        ("id", "course_id", "topic_id", "storage_path")):
        if not img_pred(r):
            continue
        stored = r.get("storage_path") or ""
        base = os.path.basename(stored)
        m = IMAGE_RE.search(base)
        name = m.group(1) if m else base
        if _safe_name(name):
            out.add(f"images/{name}")
    has_tree, T = sel.has_tree_pick, tree["topics"]
    for r in _row_dicts(sections.get("ai_tasks"),
                        ("topic_id", "result_content")):
        if has_tree and _key(r.get("topic_id")) not in T:
            continue
        text = r.get("result_content") or ""
        for m in AUDIO_RE.finditer(text):
            if _safe_name(m.group(1)):
                out.add(f"audio/{m.group(1)}")
        for m in INFOGRAPHIC_RE.finditer(text):
            if _safe_name(m.group(1)):
                out.add(f"infographics/{m.group(1)}")
    return out


def _filter_members(names, sel, reachable, manifest) -> list:
    """The file members this selection asks to write.

    A member survives when its category was ticked AND some kept row
    points at it, or when it sits in the manifest's unattributed list
    and that bucket was ticked. The list itself is always read: the
    export writes it regardless of the tick, and a full export ships no
    such files at all, so the distinction costs nothing here.
    """
    wanted = {CATEGORY_OF[m] for m in sel.media if m in CATEGORY_OF}
    unat = set((manifest or {}).get("unattributed_files") or ())
    out = []
    for member in names:
        parts = member.replace("\\", "/").split("/")
        if len(parts) < 3 or parts[0] != "files":
            out.append(member)          # manifest.json, user_data.sql, ...
            continue
        category = parts[1]
        if category not in FILE_CATEGORIES:
            out.append(member)          # unknown shape: writer decides
            continue
        if category not in wanted:
            continue
        rel = "/".join(parts[2:])
        if f"{category}/{rel}" in reachable or (
                sel.unattributed and f"{category}/{rel}" in unat):
            out.append(member)
    return out


def _safe_apply_selection(parsed, names, sel, manifest) -> tuple:
    """Filter rows and file members down to the selection.

    On ANY unexpected failure the full import runs instead: a broken
    selection may cost the user their chosen subset, never their
    restore. Tables nobody classified keep every row -- the export
    ships them unfiltered too and names them in the manifest.
    """
    if sel is None or sel.is_everything:
        return parsed, names
    try:
        sections = {t: (c, d) for t, c, d in parsed}
        specs, tree = _build_specs(sections, sel)
        filtered = []
        for table, columns, data in parsed:
            spec = specs.get(table)
            if spec is None:
                filtered.append((table, columns, data))
            else:
                filtered.append(
                    (table, columns, _filter_section(columns, data, spec)))
        reachable = _reachable_members(sections, specs, tree, sel)
        return filtered, _filter_members(names, sel, reachable, manifest)
    except Exception as e:
        _log(f"  · selección descartada, se importa el backup completo: {e}")
        return parsed, names


def _section_counts(stats: dict) -> dict:
    """Rows imported per selector section -- the `imported` report."""
    declared = {real: name for name, real in bdb.TABLE_RENAMES.items()}
    by_name = {}
    for table, n in stats.items():
        if n > 0:
            key = declared.get(table, table)
            by_name[key] = by_name.get(key, 0) + n

    def total(tables):
        return sum(by_name.get(t, 0) for t in tables)

    out = {"tree": total(TREE_TABLES),
           "agenda": total(SCOPE_TABLES["agenda"]),
           "media": total(("pdfs", "images")),
           "other": 0}
    for scope, tables in SCOPE_TABLES.items():
        if scope != "agenda":
            out[scope] = total(tables)
    counted = set(TREE_TABLES) | {"pdfs", "images"}
    for tables in SCOPE_TABLES.values():
        counted |= set(tables)
    out["other"] = sum(n for t, n in by_name.items() if t not in counted)
    return out


# The tree is the only part of a personal backup whose rows can be
# duplicated by id: courses/topics/blocks are keyed by a bare surrogate id
# with NO natural unique constraint (no slug, no uuid), so a bare
# `ON CONFLICT DO NOTHING` sees a conflict only when the surrogate ids
# collide -- and the archive's ids are a promise about the machine the
# backup was written on, not the one being restored into. When the two id
# spaces differ, every row looks new and a repeated restore duplicates the
# whole tree. These tables are therefore matched on natural keys before
# inserting, and the archive id is mapped to the id the row actually has on
# the target so children can follow.
_NATURAL_KEY_TABLES = {"courses", "topics", "blocks"}
# Parent-first processing order: topics only resolve under their course,
# blocks under their topic, so the tree is always loaded in this order no
# matter how the archive ordered the sections.
_TREE_ORDER = {"courses": 0, "topics": 1, "blocks": 2}
# Child tables that carry the tree's FK columns and must have those columns
# rewritten through the archive-id -> target-id maps once the parents above
# are resolved.
_REMAP_FK_TABLES = {"pdfs", "images", "quiz_questions", "quiz_results",
                    "quiz_errors"}
_ID_FK_COLUMNS = ("course_id", "topic_id", "block_id")
_FK_PARENT = {"course_id": "courses", "topic_id": "topics",
              "block_id": "blocks"}
_NATURAL_LOOKUPS = {
    "courses": "SELECT id FROM courses "
               "WHERE user_id = %s AND title = %s ORDER BY id LIMIT 1",
    "topics": "SELECT id FROM topics WHERE user_id = %s AND course_id = %s "
              "AND title = %s ORDER BY id LIMIT 1",
    "blocks": "SELECT id FROM blocks WHERE user_id = %s AND topic_id = %s "
              "AND title = %s ORDER BY id LIMIT 1",
}
# Tables handled row by row instead of through the single bulk INSERT: the
# tree (natural-key dedup) plus the children that must be remapped.
_PER_ROW_TABLES = _NATURAL_KEY_TABLES | _REMAP_FK_TABLES


def _restore_rows_per_row(cur, real, columns, data, user_id, maps) -> tuple:
    """Insert one staged table row by row, deduplicating on natural keys.

    The archive's ids describe the machine the backup came from, not the
    target, so for these tables the id is only a lookup key. Each row:

    1. is claimed for the restoring user (as the bulk path claims rows);
    2. has its tree FKs rewritten through the maps built by the parents
       processed before it;
    3. is matched on its natural key -- courses by (user_id, title),
       topics by (resolved course_id, title), blocks by (resolved
       topic_id, title). A match maps the archive id to the existing id,
       skips the insert and counts the row in `skipped`. An empty or
       missing title (or missing parent) means "always insert" and must
       never crash the match;
    4. is inserted with its archive id when that id is free, or under a
       fresh sequence id when the target already uses it for a DIFFERENT
       row: the tree prefers restoring the data to silently dropping it.
       Children keep the old never-overwrite semantics instead and are
       skipped.

    Returns (inserted, skipped).
    """
    cur.execute(f'CREATE TEMP TABLE _stage (LIKE "{real}")')
    stage_cols = ", ".join(f'"{c}"' for c in columns)
    cur.copy_expert(f"COPY _stage ({stage_cols}) FROM STDIN",
                    io.StringIO(data))
    cur.execute("SELECT * FROM _stage")
    staged = cur.fetchall()
    inserted = 0
    skipped = 0
    fk_cols = [c for c in columns if c in _ID_FK_COLUMNS]
    natural = real in _NATURAL_KEY_TABLES
    for raw in staged:
        # dict factory on Postgres, tuples/Row on SQLite: both are read.
        row = dict(raw) if isinstance(raw, dict) else dict(zip(columns, raw))
        if "user_id" in row:
            row["user_id"] = user_id
        for col in fk_cols:
            value = row.get(col)
            if value is None:
                continue
            parent = _FK_PARENT[col]
            if parent in maps and value in maps[parent]:
                row[col] = maps[parent][value]
        archive_id = row.get("id")
        existing = None
        if natural and row.get("title"):
            args = (user_id, row["title"])
            if real == "topics":
                args = ((user_id, row.get("course_id"), row["title"])
                        if row.get("course_id") is not None else None)
            elif real == "blocks":
                args = ((user_id, row.get("topic_id"), row["title"])
                        if row.get("topic_id") is not None else None)
            if args is not None:
                cur.execute(_NATURAL_LOOKUPS[real], args)
                hit = cur.fetchone()
                if hit is not None:
                    existing = hit["id"] if isinstance(hit, dict) else hit[0]
        if existing is not None:
            if archive_id is not None:
                maps[real][archive_id] = existing
            skipped += 1
            continue
        taken = False
        if archive_id is not None:
            cur.execute(f'SELECT 1 FROM "{real}" WHERE id = %s LIMIT 1',
                        (archive_id,))
            taken = cur.fetchone() is not None
        if taken and not natural:
            skipped += 1
            continue
        cols = ([c for c in columns if c != "id"]
                if taken or archive_id is None else list(columns))
        names = ", ".join(f'"{c}"' for c in cols)
        markers = ", ".join(["%s"] * len(cols))
        cur.execute(
            f'INSERT INTO "{real}" ({names}) VALUES ({markers}) RETURNING id',
            [row[c] for c in cols])
        got = cur.fetchone()
        new_id = got["id"] if isinstance(got, dict) else got[0]
        if archive_id is not None:
            maps[real][archive_id] = new_id
        inserted += 1
    cur.execute("DROP TABLE IF EXISTS _stage")
    return inserted, skipped


def _import_tables(conn, parsed, user_id: int) -> tuple:
    """Insert the parsed tables, never overwriting an existing row.

    The tree (courses -> topics -> blocks) is matched on NATURAL keys
    before anything is inserted: a row the user already has -- same title
    under the same resolved parent -- is not inserted again, its archive
    id is mapped to the existing row's id, and children rewritten through
    that map attach to the ORIGINAL parent. A second restore of the same
    archive therefore adds nothing, even when the archive's ids differ
    from every id the target already uses. That lookup replaces the bare
    `ON CONFLICT DO NOTHING` for these tables: their only key is the
    surrogate id, which is exactly the column that differs between
    machines. The agenda is untouched -- weeks/days/sessions carry stable
    natural keys already and keep the id-based merge.

    Each table is staged through a TEMP copy; a savepoint per table keeps
    one bad table from aborting the whole import. Parents are always
    processed before children (enforced here, whatever the archive's
    order), so the maps are complete before any child FK is rewritten.
    Foreign keys are not checked during the load
    (session_replication_role=replica) and are restored afterwards.

    Rows are claimed for the RESTORING user, not the one who wrote the
    backup: the archive keeps the original owner id, and inserting it
    verbatim into a second install would hand the data to whichever
    account happens to occupy that id there. Every child table has a
    user_id, so claiming is one rewrite per row with no special cases.

    Returns (stats, skipped): how many staged rows were already there,
    per table as the inserted count, skipped as the "already here" count
    the report asks for -- what lets a second restore of the same backup
    say "0 new rows, N skipped" instead of pretending nothing was
    attempted.
    """
    stats = {}
    skipped = 0
    cur = conn.cursor()
    try:
        cur.execute("SET session_replication_role = replica")
        resolved = []
        for table, columns, data in parsed:
            if not data.strip():
                continue
            real = _real_table(conn, table)
            if real is None:
                # The manifest names a table this schema does not have. v3
                # renamed some of hub's tables, so anything unmatched here is
                # not an error to surface but a fact to report.
                stats[table] = -1
                _log(f"  · tabla ausente en este esquema: {table}")
                continue
            resolved.append((table, real, columns, data))
        # Parents before children so the maps are complete before any child
        # FK is rewritten, whatever order the archive shipped the sections.
        resolved.sort(key=lambda item: _TREE_ORDER.get(item[1], 99))
        # One remap map per table the per-row path touches -- not just the
        # natural-key tree. pdfs/images/quiz_* are also restored row by row
        # (to rewrite their tree FKs through the SAME archive-id -> new-id
        # map), so they need an entry too. Initialising only the tree made
        # maps["pdfs"] miss, and the KeyError raised at the write site was
        # swallowed by this loop's per-table savepoint: the table was
        # reported "failed" while its rows -- and therefore the served
        # media -- silently never came back. Deriving the keys from
        # _PER_ROW_TABLES (the exact set the loop iterates) keeps them from
        # drifting apart again.
        maps = {t: {} for t in _PER_ROW_TABLES}
        for table, real, columns, data in resolved:
            cur.execute("SAVEPOINT one_table")
            try:
                if real in _PER_ROW_TABLES:
                    inserted, dupes = _restore_rows_per_row(
                        cur, real, columns, data, user_id, maps)
                    skipped += dupes
                else:
                    cur.execute(f'CREATE TEMP TABLE _stage (LIKE "{real}")')
                    # The column list is mandatory, not decorative. `COPY
                    # _stage FROM STDIN` with no list reads the staging table
                    # in ITS physical order, while the payload arrives in the
                    # order the archive's own header declares. Those two
                    # orders differ in v3 -- topics carries notes/status
                    # after order_index, so a bare COPY slid `pending` into
                    # order_index and died with `invalid input syntax for
                    # type integer`. Naming the columns on the COPY makes the
                    # mapping by name.
                    stage_cols = ", ".join(f'"{c}"' for c in columns)
                    cur.copy_expert(f"COPY _stage ({stage_cols}) FROM STDIN",
                                    io.StringIO(data))
                    # What the archive offered, before ON CONFLICT gets its
                    # hands on it. The difference against rowcount below is
                    # the "already here" count the report asks for. The row
                    # shape is not fixed (dict factory on both engines, a
                    # tuple on a bare cursor), so both are read.
                    cur.execute("SELECT count(*) AS n FROM _stage")
                    staged_row = cur.fetchone()
                    if staged_row is None:
                        staged = 0
                    elif isinstance(staged_row, dict):
                        staged = int(staged_row.get("n") or 0)
                    else:
                        staged = int(staged_row[0])
                    if "user_id" in columns:
                        # The archive's own owner is discarded, not preserved
                        # and not inherited: the row is claimed for whoever
                        # is restoring. The target list carries user_id a
                        # second time on purpose, and the source supplies it
                        # from the parameter rather than from the staged row.
                        # user_id must be dropped from the target list before
                        # it is re-added: listing it twice is a
                        # duplicate-column error from Postgres. Both lists
                        # are built from `columns` in one pass.
                        keep = [c for c in columns if c != "user_id"]
                        insert_cols = ", ".join(
                            f'"{c}"' for c in keep + ["user_id"])
                        source = ", ".join([f'"{c}"' for c in keep]
                                           + ["%s::int"])
                        cur.execute(
                            f'INSERT INTO "{real}" ({insert_cols}) '
                            f"SELECT {source} FROM _stage "
                            f"ON CONFLICT DO NOTHING",
                            (user_id,))
                    else:
                        cols = ", ".join(f'"{c}"' for c in columns)
                        cur.execute(
                            f'INSERT INTO "{real}" ({cols}) '
                            f"SELECT {cols} FROM _stage "
                            f"ON CONFLICT DO NOTHING")
                    inserted = max(0, cur.rowcount or 0)
                    skipped += max(0, staged - inserted)
                stats[table] = inserted
            except Exception as e:
                # undo ONLY this table. conn.rollback() would discard every
                # table already imported in this transaction.
                cur.execute("ROLLBACK TO SAVEPOINT one_table")
                stats[table] = -1
                _log(f"  ✗ tabla {table} no importada: {e}")
            finally:
                cur.execute("DROP TABLE IF EXISTS _stage")
        cur.execute("SET session_replication_role = DEFAULT")
    finally:
        cur.close()
    # Without this the whole import is rolled back when the connection
    # closes: the caller would be told "restored" and lose every row.
    conn.commit()
    return stats, skipped


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
    """Copy files/ from the archive into each category's own directory.

    The member path is ``files/<category>/<name>`` with ``/`` separators on
    every OS (the zip spec), so an archive made on macOS restores on
    Windows and the other way round; backslashes are tolerated for
    hand-made archives. The destination is storage_paths.category_dir --
    the same function the export resolved each source from and the routes
    resolve theirs -- which is what makes "written" mean "visible in the
    app" on a machine where the five categories live in five different
    roots instead of one. Nothing is ever overwritten.
    """
    written = 0
    for member in names:
        parts = member.replace("\\", "/").rstrip("/").split("/")
        if len(parts) < 3 or parts[0] != "files":
            continue
        category = parts[1]
        if category not in FILE_CATEGORIES:
            continue
        base = os.path.abspath(category_dir(category))
        dest = os.path.abspath(os.path.join(base, *parts[2:]))
        if not dest.startswith(base + os.sep):
            continue          # zip-slip, re-checked against THIS category
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if os.path.exists(dest):      # never clobber what is already there
            continue
        with zf.open(member) as src, open(dest, "wb") as out:
            out.write(src.read())
        written += 1
    return written


class _RestoreError(str):
    """An error message that remembers the HTTP status it deserves.

    ``_run_restore`` reports failure as a plain string -- its contract is
    ``(report | None, error | None)`` -- while the single-POST route has
    to keep answering 400/403/500 exactly as it always did. A str
    subclass carries the status without changing that shape: everywhere
    else (job store, browser alert) it is still just the message.
    """
    status = 500

    def __new__(cls, message: str, status: int = 400):
        obj = super().__new__(cls, message)
        obj.status = status
        return obj


def _run_restore(zip_path, selection=None, user_id: int = 1):
    """Run the additive restore over an already-stored archive.

    This is the whole body that used to live inline in ``restore_mine``:
    open the zip, validate manifest/owner/paths, import the rows and
    write the files, then build the report. ``zip_path`` is a filesystem
    path (chunked job) or a file-like object (the single-POST route,
    which keeps its historic in-memory upload). ``selection`` is a raw
    dict, a Selection, or None for the full import.

    Returns ``(report, None)`` on success or ``(None, error)`` on
    failure, where error is a str carrying the status the caller should
    answer with (see _RestoreError).
    """
    started = time.monotonic()
    try:
        zf = zipfile.ZipFile(zip_path)
        names = zf.namelist()
        if "user_data.sql" not in names:
            return None, _RestoreError(
                "Not a personal backup (missing user_data.sql)")

        try:
            manifest = _read_manifest(zf)
        except ValueError as e:
            return None, _RestoreError(str(e))

        owner = manifest.get("user", {}).get("id")
        # v3 ids are ints, so the manifest stores an int and comparing it
        # against a stringified one would reject the user's own backup.
        if owner is not None and owner != user_id:
            return None, _RestoreError(
                "This backup belongs to a different user", 403)

        if _has_unsafe_member(names):
            return None, _RestoreError(
                "Invalid backup: unsafe path in archive")

        if isinstance(selection, Selection):
            sel = selection
        elif isinstance(selection, dict):
            sel = Selection(selection)
        else:
            sel = None
        parsed = _parse_data_sql(zf.read("user_data.sql").decode("utf-8"))
        parsed, names = _safe_apply_selection(parsed, names, sel, manifest)

        conn = db.get_connection()
        try:
            stats, skipped = _import_tables(conn, parsed, user_id)
        finally:
            conn.close()

        written = _write_missing_files(zf, names)
        rows = sum(n for n in stats.values() if n > 0)
        failed = [t for t, n in stats.items() if n < 0]

        _log(f"✅ restore personal: usuario {user_id} -> {rows} filas, "
             f"{skipped} ya existentes, {written} ficheros en "
             f"{time.monotonic() - started:.1f}s")
        return {"ok": True, "rows_inserted": stats, "rows_total": rows,
                "files_written": written, "failed_tables": failed,
                "skipped": skipped, "imported": _section_counts(stats),
                "partial": bool(manifest.get("partial")),
                "note": "Existing rows and files were left untouched."}, None
    except zipfile.BadZipFile:
        return None, _RestoreError("Invalid or corrupt zip file")
    except Exception as e:
        _log(f"✗ restore personal: {e}")
        return None, _RestoreError(f"Restore failed: {e}", 500)


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

    stream = upload.stream
    stream.seek(0)
    report, err = _run_restore(stream, _selection_from_request(), user_id)
    if err is not None:
        return jsonify(error=str(err)), getattr(err, "status", 500)
    return jsonify(report)


# ═══════════════════════════════════════════════════════════════════
# Chunked restore jobs — a 1.3 GB multipart POST dies mid-flight in the
# browser ("Failed to fetch"), so the client uploads the zip in pieces,
# the server reassembles them and the import runs in a background
# thread. Same additive restore, same report, just survivable.
# ═══════════════════════════════════════════════════════════════════
#
# One local user, one server process: an in-memory dict guarded by a
# lock is enough. Parts live under ``<data_dir>/restore-jobs/<job_id>/``
# as ``part-{index:06d}`` and are deleted as the zip is reassembled.
# After a job reaches done/error the directory is simply left in place;
# a cleanup after ~1h is fine to add later, no timer is registered here
# on purpose.

_RESTORE_JOBS: dict = {}
_RESTORE_JOBS_LOCK = threading.Lock()


def _jobs_root() -> str:
    """Where chunked jobs assemble. Re-resolved per call on purpose:
    the module-level DATA_DIR is frozen at import, while engine.data_dir()
    re-reads the environment (test fixtures and launchers that export
    STUDYFLOW_DATA_DIR after the import must still land somewhere
    writable and private to the run)."""
    base = os.environ.get("DATA_DIR") or str(_engine_data_dir())
    return os.path.join(base, "restore-jobs")


@bp.route("/backup/mine/restore/start", methods=["POST"])
@token_required
def restore_start(user_id):
    """Open a chunked restore job: 202 + job_id, state=receiving."""
    if not isinstance(user_id, int):
        return jsonify(error="Invalid user identity"), 400
    filename = (request.form.get("filename") or "").strip()
    if not filename:
        return jsonify(error="filename is required"), 400
    try:
        total_chunks = int(request.form.get("total_chunks", ""))
    except (TypeError, ValueError):
        total_chunks = -1
    if not 1 <= total_chunks <= 100000:
        return jsonify(error="total_chunks must be an integer in 1..100000"), 400

    job_id = uuid.uuid4().hex
    job_dir = os.path.join(_jobs_root(), job_id)
    os.makedirs(job_dir, exist_ok=True)
    job = {"state": "receiving", "filename": filename,
           "total_chunks": total_chunks, "received": set(), "path": job_dir,
           "selection": _raw_selection(), "result": None, "error": None,
           "started": time.time(), "user_id": user_id}
    with _RESTORE_JOBS_LOCK:
        _RESTORE_JOBS[job_id] = job
    return jsonify(job_id=job_id), 202


@bp.route("/backup/mine/restore/chunk", methods=["POST"])
@token_required
def restore_chunk(user_id):
    """Receive one slice of the zip: part-{index:06d} inside the job dir."""
    job_id = request.form.get("job_id") or ""
    with _RESTORE_JOBS_LOCK:
        job = _RESTORE_JOBS.get(job_id)
    if job is None or job["state"] != "receiving":
        return jsonify(error="Unknown job or not receiving"), 409
    try:
        index = int(request.form.get("index", ""))
    except (TypeError, ValueError):
        index = -1
    if not 0 <= index < job["total_chunks"]:
        return jsonify(error="index out of range"), 400
    if "file" not in request.files:
        return jsonify(error="No chunk uploaded"), 400

    part = os.path.join(job["path"], f"part-{index:06d}")
    with open(part, "wb") as out:
        shutil.copyfileobj(request.files["file"].stream, out)
    with _RESTORE_JOBS_LOCK:
        job["received"].add(index)
    return jsonify(ok=True), 200


@bp.route("/backup/mine/restore/finish", methods=["POST"])
@token_required
def restore_finish(user_id):
    """Reassemble the parts in order, then restore in the background."""
    job_id = request.form.get("job_id") or ""
    with _RESTORE_JOBS_LOCK:
        job = _RESTORE_JOBS.get(job_id)
        if job is None or job["state"] != "receiving":
            return jsonify(error="Unknown job or not receiving"), 409
        missing = [i for i in range(job["total_chunks"])
                   if i not in job["received"]]
    if missing:
        return jsonify(error=f"{len(missing)} chunk(s) missing",
                       missing=missing[:50]), 400

    # Stream part by part so a 1.3 GB zip is never held in memory twice;
    # each part is deleted as soon as it has been copied.
    zip_path = os.path.join(job["path"], "backup.zip")
    try:
        with open(zip_path, "wb") as out:
            for i in range(job["total_chunks"]):
                part = os.path.join(job["path"], f"part-{i:06d}")
                with open(part, "rb") as src:
                    shutil.copyfileobj(src, out)
                try:
                    os.remove(part)
                except OSError:
                    pass
    except OSError as e:
        return jsonify(error=f"Could not reassemble the backup: {e}"), 400

    with _RESTORE_JOBS_LOCK:
        job["state"] = "restoring"
    threading.Thread(target=_restore_job_worker, args=(job_id,),
                     daemon=True).start()
    return jsonify(started=True), 202


def _restore_job_worker(job_id: str) -> None:
    """Background half of /finish: run the shared restore on the zip."""
    with _RESTORE_JOBS_LOCK:
        job = _RESTORE_JOBS.get(job_id)
    if job is None:
        return
    try:
        report, err = _run_restore(os.path.join(job["path"], "backup.zip"),
                                   job["selection"], job.get("user_id", 1))
        with _RESTORE_JOBS_LOCK:
            if err is not None:
                job["state"] = "error"
                job["error"] = str(err)
            else:
                job["state"] = "done"
                job["result"] = {"rows_inserted": report["rows_inserted"],
                                 "skipped": report["skipped"],
                                 "files_written": report["files_written"],
                                 "partial": report["partial"]}
    except Exception as e:          # never leave the job hanging in "restoring"
        _log(f"✗ restore job {job_id}: {e}")
        with _RESTORE_JOBS_LOCK:
            job["state"] = "error"
            job["error"] = str(e)


@bp.route("/backup/mine/restore/status", methods=["GET"])
@token_required
def restore_status(user_id):
    """Poll the job: receiving → restoring → done | error."""
    job_id = request.args.get("job") or ""
    with _RESTORE_JOBS_LOCK:
        job = _RESTORE_JOBS.get(job_id)
        if job is None:
            return jsonify(error="Unknown job"), 404
        payload = {"state": job["state"]}
        if job["state"] == "receiving":
            payload["progress"] = round(
                len(job["received"]) / max(job["total_chunks"], 1), 3)
        elif job["state"] == "done" and job["result"]:
            payload.update(job["result"])
        elif job["state"] == "error":
            payload["error"] = job["error"]
    return jsonify(payload), 200


# ═══════════════════════════════════════════════════════════════════
# Inspect — what is inside the zip, before anything is written
# ═══════════════════════════════════════════════════════════════════

def _options_from_archive(zf, manifest, sections) -> dict:
    """The payload GET /backup/mine/options serves, built from the zip alone.

    The selector must show what a restore of THIS file would add before
    the user confirms anything, and the only honest source for that is
    the archive itself: rows from user_data.sql, file counts and bytes
    from the manifest, sizes of unattributed members from the zip's own
    central directory (only for members the archive actually carries --
    a full export ships none of them). The estimator is backup_options',
    so the numbers match the export dialog's formula to the byte.
    """
    def rows(table, cols):
        return _row_dicts(sections.get(table), cols)

    courses = [{"cid": r["id"], "ctitle": r["title"]}
               for r in rows("courses", ("id", "title"))]
    topics = [{"tid": r["id"], "ttitle": r["title"], "cid": r["course_id"]}
              for r in rows("topics", ("id", "title", "course_id"))]
    blocks = []
    for r in rows("blocks",
                  ("id", "topic_id", "course_id", "title", "content")):
        content = r.get("content") or ""
        title = r.get("title") or ""
        blocks.append({
            "bid": r["id"], "tid": r["topic_id"], "cid": r["course_id"],
            "content_len": len(content),
            "blabel": (title or content[:60])[:60],
        })
    tree = _assemble_tree(courses, topics, blocks)

    weeks = [{"week_id": r["week_id"]}
             for r in rows("weeks", ("week_id",))]
    days = [{"date": r["date"], "week_id": r["week_id"]}
            for r in rows("days", ("date", "week_id"))]
    sessions = [{"id": r["id"], "day_date": r["day_date"]}
                for r in rows("sessions", ("id", "day_date"))]
    agenda = _assemble_agenda(weeks, days, sessions)

    # Scope counts, archive-side: a section the archive does not carry is
    # skipped, exactly as _scope_rows skips a table this schema lacks.
    scopes = {}
    for scope, tables in SCOPE_TABLES.items():
        counts = {}
        for table in tables:
            section = _section(sections, table)
            if section is not None:
                counts[table] = _line_count(section[1])
        scopes[scope] = {"rows": sum(counts.values()), "tables": counts}

    files = manifest.get("files") or {}
    counts = files.get("counts") or {}
    sizes = files.get("bytes") or {}
    media = {cat: {"files": int(counts.get(cat, 0) or 0),
                   "bytes": int(sizes.get(cat, 0) or 0)}
             for cat in CATEGORY_OF.values()}
    unat = {"files": 0, "bytes": 0}
    members = set(zf.namelist())
    for rel in manifest.get("unattributed_files") or ():
        member = f"files/{rel}"
        if member in members:
            unat["files"] += 1
            try:
                unat["bytes"] += zf.getinfo(member).file_size
            except (KeyError, OSError):
                pass

    scope_rows = sum(s["rows"] for s in scopes.values())
    tree_bytes = tree["totals"]["content_bytes"]
    media_bytes = sum(m["bytes"] for m in media.values())
    scope_bytes = scope_rows * BYTES_PER_ROW_ESTIMATE
    return {
        "tree": tree,
        "agenda": agenda,
        "scopes": scopes,
        "media": media,
        "unattributed": unat,
        "estimate": {
            "tree_bytes": tree_bytes,
            "scope_bytes": scope_bytes,
            "media_bytes": media_bytes,
            "unattributed_bytes": unat["bytes"],
            "total_bytes": tree_bytes + scope_bytes + media_bytes,
        },
    }


@bp.route("/backup/mine/inspect", methods=["POST"])
@token_required
def inspect_mine(user_id):
    """Read the archive's manifest and options without importing anything.

    The tri-state tree, the byte estimate and the unattributed bucket all
    come from here, so the selector can offer a partial restore of what
    is genuinely inside the zip. Nothing is written to disk and no SQL
    touches the database: everything is derived from the upload itself.
    """
    if not isinstance(user_id, int):
        return jsonify(error="Invalid user identity"), 400
    if "file" not in request.files:
        return jsonify(error="No file uploaded"), 400

    upload = request.files["file"]
    if not upload.filename or not upload.filename.endswith(".zip"):
        return jsonify(error="Expected a .zip personal backup"), 400
    try:
        # Read straight from Flask's spooled upload stream: on a big
        # backup the file already lives on disk, so ZipFile can page it
        # in on demand instead of pulling the whole 1.3 GB into RAM.
        stream = upload.stream
        stream.seek(0)
        zf = zipfile.ZipFile(stream)
    except zipfile.BadZipFile:
        return jsonify(error="Invalid or corrupt zip file"), 400

    if "user_data.sql" not in zf.namelist():
        return jsonify(has_manifest=False,
                       manifest_error="missing user_data.sql"), 200
    try:
        manifest = _read_manifest(zf)
    except Exception as e:
        return jsonify(has_manifest=False, manifest_error=str(e)), 200

    payload = {"has_manifest": True, "manifest": manifest}
    try:
        parsed = _parse_data_sql(
            zf.read("user_data.sql").decode("utf-8", "replace"))
        sections = {t: (c, d) for t, c, d in parsed}
        payload["options"] = _options_from_archive(zf, manifest, sections)
    except Exception as e:
        # The manifest is good but the options could not be built: the
        # caller falls back to a blind full import, so say so plainly.
        payload["options_error"] = str(e) or type(e).__name__
    return jsonify(payload), 200
