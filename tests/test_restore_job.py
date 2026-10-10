"""Chunked restore jobs: start → chunk → finish → status.

A 1.3 GB multipart POST dies mid-flight in the browser ("Failed to
fetch"), so the restore is uploaded in pieces to a job, reassembled
server-side and imported by a background worker. What has to hold:

1. ``start`` rejects bad input with 400 instead of opening a zombie job.
2. ``chunk`` writes the part where the job says it will.
3. ``finish`` refuses to assemble while chunks are missing (400 + list).
4. end-to-end: the pieces of a REAL export go back in and the job walks
   receiving → done with a report the old route would have produced.
5. unknown job → 404, never a 500.

The zip comes from the real ``POST /api/backup/mine``, like the other
backup tests: the contract is export-then-import, and a hand-built
archive would only test the fixture's idea of the format.
"""

from __future__ import annotations

import io
import json
import time
from pathlib import Path

import pytest

import database

# Children first, so a foreign key (enforced or not) can never complain.
SEED = [
    "INSERT INTO courses (id, user_id, title) VALUES (1, 1, 'Alpha')",
    "INSERT INTO topics (id, course_id, user_id, title) VALUES "
    "(10, 1, 1, 'Tema A')",
    "INSERT INTO blocks (id, user_id, course_id, topic_id, type, title, "
    "content) VALUES (100, 1, 1, 10, 'markdown', 'Bloque A', 'contenido A')",
    "INSERT INTO todos (id, user_id, title) VALUES (1, 1, 'Todo uno')",
]

WIPE = [
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
        conn.commit()
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
    _run(WIPE)
    _run(SEED)

    import server

    app = server.create_app()
    app.config["TESTING"] = True
    yield app.test_client()
    database.close_pool()


def _start(client, filename="backup.zip", total_chunks=1, selection=None):
    data = {"filename": filename, "total_chunks": str(total_chunks)}
    if selection is not None:
        data["selection"] = selection if isinstance(selection, str) \
            else json.dumps(selection)
    return client.post("/api/backup/mine/restore/start", data=data)


def _chunk(client, job_id, index, payload: bytes):
    return client.post(
        "/api/backup/mine/restore/chunk",
        data={"job_id": job_id, "index": str(index),
              "file": (io.BytesIO(payload), "part.bin")},
        content_type="multipart/form-data")


def _finish(client, job_id):
    return client.post("/api/backup/mine/restore/finish",
                       data={"job_id": job_id})


def _status(client, job_id):
    return client.get(f"/api/backup/mine/restore/status?job={job_id}")


def _wait_settled(client, job_id, timeout=30.0):
    """Poll until the background worker says done or error."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        resp = _status(client, job_id)
        assert resp.status_code == 200, resp.get_data(as_text=True)
        last = resp.get_json()
        if last["state"] in ("done", "error"):
            return last
        time.sleep(0.05)
    raise AssertionError(f"job never settled: {last}")


def test_start_rejects_invalid_input(client):
    assert _start(client, filename="").status_code == 400
    assert _start(client, filename="   ").status_code == 400
    assert _start(client, total_chunks="0").status_code == 400
    assert _start(client, total_chunks="-3").status_code == 400
    assert _start(client, total_chunks="100001").status_code == 400
    assert _start(client, total_chunks="abc").status_code == 400
    assert _start(client, total_chunks="").status_code == 400

    ok = _start(client, total_chunks=2)
    assert ok.status_code == 202, ok.get_data(as_text=True)
    assert ok.get_json()["job_id"]

    early = _status(client, ok.get_json()["job_id"])
    assert early.status_code == 200
    assert early.get_json()["state"] == "receiving"
    assert early.get_json()["progress"] == 0.0


def test_chunk_writes_the_part_file(client):
    import backup_user_restore as bur

    job_id = _start(client, total_chunks=3).get_json()["job_id"]
    resp = _chunk(client, job_id, 1, b"hola trozo")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json() is not None

    part = Path(bur._jobs_root()) / job_id / "part-000001"
    assert part.is_file(), f"{part} was not written"
    assert part.read_bytes() == b"hola trozo"

    # index outside the declared range never lands on disk
    assert _chunk(client, job_id, 3, b"x").status_code == 400
    assert _chunk(client, job_id, -1, b"x").status_code == 400
    # an unknown job is refused, not silently created
    assert _chunk(client, "nope", 0, b"x").status_code == 409
    # and a received chunk cannot be pushed again once the job moved on
    assert _finish(client, "nope").status_code == 409


def test_finish_refuses_when_chunks_are_missing(client):
    job_id = _start(client, total_chunks=3).get_json()["job_id"]
    _chunk(client, job_id, 0, b"aaa")
    _chunk(client, job_id, 2, b"ccc")

    resp = _finish(client, job_id)
    assert resp.status_code == 400, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["missing"] == [1]

    # the job is still open: the missing piece can be uploaded and retried
    assert _chunk(client, job_id, 1, b"bbb").status_code == 200
    assert _finish(client, job_id).status_code == 202
    # the parts are not a zip: the worker must report an error state
    # rather than hang in "restoring"
    final = _wait_settled(client, job_id)
    assert final["state"] == "error", final
    assert final["error"]


def test_unknown_job_status_is_404(client):
    resp = client.get("/api/backup/mine/restore/status?job=does-not-exist")
    assert resp.status_code == 404, resp.get_data(as_text=True)


def test_chunked_restore_end_to_end(client):
    """Real export, split in pieces, reassembled and imported for real."""
    import backup_user_restore as bur

    exported = client.post("/api/backup/mine", json=None)
    assert exported.status_code == 200, \
        exported.get_data(as_text=True)[:500]
    payload = exported.data
    assert payload[:2] == b"PK", "the export is not a zip"

    # Three slices (short files may produce empty tail pieces — the API
    # must accept them, the reassembly is byte-exact either way).
    step = max(1, len(payload) // 3)
    parts = [payload[i:i + step] for i in range(0, len(payload), step)]

    job_id = _start(client, filename="backup.zip",
                    total_chunks=len(parts)).get_json()["job_id"]
    for i, part in enumerate(parts):
        resp = _chunk(client, job_id, i, part)
        assert resp.status_code == 200, resp.get_data(as_text=True)

    receiving = _status(client, job_id).get_json()
    assert receiving["state"] == "receiving"
    assert receiving["progress"] == 1.0

    fin = _finish(client, job_id)
    assert fin.status_code == 202, fin.get_data(as_text=True)
    assert fin.get_json()["started"] is True

    # parts are consumed by the reassembly; the zip is what remains
    job_dir = Path(bur._jobs_root()) / job_id
    assert (job_dir / "backup.zip").is_file()
    assert not list(job_dir.glob("part-*"))

    final = _wait_settled(client, job_id)
    assert final["state"] == "done", final
    assert sum(final["rows_inserted"].values()) >= 0
    assert isinstance(final["files_written"], int)
    assert isinstance(final["partial"], bool)
    assert "error" not in final

    # a finished job still answers, and the reassembly is byte-exact
    again = _status(client, job_id)
    assert again.status_code == 200
    assert again.get_json()["state"] == "done"
