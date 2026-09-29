"""
Audio generation via edge-tts — verbatim reading or AI-timed script.

Pipeline (duration="complete", default):
1. Fetch block content from DB
2. Sanitize the content for speech (strip markdown tables/callouts/code/emojis)
3. Call edge_tts to generate MP3 from the sanitized content (verbatim, no AI rewrite)
4. Save MP3 to /data/audio/
5. Update ai_tasks with the audio URL path + the source content as transcript
   in coverage_data (audio and transcript coincide by construction — Issue #123)

Pipeline (duration="5"|"10"|"20", Issue #130):
1. Fetch block content from DB
2. Ask the configured AI provider (NotebookLM/Gemini by default; OpenZEN
   when it lands) to rewrite the content into a script that fits the
   target minutes (~150 words/min word budget)
3. Persist the provider-reported token usage to ai_usage_log (source='mp3')
   for cost tracking
4. Call edge_tts to generate MP3 from the generated script
5. Save MP3 to /data/audio/
6. Update ai_tasks with the audio URL path + the generated script as transcript
   (audio and transcript coincide by construction — single AI rewrite, no drift)

Voices (per language):
    es → es-ES-AlvaroNeural  (Álvaro)
    en → en-US-ChristopherNeural (Christopher)

The result_content stored is a URL path like "/api/ai/notebooklm/audio/xxx.mp3"
which the frontend uses to construct an <audio> player. The transcript
(the narrated text, verbatim or script) is stored in coverage_data as JSON
{"transcript_md": "..."} so the frontend can offer inserting it as a
Markdown block alongside the MP3.
"""

import asyncio
import json
import os
import re
import threading
from pathlib import Path

import edge_tts

from database import execute, execute_returning
from ai.notebooklm.prompts import (
    AUDIO_SCRIPT_PROMPT_DURATION,
    AUDIO_SCRIPT_PROMPT_DURATION_EN,
)
from ai.providers import get_provider

# ── config ──────────────────────────────────────────────────────────

_AUDIO_DIR = os.environ.get(
    "AUDIO_UPLOAD_FOLDER",
    str(Path.home() / ".studyflow-app" / "audio"),
)

# ── language → voice mapping ────────────────────────────────────────

_LANG_CONFIG = {
    "es": {"voice": "es-ES-AlvaroNeural"},
    "en": {"voice": "en-US-ChristopherNeural"},
}

_TTS_VOICE = "es-ES-AlvaroNeural"  # fallback for legacy callers

# ── duration → word budget (~150 words/min TTS narration) ────────────

_WORDS_PER_MINUTE = 150

_DURATION_WORD_BUDGET = {
    "5": _WORDS_PER_MINUTE * 5,    # 750 words
    "10": _WORDS_PER_MINUTE * 10,  # 1500 words
    "20": _WORDS_PER_MINUTE * 20,  # 3000 words
}

_VALID_DURATIONS = ("complete",) + tuple(_DURATION_WORD_BUDGET.keys())


def _validate_duration(duration: str) -> None:
    """Raise ValueError unless duration is one of 'complete'|'5'|'10'|'20'."""
    if duration not in _VALID_DURATIONS:
        raise ValueError(
            f"duration must be one of {_VALID_DURATIONS}, got '{duration}'"
        )


def _ensure_dirs():
    """Ensure the output directory exists."""
    os.makedirs(_AUDIO_DIR, exist_ok=True)


def _sanitize_for_speech(text: str) -> str:
    """Strip markdown, tables, callouts, code, emojis and special chars.

    The MP3 reads the source content verbatim, so this only removes
    visual/syntax noise that should not be spoken aloud — never rewrites
    the actual words.
    """
    # Remove code blocks (keep inline code content, drop the backticks)
    text = re.sub(r'```[\s\S]*?```', '', text)
    text = re.sub(r'`([^`]+)`', r'\1', text)
    # Remove markdown links (keep link text)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    # Remove bold/italic
    text = text.replace('**', '').replace('__', '').replace('*', '').replace('~~', '')
    # Remove heading markers
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    # Remove URLs
    text = re.sub(r'https?://\S+', '', text)
    # Remove table rows and separator lines
    text = re.sub(r'^\|.*\|$', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\|?[-:|\s]+\|?\s*$', '', text, flags=re.MULTILINE)
    # Remove callout markers (keep the callout body text)
    text = re.sub(r'^>\s*\[![^\]]*\]\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'^>\s?', '', text, flags=re.MULTILINE)
    # Remove emojis and other non-spoken symbols
    text = re.sub(r'[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u2B00-\u2BFF\u2190-\u21FF]', '', text)
    # Collapse multiple spaces/newlines
    text = re.sub(r'  +', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _generate_speech(text: str, voice: str, output_path: str) -> None:
    """Synchronous wrapper around edge_tts — saves MP3 to output_path."""
    async def _run():
        communicate = edge_tts.Communicate(text, voice)
        audio_data = b""
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_data += chunk["data"]
        if not audio_data:
            raise RuntimeError("edge-tts produced no audio data")
        with open(output_path, "wb") as f:
            f.write(audio_data)

    asyncio.run(_run())


def _generate_duration_script(
    content_text: str,
    language: str,
    minutes: str,
    provider_name: str = "notebooklm",
) -> tuple[str, dict | None]:
    """Ask an AI provider to rewrite the content into a script fitting ~minutes min.

    Uses the duration-tuned prompt with a word budget (~150 words/min).
    Returns the plain-text script, which is then narrated by edge-tts and
    stored as the transcript — so audio and transcript coincide by
    construction (single AI rewrite, no drift, per Issue #130/#123).

    Also returns the provider-reported token usage (or None if the
    provider did not expose it) so the caller can persist it to
    ai_usage_log for cost tracking.

    Args:
        content_text: The full text content of the source block.
        language: Target language code ('es' or 'en').
        minutes: Target duration key, one of '5'|'10'|'20'.
        provider_name: AI provider used to write the script.
            'notebooklm' (default, Gemini) or 'opencode-acp' (OpenZEN,
            deferred until the OpenCode sidecar exists).

    Returns:
        (script, usage) — the plain-text audio script and the usage dict
        {input_tokens, output_tokens, total_tokens, cost, ...} or None.

    Raises:
        RuntimeError: If the provider returns an empty response.
    """
    prompt = (
        AUDIO_SCRIPT_PROMPT_DURATION_EN
        if language == "en"
        else AUDIO_SCRIPT_PROMPT_DURATION
    )
    system_prompt = prompt.format(
        minutes=minutes,
        words=_DURATION_WORD_BUDGET[minutes],
    )
    provider = get_provider(provider_name)
    result = provider.chat(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content_text},
        ]
    )
    script = (result.get("content") or "").strip()
    if not script:
        raise RuntimeError("El proveedor devolvió una respuesta vacía")
    usage = result.get("usage")
    return script, usage


# ── progress helper ──────────────────────────────────────────────────


def _update_progress(task_id: str, message: str) -> None:
    """Update ai_tasks error_message with current progress checklist."""
    execute(
        "UPDATE ai_tasks SET error_message = %s, updated_at = NOW() WHERE id = %s",
        (message, task_id),
    )


def _log_usage(
    task_id: str,
    user_id: str,
    duration: str,
    usage: dict | None,
    task_type: str = "notebooklm_audio",
) -> None:
    """Persist provider token usage to ai_usage_log (source='mp3').

    Only writes a row when usage data is present (i.e. an AI call
    actually consumed tokens — duration != 'complete').  For verbatim
    audio there is no AI call, so nothing is logged.

    Args:
        task_id: The ai_tasks row id.
        user_id: The user owning the task.
        duration: The target duration key ('5'|'10'|'20').
        usage: The provider-reported usage dict, or None to skip.
        task_type: 'notebooklm_audio' (Gemini) or 'opencode_audio' (OpenZEN).
    """
    if not usage:
        return
    execute(
        """INSERT INTO ai_usage_log
           (user_id, task_id, task_type, source, duration,
            model_id, provider_id,
            input_tokens, output_tokens, reasoning_tokens,
            cache_read_tokens, cache_write_tokens, total_tokens, cost)
           VALUES (%s, %s, %s, 'mp3', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            user_id,
            task_id,
            task_type,
            duration,
            usage.get("model_id"),
            usage.get("provider_id"),
            usage.get("input_tokens", 0),
            usage.get("output_tokens", 0),
            usage.get("reasoning_tokens", 0),
            usage.get("cache_read_tokens", 0),
            usage.get("cache_write_tokens", 0),
            usage.get("total_tokens", 0),
            usage.get("cost", 0),
        ),
    )


# ── background task ──────────────────────────────────────────────────


def _run_audio_task(
    task_id: str,
    user_id: str,
    content_text: str,
    language: str = "es",
    duration: str = "complete",
    provider_name: str = "notebooklm",
    task_type: str = "notebooklm_audio",
) -> None:
    """Background thread: content → (verbatim | AI script) → edge-tts → MP3.

    duration="complete" → the MP3 reads the source content verbatim (no AI
        rewrite), and the same content is stored as the transcript — audio
        and transcript coincide by construction (Issue #123).
    duration="5"|"10"|"20" → the configured AI provider rewrites the content
        into a script that fits the target minutes; that script is both
        narrated and stored as the transcript — audio and transcript still
        coincide (Issue #130). The provider token usage is persisted to
        ai_usage_log (source='mp3').

    Args:
        task_id: The ai_tasks row id to update.
        user_id: The user owning the task (for ai_usage_log).
        content_text: The full text content of the source block.
        language: Target language code ('es' or 'en').
        duration: 'complete' (verbatim) or '5'|'10'|'20' (AI-timed).
        provider_name: AI provider for the timed script — 'notebooklm'
            (default, Gemini) or 'opencode-acp' (OpenZEN, deferred).
        task_type: ai_tasks/ai_usage_log task_type to record with.
    """
    _validate_duration(duration)
    _ensure_dirs()

    try:
        execute(
            "UPDATE ai_tasks SET status = 'processing', updated_at = NOW() WHERE id = %s",
            (task_id,),
        )

        if duration == "complete":
            # ── Verbatim path: sanitize source content for speech ───────
            _update_progress(
                task_id,
                "⏳ Preparando contenido…",
            )
            clean_script = _sanitize_for_speech(content_text)
            if not clean_script:
                raise RuntimeError("Content is empty after TTS sanitization")
            transcript_md = content_text
            usage = None
            _update_progress(
                task_id,
                "✅ Contenido preparado\n"
                "⏳ Generando audio con TTS…",
            )
        else:
            # ── Duration path: AI rewrites into a timed script ───────────
            provider_label = (
                "NotebookLM" if provider_name == "notebooklm" else "OpenZEN"
            )
            _update_progress(
                task_id,
                f"🤖 Generando guion de {duration} minutos con {provider_label}…",
            )
            clean_script, usage = _generate_duration_script(
                content_text, language, duration, provider_name
            )
            transcript_md = clean_script
            _update_progress(
                task_id,
                "✅ Guion generado\n"
                "⏳ Generando audio con TTS…",
            )

        # ── Generate MP3 via edge-tts ──────────────────────────────────
        audio_filename = f"audio_{task_id}.mp3"
        audio_path = os.path.join(_AUDIO_DIR, audio_filename)
        tts_voice = _LANG_CONFIG.get(language, _LANG_CONFIG["es"])["voice"]
        _generate_speech(clean_script, tts_voice, audio_path)

        if not os.path.isfile(audio_path) or os.path.getsize(audio_path) == 0:
            raise RuntimeError("TTS produced empty audio file")

        # ── Persist token usage for AI-generated scripts ───────────────
        _log_usage(task_id, user_id, duration, usage, task_type)

        # ── Result URL for the frontend audio player ───────────────────
        # Must match the registered route in routes/notebooklm_content.py.
        # (The legacy /api/audio/serve/ alias still exists for rows written
        # before this fix, but new rows use the canonical path.)
        audio_url = f"/api/ai/notebooklm/audio/{audio_filename}"

        # Transcript = what is narrated (verbatim source or generated script)
        coverage_json = json.dumps({"transcript_md": transcript_md})

        execute(
            """UPDATE ai_tasks
               SET status = 'done', result_content = %s, error_message = NULL,
                   coverage_data = %s,
                   completed_at = NOW(), updated_at = NOW()
               WHERE id = %s""",
            (audio_url, coverage_json, task_id),
        )

    except Exception as e:
        execute(
            """UPDATE ai_tasks
               SET status = 'error', error_message = %s,
                   completed_at = NOW(), updated_at = NOW()
               WHERE id = %s""",
            (str(e), task_id),
        )


# ── public task creator ──────────────────────────────────────────────


def create_audio_task(
    block_ids: str | list[str],
    topic_id: str,
    user_id: str,
    language: str = "es",
    duration: str = "complete",
    provider_name: str = "notebooklm",
) -> dict:
    """Create an audio generation background task.

    Args:
        block_ids: One or more content/markdown block ids to process
            (Phase 45 multi-markdown selection). A single id (legacy)
            behaves exactly as before (`block_id` → `[block_id]`).
        topic_id: The topic to associate the result with.
        user_id: The user creating the task.
        language: Target language code ('es' or 'en'). Default 'es'.
        duration: 'complete' (verbatim, default) or '5'|'10'|'20' minutes.
        provider_name: AI provider used when duration != 'complete':
            'notebooklm' (Gemini, default) or 'opencode-acp' (OpenZEN,
            deferred). Verbatim audio ('complete') never uses the provider.

    Returns:
        {"task_id": str}

    Raises:
        ValueError: If input validation fails or blocks are not found.
    """
    _validate_duration(duration)
    if not block_ids:
        raise ValueError("Missing required field: block_ids")

    if provider_name not in ("notebooklm", "opencode-acp"):
        raise ValueError(f"Unsupported provider: {provider_name}")

    task_type = (
        "notebooklm_audio" if provider_name == "notebooklm" else "opencode_audio"
    )

    from ai.notebooklm.utils import _coerce_id, _new_task_id, _resolve_blocks_content

    content_text, source_type, source_id = _resolve_blocks_content(block_ids, user_id)
    if not content_text:
        raise ValueError("Block has no content")

    topic_id_int = _coerce_id(topic_id, "topic_id")
    task_row = execute_returning(
        """INSERT INTO ai_tasks
           (id, user_id, topic_id, task_type, format, source_type, source_id, status)
           VALUES (%s, %s, %s, %s, 'audio', %s, %s, 'pending')
           RETURNING id""",
        (_new_task_id(), user_id, topic_id_int, task_type, source_type, source_id),
    )
    task_id = task_row["id"]

    thread = threading.Thread(
        target=_run_audio_task,
        args=(task_id, user_id, content_text, language, duration, provider_name, task_type),
        daemon=True,
    )
    thread.start()

    return {"task_id": task_id}
