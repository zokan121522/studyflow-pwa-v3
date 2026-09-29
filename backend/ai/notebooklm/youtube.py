"""
YouTube processing via NotebookLM.

Validates YouTube URLs, creates temporary notebooks, adds YouTube as a source,
and uses the Chat API to generate structured markdown or HTML content.

Exports:
    create_youtube_md_task, create_youtube_html_task
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path

import requests
from notebooklm import NotebookLMClient

from database import execute, execute_returning
from ai.notebooklm.client import _strip_citation_markers
from ai.notebooklm.prompts import (
    YOUTUBE_TO_MARKDOWN_PROMPT,
    YOUTUBE_TO_HTML_PROMPT,
)

logger = logging.getLogger(__name__)


# ── helpers ─────────────────────────────────────────────────────────

_NOTEBOOK_PREFIX = "tmp-youtube"
_CHAT_TIMEOUT = 300
_SOURCE_WAIT_TIMEOUT = 300.0


def validate_youtube_url(url: str) -> bool:
    """Validate a YouTube URL using the SDK's built-in detection.

    Args:
        url: The URL to validate.

    Returns:
        True if the URL is a valid YouTube URL.
    """
    from notebooklm._url_utils import is_youtube_url
    return is_youtube_url(url)


def _sync_run(coro):
    """Run a coroutine synchronously."""
    return asyncio.run(coro)


from ai.notebooklm.utils import get_active_profile as _get_active_profile


async def _add_youtube_and_ask(url: str, prompt: str) -> str:
    """One-shot pipeline: create notebook → add YouTube URL → ask → delete.

    Args:
        url: YouTube video URL.
        prompt: The question/prompt to ask the notebook.

    Returns:
        The answer text from NotebookLM.

    Raises:
        RuntimeError: On auth failure, upload failure, or timeout.
    """
    if not validate_youtube_url(url):
        raise ValueError(f"Invalid YouTube URL: {url}")

    profile = _get_active_profile()
    kwargs = {"profile": profile} if profile else {}
    async with NotebookLMClient.from_storage(**kwargs) as client:
        ts = int(time.time())
        nb = await client.notebooks.create(f"{_NOTEBOOK_PREFIX}-{ts}")

        try:
            # Add YouTube as a source with enforced timeout
            # SDK's wait_timeout may not work reliably for YouTube URLs
            try:
                source = await asyncio.wait_for(
                    client.sources.add_url(nb.id, url, wait=True),
                    timeout=_SOURCE_WAIT_TIMEOUT,
                )
            except asyncio.TimeoutError:
                raise RuntimeError(
                    f"NotebookLM timeout: no pudo procesar el video de YouTube en {_SOURCE_WAIT_TIMEOUT}s. "
                    "El video puede ser muy largo o estar restringido."
                )

            if not source or not source.id:
                raise RuntimeError("Failed to add YouTube source to NotebookLM")

            # Ask the question (Chat API)
            answer = await asyncio.wait_for(
                client.chat.ask(nb.id, prompt),
                timeout=_CHAT_TIMEOUT,
            )
            if not answer or not answer.answer:
                raise RuntimeError("NotebookLM returned empty answer")

            return answer.answer

        finally:
            try:
                await client.notebooks.delete(nb.id)
            except Exception:
                pass  # best-effort cleanup


# ── public API ──────────────────────────────────────────────────────


def youtube_to_markdown(url: str) -> str:
    """YouTube → Markdown via NotebookLM Chat API.

    Args:
        url: YouTube video URL.

    Returns:
        Markdown string with structured course notes.

    Raises:
        ValueError: If URL is invalid.
        RuntimeError: On API failure.
    """
    return _sync_run(_add_youtube_and_ask(url, YOUTUBE_TO_MARKDOWN_PROMPT))


def youtube_to_html(url: str) -> str:
    """YouTube → HTML via NotebookLM Chat API.

    Args:
        url: YouTube video URL.

    Returns:
        HTML string (body content only).

    Raises:
        ValueError: If URL is invalid.
        RuntimeError: On API failure.
    """
    return _sync_run(_add_youtube_and_ask(url, YOUTUBE_TO_HTML_PROMPT))


# ── background task helpers ─────────────────────────────────────────


def _update_progress(task_id: str, message: str) -> None:
    """Update ai_tasks error_message with current progress."""
    execute(
        "UPDATE ai_tasks SET error_message = %s, updated_at = NOW() WHERE id = %s",
        (message, task_id),
    )


def _fetch_video_title(url: str) -> str:
    """yt-dlp → oEmbed fallback chain. Motivation (#255): oEmbed 401s
    from the home server IP (works on Mac, fails in container), so
    yt-dlp runs first; oEmbed is the last resort. Never raises.
    """
    base = ["yt-dlp", "--print", "title", "--skip-download", "--no-playlist", "--no-warnings", url]
    cookies = os.environ.get(
        "YOUTUBE_COOKIES",
        str(Path.home() / ".studyflow-app" / "cookies" / "youtube_cookies.txt"),
    )
    cmds = [base, ["yt-dlp", "--cookies", cookies, *base[1:]]] if os.path.isfile(cookies) else [base]
    for cmd in cmds:
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=25)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.decode(errors="replace").splitlines()[0].strip()
        except Exception as e:
            logger.debug("yt-dlp title failed: %s", e)
    try:
        resp = requests.get("https://www.youtube.com/oembed", params={"format": "json", "url": url}, timeout=5)
        if resp.ok:
            return str(resp.json().get("title", "")).strip()
    except Exception as e:
        logger.debug("oEmbed title failed: %s", e)
    return ""


def _run_youtube_task(task_id: str, url: str, output_format: str) -> None:
    """Background thread: process YouTube URL and update ai_tasks.

    Args:
        task_id: The ai_tasks row id to update.
        url: The YouTube URL to process.
        output_format: 'markdown' or 'html'.
    """
    try:
        execute(
            "UPDATE ai_tasks SET status = 'processing', updated_at = NOW() WHERE id = %s",
            (task_id,),
        )
        _update_progress(task_id, "⏳ Validando URL de YouTube…")

        if not validate_youtube_url(url):
            raise ValueError(f"URL de YouTube no válida: {url}")

        _update_progress(
            task_id,
            "✅ URL válida\n"
            "⏳ Añadiendo video a NotebookLM…\n"
            "⏳ Procesando con Gemini…",
        )

        logger.info(f"YouTube task {task_id}: processing {url} as {output_format}")

        if output_format == "markdown":
            result = youtube_to_markdown(url)
        else:
            result = youtube_to_html(url)

        # Phase 65: native flow doesn't go through client.py's chat helper,
        # so its citation strip never fires — apply it at the task boundary
        # so [4]-style markers from NotebookLM never leak into md/html output.
        result = _strip_citation_markers(result or "")

        if not result or not result.strip():
            raise RuntimeError("NotebookLM returned empty result")

        logger.info(f"YouTube task {task_id}: completed successfully ({len(result)} chars)")

        # Phase 64 (#255): fetch the real YouTube video title so the frontend
        # can name the generated block after the video instead of the generic
        # "NotebookLM" label. Best-effort: never aborts the task.
        video_title = _fetch_video_title(url)
        coverage_data = json.dumps({"video_title": video_title, "video_url": url})

        execute(
            """UPDATE ai_tasks
               SET status = 'done', result_content = %s, coverage_data = %s,
                   error_message = NULL, completed_at = NOW(), updated_at = NOW()
               WHERE id = %s""",
            (result, coverage_data, task_id),
        )

    except Exception as e:
        logger.error(f"YouTube task {task_id}: failed — {e}")
        execute(
            """UPDATE ai_tasks
               SET status = 'error', error_message = %s,
                   completed_at = NOW(), updated_at = NOW()
               WHERE id = %s""",
            (str(e), task_id),
        )


def create_youtube_md_task(url: str, topic_id: str, block_id: str, user_id: str) -> dict:
    """Create a YouTube → Markdown background task.

    Args:
        url: YouTube video URL.
        topic_id: Topic to associate the result with.
        block_id: Source block id (may be empty for standalone YouTube).
        user_id: User creating the task.

    Returns:
        {"task_id": str}
    """
    if not url:
        raise ValueError("Missing required field: url")

    if not validate_youtube_url(url):
        raise ValueError(f"Invalid YouTube URL: {url}")

    task_row = execute_returning(
        """INSERT INTO ai_tasks
           (user_id, topic_id, task_type, format, source_type, source_id, status)
           VALUES (%s, %s, 'youtube', 'markdown', 'youtube', %s, 'pending')
           RETURNING id""",
        (user_id, topic_id, block_id),
    )
    task_id = task_row["id"]

    thread = threading.Thread(
        target=_run_youtube_task,
        args=(task_id, url, "markdown"),
        daemon=True,
    )
    thread.start()

    return {"task_id": task_id}


def create_youtube_html_task(url: str, topic_id: str, block_id: str, user_id: str) -> dict:
    """Create a YouTube → HTML background task.

    Args:
        url: YouTube video URL.
        topic_id: Topic to associate the result with.
        block_id: Source block id.
        user_id: User creating the task.

    Returns:
        {"task_id": str}
    """
    if not url:
        raise ValueError("Missing required field: url")

    if not validate_youtube_url(url):
        raise ValueError(f"Invalid YouTube URL: {url}")

    task_row = execute_returning(
        """INSERT INTO ai_tasks
           (user_id, topic_id, task_type, format, source_type, source_id, status)
           VALUES (%s, %s, 'youtube', 'html', 'youtube', %s, 'pending')
           RETURNING id""",
        (user_id, topic_id, block_id),
    )
    task_id = task_row["id"]

    thread = threading.Thread(
        target=_run_youtube_task,
        args=(task_id, url, "html"),
        daemon=True,
    )
    thread.start()

    return {"task_id": task_id}
