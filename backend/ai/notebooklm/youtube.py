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
try:  # pragma: no cover - optional, heavy SDK
    from notebooklm import NotebookLMClient
except ImportError:  # pragma: no cover
    NotebookLMClient = None

from database import execute, execute_returning, query_one
from ai.notebooklm.client import _strip_citation_markers
from ai.notebooklm.prompts import (
    YOUTUBE_TO_MARKDOWN_PROMPT,
    YOUTUBE_TO_HTML_PROMPT,
    compose_nb_md_prompt,
)

logger = logging.getLogger(__name__)


# ── mode instruction (issue #13) ─────────────────────────────────────
#
# The native NotebookLM YouTube path used to ship ONE fixed prompt, so the
# dialog had nothing to offer: no template, no language, no depth and no
# mode. Phase 64 (#255) hid those controls because the endpoint only read
# url + topic_id. They are now real, so we compose the prompt instead of
# accepting the fixed one.
#
# Only `por_tema` needs a nudge. `unitema` is the default single-block
# layout NotebookLM already produces, so it adds nothing — keeping the
# no-options path byte-identical to the pre-#13 behaviour.

_MODE_POR_TEMA_INSTRUCTION = (
    "\n## ORGANISATION OVERRIDE\n"
    "Split the notes into SEVERAL thematic sections, one per main idea of "
    "the video, each with its own heading. Do not collapse everything into "
    "a single block of prose.\n"
)

_VALID_MODES = ("unitema", "por_tema")


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


from ai.notebooklm.utils import _new_task_id
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


def build_native_youtube_prompt(
    template_id: str | None = None,
    depth: str = "standard",
    mode: str = "unitema",
    language: str = "auto",
) -> str:
    """Compose the native NotebookLM YouTube prompt from the dialog options.

    Issue #13 — the NotebookLM YouTube button now offers the same
    template / depth / mode / language controls as YouTubeZen.

    Delegates role, language and length to the shared Phase 62 composer
    (`compose_nb_md_prompt`, issue #253) instead of reinventing them here,
    then appends the mode instruction when `por_tema` is selected.

    When every value is a default the result is EXACTLY
    ``YOUTUBE_TO_MARKDOWN_PROMPT`` — same string object, no prelude — so
    the pre-#13 behaviour is preserved byte for byte and a regression
    cannot slip in through the new controls.

    Args:
        template_id: MD_TEMPLATES key, or None/'' for the standard role.
        depth: 'concise' | 'standard' | 'detailed' (maps to length).
        mode: 'unitema' | 'por_tema'. Unknown values fall back to
            'unitema' rather than raising: a stale bookmark should not
            fail the task.
        language: 'auto' | 'es' | 'en'.

    Returns:
        The composed system prompt.
    """
    depth = (depth or "standard").strip().lower()
    if depth not in ("concise", "standard", "detailed"):
        depth = "standard"

    mode = (mode or "unitema").strip().lower()
    if mode not in _VALID_MODES:
        mode = "unitema"

    language = (language or "auto").strip().lower() or "auto"
    template_id = (template_id or "").strip() or None

    prompt = compose_nb_md_prompt(
        template_id=template_id,
        language=language,
        length=depth,
        base_prompt=YOUTUBE_TO_MARKDOWN_PROMPT,
    )

    if mode == "por_tema":
        prompt += _MODE_POR_TEMA_INSTRUCTION

    return prompt


def youtube_to_markdown(url: str, prompt: str | None = None) -> str:
    """YouTube → Markdown via NotebookLM Chat API.

    Args:
        url: YouTube video URL.
        prompt: Optional system prompt. Defaults to the fixed
            YOUTUBE_TO_MARKDOWN_PROMPT, preserving the original
            behaviour for callers that pass nothing.

    Returns:
        Markdown string with structured course notes.

    Raises:
        ValueError: If URL is invalid.
        RuntimeError: On API failure.
    """
    if prompt is None:
        prompt = YOUTUBE_TO_MARKDOWN_PROMPT
    return _sync_run(_add_youtube_and_ask(url, prompt))


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


def _fetch_video_meta(url: str) -> tuple[str, int | None]:
    """Return (title, duration_seconds) via yt-dlp, then an oEmbed fallback.

    Duration matters on its own: NotebookLM ingests the whole video as a
    single source and the chat RPC must return the *entire* answer in one
    response, so a multi-hour lecture produces a response that blows the
    client's 50 MiB cap. Knowing the duration before we start lets us fail
    in a second with advice instead of after NotebookLM has spent a minute
    ingesting and generating.
    """
    fmt = "%(title)s\t%(duration)s"
    base = ["yt-dlp", "--print", fmt, "--skip-download", "--no-playlist", "--no-warnings", url]
    cookies = os.environ.get(
        "YOUTUBE_COOKIES",
        str(Path.home() / ".studyflow-app" / "cookies" / "youtube_cookies.txt"),
    )
    cmds = [base, ["yt-dlp", "--cookies", cookies, *base[1:]]] if os.path.isfile(cookies) else [base]
    for cmd in cmds:
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=25)
            if r.returncode == 0 and r.stdout.strip():
                line = r.stdout.decode(errors="replace").splitlines()[0]
                title, _, dur = line.partition("\t")
                try:
                    return title.strip(), int(float(dur))
                except (TypeError, ValueError):
                    return title.strip(), None
        except Exception as e:
            logger.debug("yt-dlp metadata failed: %s", e)
    try:
        resp = requests.get("https://www.youtube.com/oembed", params={"format": "json", "url": url}, timeout=5)
        if resp.ok:
            return str(resp.json().get("title", "")).strip(), None
    except Exception as e:
        logger.debug("oEmbed title failed: %s", e)
    return "", None


def _fetch_video_title(url: str) -> str:
    """yt-dlp → oEmbed fallback chain. Motivation (#255): oEmbed 401s
    from the home server IP (works on Mac, fails in container), so
    yt-dlp runs first; oEmbed is the last resort. Never raises.
    """
    return _fetch_video_meta(url)[0]


# Past roughly this length the single-shot NotebookLM ask stops being viable:
# the answer has to come back in one RPC response and the client refuses
# anything over 50 MiB. A 7.5 h course produced a 52,449,345-byte response
# and failed with "RPC response exceeded 52428800 bytes" after a minute of
# ingestion, which reads like a bug but is really just a video that is too
# long for a one-shot ask.
_LONG_VIDEO_SECONDS = 3 * 3600  # 3 h

_LONG_VIDEO_MESSAGE = (
    "Este vídeo dura más de 3 horas y NotebookLM lo devuelve entero en una "
    "sola respuesta (tope de 50 MB), así que no cabe y además no se puede "
    "trocear: una URL de YouTube es una única fuente, y la consulta no "
    "admite un tramo de tiempo.\n\n"
    "Para vídeos largos usa el botón YouTube (☁️ OpenZen): ahí sí se trocea "
    "en un chunk por sección, y si un chunk falla puedes reintentarlo o "
    "saltarlo y seguir con el resto sin perder lo ya generado. No gasta "
    "cuota de NotebookLM."
)


def _read_task_options(task_id: str) -> dict:
    """Read the dialog options stored on a task row (issue #13).

    The worker runs in a background thread long after the request, so it
    must re-read the options from the row instead of relying on anything
    the caller held in memory. Missing or malformed values fall back to
    the defaults, so a task row written before #13 still runs unchanged.
    """
    row = query_one(
        """SELECT template_id, language, length, coverage_data
             FROM ai_tasks WHERE id = %s""",
        (task_id,),
    ) or {}

    mode = "unitema"
    raw = row.get("coverage_data") or ""
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                mode = parsed.get("mode") or "unitema"
        except (ValueError, TypeError):
            logger.warning("task %s: unparsable coverage_data, using unitema", task_id)

    return {
        "template_id": (row.get("template_id") or "").strip() or None,
        "language": (row.get("language") or "").strip() or "auto",
        # NOTE: the column is `length` (kept for parity with YouTubeZen and
        # the Phase 62 composer) but the composer parameter is `depth`, so it
        # is returned under the parameter name. The worker splats this dict
        # straight into build_native_youtube_prompt(**opts) — returning
        # "length" here raises TypeError and fails every task.
        "depth": (row.get("length") or "").strip() or "standard",
        "mode": mode,
    }


def _merge_coverage(task_id: str, extra: dict) -> str:
    """Merge `extra` into the task's existing coverage_data and re-serialise.

    Writing coverage_data wholesale here used to destroy the dialog options
    that create_youtube_md_task had already stored on the row — `mode`,
    `url`, `template_id`… The options are read by the worker from the row
    itself, so the damage was invisible server-side: the prompt came out
    right, the task succeeded, and the options simply vanished from the
    response. The frontend is what noticed, because `mode` is how
    por_tema is detected, so a "por tema" run arrived as a single block
    with no trace that per-topic mode had ever been requested.

    Kept separate from the UPDATE so the merge can be tested without a
    database, and so a malformed row degrades to `extra` instead of
    failing the task that has already done all the expensive work.
    """
    merged = dict(extra)
    row = query_one(
        "SELECT coverage_data FROM ai_tasks WHERE id = %s", (task_id,)
    ) or {}
    raw = row.get("coverage_data") or ""
    if raw:
        try:
            existing = json.loads(raw)
            if isinstance(existing, dict):
                # Existing keys win only where `extra` is empty; the freshly
                # fetched title/url must not resurrect a stale blank value.
                merged = {k: v for k, v in existing.items() if v not in (None, "")}
                merged.update({k: v for k, v in extra.items() if v not in (None, "")})
        except (ValueError, TypeError):
            logger.warning(
                "task %s: unparsable coverage_data on completion, "
                "overwriting with video metadata", task_id,
            )
    return json.dumps(merged, ensure_ascii=False)


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

        # Pre-flight the length before handing the video to NotebookLM. The
        # check is advisory: an unknown duration is not a reason to refuse,
        # because the metadata lookup itself can fail behind a network or a
        # missing cookie file, and the RPC guard below still catches the real
        # case. But when we *do* know the video is 7 hours long we can say so
        # in a second instead of after a minute of ingestion.
        if output_format == "markdown":
            try:
                _, duration = _fetch_video_meta(url)
                if duration and duration > _LONG_VIDEO_SECONDS:
                    logger.warning(
                        "task %s: refusing %ss video, over the one-shot limit",
                        task_id, duration,
                    )
                    raise ValueError(_LONG_VIDEO_MESSAGE)
            except ValueError:
                raise
            except Exception as e:
                logger.debug("task %s: duration pre-flight skipped: %s", task_id, e)

        _update_progress(
            task_id,
            "✅ URL válida\n"
            "⏳ Añadiendo video a NotebookLM…\n"
            "⏳ Procesando con Gemini…",
        )

        logger.info(f"YouTube task {task_id}: processing {url} as {output_format}")

        if output_format == "markdown":
            # Issue #13: compose the prompt from the dialog options. With
            # every option at its default this yields the original fixed
            # prompt, so the common path is unchanged.
            opts = _read_task_options(task_id)
            result = youtube_to_markdown(url, build_native_youtube_prompt(**opts))
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
        coverage_data = _merge_coverage(task_id, {
            "video_title": video_title,
            "video_url": url,
        })

        execute(
            """UPDATE ai_tasks
               SET status = 'done', result_content = %s, coverage_data = %s,
                   error_message = NULL, completed_at = NOW(), updated_at = NOW()
               WHERE id = %s""",
            (result, coverage_data, task_id),
        )

    except Exception as e:
        message = _humanise_task_error(e)
        logger.error(f"YouTube task {task_id}: failed — {e}")
        execute(
            """UPDATE ai_tasks
               SET status = 'error', error_message = %s,
                   completed_at = NOW(), updated_at = NOW()
               WHERE id = %s""",
            (message, task_id),
        )


def _humanise_task_error(exc: Exception) -> str:
    """Turn library-internal error text into something the user can act on.

    The raw failure for a long video was

        RPC response exceeded 52428800 bytes (read 52449345 bytes before
        aborting)

    which is a client-library guard, mentions two byte counts nobody can do
    anything with, and gives no hint that the cause is simply "this video is
    too long for a one-shot ask". Everything else is passed through, because
    an unfamiliar error is more useful verbatim than creatively rewritten.
    """
    text = str(exc)
    if "RPC response exceeded" in text:
        logger.warning("NotebookLM response blew past the RPC cap: %s", text)
        return _LONG_VIDEO_MESSAGE
    return text or exc.__class__.__name__


def _norm_topic_id(topic_id) -> int | None:
    """Normalise a topic id for the ``ai_tasks.topic_id`` INTEGER column.

    The frontend launches the YouTube dialog from a topic-less context too
    (and the stub dialog passes ``""``), which psycopg2 rejects with
    ``invalid input syntax for type integer: ""`` — a 500 before the task is
    even created. An empty/absent topic simply means "not attached", which is
    what NULL expresses.
    """
    if topic_id is None or topic_id == "":
        return None
    return int(topic_id)


def create_youtube_md_task(
    url: str,
    topic_id: str,
    block_id: str,
    user_id: str,
    template_id: str | None = None,
    depth: str = "standard",
    mode: str = "unitema",
    language: str = "auto",
) -> dict:
    """Create a YouTube → Markdown background task.

    Issue #13 — the four dialog options are stored on the task row so the
    background worker can rebuild the prompt. The storage layout mirrors
    YouTubeZen exactly: template_id / language / length in their own
    columns, and `mode` inside the coverage_data JSON, which already
    carries the video URL for this task type.

    Args:
        url: YouTube video URL.
        topic_id: Topic to associate the result with.
        block_id: Source block id (may be empty for standalone YouTube).
        user_id: User creating the task.
        template_id: MD_TEMPLATES key, or None for the standard role.
        depth: 'concise' | 'standard' | 'detailed'.
        mode: 'unitema' | 'por_tema'.
        language: 'auto' | 'es' | 'en'.

    Returns:
        {"task_id": str}
    """
    if not url:
        raise ValueError("Missing required field: url")

    if not validate_youtube_url(url):
        raise ValueError(f"Invalid YouTube URL: {url}")

    # Normalise once here so the stored row always matches what the worker
    # will read back, and so junk from the client never reaches the prompt.
    template_id = (template_id or "").strip() or None
    depth = (depth or "standard").strip().lower() or "standard"
    if depth not in ("concise", "standard", "detailed"):
        depth = "standard"
    mode = (mode or "unitema").strip().lower() or "unitema"
    if mode not in _VALID_MODES:
        mode = "unitema"
    language = (language or "auto").strip().lower() or "auto"

    task_row = execute_returning(
        """INSERT INTO ai_tasks
           (id, user_id, topic_id, task_type, format, source_type, source_id, status,
            template_id, language, length, coverage_data)
           VALUES (%s, %s, %s, 'youtube', 'markdown', 'youtube', %s, 'pending',
                   %s, %s, %s, %s)
           RETURNING id""",
        (
            _new_task_id(), user_id, _norm_topic_id(topic_id), block_id,
            template_id or "", language, depth,
            json.dumps({"url": url, "mode": mode}, ensure_ascii=False),
        ),
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
           (id, user_id, topic_id, task_type, format, source_type, source_id, status)
           VALUES (%s, %s, %s, 'youtube', 'html', 'youtube', %s, 'pending')
           RETURNING id""",
        (_new_task_id(), user_id, _norm_topic_id(topic_id), block_id),
    )
    task_id = task_row["id"]

    thread = threading.Thread(
        target=_run_youtube_task,
        args=(task_id, url, "html"),
        daemon=True,
    )
    thread.start()

    return {"task_id": task_id}
