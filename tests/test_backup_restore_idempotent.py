"""Restore idempotency: natural-key dedup + FK remap for the tree.

The duplicate-courses bug: courses/topics/blocks are keyed only by a
surrogate id, so a restore that merges with `ON CONFLICT DO NOTHING` sees
a conflict only when the archive's ids collide with the target's. The
archive's ids come from the machine the backup was written on; when they
differ from the target's, every row looks new and a repeated restore
duplicates the whole tree (the user's live DB ended up with courses
72-88 and duplicates 89-105).

The fix matches the tree on natural keys before inserting -- courses by
(user_id, title), topics by (resolved course_id, title), blocks by
(resolved topic_id, title) -- and remaps children's FK columns through
the archive-id -> target-id maps, so children attach to the ORIGINAL
parent and a second restore adds nothing.

Two regressions prove it:
1. Restoring the same archive twice into an empty DB: the second run
   creates ZERO new courses/topics/blocks and reports them in `skipped`.
2. Restoring into a DB that already has the same content under DIFFERENT
   ids (the user's exact situation): nothing is duplicated, and the
   child block still points at the existing parent id, not a copy.

The zips come from the real `POST /api/backup/mine`, never from a
hand-built fixture: the contract under test is export-then-import.
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
    "(200, 1, 2, 20, 'markdown', 'Bloque B', 'contenido B')",
    "INSERT INTO weeks (week_id, user_id) VALUES ('W1', 1)",
    "INSERT INTO days (date, week_id, user_id) VALUES ('2026-01-05', 'W1', 1)",
    "INSERT INTO sessions (id, day_date, user_id, title) "
    "VALUES ('s1', '2026-01-05', 1, 'Sesion 1')",
    "INSERT INTO todos (id, user_id, title) VALUES (1, 1, 'Todo uno')",
]

# The same content, but under a DIFFERENT id space: this is the user's
# live-DB situation (originals 72-88, archive carries other ids).
SEED_DIFFERENT_IDS = [
    "INSERT INTO courses (id, user_id, title) VALUES "
    "(999, 1, 'Alpha'), (998, 1, 'Beta')",
    "INSERT INTO topics (id, course_id, user_id, title) VALUES "
    "(9990, 999, 1, 'Tema A'), (9980, 998, 1, 'Tema B')",
    "INSERT INTO blocks (id, user_id, course_id, topic_id, type, title, "
    "content) VALUES "
    "(99900, 1, 999, 9990, 'markdown', 'Bloque A', 'contenido A'), "
    "(99800, 1, 998, 9980, 'markdown', 'Bloque B', 'contenido B')",
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


def _run(statements):
    conn = database.get_connection()
    try:
        cur = conn.cursor()
        for sql in statements:
            cur.execute(sql)
        conn.commit()          # sqlite_compat holds the tx open until told
    finally:
        conn.close()


def _query(sql: str) -> list:
    conn = database.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql)
        return [dict(r) if not isinstance(r, dict) else r
                for r in cur.fetchall()]
    finally:
        conn.close()


def _one(sql: str):
    rows = _query(sql)
    return rows[0] if rows else None


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


def _restore(client, payload: bytes):
    return client.post(
        "/api/backup/mine/restore",
        data={"file": (io.BytesIO(payload), "backup.zip")},
        content_type="multipart/form-data")


def _inspect(client, payload: bytes):
    return client.post(
        "/api/backup/mine/inspect",
        data={"file": (io.BytesIO(payload), "backup.zip")},
        content_type="multipart/form-data")


def _tree_counts() -> tuple:
    return (_one("SELECT count(*) AS n FROM courses")["n"],
            _one("SELECT count(*) AS n FROM topics")["n"],
            _one("SELECT count(*) AS n FROM blocks")["n"])


def test_inspect_reads_the_upload_stream_and_serves_options(client):
    """The inspect path streams the upload instead of buffering it in RAM.

    A 1.3 GB backup used to be read whole into a BytesIO, which OOM-killed
    the process. Opening ZipFile straight on Flask's spooled stream must
    still parse a real export and return manifest + options.
    """
    payload = _export(client)

    response = _inspect(client, payload)
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert body["has_manifest"] is True
    assert body["manifest"]["format"] == "studyflow-user-backup"
    assert "options" in body
    assert len(body["options"]["tree"]["courses"]) == 2


def test_second_restore_into_empty_db_adds_nothing(client):
    """Same archive twice: the second run inserts ZERO tree rows."""
    payload = _export(client)
    _run(WIPE)

    first = _restore(client, payload)
    assert first.status_code == 200, first.get_data(as_text=True)
    assert first.get_json()["ok"] is True
    assert _tree_counts() == (2, 2, 2)

    second = _restore(client, payload)
    assert second.status_code == 200, second.get_data(as_text=True)
    body = second.get_json()
    assert body["ok"] is True
    # nothing new anywhere: agenda/always-tables also conflict on their keys
    assert body["rows_total"] == 0
    assert _tree_counts() == (2, 2, 2)
    # the six tree rows already existed and are reported as skipped
    assert body["skipped"] >= 6


def test_restore_against_different_ids_remaps_to_existing_parents(client):
    """The user's live-DB situation: same titles, different id space.

    The archive carries ids 1/10/100, the target already owns the same
    content under 999/9990/99900. The restore must add ZERO rows and the
    child block must keep pointing at the EXISTING parent ids -- not at a
    freshly inserted duplicate.
    """
    payload = _export(client)
    _run(WIPE)
    _run(SEED_DIFFERENT_IDS)

    response = _restore(client, payload)
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert body["ok"] is True
    # no duplicates: still exactly the pre-existing rows
    assert _tree_counts() == (2, 2, 2)
    # ...which means the six archived tree rows were all skipped
    assert body["skipped"] >= 6

    # the tree rows survived where they were, under the ORIGINAL ids
    assert _one("SELECT id FROM courses WHERE title = 'Alpha'")["id"] == 999
    assert _one("SELECT id FROM topics WHERE title = 'Tema A'")["id"] == 9990
    assert _one("SELECT id FROM blocks WHERE title = 'Bloque A'")["id"] == 99900

    # children attach to the EXISTING parents, not to duplicates
    block = _one(
        "SELECT b.topic_id, b.course_id FROM blocks b "
        "WHERE b.title = 'Bloque A'")
    assert block["topic_id"] == 9990
    topic = _one(
        "SELECT t.course_id FROM topics t WHERE t.title = 'Tema A'")
    assert topic["course_id"] == 999
    # every block's parent topic is a real topic, and there is exactly one
    # parent per title: the map rewrote the archive ids, never re-inserted
    assert _one(
        "SELECT count(*) AS n FROM blocks b "
        "JOIN topics t ON t.id = b.topic_id "
        "WHERE b.title = 'Bloque A' AND t.title = 'Tema A'")["n"] == 1
    assert _one(
        "SELECT count(*) AS n FROM topics t "
        "JOIN courses c ON c.id = t.course_id "
        "WHERE t.title = 'Tema A' AND c.title = 'Alpha'")["n"] == 1