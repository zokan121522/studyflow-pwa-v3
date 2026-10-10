"""Backup, restore and serving must agree on where each category lives.

Two failures this file exists for, both silent while they lasted:

1. The one-root bug: the export rooted ``files/`` at
   ``dirname(PDF_UPLOAD_FOLDER)`` while the restore rooted it at
   ``engine.data_dir()``. Different directories, so every file landed one
   level above where the app served it. The rows came back, so the restore
   reported success and the app showed every course title with empty
   content. Nothing errored.

2. The Windows bug (2026-10-06): ONE ROOT itself was wrong. Inside Docker
   all five categories are subdirectories of ``/srv/backend/uploads``; on a
   bare-metal install they live in four different places (pdfs in
   ``<repo>/uploads/pdfs``, audio in ``~/.studyflow-app/audio``, ...). The
   export's fallback guessed ``/srv/backend/uploads``, which on Windows
   resolved to ``C:\\srv\\backend\\uploads``: the restore wrote 220 files
   there, the app kept serving from its real folders, and the success print
   (cp1252) crashed the request afterwards anyway.

The invariant now: for every category, the export reads, the restore
writes and the route serves ``storage_paths.category_dir(category)`` --
under every environment shape the product actually runs in.
"""

from __future__ import annotations

import contextlib
import importlib
import os
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
BACKEND = REPO / "backend"

CATEGORIES = ("pdfs", "audio", "infographics", "images", "scraped_pdfs")

# The deploy contract, duplicated here ON PURPOSE: if storage_paths renames
# an env var without the compose file, these tests stop seeing the value
# and fall back to the default, which fails the assertion below.
ENV_VAR = {
    "pdfs": "PDF_UPLOAD_FOLDER",
    "audio": "AUDIO_UPLOAD_FOLDER",
    "infographics": "INFOGRAPHIC_UPLOAD_FOLDER",
    "images": "IMAGE_UPLOAD_FOLDER",
    "scraped_pdfs": "SCRAPED_DIR",
}

_FIRST_NAME = {
    "pdfs": "abc123.pdf",
    "audio": "voice.mp3",
    "infographics": "infographic_1.png",
    "images": "diagram.png",
    "scraped_pdfs": "scorm_export.pdf",
}


def _shape(shape: str, tmp_path: Path) -> tuple[dict, dict]:
    """(env to set, expected dir per category the env decides).

    Three shapes, chosen because they are the three the product ships in:
    ``docker`` (compose sets all five vars), ``launcher`` (launch.py's
    child_env sets PDF + SCRAPED only) and ``bare`` (the Windows scheduled
    task's studyflow_launcher.py sets nothing).
    """
    root = tmp_path / "root"
    if shape == "docker":
        expected = {c: str(root / "uploads" / c) for c in CATEGORIES}
        env = {ENV_VAR[c]: path for c, path in expected.items()}
        return env, expected
    if shape == "launcher":
        env = {
            "PDF_UPLOAD_FOLDER": str(root / "uploads" / "pdfs"),
            "SCRAPED_DIR": str(root / "scraped"),
        }
        return env, {
            "pdfs": env["PDF_UPLOAD_FOLDER"],
            "scraped_pdfs": env["SCRAPED_DIR"],
        }
    return {}, {}


def _defaults(tmp_path: Path, home: Path) -> dict:
    """The fallback each category had before storage_paths existed."""
    return {
        "pdfs": str(REPO / "uploads" / "pdfs"),
        "audio": str(home / ".studyflow-app" / "audio"),
        "infographics": str(home / ".studyflow-app" / "infographics"),
        "images": str(BACKEND / "uploads" / "images"),
        "scraped_pdfs": "/tmp/scraped_pdfs",
    }


@contextlib.contextmanager
def _fresh_modules(env: dict):
    """Reimport the touched modules with a given environment.

    Module-level constants capture the environment at import time (the
    routes do ``UPLOAD_FOLDER = category_dir('pdfs')`` at import), so
    patching os.environ afterwards would not exercise the real code path.

    ``engine`` must NOT be evicted from sys.modules: it holds process-wide
    state initialised once, and re-importing it leaks a fresh module whose
    ``active_engine()`` returns None into every later test. The environment
    is patched and the modules are reloaded on top of the existing engine.
    """
    mods = ("backup_files", "backup_user_restore", "routes.pdf", "routes.image")
    saved = dict(os.environ)
    for var in ENV_VAR.values():
        os.environ.pop(var, None)
    for mod in mods:
        sys.modules.pop(mod, None)
    os.environ.update(env)
    sys.path.insert(0, str(BACKEND))
    try:
        files = importlib.import_module("backup_files")
        restore = importlib.import_module("backup_user_restore")
        routes_pdf = importlib.import_module("routes.pdf")
        routes_image = importlib.import_module("routes.image")
        yield files, restore, routes_pdf, routes_image
    finally:
        os.environ.clear()
        os.environ.update(saved)
        for mod in mods:
            sys.modules.pop(mod, None)
        sys.path.remove(str(BACKEND))


@pytest.mark.parametrize("shape", ["docker", "launcher", "bare"])
def test_export_and_routes_agree_in_every_environment_shape(
        tmp_path, monkeypatch, shape):
    """Same directory from export, routes and storage_paths -- always."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    env, decided = _shape(shape, tmp_path)
    expected = {**_defaults(tmp_path, home), **decided}

    sys.path.insert(0, str(BACKEND))
    try:
        from storage_paths import category_dir
    finally:
        sys.path.remove(str(BACKEND))

    with _fresh_modules(env) as (files, restore, routes_pdf, routes_image):
        for category in CATEGORIES:
            base = category_dir(category)
            assert base == expected[category], (
                f"{category} resolves to {base}, expected {expected[category]}"
                f" (shape={shape})"
            )
            # Export side: the absolute path of a row's file is built on
            # that same directory.
            name = _FIRST_NAME[category]
            assert files._rel_path(category, name) == os.path.join(
                expected[category], name)
        # Serving side: the routes captured the same directories at import.
        assert routes_pdf.UPLOAD_FOLDER == expected["pdfs"]
        assert routes_image.UPLOAD_FOLDER == expected["images"]


def test_restore_scatters_each_category_into_its_own_root(tmp_path):
    """The Windows failure, end to end: five roots, not one guessed one.

    Every member must land in ITS category's directory -- and nowhere
    else. One pre-existing file doubles as the never-overwrite promise.
    """
    roots = {c: tmp_path / "roots" / c for c in CATEGORIES}
    env = {ENV_VAR[c]: str(roots[c]) for c in CATEGORIES}

    members = {
        "files/pdfs/abc123.pdf": b"%PDF-1.4 test",
        "files/audio/voice.mp3": b"ID3 overwritten?",
        "files/infographics/infographic_1.png": b"\x89PNG test",
        "files/images/diagram.png": b"\x89PNG test",
        "files/scraped_pdfs/scorm_export.pdf": b"%PDF-1.4 scorm",
    }
    # voice.mp3 already exists with different content: additive, no clobber.
    roots["audio"].mkdir(parents=True)
    (roots["audio"] / "voice.mp3").write_bytes(b"ID3 original")

    archive = tmp_path / "bk.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("manifest.json",
                    '{"format": "studyflow-user-backup", "version": 1}')
        for member, payload in members.items():
            zf.writestr(member, payload)

    with _fresh_modules(env) as (files, restore, *_):
        with zipfile.ZipFile(archive) as zf:
            written = restore._write_missing_files(zf, zf.namelist())

    assert written == 4, "only the four missing files may be written"
    for member in members:
        category, name = member.split("/")[1:]
        if name == "voice.mp3":
            assert (roots[category] / name).read_bytes() == b"ID3 original"
        else:
            assert (roots[category] / name).is_file(), (
                f"{member} did not land in {roots[category]}"
            )
        # And NOT in any other category's root, nor one level too high.
        for other, other_root in roots.items():
            if other != category:
                assert not (other_root / name).exists(), (
                    f"{member} leaked into the {other} root"
                )
    assert not (tmp_path / "pdfs").exists()
    assert not (tmp_path / "audio").exists()


def test_restore_on_a_bare_install_uses_the_app_defaults(tmp_path,
                                                          monkeypatch):
    """No env at all (the Windows scheduled task): the app's own folders."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    # Pin pdfs/images the way storage_paths computes them, without ever
    # writing into the real repository during a test.
    sys.path.insert(0, str(BACKEND))
    try:
        import storage_paths
    finally:
        sys.path.remove(str(BACKEND))
    monkeypatch.setattr(storage_paths, "_REPO_ROOT", str(tmp_path / "repo"))
    monkeypatch.setattr(storage_paths, "_BACKEND_DIR",
                        str(tmp_path / "repo" / "backend"))

    archive = tmp_path / "bk.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("files/pdfs/abc123.pdf", b"%PDF-1.4 test")
        zf.writestr("files/audio/voice.mp3", b"ID3 test")
        zf.writestr("files/infographics/infographic_1.png", b"\x89PNG test")

    with _fresh_modules({}) as (files, restore, *_):
        with zipfile.ZipFile(archive) as zf:
            written = restore._write_missing_files(zf, zf.namelist())

    assert written == 3
    assert (tmp_path / "repo" / "uploads" / "pdfs" / "abc123.pdf").is_file()
    assert (home / ".studyflow-app" / "audio" / "voice.mp3").is_file()
    assert (home / ".studyflow-app" / "infographics"
            / "infographic_1.png").is_file()
    # The bug this file exists for: NOT one directory too high.
    assert not (tmp_path / "repo" / "uploads" / "abc123.pdf").exists()
    assert not (home / "voice.mp3").exists()


def test_restore_refuses_to_escape_any_category_root(tmp_path):
    """The path-traversal guard, re-checked against EACH category root."""
    roots = {c: tmp_path / "roots" / c for c in CATEGORIES}
    env = {ENV_VAR[c]: str(roots[c]) for c in CATEGORIES}

    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("files/pdfs/../../escaped.pdf", b"nope")
        zf.writestr("files/../../../top.pdf", b"nope")
        zf.writestr("files\\..\\..\\win-escaped.pdf", b"nope")
        zf.writestr("/etc/passwd", b"nope")

    with _fresh_modules(env) as (files, restore, *_):
        with zipfile.ZipFile(archive) as zf:
            written = restore._write_missing_files(zf, zf.namelist())

    assert written == 0
    assert not (tmp_path / "escaped.pdf").exists()
    assert not (tmp_path / "top.pdf").exists()
    assert not (tmp_path / "roots" / "escaped.pdf").exists()
    for root in roots.values():
        assert list(root.glob("**/*")) == [] or not root.exists()


def test_hand_made_windows_members_and_unknown_categories(tmp_path):
    """Backslash members restore; categories this app has do not."""
    audio_root = tmp_path / "audio"
    env = {"AUDIO_UPLOAD_FOLDER": str(audio_root)}

    archive = tmp_path / "handmade.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("files\\audio\\voice.mp3", b"ID3 test")
        zf.writestr("files/videos/clip.mp4", b"\x00mp4")
        zf.writestr("files/pdfs/", b"")      # bare directory entry

    with _fresh_modules(env) as (files, restore, *_):
        with zipfile.ZipFile(archive) as zf:
            written = restore._write_missing_files(zf, zf.namelist())

    assert written == 1
    assert (audio_root / "voice.mp3").read_bytes() == b"ID3 test"
    assert not (audio_root / "videos").exists()
    assert not (audio_root / "clip.mp4").exists()


def test_export_addresses_files_relative_to_their_own_category(tmp_path):
    """Archive members are ``files/<category>/<name>`` with ``/``, no ``..``."""
    audio_root = tmp_path / "audio"
    audio_root.mkdir()
    voice = audio_root / "voice.mp3"
    voice.write_bytes(b"ID3 test")

    with _fresh_modules({"AUDIO_UPLOAD_FOLDER": str(audio_root)}) as (
            files, restore, *_):
        col = files._Collector()
        col.add(str(voice), "audio", "voice.mp3")
        assert col.files == [(str(voice), "files/audio/voice.mp3", "audio")]
        assert files.archive_rel("audio", str(voice)) == "audio/voice.mp3"

        # A file outside its own category is reported, never shipped with
        # a ".." the restore would later refuse.
        rogue = tmp_path / "rogue.mp3"
        rogue.write_bytes(b"ID3 rogue")
        col2 = files._Collector()
        col2.add(str(rogue), "audio", "rogue.mp3")
        assert col2.files == []
        assert "rogue.mp3" in col2.missing
        assert str(rogue) in col2.owned
