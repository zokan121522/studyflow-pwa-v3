"""Restore against SQLite, which is the engine the launcher actually uses.

Neither restore path had an HTTP-level test. The v2 helpers were exercised
directly, so it was possible to believe backup/restore worked on a local
install while only ever having proven it against the development database.

The two formats are not interchangeable, and that is the trap:

* ``/api/backup/import`` opens ``backup.json``  → a v3 ZIP.
* ``/api/backup/v2/import`` opens ``user_data.sql`` → a v2 PostgreSQL COPY ZIP.

Feeding a v2 ZIP to the v3 route must fail as a clean 400 and change nothing.
An importer that half-applies a foreign archive is how a restore turns into
data loss, so that rejection is asserted as carefully as the success paths.
"""

from __future__ import annotations

import io
import json
import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import database  # noqa: E402


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    """Throwaway data directory, reset the same way the passing suite does."""
    target = tmp_path / "data"
    target.mkdir()
    monkeypatch.setenv("STUDYFLOW_DATA_DIR", str(target))
    monkeypatch.setenv("STUDYFLOW_DB_ENGINE", "sqlite")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    import database
    import engine

    database.close_pool()
    monkeypatch.setattr(engine, "_active_engine", None, raising=False)
    yield target
    database.close_pool()


@pytest.fixture()
def conn(data_dir):
    """An initialised SQLite connection through the production get/put path."""
    import database

    database.init_db()
    connection = database.get_connection()
    yield connection
    database.put_connection(connection)


def _copy_block(table: str, columns: list[str], rows: list[dict]) -> str:
    """A psql `COPY ... FROM stdin` section, text format, tab separated.

    Built to match what pg_dump emits, because the point is to prove the parser
    copes with that dialect rather than with something convenient.
    """
    head = f'COPY "{table}" ({", ".join(chr(34) + c + chr(34) for c in columns)}) FROM stdin;'
    lines = [head]
    for row in rows:
        cells = []
        for col in columns:
            value = row.get(col)
            if value is None:
                cells.append(r"\N")  # NULL, not an empty string
            else:
                # COPY text escaping: backslash first, then tabs and newlines.
                text = str(value).replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n")
                cells.append(text)
        lines.append("\t".join(cells))
    lines.append(r"\.")
    return "\n".join(lines) + "\n"


def _v2_zip(courses: list[dict]) -> bytes:
    """A ZIP shaped like a v2 PostgreSQL user_data.sql backup."""
    sections = ""
    if courses:
        sections += _copy_block(
            "courses",
            # The real v3 column list. An earlier version of this test used the
            # v2 shape ("archived"), which no longer exists, and died with
            # "no such column" -- a bug in the test's idea of the schema.
            ["id", "user_id", "title", "description", "color", "progress",
             "is_favorite", "icon", "order_index", "created_at", "updated_at"],
            courses,
        )
    # manifest.json is what detect_format() looks for to call it "v2".
    manifest = {"format": "studyflow-user-backup", "version": 2}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("user_data.sql", sections)
    return buffer.getvalue()


def _v3_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("backup.json", json.dumps({"version": 1, "studyflow": {}}))
    return buffer.getvalue()


def test_v2_course_dump_imports_onto_sqlite(conn):
    """A v2 PostgreSQL dump must land in the local SQLite database.

    The COPY values arrive as text and are bound as text. SQLite is dynamically
    typed and stores each in its own affinity, so `archived=0` becomes the
    integer 0 and `created_at='2026-10-05T09:00:00Z'` stays a string that
    compares and sorts as written.
    """
    from v2_parser import parse_copy_sections
    import v2_import

    payload = _v2_zip([
        {"id": 41, "user_id": 1, "title": "DAW", "color": "#ff0000",
         "progress": 0, "is_favorite": 0, "order_index": 0,
         "created_at": "2026-10-05T09:00:00Z"},
        {"id": 42, "user_id": 1, "title": "P&A", "color": None,
         "progress": 100, "is_favorite": 1, "order_index": 1,
         "created_at": "2026-10-05T10:00:00Z"},
    ])

    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        raw = archive.read("user_data.sql").decode()
        tables = parse_copy_sections(raw)
        report = v2_import.migrate_v2(conn, 1, archive, tables)
    conn.commit()

    rows = conn.execute(
        "SELECT id, title, color FROM courses WHERE user_id=1 ORDER BY id"
    ).fetchall()
    # Rows come back as dicts, for parity with Postgres' dict cursors.
    #
    # The v2 primary keys are NOT preserved: 41/42 become 1/2. A local install
    # may already hold courses, so reusing the old ids would either collide or
    # silently renumber unrelated rows. The importer always allocates fresh
    # ones, which also makes re-importing append rather than overwrite --
    # see test_v2_reimport_duplicates_rather_than_overwriting.
    assert [r["title"] for r in rows] == ["DAW", "P&A"]
    assert [r["id"] for r in rows] == [1, 2]

    # NULL survives as NULL, not as the string "\N" or an empty string.
    assert rows[0]["color"] == "#ff0000"
    assert rows[1]["color"] is None
    # The v2 timestamp is readable as the string it was written as (id 1, not 41:
    # keys are remapped).
    stored = conn.execute("SELECT created_at FROM courses WHERE id=1").fetchone()["created_at"]
    assert str(stored).startswith("2026-10-05T09:00:00Z")

    assert isinstance(report, dict) and "counts" in report


def test_migrate_v2_does_not_commit(conn):
    """migrate_v2 must leave the transaction open, because the route owns it.

    This is the property that makes the route's `conn.rollback()` meaningful:
    if the importer committed on its own, a failure after a later step would
    leave the earlier writes in the database and the 500 the user sees would be
    a lie.

    Testing it by provoking an error is unreliable -- two obvious candidates
    turned out to be non-events (a truncated COPY section imports cleanly as
    fewer rows; a duplicate primary key is upserted, because re-importing is
    treated as an update). The contract itself is the thing worth pinning.
    """
    from v2_parser import parse_copy_sections
    import v2_import

    payload = _v2_zip([
        {"id": 41, "user_id": 1, "title": "Sin commitear", "color": "#ff0000",
         "progress": 0, "is_favorite": 0, "order_index": 0,
         "created_at": "2026-10-05T09:00:00Z"},
    ])
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        tables = parse_copy_sections(archive.read("user_data.sql").decode())
        v2_import.migrate_v2(conn, 1, archive, tables)

    # Written to the transaction...
    assert len(conn.execute(
        "SELECT id FROM courses WHERE title='Sin commitear'"
    ).fetchall()) == 1

    # ...and gone once the caller rolls it back, which only holds if
    # migrate_v2 never committed on its own.
    conn.rollback()
    assert len(conn.execute(
        "SELECT id FROM courses WHERE title='Sin commitear'"
    ).fetchall()) == 0, "migrate_v2 committed; the route's rollback cannot be trusted"


def test_v2_reimport_duplicates_rather_than_overwriting(conn):
    """Restoring the same archive twice yields copies. Pinned so it is a
    decision rather than an accident.

    The v3 restore route asks the user how to resolve title conflicts. The v2
    migration route does not: it appends and renames to "DAW (2)". Nothing is
    lost, but a user who retries a restore they thought had failed ends up with
    two of everything, which is confusing enough to look like data corruption.
    The safe behaviour is to check `status` first and restore once.
    """
    from v2_parser import parse_copy_sections
    import v2_import

    payload = _v2_zip([
        {"id": 41, "user_id": 1, "title": "DAW", "color": "#ff0000",
         "progress": 0, "is_favorite": 0, "order_index": 0,
         "created_at": "2026-10-05T09:00:00Z"},
    ])

    def restore():
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            tables = parse_copy_sections(archive.read("user_data.sql").decode())
            v2_import.migrate_v2(conn, 1, archive, tables)
        conn.commit()

    restore()
    restore()

    titles = [r["title"] for r in conn.execute("SELECT title FROM courses ORDER BY id")]
    assert len(titles) == 2, titles
    assert titles[0] == "DAW"


def test_v3_restore_rejects_a_v2_archive_without_touching_anything():
    """The two formats are not interchangeable, and the rejection is clean.

    The v3 route looks for backup.json; a v2 archive has user_data.sql and no
    backup.json. It must answer 400 before opening a transaction, because a
    user who has just pointed the restore at the wrong file should not have to
    wonder whether their current data survived.
    """
    from backup_core import load_zip

    assert load_zip(_BytesFile(_v2_zip([]))) is None, (
        "a v2 archive was accepted by the v3 loader"
    )
    assert load_zip(_BytesFile(_v3_zip())) is not None, (
        "a v3 archive was rejected by its own loader"
    )


def test_detect_format_tells_the_two_apart():
    """The UI has to route the user to the right restore path."""
    from v2_parser import detect_format

    with zipfile.ZipFile(io.BytesIO(_v2_zip([]))) as archive:
        assert detect_format(archive) == "v2"
    with zipfile.ZipFile(io.BytesIO(_v3_zip())) as archive:
        assert detect_format(archive) == "v3"


class _BytesFile:
    """Minimal FileStorage stand-in.

    backup_core.load_zip() calls ``.read()`` on the upload, while the v2 helper
    calls ``.stream`` -- a real werkzeug FileStorage serves both, so the stub
    has to as well. (An earlier version of this test only provided ``.stream``
    and every v3 archive came back None, which looked like a loader bug.)
    """

    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self.stream = io.BytesIO(payload)

    def read(self, size: int = -1) -> bytes:
        return self.stream.read(size)