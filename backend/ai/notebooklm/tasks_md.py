"""
NotebookLM Markdown Enhancement + Markdown→HTML tasks (v3 port of v2
ai/notebooklm/tasks.py, split #3/4 — Gemini Chat API).

Synchronous task creators. Run inside Flask request context (blocking),
validate input, create the DB record, and start a background thread.

Exports:
    create_enhance_md_task, create_md_to_html_task
"""

import asyncio
import os
import threading
import time

from database import execute, execute_returning, query_one

from ai.notebooklm.tasks_pdf import _update_progress
from ai.notebooklm.utils import _coerce_id, _new_task_id

_TEMP_MD_DIR = "/tmp/notebooklm-content"
_MD_ENHANCE_PREFIX = "tmp-enhance-md"
_MD_HTML_PREFIX = "tmp-to-html"
_SOURCE_WAIT_TIMEOUT = 120.0


def _run_notebooklm_enhance_md_task(
    task_id: str,
    content_text: str,
    *,
    template_id: str | None = None,
    language: str = "auto",
    length: str = "standard",
) -> None:
    """Background thread: enhance markdown content via Gemini Chat API.

    Accepts optional ``template_id`` / ``language`` / ``length`` from the
    shared markdown config modal. Defaults preserve the legacy behaviour
    exactly (base ENHANCE_MD_PROMPT unchanged).

    Args:
        task_id: The ai_tasks row id to update.
        content_text: The full markdown content to enhance.
        template_id: MD_TEMPLATES key (or None → base prompt).
        language: 'auto' | 'es' | 'en'.
        length: 'concise' | 'standard' | 'detailed'.
    """
    os.makedirs(_TEMP_MD_DIR, exist_ok=True)

    try:
        execute(
            "UPDATE ai_tasks SET status = 'processing', updated_at = NOW() WHERE id = %s",
            (task_id,),
        )
        _update_progress(
            task_id,
            "⏳ Preparando contenido…",
        )

        # ── 1. Save content to a temp text file ─────────────────────
        ts = int(time.time())
        temp_path = os.path.join(_TEMP_MD_DIR, f"content_{ts}.txt")
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(content_text)

        _update_progress(
            task_id,
            "✅ Contenido preparado\n"
            "⏳ Mejorando Markdown con Gemini…",
        )

        # ── 2. Upload to NotebookLM + ask with enhance prompt ──────
        from ai.notebooklm.prompts import (
            ENHANCE_MD_PROMPT,
            compose_nb_md_prompt,
        )
        from ai.notebooklm.utils import get_active_profile as _get_active_profile
        from notebooklm import NotebookLMClient

        # Shared markdown config modal parameters are folded into the
        # prompt only when explicitly provided; defaults keep the legacy
        # ENHANCE_MD_PROMPT untouched.
        enhance_prompt = compose_nb_md_prompt(
            template_id, language, length, ENHANCE_MD_PROMPT,
        )

        async def _run():
            profile = _get_active_profile()
            kwargs = {"profile": profile} if profile else {}
            async with NotebookLMClient.from_storage(**kwargs) as client:
                nb = await client.notebooks.create(
                    f"{_MD_ENHANCE_PREFIX}-{ts}"
                )
                try:
                    source = await client.sources.add_file(
                        nb.id, temp_path, wait=True,
                        wait_timeout=_SOURCE_WAIT_TIMEOUT,
                    )
                    if not source or not source.id:
                        raise RuntimeError(
                            "Failed to add content to NotebookLM"
                        )

                    execute(
                        "UPDATE ai_tasks SET error_message = %s, "
                        "updated_at = NOW() WHERE id = %s",
                        (
                            "✅ Contenido preparado\n"
                            "⏳ Mejorando Markdown con Gemini…",
                            task_id,
                        ),
                    )

                    answer = await client.chat.ask(
                        nb.id, enhance_prompt
                    )
                    if not answer or not answer.answer:
                        raise RuntimeError(
                            "Gemini returned empty result"
                        )

                    return answer.answer
                finally:
                    try:
                        await client.notebooks.delete(nb.id)
                    except Exception:
                        pass

        result = asyncio.run(_run())

        if not result or not result.strip():
            raise RuntimeError("Gemini returned empty enhanced markdown")

        # Clean up temp file
        try:
            os.remove(temp_path)
        except OSError:
            pass

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


def create_enhance_md_task(
    block_id: str,
    topic_id: str,
    user_id: str,
    *,
    template_id: str | None = None,
    language: str = "auto",
    length: str = "standard",
) -> dict:
    """Create a markdown enhancement task via Gemini Chat API.

    Accepts optional ``template_id`` / ``language`` / ``length`` from the
    shared markdown config modal. Defaults preserve the legacy behaviour
    exactly.

    Args:
        block_id: The markdown/content block id to process.
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
    if block["type"] not in ("markdown", "content"):
        raise ValueError(
            f"Block must be 'markdown' or 'content', "
            f"got '{block['type']}'"
        )

    content_text = (block.get("content") or "").strip()
    if not content_text:
        raise ValueError("Block has no content")

    task_row = execute_returning(
        """INSERT INTO ai_tasks
           (id, user_id, topic_id, task_type, format, source_type, source_id,
            template_id, language, length, status)
           VALUES (%s, %s, %s, 'notebooklm_enhance_md', 'md', %s, %s,
                   %s, %s, %s, 'pending')
           RETURNING id""",
        (
            _new_task_id(), user_id, topic_id_int, block["type"], block_id,
            template_id, language, length,
        ),
    )
    task_id = task_row["id"]

    thread = threading.Thread(
        target=_run_notebooklm_enhance_md_task,
        args=(task_id, content_text),
        kwargs={
            "template_id": template_id,
            "language": language,
            "length": length,
        },
        daemon=True,
    )
    thread.start()

    return {"task_id": task_id}


def _run_notebooklm_md_to_html_task(
    task_id: str, content_text: str
) -> None:
    """Background thread: convert markdown to HTML via Gemini Chat API.

    Args:
        task_id: The ai_tasks row id to update.
        content_text: The full markdown content to convert.
    """
    os.makedirs(_TEMP_MD_DIR, exist_ok=True)

    try:
        execute(
            "UPDATE ai_tasks SET status = 'processing', "
            "updated_at = NOW() WHERE id = %s",
            (task_id,),
        )
        _update_progress(
            task_id,
            "⏳ Preparando contenido…",
        )

        # ── 1. Save content to a temp text file ─────────────────────
        ts = int(time.time())
        temp_path = os.path.join(_TEMP_MD_DIR, f"content_{ts}.txt")
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(content_text)

        _update_progress(
            task_id,
            "✅ Contenido preparado\n"
            "⏳ Convirtiendo a HTML con Gemini…",
        )

        # ── 2. Upload to NotebookLM + ask with HTML prompt ──────────
        from ai.notebooklm.prompts import MD_TO_HTML_PROMPT
        from ai.notebooklm.utils import get_active_profile as _get_active_profile
        from notebooklm import NotebookLMClient

        async def _run():
            profile = _get_active_profile()
            kwargs = {"profile": profile} if profile else {}
            async with NotebookLMClient.from_storage(**kwargs) as client:
                nb = await client.notebooks.create(
                    f"{_MD_HTML_PREFIX}-{ts}"
                )
                try:
                    source = await client.sources.add_file(
                        nb.id, temp_path, wait=True,
                        wait_timeout=_SOURCE_WAIT_TIMEOUT,
                    )
                    if not source or not source.id:
                        raise RuntimeError(
                            "Failed to add content to NotebookLM"
                        )

                    execute(
                        "UPDATE ai_tasks SET error_message = %s, "
                        "updated_at = NOW() WHERE id = %s",
                        (
                            "✅ Contenido preparado\n"
                            "⏳ Convirtiendo a HTML con Gemini…",
                            task_id,
                        ),
                    )

                    answer = await client.chat.ask(
                        nb.id, MD_TO_HTML_PROMPT
                    )
                    if not answer or not answer.answer:
                        raise RuntimeError(
                            "Gemini returned empty HTML result"
                        )

                    return answer.answer
                finally:
                    try:
                        await client.notebooks.delete(nb.id)
                    except Exception:
                        pass

        result = asyncio.run(_run())

        if not result or not result.strip():
            raise RuntimeError("Gemini returned empty HTML")

        # Wrap body fragment in full HTML document with StudyFlow CSS
        # (same as PDF→HTML pipeline — gives proper dark theme styling)
        from ai.notebooklm.html_template import wrap_html_document
        _title = "StudyFlow Content"
        for _line in content_text.split("\n"):
            _line = _line.strip()
            if _line.startswith("# ") and not _line.startswith("##"):
                _title = _line[2:].strip()
                break
        result = wrap_html_document(result, title=_title)

        # Clean up temp file
        try:
            os.remove(temp_path)
        except OSError:
            pass

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


def create_md_to_html_task(
    block_id: str, topic_id: str, user_id: str
) -> dict:
    """Create a markdown-to-HTML conversion task via Gemini Chat API.

    Args:
        block_id: The markdown/content block id to process.
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
    if block["type"] not in ("markdown", "content"):
        raise ValueError(
            f"Block must be 'markdown' or 'content', "
            f"got '{block['type']}'"
        )

    content_text = (block.get("content") or "").strip()
    if not content_text:
        raise ValueError("Block has no content")

    task_row = execute_returning(
        """INSERT INTO ai_tasks
           (id, user_id, topic_id, task_type, format, source_type, source_id, status)
           VALUES (%s, %s, %s, 'notebooklm_md_to_html', 'html', %s, %s, 'pending')
           RETURNING id""",
        (_new_task_id(), user_id, topic_id_int, block["type"], block_id),
    )
    task_id = task_row["id"]

    thread = threading.Thread(
        target=_run_notebooklm_md_to_html_task,
        args=(task_id, content_text),
        daemon=True,
    )
    thread.start()

    return {"task_id": task_id}