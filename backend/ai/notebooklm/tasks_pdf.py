"""
NotebookLM PDF → Markdown / HTML task creators (v3 port of
v2 ai/notebooklm/tasks.py, split #1/4).

Synchronous task creators. Run inside Flask request context (blocking),
validate input, create the DB record, and start a background thread.

Exports:
    create_notebooklm_md_task, create_notebooklm_html_task

Shared helpers exported for sibling modules:
    _update_progress, _repair_invalid_escapes
"""

import os
import threading

from database import execute, execute_returning, query_one

from ai.utils import _resolve_pdf_path
from ai.notebooklm.utils import _coerce_id, _new_task_id


def _update_progress(task_id: str, message: str) -> None:
    """Update ai_tasks error_message with current progress checklist."""
    execute(
        "UPDATE ai_tasks SET error_message = %s, updated_at = NOW() WHERE id = %s",
        (message, task_id),
    )


def _repair_invalid_escapes(raw: str) -> str:
    """Double backslashes that are NOT valid JSON escapes.

    NotebookLM/Gemini occasionally emits raw LaTeX (``\\frac``), regex
    (``\\d``) or Windows paths (``C:\\Users``) inside JSON strings without
    escaping the backslash, which makes ``json.loads`` fail with
    ``Invalid \\escape``. This repairs exactly that: valid JSON escapes
    (``\\n``, ``\\"``, ``\\uXXXX``, ...) are left untouched, invalid ones
    are doubled so the JSON parses and the literal backslash survives.

    Args:
        raw: Raw JSON text from the model.

    Returns:
        The same text with invalid backslash escapes doubled.
    """
    out = []
    i = 0
    n = len(raw)
    while i < n:
        ch = raw[i]
        if ch == "\\" and i + 1 < n:
            nxt = raw[i + 1]
            if nxt in '"\\/bfnrt' or (
                nxt == "u"
                and i + 5 < n
                and all(c in "0123456789abcdefABCDEF" for c in raw[i + 2 : i + 6])
            ):
                out.append(ch)  # valid escape → keep as-is
                out.append(nxt)
                i += 2
            else:
                out.append("\\\\")  # invalid escape → double it
                i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _run_notebooklm_task(
    task_id: str,
    pdf_path: str,
    output_format: str,
    *,
    template_id: str | None = None,
    language: str = "auto",
    length: str = "standard",
) -> None:
    """Background thread: run NotebookLM operation and update ai_tasks.

    Args:
        task_id: The ai_tasks row id to update.
        pdf_path: Absolute path to the PDF file.
        output_format: 'markdown' or 'html'.
        template_id: optional MD_TEMPLATES key for markdown output.
        language: optional output language override.
        length: optional verbosity override.

    Note:
        ``template_id`` / ``language`` / ``length`` only affect the
        markdown output path (HTML keeps its pre-existing fixed prompt).
    """
    try:
        # Mark as processing
        execute(
            "UPDATE ai_tasks SET status = 'processing', updated_at = NOW() WHERE id = %s",
            (task_id,),
        )
        _update_progress(task_id, "⏳ Preparando PDF…")

        # Resolve PDF path
        resolved = _resolve_pdf_path(pdf_path)
        if not resolved:
            raise RuntimeError(f"PDF no encontrado: {pdf_path}")
        _update_progress(
            task_id,
            "✅ PDF encontrado\n"
            "⏳ Subiendo PDF a NotebookLM…",
        )

        # Call NotebookLM
        from ai.notebooklm.client import nb_pdf_to_markdown, nb_pdf_to_html

        _update_progress(
            task_id,
            "✅ PDF encontrado\n"
            "⏳ Subiendo PDF a NotebookLM…\n"
            "⏳ Procesando con Gemini…",
        )

        if output_format == "markdown":
            result = nb_pdf_to_markdown(
                resolved,
                template_id=template_id,
                language=language,
                length=length,
            )
        else:
            body_html = nb_pdf_to_html(resolved)
            if not body_html or not body_html.strip():
                raise RuntimeError("NotebookLM returned empty result")
            # Wrap body fragment in full HTML document with StudyFlow CSS
            from ai.notebooklm.html_template import wrap_html_document
            title = os.path.splitext(os.path.basename(resolved))[0]
            title = title.replace("_", " ").replace("-", " ").title()
            result = wrap_html_document(body_html, title=title)

        if not result or not result.strip():
            raise RuntimeError("NotebookLM returned empty result")

        # Update task as done
        execute(
            """UPDATE ai_tasks
               SET status = 'done', result_content = %s, error_message = NULL,
                   completed_at = NOW(), updated_at = NOW()
               WHERE id = %s""",
            (result, task_id),
        )

    except Exception as e:
        execute(
            """UPDATE ai_tasks
               SET status = 'error', error_message = %s,
                   completed_at = NOW(), updated_at = NOW()
               WHERE id = %s""",
            (str(e), task_id),
        )


def _resolve_pdf_block_path(block: dict) -> str:
    """Return the PDF path/url for a blocks row, v2/v3 compatible.

    v2 stored ``pdf_path`` directly on the block. v3 has no such column:
    PDF blocks are ``type='pdf-ref'`` and carry ``url='/api/pdf/<id>'``
    (the ``_resolve_pdf_path`` helper resolves that back to a real path
    via the pdfs table).
    """
    return (block.get("pdf_path") or block.get("url") or "").strip()


def create_notebooklm_md_task(
    block_id: str,
    topic_id: str,
    user_id: str,
    *,
    template_id: str | None = None,
    language: str = "auto",
    length: str = "standard",
) -> dict:
    """Create a NotebookLM PDF→Markdown task.

    Accepts optional ``template_id`` / ``language`` / ``length`` from the
    shared markdown config modal. Defaults preserve the legacy behaviour
    exactly.

    Args:
        block_id: The PDF block id to process.
        topic_id: The topic to associate the result with.
        user_id: The user creating the task.
        template_id: MD_TEMPLATES key (or None → base prompt).
        language: 'auto' | 'es' | 'en'.
        length: 'concise' | 'standard' | 'detailed'.

    Returns:
        {"task_id": str}

    Raises:
        ValueError: If input validation fails or block is not found.
    """
    if not block_id:
        raise ValueError("Missing required field: block_id")
    block_id_int = _coerce_id(block_id, "block_id")
    topic_id_int = _coerce_id(topic_id, "topic_id")

    block = query_one(
        "SELECT * FROM blocks WHERE id = %s AND user_id = %s",
        (block_id_int, user_id),
    )
    if not block:
        raise ValueError("Block not found")
    if block["type"] not in ("pdf", "pdf-ref"):
        raise ValueError(f"Block must be 'pdf' or 'pdf-ref', got '{block['type']}'")

    pdf_path = _resolve_pdf_block_path(block)
    if not pdf_path:
        raise ValueError("PDF block has no file path")

    resolved = _resolve_pdf_path(pdf_path)
    if not resolved:
        raise ValueError(f"PDF no encontrado: {pdf_path}")

    # Create ai_tasks record
    task_row = execute_returning(
        """INSERT INTO ai_tasks
           (id, user_id, topic_id, task_type, format, source_type, source_id,
            template_id, language, length, status)
           VALUES (%s, %s, %s, 'notebooklm', 'markdown', 'pdf', %s,
                   %s, %s, %s, 'pending')
           RETURNING id""",
        (_new_task_id(), user_id, topic_id_int, block_id, template_id, language, length),
    )
    task_id = task_row["id"]

    # Start background thread
    thread = threading.Thread(
        target=_run_notebooklm_task,
        args=(task_id, pdf_path, "markdown"),
        kwargs={
            "template_id": template_id,
            "language": language,
            "length": length,
        },
        daemon=True,
    )
    thread.start()

    return {"task_id": task_id}


def create_notebooklm_html_task(block_id: str, topic_id: str, user_id: str) -> dict:
    """Create a NotebookLM PDF→HTML task.

    Args:
        block_id: The PDF block id to process.
        topic_id: The topic to associate the result with.
        user_id: The user creating the task.

    Returns:
        {"task_id": str}

    Raises:
        ValueError: If input validation fails or block is not found.
    """
    if not block_id:
        raise ValueError("Missing required field: block_id")
    block_id_int = _coerce_id(block_id, "block_id")
    topic_id_int = _coerce_id(topic_id, "topic_id")

    block = query_one(
        "SELECT * FROM blocks WHERE id = %s AND user_id = %s",
        (block_id_int, user_id),
    )
    if not block:
        raise ValueError("Block not found")
    if block["type"] not in ("pdf", "pdf-ref"):
        raise ValueError(f"Block must be 'pdf' or 'pdf-ref', got '{block['type']}'")

    pdf_path = _resolve_pdf_block_path(block)
    if not pdf_path:
        raise ValueError("PDF block has no file path")

    resolved = _resolve_pdf_path(pdf_path)
    if not resolved:
        raise ValueError(f"PDF no encontrado: {pdf_path}")

    task_row = execute_returning(
        """INSERT INTO ai_tasks
           (id, user_id, topic_id, task_type, format, source_type, source_id, status)
           VALUES (%s, %s, %s, 'notebooklm', 'html', 'pdf', %s, 'pending')
           RETURNING id""",
        (_new_task_id(), user_id, topic_id_int, block_id),
    )
    task_id = task_row["id"]

    thread = threading.Thread(
        target=_run_notebooklm_task,
        args=(task_id, pdf_path, "html"),
        daemon=True,
    )
    thread.start()

    return {"task_id": task_id}