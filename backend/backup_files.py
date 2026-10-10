"""Selection-aware file resolution for the per-user backup.

Extracted from backup_user.py, which was at the edge of the 500-line
ceiling and would have crossed it.

This is a rewrite of the attribution layer, not a copy of it. hub found its
files through columns on `blocks` that v3 does not have; v3 instead reaches
every media category from a table that also carries its course and topic. The
subtleties below are therefore the same two hub had to solve -- selection
versus unattributed, and mine versus anybody else's -- but the sources are
v3's own. See the banner above _collect_owned for what moved and why.

THE SUBTLE PART
---------------
Scanning the media directory for files nothing claimed used to be a
straightforward "these are orphans, report them". Once a selection is in
play that reasoning breaks: a file belonging to a subject the user simply
did not tick is not unattributed, it is *excluded by choice*, and if those
two were conflated then ticking the "unattributed" checkbox would hand
back the whole account — every gigabyte of it.

So unattributed is defined against what the USER owns, never against what
the selection kept:

    owned     = files reachable from any of the user's rows (unfiltered)
    selected  = files reachable from the rows in scope
    excluded  = owned - selected        -> not shipped, not reported
    unattributed = on disk - owned      -> genuinely unclaimable
"""
import os
import re

import backup_db as db
from backup_selection import MEDIA_DIRS
from storage_paths import category_dir

# WHERE each category lives is no longer guessed here: storage_paths is the
# one resolver consulted by the export, the restore AND the serving routes,
# so "the file the backup stored" and "the file the app serves" end up as
# the same path on every operating system. The old model -- one uploads
# root, every category a subdirectory of it -- held only inside Docker; on
# a bare-metal install it aimed the export at C:\srv\backend\uploads on
# Windows while the app served from four different roots. This module now
# only maps vocabularies (selection term <-> archive category <-> folder)
# and resolves one file at a time against storage_paths.


FILE_CATEGORIES = {
    "pdfs": "PDF subidos (asignaturas)",
    "audio": "Audio generado (TTS)",
    "infographics": "Infografías generadas",
    "images": "Imágenes de bloques",
    "scraped_pdfs": "PDFs descargados (SCORM)",
}

# Storage names are `<uuidhex>.pdf` / `.png`, plus generated
# `annotated_<uuid>_<stamp>.pdf` and `infographic_<ai_tasks.id>.png`, so the
# patterns stay permissive about the shape and let the filesystem decide
# whether a match is real. An over-tight regex is how files go missing
# silently.
PDF_RE = re.compile(r"([A-Za-z0-9_.-]+\.pdf)", re.IGNORECASE)
AUDIO_RE = re.compile(r"([A-Za-z0-9_-]+\.(?:mp3|wav|m4a|ogg|opus|aac|flac))",
                      re.IGNORECASE)
IMAGE_RE = re.compile(r"([A-Za-z0-9_.-]+\.(?:png|jpg|jpeg|gif|webp|svg))",
                      re.IGNORECASE)
INFOGRAPHIC_RE = re.compile(r"(infographic_[A-Za-z0-9_-]+\.png)", re.IGNORECASE)

# v3 stores a served URL in ai_tasks.result_content, not a disk path, and two
# routes carry audio (/api/audio/serve/ for TTS, /api/ai/notebooklm/audio/
# for NotebookLM) with different filename shapes. Taking the basename of
# anything audio-shaped is what makes both resolve to the same file.
#
# Selection vocabulary -> internal category, the plural names used inside the
# archive. FOLDER_OF below is the second half of that same mapping.
CATEGORY_OF = {
    "pdf": "pdfs",
    "audio": "audio",
    "infographic": "infographics",
    "image": "images",
    "scraped_pdf": "scraped_pdfs",
}

# Internal category -> the uploads subdirectory it lives in. This is the ONLY
# place the plural names are tied to folders.
FOLDER_OF = {
    "pdfs": "pdfs",
    "audio": "audio",
    "infographics": "infographics",
    "images": "images",
    "scraped_pdfs": "scraped_pdfs",
}

# Folder -> category, driving the disk scan and the bucketing of files found
# there.
DIR_CATEGORY = {folder: cat for cat, folder in FOLDER_OF.items()}

# The three mappings above are the same fact written three times, and getting
# one wrong is silent: a mismatch shows up as a KeyError at export time, or
# worse, as files landing in the wrong archive folder. Two vocabularies that
# look alike (selection says `infographic`, the archive says `infographics`)
# is precisely how that happens, so the agreement is asserted rather than
# trusted.
assert set(MEDIA_DIRS) == set(CATEGORY_OF), "vocabulario de media desalineado"
assert {CATEGORY_OF[k]: v for k, v in MEDIA_DIRS.items()} == FOLDER_OF, \
    "carpeta de media desalineada respecto a la categoría interna"
assert set(FILE_CATEGORIES) == set(FOLDER_OF), \
    "categorías de fichero desalineadas"


def _rel_path(category: str, filename: str) -> str:
    """Absolute path of a category's file, or "" when the name is unusable.

    Every database-sourced filename goes through here. An empty result is not
    an error to swallow: the collector hands it to _Collector.add as a missing
    reference, which puts it in the manifest where the user can see that a row
    names a file this machine cannot locate. Dropping it silently would make
    the archive look complete.
    """
    name = _safe_name(filename)
    if name is None:
        return ""
    return os.path.join(category_dir(category), name)


def archive_rel(category: str, abs_path: str) -> str:
    """``<category>/<name>`` as the manifest lists it, always with ``/``.

    Built against storage_paths.category_dir -- the same base the archive
    stores its files relative to -- so the manifest listing cannot drift
    from the layout the restore writes. On Docker this is byte-identical to
    the old relpath-against-the-uploads-root listing; on a bare-metal
    install it replaces junk like ``../.studyflow-app/audio/x.mp3`` with a
    path that actually sits inside the media tree.
    """
    rel = os.path.relpath(abs_path, category_dir(category)).replace(os.sep, "/")
    return f"{category}/{rel}"


class _Collector:
    """Accumulates files, de-duplicating by absolute path.

    `wanted` is the set of internal categories to keep ("pdfs", "audio",
    "infographics"). A file of an unchecked category is still recorded as
    owned — otherwise it would show up later as unattributable, which would
    be a lie — but it is not shipped.
    """

    def __init__(self, wanted=None):
        self.wanted = set(wanted) if wanted is not None else set(
            CATEGORY_OF.values())
        self.files = []
        self.missing = []
        self.owned = set()      # every path the user's rows can reach
        self.claimed = set()    # subset actually included

    def add(self, abs_path, category, original, take: bool = True):
        if not abs_path or not os.path.isfile(abs_path):
            if original:
                self.missing.append(original)
            return
        self.owned.add(abs_path)
        if not take or category not in self.wanted or abs_path in self.claimed:
            return
        # The archive member is ``files/<category>/<name>`` with "/" on every
        # OS (the zip spec), rooted at this category's own directory -- the
        # same one the restore writes to and the routes serve from.
        rel = os.path.relpath(abs_path, category_dir(category)).replace(
            os.sep, "/")
        if rel.startswith(".."):
            # A file outside its own category cannot be addressed in the
            # archive without "..". Report it instead of shipping a member
            # the restore would refuse, and never drop it silently.
            if original:
                self.missing.append(original)
            return
        self.claimed.add(abs_path)
        self.files.append((abs_path, f"files/{category}/{rel}", category))


def resolve_files(conn, user_id: str, sel=None, scope=None):
    """Return (files, missing, unattributed, excluded) for the user.

    `scope` is the {table: fragment} mapping built by _topic_filter, or
    None for a full backup, which must behave exactly as it did before
    selection existed.
    """
    wanted = None
    if sel is not None and not sel.is_everything:
        wanted = {CATEGORY_OF[m] for m in sel.media if m in CATEGORY_OF}

    col = _Collector(wanted)
    _collect_owned(conn, user_id, col, scope)

    if sel is not None and not sel.is_everything:
        # Everything the user owns but did not select. Reported as
        # excluded, never as unattributed, and never shipped.
        full = _Collector()
        _collect_owned(conn, user_id, full, None)
        excluded = sorted(full.owned - col.claimed)
    else:
        excluded = []

    foreign = _claimed_by_others(conn, user_id)
    unattributed = _scan_unclaimed(col.owned, foreign)

    # The bucket only ships when it was ticked. Until then it is reported
    # and left on disk, which is what the unchecked default promises.
    if sel is not None and sel.unattributed and not sel.is_everything:
        for path in unattributed:
            col.add(path, _category_of_disk_file(path), None)

    return col.files, col.missing, unattributed, excluded


def _frag(scope, table):
    """The scope fragment for one table, or None when unscoped.

    No special-casing of empty id sets is needed: `lit` renders those as
    `(NULL)`, so the fragment stays valid SQL that matches nothing rather
    than a truncated `IN ()`.
    """
    if not scope:
        return None
    return scope.get(table) or None


# ═══════════════════════════════════════════════════════════════════
# Per-source collection
# ═══════════════════════════════════════════════════════════════════
#
# WHAT CHANGED FROM HUB, AND WHY IT IS NOT A DETAIL
# ------------------------------------------------
# hub reached two of its three media categories through columns on `blocks`
# (`blocks.pdf_path`, `blocks.audio_path`). v3 has neither column: a block
# carries `url` and `content`, and files live in their own tables. Copying
# hub's collectors would not have degraded gracefully, it would have raised
# `column blocks.pdf_path does not exist` on the first call.
#
# v3 is in fact better structured for this than hub was. Every category is
# reachable from a table that also holds the course and topic it belongs to,
# so "which files belong to this subject" is answerable directly instead of
# inferred from URLs embedded in text. That is the whole basis of selection
# here, so it is worth stating: the port could be simpler, and it is only
# simpler because of a difference that first looked like a loss.
#
# `blocks` still gets consulted, but only for scope. v3 records a PDF as a
# block of type `pdf-ref` whose `url` is `/api/pdf/<pdfs.id>`, which is how a
# file stored without a course gets attributed to the topic that displays it.
# That reconciliation lives in backup_resolve, in the scope fragments, not
# here, so the SQL that decides membership stays in one place.


def _collect_owned(conn, user_id, col, scope):
    """Collect every file this user's rows can reach, honouring `scope`."""
    _collect_pdfs(conn, user_id, col, _frag(scope, "pdfs"))
    _collect_images(conn, user_id, col, _frag(scope, "images"))
    _collect_audio(conn, user_id, col, _frag(scope, "ai_tasks"))
    _collect_infographics(conn, user_id, col, _frag(scope, "ai_tasks"))
    # scraped_pdfs has no table at all in v3 -- the three SCORM exports in
    # that directory are not referenced by any row. Deliberately absent here
    # so they arrive through the disk scan as unattributed, which is the
    # honest classification and the only one the user can act on.


def _collect_pdfs(conn, user_id, col, frag):
    """PDFs from the `pdfs` table, which is also what scopes them."""
    where = f"user_id = %s AND ({frag})" if frag else "user_id = %s"
    for row in db.query_dicts_on_conn(conn, f"""
        SELECT filename FROM pdfs WHERE {where}
    """, (user_id,)):
        stored = row["filename"] or ""
        m = PDF_RE.search(os.path.basename(stored))
        col.add(_rel_path("pdfs", m.group(1) if m else stored),
                "pdfs", stored)


def _collect_images(conn, user_id, col, frag):
    """Block images. storage_path is absolute; only the name is portable."""
    where = f"user_id = %s AND ({frag})" if frag else "user_id = %s"
    for row in db.query_dicts_on_conn(conn, f"""
        SELECT storage_path FROM images WHERE {where}
    """, (user_id,)):
        stored = row["storage_path"] or ""
        base = os.path.basename(stored)
        m = IMAGE_RE.search(base)
        col.add(_rel_path("images", m.group(1) if m else base), "images", stored)


def _collect_audio(conn, user_id, col, frag):
    """Audio embedded in text: NotebookLM result_content and TTS bodies.

    v3's audio_files table is EMPTY; the generated mp3s are only reachable
    through ai_tasks.result_content, which stores a served URL
    (`/api/ai/notebooklm/audio/audio_<uuid>.mp3`) rather than a disk path.
    Taking the basename of anything audio-shaped is what makes both the TTS
    route and the NotebookLM route resolve to the same file.
    """
    where = f"AND ({frag})" if frag else ""
    for row in db.query_dicts_on_conn(conn, f"""
        SELECT result_content AS t FROM ai_tasks
        WHERE user_id = %s AND result_content LIKE %s {where}
    """, (user_id, "%.mp3%")):
        for m in AUDIO_RE.finditer(row["t"] or ""):
            col.add(_rel_path("audio", m.group(1)), "audio", m.group(1))


def _collect_infographics(conn, user_id, col, frag):
    """Infographics are named by a URL inside ai_tasks.result_content.

    hub got away with the convention `infographic_<ai_tasks.id>.png`, because
    in hub's schema the id and the filename were the same value. In v3 they
    are not. Checked against the live data: of the 154 infographics on disk,
    NOT ONE has an ai_tasks row carrying that id, while all 154 are named in
    a result_content URL. Trusting the convention matched 14 files by
    coincidence and invented 494 "missing file" entries, which is a
    fabricated warning loud enough to be believed and wrong in every row.

    Same treatment as audio, for the same reason: the filename lives in text.
    """
    where = f"AND ({frag})" if frag else ""
    for row in db.query_dicts_on_conn(conn, f"""
        SELECT result_content AS t FROM ai_tasks
        WHERE user_id = %s AND result_content LIKE %s {where}
    """, (user_id, "%.png%")):
        for m in INFOGRAPHIC_RE.finditer(row["t"] or ""):
            col.add(_rel_path("infographics", m.group(1)),
                    "infographics", m.group(1))


_AUDIO_EXTS = (".mp3", ".wav", ".m4a", ".ogg", ".opus", ".aac", ".flac")
_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")


def _looks_like(category: str, name: str) -> bool:
    """Whether a file in this directory is one this app would have written.

    Extension-based on purpose. This runs on names that came from os.listdir,
    where a regex over the whole name is the wrong tool: `_ PDF.pdf` is a real
    file in scraped_pdfs, and a character class of `[A-Za-z0-9_.-]` silently
    decided it did not exist, so it was neither backed up nor reported as
    unaccounted for. It disappeared. A file you have should never be
    invisible because of its name.

    Only inclusion is decided here. Names from listdir are already inside the
    directory being scanned, so nothing is built from them.
    """
    ext = os.path.splitext(name)[1].lower()
    if category in ("pdfs", "scraped_pdfs"):
        return ext == ".pdf"
    if category == "audio":
        return name.startswith("script_") or ext in _AUDIO_EXTS
    if category == "infographics":
        return name.startswith("infographic_") and ext == ".png"
    if category == "images":
        return ext in _IMAGE_EXTS
    return False


def _safe_name(name: str) -> str | None:
    """A single path component that cannot escape the category folder.

    Used for names that come out of the DATABASE, where they are joined onto
    the uploads root and so could in principle walk out of it. Backslash and
    forward slash are refused rather than stripped: a filename containing one
    is not a file we can honestly archive, and silently repairing it would
    mean shipping something other than what the row names.
    """
    name = (name or "").strip()
    if not name or name in (".", ".."):
        return None
    if "/" in name or "\\" in name or "\0" in name:
        return None
    return name


def _scan_unclaimed(owned: set, foreign: set) -> list:
    """Media on disk that no row of any user can reach.

    The distinction that matters is "unreachable by anyone", not
    "unreachable by me". These directories are shared by every account, so the
    second reading sweeps up other people's files, which is the exact leak a
    per-user backup exists to prevent. `foreign` holds the paths other users'
    rows claim and is subtracted here.

    Scans all five media directories, each one resolved through
    storage_paths so the scan sees exactly the folders the app writes to.
    In v3 that also means scraped_pdfs, whose three SCORM exports no table
    references and which therefore always land here -- correct, and visible
    to the user as an opt-in rather than silently dropped or silently
    included.
    """
    out = []
    for category in sorted(DIR_CATEGORY):
        path_dir = category_dir(category)
        if not os.path.isdir(path_dir):
            continue
        for name in sorted(os.listdir(path_dir)):
            path = os.path.join(path_dir, name)
            if not os.path.isfile(path) or path in owned or path in foreign:
                continue
            if _looks_like(category, name):
                out.append(path)
    return out


def _claimed_by_others(conn, user_id: str) -> set:
    """Absolute paths of media that some *other* user's rows point at.

    Read only to withhold those files from this user's archive, so it exposes
    nothing about anyone else: what comes back is a set of filenames used as a
    subtraction, never rendered or shipped.

    Mirrors _collect_owned's sources exactly. A subtraction that forgets a
    source is not a smaller leak, it is an unclosed one, so the two lists are
    meant to be read side by side.
    """
    out = set()
    for row in db.query_dicts_on_conn(conn, """
        SELECT filename FROM pdfs WHERE user_id <> %s
    """, (user_id,)):
        m = PDF_RE.search(os.path.basename(row["filename"] or ""))
        if m:
            out.add(_rel_path("pdfs", m.group(1)))
    for row in db.query_dicts_on_conn(conn, """
        SELECT storage_path FROM images WHERE user_id <> %s
    """, (user_id,)):
        base = os.path.basename(row["storage_path"] or "")
        m = IMAGE_RE.search(base)
        if m:
            out.add(_rel_path("images", m.group(1)))
    for row in db.query_dicts_on_conn(conn, """
        SELECT result_content AS t FROM ai_tasks
        WHERE user_id <> %s AND result_content LIKE %s
    """, (user_id, "%.mp3%")):
        for m in AUDIO_RE.finditer(row["t"] or ""):
            out.add(_rel_path("audio", m.group(1)))
    for row in db.query_dicts_on_conn(conn, """
        SELECT result_content AS t FROM ai_tasks
        WHERE user_id <> %s AND result_content LIKE %s
    """, (user_id, "%.png%")):
        for m in INFOGRAPHIC_RE.finditer(row["t"] or ""):
            out.add(_rel_path("infographics", m.group(1)))
    return out


def _category_of_disk_file(path: str) -> str:
    """Which archive folder a file found on disk belongs in."""
    return DIR_CATEGORY.get(os.path.basename(os.path.dirname(path)), "pdfs")
