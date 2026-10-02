"""
Shared utilities for NotebookLM modules.

Single source of truth for profile resolution — eliminates duplicate
``_get_active_profile()`` functions across notebooklm modules.
Also hosts multi-block content resolution (Phase 45): a single
ownership-checked fetch of one or more blocks joined into one text.

v3 changes from v2:
    - Cookie dir is ``~/.notebooklm`` (local macOS) instead of container
      ``/root/.notebooklm``; ``NOTEBOOKLM_DIR`` env overrides it.
    - DB access uses v3 flat imports (`from database import query`).
    - PDF text extraction hooks into the v3 pdf module lazily; until the
      tasks pipeline (7.4) lands, pdf blocks degrade to empty text
      instead of importing v2-only helpers.
"""

import json
import logging
import os
import uuid
from pathlib import Path

from database import query

logger = logging.getLogger(__name__)

COOKIE_DIR = os.environ.get(
    "NOTEBOOKLM_DIR",
    os.path.join(os.path.expanduser("~"), ".notebooklm"),
)
ACTIVE_FILE = os.path.join(COOKIE_DIR, "active.txt")
PROFILES_DIR = os.path.join(COOKIE_DIR, "profiles")
STORAGE_FILENAME = "storage_state.json"


def _new_task_id() -> str:
    """Generate a fresh ai_tasks primary key (v3 table has no DEFAULT).

    v2 relied on ``gen_random_uuid()::text`` as column default; the v3
    schema omits it, so task creators must supply ``id`` explicitly.
    """
    return str(uuid.uuid4())


def _local_part(email: str) -> str:
    """`zokan121522@gmail.com` → `zokan121522`.

    Profile dirs are written from the upload/export path with the bare
    local-part while active.txt holds the full address, so an exact match
    is not available when resolving. Comparing local parts is what lets
    `active.txt` find its own directory.
    """
    return email.strip().split("@", 1)[0].lower()


def _storage_ok(directory: str) -> bool:
    """True when `directory` holds a non-empty storage_state.json.

    A zero-byte file is not a session: it is what an interrupted login left
    behind, and passing it to the SDK fails later with a confusing error.
    """
    try:
        storage = Path(PROFILES_DIR) / directory / STORAGE_FILENAME
        return storage.is_file() and storage.stat().st_size > 0
    except OSError:
        return False


def _profile_has_cookies(profile: str | None) -> bool:
    """True when `profile` resolves to a directory with a usable session."""
    return _resolve_profile_dir(profile) is not None if profile else False


def _resolve_profile_dir(profile: str) -> str | None:
    """Directory for `profile` **that actually holds a session**.

    Two wrinkles this absorbs, both seen on the user's server:

      * ``active.txt`` holds a full address while the directory created by
        the upload/export path is named after the local part
        (``zokan121522`` vs ``zokan121522@gmail.com``).
      * BOTH directories can exist, and the exact-match one can be the empty
        leftover of an interrupted login. So candidates are tried in order
        (exact name, then local-part match) and the first one **with
        cookies** wins; a name that resolves to an empty dir is not a match.

    Returns the directory name as it exists on disk, or None.
    """
    if not profile:
        return None
    try:
        entries = [p.name for p in Path(PROFILES_DIR).iterdir() if p.is_dir()]
    except OSError:
        return None

    wanted = profile.strip().lower()
    candidates = [wanted] if wanted in entries else []
    wanted_local = _local_part(wanted)
    candidates += [
        name for name in entries
        if name != wanted and _local_part(name) == wanted_local
    ]

    for name in candidates:
        if _storage_ok(name):
            return name
    return None


def _connected_profiles() -> list[str]:
    """Every profile under profiles/ with a usable session, sorted."""
    try:
        entries = sorted(p.name for p in Path(PROFILES_DIR).iterdir() if p.is_dir())
    except OSError:
        return []
    return [name for name in entries if _profile_has_cookies(name)]


def get_active_profile() -> str | None:
    """Resolve which NotebookLM profile to authenticate as.

    Resolution order, each step replacing a fallback that used to be silent:

      1. ``active.txt`` — written when the user picks a profile. Resolved
         through ``_resolve_profile_dir`` because the file holds a full
         address while the directory may be named after the local part.
      2. The only connected profile, when there is exactly one. This is the
         case that bit the user on 2026-10-01: active.txt had gone missing
         while a valid 15 KB session sat in the profiles dir, so every
         generation fell through to an empty ``default/`` directory and
         failed with "Storage file not found".
      3. ``None`` — genuinely ambiguous, or nothing is connected. The log
         says which, so the cause is diagnosable instead of a dead end.

    Never returns a profile whose storage_state.json is missing or empty:
    handing the SDK an empty session only moves the failure later.
    """
    raw = ""
    try:
        raw = Path(ACTIVE_FILE).read_text().strip()
    except (FileNotFoundError, OSError):
        logger.warning(
            "[NotebookLM] active.txt not found at %s — resolving from profiles",
            ACTIVE_FILE,
        )

    if raw:
        directory = _resolve_profile_dir(raw)
        if directory and _profile_has_cookies(directory):
            logger.info("[NotebookLM] Active profile: %s", directory)
            return directory
        logger.warning(
            "[NotebookLM] active.txt points at %s but no connected profile "
            "matches it — ignoring it", raw,
        )

    connected = _connected_profiles()
    if len(connected) == 1:
        logger.info(
            "[NotebookLM] active.txt unusable; single connected profile "
            "recovered: %s", connected[0],
        )
        return connected[0]

    if connected:
        logger.warning(
            "[NotebookLM] No usable active profile and %d connected profiles "
            "%s — the caller must disambiguate (pick one in Ajustes)",
            len(connected), connected,
        )
    else:
        logger.warning(
            "[NotebookLM] No active profile and no connected profiles — "
            "the user must re-authenticate (subir cookies o script Chrome)",
        )
    return None


# ═══════════════════════════════════════════════════════════════════
# Multi-block content resolution (Phase 45)
# ═══════════════════════════════════════════════════════════════════

_BLOCK_CAP = 4000  # per-block char cap when combining multiple blocks


def _coerce_id(value, field: str = "id") -> int | None:
    """Coerce an id to int (v3 SERIAL columns) with a clean ValueError.

    v2 used uuid-style string ids; v3 blocks/topics ids are SERIAL
    integers. Accepts None/"" → None, int → int, numeric str → int.
    """
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid {field}: '{value}' (must be a numeric id)")


def _coerce_ids(ids, field: str = "block_ids") -> list[int]:
    """Coerce a list of ids to ints (used for ``WHERE id = ANY(%s)``)."""
    out = []
    for i in ids:
        v = _coerce_id(i, field)
        if v is None:
            continue
        out.append(v)
    if not out:
        raise ValueError(f"Missing required field: {field} (must be a non-empty list)")
    return out


def _extract_pdf_text(pdf_path: str) -> str:
    """Extract text from a PDF using the v3 pdf helpers, if available.

    v3 keeps PDF storage in backend/pdf_resources.py; the text extraction
    helper lands with the tasks pipeline (7.4). Until then this returns
    "" with a warning so notebooklm utils stay importable on any v3 tree.
    """
    try:
        from pdf_resources import extract_pdf_text  # type: ignore

        return extract_pdf_text(pdf_path)
    except (ImportError, AttributeError):
        logger.warning(
            "[NotebookLM] PDF text extraction not wired in v3 yet "
            "(block pipeline lands in 7.4) — treating pdf block as empty"
        )
        return ""


def _block_text(block: dict, cap: int | None = None) -> str:
    """Extract readable text from a block row.

    markdown/content → inline content; pdf → extracted text.
    Returns "" if the block has no usable content.
    """
    if block["type"] == "content":
        # Content blocks store HTML — strip tags to plain text for NotebookLM.
        import re as _re

        raw = (block.get("content") or "").strip()
        text = _re.sub(r"<br\s*/?>", "\n", raw, flags=_re.IGNORECASE)
        text = _re.sub(r"</?(p|div|h[1-6]|li|tr|td|th|blockquote)[^>]*>", "\n", text, flags=_re.IGNORECASE)
        text = _re.sub(r"<[^>]+>", "", text)  # strip remaining tags
        text = _re.sub(r"&nbsp;", " ", text, flags=_re.IGNORECASE)
        text = _re.sub(r"&amp;", "&", text, flags=_re.IGNORECASE)
        text = _re.sub(r"&lt;", "<", text, flags=_re.IGNORECASE)
        text = _re.sub(r"&gt;", ">", text, flags=_re.IGNORECASE)
        text = _re.sub(r"\n{3,}", "\n\n", text)  # collapse excessive newlines
        text = text.strip()
    elif block["type"] == "markdown":
        text = (block.get("content") or "").strip()
    elif block["type"] == "pdf":
        pdf_path = block.get("pdf_path") or ""
        if not pdf_path:
            return ""
        try:
            text = _extract_pdf_text(pdf_path).strip()
        except Exception as e:  # noqa: BLE001 — keep task errors informative
            logger.warning("[NotebookLM] PDF text extraction failed for %s: %s", block["id"], e)
            return ""
    else:
        return ""

    if cap and len(text) > cap:
        text = text[:cap] + "\n\n_[contenido truncado]"
    return text


def _resolve_blocks_content(block_ids: str | list[str], user_id: str) -> tuple[str, str, str]:
    """Resolve one or more block ids into a single combined text.

    Single ownership-checked fetch (``WHERE b.id = ANY(%s) AND b.user_id = %s``)
    with course/topic titles for context headers.

    Returns:
        (content_text, source_type, source_id):
        - One block  → (text, original block type, block id)
        - Many blocks → (text joined with "\\n\\n---\\n\\n", "blocks", json(ids))

    Raises:
        ValueError: If validation fails or no block has content.
    """
    if isinstance(block_ids, str):
        block_ids = [block_ids]
    ids = _coerce_ids(block_ids, "block_ids")

    rows = query(
        """SELECT b.*, c.title AS course_title, t.title AS topic_title
           FROM blocks b
           LEFT JOIN courses c ON c.id = b.course_id AND c.user_id = b.user_id
           LEFT JOIN topics t ON t.id = b.topic_id AND t.user_id = b.user_id
           WHERE b.id = ANY(%s) AND b.user_id = %s
           ORDER BY b."order_index" """,
        (ids, user_id),
    )
    if not rows:
        raise ValueError("None of the selected blocks were found")

    # Legacy single-block path: preserve original source_type / source_id
    if len(rows) == 1:
        text = _block_text(rows[0])
        if not text:
            raise ValueError("Block has no content")
        return text, rows[0]["type"], rows[0]["id"]

    sections = []
    for b in rows:
        text = _block_text(b, cap=_BLOCK_CAP)
        if not text:
            continue
        title = (b.get("title") or "").strip() or b["id"]
        sections.append(f"### {title}\n\n{text}")

    if not sections:
        raise ValueError("Selected blocks have no content")
    return "\n\n---\n\n".join(sections), "blocks", json.dumps(ids)


def _resolve_blocks_content_per_block(block_ids: str | list[str], user_id: str) -> list[dict]:
    """Resolve block ids into a list of per-block texts (title + text).

    Same ownership-checked fetch as ``_resolve_blocks_content`` but returns
    one entry per block (``{"id", "title", "text"}``) instead of joining
    them — used by the multi-block test flow so each block can be asked for
    its EXACT question quota (10/20/30 evenly distributed).

    Returns:
        list of {"id": str, "title": str, "text": str} — blocks without
        usable content are skipped.

    Raises:
        ValueError: If validation fails or no block has content.
    """
    if isinstance(block_ids, str):
        block_ids = [block_ids]
    ids = _coerce_ids(block_ids, "block_ids")

    rows = query(
        """SELECT b.*, c.title AS course_title, t.title AS topic_title
           FROM blocks b
           LEFT JOIN courses c ON c.id = b.course_id AND c.user_id = b.user_id
           LEFT JOIN topics t ON t.id = b.topic_id AND t.user_id = b.user_id
           WHERE b.id = ANY(%s) AND b.user_id = %s
           ORDER BY b."order_index" """,
        (ids, user_id),
    )
    if not rows:
        raise ValueError("None of the selected blocks were found")

    blocks = []
    for b in rows:
        text = _block_text(b, cap=_BLOCK_CAP)
        if not text:
            continue
        title = (b.get("title") or "").strip() or b["id"]
        blocks.append({"id": b["id"], "type": b["type"], "title": title, "text": text})

    if not blocks:
        raise ValueError("Selected blocks have no content")
    return blocks