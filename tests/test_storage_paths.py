"""storage_paths is the single media-folder resolver; nobody may guess.

The Windows restore failure (2026-10-06) was five independent guesses for
five folders: each module read its own env var with its own fallback, and
the backup assumed all five shared one root (true only in Docker). On the
ASUS laptop that wrote 220 restored files into ``C:\\srv\\backend\\uploads``
while the app served audio, infographics and PDFs from three other
directories -- and the rows came back fine, so nothing looked broken until
no media could be found.

Two layers of defence, both asserted here:

* **Source gates**: every module that owns a media folder must DELEGATE to
  ``storage_paths.category_dir`` and must not re-state its own env-or-
  fallback resolution. A regression reintroduces a second guess, and these
  greps fail before the second guess can drift.
* **Runtime behaviour**: the env var wins, the fallback is the path the
  owning module always used, and the env is read at call time.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path[:0] = [str(BACKEND), str(ROOT)]

# relpath -> (must contain, must not contain)
GATES = {
    "backend/routes/pdf.py": (
        ["category_dir('pdfs')", "resolve_media"],
        ["_REPO_ROOT", "'PDF_UPLOAD_FOLDER', os.path"],
    ),
    "backend/routes/image.py": (
        ["category_dir('images')", "resolve_media"],
        ["'IMAGE_UPLOAD_FOLDER'"],
    ),
    "backend/ai/notebooklm/audio.py": (
        ['category_dir("audio")'],
        ['"AUDIO_UPLOAD_FOLDER"'],
    ),
    "backend/ai/notebooklm/infographic.py": (
        ['category_dir("infographics")'],
        ['"INFOGRAPHIC_UPLOAD_FOLDER"'],
    ),
    "backend/scorm_import.py": (
        ['category_dir("scraped_pdfs")'],
        ['"SCRAPED_DIR"'],
    ),
    "backend/pdf_resources.py": (
        ['category_dir("pdfs")'],
        ['"/app/uploads/pdfs"'],
    ),
    "backend/ai/utils.py": (
        ['category_dir("pdfs")'],
        ['"PDF_UPLOAD_FOLDER"'],
    ),
    "backend/v2_import.py": (
        ["category_dir"],
        ["os.environ.get"],
    ),
    "backend/backup_files.py": (
        ["from storage_paths import category_dir", "archive_rel"],
        ["uploads_root", "DATA_DIR"],
    ),
    "backend/backup_user_restore.py": (
        ["from storage_paths import category_dir", "FILE_CATEGORIES"],
        ["uploads_root", "_MEDIA_BASE"],
    ),
    "backend/backup_user.py": (
        ["archive_rel"],
        ["relpath(p, DATA_DIR)"],
    ),
    # The cp1252 guard: a print must never again be able to turn a
    # committed restore into a 500 (Windows locale encoding has no ✅).
    "launcher/serve.py": (
        ['reconfigure(encoding="utf-8"'],
        [],
    ),
}


@pytest.mark.parametrize("relpath, gates", sorted(GATES.items()),
                         ids=[g[0] for g in sorted(GATES.items())])
def test_nobody_guesses_a_media_folder(relpath, gates):
    """The module delegates to storage_paths and states no fallback."""
    must, must_not = gates
    src = (ROOT / relpath).read_text(encoding="utf-8")
    for token in must:
        assert token in src, f"{relpath}: debe delegar en storage_paths ({token!r})"
    for token in must_not:
        assert token not in src, (
            f"{relpath}: {token!r} vuelve a adivinar una carpeta; "
            f"usa storage_paths.category_dir"
        )


def test_category_dir_env_var_wins(monkeypatch, tmp_path):
    """The deploy decides: every env var relocates its category."""
    import storage_paths

    for category, var in storage_paths._ENV_OF.items():
        target = str(tmp_path / category)
        monkeypatch.setenv(var, target)
        assert storage_paths.category_dir(category) == target
        monkeypatch.delenv(var)


def test_category_dir_reads_the_environment_at_call_time(monkeypatch, tmp_path):
    """A redirect must take effect without reimporting anything."""
    import storage_paths

    default = storage_paths.category_dir("audio")
    target = str(tmp_path / "audio")
    monkeypatch.setenv("AUDIO_UPLOAD_FOLDER", target)
    assert storage_paths.category_dir("audio") == target
    assert storage_paths.category_dir("audio") != default


def test_category_dir_fallbacks_are_the_owning_modules_defaults(
        monkeypatch, tmp_path):
    """Unset env -> exactly the path each module used before storage_paths."""
    import storage_paths

    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(storage_paths, "_REPO_ROOT", str(tmp_path / "repo"))
    monkeypatch.setattr(storage_paths, "_BACKEND_DIR",
                        str(tmp_path / "repo" / "backend"))

    assert storage_paths.category_dir("pdfs") == str(
        tmp_path / "repo" / "uploads" / "pdfs")
    assert storage_paths.category_dir("audio") == str(
        home / ".studyflow-app" / "audio")
    assert storage_paths.category_dir("infographics") == str(
        home / ".studyflow-app" / "infographics")
    assert storage_paths.category_dir("images") == str(
        tmp_path / "repo" / "backend" / "uploads" / "images")
    assert storage_paths.category_dir("scraped_pdfs") == "/tmp/scraped_pdfs"


def test_category_dir_rejects_unknown_categories():
    import storage_paths

    with pytest.raises(KeyError):
        storage_paths.category_dir("videos")


def test_resolve_media_prefers_the_stored_path_while_it_exists(tmp_path,
                                                              monkeypatch):
    """Same machine: the absolute path the row holds is still the file."""
    import storage_paths

    monkeypatch.setenv("IMAGE_UPLOAD_FOLDER", str(tmp_path / "images"))
    local = tmp_path / "images" / "diagram.png"
    local.parent.mkdir(parents=True)
    local.write_bytes(b"\x89PNG")

    assert storage_paths.resolve_media("images", str(local)) == str(local)


def test_resolve_media_falls_back_to_the_local_category_dir(tmp_path,
                                                           monkeypatch):
    """Cross-OS restore: the Windows path is dead, the basename is not."""
    import storage_paths

    root = tmp_path / "images"
    root.mkdir()
    (root / "diagram.png").write_bytes(b"\x89PNG")
    monkeypatch.setenv("IMAGE_UPLOAD_FOLDER", str(root))

    stale = "C:\\srv\\backend\\uploads\\images\\diagram.png"
    assert storage_paths.resolve_media("images", stale) == str(
        root / "diagram.png")


def test_resolve_media_never_escapes_its_category_dir(tmp_path, monkeypatch):
    """Only the basename survives; a traversal in the row is defused."""
    import storage_paths

    root = tmp_path / "images"
    monkeypatch.setenv("IMAGE_UPLOAD_FOLDER", str(root))

    out = storage_paths.resolve_media(
        "images", "/srv/images/../../../etc/passwd")
    assert out == str(root / "passwd")


def test_resolve_media_keeps_a_missing_path_as_the_caller_sent_it():
    """No path at all stays falsy: callers keep their own branch."""
    import storage_paths

    assert storage_paths.resolve_media("images", "") == ""
    assert storage_paths.resolve_media("images", None) == ""
