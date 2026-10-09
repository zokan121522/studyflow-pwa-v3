"""A personal backup must restore media ROWS, not only media FILES.

The export writes each ``pdfs``/``images`` file into ``files/<cat>/<name>``
and each row into ``user_data.sql``. The restore used to write the files
into ``storage_paths.category_dir(category)`` but insert ZERO rows, so the
app had nothing to resolve: ``resolve_media`` was never called, the file
sat on disk invisible and every PDF/image looked lost.

Root cause: ``_import_tables`` built its archive-id -> target-id remap map
only for the natural-key tree::

    maps = {t: {} for t in _NATURAL_KEY_TABLES}   # courses/topics/blocks

but ``_restore_rows_per_row`` also runs for ``_REMAP_FK_TABLES``
(pdfs/images/quiz_*). Writing ``maps["pdfs"][archive_id]`` raised
``KeyError('pdfs')``; the per-table savepoint rolled just that INSERT back
and the table was reported in ``failed_tables`` -- a partial failure that
still returned HTTP 200, so the files were written and the rows silently
were not.

This test drives the real HTTP round-trip end to end -- seed a source
install, ``POST /api/backup/mine``, restore the produced zip into a SECOND
fresh install, then serve through ``resolve_media`` -- and asserts the two
halves agree. The engine caches its choice process-wide, so source and
destination run in separate ``sys.executable`` children (as the original
diagnosis did); each child gets its own ``STUDYFLOW_DATA_DIR``, a throwaway
SQLite database and throwaway media roots. The developer's real database
and data dir are never touched.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"

_MEDIA_VARS = (
    "PDF_UPLOAD_FOLDER",
    "AUDIO_UPLOAD_FOLDER",
    "INFOGRAPHIC_UPLOAD_FOLDER",
    "IMAGE_UPLOAD_FOLDER",
    "SCRAPED_DIR",
)

# ── SOURCE child: seed rows + real media files, export via the real route ──
# The two stored paths deliberately point at OTHER machines (a Linux /srv
# path and a Windows C:\ path) so the restore is forced to fall back to the
# category dir it wrote the file into, never the originating absolute path.
_SOURCE = r'''
import json, os, sys, struct, zlib

REPO = sys.argv[1]
ZIP = os.environ["SF_ZIP"]
OUT = os.environ["SF_OUT"]
sys.path[:0] = [os.path.join(REPO, "backend"), REPO]

import database as db
db.init_db()


def run(sql, params=()):
    conn = db.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def one(sql, params=()):
    conn = db.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def png_1x1():
    def chunk(t, d):
        c = t + d
        return struct.pack(">I", len(d)) + c + struct.pack(">I", zlib.crc32(c) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00\xff"))
            + chunk(b"IEND", b""))


PDF_BYTES = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
             b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
             b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
             b"trailer<</Root 1 0 R>>\n%%EOF\n")
PNG_BYTES = png_1x1()

pdf_dir = os.environ["PDF_UPLOAD_FOLDER"]
img_dir = os.environ["IMAGE_UPLOAD_FOLDER"]
os.makedirs(pdf_dir, exist_ok=True)
os.makedirs(img_dir, exist_ok=True)
open(os.path.join(pdf_dir, "srcpdf01.pdf"), "wb").write(PDF_BYTES)
open(os.path.join(img_dir, "srcimg01.png"), "wb").write(PNG_BYTES)

run("INSERT INTO courses (id, user_id, title) VALUES (1, 1, 'Alpha')")
run("INSERT INTO topics (id, course_id, user_id, title) VALUES (10, 1, 1, 'Tema A')")
run("INSERT INTO blocks (id, user_id, course_id, topic_id, type, title, content) "
    "VALUES (100, 1, 1, 10, 'markdown', 'Bloque A', 'contenido A')")
run("INSERT INTO pdfs (id, user_id, course_id, topic_id, filename, original_name, "
    "file_size, storage_path) VALUES (7, 1, 1, 10, 'srcpdf01.pdf', 'orig.pdf', %s, %s)",
    (len(PDF_BYTES), "/srv/backend/uploads/pdfs/srcpdf01.pdf"))
run("INSERT INTO images (id, user_id, course_id, topic_id, filename, original_name, "
    "mime, file_size, storage_path) VALUES (9, 1, 1, 10, 'srcimg01.png', 'orig.png', "
    "'image/png', %s, %s)",
    (len(PNG_BYTES), "C:\\srv\\backend\\uploads\\images\\srcimg01.png"))

import server
app = server.create_app()
app.config["TESTING"] = True
resp = app.test_client().post("/api/backup/mine", json={})
if resp.status_code != 200:
    sys.stderr.write(resp.get_data(as_text=True)[:1500])
    sys.exit(3)
open(ZIP, "wb").write(resp.data)

import zipfile
names = zipfile.ZipFile(ZIP).namelist()
json.dump({
    "pdfs": one("SELECT COUNT(*) c FROM pdfs")["c"],
    "images": one("SELECT COUNT(*) c FROM images")["c"],
    "media_entries": sorted(n for n in names if n.startswith("files/")),
}, open(OUT, "w"))
'''

# ── DESTINATION child: a brand-new install, restore the zip, then serve ────
_DEST = r'''
import io, json, os, sys

REPO = sys.argv[1]
ZIP = os.environ["SF_ZIP"]
OUT = os.environ["SF_OUT"]
sys.path[:0] = [os.path.join(REPO, "backend"), REPO]

import database as db
db.init_db()


def one(sql, params=()):
    conn = db.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def count(table):
    return one("SELECT COUNT(*) c FROM " + table)["c"]


import server
app = server.create_app()
app.config["TESTING"] = True
client = app.test_client()

payload = open(ZIP, "rb").read()
resp = client.post("/api/backup/mine/restore",
                   data={"file": (io.BytesIO(payload), "backup.zip")},
                   content_type="multipart/form-data")
try:
    body = resp.get_json()
except Exception:
    body = None

from storage_paths import category_dir, resolve_media

pdf_dir = category_dir("pdfs")
img_dir = category_dir("images")
pdf_file = os.path.join(pdf_dir, "srcpdf01.pdf")
img_file = os.path.join(img_dir, "srcimg01.png")
pdf_row = one("SELECT id, user_id, filename, storage_path FROM pdfs")
img_row = one("SELECT id, user_id, filename, storage_path FROM images")

json.dump({
    "http_status": resp.status_code,
    "body": body,
    "pdfs": count("pdfs"),
    "images": count("images"),
    "blocks": count("blocks"),
    "pdf_row": pdf_row,
    "img_row": img_row,
    "pdf_file": pdf_file,
    "img_file": img_file,
    "pdf_file_exists": os.path.exists(pdf_file),
    "img_file_exists": os.path.exists(img_file),
    "pdf_resolved": resolve_media("pdfs", pdf_row["storage_path"]) if pdf_row else "",
    "img_resolved": resolve_media("images", img_row["storage_path"]) if img_row else "",
    "pdf_dir": pdf_dir,
    "img_dir": img_dir,
}, open(OUT, "w"))
'''


def _run_child(script, data_dir, media, zip_path, out_path):
    """Run one install in its own process, so the engine picks its own DB."""
    env = dict(os.environ)
    for var in _MEDIA_VARS:
        env.pop(var, None)
    env.pop("DATABASE_URL", None)          # force the throwaway SQLite file
    env["STUDYFLOW_DB_ENGINE"] = "sqlite"
    env["STUDYFLOW_DATA_DIR"] = str(data_dir)
    env["SF_ZIP"] = str(zip_path)
    env["SF_OUT"] = str(out_path)
    env.update(media)
    proc = subprocess.run(
        [sys.executable, "-c", script, str(REPO)],
        env=env, cwd=str(REPO), capture_output=True, text=True)
    assert proc.returncode == 0, (
        f"child failed rc={proc.returncode}\n"
        f"STDOUT:\n{proc.stdout[-2000:]}\nSTDERR:\n{proc.stderr[-3000:]}"
    )
    return json.loads(out_path.read_text())


def test_media_rows_and_files_survive_a_roundtrip(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    zip_path = tmp_path / "backup.zip"

    src_media = {var: str(src / "uploads" / var.lower())
                 for var in _MEDIA_VARS}
    dst_media = {var: str(dst / "uploads" / var.lower())
                 for var in _MEDIA_VARS}

    exported = _run_child(_SOURCE, src, src_media, zip_path,
                          tmp_path / "export.json")

    # The export is the control: if it stopped shipping the media files the
    # restore assertions below would be vacuous.
    assert "files/pdfs/srcpdf01.pdf" in exported["media_entries"]
    assert "files/images/srcimg01.png" in exported["media_entries"]
    assert exported["pdfs"] == 1 and exported["images"] == 1

    restored = _run_child(_DEST, dst, dst_media, zip_path,
                          tmp_path / "restore.json")

    assert restored["http_status"] == 200, restored["body"]
    body = restored["body"]
    assert body["failed_tables"] == [], body
    assert "pdfs" not in body["failed_tables"]
    assert "images" not in body["failed_tables"]

    # The bug: files landed on disk but no row was inserted to serve them.
    assert restored["pdfs"] == 1, restored
    assert restored["images"] == 1, restored

    # Both halves of the media must now be visible to the app.
    assert restored["pdf_file_exists"], restored
    assert restored["img_file_exists"], restored
    assert os.path.exists(restored["pdf_resolved"]), restored
    assert os.path.exists(restored["img_resolved"]), restored
    # Resolved through the restore install's own category dir, not the
    # originating machine's absolute path.
    assert restored["pdf_resolved"].startswith(restored["pdf_dir"])
    assert restored["img_resolved"].startswith(restored["img_dir"])
    assert restored["pdf_row"]["user_id"] == 1
    assert restored["img_row"]["user_id"] == 1
