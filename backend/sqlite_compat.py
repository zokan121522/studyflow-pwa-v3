"""SQLite compatibility layer — Phase LF (local-first).

Lets StudyFlow run on a single local SQLite file instead of PostgreSQL,
without rewriting the 566 SQL statements in the 32 feature modules.

Design rule: **translate at the boundary, never at the call sites.**
Every query in the app funnels through database.py's helpers, so the
Postgres dialect is normalised here once and the feature modules keep
reading like they always have.

What is translated, and why each one is needed
---------------------------------------------
1. ``%s`` placeholders → ``?``
   psycopg2 uses ``%s``; the stdlib sqlite3 driver is qmark-only and
   raises ``OperationalError: near "%": syntax error``. Rewritten here so
   the ~539 call sites that pass ``%s`` keep working untouched.

2. ``NOW()`` → a registered SQLite function
   Registered rather than textually replaced so the 204 call sites keep
   their ``NOW()``. Returns tz-aware UTC ISO-8601, matching what
   psycopg2 handed out for ``TIMESTAMP WITH TIME ZONE``.

3. ``SERIAL`` / ``TIMESTAMP WITH TIME ZONE`` / ``JSONB`` in DDL
   SQLite has no SERIAL and no timezone-aware type. These become
   ``INTEGER PRIMARY KEY AUTOINCREMENT`` and ``TEXT`` respectively.

4. Row shape
   psycopg2's ``RealDictCursor`` yields dicts; sqlite3 yields tuples.
   ``dict_factory`` restores the dict rows every caller already expects.

Deliberately NOT ported
-----------------------
There is nothing to port. Measured over backend/: zero full-text search
(``to_tsvector``/``tsquery``), zero ``ILIKE``, zero array functions,
zero ``pg_trgm``, and no triggers, views or stored procedures. That is
the reason this file is small rather than a 2,000-line shim.

``RETURNING`` needs no work either: the ``RETURNING`` clause the 71
call sites use is supported natively from SQLite 3.35, and
``init_db()`` enforces that floor.

Rowcount caveat
---------------
sqlite3 sets ``cursor.rowcount`` to ``-1`` for ``SELECT``. The helpers
in database.py only read rowcount for INSERT/UPDATE/DELETE, where
sqlite3 reports it correctly, so the helpers pass through unchanged.
"""

from __future__ import annotations

import datetime
import re
import sqlite3
from typing import Any, Iterable, Optional, Sequence

# SQLite gained RETURNING in 3.35 (2021-03-12). The 71 RETURNING call
# sites depend on it. Anything older fails at the first insert, so this
# is checked once at init rather than surfacing as a mystery later.
MIN_SQLITE_VERSION = (3, 35, 0)

#: Postgres placeholder. Matches ``%s`` only — a bare ``%`` (LIKE
#: wildcard) and ``%(name)s`` (pyformat, unused here) are left alone so
#: a LIKE '%algo%' in a query is never mangled.
_PYFORMAT_RE = re.compile(r"%s")

#: Postgres named placeholder (``%(name)s``). Used by agenda_state.py for
#: its timer arithmetic, where a named parameter is clearer than a bare
#: ``%s`` repeated twice. Rewritten to the anonymous ``?`` form, with the
#: values re-ordered by translate_params so the pairing survives.
_PYNAME_RE = re.compile(r"%\((\w+)\)s")

#: ``EXTRACT(EPOCH FROM <a> - <b>)`` over timestamps, the only EXTRACT
#: shape in the app. Postgres subtracts two timestamps to get an
#: *interval* and reads its total seconds; SQLite has no interval type, so
#: the same number comes from julianday arithmetic scaled by 86400.
_EXTRACT_EPOCH_RE = re.compile(
    r"EXTRACT\s*\(\s*EPOCH\s+FROM\s+"
    r"CAST\s*\(\s*(?P<a>%\(\w+\)s|\?)\s+AS\s+timestamp\s*\)"
    r"\s*-\s*"
    r"CAST\s*\(\s*(?P<b>[\w.]+)\s+AS\s+timestamp\s*\)"
    r"\s*\)",
    re.IGNORECASE,
)


class SqliteCompatError(RuntimeError):
    """Raised when the runtime SQLite is too old for the local-first app."""


def utcnow_iso() -> str:
    """Current UTC time as ISO-8601 with offset.

    ISO-8601 with a ``T`` separator and an explicit ``+00:00`` offset is
    the one format that ``datetime.fromisoformat`` and JavaScript's
    ``new Date()`` both parse unambiguously, in any browser locale. That
    matters because the timestamps round-trip through the JSON API into
    the PWA.
    """
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# ─── DDL translation ──────────────────────────────────────────────────

# Applied to DDL only, never to DML: rewriting an INSERT would risk
# mangling a string literal that happens to contain one of these words.
# Word-boundary anchored so a column named ``serial_number`` is safe.
_DDL_REWRITES: tuple[tuple[re.Pattern[str], str], ...] = (
    # SERIAL PRIMARY KEY -> AUTOINCREMENT. The trailing " PRIMARY KEY"
    # is required for lastrowid/AUTOINCREMENT semantics, so the combined
    # form is rewritten before the bare one.
    (
        re.compile(r"\bSERIAL\s+PRIMARY\s+KEY\b", re.IGNORECASE),
        "INTEGER PRIMARY KEY AUTOINCREMENT",
    ),
    (re.compile(r"\bBIGSERIAL\b", re.IGNORECASE), "INTEGER"),
    (re.compile(r"\bSERIAL\b", re.IGNORECASE), "INTEGER"),
    # No timezone-aware type in SQLite. TEXT holds ISO-8601, which sorts
    # and compares correctly as a string — see the note on ORDER BY.
    (
        re.compile(r"\bTIMESTAMP\s+WITH\s+TIME\s+ZONE\b", re.IGNORECASE),
        "TEXT",
    ),
    (re.compile(r"\bTIMESTAMPTZ\b", re.IGNORECASE), "TEXT"),
    # JSONB as TEXT: the app already json.dumps()/json.loads() these
    # columns, and SQLite's JSON1 extension is optional in some builds.
    (re.compile(r"\bJSONB\b", re.IGNORECASE), "TEXT"),
    (re.compile(r"\bJSON\b", re.IGNORECASE), "TEXT"),
    # Postgres-only cast syntax that SQLite spells differently.
    (re.compile(r"\bUUID\b(?!\s*\))", re.IGNORECASE), "TEXT"),
    # SQLite forbids bare function calls in DEFAULT: the expression must
    # be wrapped in parentheses. Postgres happily allows DEFAULT NOW(),
    # and the schema uses it on nearly every created_at/updated_at
    # column, so this was the single most common DDL failure.
    #
    # The wrapped form is the documented SQLite rule for "an expression
    # may be used in place of a literal value if it is enclosed in
    # parentheses". strftime with an explicit ISO-8601 pattern is used
    # instead of CURRENT_TIMESTAMP because the keyword yields
    # 'YYYY-MM-DD HH:MM:SS' with a space separator, while the rest of
    # the app's timestamps are ISO-8601 with a 'T' — mixing the two in
    # the same column would break string ordering by created_at.
    (
        re.compile(r"\bDEFAULT\s+NOW\(\)", re.IGNORECASE),
        "DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
    ),
    (
        re.compile(r"\bDEFAULT\s+CURRENT_TIMESTAMP\b", re.IGNORECASE),
        "DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
    ),
)


def translate_ddl(ddl: str) -> str:
    """Rewrite Postgres DDL types into their SQLite equivalents.

    Only for CREATE TABLE / CREATE INDEX / ALTER TABLE / DROP TABLE.
    Passing a SELECT here would rewrite string literals.
    """
    out = ddl
    # DROP TABLE … CASCADE: no CASCADE in SQLite's DROP, and none needed
    # — it drops the table and ignores dangling references. The app writes
    # ``DROP TABLE IF EXISTS <name> CASCADE`` throughout its migrations.
    out = _DROP_CASCADE_RE.sub(r"\1", out)
    for pattern, replacement in _DDL_REWRITES:
        out = pattern.sub(replacement, out)
    return out


# ─── DML translation ──────────────────────────────────────────────────


#: Postgres catalogue view name → the temp view that emulates it.
#: SQLite identifiers cannot contain a dot, so the name is rewritten
#: rather than quoted.
_CATALOGUE_VIEW_MAP = (
    ("information_schema.columns", "information_schema_columns"),
)

#: Postgres ``DROP TABLE ... CASCADE`` — SQLite has no CASCADE there and
#: does not need one: it drops the table and ignores dangling references
#: unless foreign_keys enforcement is on. The app's own code writes
#: ``DROP TABLE IF EXISTS <name> CASCADE``, so the clause is stripped
#: rather than left to fail at runtime.
_DROP_CASCADE_RE = re.compile(
    r"(\bDROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?[\w.\"'`]+)\s+CASCADE\b",
    re.IGNORECASE,
)


def translate_sql(sql: str) -> str:
    """Normalise Postgres DML for the sqlite3 driver.

    Rewrites the placeholder style (both ``%s`` and ``%(name)s``), the one
    catalogue view the migration helpers query, and the
    timestamp-difference EPOCH extraction. Everything else (``ON
    CONFLICT``, ``RETURNING``, ``EXCLUDED``, window functions, CTEs) is
    already valid SQLite and must be left alone.
    """
    for postgres_name, sqlite_name in _CATALOGUE_VIEW_MAP:
        if postgres_name in sql:
            sql = sql.replace(postgres_name, sqlite_name)
    if "%(" in sql:
        sql = _PYNAME_RE.sub("?", sql)
    if "EXTRACT" in sql.upper():
        sql = _rewrite_extract_epoch(sql)
    if "%s" not in sql:
        return sql
    return _PYFORMAT_RE.sub("?", sql)


def _rewrite_extract_epoch(sql: str) -> str:
    """Rewrite a timestamp-difference EPOCH extraction to julianday math.

    ``EXTRACT(EPOCH FROM CAST(a AS timestamp) - CAST(b AS timestamp))``
    becomes ``((julianday(a) - julianday(b)) * 86400.0)``. SQLite's
    julianday() parses the ISO-8601 timestamps the app stores, and 86400
    converts the day difference into seconds.

    Only the two-operand difference is handled, because that is the only
    shape present. A bare ``EXTRACT(EPOCH FROM <single value>)`` is left
    untouched so it surfaces as a syntax error in review rather than
    silently returning NULL.
    """
    return _EXTRACT_EPOCH_RE.sub(
        lambda m: f"((julianday({m.group('a')}) - julianday({m.group('b')})) * 86400.0)",
        sql,
    )


def translate_params(sql: str, params: Optional[Sequence]) -> tuple:
    """Return params in the order the translated SQL expects them.

    Rewrites only when the query used named placeholders, which is the one
    case where converting to ``?`` changes the positional order.
    """
    if params is None:
        return ()
    if "%(" in sql and isinstance(params, dict):
        # The names in the order the original SQL used them, so the
        # rewritten positional markers line up with the values.
        names = _PYNAME_RE.findall(sql)
        return tuple(params[name] for name in names) if names else tuple(params.values())
    if isinstance(params, (list, tuple)):
        return tuple(params)
    return (params,)


def _dict_factory(cursor: sqlite3.Cursor, row: tuple) -> dict[str, Any]:
    """sqlite3 row factory mirroring psycopg2's RealDictCursor.

    Column names come from ``cursor.description``, so a query's keys are
    identical under both engines — which is what lets the 539 call sites
    keep treating rows as dicts.
    """
    return {col[0]: value for col, value in zip(cursor.description, row)}


def _register_functions(conn: sqlite3.Connection) -> None:
    """Register Postgres functions the app calls by name.

    ``NOW()`` appears 204 times. Registering it as a SQLite function
    means those statements run as written, instead of 204 textual
    rewrites that would each need re-verification.
    """
    conn.create_function("NOW", 0, utcnow_iso)
    # Postgres aliases: CURRENT_TIMESTAMP is a keyword SQLite already
    # has, but the camel/spaced spellings show up in query strings.
    conn.create_function("now", 0, utcnow_iso)

    # Catalogue introspection. The shape-detection helpers in database.py
    # ask Postgres whether a table exists (to_regclass) and what type a
    # column has (information_schema.columns) before migrating it. Both
    # are emulated so those helpers keep reading as Postgres.
    def to_regclass(name: Optional[str]) -> Optional[str]:
        if not name:
            return None
        table = str(name).split(".")[-1].strip('"')
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        return table if row else None

    conn.create_function("to_regclass", 1, to_regclass)

    # information_schema.columns is referenced as a table, not called, so
    # it is emulated as a real view over PRAGMA table_info exposing the
    # three columns those queries select: table_name, column_name,
    # data_type. The dotted name cannot be a SQLite identifier, so the
    # query text is rewritten to the view name on the way in.
    conn.execute(
        """
        CREATE TEMP VIEW IF NOT EXISTS information_schema_columns AS
        SELECT m.name AS table_name,
               p.name AS column_name,
               p.type AS data_type
        FROM sqlite_master AS m
        JOIN pragma_table_info(m.name) AS p
        WHERE m.type = 'table'
        """
    )


def _parse_copy_line(line: str, width: int) -> list:
    """Decode one COPY text line into Python values.

    Inverse of the escaping in :meth:`CompatCursor.copy_expert`: ``\\N``
    becomes None, ``\\x<hex>`` becomes bytes, and the tab / newline /
    carriage-return / backslash escapes are undone. Splitting on tabs
    first is safe because every literal tab in the data was escaped.
    """
    fields: List = []
    for raw in line.split("\t"):
        if raw == r"\N":
            fields.append(None)
        elif raw.startswith(r"\x"):
            fields.append(bytes.fromhex(raw[2:]))
        else:
            out = []
            i = 0
            while i < len(raw):
                ch = raw[i]
                if ch == "\\" and i + 1 < len(raw):
                    nxt = raw[i + 1]
                    out.append({"t": "\t", "n": "\n", "r": "\r", "\\": "\\"}.get(nxt, nxt))
                    i += 2
                else:
                    out.append(ch)
                    i += 1
            fields.append("".join(out))
    if len(fields) != width:
        raise sqlite3.ProgrammingError(
            f"COPY line has {len(fields)} fields, expected {width}"
        )
    return fields


#: The COPY statement forms the compat layer understands.
_COPY_TO_RE = re.compile(
    r"^\s*COPY\s+(?P<table>[\w.\"]+)\s*\((?P<cols>[^)]*)\)\s*TO\s+STDOUT",
    re.IGNORECASE,
)
_COPY_FROM_RE = re.compile(
    r"^\s*COPY\s+(?P<table>[\w.\"]+)\s*\((?P<cols>[^)]*)\)\s*FROM\s+STDIN",
    re.IGNORECASE,
)


def plain_row_factory(cursor: sqlite3.Cursor, row) -> tuple:
    """Row factory equivalent to psycopg2's plain cursor: a bare tuple.

    Used where the caller relies on positional access, e.g. the backup
    export path concatenating columns positionally so the dump stays
    replayable independent of dict ordering.
    """
    return tuple(row)


def real_dict_row_factory(cursor: sqlite3.Cursor, row) -> dict:
    """Row factory equivalent to psycopg2's RealDictCursor."""
    return {
        desc[0]: row[idx]
        for idx, desc in enumerate(cursor.description or ())
    }


#: Cursor factories by the name the ported Postgres code imports them
#: under. ``cursor_factory=PlainCursor`` in backup_db.py resolves here.
PLAIN_CURSOR = plain_row_factory
REAL_DICT_CURSOR = real_dict_row_factory


class CompatCursor:
    """A sqlite3 cursor that speaks Postgres.

    Wraps the driver cursor so ``cur.execute()`` translates the dialect
    on the way in. Feature code such as ``_create_ai_tasks(cur)`` calls
    ``cur.execute`` directly with raw Postgres DDL, and there are dozens
    of such helpers; making them keep their current signature is what
    avoids touching them.

    Delegates everything else to the wrapped cursor, so it is a drop-in
    replacement wherever database.py previously handed out a
    ``RealDictCursor``.
    """

    __slots__ = ("_cur", "_row_factory", "_copy_target")

    def __init__(self, cur: sqlite3.Cursor, cursor_factory=None):
        self._cur = cur
        self._copy_target = None
        # backup_db.py passes cursor_factory=PlainCursor because it needs
        # positional rows; the stdlib cursor has no such parameter, so the
        # factory is applied to the driver's cursor here instead.
        self._row_factory = cursor_factory
        if cursor_factory is not None:
            cur.row_factory = cursor_factory

    def execute(self, sql: str, params: Optional[Iterable] = None):
        # COPY ... FROM STDIN has no SQLite equivalent: the statement itself
        # carries no data, the stream handed to copy_from() does. Record the
        # target so copy_from() knows where the rows are going.
        m = _COPY_FROM_RE.match(sql)
        if m:
            table = m.group("table").strip('"')
            cols = [c.strip().strip('"') for c in m.group("cols").split(",")]
            self._copy_target = (table, cols)
            return self
        # Handled specially: Postgres's multi-column ADD COLUMN has no
        # SQLite equivalent, so it becomes one guarded statement per column.
        if _alter_add_columns(self._cur, sql):
            return self
        # DROP TABLE … CASCADE is a DDL-family statement, so it reaches
        # translate_ddl rather than translate_sql; the clause is stripped
        # there. The catalogue-view rewrite lives in translate_sql, so DDL
        # gets it applied here too via the same helper.
        if _is_ddl(sql):
            statement = translate_ddl(sql)
            bound = tuple(params) if params else ()
        else:
            statement = translate_sql(sql)
            bound = translate_params(sql, params)
        return self._cur.execute(statement, bound)

    def executemany(self, sql: str, seq_of_params: Iterable[Sequence]):
        statement = translate_ddl(sql) if _is_ddl(sql) else translate_sql(sql)
        return self._cur.executemany(statement, seq_of_params)

    def executescript(self, script: str):
        """Run a multi-statement script, translating each statement.

        SQLite's executescript only understands SQLite grammar, and the
        feature schema modules ship Postgres DDL, so each statement is
        rewritten individually.
        """
        for statement in _split_script(script):
            if statement.strip():
                self.execute(statement)

    # ── pass-through ────────────────────────────────────────────────
    @property
    def rowcount(self) -> int:
        return self._cur.rowcount

    @property
    def description(self):
        return self._cur.description

    @property
    def lastrowid(self):
        return self._cur.lastrowid

    def fetchone(self):
        return self._cur.fetchone()

    def fetchmany(self, size: Optional[int] = None):
        return self._cur.fetchmany(size) if size is not None else self._cur.fetchmany()

    def fetchall(self):
        return self._cur.fetchall()

    def copy_expert(self, sql: str, stream) -> None:
        """Emulate Postgres ``COPY ... TO STDOUT`` onto a text stream.

        The backup exporter is built on COPY: it is the only way to get a
        byte-faithful dump without quoting bugs, and the output has to
        replay later through ``COPY ... FROM STDIN``. sqlite3 has no
        equivalent, so the statement is run as a plain SELECT and each row
        is rendered into the tab-separated COPY text format.

        Postel's law note: the result is COPY *shaped* but not a genuine
        COPY -- NULLs and embedded tabs/newlines are rendered with the
        escape sequences below. That is enough to round-trip through
        :meth:`copy_from`, which is the only consumer.
        """
        m = _COPY_TO_RE.match(sql)
        if not m:  # pragma: no cover - callers only pass COPY ... TO STDOUT
            raise sqlite3.ProgrammingError(f"not a COPY ... TO STDOUT: {sql!r}")
        table = m.group("table").strip('"')
        cols = [c.strip().strip('"') for c in m.group("cols").split(",")]
        collist = ", ".join(f'"{c}"' for c in cols)
        # The WHERE clause, if any, rides through verbatim; translate_sql
        # rewrites its %s placeholders and Postgres-only constructs.
        tail = sql[m.end():]
        cur = self._cur.execute(translate_sql(f'SELECT {collist} FROM "{table}"{tail}'))
        names = [d[0] for d in (cur.description or ())]
        if not names:
            return
        # Materialise first: the cursor may carry a dict row_factory, and
        # iterating a dict yields its KEYS, not its values -- which silently
        # turns every row into its own column-name "header". A dict row is
        # re-ordered by `names` so the output follows the COPY column list
        # rather than the driver's column order.
        for row in cur.fetchall():
            if isinstance(row, dict):
                values = [row.get(name) for name in names]
            else:
                values = list(row)
            fields = []
            for value in values:
                if value is None:
                    fields.append(r"\N")
                elif isinstance(value, bytes):
                    fields.append(r"\x" + value.hex())
                else:
                    text = str(value)
                    text = text.replace("\\", "\\\\").replace("\t", "\\t")
                    text = text.replace("\n", "\\n").replace("\r", "\\r")
                    fields.append(text)
            stream.write("\t".join(fields) + "\n")

    def copy_from(self, stream) -> None:
        """Emulate Postgres ``COPY ... FROM STDIN`` from a text stream.

        Paired with :meth:`copy_expert`. Undoes its escaping and inserts
        the rows in a single transaction, so a truncated or malformed
        stream leaves the table untouched.
        """
        # INSERT INTO <table> (cols...) is carried in the statement the
        # caller paired with the stream; recover it from the cursor's
        # last-set context, which copy_from_setup records.
        target = getattr(self, "_copy_target", None)
        if target is None:  # pragma: no cover - guarded by copy_from_setup
            raise sqlite3.ProgrammingError("copy_from called without a target table")
        table, columns = target
        placeholders = ",".join("?" * len(columns))
        collist = ",".join(f'"{c}"' for c in columns)
        insert = f'INSERT INTO "{table}" ({collist}) VALUES ({placeholders})'
        for raw in stream:
            line = raw.rstrip("\n")
            if line == "":
                continue
            self.execute(insert, _parse_copy_line(line, len(columns)))
        self._copy_target = None

    def close(self):
        self._cur.close()

    def __iter__(self):
        return iter(self._cur)

    def __enter__(self) -> "CompatCursor":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _split_script(script: str) -> list[str]:
    """Split a SQL script on semicolons that are not inside a literal.

    Naive ``script.split(';')`` corrupts any statement containing a
    semicolon in a string or an identifier. A quote-aware scan is enough
    here: the schema DDL has no dollar-quoted blocks, and comments are
    stripped so a trailing ``; -- note`` does not swallow the next
    statement.
    """
    statements: list[str] = []
    buf: list[str] = []
    quote: Optional[str] = None
    i = 0
    n = len(script)
    while i < n:
        ch = script[i]
        if quote:
            buf.append(ch)
            if ch == quote:
                # Doubled quote is an escaped quote, not a terminator.
                if i + 1 < n and script[i + 1] == quote:
                    buf.append(script[i + 1])
                    i += 2
                    continue
                quote = None
            i += 1
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            buf.append(ch)
        elif ch == "-" and i + 1 < n and script[i + 1] == "-":
            newline = script.find("\n", i)
            i = n if newline == -1 else newline + 1
        elif ch == ";":
            statements.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    if buf:
        statements.append("".join(buf))
    return statements


# ─── ALTER TABLE … ADD COLUMN IF NOT EXISTS ───────────────────────────

# Postgres lets one ALTER TABLE add many columns, each with its own IF NOT
# EXISTS. SQLite accepts exactly one ADD COLUMN per statement and has no
# IF NOT EXISTS at all, so the multi-column form must be split and each
# column guarded by a PRAGMA lookup.
_ADD_COLUMN_RE = re.compile(
    r"^\s*ALTER\s+TABLE\s+(?P<table>[\w.\"'`]+)\s+"
    r"ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+(?P<rest>.+)$",
    re.IGNORECASE | re.DOTALL,
)

# Splits "a TEXT, b INTEGER DEFAULT 0" on commas that separate column
# definitions rather than appearing inside a parenthesised type such as
# VARCHAR(255) or DECIMAL(10,2).
_ADD_COLUMN_SPLIT_RE = re.compile(r",\s*(?=[A-Za-z_\"'`])")

# The repeated clause inside a multi-column ADD COLUMN, stripped from
# each piece before it is re-wrapped for SQLite.
_ADD_COLUMN_PREFIX_RE = re.compile(
    r"^ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?", re.IGNORECASE
)


def _existing_columns(cur: sqlite3.Cursor, table: str) -> set[str]:
    """Lower-cased column names for a table, via PRAGMA.

    Empty when the table does not exist yet, which is the correct
    outcome: the caller is about to CREATE it, and the ALTERs in the
    feature schemas are idempotency guards for a pre-existing table.
    """
    try:
        rows = cur.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.Error:
        return set()
    # PRAGMA rows are dicts when the connection carries our
    # row_factory and plain tuples otherwise, so both shapes are read.
    names: set[str] = set()
    for row in rows:
        if isinstance(row, dict):
            name = row.get("name")
        else:
            name = row[1] if len(row) > 1 else None
        if name:
            names.add(str(name).lower())
    return names


def _alter_add_columns(cur: sqlite3.Cursor, sql: str) -> bool:
    """Rewrite a Postgres multi-column ADD COLUMN into SQLite statements.

    Returns True when the statement was handled here, so the caller knows
    not to pass the original SQL to the driver.
    """
    match = _ADD_COLUMN_RE.match(sql)
    if not match:
        return False

    table = match.group("table").strip('"`')
    existing = _existing_columns(cur, table)
    for definition in _ADD_COLUMN_SPLIT_RE.split(match.group("rest")):
        # Each comma-separated piece repeats the clause Postgres allows
        # per column ("ADD COLUMN IF NOT EXISTS duration TEXT"), which
        # must be stripped before the definition can be re-wrapped in a
        # single SQLite ADD COLUMN.
        definition = _ADD_COLUMN_PREFIX_RE.sub("", definition.strip())
        if not definition:
            continue
        name = definition.split()[0].strip('"`[]')
        if name.lower() in existing:
            continue
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {translate_ddl(definition)}")
        existing.add(name.lower())
    return True


def check_sqlite_version() -> str:
    """Verify the runtime SQLite can run the local-first app.

    Returns the version string on success. Raises SqliteCompatError with
    the remedy rather than letting the first RETURNING insert fail with
    an opaque syntax error.
    """
    version = sqlite3.sqlite_version
    try:
        parts = tuple(int(p) for p in version.split(".")[:3])
    except ValueError:  # pragma: no cover - defensive, non-numeric version
        return version
    if parts < MIN_SQLITE_VERSION:
        raise SqliteCompatError(
            f"SQLite {version} is too old; the local-first build needs "
            f"{'.'.join(map(str, MIN_SQLITE_VERSION))} or newer for "
            f"RETURNING. On macOS this ships with Python 3.12+; on Debian "
            f"install libsqlite3-0 >= 3.35."
        )
    return version


#: Open-transaction bookkeeping, keyed by ``id(sqlite3.Connection)``.
#: See _txn_active for why this is a module-level map and not an
#: attribute on the connection or the wrapper.
_TXN_STATE: Dict[int, bool] = {}


class CompatConnection:
    """A sqlite3 connection whose cursors translate the dialect.

    The counterpart to :class:`CompatCursor`, one level up. It exists for
    the ~21 call sites that reach for ``conn.cursor()`` directly instead
    of going through database.py's helpers — the v2 backup importer
    (v2_domains, migrate_studies) is the largest cluster, with 11 raw
    cursors between them.

    Wrapping the connection rather than rewriting those 21 sites is what
    keeps the importer readable as Postgres, and keeps the change
    reversible: on Postgres, database.py hands out the raw pool
    connection and this class is never instantiated.
    """

    __slots__ = ("_conn",)

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def cursor(self, *args, cursor_factory=None, **kwargs) -> "_TxnCursor":
        return _TxnCursor(self, *args, cursor_factory=cursor_factory, **kwargs)

    # ── pass-through ────────────────────────────────────────────────
    @property
    def row_factory(self):
        return self._conn.row_factory

    @property
    def total_changes(self) -> int:
        return self._conn.total_changes

    @property
    def in_transaction(self) -> bool:
        return _txn_active(self._conn)

    # ── transaction control ─────────────────────────────────────────
    # The connection runs with isolation_level=None, which means every
    # statement autocommits. That is right for the helper path — execute()
    # commits explicitly, so there is nothing to defer — but it makes
    # rollback() a no-op, and the v2 backup importer relies on it: a failed
    # import must leave the database untouched, not half-written.
    #
    # So the first statement on this connection opens a transaction
    # implicitly, giving rollback() something real to undo. commit() and
    # rollback() both close it, which is the behaviour the psycopg2
    # connection the importer was written against had: one transaction,
    # explicitly ended by the caller.
    def _ensure_transaction(self) -> None:
        if not _txn_active(self._conn):
            self._conn.execute("BEGIN")
            _mark_txn(self._conn, True)

    def commit(self) -> None:
        if _txn_active(self._conn):
            self._conn.commit()
            _mark_txn(self._conn, False)

    def rollback(self) -> None:
        if _txn_active(self._conn):
            self._conn.rollback()
            _mark_txn(self._conn, False)

    def close(self) -> None:
        # An open transaction would be discarded by close() anyway, but
        # rolling back first releases the file lock deterministically.
        if _txn_active(self._conn):
            self._conn.rollback()
            _mark_txn(self._conn, False)
        self._conn.close()

    def execute(self, sql: str, params: Optional[Iterable] = None):
        self._ensure_transaction()
        return CompatCursor(self._conn.cursor()).execute(sql, params)

    def __enter__(self) -> "CompatConnection":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class _TxnCursor(CompatCursor):
    """A cursor that opens its connection's transaction on first execute.

    The transaction belongs to the connection, not the cursor, and it must
    begin before the first statement so a rollback after a failed import
    can undo it. Starting it lazily here — rather than in
    ``CompatConnection.cursor()`` — means a caller that opens a cursor for
    reads only never takes a write lock.
    """

    def __init__(self, conn: "CompatConnection", *args, cursor_factory=None, **kwargs):
        self._conn = conn
        super().__init__(conn._conn.cursor(*args, **kwargs), cursor_factory=cursor_factory)

    def execute(self, sql: str, params: Optional[Iterable] = None):
        self._conn._ensure_transaction()
        return super().execute(sql, params)

    def executemany(self, sql: str, seq_of_params: Iterable[Sequence]):
        self._conn._ensure_transaction()
        return super().executemany(sql, seq_of_params)

def connect(path: str, *, read_only: bool = False) -> sqlite3.Connection:
    """Open a SQLite connection configured like the Postgres pool was.

    The keyword arguments mirror the behaviour the app already relies on
    from psycopg2 + gunicorn:

    ``detect_types=0``
        Postgres handed back parsed ``datetime``/``Decimal`` objects.
        SQLite returns TEXT as ``str``, so the JSON layer serialises
        ISO-8601 strings instead of datetimes. Both are valid JSON, and
        the PWA parses either with ``new Date()`` — but text is what
        survives a JSON round-trip through ``json.dumps`` unchanged, so
        timestamps are stored and returned as ISO-8601 strings.

    ``check_same_thread=False``
        gunicorn's gevent worker serves requests as greenlets off one OS
        thread, and the test suite uses threads. This mirrors the pool's
        old cross-thread behaviour.

    ``isolation_level=None``
        Autocommit off-by-default in the stdlib driver would put every
        statement in an implicit transaction that database.py's
        ``conn.commit()`` calls were written to close. The helpers own
        transaction boundaries explicitly, so the driver must not second-
        guess them.

    ``row_factory=_dict_factory``
        Dict rows, as RealDictCursor produced.
    """
    check_sqlite_version()
    if read_only:
        conn = sqlite3.connect(
            f"file:{path}?mode=ro", uri=True, check_same_thread=False
        )
    else:
        conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = _dict_factory
    conn.isolation_level = None  # explicit BEGIN/COMMIT, driven by callers
    _register_functions(conn)
    return conn


def cursor(conn: sqlite3.Connection) -> CompatCursor:
    """A dialect-translating cursor for an open connection.

    database.py hands this to the ``_create_*`` / ``_migrate_*`` helpers
    that execute raw Postgres DDL, so those keep their existing signature.
    """
    return CompatCursor(conn.cursor())


def _txn_active(conn: sqlite3.Connection) -> bool:
    """Whether a transaction is open on the shared connection.

    ``sqlite3.Connection`` does not accept arbitrary attributes (no
    ``__dict__``), so the flag cannot live on the connection object. It
    lives in a module-level map keyed by ``id(conn)`` instead.

    Why it cannot simply live on the wrapper: ``get_connection()`` hands
    out a **new** wrapper on every call, and all of them share one
    underlying connection. Per-wrapper state meant two callers each
    believed they owned the transaction, and the second ``BEGIN`` failed
    with "cannot start a transaction within a transaction". The
    transaction is a property of the connection, so the bookkeeping has to
    be too.
    """
    return _TXN_STATE.get(id(conn), False)


def _mark_txn(conn: sqlite3.Connection, active: bool) -> None:
    if active:
        _TXN_STATE[id(conn)] = True
    else:
        _TXN_STATE.pop(id(conn), None)


def wrap_connection(conn: sqlite3.Connection) -> CompatConnection:
    """Wrap a connection so its cursors translate the dialect.

    Used for the callers that receive a connection and open their own
    cursors — the v2 backup importer is the main one. Wrapping at the
    boundary means those modules keep writing plain Postgres.
    """
    if isinstance(conn, CompatConnection):
        return conn
    return CompatConnection(conn)


def close(conn: sqlite3.Connection) -> None:
    """Roll back anything open, close the connection, forget its state.

    Clearing _TXN_STATE is not optional housekeeping: it is keyed by
    ``id(conn)``, and CPython reuses addresses. A stale True left behind by
    a closed connection would be inherited by the next connection to land
    on that address, which would then skip its BEGIN and autocommit.
    """
    try:
        if _txn_active(conn):
            conn.rollback()
    finally:
        _mark_txn(conn, False)
        conn.close()


def execute(conn: sqlite3.Connection, sql: str, params: Optional[Sequence] = None):
    """Execute one statement on an already-open connection.

    Used by database.py's helpers. DDL is routed through the type
    rewriter; DML only gets the placeholder fix.
    """
    cur = conn.cursor()
    statement = translate_ddl(sql) if _is_ddl(sql) else translate_sql(sql)
    cur.execute(statement, tuple(params) if params else ())
    return cur


def _is_ddl(sql: str) -> bool:
    """True for DDL, which needs the Postgres type rewrites.

    Leading comments and whitespace are skipped so a commented-out
    CREATE TABLE is still recognised.
    """
    stripped = sql.lstrip()
    while stripped.startswith("--"):
        newline = stripped.find("\n")
        if newline == -1:
            return False
        stripped = stripped[newline + 1:].lstrip()
    head = stripped[:16].upper()
    return head.startswith(("CREATE", "ALTER", "DROP"))


def table_names(conn: sqlite3.Connection) -> list[str]:
    """List user tables, excluding SQLite internals."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r["name"] for r in rows]


def column_types(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    """Declared column types for a table, for migration assertions."""
    return {
        r["name"]: (r["type"] or "").upper()
        for r in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
