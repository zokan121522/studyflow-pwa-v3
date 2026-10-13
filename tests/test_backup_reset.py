"""POST /api/backup/reset — the destructive "Borrar todo".

The endpoint exists so a user can wipe every piece of content and then
restore a clean backup. Two things make that acceptable rather than
terrifying, and both are asserted here:

* ``users`` and ``addons`` survive. A reset that also removed the login or
  the global addon catalogue would lock the user out of their own instance.
* a timestamped SQLite snapshot is taken BEFORE the wipe and its path is
  returned, so even a misclick is recoverable.

The content tables (courses/topics/blocks/todos/…) must come back empty, and
uploaded media must be gone from disk.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import database

SEED = [
    "INSERT INTO courses (id, user_id, title) VALUES (1, 1, 'Alpha')",
    "INSERT INTO topics (id, course_id, user_id, title) VALUES "
    "(10, 1, 1, 'Tema A')",
    "INSERT INTO blocks (id, user_id, course_id, topic_id, type, title, "
    "content) VALUES (100, 1, 1, 10, 'markdown', 'Bloque A', 'contenido A')",
    "INSERT INTO todos (id, user_id, title) VALUES (1, 1, 'Todo uno')",
]

#: Tables the seed touched and that must be empty after the reset.
CONTENT_TABLES = ["todos", "blocks", "topics", "courses"]

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
        conn.commit()
    finally:
        conn.close()


def _count(table: str) -> int:
    conn = database.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(f'SELECT COUNT(*) FROM "{table}"')
        row = cur.fetchone()
    finally:
        conn.close()
    return row[0] if not isinstance(row, dict) else list(row.values())[0]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """The real app on a throwaway SQLite file with seeded content + media."""
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

    # One tiny file per media category, plus a leftover restore-job.
    for var, folder in MEDIA_DIRS.items():
        (media / folder / "old.bin").write_bytes(b"x" * 16)
    jobs = data / "restore-jobs" / "job-123"
    jobs.mkdir(parents=True)
    (jobs / "part-000000").write_bytes(b"y" * 8)

    import server

    app = server.create_app()
    app.config["TESTING"] = True
    yield app.test_client(), data, media
    database.close_pool()


def test_reset_wipes_content_and_keeps_safekeepers(client):
    test_client, data, media = client

    users_before = _count("users")
    addons_before = _count("addons")
    assert users_before >= 1
    assert addons_before >= 1

    resp = test_client.post("/api/backup/reset")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    report = resp.get_json()

    # Report shape the UI relies on.
    assert report["ok"] is True
    assert isinstance(report["tables"], dict)
    assert report["tables_count"] >= len(CONTENT_TABLES)
    assert set(report["preserved"]) == {"users", "addons", "sqlite_sequence"}
    assert report["integrity_check"] == "ok"

    # Every seeded content table is empty now.
    for table in CONTENT_TABLES:
        assert _count(table) == 0, f"{table} was not wiped"
    # And the report says so.
    for table in CONTENT_TABLES:
        assert report["tables"].get(table) == 1

    # Safekeepers untouched.
    assert _count("users") == users_before
    assert _count("addons") == addons_before

    # Snapshot exists on disk next to the DB and is non-empty.
    snapshot = Path(report["snapshot"])
    assert snapshot.is_file()
    assert snapshot.stat().st_size > 0
    assert snapshot.parent == data

    # Media erased, and reported.
    assert report["media"]["files_removed"] >= len(MEDIA_DIRS) + 1
    assert report["media"]["bytes_removed"] > 0
    for folder in MEDIA_DIRS.values():
        assert list((media / folder).iterdir()) == []
    assert not any((data / "restore-jobs").iterdir())
