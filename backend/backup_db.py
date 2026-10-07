# backend/backup_db.py
"""Adaptation layer between the ported hub backup modules and v3's database.

Three incompatibilities have to be absorbed here, once, instead of being
patched into six modules.

1. **Row shape.** The ported modules call `db.query_on_conn(conn, sql, params)`.
   v3's `database.query()` returns dicts and borrows a connection from its own
   pool; the ported code wants to run on a connection it was handed. Two
   accessors are provided rather than one: `query_on_conn` for tuples where the
   code indexes positionally, and `query_dicts_on_conn` where it reads columns
   by name. Positional indexing is kept where hub had it, because hub's
   queries were written against column order and rewriting them to dict lookups
   would be a second, riskier migration.

2. **Tables that do not exist here.** v3 has 30 tables in `public`, hub has 43.
   The gap is not an error to be hidden: a scope whose tables are all missing
   has to say so in the manifest, because "this scope is empty" and "this scope
   has no table in v3 yet" are different facts and only one of them is the user's
   data being empty. So declared tables are intersected with the real schema
   and the difference is reported, never silently swallowed.

3. **Integer keys versus hub's slugs.** Every tree table in v3 is keyed by
   INTEGER; hub's are readable slugs. An id that is legal for one and not the
   other is only a string at the selection layer, and only a crash further
   down, so `as_int_set` narrows ids where the column type is known.

Nothing in here decides what a scope contains -- that stays in
backup_selection.py. This module only reports what v3 can actually answer.
"""

# Cursor factories. Which set applies depends on the CONNECTION handed in,
# not on whether psycopg2 is importable: psycopg2 is a declared dependency
# and is normally installed even when the app runs on SQLite, so an
# ImportError guard would pass the Postgres factories to the stdlib driver.
# The connection type is the reliable signal.
try:
    from psycopg2.extensions import cursor as PlainCursor
    from psycopg2.extras import RealDictCursor
except ImportError:  # pragma: no cover - psycopg2 is a declared dependency
    PlainCursor = RealDictCursor = None

import sqlite_compat


def _factories_for(conn):
    """(plain, real_dict) cursor factories matching `conn`'s driver."""
    if isinstance(conn, sqlite_compat.CompatConnection):
        return sqlite_compat.PLAIN_CURSOR, sqlite_compat.REAL_DICT_CURSOR
    return PlainCursor, RealDictCursor

# hub table name -> v3 table name. Only renames belong here; a table that
# simply does not exist in v3 is handled by resolve_tables, not by an alias,
# because inventing an alias for a table with no counterpart would make the
# manifest claim a table was backed up when it never was.
TABLE_RENAMES = {
    # hub calls it `questions`; v3 names it for what it is.
    "questions": "quiz_questions",
}


def query_on_conn(conn, sql: str, params: tuple = None) -> list:
    """Run `sql` on `conn` and return rows as plain tuples.

    Named for the hub helper the ported modules already call, so the port
    stays a diff against hub rather than a rewrite.

    The row factory is pinned rather than inherited. v3 creates its pool with
    `cursor_factory=RealDictCursor`, so a bare `conn.cursor()` hands back
    RealDictRow -- and `RealDictRow[0]` is a KeyError, not a value. Passing
    `cursor_factory=None` does NOT undo it either: psycopg2 reads that as
    "keep the connection's factory". The plain factory has to be named
    explicitly.
    """
    plain, _ = _factories_for(conn)
    cur = conn.cursor(cursor_factory=plain)
    try:
        cur.execute(sql, params)
        return cur.fetchall()
    finally:
        cur.close()


def query_one_on_conn(conn, sql: str, params: tuple = None):
    """First row as a tuple, or None."""
    rows = query_on_conn(conn, sql, params)
    return rows[0] if rows else None


def query_dicts_on_conn(conn, sql: str, params: tuple = None) -> list:
    """Run `sql` and return rows as dicts keyed by column name.

    The ported code needs both shapes. The COPY/export path counts and
    concatenates positionally, because column order is what makes the dump
    replayable and it must not depend on a dict's key order. The file
    attribution layer reads single columns out of a wide row
    (`row["storage_path"]`) and would be unreadable positionally, so it gets
    these instead of being rewritten to `row[2]`.

    Two helpers rather than one clever hybrid on purpose: a row that is both
    indexable by position and by name is the kind of convenience that lets a
    column reorder slip through unnoticed.
    """
    _, real = _factories_for(conn)
    cur = conn.cursor(cursor_factory=real)
    try:
        cur.execute(sql, params)
        return cur.fetchall()
    finally:
        cur.close()


def available_tables(conn) -> set:
    """The tables v3 actually has, lowercased.

    Queried once per export and passed down, because the ported modules call
    this in several places and the schema does not change mid-request.
    """
    rows = query_on_conn(
        conn,
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public'",
    )
    return {r[0] for r in rows}


def real_name(table: str, available: set) -> str | None:
    """The name this table has in v3, or None when it has none.

    Renames are applied first, then the result is looked up. A rename whose
    target is also absent resolves to None like any other missing table --
    an alias to a non-existent table is still a missing table.
    """
    candidate = TABLE_RENAMES.get(table, table)
    return candidate if candidate in available else None


def resolve_tables(declared, available: set) -> tuple:
    """Split declared tables into (present, missing).

    `declared` keeps its original order because the export concatenates COPY
    blocks in it and table order is what makes the dump replayable.

    `missing` is returned so the caller can put it in the manifest. A scope
    whose tables are all missing still appears, with zero rows and the reason
    attached -- the selector needs to distinguish "you have no flashcards"
    from "v3 has no flashcards table".
    """
    present, missing = [], []
    for table in declared:
        actual = real_name(table, available)
        if actual is None:
            missing.append(table)
        else:
            present.append((table, actual))
    return present, missing


def as_int_set(values) -> set:
    """Keep only the values Postgres can compare against an integer column.

    v3 keys courses, topics, blocks, pdfs and images by INTEGER, while a v2
    backup made from hub carries those same ids as human-readable slugs --
    `general-BaseDeDatos  🗃️` is a real hub topic id. Selection accepts both
    without complaint, because at that layer an id is just a string.

    It stops mattering one step later. `q()` renders an IN list as SQL
    literals, and `id IN ('general-BaseDeDatos')` against an integer column
    is not an empty result, it is `invalid input syntax for type integer` --
    the whole export dies on a single stale id, with the backup half-written.

    So ids are narrowed to integers at the boundary, where the column type is
    known, instead of hoping the input is clean. Dropping the unusable ones is
    the intended outcome: they cannot match a row in this schema anyway.
    """
    out = set()
    for value in values or ():
        try:
            out.add(int(value))
        except (TypeError, ValueError):
            continue
    return out