"""Selective import: the restore must mirror what the export decided.

The export answers "what ships?" with the fragments in backup_resolve;
this file proves the import answers the same question over the same
archive. Six behaviours, each a regression someone actually hit or
would:

1. A partial selection imports only the ticked tree -- and does NOT
   drag the agenda along, because the agenda was not ticked.
2. No selection at all is the historical full restore, and a second
   run of the same archive adds nothing while saying how many rows it
   skipped instead of pretending nothing happened.
3. A malformed `selection` field degrades to (2): an unreadable field
   must never turn a working full restore into a 500.
4. The inspect endpoint reports a manifest-less zip honestly instead
   of crashing on it.
5. The inspect endpoint serves the selector's options -- tree, agenda,
   media -- built from the archive alone.
6. Files follow the media tick: unticked means zero bytes written even
   when the archive carries them, ticked (plus the unattributed
   opt-in) writes them.

The zips come from the real `POST /api/backup/mine`, never from a
hand-built fixture: the contract under test is export-then-import, and
a hand-built archive would test the fixture's idea of the format
instead of the exporter's.
"""

from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path

import pytest

import database

SEED = [
    # no users row: init_db already seeds LOCAL_USER_ID idempotently
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

# Children first, so a foreign key (enforced or not) can never complain.
WIPE = [
    "DELETE FROM sessions", "DELETE FROM days", "DELETE FROM weeks",
    "DELETE FROM todos", "DELETE FROM blocks", "DELETE FROM topics",
    "DELETE FROM courses",
]

# storage_paths keys the disk-scan bucket off the directory NAME, so the
# folder must be called exactly what FOLDER_OF calls it.
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


def _export(client, body=None) -> bytes:
    response = client.post("/api/backup/mine", json=body)
    assert response.status_code == 200, \
        response.get_data(as_text=True)[:500]
    return response.data


def _restore(client, payload: bytes, selection=None):
    data = {"file": (io.BytesIO(payload), "backup.zip")}
    if selection is not None:
        data["selection"] = selection if isinstance(selection, str) \
            else json.dumps(selection)
    return client.post("/api/backup/mine/restore", data=data,
                       content_type="multipart/form-data")


def _inspect(client, payload: bytes):
    return client.post(
        "/api/backup/mine/inspect",
        data={"file": (io.BytesIO(payload), "backup.zip")},
        content_type="multipart/form-data")


def test_partial_restore_keeps_only_the_tick(client):
    """Course 1 ticked: Alpha lands, Beta and the agenda do not."""
    payload = _export(client)
    _run(WIPE)

    response = _restore(client, payload, {"courses": [1]})
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert body["ok"] is True

    titles = [r["title"] for r in _query(
        "SELECT title FROM courses ORDER BY id")]
    assert titles == ["Alpha"]
    topics = [r["title"] for r in _query(
        "SELECT title FROM topics ORDER BY id")]
    assert topics == ["Tema A"]
    blocks = [r["id"] for r in _query("SELECT id FROM blocks ORDER BY id")]
    assert blocks == [100]
    # agenda was not ticked: its three tables stay empty
    assert _one("SELECT count(*) AS n FROM weeks")["n"] == 0
    assert _one("SELECT count(*) AS n FROM days")["n"] == 0
    assert _one("SELECT count(*) AS n FROM sessions")["n"] == 0
    # todos is an always-table: shipped whatever the user ticks
    assert _one("SELECT count(*) AS n FROM todos")["n"] == 1


def test_full_restore_is_untouched_and_idempotent(client):
    """No selection restores everything; the second run adds nothing."""
    payload = _export(client)
    _run(WIPE)

    first = _restore(client, payload)
    assert first.status_code == 200, first.get_data(as_text=True)
    body = first.get_json()
    assert body["ok"] is True
    assert _one("SELECT count(*) AS n FROM courses")["n"] == 2
    assert _one("SELECT count(*) AS n FROM weeks")["n"] == 1
    assert _one("SELECT count(*) AS n FROM sessions")["n"] == 1

    second = _restore(client, payload)
    assert second.status_code == 200
    again = second.get_json()
    assert again["ok"] is True
    assert again["rows_total"] == 0
    # users + 2 courses + 2 topics + 2 blocks + week + day + session + todo
    assert again["skipped"] >= 5
    assert _one("SELECT count(*) AS n FROM courses")["n"] == 2


def test_malformed_selection_falls_back_to_full(client):
    """An unreadable selection field is a full restore, not a 500."""
    payload = _export(client)
    _run(WIPE)

    response = _restore(client, payload, "{oops")
    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()["ok"] is True
    assert _one("SELECT count(*) AS n FROM courses")["n"] == 2
    assert _one("SELECT count(*) AS n FROM weeks")["n"] == 1


def test_inspect_reports_a_manifest_less_zip(client):
    """No manifest.json: 200 with has_manifest False, never a crash."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("user_data.sql",
                         'COPY "courses" (id) FROM stdin;\n1\n\\.\n')

    response = _inspect(client, buffer.getvalue())
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert body["has_manifest"] is False
    assert "manifest.json" in body["manifest_error"]


def test_inspect_returns_the_selector_options(client):
    """The zip's own tree, agenda and media -- no database involved."""
    payload = _export(client)

    response = _inspect(client, payload)
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert body["has_manifest"] is True
    assert body["manifest"]["format"] == "studyflow-user-backup"

    options = body["options"]
    assert len(options["tree"]["courses"]) == 2
    assert options["agenda"]["totals"]["weeks"] == 1
    assert options["agenda"]["totals"]["sessions"] == 1
    assert "audio" in options["media"]
    assert options["estimate"]["total_bytes"] >= 0


def test_media_is_written_only_when_the_tick_says_so(client):
    """Unticked media writes zero files; ticked (plus unattributed) writes."""
    audio_dir = Path(os.environ["AUDIO_UPLOAD_FOLDER"])
    voice = audio_dir / "voice.mp3"
    voice.write_bytes(b"ID3 fake audio")

    payload = _export(client, {"courses": [1], "media": ["audio"],
                               "unattributed": True})
    _run(WIPE)
    # A fresh machine: the export already read the file, now it is gone,
    # so existence afterwards is proof the import wrote it (or did not).
    voice.unlink()

    without_media = _restore(client, payload, {"courses": [1]})
    assert without_media.status_code == 200, \
        without_media.get_data(as_text=True)
    assert without_media.get_json()["files_written"] == 0
    assert not voice.exists()

    with_media = _restore(client, payload,
                          {"media": ["audio"], "unattributed": True})
    assert with_media.status_code == 200, with_media.get_data(as_text=True)
    assert with_media.get_json()["files_written"] >= 1
    assert voice.exists()
    assert voice.read_bytes() == b"ID3 fake audio"
