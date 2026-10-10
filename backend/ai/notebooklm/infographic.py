"""
Infographic generation via NotebookLM Artifacts API.

Pipeline:
1. Fetch block content from DB
2. Save to temporary text file
3. Create notebook + add file as source
4. Call NotebookLM artifacts.generate_infographic() → GenerationStatus
5. wait_for_completion()
6. list_infographics() → Artifact
7. download_infographic() → save PNG to ~/.studyflow-app/infographics/
8. Update ai_tasks with the image URL path

The result_content stored is a URL path like "/api/ai/notebooklm/infographic/xxx.png"
which the frontend uses to construct an <img> tag.

Text helpers (markdown→plain, truncation, language detection) live in
``ai.notebooklm.infographic_text`` (v3 split to keep both modules <500L).
"""

import asyncio
import logging
import os
import threading
import time

try:  # pragma: no cover - optional, heavy SDK
    from notebooklm.types import (
        InfographicOrientation,
        InfographicDetail,
        InfographicStyle,
    )
except ImportError:  # pragma: no cover
    # These maps below are built at import time, so a plain "None" would crash
    # the app on a clean install. The SDK enums are string-valued, so these
    # stand-ins keep the maps valid; nothing here is ever sent to NotebookLM
    # without the SDK installed, because the calling route fails first.
    class _StrEnum:
        def __init__(self, name: str, value: str):
            self.name, self.value = name, value

        def __repr__(self):
            return f"<{type(self).__name__}.{self.name}: {self.value!r}>"

    class InfographicOrientation:
        LANDSCAPE = _StrEnum("LANDSCAPE", "landscape")
        PORTRAIT = _StrEnum("PORTRAIT", "portrait")
        SQUARE = _StrEnum("SQUARE", "square")

    class InfographicDetail:
        CONCISE = _StrEnum("CONCISE", "concise")
        STANDARD = _StrEnum("STANDARD", "standard")
        DETAILED = _StrEnum("DETAILED", "detailed")

    class InfographicStyle:
        AUTO_SELECT = _StrEnum("AUTO_SELECT", "auto_select")
        SKETCH_NOTE = _StrEnum("SKETCH_NOTE", "sketch_note")
        PROFESSIONAL = _StrEnum("PROFESSIONAL", "professional")
        BENTO_GRID = _StrEnum("BENTO_GRID", "bento_grid")
        EDITORIAL = _StrEnum("EDITORIAL", "editorial")
        INSTRUCTIONAL = _StrEnum("INSTRUCTIONAL", "instructional")
        BRICKS = _StrEnum("BRICKS", "bricks")
        CLAY = _StrEnum("CLAY", "clay")
        ANIME = _StrEnum("ANIME", "anime")
        KAWAII = _StrEnum("KAWAII", "kawaii")
        SCIENTIFIC = _StrEnum("SCIENTIFIC", "scientific")

from database import execute, execute_returning

from ai.notebooklm.infographic_text import (
    _strip_markdown,
    _truncate_content,
    _infographic_max_chars,
    _truncate_target_lang,
    _detect_language,
)

logger = logging.getLogger(__name__)

# ── config ────────────────────────────────────────────────────────────

# storage_paths resolves this: INFOGRAPHIC_UPLOAD_FOLDER when the deploy
# sets it, else ~/.studyflow-app/infographics -- the folder the backup
# export reads and the restore writes, so a restored PNG is served by the
# same route that generated it.
from storage_paths import category_dir  # noqa: E402  (kept with the config)

_INFOGRAPHIC_DIR = category_dir("infographics")
_TEMP_DIR = "/tmp/notebooklm-infographic"
_NOTEBOOK_PREFIX = "tmp-infographic"
_SOURCE_WAIT_TIMEOUT = 120.0


def _sync_run(coro):
    """Run a coroutine synchronously, even inside an existing event loop."""
    import concurrent.futures

    def _run():
        return asyncio.run(coro)

    # Always run in a fresh thread to avoid nested-loop issues
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_run).result()

from ai.notebooklm.utils import get_active_profile as _get_active_profile

def _ensure_dirs():
    """Ensure output and temp directories exist."""
    os.makedirs(_INFOGRAPHIC_DIR, exist_ok=True)
    os.makedirs(_TEMP_DIR, exist_ok=True)

# ── progress helper ──────────────────────────────────────────────────

def _update_progress(task_id: str, message: str) -> None:
    """Update ai_tasks error_message with current progress checklist."""
    execute(
        "UPDATE ai_tasks SET error_message = %s, updated_at = NOW() WHERE id = %s",
        (message, task_id),
    )

# ── enum mapping ─────────────────────────────────────────────────────
# Maps user-friendly string keys → NotebookLM SDK enums.
# These are defined in notebooklm.rpc.types and re-exported from notebooklm.
# Using them directly avoids the redundant string → int → enum detour.

_ORIENTATION_MAP = {
    "landscape": InfographicOrientation.LANDSCAPE,
    "portrait": InfographicOrientation.PORTRAIT,
    "square": InfographicOrientation.SQUARE,
}

_DETAIL_MAP = {
    "concise": InfographicDetail.CONCISE,
    "standard": InfographicDetail.STANDARD,
    "detailed": InfographicDetail.DETAILED,
}

_STYLE_MAP = {
    "auto": InfographicStyle.AUTO_SELECT,
    "auto_select": InfographicStyle.AUTO_SELECT,  # legacy alias
    "sketch-note": InfographicStyle.SKETCH_NOTE,
    "sketch_note": InfographicStyle.SKETCH_NOTE,  # legacy alias
    "professional": InfographicStyle.PROFESSIONAL,
    "bento-grid": InfographicStyle.BENTO_GRID,
    "editorial": InfographicStyle.EDITORIAL,
    "instructional": InfographicStyle.INSTRUCTIONAL,
    "bricks": InfographicStyle.BRICKS,
    "clay": InfographicStyle.CLAY,
    "anime": InfographicStyle.ANIME,
    "kawaii": InfographicStyle.KAWAII,
    "scientific": InfographicStyle.SCIENTIFIC,
}

# ── background task ──────────────────────────────────────────────────

def _run_infographic_task(
    task_id: str,
    content_text: str,
    orientation: str,
    detail_level: str,
    style: str,
    instructions: str | None,
    language: str | None = None,
) -> None:
    """Background thread: generate infographic via NotebookLM artifacts."""
    _ensure_dirs()

    try:
        execute(
            "UPDATE ai_tasks SET status = 'processing', updated_at = NOW() WHERE id = %s",
            (task_id,),
        )
        _update_progress(
            task_id,
            "⏳ Preparando contenido…",
        )

        # ── 1. Save content to a temp text file for NotebookLM upload ──
        # NOTE: _sanitize_for_notebooklm was REMOVED — it destroyed content
        # by matching ASCII chars in its emoji regex. _strip_markdown() handles
        # all cleaning: markdown syntax, structure, emojis → narrative text.
        ts = int(time.time())
        temp_filename = f"content_{ts}.txt"
        temp_path = os.path.join(_TEMP_DIR, temp_filename)
        clean_text = _strip_markdown(content_text)

        # Truncate for NotebookLM — very long sources hang the infographic in
        # "pending" forever (previously stuck at 600s timeout). We only trim
        # the copy uploaded to NotebookLM; the stored block is untouched.
        original_len = len(clean_text)
        max_chars = _infographic_max_chars(_truncate_target_lang(language, content_text))
        if original_len > max_chars:
            clean_text = _truncate_content(clean_text, max_chars)
            logger.warning(
                "[infographic] content truncated %d → %d chars (lang ceiling=%d, "
                "NotebookLM infographic size limit)",
                original_len, len(clean_text), max_chars,
            )

        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(clean_text)

        _update_progress(
            task_id,
            "✅ Contenido preparado (sanitizado)\n"
            "⏳ Generando infografía con NotebookLM…",
        )

        # ── 2. Create notebook + add source + generate infographic ────
        from notebooklm import NotebookLMClient

        profile = _get_active_profile()
        kwargs = {"profile": profile} if profile else {}

        image_path = _sync_run(
            _generate_infographic(
                NotebookLMClient,
                kwargs,
                temp_path,
                orientation,
                detail_level,
                style,
                instructions,
                task_id,
                language=language,
                content_text=content_text,
            )
        )

        if not image_path or not os.path.isfile(image_path):
            raise RuntimeError("Failed to generate infographic image")

        # Clean up temp file
        try:
            os.remove(temp_path)
        except OSError:
            pass

        # ── 4. Result URL for the frontend image tag ──────────────────
        image_filename = os.path.basename(image_path)
        image_url = f"/api/ai/notebooklm/infographic/{image_filename}"

        execute(
            """UPDATE ai_tasks
               SET status = 'done', result_content = %s, error_message = NULL,
                   completed_at = NOW(), updated_at = NOW()
               WHERE id = %s""",
            (image_url, task_id),
        )

    except Exception as e:
        execute(
            """UPDATE ai_tasks
               SET status = 'error', error_message = %s,
                   completed_at = NOW(), updated_at = NOW()
               WHERE id = %s""",
            (str(e), task_id),
        )

async def _generate_infographic(
    ClientClass,
    client_kwargs: dict,
    temp_path: str,
    orientation: str,
    detail_level: str,
    style: str,
    instructions: str | None,
    task_id: str,
    language: str | None = None,
    content_text: str | None = None,
) -> str:
    """Async helper: upload temp file → generate infographic → download PNG.

    SDK pipeline:
      1. generate_infographic() → GenerationStatus (task_id, status, url)
      2. wait_for_completion(notebook_id, task_id) → final GenerationStatus
      3. list_infographics(notebook_id) → list[Artifact]
      4. download_infographic(notebook_id, output_path, artifact_id) → file

    Returns:
        Absolute path to the saved PNG file.

    Raises:
        RuntimeError: On any failure.
    """
    # Map string params to SDK enums (imported at module level above)
    or_enum = _ORIENTATION_MAP.get(orientation)
    det_enum = _DETAIL_MAP.get(detail_level)
    sty_enum = _STYLE_MAP.get(style)
    if not or_enum:
        raise ValueError(f"Invalid orientation '{orientation}'. Choose: {', '.join(_ORIENTATION_MAP)}")
    if not det_enum:
        raise ValueError(f"Invalid detail_level '{detail_level}'. Choose: {', '.join(_DETAIL_MAP)}")
    if not sty_enum:
        raise ValueError(f"Invalid style '{style}'. Choose: {', '.join(_STYLE_MAP)}")

    async with ClientClass.from_storage(**client_kwargs) as client:
        ts = int(time.time())
        nb = await client.notebooks.create(f"{_NOTEBOOK_PREFIX}-{ts}")

        try:
            # ── 1. Add temp text file as source ──────────────────────
            source = await client.sources.add_file(
                nb.id, temp_path, wait=True, wait_timeout=_SOURCE_WAIT_TIMEOUT
            )
            if not source or not source.id:
                raise RuntimeError("Failed to add content to NotebookLM")

            execute(
                "UPDATE ai_tasks SET error_message = %s, updated_at = NOW() WHERE id = %s",
                ("✅ Contenido preparado\n⏳ Generando infografía con NotebookLM…", task_id),
            )

            # ── 2. Generate infographic (async) → GenerationStatus ───
            # Determine language: explicit param > auto-detect from content
            if language and language != "auto":
                infographic_lang = language
            elif content_text:
                infographic_lang = _detect_language(content_text)
            else:
                infographic_lang = "es"  # fallback

            # ── Default instructions: force teaching infographic ───────
            # Without explicit instructions, Gemini defaults to structural
            # document analysis (THE ARCHITECTURE OF SYNTAX, PHASE 1, etc.)
            # instead of creating a visual teaching infographic.
            default_instructions = (
                "Create a visual teaching infographic from this educational content. "
                "Show the KEY CONCEPTS, RULES, and EXAMPLES of the content "
                "as visual elements (cards, icons, diagrams). "
                "Do NOT analyze the document structure. "
                "Do NOT describe formatting or syntax. "
                "Focus ONLY on the teaching content and make it visually engaging."
            )
            effective_instructions = instructions.strip() if instructions else ""
            if not effective_instructions:
                effective_instructions = default_instructions

            # Determine the language the USER wants the infographic written in
            # (what Gemini must produce). This is independent of the internal
            # `language` param NotebookLM needs to GET the generation running
            # (forcing 'en' there can fail/finish-failed silently → fall back).
            if language and language != "auto":
                output_lang = language
            else:
                output_lang = infographic_lang  # auto-detected (en|es)

            # If the user wants the infographic written in English, force Gemini
            # to produce English via the instructions — independent of the
            # internal `language` param NotebookLM needs to get generation going.
            if output_lang == "en":
                lang_directive = (
                    "IMPORTANT: Write ALL text of the infographic IN ENGLISH — "
                    "every heading, label, title, and caption. The source content "
                    "is English; produce the infographic fully in English even if "
                    "other settings suggest otherwise."
                )
                effective_instructions = f"{effective_instructions}\n\n{lang_directive}"

            # NotebookLM fails when forcing English (observed: status/failed
            # with error=None). The failure can surface either as a rejected
            # initial status OR as a failed wait_for_completion AFTER 'pending'.
            # So we loop over candidate languages doing the FULL generate+wait
            # per attempt, falling back to the next language when one fails.
            final_status = None
            failed_msgs = []
            candidates = [infographic_lang]
            for alt in ("es", "auto"):
                if alt not in candidates:
                    candidates.append(alt)
            for attempt_lang in candidates:
                logger.warning("[infographic] attempt lang=%s (candidates=%s)",
                               attempt_lang, candidates)
                gen_status = None
                try:
                    gen_status = await client.artifacts.generate_infographic(
                        nb.id,
                        source_ids=[source.id],
                        language=attempt_lang,
                        orientation=or_enum,
                        detail_level=det_enum,
                        style=sty_enum,
                        instructions=effective_instructions or None,
                    )
                except RuntimeError:
                    raise
                except Exception as e:
                    logger.error("[infographic] generate_infographic lang=%s raised %s: %s",
                                 attempt_lang, type(e).__name__, e)
                    failed_msgs.append(f"{attempt_lang}: {type(e).__name__}: {e}")
                    continue

                logger.warning("[infographic] gen_status=%s (task_id=%s, status=%s, error=%s, error_code=%s, lang=%s)",
                               gen_status, getattr(gen_status, 'task_id', '?'),
                               getattr(gen_status, 'status', '?'),
                                   getattr(gen_status, 'error', '?'),
                                   getattr(gen_status, 'error_code', '?'),
                                   attempt_lang)

                if not gen_status:
                    failed_msgs.append(f"{attempt_lang}: no status")
                    continue
                if gen_status.status == "failed":
                    failed_msgs.append(f"{attempt_lang}: {gen_status.error or 'no error details'}")
                    logger.warning("[infographic] lang=%s failed at gen (err=%s) — next candidate",
                                   attempt_lang, gen_status.error)
                    continue

                # ── Wait for completion ─────────────────────────────
                try:
                    fs = await client.artifacts.wait_for_completion(
                        nb.id,
                        gen_status.task_id,
                        timeout=600.0,
                    )
                except Exception as wait_err:
                    logger.error("[infographic] wait_for_completion(lang=%s) raised: %s %s",
                                 attempt_lang, type(wait_err).__name__, wait_err)
                    failed_msgs.append(f"{attempt_lang}: wait {type(wait_err).__name__}: {wait_err}")
                    raise  # timeout is fatal, don't keep retrying

                logger.warning("[infographic] final_status=%s (status=%s, error=%s)",
                               fs, getattr(fs, 'status', '?'), getattr(fs, 'error', '?'))
                if fs and fs.status in ("complete", "completed", "success"):
                    final_status = fs
                    gen_status = fs  # reuse for later wait/url checks
                    infographic_lang = attempt_lang
                    break
                if fs and fs.status == "failed":
                    failed_msgs.append(f"{attempt_lang}: {fs.error or 'no error details'}")
                    logger.warning("[infographic] lang=%s failed after wait (err=%s) — next candidate",
                                   attempt_lang, fs.error)
                    continue
                # Unknown status → treat as failure of this candidate
                failed_msgs.append(
                    f"{attempt_lang}: ended with '{getattr(fs, 'status', '?')}'"
                )
                continue

            if final_status is None:
                raise RuntimeError(
                    f"Infographic generation failed in all languages (tried: {', '.join(candidates)}). "
                    f"Details: {'; '.join(failed_msgs) or 'no details'}. "
                    f"This looks like a NotebookLM/Google-side limitation (English is prone to "
                    f"silent failures) or a temporary quota/rate limit."
                )

            # ── 4. List artifacts to find the infographic ─────────────
            artifacts = await client.artifacts.list_infographics(nb.id)
            if not artifacts:
                raise RuntimeError("No infographic artifacts found after generation")

            # Use the first (most recent) infographic artifact
            artifact = artifacts[0]

            # ── 5. Download infographic PNG ──────────────────────────
            image_filename = f"infographic_{task_id}.png"
            image_path = os.path.join(_INFOGRAPHIC_DIR, image_filename)

            await client.artifacts.download_infographic(
                nb.id,
                image_path,
                artifact_id=artifact.id,
            )

            if not os.path.isfile(image_path):
                raise RuntimeError("Download completed but PNG file not found on disk")

            return image_path

        finally:
            try:
                await client.notebooks.delete(nb.id)
            except Exception:
                pass

# ── public task creator ──────────────────────────────────────────────

def create_infographic_task(
    block_ids: str | list[str],
    topic_id: str,
    user_id: str,
    orientation: str = "landscape",
    detail_level: str = "standard",
    style: str = "auto_select",
    instructions: str | None = None,
    language: str | None = None,
) -> dict:
    """Create an infographic generation background task (multi-block).

    Returns {"task_id": str}. Raises ValueError on invalid params/content.
    """
    # Validate params before touching the DB
    if orientation not in _ORIENTATION_MAP:
        raise ValueError(f"Invalid orientation '{orientation}'. Choose: {', '.join(_ORIENTATION_MAP)}")
    if detail_level not in _DETAIL_MAP:
        raise ValueError(f"Invalid detail_level '{detail_level}'. Choose: {', '.join(_DETAIL_MAP)}")
    if style not in _STYLE_MAP:
        raise ValueError(f"Invalid style '{style}'. Choose: {', '.join(_STYLE_MAP)}")

    from ai.notebooklm.utils import _coerce_id, _new_task_id, _resolve_blocks_content

    content_text, source_type, source_id = _resolve_blocks_content(block_ids, user_id)
    if not content_text:
        raise ValueError("Selected blocks have no content")

    topic_id_int = _coerce_id(topic_id, "topic_id")
    task_row = execute_returning(
        """INSERT INTO ai_tasks
           (id, user_id, topic_id, task_type, format, source_type, source_id, status)
           VALUES (%s, %s, %s, 'notebooklm_infographic', 'infographic', %s, %s, 'pending')
           RETURNING id""",
        (_new_task_id(), user_id, topic_id_int, source_type, source_id),
    )
    task_id = task_row["id"]

    thread = threading.Thread(
        target=_run_infographic_task,
        args=(task_id, content_text, orientation, detail_level, style, instructions, language),
        daemon=True,
    )
    thread.start()

    return {"task_id": task_id}
