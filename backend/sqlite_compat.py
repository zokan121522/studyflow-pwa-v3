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
import threading
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
#: The app's SQL is written against Postgres, where the schema is always
#: 'public' and is spelled out in a few queries. SQLite has one unnamed
#: schema, so the catalogue views below report 'public' -- the name the
#: queries filter on. Reporting 'main' made every
#: ``table_schema = 'public'`` predicate silently match nothing, which
#: surfaced as the backup exporter resolving zero tables.
#:
#: SQLite identifiers cannot contain a dot, so the name is rewritten
#: rather than quoted.
_CATALOGUE_VIEW_MAP = (
    ("information_schema.columns", "information_schema_columns"),
    ("information_schema.tables", "information_schema_tables"),
    ("information_schema.table_constraints", "information_schema_table_constraints"),
    ("information_schema.key_column_usage", "information_schema_key_column_usage"),
    ("information_schema.constraint_column_usage", "information_schema_constraint_column_usage"),
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


#: Postgres scalar max/min. SQLite has no ``GREATEST``/``LEAST`` at all --
#: its ``MAX``/``MIN`` are aggregates over a column, and only become scalar
#: two-or-more-argument functions when given more than one argument.
#: ``MAX(a, b)`` is therefore the right shape, but with one important
#: difference in NULL handling (see _scalar_max_min).
_SCALAR_MAX_MIN = {"GREATEST": "MAX", "LEAST": "MIN"}
_SCALAR_MAX_MIN_RE = re.compile(r"\b(GREATEST|LEAST)\s*\(", re.IGNORECASE)


def _split_top_level(inner: str) -> list[str]:
    """Split on commas that are not nested in parentheses or quotes."""
    parts: list[str] = []
    depth = 0
    quote = None
    start = 0
    idx = 0
    while idx < len(inner):
        char = inner[idx]
        if quote is not None:
            if char == quote:
                if idx + 1 < len(inner) and inner[idx + 1] == quote:
                    idx += 2
                    continue
                quote = None
        elif char in "'\"":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(inner[start:idx])
            start = idx + 1
        idx += 1
    parts.append(inner[start:])
    return [part.strip() for part in parts if part.strip()]


def _find_call_end(sql: str, open_paren: int) -> int:
    """Index of the ``)`` closing the paren at ``open_paren``, or -1.

    Scans from just after the opening paren, so depth 0 means "directly inside
    the call" and the first ``)`` seen there is the one that closes it.
    """
    depth = 0
    quote = None
    idx = open_paren + 1
    while idx < len(sql):
        char = sql[idx]
        if quote is not None:
            if char == quote:
                if idx + 1 < len(sql) and sql[idx + 1] == quote:
                    idx += 2
                    continue
                quote = None
        elif char in "'\"":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            if depth == 0:
                return idx
            depth -= 1
        idx += 1
    return -1


def _rewrite_scalar_max_min(sql: str) -> str:
    """Rewrite ``GREATEST(a, b)`` to SQLite's scalar ``MAX(a, b)``.

    A regex cannot do this: the arguments contain nested parentheses,
    subqueries and commas. The single-argument spelling is skipped on
    purpose -- Postgres ``GREATEST(x)`` is the identity, whereas SQLite
    ``MAX(x)`` is an aggregate over a column, so rewriting it would silently
    change the query's meaning.

    The rewrite is a plain rename, which means it preserves the argument
    list verbatim and therefore never disturbs placeholder numbering. An
    earlier attempt wrapped each argument in a rotated ``COALESCE`` to copy
    Postgres's ignore-NULL rule; it reproduced that rule correctly and then
    silently broke every named-parameter query, because each rotation
    duplicated the ``?`` the argument had already been rewritten to and
    shifted every later bind. Two spellings of ``%(now)s`` became two binds
    and the statement failed to execute -- a worse outcome than the NULL
    difference, which callers can close with an explicit ``COALESCE``.

    The remaining difference is NULL handling: Postgres ``GREATEST`` skips
    NULLs, SQLite's scalar ``MAX`` returns NULL if *any* argument is NULL.
    Queries that depend on the clamp should wrap the call themselves::

        COALESCE(GREATEST(0, delta), 0)   # 0 in both dialects
    """
    out = []
    pos = 0
    while True:
        match = _SCALAR_MAX_MIN_RE.search(sql, pos)
        if match is None:
            out.append(sql[pos:])
            return "".join(out)

        close = _find_call_end(sql, match.end() - 1)
        args = (
            [] if close == -1
            else _split_top_level(sql[match.end():close])
        )
        if len(args) < 2:
            # Unbalanced, or the one-argument identity form.
            out.append(sql[pos:match.end()])
            pos = match.end()
            continue

        func = _SCALAR_MAX_MIN[match.group(1).upper()]
        out.append(sql[pos:match.start()])
        out.append(func + "(" + ", ".join(args) + ")")
        pos = close + 1


def translate_sql(sql: str) -> str:
    """Normalise Postgres DML for the sqlite3 driver.

    Rewrites the placeholder style (both ``%s`` and ``%(name)s``), the one
    catalogue view the migration helpers query, the timestamp-difference
    EPOCH extraction, and the scalar ``GREATEST``/``LEAST`` spellings.
    Everything else (``ON CONFLICT``, ``RETURNING``, ``EXCLUDED``, window
    functions, CTEs) is already valid SQLite and must be left alone.
    """
    for postgres_name, sqlite_name in _CATALOGUE_VIEW_MAP:
        if postgres_name in sql:
            sql = sql.replace(postgres_name, sqlite_name)
    # Casts run before the placeholder rewrite, while %(name)s is still
    # intact. Once it has become a bare ?, the expression group can no
    # longer tell it apart from any other marker.
    if "::" in sql:
        sql = _rewrite_postgres_casts(sql)
    if "%(" in sql:
        sql = _PYNAME_RE.sub("?", sql)
    if "EXTRACT" in sql.upper():
        sql = _rewrite_extract_epoch(sql)
        sql = _rewrite_extract_fields(sql)
    if "to_date" in sql:
        sql = _rewrite_to_date(sql)
    if "AT TIME ZONE" in sql.upper():
        sql = _AT_TIME_ZONE_RE.sub("", sql)
    if "INTERVAL" in sql.upper():
        sql = _rewrite_intervals(sql)
    if _SCALAR_MAX_MIN_RE.search(sql):
        sql = _rewrite_scalar_max_min(sql)
    if _BARE_ON_CONFLICT_RE.search(sql):
        sql = _fix_bare_on_conflict(sql)
    if "%s" not in sql:
        return sql
    return _PYFORMAT_RE.sub("?", sql)



#: Postgres EXTRACT(<field> FROM <expr>) over a single value. The EPOCH
#: difference form is handled separately by _rewrite_extract_epoch, which runs
#: first; this covers the calendar-field spellings, of which
#: ``EXTRACT(DAY FROM to_date(day_date, 'YYYY-MM-DD'))`` in the agenda month
#: view is the one in the codebase today.
_EXTRACT_FIELD_RE = re.compile(
    r"EXTRACT\s*\(\s*(?P<field>EPOCH|DAY|MONTH|YEAR|DOY|DOW|HOUR|MINUTE|SECOND)"
    r"\s+FROM\s+(?P<expr>(?:[^()]*(?:\([^()]*\)[^()]*)*))\s*\)",
    re.IGNORECASE,
)

#: strftime format per Postgres EXTRACT field. SQLite's strftime reads the
#: same ISO-8601 text the app stores, so these agree by construction.
_EXTRACT_FORMATS = {
    "DAY": "%d", "MONTH": "%m", "YEAR": "%Y", "DOY": "%j", "DOW": "%w",
    "HOUR": "%H", "MINUTE": "%M", "SECOND": "%S",
    # %s is seconds since the epoch, which is what EPOCH means in Postgres.
    "EPOCH": "%s",
}


def _rewrite_extract_fields(sql: str) -> str:
    """Rewrite calendar-field EXTRACT() to strftime().

    ``EXTRACT(DAY FROM to_date(day_date, 'YYYY-MM-DD'))`` becomes
    ``CAST(strftime('%d', date(day_date)) AS INTEGER)``.

    The CAST is not decoration: the route downstream does int() on the value
    and builds a dict key from it, and Postgres's EXTRACT yields an integer,
    so returning a zero-padded string would change the API's response shape.
    """

    def repl(m: "re.Match") -> str:
        fmt = _EXTRACT_FORMATS.get(m.group("field").upper())
        if fmt is None:
            return m.group(0)
        return f"CAST(strftime('{fmt}', {m.group('expr')}) AS INTEGER)"

    return _EXTRACT_FIELD_RE.sub(repl, sql)


#: Postgres to_date(text, format). SQLite's date() parses the ISO-8601 text
#: the app stores directly, so the format argument is simply dropped. The
#: format is checked first: a non-ISO layout means date() would silently
#: return NULL, and a loud no-op is better than a wrong answer.
_TO_DATE_RE = re.compile(
    r"\bto_date\s*\(\s*(?P<value>[^,()]+?)\s*,\s*'(?P<fmt>[^']*)'\s*\)",
    re.IGNORECASE,
)


def _rewrite_to_date(sql: str) -> str:
    """Rewrite to_date(value, 'YYYY-MM-DD') to date(value)."""

    def repl(m: "re.Match") -> str:
        if m.group("fmt").upper() not in ("YYYY-MM-DD", "YYYY-MM-DD HH24:MI:SS"):
            return m.group(0)
        return f"date({m.group('value')})"

    return _TO_DATE_RE.sub(repl, sql)


#: ``<expr> AT TIME ZONE 'UTC'``. On Postgres this converts a timestamp with
#: a zone into a naive timestamp, and the app uses it to reduce an instant to
#: a UTC calendar day. SQLite has no time zones and the values are already
#: stored as UTC ISO-8601 text, so the conversion is a no-op and the clause is
#: dropped. Keeping it would be a syntax error; leaving the call site to strip
#: it would mean 32 modules each learning about the storage format.
_AT_TIME_ZONE_RE = re.compile(
    r"\s+AT\s+TIME\s+ZONE\s+'[^']*'", re.IGNORECASE
)


#: ``NOW() - INTERVAL '1 hour'`` and its ``+`` mirror. The whole subtraction
#: is folded, rather than the interval alone, because NOW() is a registered
#: function returning ISO text: leaving it as ``text - strftime(...)`` would
#: be SQLite's *numeric* subtraction on two leading digits, silently
#: producing a plausible-looking wrong number. SQLite has no timestamp
#: arithmetic type, so the only correct translation moves the step into
#: strftime's own modifier argument, where "now" is a real reference point.
_INTERVAL_OFFSET_RE = re.compile(
    r"(?P<base>NOW\s*\(\s*\)|CURRENT_TIMESTAMP|"
    r"\bstrftime\s*\([^()]*?\))"
    r"\s*(?P<sign>[+-])\s*"
    r"INTERVAL\s+'(?P<value>[-+]?\d+(?:\.\d+)?)\s*(?P<unit>[a-zA-Z]+)?'",
    re.IGNORECASE,
)

#: strftime modifier per Postgres interval unit. The modifier carries its own
#: unit suffix, which is how strftime walks time in either direction.
_INTERVAL_UNITS = {
    "second": "seconds", "seconds": "seconds", "sec": "seconds", "secs": "seconds",
    "minute": "minutes", "minutes": "minutes", "min": "minutes", "mins": "minutes",
    "hour": "hours", "hours": "hours", "hr": "hours", "hrs": "hours",
    "day": "days", "days": "days",
    "week": "days", "weeks": "days",
    "month": "months", "months": "months",
    "year": "years", "years": "years",
}

#: The app stores UTC ISO-8601 text, so an instant shifted in time is
#: rendered in exactly the format utcnow_iso() produces. Without the
#: fractional part and the Z, the shifted value would not compare equal, as
#: text, to the stored values it is being compared against.
_INTERVAL_OUT = "%Y-%m-%dT%H:%M:%fZ"


def _rewrite_intervals(sql: str) -> str:
    """Rewrite ``NOW() - INTERVAL '1 hour'`` to a strftime step back from now.

    A bare ``INTERVAL '1 hour'`` with nothing to subtract it from is left
    alone, so it raises a syntax error in review rather than quietly
    comparing a string against NULL.
    """

    def repl(m: "re.Match") -> str:
        unit = (m.group("unit") or "day").lower()
        modifier = _INTERVAL_UNITS.get(unit)
        if modifier is None:
            return m.group(0)
        amount = float(m.group("value"))
        if unit in ("week", "weeks"):
            amount *= 7          # strftime has no week modifier
        sign = "-" if m.group("sign") == "-" else ""
        amount = -amount if amount < 0 else amount
        return (f"strftime('{_INTERVAL_OUT}', 'now', "
                f"'{sign}{amount:g} {modifier}')")

    return _INTERVAL_OFFSET_RE.sub(repl, sql)


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


#: Postgres ``col = ANY(%s)`` — an array membership test. SQLite has no
#: arrays, so it becomes ``col IN (...)``. The placeholders are expanded
#: to match, and the single bound value (a Python list) is dropped, since
#: SQLite cannot bind one value to several markers.
_ANY_RE = re.compile(
    r"(?P<col>[\w.\"]+)\s*=\s*ANY\s*\(\s*%\s*s\s*\)",
    re.IGNORECASE,
)


def _expand_any(sql: str, params=None) -> tuple[str, Any]:
    """Rewrite ``= ANY(%s)`` into ``IN (?, ?, ...)``.

    One placeholder per element of the bound list -- not one per occurrence
    of the clause: each occurrence is a separate membership test over the
    same array. The list is spliced out and its elements returned in order,
    to be spliced into the parameter list in place of the single bound
    value, since sqlite3 cannot bind one value to several markers.
    """
    matches = _ANY_RE.findall(sql)
    if not matches:
        # Hand the params back untouched. Coercing to a tuple here would
        # turn a dict into its *keys* and drop every value before
        # translate_params gets a chance to map them by name.
        return sql, params
    array = params[0] if params and isinstance(params[0], (list, tuple)) else None
    rest = list(params[1:]) if params else []
    if array is not None:
        # The bound value IS the array: splice its elements in place of it.
        sql = _ANY_RE.sub(
            lambda m: f'{m.group("col")} IN ({", ".join("?" * len(array))})', sql
        )
        return sql, list(array) + rest
    # No array bound (should not happen, but do not silently mis-bind):
    # keep one marker per occurrence and drop nothing.
    sql = _ANY_RE.sub(
        lambda m: f'{m.group("col")} IN ({", ".join("?" * len(matches))})', sql
    )
    return sql, rest


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

    # ── Postgres string functions ────────────────────────────────
    # left()/right() are used by the backup selectors to trim a label for
    # display, and SQLite has neither. substr() is the equivalent, but
    # left(s, n) and substr(s, 1, n) reorder their arguments, so this is a
    # function rather than a textual rewrite.

    def _pg_left(value, length=None):
        if value is None:
            return None
        if length is None:
            return str(value)[:1]
        return str(value)[:int(length)]

    def _pg_right(value, length=None):
        if value is None:
            return None
        text = str(value)
        if length is None:
            return text[-1:]
        n = int(length)
        return text[-n:] if n else ""

    def _pg_repeat(value, times):
        return None if value is None else str(value) * int(times)

    conn.create_function("left", 2, _pg_left)
    conn.create_function("left", 1, _pg_left)
    conn.create_function("right", 2, _pg_right)
    conn.create_function("right", 1, _pg_right)
    conn.create_function("repeat", 2, _pg_repeat)

    # ── Advisory locks ────────────────────────────────────────────
    # Postgres uses these to serialise a job that must not run twice. The
    # local-first app is one process, one user, one request at a time, and
    # its own transaction already provides that exclusion, so there is
    # nothing to lock. The alternative is registering no function at all,
    # which turns "this queue already has a sync running" into a 500.

    def _advisory_lock(*_args) -> int:
        return 1

    def _advisory_unlock(*_args) -> int:
        return 1

    for name in ("pg_try_advisory_lock", "pg_advisory_lock",
                 "pg_try_advisory_xact_lock", "pg_advisory_xact_lock"):
        conn.create_function(name, 1, _advisory_lock)
        conn.create_function(name, 2, _advisory_lock)
    for name in ("pg_advisory_unlock", "pg_advisory_unlock_all"):
        conn.create_function(name, 0, _advisory_unlock)
        conn.create_function(name, 1, _advisory_unlock)

    # information_schema.columns is referenced as a table, not called, so
    # it is emulated as a real view over PRAGMA table_info exposing the
    # three columns those queries select: table_name, column_name,
    # data_type. The dotted name cannot be a SQLite identifier, so the
    # query text is rewritten to the view name on the way in.
    #
    # lower(p.type) is load-bearing, not tidiness. SQLite reports declared
    # types in the case they were written ('TEXT', 'INTEGER'); Postgres
    # reports them lowercased ('text', 'integer'). Callers compare against
    # the Postgres spelling, and one of them -- _sessions_is_legacy, which
    # decides whether to DROP TABLE sessions -- tests `!= "text"`. With the
    # raw uppercase value that comparison is *always* true, so a healthy,
    # already-correct table was judged legacy and dropped on every startup.
    # On Postgres the same code is correct, which is exactly why it survived:
    # the emulation was the only place the two dialects disagreed, and only
    # on a fresh local install does it destroy anything. The table_type
    # normalisation in information_schema.tables below is the same trap,
    # already fixed there.
    conn.execute(
        """
        CREATE TEMP VIEW IF NOT EXISTS information_schema_columns AS
        SELECT 'public' AS table_schema,
               m.name AS table_name,
               p.name AS column_name,
               lower(p.type) AS data_type,
               -- pragma_table_info.cid is 0-based; Postgres ordinal_position
               -- is 1-based. Anything ordering columns off this view (the
               -- backup COPY column list) would otherwise reverse the table.
               p.cid + 1 AS ordinal_position
        FROM sqlite_master AS m
        JOIN pragma_table_info(m.name) AS p
        WHERE m.type = 'table'
        """
    )
    # The backup path resolves its table list through
    # information_schema.tables, and filters on table_type = 'BASE TABLE'
    # -- SQLite's own type string is lowercase 'table', so the predicate
    # would match nothing and the export would come back empty. The view
    # reports the Postgres spelling, which is the only one callers know.
    conn.execute(
        """
        CREATE TEMP VIEW IF NOT EXISTS information_schema_tables AS
        SELECT 'public' AS table_schema,
               name AS table_name,
               CASE type WHEN 'view' THEN 'VIEW' ELSE 'BASE TABLE' END AS table_type
        FROM sqlite_master
        WHERE type IN ('table', 'view')
        """
    )
    # Foreign-key metadata, as the three Postgres catalogue views the FK
    # ordering in backup_user._order_tables() joins them through. SQLite
    # exposes the same information one table at a time through
    # PRAGMA foreign_key_list, and pragma functions can be joined against
    # sqlite_master to get every table in one query.
    #
    # The constraint names are synthesised as "<table>_fk_<n>" because the
    # join is on constraint_name; SQLite does not store names for foreign
    # keys, and the ordering code only needs the edges, not the names.
    conn.execute(
        """
        CREATE TEMP VIEW IF NOT EXISTS information_schema_table_constraints AS
        SELECT m.name AS constraint_schema,
               'public' AS table_schema,
               m.name AS table_name,
               m.name || '_fk_' || f."id" AS constraint_name,
               'FOREIGN KEY' AS constraint_type
        FROM sqlite_master AS m
        JOIN pragma_foreign_key_list(m.name) AS f
        WHERE m.type = 'table'
        """
    )
    conn.execute(
        """
        CREATE TEMP VIEW IF NOT EXISTS information_schema_key_column_usage AS
        SELECT m.name AS constraint_schema,
               'public' AS table_schema,
               m.name AS table_name,
               m.name || '_fk_' || f."id" AS constraint_name,
               f."from" AS column_name
        FROM sqlite_master AS m
        JOIN pragma_foreign_key_list(m.name) AS f
        WHERE m.type = 'table'
        """
    )
    conn.execute(
        """
        CREATE TEMP VIEW IF NOT EXISTS information_schema_constraint_column_usage AS
        SELECT m.name AS constraint_schema,
               'public' AS table_schema,
               m.name || '_fk_' || f."id" AS constraint_name,
               f."table" AS table_name,
               f."to" AS column_name
        FROM sqlite_master AS m
        JOIN pragma_foreign_key_list(m.name) AS f
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


#: The COPY statement forms the compat layer understands. The export path
#: uses both: ``COPY <table> (cols) TO STDOUT`` and the query form
#: ``COPY (SELECT ...) TO STDOUT``, which needs no table name because the
#: query is self-contained.
_COPY_TO_RE = re.compile(
    r"^\s*COPY\s+"
    r"(?:\(\s*(?P<query>SELECT\b.*)\)\s*|(?P<table>[\w.\"]+)\s*(?:\((?P<cols>[^)]*)\))?\s*)"
    r"TO\s+STDOUT",
    re.IGNORECASE | re.DOTALL,
)
_COPY_FROM_RE = re.compile(
    r"^\s*COPY\s+(?P<table>[\w.\"]+)\s*\((?P<cols>[^)]*)\)\s*FROM\s+STDIN",
    re.IGNORECASE,
)


#: Postgres ``CREATE TEMP TABLE _stage (LIKE "courses")`` — the restore path
#: stages a table before merging it. SQLite has no LIKE clause, so the shape
#: is rebuilt from ``PRAGMA table_info``: column names, types and NOT NULL are
#: enough for a staging table whose only job is to hold rows verbatim.
_CREATE_TEMP_LIKE_RE = re.compile(
    r"^\s*CREATE\s+(?P<temp>TEMP(?:ORARY)?\s+)?TABLE\s+(?P<name>[\w.\"]+)\s*"
    r"\(\s*LIKE\s+(?P<src>[\w.\"]+)\s*\)\s*;?\s*$",
    re.IGNORECASE,
)


def _expand_create_like(cur: sqlite3.Cursor, sql: str) -> Optional[str]:
    """Rewrite ``CREATE TEMP TABLE x (LIKE y)`` into an explicit column list."""
    m = _CREATE_TEMP_LIKE_RE.match(sql)
    if not m:
        return None
    src = m.group("src").strip('"`')
    cols = []
    for row in cur.execute(f'PRAGMA table_info("{src}")').fetchall():
        # PRAGMA rows come back as dicts on the app's connections, and a
        # dict is not indexable by position -- read them by column name.
        if isinstance(row, dict):
            name, ctype, notnull = row["name"], row["type"] or "", row["notnull"]
        else:
            name, ctype, notnull = row[1], row[2] or "", row[3]
        part = f'"{name}" {ctype}'.rstrip()
        if notnull:
            part += " NOT NULL"
        cols.append(part)
    if not cols:
        raise sqlite3.ProgrammingError(f"cannot stage: no columns in {src!r}")
    temp = "TEMP " if m.group("temp") else ""
    name = m.group("name").strip('"')
    return f'CREATE {temp}TABLE "{name}" ({", ".join(cols)})' 


#: Postgres ``SET session_replication_role = replica`` suspends foreign-key
#: enforcement for the session; the restore path uses it to load tables in
#: an order the constraints would otherwise reject.
#:
#: In SQLite this is already the default: ``PRAGMA foreign_keys`` is off
#: unless a connection opts in, and this app never does. So the statement
#: becomes a no-op rather than a PRAGMA -- the pragma is silently ignored
#: inside a transaction, and the transaction is already open by the time a
#: cursor executes anything, so issuing it would be a false promise.
_SESSION_REPLICATION_RE = re.compile(
    r"^\s*SET\s+session_replication_role\s*=\s*(?:replica|DEFAULT|ORIGINAL)\s*;?\s*$",
    re.IGNORECASE,
)


#: A bare ``ON CONFLICT DO NOTHING`` in an INSERT ... SELECT.
#:
#: SQLite cannot parse it: with no conflict target there is nothing for the
#: parser to attach the DO clause to, and it reports "near DO: syntax
#: syntax error" (a WHERE true before the clause does not help -- that
#: produces "near ON" instead). Postgres accepts the bare form.
#:
#: The faithful equivalent is INSERT OR IGNORE, which has identical
#: semantics: skip rows that violate a uniqueness constraint, let every
#: other row through. A targeted ``ON CONFLICT (col) DO NOTHING`` is left
#: alone -- SQLite parses that form fine.
_BARE_ON_CONFLICT_RE = re.compile(
    r"\bON\s+CONFLICT\s+DO\s+NOTHING\b(?!\s*\()",
    re.IGNORECASE,
)
_INSERT_SELECT_RE = re.compile(
    r"^\s*INSERT\s+(?:OR\s+\w+\s+)?INTO\b.*\bSELECT\b",
    re.IGNORECASE | re.DOTALL,
)


def _fix_bare_on_conflict(sql: str) -> str:
    """Turn a bare ``ON CONFLICT DO NOTHING`` into ``INSERT OR IGNORE``.

    Only for INSERT ... SELECT. A VALUES insert parses fine as written,
    and rewriting its VALUES list would be a much larger change than the
    problem needs.
    """
    if not _BARE_ON_CONFLICT_RE.search(sql):
        return sql
    if not _INSERT_SELECT_RE.match(sql):
        return sql
    sql = _BARE_ON_CONFLICT_RE.sub("", sql).rstrip()
    return re.sub(
        r"^\s*INSERT\s+INTO\b",
        "INSERT OR IGNORE INTO",
        sql,
        count=1,
        flags=re.IGNORECASE,
    )


#: Postgres ``expr::type`` casts. SQLite spells these CAST(expr AS type), and
#: the restore path and the v2 importer both use them to coerce timestamps
#: and ids.
#:
#: An "atom" is one castable expression: a balanced parenthesised group, a
#: quoted identifier, a dotted name, or a bare marker. It deliberately
#: excludes a stray ``)`` from a run of word characters -- an earlier,
#: looser version happily matched the ``T))`` tail of a CAST it had itself
#: just produced, so chained casts came out as ``CAST(x AS (CAST(T)))``.
#:
#: The whole chain ``a::t::u`` is matched at once and rebuilt from the
#: right, because ``::`` is right-associative: ``a::t::u`` is ``a::t``
#: cast to ``u``, not two independent casts.
#:
#: The type alternation is ordered longest-first and closed with a word
#: boundary on purpose. Regex alternation is first-match, so listing
#: ``timestamp`` before ``timestamptz`` consumed the first nine characters
#: of ``timestamptz`` and left a dangling ``tz`` behind, which SQLite then
#: rejected with "near tz: syntax error". Both spellings occur in the app.
_CAST_TYPE_ALT = (
    r"character\s+varying|double\s+precision|timestamptz|timestamp|bigint|"
    r"integer|smallint|boolean|bool|numeric|varchar|text|date|real|jsonb|"
    r"json|uuid|int"
)
_CAST_ATOM = r'CAST\((?:[^()]|\([^()]*\))*\)|"(?:[^"]|"")*"|\([^()]*\)|%\(\w+\)s|%s|[A-Za-z_][\w.$]*'
_POSTGRES_CAST_RE = re.compile(
    rf"(?P<atom>{_CAST_ATOM})"
    rf"(?P<suffix>(?:\s*::\s*(?:{_CAST_TYPE_ALT})(?![A-Za-z0-9_]))+)",
    re.IGNORECASE,
)
#: Pulls the individual ``::type`` names back out of a matched chain.
_CAST_TYPE_FIND_RE = re.compile(
    rf"::\s*((?:{_CAST_TYPE_ALT}))(?![A-Za-z0-9_])", re.IGNORECASE
)
_SQLITE_CAST_TYPES = {
    "int": "INTEGER", "integer": "INTEGER", "bigint": "INTEGER",
    "smallint": "INTEGER", "bool": "INTEGER", "boolean": "INTEGER",
    "text": "TEXT", "varchar": "TEXT", "character varying": "TEXT",
    "timestamp": "TEXT", "timestamptz": "TEXT", "date": "TEXT",
    "numeric": "NUMERIC", "real": "REAL", "json": "TEXT", "jsonb": "TEXT",
    "uuid": "TEXT",
}


def _sqlite_cast_type(name: str) -> str:
    return _SQLITE_CAST_TYPES.get(re.sub(r"\s+", " ", name.lower()), "TEXT")


def _rewrite_postgres_casts(sql: str) -> str:
    """Rewrite ``x::type`` chains into nested ``(CAST(x AS TYPE))``.

    The outer parentheses are not decoration. A bare CAST is not accepted
    everywhere an expression is: ``VALUES (%s::int)`` becomes
    ``VALUES CAST(? AS INTEGER)`` and SQLite rejects that with "near CAST".
    Redundant parens are legal in every expression position, so always
    wrapping is the one form that survives SELECT lists, COALESCE
    arguments, ORDER BY and VALUES rows alike.
    """

    def repl(m: re.Match) -> str:
        # The whole ``::a::b`` run arrives in one group, so the rewrite
        # never has to run a second pass over text it has already
        # rewritten -- doing that is what used to let it match the "T))"
        # tail of the CAST it had just produced.
        expr = m.group("atom")
        for name in _CAST_TYPE_FIND_RE.findall(m.group("suffix")):
            expr = f"(CAST({expr} AS {_sqlite_cast_type(name)}))"
        return expr

    return _POSTGRES_CAST_RE.sub(repl, sql)


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
        if _SESSION_REPLICATION_RE.match(sql):
            return self
        if _is_ddl(sql):
            like = _expand_create_like(self._cur, sql)
            statement = translate_ddl(like if like is not None else sql)
            bound = tuple(params) if params else ()
        else:
            # ANY(%s) binds one array value to several markers, so the
            # rewrite has to happen before the params are ordered: the
            # spliced elements shift every position after it.
            sql, bound = _expand_any(sql, params)
            statement = translate_sql(sql)
            bound = translate_params(sql, bound) if bound else ()
        _note_write(self._cur, statement)
        return self._cur.execute(statement, bound)

    def executemany(self, sql: str, seq_of_params: Iterable[Sequence]):
        statement = translate_ddl(sql) if _is_ddl(sql) else translate_sql(sql)
        _note_write(self._cur, statement)
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
        if m is None:
            # psycopg2's copy_expert carries both directions, and the restore
            # path loads its staging table with COPY ... FROM STDIN and the
            # payload as the stream argument. So this is not a no-op: the
            # stream is read straight into the target table here.
            fm = _COPY_FROM_RE.match(sql)
            if not fm:  # pragma: no cover - callers only pass COPY forms
                raise sqlite3.ProgrammingError(f"not a COPY statement: {sql!r}")
            table = fm.group("table").strip('"')
            cols = [c.strip().strip('"') for c in fm.group("cols").split(",")]
            self._copy_target = (table, cols)
            self.copy_from(stream)
            return
        names: List[str] = []
        if m.group("query"):
            # COPY (SELECT ...) TO STDOUT -- the query stands on its own and
            # the column list comes from the result, so translate it as-is.
            cur = self._cur.execute(translate_sql(m.group("query")))
        else:
            table = m.group("table").strip('"')
            raw_cols = m.group("cols")
            cols = (
                [c.strip().strip('"') for c in raw_cols.split(",")]
                if raw_cols
                else None
            )
            if cols is None:
                # No column list: SELECT * would reorder on restore anyway,
                # so spell the columns out in declaration order.
                cols = [
                    r[1]
                    for r in self._cur.execute(
                        f'PRAGMA table_info("{table}")'
                    ).fetchall()
                ]
            collist = ", ".join(f'"{c}"' for c in cols)
            cur = self._cur.execute(
                translate_sql(f'SELECT {collist} FROM "{table}"')
            )
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

#: Whether the open transaction has seen a write that is not yet committed,
#: keyed the same way. This is what lets close() distinguish "left an innocent
#: read transaction open" from "discarded real work", which matters because the
#: first is harmless and happens on every read-only request, so warning on it
#: would train us to ignore the warning that does matter.
_TXN_WROTE: Dict[int, bool] = {}

#: No lock is held around transactions, and that is deliberate.
#:
#: There used to be one, guarding a single process-wide connection. Both the
#: connection and the lock are gone: database.get_connection() now hands out
#: one connection per thread, so a handle is only ever touched by its owner
#: and there is nothing to serialise. SQLite's own file locking does the rest,
#: and it is the right tool for it -- it arbitrates between *processes*, which
#: is the real constraint when several local app instances point at one file.
#:
#: Keeping the lock alongside per-thread connections would have been worse
#: than useless: it serialised every request in the process behind whichever
#: thread happened to be writing, which is the throughput cliff the per-thread
#: change existed to remove.


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

    __slots__ = ("_conn", "_borrowed")

    def __init__(self, conn: sqlite3.Connection, *, borrowed: bool = False):
        self._conn = conn
        # A borrowed wrapper stands for the process-wide connection, handed
        # out by database.get_connection(). See close().
        self._borrowed = borrowed

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
            try:
                self._conn.commit()
            finally:
                _mark_txn(self._conn, False)

    def rollback(self) -> None:
        if _txn_active(self._conn):
            try:
                self._conn.rollback()
            finally:
                _mark_txn(self._conn, False)

    def close(self) -> None:
        """End the transaction; close the handle only if we own it.

        Closing the shared connection is fatal. On SQLite there is exactly
        one connection for the whole process, so a single request that
        reached this -- and three did, each in a ``finally: conn.close()``
        written against a pool where closing a borrowed connection just
        returns it -- left every later request failing with "Cannot operate
        on a closed database". The health endpoint could report healthy
        while the app was dead.

        So a *borrowed* wrapper treats close() as what the callers mean by
        it: end the transaction and hand the connection back. The handle
        stays open, and database.close_pool() remains the one thing that
        really shuts it, which is what tests and shutdown need.

        A connection this wrapper owns -- opened by connect() for a single
        task, such as the v2 importer's -- still closes for real, so no
        handle leaks.
        """
        # rollback() rather than a bare driver rollback, so the transaction
        # lock is released on the same path. An open transaction would be
        # discarded by close() anyway, but rolling back first releases the
        # file lock deterministically.
        self.rollback()
        if self._borrowed:
            return
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
    _apply_pragmas(conn, read_only=read_only)
    _register_functions(conn)
    return conn


#: How long a statement waits for another connection's write lock before
#: giving up, in milliseconds. Long enough to ride out a burst of tabs
#: writing at once, short enough that a genuinely stuck writer surfaces as
#: an error instead of hanging the request.
_BUSY_TIMEOUT_MS = 15000


def _apply_pragmas(conn: sqlite3.Connection, *, read_only: bool) -> None:
    """Configure the connection the way a multi-tab local app needs.

    Nothing here was set before, and SQLite's defaults are the wrong ones for
    this app:

    ``busy_timeout`` defaults to **0**, which does not mean "wait briefly" —
    it means *never wait*. Any overlap between two connections returns
    ``database is locked`` on the spot. Measured here: twelve threads
    inserting at once lost the race immediately and the test failed on the
    first collision, while the same twelve succeed once a busy handler is
    installed. This is also why the threaded server reported
    ``database is locked`` before connections became per-thread; per-thread
    connections removed the deadlock but not the contention.

    ``journal_mode=WAL`` matters for the same reason from the other side.
    Under the default rollback journal a reader blocks a writer for the whole
    read, so one long dashboard query would stall an unrelated write. WAL lets
    readers and a writer coexist. It is a persistent property of the *file*,
    so setting it per connection is harmless after the first.

    ``foreign_keys`` is left alone: SQLite defaults it **off** per connection
    and the schema relies on real FK constraints, but turning it on here would
    start rejecting legacy rows that the Postgres instance tolerated, which is
    a migration decision rather than a connection one.

    ``synchronous=NORMAL`` is the usual companion to WAL and is safe here
    because the database file lives in the user's own data directory, not on a
    server holding anyone else's data at risk.
    """
    conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
    if not read_only:
        # Read-only handles cannot change a file's journal mode, and asking
        # is an error rather than a no-op.
        try:
            conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.OperationalError:
            pass  # e.g. a read-only or network filesystem
        conn.execute("PRAGMA synchronous = NORMAL")


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
        _TXN_WROTE.pop(id(conn), None)


def txn_has_writes(conn: sqlite3.Connection) -> bool:
    """Whether the open transaction has issued a data-changing statement.

    An open transaction is not by itself a problem: the compat layer begins
    one on the first statement of any kind so that rollback() has something
    to undo, which means a read-only request also leaves one open. What
    matters is whether a request left *work* behind, because that is the
    only case where closing the transaction can lose data.

    So the caller that ends the transaction asks this first, and warns about
    the paths that actually discard something, instead of logging a warning
    on every read the app ever makes.
    """
    return _TXN_WROTE.get(id(conn), False)


def _mark_write(conn: sqlite3.Connection) -> None:
    _TXN_WROTE[id(conn)] = True


#: Leading keyword of a statement that changes stored data. DDL counts: an
#: uncommitted CREATE TABLE that gets rolled back is work lost just as surely
#: as a lost INSERT, and the v2 importer leans on that.
_WRITE_LEADERS = ("INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "DROP",
                  "ALTER", "TRUNCATE")


def _driver_connection(obj):
    """Reach the raw ``sqlite3.Connection`` behind any layer of wrappers.

    The wrappers nest, and the three starting points are all real cases:
    a driver ``Cursor`` (which exposes ``.connection``), a ``CompatCursor``
    (which holds the object below it in ``_cur``), and a ``_TxnCursor`` /
    ``CompatConnection`` pair (which hold it in ``_conn``). The visited set
    makes a cycle harmless rather than fatal, and returning None on anything
    unrecognised keeps this from ever raising -- it only backs a warning.
    """
    seen = set()
    while obj is not None and not isinstance(obj, sqlite3.Connection):
        if id(obj) in seen:
            return None
        seen.add(id(obj))
        for attr in ("connection", "_conn", "_cur"):
            nxt = getattr(obj, attr, None)
            if nxt is not None:
                obj = nxt
                break
        else:
            return None
    return obj


def _note_write(driver_cursor, statement: str) -> None:
    """Record that this statement changed data, if it did.

    The transaction and its bookkeeping belong to the connection, so the
    cursor is only a way in.

    Best effort by design: this backs a diagnostic warning, never a
    correctness decision, so a cursor or statement too odd to classify here
    simply does not set the flag rather than raising.
    """
    conn = _driver_connection(driver_cursor)
    if conn is None or not _txn_active(conn):
        return
    head = statement.lstrip().lstrip("(").split(None, 1)
    if head and head[0].upper() in _WRITE_LEADERS:
        _mark_write(conn)


def wrap_connection(conn: sqlite3.Connection) -> CompatConnection:
    """Wrap a connection so its cursors translate the dialect.

    Used for the callers that receive a connection and open their own
    cursors — the v2 backup importer is the main one. Wrapping at the
    boundary means those modules keep writing plain Postgres.
    """
    if isinstance(conn, CompatConnection):
        return conn
    return CompatConnection(conn)


def borrow(conn: sqlite3.Connection) -> CompatConnection:
    """Wrap the process-wide connection for a caller that will hand it back.

    The difference from :func:`wrap_connection` is ownership: a borrowed
    wrapper's ``close()`` ends the transaction without closing the shared
    handle. database.get_connection() is the only caller.
    """
    if isinstance(conn, CompatConnection):
        return conn
    return CompatConnection(conn, borrowed=True)


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
