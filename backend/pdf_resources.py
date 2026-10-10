# backend/pdf_resources.py
"""PDF + pdf-ref block persistence for the SCORM import branches.

Split out of scorm_import.py during S7b-A: that module crossed the
500-line ceiling, and "write bytes → insert pdfs row → insert block" is a
single cohesive concern shared by the ZIP and the Moodle-URL branches.

Public API:
    save_pdf_and_block(...) → {block_id, pdf_id, title, url, original_name}
                               or None on any persistence failure.
"""

import os
import uuid
from typing import Any, Dict, Optional

from flask import current_app

from database import execute, fetchone
from storage_paths import category_dir


def save_pdf_and_block(
    *, user_id: int, course_id: Optional[int], topic_id: Optional[int],
    data: bytes, original_name: str, title: str,
) -> Optional[Dict[str, Any]]:
    """Write bytes to PDF store + insert row + create pdf-ref block."""
    pdf_id = _persist_pdf_bytes(
        user_id=user_id, course_id=course_id, topic_id=topic_id,
        data=data, original_name=original_name,
    )
    if not pdf_id:
        return None
    url_path = f"/api/pdf/{pdf_id}"
    block_id = _create_pdf_ref_block(
        user_id=user_id, course_id=course_id, topic_id=topic_id,
        title=title, url_path=url_path,
    )
    if not block_id:
        return None
    return {
        "block_id": block_id, "pdf_id": pdf_id,
        "title": title, "url": url_path, "original_name": original_name,
    }


def _persist_pdf_bytes(
    *, user_id: int, course_id: Optional[int], topic_id: Optional[int],
    data: bytes, original_name: str,
) -> Optional[int]:
    """Write PDF bytes to disk + insert a pdfs row. Returns the pdf id or None."""
    storage_name = _write_upload(data)
    if not storage_name:
        return None
    page_count = _count_pages(data)
    if not _insert_pdf_row(
        user_id=user_id, course_id=course_id, topic_id=topic_id,
        storage_name=storage_name, original_name=original_name,
        storage_path=_upload_path(storage_name), size=len(data),
        page_count=page_count,
    ):
        _remove_upload(storage_name)
        return None
    row = fetchone(
        "SELECT * FROM pdfs WHERE user_id = %s ORDER BY id DESC LIMIT 1",
        (user_id,),
    )
    return row["id"] if row else None


def _upload_folder() -> str:
    """Resolved PDF upload folder (app config > storage_paths).

    The old fallback was the Docker path ``/app/uploads/pdfs``, which on a
    bare-metal install resolved to ``C:\\app\\...`` on Windows and wrote
    SCORM-imported PDFs where no route would ever serve them.
    storage_paths falls back to the repo-local folder routes/pdf.py serves
    from instead.
    """
    return current_app.config.get("PDF_UPLOAD_FOLDER") or category_dir("pdfs")


def _upload_path(storage_name: str) -> str:
    return os.path.join(_upload_folder(), storage_name)


def _write_upload(data: bytes) -> Optional[str]:
    """Save the bytes under a uuid name. Returns the storage name or None."""
    os.makedirs(_upload_folder(), exist_ok=True)
    storage_name = f"{uuid.uuid4().hex}.pdf"
    try:
        with open(_upload_path(storage_name), "wb") as f:
            f.write(data)
    except OSError:
        return None
    return storage_name


def _insert_pdf_row(
    *, user_id: int, course_id: Optional[int], topic_id: Optional[int],
    storage_name: str, original_name: str, storage_path: str,
    size: int, page_count: Optional[int],
) -> bool:
    """Insert the pdfs row. Returns False when the insert fails."""
    try:
        execute(
            """INSERT INTO pdfs
                 (user_id, course_id, topic_id, filename, original_name,
                  file_size, page_count, storage_path)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (user_id, course_id, topic_id, storage_name, original_name,
             size, page_count, storage_path),
        )
    except Exception:
        return False
    return True


def _remove_upload(storage_name: str) -> None:
    """Best-effort cleanup of a file whose DB insert failed."""
    try:
        os.remove(_upload_path(storage_name))
    except OSError:
        pass


def _create_pdf_ref_block(
    *, user_id: int, course_id: Optional[int], topic_id: Optional[int],
    title: str, url_path: str,
) -> Optional[int]:
    """Insert one pdf-ref block. Returns id or None."""
    order_index = _next_order(user_id, course_id, topic_id)
    try:
        execute(
            """INSERT INTO blocks
                 (user_id, course_id, topic_id, type, title, content,
                  url, order_index, color, collapsed, done)
               VALUES (%s, %s, %s, 'pdf-ref', %s, '', %s,
                        %s, '', FALSE, FALSE)""",
            (user_id, course_id, topic_id, title, url_path, order_index),
        )
    except Exception:
        return None
    row = fetchone(
        "SELECT * FROM blocks WHERE user_id = %s ORDER BY id DESC LIMIT 1",
        (user_id,),
    )
    return row["id"] if row else None


def _next_order(user_id: int, course_id: Optional[int], topic_id) -> int:
    """Next order_index inside the course, scoped to the topic when given."""
    row = fetchone(
        """SELECT COALESCE(MAX(order_index), -1) + 1 AS n
           FROM blocks WHERE course_id = %s AND user_id = %s
             AND (%s IS NULL OR topic_id IS NOT DISTINCT FROM %s)""",
        (course_id, user_id, topic_id, topic_id),
    )
    return (row["n"] if row else 0) or 0


def _count_pages(pdf_bytes: bytes) -> Optional[int]:
    """Best-effort page count via PyMuPDF; returns None if unavailable."""
    try:
        import fitz
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        n = doc.page_count
        doc.close()
        return n
    except Exception:
        return None
