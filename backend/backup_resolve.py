"""Turns a `Selection` into per-table WHERE fragments.

Written after reading the live schema, which changed the design twice:

  * `days` has NO foreign key to `weeks` — the link is a bare `week_id`
    text column. `sessions` has no `week_id` at all; it reaches a week only
    through `sessions.day_date -> days.date -> days.week_id`. That is two
    soft hops, and a predicate written as though sessions carried a week_id
    would silently ignore week selection while still looking correct.

  * `questions` and `cards` both carry `user_id`, so they are ordinary
    user_id tables, not child tables. `cards` even carries `course_id` and
    `topic_id` directly, which saves a join.

So instead of assembling SQL fragments that have to compose with two
different WHERE shapes (user_id tables and FK-child tables), this module
RESOLVES THE ID SETS FIRST and then emits a simple `IN (...)` fragment per
table. The closure logic then lives in one inspectable place, and the
options endpoint and the export share the exact same numbers — so the size
the user is shown is the size they get.
"""
import backup_db as db
from backend.backup_db import as_int_set
from backup_selection import (
    ALWAYS_TABLES, SCOPE_TABLES, TREE_TABLES, Selection, _clean_ids,
)


def sql_text(value: str) -> str:
    """Escape one value for use inside a single-quoted SQL literal.

    Do not try to whitelist these instead. This app's primary keys are
    human-readable slugs, not uuids — a real one is
    `general-BaseDeDatos  🗃️`, with spaces and an emoji — so a character
    whitelist silently dropped the user's actual ids and every selective
    backup exported nothing. A smoke test against the live database is
    what caught it; the unit tests had all used uuid-shaped ids.

    Escaping is the correct tool here. PostgreSQL doubles a single quote
    inside a literal, and with standard_conforming_strings on (the
    default since 9.1) a backslash is an ordinary character — it is
    escaped anyway, so the code is still correct on a server with the
    setting off. NUL cannot appear in a text column at all, so refusing
    it only rejects input Postgres could not have stored.
    """
    return "'" + str(value).replace("'", "''").replace("\\", "\\\\") + "'"


def lit(values) -> str:
    """Render ids as a quoted SQL literal list.

    An empty set becomes `(NULL)`, NOT the bare word FALSE. This is used
    in two places that look alike but are not: as a whole predicate
    (`WHERE x IN (NULL)`, fine) and spliced into a list (`id IN (NULL)`,
    also fine) — whereas `FALSE` is only valid in the first position.
    `id IN FALSE` is a syntax error, and shipping one meant every
    single-subject backup died on the first query.

    Values pass through `sql_text`, not a filter: see its docstring.
    """
    if not values:
        return "(NULL)"
    return "(" + ", ".join(sql_text(v) for v in sorted(values)) + ")"


def q(col: str, values) -> str:
    """`col IN (...)`, with the empty case delegated to `lit` so the two
    can never disagree about what an empty set means."""
    return f"{col} IN {lit(values)}"


# ═══════════════════════════════════════════════════════════════════
# Tree closure
# ═══════════════════════════════════════════════════════════════════

def resolve_tree(conn, sel, user_id) -> dict:
    """Course/topic/block ids the selection covers, parents included.

    Returns {"courses": set, "topics": set, "blocks": set}. A block
    selected on its own still drags in its topic and course, because a
    topic whose course is missing does not restore into a usable tree —
    which is not hypothetical here: this account has 116 such topics.
    """
    if sel.is_everything:
        # Rows by name. _pivot_tree indexes with r[ck], where ck is a column
        # name; v3's connections carry a dict row factory, so a tuple here
        # raises on a string subscript. hub had no such factory and its
        # positional-style access was fine there.
        rows = db.query_dicts_on_conn(conn, """
            SELECT co.id AS cid, t.id AS tid, b.id AS bid
            FROM courses co
            LEFT JOIN topics t ON t.course_id = co.id AND t.user_id = co.user_id
            LEFT JOIN blocks b ON b.user_id = co.user_id
                 AND (b.course_id = co.id OR b.topic_id = t.id)
            WHERE co.user_id = %s
        """, (user_id,))
        return _pivot_tree(rows, "cid", "tid", "bid")
    return _tree_from_picks(conn, sel, user_id)


def _tree_from_picks(conn, sel, user_id) -> dict:
    """Expand the ticked ids downwards, then pull parents back up.

    Every id list goes through as_int_set before it reaches `= ANY(%s)`.
    Selection keeps ids as strings, and psycopg2 adapts a list of strings to
    `ARRAY['105']::text[]`, against which `integer = ANY(...)` is simply not an
    operator -- the error is `UndefinedFunction`, raised while resolving the
    tree, long before any file is looked at. Narrowing to ints here makes the
    parameter adapt as an integer array and keeps a stale slug from a v2
    backup harmless instead of fatal.
    """
    out = {"courses": set(), "topics": set(), "blocks": set()}

    if sel.course_ids:
        out["courses"] |= _fetch_ids(conn, """
            SELECT id FROM courses WHERE user_id = %s AND id = ANY(%s)
        """, (user_id, sorted(as_int_set(sel.course_ids))), "id")

    if sel.topic_ids:
        rows = db.query_dicts_on_conn(conn, """
            SELECT t.id AS tid, t.course_id AS cid
            FROM topics t WHERE t.user_id = %s AND t.id = ANY(%s)
        """, (user_id, sorted(as_int_set(sel.topic_ids))))
        out["topics"] |= {r["tid"] for r in rows}
        out["courses"] |= {r["cid"] for r in rows if r["cid"]}

    if sel.block_ids:
        rows = db.query_dicts_on_conn(conn, """
            SELECT b.id AS bid, b.topic_id AS tid, b.course_id AS cid
            FROM blocks b WHERE b.user_id = %s AND b.id = ANY(%s)
        """, (user_id, sorted(as_int_set(sel.block_ids))))
        out["blocks"] |= {r["bid"] for r in rows}
        out["topics"] |= {r["tid"] for r in rows if r["tid"]}
        out["courses"] |= {r["cid"] for r in rows if r["cid"]}

    _pull_children(conn, out, user_id, sel)
    _pull_parents(conn, out, user_id)
    return out


def _pull_children(conn, out, user_id, sel=None) -> None:
    """A ticked course reaches its topics and their blocks."""
    c, t = out["courses"], out["topics"]
    if c:
        t |= _fetch_ids(conn, """
            SELECT t.id AS tid FROM topics t
            WHERE t.user_id = %s AND t.course_id = ANY(%s)
        """, (user_id, sorted(c)), "tid")
    if c or t:
        out["blocks"] |= _fetch_ids(conn, """
            SELECT b.id AS bid FROM blocks b
            WHERE b.user_id = %s AND (b.course_id = ANY(%s) OR b.topic_id = ANY(%s))
        """, (user_id, sorted(c), sorted(t)), "bid")
    if not sel.topic_ids and not sel.block_ids and _covers_every_course(
            conn, sel, user_id):
        # The picker sends every course id when "all" is ticked, so "no
        # restriction" is not detectable from an empty list -- it has to be
        # measured against what the user actually owns. Only then does a block
        # attached to no course and no topic count as part of "everything".
        # Left out, blocks 19/20 ("Leccion 1/2") vanished from a full backup
        # while the selector was still advertising them as loose blocks that
        # could not be ticked anywhere. Unrestricted-only on purpose:
        # exporting course 105 must not smuggle in unrelated orphans.
        out["blocks"] |= _fetch_ids(conn, """
            SELECT b.id AS bid FROM blocks b
            WHERE b.user_id = %s AND b.course_id IS NULL AND b.topic_id IS NULL
        """, (user_id,), "bid")


def _covers_every_course(conn, sel, user_id) -> bool:
    """True when the selection names every course the user owns.

    The tree sends concrete ids rather than a wildcard, so "all ticked" and
    "nothing ticked" are both represented by id lists and only the comparison
    with reality tells them apart.
    """
    picked = as_int_set(sel.course_ids)
    owned = _fetch_ids(conn, """
        SELECT id AS cid FROM courses WHERE user_id = %s
    """, (user_id,), "cid")
    return bool(owned) and owned <= picked


def _pull_parents(conn, out, user_id) -> None:
    """A ticked topic or block brings the course it hangs from."""
    t, b = out["topics"], out["blocks"]
    if b:
        rows = db.query_dicts_on_conn(conn, """
            SELECT b.topic_id AS tid, b.course_id AS cid FROM blocks b
            WHERE b.user_id = %s AND b.id = ANY(%s)
        """, (user_id, sorted(b)))
        t |= {r["tid"] for r in rows if r["tid"]}
        out["courses"] |= {r["cid"] for r in rows if r["cid"]}
    if t:
        out["courses"] |= _fetch_ids(conn, """
            SELECT DISTINCT t.course_id AS cid FROM topics t
            WHERE t.user_id = %s AND t.id = ANY(%s) AND t.course_id IS NOT NULL
        """, (user_id, sorted(t)), "cid")


def _pivot_tree(rows, ck, tk, bk) -> dict:
    out = {"courses": set(), "topics": set(), "blocks": set()}
    for r in rows:
        if r[ck]:
            out["courses"].add(r[ck])
        if r.get(tk):
            out["topics"].add(r[tk])
        if r.get(bk):
            out["blocks"].add(r[bk])
    return out


def _fetch_ids(conn, sql, params, col) -> set:
    # Name-keyed, not positional: `col` is a column name, and hub's tuple
    # cursor cannot answer a string index. Every caller wants the same
    # one-column set, so the shape is fixed here rather than at nine call
    # sites.
    return {r[col] for r in db.query_dicts_on_conn(conn, sql, params)}


# ═══════════════════════════════════════════════════════════════════
# Agenda closure — two soft hops, remember that
# ═══════════════════════════════════════════════════════════════════

def resolve_agenda(conn, sel, user_id) -> dict:
    """Week/day/session ids covered, given the two-hop chain."""
    if sel.is_everything:
        weeks = _fetch_ids(conn, "SELECT week_id FROM weeks WHERE user_id = %s",
                           (user_id,), "week_id")
        days = _fetch_ids(conn, "SELECT date FROM days WHERE user_id = %s",
                          (user_id,), "date")
        sessions = _fetch_ids(conn, "SELECT id FROM sessions WHERE user_id = %s",
                              (user_id,), "id")
        return {"weeks": weeks, "days": days, "sessions": sessions}

    weeks, days, sessions = set(), set(), set()

    if sel.week_ids:
        weeks |= _fetch_ids(conn, """
            SELECT week_id FROM weeks
            WHERE user_id = %s AND week_id = ANY(%s)
        """, (user_id, list(sel.week_ids)), "week_id")

    if sel.day_ids:
        rows = db.query_dicts_on_conn(conn, """
            SELECT date, week_id FROM days
            WHERE user_id = %s AND date = ANY(%s)
        """, (user_id, list(sel.day_ids)))
        days |= {r["date"] for r in rows}
        weeks |= {r["week_id"] for r in rows if r["week_id"]}

    if sel.session_ids:
        rows = db.query_dicts_on_conn(conn, """
            SELECT s.id AS sid, s.day_date AS did, d.week_id AS wid
            FROM sessions s
            LEFT JOIN days d ON d.date = s.day_date AND d.user_id = s.user_id
            WHERE s.user_id = %s AND s.id = ANY(%s)
        """, (user_id, list(sel.session_ids)))
        sessions |= {r["sid"] for r in rows}
        days |= {r["did"] for r in rows if r["did"]}
        weeks |= {r["wid"] for r in rows if r["wid"]}

    if weeks:
        rows = db.query_dicts_on_conn(conn, """
            SELECT date FROM days WHERE user_id = %s AND week_id = ANY(%s)
        """, (user_id, list(weeks)))
        days |= {r["date"] for r in rows}
    if days:
        rows = db.query_dicts_on_conn(conn, """
            SELECT id FROM sessions WHERE user_id = %s AND day_date = ANY(%s)
        """, (user_id, list(days)))
        sessions |= {r["id"] for r in rows}
    return {"weeks": weeks, "days": days, "sessions": sessions}


# ═══════════════════════════════════════════════════════════════════
# The one function the export asks
# ═══════════════════════════════════════════════════════════════════

def resolve_scope(conn, sel: Selection, user_id: str) -> dict:
    """{table: where_fragment} for every table the export walks.

    `""` means in scope with no extra filter. `"FALSE"` means out of
    scope and must not ship. A real predicate narrows by resolved ids.
    """
    if sel.is_everything:
        return {}

    tree = resolve_tree(conn, sel, user_id)
    agenda = resolve_agenda(conn, sel, user_id) if sel.scope_on("agenda") \
        else {"weeks": set(), "days": set(), "sessions": set()}

    out = {}
    for table in TREE_TABLES:
        out[table] = _tree_fragment(table, tree)
    for table in ("weeks", "days", "sessions"):
        col = {"weeks": "week_id", "days": "date", "sessions": "id"}[table]
        out[table] = q(col, agenda[table]) if sel.scope_on("agenda") else "FALSE"
    # custom_categories is a flat 7-row per-user table, not week-scoped;
    # it rides along with agenda because the sessions reference it by key.
    out["custom_categories"] = "" if sel.scope_on("agenda") else "FALSE"

    for scope, tables in SCOPE_TABLES.items():
        for table in tables:
            if table in out:
                continue
            out[table] = "" if sel.scope_on(scope) else "FALSE"

    for table in ALWAYS_TABLES:
        out.setdefault(table, "")

    out["ai_tasks"] = _ai_tasks_fragment(sel, tree)
    # Media tables are scoped like the tree, but not by membership of it.
    out["pdfs"] = _resource_fragment("pdfs", sel, tree)
    out["images"] = _resource_fragment("images", sel, tree)
    # Annotations have no course of their own; they follow their PDFs.
    out["pdf_annotations"] = _annotation_fragment(sel, tree)
    return out


def _tree_fragment(table: str, tree: dict) -> str:
    """Filter fragment per tree table.

    Watch the key column. `questions` and `cards` each have their OWN `id`
    and reach a block through `block_id` — filtering them on `id` against
    the block set would match nothing at all and quietly export zero
    questions. `cards` additionally carries `topic_id` and `course_id`, so
    it needs no join.
    """
    c, t, b = (as_int_set(tree[k]) for k in ("courses", "topics", "blocks"))
    if table == "courses":
        return q("id", c)
    if table == "topics":
        return f"({q('id', t)} OR {q('course_id', c)})"
    if table == "blocks":
        return f"({q('id', b)} OR {q('topic_id', t)} OR {q('course_id', c)})"
    if table == "cards":
        return (f"({q('block_id', b)} OR {q('topic_id', t)} "
                f"OR {q('course_id', c)})")
    if table == "questions":
        return q("block_id", b)
    if table in ("quiz_results", "quiz_errors"):
        # Same three optional keys, so the same three-way disjunction. Matching
        # on any one of them keeps a result whose block was deleted but whose
        # course is still in the archive, and an unattributed answer
        # (all three NULL) is excluded unless the tree itself is unbounded.
        return (f"({q('block_id', b)} OR {q('topic_id', t)} "
                f"OR {q('course_id', c)})")
    return "FALSE"


def _pdf_predicate(c, t, b) -> str:
    """Predicate over the `pdfs` table selecting the PDFs inside this tree.

    Shared with _annotation_fragment so the two cannot drift. If they were
    written separately, a PDF would eventually be exported without its
    annotations or the reverse, and since both tables restore cleanly that
    failure would never announce itself -- it would just quietly cost the user
    their highlighting.
    """
    in_tree = (f"({q('topic_id', t)} OR {q('course_id', c)} OR {q('id', b)})")
    # NULLIF guards the cast: a url that is not /api/pdf/<n> yields NULL rather
    # than an invalid-input-syntax error that would fail the whole export. A
    # row with an empty url exists in this data.
    by_block = (
        "SELECT NULLIF(substring(url from '/api/pdf/([0-9]+)'), '')::int "
        f"FROM blocks WHERE type = 'pdf-ref' AND {in_tree}"
    )
    return (f"({q('course_id', c)} OR {q('topic_id', t)} "
            f"OR id IN ({by_block}))")


def _annotation_fragment(sel, tree: dict) -> str:
    """Scope fragment for pdf_annotations.

    An annotation has no course or topic of its own -- it is (user_id, pdf_id,
    page, data) -- so the only honest scope is the PDFs it annotates. Asking
    for one subject's highlights and getting another subject's would be worse
    than getting none, because the export would be wrong rather than empty.
    """
    if not sel.has_tree_pick:
        return ""
    c, t, b = (as_int_set(tree[k]) for k in ("courses", "topics", "blocks"))
    if not (c or t or b):
        return "FALSE"
    base = f"pdf_id IN (SELECT id FROM pdfs WHERE {_pdf_predicate(c, t, b)})"
    if sel.unattributed:
        return f"({base} OR pdf_id IS NULL)"
    return f"({base})"


def _resource_fragment(table: str, sel, tree: dict) -> str:
    """Scope fragment for a media table keyed by course and topic.

    `pdfs` and `images` are not tree nodes, so they have no entry in
    TREE_TABLES, and that used to mean they had no fragment at all: an absent
    key reads as "no extra filter", which is how ticking ONE subject while
    exporting media would have shipped every file the user owns. The failure
    is invisible in the artifact too -- the archive opens fine, it is just far
    bigger than what was asked for.

    Both tables carry course_id and topic_id, and both are also reachable
    from `blocks`, which is not redundant. A PDF stored without a course is
    still displayed inside a topic, and answering from the pdfs row alone
    would call it unattributed while the user is looking straight at it in
    that topic. v3 records a PDF as a block of type `pdf-ref` whose url is
    `/api/pdf/<pdfs.id>`; an image is `blocks.image_id`.

    No user filter on the block subquery on purpose. It would need a second
    bound parameter in the middle of the fragment, and the fragment contract
    is one predicate with the caller's params already fixed. Filtering only by
    the selected topics cannot widen the result past the caller's own
    `user_id = %s` on the outer query, so the rows it adds are still rows this
    user owns.
    """
    if not sel.has_tree_pick:
        # Media is not being narrowed by a tree selection, so it follows the
        # category switches instead.
        return ""
    c, t, b = (as_int_set(tree[k]) for k in ("courses", "topics", "blocks"))
    if not (c or t or b):
        # A tree WAS ticked and resolved to nothing -- an id that no longer
        # exists, or belongs to another user. The fragment vocabulary is
        # "" for no filter and "FALSE" for match nothing, and the two are not
        # interchangeable: returning "" here makes the caller wrap it as
        # `AND ()`, which is a syntax error, and returning "FALSE" from the
        # no-tree-pick branch would silently drop every file.
        return "FALSE"
    in_tree = (f"({q('topic_id', t)} OR {q('course_id', c)} OR {q('id', b)})")
    if table == "pdfs":
        own = _pdf_predicate(c, t, b)
    else:
        by_block = ("SELECT image_id FROM blocks "
                    f"WHERE image_id IS NOT NULL AND {in_tree}")
        own = (f"({q('course_id', c)} OR {q('topic_id', t)} "
               f"OR id IN ({by_block}))")
    if sel.unattributed:
        return f"({own} OR (course_id IS NULL AND topic_id IS NULL))"
    return f"({own})"


def _ai_tasks_fragment(sel, tree) -> str:
    """AI tasks follow the selected topics.

    Without this, ticking one subject plus the agents_ai scope would drag
    in all 956 tasks and their audio — the exact thing the user asked to
    avoid.

    Two kinds of task are deliberately NOT pulled in by a tree selection:

      * `topic_id IS NULL` — ships only on the explicit `unattributed`
        opt-in, because that checkbox is the whole point of the bucket.
        Always including it here would quietly bypass the decision the
        user just made.

      * a topic_id that resolves to no course of this user's own — the 116
        orphan and cross-user topics. Their files are not referenced by any
        exported row, so they surface through the disk scan as
        unattributed instead, where they can be opted into on their merits.
    """
    if not sel.has_tree_pick:
        return ""
    in_tree = q("topic_id", tree["topics"])
    if sel.unattributed:
        return f"({in_tree} OR topic_id IS NULL)"
    return in_tree
