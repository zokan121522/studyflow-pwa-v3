"""POST /pdf/md2pdf — turn a note's markdown into a stored PDF.

Split out of routes/pdf.py on purpose: that module is already ~400 lines and
adding a 60-line route there would push it past the point where a file is easy
to hold in your head. The route is small because the work lives in
``services/md_to_pdf.py``.

The PDF is saved into the same ``pdfs`` table and the same uploads folder as an
uploaded PDF, with its course and topic attached, so it shows up next to the
user's other 47 documents and can be annotated like any of them.
"""

from __future__ import annotations

import logging
import uuid

from flask import Blueprint, jsonify, request

from database import execute, fetchone
from routes.auth import token_required
from routes.pdf import UPLOAD_FOLDER, PDF
from services.md_to_pdf import MdRenderError, markdown_to_pdf, _sanitize_filename

log = logging.getLogger(__name__)

bp = Blueprint("pdf_md2pdf", __name__)

# A note longer than this is a document, not a note. 2 MB of markdown is far
# past anything a person types by hand and would only mean a runaway client.
MAX_MARKDOWN_CHARS = 2_000_000


@bp.post("/pdf/md2pdf")
@token_required
def markdown_to_pdf_endpoint(current_user_id: int):
    """Render markdown to PDF, store it, and return the row.

    Body: ``{"markdown": str, "title": str, "course_id": int?, "topic_id": int?}``
    """
    payload = request.get_json(silent=True) or {}
    markdown = payload.get("markdown") or ""
    title = payload.get("title") or "documento"

    if not markdown.strip():
        return jsonify({"error": "La nota está vacía"}), 400
    if len(markdown) > MAX_MARKDOWN_CHARS:
        return jsonify(
            {"error": "La nota es demasiado grande para convertirla en un PDF"}
        ), 400

    course_id = payload.get("course_id")
    topic_id = payload.get("topic_id")
    course_id = int(course_id) if str(course_id or "").isdigit() else None
    topic_id = int(topic_id) if str(topic_id or "").isdigit() else None

    original_name = f"{_sanitize_filename(title)}.pdf"
    # uuid in the stored name: two exports of the same block must not collide.
    storage_filename = f"{uuid.uuid4().hex}.pdf"
    storage_path = f"{UPLOAD_FOLDER}/{storage_filename}"

    try:
        tmp_path, page_count = markdown_to_pdf(markdown, title=title)
    except MdRenderError as err:
        # The user's note could not be rendered: that is a 4xx with a reason,
        # not a 500 and an empty screen.
        log.warning("md2pdf render failed: %s", err)
        return jsonify({"error": str(err)}), 400

    try:
        with open(tmp_path, "rb") as fh:
            data = fh.read()
        import os

        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
        with open(storage_path, "wb") as fh:
            fh.write(data)
    except OSError as err:
        log.error("md2pdf could not store the file: %s", err)
        return jsonify({"error": "No se pudo guardar el PDF"}), 500

    execute(
        """INSERT INTO pdfs (user_id, course_id, topic_id, filename,
                              original_name, file_size, page_count, storage_path)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            current_user_id, course_id, topic_id, storage_filename,
            original_name, len(data), page_count, storage_path,
        ),
    )
    row = fetchone(
        "SELECT * FROM pdfs WHERE user_id = %s ORDER BY id DESC LIMIT 1",
        (current_user_id,),
    )
    if not row:
        return jsonify({"error": "El PDF se generó pero no se pudo registrar"}), 500

    pdf_dict = PDF.from_row(row).to_dict()
    log.info("md2pdf: user=%s course=%s topic=%s pages=%s",
             current_user_id, course_id, topic_id, page_count)
    return jsonify({"pdf": pdf_dict}), 201