"""Restore must not import blocks whose parent topic is not restored.

The orphan-blocks bug: a block is selected when its id is ticked OR its
course is ticked OR its topic is ticked, but the FK remap only rewrites
``topic_id`` when the topic itself was restored. A block that survives on
its course alone, while its ``topic_id`` points at a topic the archive does
not carry (a dangling reference -- 48 of them reached a live DB), is
inserted with the stale archive ``topic_id``. The row then exists but no
topic can render it, so the content is invisible in the UI.

The guard under test resolves the post-remap ``topic_id`` against the
target ``topics`` table and drops the row when the parent is not there.

The zip comes from the real ``POST /api/backup/mine``, never a hand-built
fixture: the contract under test is export-then-import, and the orphan has
to survive the exporter for the import to see it (SQLite leaves foreign
keys off by default, exactly as a local install does).
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest

import database

SEED = [
    "INSERT INTO courses (id, user_id, title) VALUES "
    "(1, 1, 'Alpha'), (2, 1, 'Beta')",
    "INSERT INTO topics (id, course_id, user_id, title) VALUES "
    "(10, 1, 1, 'Tema A'), (20, 2, 1, 'Tema B')",
    "INSERT INTO blocks (id, user_id, course_id, topic_id, type, title, "
    "content) VALUES "
    "(100, 1, 1, 10, 'markdown', 'Bloque A', 'contenido A'), "
    "(200, 1, 2, 20, 'markdown', 'Bloque B', 'contenido B'), "
    # Claims course 1, but hangs off topic 999 -- a topic the archive does
    # not carry. Selecting course 1 selects this block through its course
    # alone; the archive has no topic to remap 999 to.
    "(300, 1, 1, 999, 'markdown', 'Bloque huerfano', 'sin tema real')",
    "INSERT INTO weeks (week_id, user_id) VALUES ('W1', 1)",
    "INSERT INTO days (date, week_id, user_id) VALUES ('2026-01-05', 'W1', 1)",
    "INSERT INTO sessions (id, day_date, user_id, title) "
    "VALUES ('s1', '2026-01-05', 1, 'Sesion 1')",
    "INSERT INTO todos (id, user_id, title) VALUES (1, 1, 'Todo uno')",
]

# Children first, so a foreign key (enforced or not) can never complain.
WIPE = [
    "DELETE FROM sessions", "DELETE FROM days", "DELETE FROM weeks",
    "DELETE FROM todos", "DELETE FROM blocks", "DELETE FROM topics",
    "DELETE FROM courses",
]

MEDIA_DIRS = {
    "PDF_UPLOAD_FOLDER": "pdfs",
    "AUDIO_UPLOAD_FOLDER": "audio",
    "INFOGRAPHIC_UPLOAD_FOLDER": "infographics",
    "IMAGE_UPLOAD_FOLDER": "images",
    "SCRAPED_DIR": "scraped_pdfs",
}

ORPHANS_SQL = (
    "SELECT count(*) AS n FROM blocks b LEFT JOIN topics t "
    "ON t.id = b.topic_id WHERE b.topic_id IS NOT NULL AND t.id IS NULL"
)


def _run(statements):
    conn = database.get_connection()
    try:
        cur = conn.cursor()
        for sql in statements:
            cur.execute(sql)
        conn.commit()          # sqlite_compat holds the tx open until told
    finally:
        conn.close()


def _one(sql: str):
    conn = database.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql)
        row = cur.fetchone()
        return dict(row) if row is not None and not isinstance(row, dict) \
            else row
    finally:
        conn.close()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """The real app on a throwaway SQLite file with seeded data."""
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("STUDYFLOW_DATA_DIR", str(data))
    monkeypatch.setenv("STUDYFLOW_DB_ENGINE", "sqlite")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    media = tmp_path / "media"
    for var, folder in MEDIA_DIRS.items():
        target = media / folder
        target.mkdir(parents=True)
        monkeypatch.setenv(var, str(target))

    import engine

    database.close_pool()
    monkeypatch.setattr(engine, "_active_engine", None, raising=False)
    database.init_db()
    _run(SEED)

    import server

    app = server.create_app()
    app.config["TESTING"] = True
    yield app.test_client()
    database.close_pool()


def _export(client) -> bytes:
    response = client.post("/api/backup/mine", json={})
    assert response.status_code == 200, \
        response.get_data(as_text=True)[:500]
    return response.data


def _restore(client, payload: bytes, selection):
    data = {"file": (io.BytesIO(payload), "backup.zip"),
            "selection": json.dumps(selection)}
    return client.post("/api/backup/mine/restore", data=data,
                       content_type="multipart/form-data")


def test_restore_skips_blocks_whose_parent_topic_is_not_restored(client):
    """Course 1 ticked: the block dangling off topic 999 is NOT imported."""
    payload = _export(client)
    _run(WIPE)

    response = _restore(client, payload, {"courses": [1]})
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert body["ok"] is True

    # The good block under course 1's own topic landed.
    assert _one("SELECT count(*) AS n FROM blocks")["n"] == 1
    assert _one("SELECT title FROM blocks")["title"] == "Bloque A"
    # The dangling block was skipped, not inserted with a stale topic_id.
    assert _one("SELECT count(*) AS n FROM blocks WHERE id = 300")["n"] == 0
    # No block in the DB points at a topic that does not exist.
    assert _one(ORPHANS_SQL)["n"] == 0
    # The skip is reported so the UI can mention it.
    assert body.get("skipped_orphan_blocks") == 1
