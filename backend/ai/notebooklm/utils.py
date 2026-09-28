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
from pathlib import Path

from database import query

logger = logging.getLogger(__name__)

_COOKIE_DIR = os.environ.get(
    "NOTEBOOKLM_DIR",
    os.path.join(os.path.expanduser("~"), ".notebooklm"),
)
_ACTIVE_FILE = os.path.join(_COOKIE_DIR, "active.txt")


def get_active_profile() -> str | None:
    """Read the active profile email from active.txt.

    Returns:
        The email string, or None if no active profile is set
        (→ falls back to global cookie file).
    """
    try:
        raw = Path(_ACTIVE_FILE).read_text().strip()
        if raw:
            logger.info("[NotebookLM] Active profile: %s", raw)
        else:
            logger.warning("[NotebookLM] active.txt is empty — falling back to global cookies")
        return raw if raw else None
    except (FileNotFoundError, OSError):
        logger.warning("[NotebookLM] active.txt not found at %s — falling back to global cookies", _ACTIVE_FILE)
        return None


# ═══════════════════════════════════════════════════════════════════
# Multi-block content resolution (Phase 45)
# ═══════════════════════════════════════════════════════════════════

_BLOCK_CAP = 4000  # per-block char cap when combining multiple blocks


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
    ids = [b.strip() for b in block_ids if b and b.strip()]
    if not ids:
        raise ValueError("Missing required field: block_ids (must be a non-empty list)")

    rows = query(
        """SELECT b.*, c.title AS course_title, t.title AS topic_title
           FROM blocks b
           LEFT JOIN courses c ON c.id = b.course_id AND c.user_id = b.user_id
           LEFT JOIN topics t ON t.id = b.topic_id AND t.user_id = b.user_id
           WHERE b.id = ANY(%s) AND b.user_id = %s
           ORDER BY b."order" """,
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
    ids = [b.strip() for b in block_ids if b and b.strip()]
    if not ids:
        raise ValueError("Missing required field: block_ids (must be a non-empty list)")

    rows = query(
        """SELECT b.*, c.title AS course_title, t.title AS topic_title
           FROM blocks b
           LEFT JOIN courses c ON c.id = b.course_id AND c.user_id = b.user_id
           LEFT JOIN topics t ON t.id = b.topic_id AND t.user_id = b.user_id
           WHERE b.id = ANY(%s) AND b.user_id = %s
           ORDER BY b."order" """,
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