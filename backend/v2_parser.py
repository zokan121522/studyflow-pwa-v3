# backend/v2_parser.py
r"""Parser for the v2 PostgreSQL COPY-TSV dump (issue #8).

The v2 backup (format "studyflow-user-backup") stores its rows in
`user_data.sql` as psql COPY blocks:

    -- ===== courses =====
    COPY "courses" ("id", "title", ...) FROM stdin;
    <tab separated rows>
    \.

This module turns that dump into plain Python dicts WITHOUT ever
executing SQL — the v2 dump is untrusted input, so it is only parsed,
never replayed against the database.

COPY text-format escaping (subset we need):
    \\N    → NULL
    \\n \\t \\r \\b \\f \\v  → control chars
    \\\\   → literal backslash
    any other char after a backslash → itself
"""

import re

# The "-- ===== name =====" banner is optional: a dump without it must still
# parse, so the COPY statement alone is enough to locate a section.
_SECTION_RE = re.compile(
    r'(?:--\s*=====\s*(?P<name>[\w.]+)\s*=====\s*\n)?'
    r'COPY\s+"(?P<table>[\w.]+)"\s*\((?P<cols>[^)]*)\)\s*FROM\s+stdin;\s*\n'
)


def _unescape(value: str):
    """Undo psql COPY text escaping. Returns None for \\N (SQL NULL).

    Scans left to right instead of chained replace() calls: PostgreSQL
    escapes the backslash itself (\\\\), so a naive sequence turns `\\\\n`
    into a newline where the source had a literal backslash followed by n.
    An unrecognised escape yields the bare character, matching COPY.
    """
    if value == "\\N":
        return None
    if "\\" not in value:
        return value
    simple = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v"}
    out = []
    i, n = 0, len(value)
    while i < n:
        ch = value[i]
        if ch != "\\" or i + 1 >= n:
            out.append(ch)
            i += 1
            continue
        nxt = value[i + 1]
        if nxt == "\\":
            out.append("\\")
        else:
            out.append(simple.get(nxt, nxt))
        i += 2
    return "".join(out)


def _split_cols(cols: str) -> list:
    return [c.strip().strip('"') for c in cols.split(",")]


def parse_copy_sections(sql_text: str) -> dict:
    """Parse a whole v2 dump → {table_name: [row_dict, ...]}.

    Tables with no rows are omitted. Malformed lines (wrong column count)
    are skipped rather than aborting the whole parse, and the skip is
    reported in the returned `_skipped` counter key.
    """
    out, skipped = {}, 0
    matches = list(_SECTION_RE.finditer(sql_text))
    for i, m in enumerate(matches):
        table = m.group("table")
        cols = _split_cols(m.group("cols"))
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(sql_text)
        chunk = sql_text[start:end]
        rows = []
        # Rows end at the "\." terminator line. It may sit at the very end
        # of the chunk with no trailing newline, so accept both endings —
        # otherwise the terminator is read as data and shifts every column.
        term = re.search(r"^\\\.$", chunk, re.MULTILINE)
        if term:
            chunk = chunk[:term.start()]
        for line in chunk.split("\n"):
            if not line.strip():
                continue
            fields = line.split("\t")
            if len(fields) != len(cols):
                skipped += 1
                continue
            rows.append(
                {c: _unescape(v) for c, v in zip(cols, fields)}
            )
        if rows:
            out[table] = rows
    if skipped:
        out["_skipped"] = skipped
    return out


def parse_manifest(zipfile) -> dict:
    """Return the v2 manifest.json dict (empty dict when absent/invalid)."""
    import json
    for name in ("manifest.json", "backup.json"):
        try:
            with zipfile.open(name) as f:
                payload = json.loads(f.read().decode("utf-8"))
            if isinstance(payload, dict):
                return payload
        except Exception:
            continue
    return {}


def detect_format(zipfile) -> str:
    """Classify a backup ZIP: "v3" | "v2" | "unknown"."""
    try:
        with zipfile.open("backup.json") as f:
            import json
            payload = json.loads(f.read().decode("utf-8"))
        if isinstance(payload, dict) and payload.get("version") == 1:
            return "v3"
    except Exception:
        pass
    try:
        with zipfile.open("manifest.json") as f:
            import json
            manifest = json.loads(f.read().decode("utf-8"))
        if isinstance(manifest, dict) and \
                manifest.get("format") == "studyflow-user-backup":
            return "v2"
    except Exception:
        pass
    return "unknown"
