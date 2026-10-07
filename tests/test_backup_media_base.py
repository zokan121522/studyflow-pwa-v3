"""The restore must write media exactly where the app looks for it.

This is the regression that made a restore report success and still lose every
PDF, infographic and audio file. The archive stores paths relative to
``files/``; the export rooted them at ``dirname(PDF_UPLOAD_FOLDER)`` and the
restore re-rooted them at ``engine.data_dir()``. Those are different
directories -- the launcher points PDF_UPLOAD_FOLDER at
``<data>/uploads/pdfs`` -- so files landed one level too high.

Because the database rows came back fine, the only symptom was an app showing
every course title with empty content. Nothing errored.
"""

from __future__ import annotations

import importlib
import os
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
BACKEND = REPO / "backend"


def _fresh_modules(env: dict):
    """Reimport the backup modules with a given environment.

    Module-level constants capture the environment at import time, so simply
    patching os.environ afterwards would not exercise the real code path.

    ``engine`` must NOT be evicted from sys.modules. It holds process-wide
    state (``active_engine``) that is initialised once; re-importing it yields
    a fresh module whose ``active_engine()`` returns None, and that leaks into
    every later test in the session. Instead the environment is patched and the
    backup modules are reloaded on top of the existing engine.
    """
    saved = dict(os.environ)
    for mod in ("backup_files", "backup_user_restore"):
        sys.modules.pop(mod, None)
    os.environ.update(env)
    try:
        files = importlib.import_module("backup_files")
        restore = importlib.import_module("backup_user_restore")
        yield files, restore
    finally:
        os.environ.clear()
        os.environ.update(saved)
        for mod in ("backup_files", "backup_user_restore"):
            sys.modules.pop(mod, None)


def test_export_and_restore_share_one_media_base(tmp_path):
    """The two sides must agree, whatever the environment says."""
    data = tmp_path / "studyflow"
    for env in (
        {"PDF_UPLOAD_FOLDER": str(data / "uploads" / "pdfs")},
        {"PDF_UPLOAD_FOLDER": str(data / "pdfs")},
        {},  # unset: both must fall back identically
    ):
        base = {"STUDYFLOW_DATA_DIR": str(data), **env}
        sys.path.insert(0, str(BACKEND))
        try:
            for files, restore in _fresh_modules(base):
                assert restore._MEDIA_BASE == files.DATA_DIR, (
                    f"el restore escribe en {restore._MEDIA_BASE} pero el export "
                    f"lee de {files.DATA_DIR}: el mismo fichero acabaría en dos "
                    f"sitios distintos (env={env})"
                )
        finally:
            sys.path.remove(str(BACKEND))


def test_restore_writes_media_where_the_pdf_route_serves_it(tmp_path, monkeypatch):
    """End to end: unzip a small archive and check the resulting paths.

    Asserts on the file actually landing on disk, not on a constant, because
    the constants agreed once already while the behaviour did not.
    """
    data = tmp_path / "studyflow"
    uploads = data / "uploads"
    uploads.mkdir(parents=True)

    monkeypatch.setenv("PDF_UPLOAD_FOLDER", str(uploads / "pdfs"))
    monkeypatch.setenv("STUDYFLOW_DATA_DIR", str(data))
    sys.path.insert(0, str(BACKEND))
    try:
        files = importlib.import_module("backup_files")
        restore = importlib.import_module("backup_user_restore")

        archive = tmp_path / "bk.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("manifest.json", '{"format": "studyflow-user-backup", "version": 1}')
            zf.writestr("files/pdfs/abc123.pdf", b"%PDF-1.4 test")
            zf.writestr("files/audio/voice.mp3", b"ID3 test")
            zf.writestr("files/infographics/infographic_1.png", b"\x89PNG test")

        with zipfile.ZipFile(archive) as zf:
            written = restore._write_missing_files(zf, zf.namelist())
        assert written == 3

        # The real assertion: next to where the app serves them from.
        assert (uploads / "pdfs" / "abc123.pdf").is_file()
        assert (uploads / "audio" / "voice.mp3").is_file()
        assert (uploads / "infographics" / "infographic_1.png").is_file()

        # And NOT one level too high, which is the bug this file exists for.
        assert not (data / "pdfs" / "abc123.pdf").exists()
        assert not (data / "audio" / "voice.mp3").exists()
    finally:
        sys.path.remove(str(BACKEND))
        for mod in ("backup_files", "backup_user_restore"):
            sys.modules.pop(mod, None)


def test_restore_still_refuses_to_escape_the_media_base(tmp_path, monkeypatch):
    """The path-traversal guard must survive the base change."""
    data = tmp_path / "studyflow"
    (data / "uploads").mkdir(parents=True)
    monkeypatch.setenv("PDF_UPLOAD_FOLDER", str(data / "uploads" / "pdfs"))
    monkeypatch.setenv("STUDYFLOW_DATA_DIR", str(data))

    sys.path.insert(0, str(BACKEND))
    try:
        restore = importlib.import_module("backup_user_restore")
        archive = tmp_path / "evil.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("files/../../../escaped.pdf", b"nope")

        with zipfile.ZipFile(archive) as zf:
            written = restore._write_missing_files(zf, zf.namelist())
        assert written == 0
        assert not (tmp_path / "escaped.pdf").exists()
    finally:
        sys.path.remove(str(BACKEND))
        for mod in ("backup_files", "backup_user_restore"):
            sys.modules.pop(mod, None)
