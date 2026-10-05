"""GET /api/backup/mine/options — the tree the selector modal is built from.

The export resolves a `Selection` against the live database and ships an
archive; this endpoint serves the SAME numbers before anything is
generated, so the size the modal shows is the size the user gets. The two
share `resolve_tree`/`resolve_agenda` semantics: a ticked course always
brings its topics and blocks, parents travel with children.

What is served:

  * `tree` — courses with topics and blocks, each with a block count and
    the byte size of `blocks.content`, the dominant text payload.
  * `tree.misc_blocks` — blocks whose topic is missing or belongs to no
    course of this user (pre-existing data faults); without this bucket
    those blocks could not be selected at all.
  * `agenda` — weeks with their day and session counts, plus the orphan
    days/sessions that have no week.
  * `scopes` — total rows per scope switch (habits, flashcards, jsp,
    agents_ai, preferences); agenda is shown through `agenda` instead,
    because it has its own week-level tree.
  * `media` — files and bytes per category reachable from the user's own
    rows, so the modal can estimate the dominant part of the archive.
  * `unattributed` — files on disk no row of any user can reach, with
    bytes; shipping them is the user's explicit opt-in.

Everything comes from the live schema the same way the export reads it;
there is no separate "options database" that could drift.
"""
import os

from flask import Blueprint, jsonify

import backup_db as bdb
import database as db
from backup_files import (
    CATEGORY_OF, _claimed_by_others, _collect_owned, _Collector,
    _scan_unclaimed,
)
from backup_selection import SCOPE_TABLES
from backup_user import CHILD_TABLE_SPECS
from routes.auth import token_required

bp = Blueprint("backup_options", __name__)

DATA_DIR = os.environ.get("DATA_DIR", "/data")

# A rough bytes-per-row for the scopes the tree does not cover. The exact
# weight of, say, `notices` varies, but the export is dominated by media
# and by blocks.content; a flat estimate keeps the modal honest without
# pretending to precision it does not have.
BYTES_PER_ROW_ESTIMATE = 512


# ═══════════════════════════════════════════════════════════════════
# Tree assembly (pure — unit-testable without a connection)
# ═══════════════════════════════════════════════════════════════════

def _assemble_tree(courses, topics, blocks) -> dict:
    """Build the course→topic→block tree from plain row dicts.

    `courses`/`topics`/`blocks` are lists of dicts with the exact keys the
    loader queries return. Each course and topic node carries its own
    `blocks` list (so individual blocks can be ticked) plus the aggregate
    counts. A block whose topic the user's tree does not know goes into
    `misc_blocks` so the selector can still reach it; a block with no topic
    hangs off its course directly.
    """
    by_course = {c["cid"] for c in courses}

    topics_by_course = {}
    for t in topics:
        topics_by_course.setdefault(t["cid"], []).append(t)

    tree = {
        "courses": [
            {
                "id": c["cid"],
                "title": c["ctitle"],
                "blocks": [],
                "topics": [],
            } for c in courses
        ],
        "misc_blocks": [],
        "totals": {"blocks": 0, "content_bytes": 0},
    }

    # Topic id -> node, and a map of course node so loose blocks land there.
    topic_index = {}
    course_index = {n["id"]: n for n in tree["courses"]}
    for course_node in tree["courses"]:
        for t in topics_by_course.get(course_node["id"], []):
            node = {
                "id": t["tid"],
                "title": t["ttitle"],
                "blocks": [],
            }
            course_node["topics"].append(node)
            topic_index[t["tid"]] = node

    def add(node, b):
        # str() on the fallback, because v3 ids are integers where hub's were
        # UUIDs. finalize() sorts with label.lower(), so a block with no title
        # and no content falls back to its id and crashes the whole endpoint
        # on an int. Coercing here keeps the sort key a string everywhere.
        node["blocks"].append({
            "id": b["bid"], "label": str(b.get("blabel") or b["bid"]),
            "content_bytes": b.get("content_len") or 0,
        })

    for b in blocks:
        tid, cid = b.get("tid"), b.get("cid")
        if tid and tid in topic_index:
            add(topic_index[tid], b)
        elif cid in course_index:
            add(course_index[cid], b)
        else:
            tree["misc_blocks"].append({
                "id": b["bid"], "topic_id": tid, "course_id": cid,
                "label": str(b.get("blabel") or b["bid"]),
                "content_bytes": b.get("content_len") or 0,
            })

    def finalize(node):
        node["blocks"].sort(key=lambda x: x["label"].lower())
        return {
            "id": node["id"],
            "title": node["title"],
            "blocks": len(node["blocks"]),
            "content_bytes": sum(x["content_bytes"] for x in node["blocks"]),
            "block_ids": [x["id"] for x in node["blocks"]],
            "block_sizes": {x["id"]: x["content_bytes"]
                            for x in node["blocks"]},
        }

    for course_node in tree["courses"]:
        course_node["topics"].sort(key=lambda n: n["title"].lower())
        course_node["topics"] = [finalize(t) for t in course_node["topics"]]
        loose_bytes = sum(
            x["content_bytes"] for x in course_node["blocks"])
        course_node["loose_bytes"] = loose_bytes
        course_node["blocks"] = [x["id"] for x in course_node["blocks"]]
    tree["courses"].sort(key=lambda n: n["title"].lower())
    tree["misc_blocks"].sort(key=lambda n: n["label"].lower())
    tree["totals"]["blocks"] = sum(
        len(n["blocks"]) + sum(len(t["block_ids"]) for t in n["topics"])
        for n in tree["courses"]) + len(tree["misc_blocks"])
    tree["totals"]["content_bytes"] = sum(
        sum(t["content_bytes"] for t in n["topics"]) + n["loose_bytes"]
        for n in tree["courses"])
    return tree


def _load_tree_rows(conn, user_id) -> tuple:
    """(courses, topics, blocks) row dicts for one user."""
    # Rows by name, not tuples. Every consumer below indexes with d["week_id"]
    # and friends, and hub's cursor returned dicts; switching to plain tuples
    # is what produced "tuple indices must be integers or slices, not str".
    courses = bdb.query_dicts_on_conn(conn, """
        SELECT id AS cid, title AS ctitle FROM courses
        WHERE user_id = %s ORDER BY title
    """, (user_id,))
    topics = bdb.query_dicts_on_conn(conn, """
        SELECT id AS tid, title AS ttitle, course_id AS cid FROM topics
        WHERE user_id = %s ORDER BY title
    """, (user_id,))
    blocks = bdb.query_dicts_on_conn(conn, """
        SELECT id AS bid, topic_id AS tid, course_id AS cid,
               length(content) AS content_len,
               left(coalesce(nullif(title, ''), left(content, 60)), 60) AS blabel
        FROM blocks WHERE user_id = %s
    """, (user_id,))
    return courses, topics, blocks


# ═══════════════════════════════════════════════════════════════════
# Agenda assembly (pure)
# ═══════════════════════════════════════════════════════════════════

def _assemble_agenda(weeks, days, sessions) -> dict:
    """Week/day/session tree with counts, plus orphan buckets.

    The `days` table links to a week through a bare `week_id` text column
    and `sessions` reaches a week only through `sessions.day_date ->
    days.date -> days.week_id` (the two-soft-hop shape documented in
    backup_resolve). Days with a missing/unknown week and sessions with a
    missing day must still be selectable, so they get their own buckets.
    """
    days_by_week = {}
    for d in days:
        days_by_week.setdefault(d.get("week_id"), []).append(d["date"])
    sessions_by_day = {}
    for s in sessions:
        sessions_by_day.setdefault(s.get("day_date"), []).append(s["id"])

    weeks_out = []
    for w in sorted(weeks, key=lambda x: x["week_id"]):
        dates = days_by_week.get(w["week_id"], [])
        weeks_out.append({
            "id": w["week_id"],
            "days": len(dates),
            "sessions": sum(len(sessions_by_day.get(d, [])) for d in dates),
        })

    known_days = {d["date"] for d in days}
    known_weeks = {w["week_id"] for w in weeks}
    orphan_days = [
        {"date": d["date"]}
        for d in days if d.get("week_id") not in known_weeks
    ]
    orphan_sessions = [
        {"id": s["id"], "day_date": s.get("day_date")}
        for s in sessions if s.get("day_date") not in known_days
    ]

    return {
        "weeks": weeks_out,
        "totals": {
            "weeks": len(weeks),
            "days": len(days),
            "sessions": len(sessions),
        },
        "orphan_days": orphan_days,
        "orphan_sessions": orphan_sessions,
    }


def _load_agenda_rows(conn, user_id) -> tuple:
    weeks = bdb.query_dicts_on_conn(conn, """
        SELECT week_id FROM weeks WHERE user_id = %s ORDER BY week_id
    """, (user_id,))
    days = bdb.query_dicts_on_conn(conn, """
        SELECT date, week_id FROM days WHERE user_id = %s ORDER BY date
    """, (user_id,))
    sessions = bdb.query_dicts_on_conn(conn, """
        SELECT id, day_date FROM sessions WHERE user_id = %s ORDER BY id
    """, (user_id,))
    return weeks, days, sessions


# ═══════════════════════════════════════════════════════════════════
# Scope rows and media sizes
# ═══════════════════════════════════════════════════════════════════

def _count_rows_in_table(conn, user_id, table) -> int:
    """Rows of `table` belonging to the user, child tables included."""
    links = {t: lk for t, lk in CHILD_TABLE_SPECS}.get(table)
    if links:
        cond = " OR ".join(
            f'"{col}" IN (SELECT id FROM "{parent}" WHERE user_id = %s)'
            for col, parent in links
        )
        args = (user_id,) * len(links)
        row = bdb.query_dicts_on_conn(
            conn, f'SELECT count(*) AS n FROM "{table}" WHERE {cond}',
            args)[0]
        return row["n"]
    row = bdb.query_dicts_on_conn(conn, f"""
        SELECT count(*) AS n FROM "{table}" WHERE user_id = %s
    """, (user_id,))[0]
    return row["n"]


def _scope_rows(conn, user_id) -> dict:
    """{scope_name: {rows: N, tables: {table: N}}} for every scope.

    Two things keep a table missing from this schema from taking the endpoint
    down with it.

    The name is resolved before it is queried, so an absent table is skipped
    rather than attempted and failed. And any query that does raise is
    followed by a rollback, because a failed statement leaves the connection's
    transaction aborted: without it every later query in this same function
    fails with InFailedSqlTransaction, and the counts for the tables that DO
    exist come back as zero. That is the worse failure, because it is
    invisible -- the response looks like a working selector for an account
    that happens to hold no flashcards.
    """
    out = {}
    available = bdb.available_tables(conn)
    for scope, tables in SCOPE_TABLES.items():
        counts = {}
        for table in tables:
            real = bdb.real_name(table, available)
            if real is None:
                continue
            try:
                counts[table] = _count_rows_in_table(conn, user_id, real)
            except Exception as e:
                conn.rollback()
                print(f"  · options: tabla {table} no consultada: {e}",
                      flush=True)
        out[scope] = {"rows": sum(counts.values()), "tables": counts}
    return out


def _media_summary(conn, user_id) -> tuple:
    """(media, unattributed) with file counts and total bytes.

    Reuses the same collectors the export runs, with no category filtered,
    so the numbers match what a full backup would actually ship. The
    `unattributed` bucket is the same subtraction the export performs
    (owned + foreign) and is reported always, before the checkbox.
    """
    col = _Collector()                       # wanted = everything
    # One call, not three. hub drove its collectors separately because its
    # media hung off blocks columns; v3 reaches every category through the
    # table that owns it, so _collect_owned walks all of them. Keeping the
    # three-call shape would have meant re-deriving which sources exist, and
    # the counts here would quietly stop matching what the export ships.
    _collect_owned(conn, user_id, col, None)

    media = {cat: {"files": 0, "bytes": 0} for cat in CATEGORY_OF.values()}
    for abs_path, _arc, cat in col.files:
        media[cat]["files"] += 1
        try:
            media[cat]["bytes"] += os.path.getsize(abs_path)
        except OSError:
            pass

    foreign = _claimed_by_others(conn, user_id)
    unat = _scan_unclaimed(col.owned, foreign)
    unat_bytes = 0
    for p in unat:
        try:
            unat_bytes += os.path.getsize(p)
        except OSError:
            pass

    return media, {"files": len(unat), "bytes": unat_bytes}


# ═══════════════════════════════════════════════════════════════════
# ENDPOINT
# ═══════════════════════════════════════════════════════════════════

@bp.route("/backup/mine/options", methods=["GET"])
@token_required
def backup_options(user_id):
    """Serve the selection tree for the backup selector modal."""
    if not isinstance(user_id, int):
        return jsonify(error="Invalid user identity"), 400

    conn = db.get_connection()
    try:
        tree = _assemble_tree(*_load_tree_rows(conn, user_id))
        agenda = _assemble_agenda(*_load_agenda_rows(conn, user_id))
        scopes = _scope_rows(conn, user_id)
        media, unattributed = _media_summary(conn, user_id)
    finally:
        conn.close()

    # Scope rows outside the tree: their byte weight is estimated flat; the
    # modal adds it to the tree content bytes and the media bytes.
    scope_rows = sum(s["rows"] for s in scopes.values())
    payload = {
        "tree": tree,
        "agenda": agenda,
        "scopes": scopes,
        "media": media,
        "unattributed": unattributed,
        "estimate": {
            "tree_bytes": tree["totals"]["content_bytes"],
            "scope_bytes": scope_rows * BYTES_PER_ROW_ESTIMATE,
            "media_bytes": sum(m["bytes"] for m in media.values()),
            "unattributed_bytes": unattributed["bytes"],
            "total_bytes": (
                tree["totals"]["content_bytes"]
                + scope_rows * BYTES_PER_ROW_ESTIMATE
                + sum(m["bytes"] for m in media.values())
            ),
        },
    }
    print(f"◈ options backup: usuario {user_id} — "
          f"{tree['totals']['blocks']} bloques, {len(tree['courses'])} cursos, "
          f"{media['audio']['files']} audio, "
          f"{media['pdfs']['files']} pdf, "
          f"{media['infographics']['files']} infograficas", flush=True)
    return jsonify(payload)