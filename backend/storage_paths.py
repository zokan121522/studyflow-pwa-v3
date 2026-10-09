# backend/storage_paths.py
"""Single source of truth for where each media category lives on disk.

Why this module exists
----------------------
The personal backup stores media as ``files/<category>/<name>`` -- a path
relative to the CATEGORY directory, never an absolute one, so a zip made on
macOS restores on Windows and the other way round. That promise only holds
if the export, the restore and the serving routes all agree on where each
category lives. They used to disagree: every module read its own env var
with its own fallback, and backup_files assumed all five categories were
subdirectories of ONE root. That is true inside Docker (compose sets the
five env vars under /srv/backend/uploads) and false on a bare-metal
install, where audio lives in ~/.studyflow-app/audio while pdfs live in
<repo>/uploads/pdfs. The first restore on Windows therefore wrote all 220
files to C:\\srv\\backend\\uploads while the app served from four other
places: the rows came back and every file looked lost.

The rules
---------
* the env var wins -- the deploy decides, as it always did;
* the fallback is EXACTLY the path the owning module used before this
  module existed, so data already on disk is never orphaned;
* the env is read at CALL time, so tests and future launchers can redirect
  a category without reimporting the world;
* nothing from the app is imported here: this is the bottom of the
  dependency graph, which is what lets routes, backup, importers and tests
  all depend on it without ever forming a cycle.
"""
from __future__ import annotations

import os
from pathlib import Path

_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_BACKEND_DIR)

# category -> the env var a deploy uses to relocate that category.
_ENV_OF = {
    "pdfs": "PDF_UPLOAD_FOLDER",
    "audio": "AUDIO_UPLOAD_FOLDER",
    "infographics": "INFOGRAPHIC_UPLOAD_FOLDER",
    "images": "IMAGE_UPLOAD_FOLDER",
    "scraped_pdfs": "SCRAPED_DIR",
}


def category_dir(category: str) -> str:
    """Absolute directory where one media category lives.

    Call-time resolution on purpose: an unset var must fall back the way the
    owning module always fell back, and tests must be able to point a whole
    category at a tmp_path by patching the environment alone.
    """
    if category not in _ENV_OF:
        raise KeyError(f"unknown media category: {category!r}")
    env = os.environ.get(_ENV_OF[category])
    if env:
        return env
    if category == "pdfs":
        # routes/pdf.py's own fallback: repo-local and writable without
        # root, unlike the '/app/...' path that only exists in Docker.
        return os.path.join(_REPO_ROOT, "uploads", "pdfs")
    if category == "audio":
        return str(Path.home() / ".studyflow-app" / "audio")
    if category == "infographics":
        return str(Path.home() / ".studyflow-app" / "infographics")
    if category == "images":
        # routes/image.py's own fallback: backend/uploads/images.
        return os.path.join(_BACKEND_DIR, "uploads", "images")
    # scraped_pdfs: scorm_import.py's own fallback. /tmp is where the
    # scraper has always written on bare metal; Docker sets SCRAPED_DIR.
    return "/tmp/scraped_pdfs"


def resolve_media(category: str, stored: str) -> str:
    """Local path of a media file whose row persisted an absolute path.

    Rows store whatever absolute path the WRITING machine had. A personal
    backup restored on another OS therefore points at a directory that does
    not exist here (``C:\\srv\\...`` on macOS), even though the export and
    the restore place every file under THIS install's
    ``category_dir(category)`` with the stored basename.

    Prefer the stored path while it still resolves -- the same machine keeps
    its layout -- and fall back to the local category dir, which is exactly
    where the restore put the file. Backslashes are normalised first, so a
    Windows path resolves on a POSIX install too. Only the basename survives
    the fallback, so a row can never escape its own category dir. A falsy
    ``stored`` comes back unchanged, so callers keep their own "no path"
    branch.
    """
    if not stored:
        return stored or ""
    if os.path.exists(stored):
        return stored
    name = os.path.basename(stored.replace("\\", "/"))
    if not name:
        return stored
    return os.path.join(category_dir(category), name)
