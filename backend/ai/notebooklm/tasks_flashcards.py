"""
NotebookLM Flashcards Generation (✨ Nb Flashcards, Phase 47) — v3 port
of v2 ai/notebooklm/tasks.py, split #4/4.

Generates flashcards via OpenZEN (``opencode-acp`` provider). In v3 the
``opencode-acp`` provider is NOT registered yet (its sub-phase), so
``get_provider`` raises a clear ValueError until OpenZEN lands — the task
is created as 'error' in that case.

Exports:
    create_notebooklm_flashcards_task

NOTE: this module intentionally imports ``_repair_invalid_escapes`` from
``ai.notebooklm.tasks_pdf`` (shared JSON-repair helper).
"""

import json
import logging
import re
import threading

from database import execute, execute_returning

from ai.notebooklm.tasks_pdf import _repair_invalid_escapes, _update_progress

# Levels are MAXIMUMS, not fixed counts: the model extracts ALL key concepts,
# prioritizes them and returns the best ≤5/≤15/≤25 (user decision #2).
FLASHCARD_LEVEL_MAX = {"concise": 5, "standard": 15, "detailed": 25}


def _ask_opencode_flashcards(content_text: str, max_cards: int) -> str:
    """Send content to OpenZEN (opencode-acp) and ask for flashcards."""
    from ai.notebooklm.prompts import FLASHCARDS_GENERATION_PROMPT
    from ai.providers import get_provider

    logger = logging.getLogger(__name__)
    provider = get_provider("opencode-acp")
    system_prompt = FLASHCARDS_GENERATION_PROMPT.format(max_cards=max_cards)
    logger.info("[Flashcards] Calling OpenZEN (max_cards=%d, content_len=%d)", max_cards, len(content_text))
    result = provider.chat(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content_text},
        ]
    )
    answer = (result.get("content") or "").strip()
    logger.info("[Flashcards] OpenZEN response len=%d, preview=%.200s", len(answer), answer)
    if not answer:
        raise RuntimeError("OpenZEN devolvió flashcards vacías")
    return answer


def _strip_code_fences(text: str) -> str:
    """Remove the first markdown code fence (```json ... ```) wrapping JSON.

    OpenZEN sometimes wraps the answer in a fence even though the prompt
    forbids it. The fence may sit at the start OR be surrounded by prose,
    so we locate the first ```(?:json)? block instead of requiring the
    whole text to be a fence.
    """
    m = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text


def _extract_json_object(text: str) -> str:
    """Return the outermost JSON object embedded in conversational text.

    OpenZEN sometimes answers with prose around the JSON ("Aquí tienes las
    flashcards: {...}" or a trailing summary). ``json.loads`` on the full
    text then fails at char 0 ("Expecting value: line 1 column 1"). We try
    ``json.JSONDecoder.raw_decode`` starting at each ``{`` and return the
    first valid object span, ignoring surrounding chatter.
    """
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start != -1:
        try:
            obj, end = decoder.raw_decode(text[start:])
            return text[start : start + end]
        except json.JSONDecodeError:
            start = text.find("{", start + 1)
    return text


def _validate_flashcards_json(raw: str, max_cards: int) -> dict:
    """Parse and sanitize the model JSON into {cards, total_concepts}."""
    cleaned = _strip_code_fences(raw.strip())
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        try:
            # Model wrapped JSON in prose → extract the object span.
            parsed = json.loads(_extract_json_object(cleaned))
        except json.JSONDecodeError:
            try:
                # Model emitted raw LaTeX/regex/Windows backslashes → repair.
                parsed = json.loads(_repair_invalid_escapes(cleaned))
            except json.JSONDecodeError:
                # Give up — but include a preview of the raw response so the
                # next failure can be diagnosed without guessing.
                preview = raw.strip()[:400]
                if "{" not in raw:
                    raise RuntimeError(
                        "OpenZEN no devolvió JSON (respuesta sin llaves). "
                        "El bloque seleccionado probablemente no contiene "
                        "contenido académico (ej: una instrucción o una "
                        "tabla), por lo que el modelo respondió sin generar "
                        f"flashcards. Preview: {preview!r}"
                    ) from None
                raise json.JSONDecodeError(
                    f"Respuesta no parseable como JSON. Preview: {preview!r}",
                    raw, 0,
                ) from None
    if not isinstance(parsed, dict) or not isinstance(parsed.get("cards"), list):
        raise ValueError("Response is not a JSON object with a cards list")
    cards = [
        {"front": c["front"].strip(), "back": c["back"].strip()}
        for c in parsed["cards"]
        if isinstance(c, dict)
        and (c.get("front") or "").strip()
        and (c.get("back") or "").strip()
    ]
    if not cards:
        raise RuntimeError("Generated flashcards have no cards")
    return {
        "cards": cards[:max_cards],
        "total_concepts": parsed.get("total_concepts", len(cards)),
    }


def _finalize_flashcards_task(task_id: str, result_json: str, temp_path: str | None = None) -> None:
    """Mark a flashcards task as done and clean up the temp file (if any)."""
    if temp_path:
        try:
            import os
            os.remove(temp_path)
        except OSError:
            pass
    execute(
        """UPDATE ai_tasks
           SET status = 'done', result_content = %s, error_message = NULL,
               completed_at = NOW(), updated_at = NOW()
           WHERE id = %s""",
        (result_json, task_id),
    )


def _run_opencode_flashcards_task(task_id: str, content_text: str, max_cards: int) -> None:
    """Background thread: generate flashcards via OpenZEN (opencode-acp provider).

    Args:
        task_id: The ai_tasks row id to update.
        content_text: The full text content of the source blocks.
        max_cards: Hard maximum from the selected level (5/15/25).
    """
    try:
        execute(
            "UPDATE ai_tasks SET status = 'processing', updated_at = NOW() WHERE id = %s",
            (task_id,),
        )
        _update_progress(task_id, "⏳ Preparando contenido…")

        _update_progress(
            task_id,
            "✅ Contenido preparado\n⏳ Generando flashcards con OpenZEN…",
        )

        result_json = _ask_opencode_flashcards(content_text, max_cards)
        if not result_json or not result_json.strip():
            raise RuntimeError("OpenZEN devolvió flashcards vacías")

        result = _validate_flashcards_json(result_json, max_cards)
        _finalize_flashcards_task(
            task_id, json.dumps(result, ensure_ascii=False), None
        )
    except Exception as e:
        execute(
            """UPDATE ai_tasks
               SET status = 'error', error_message = %s,
                   completed_at = NOW(), updated_at = NOW()
               WHERE id = %s""",
            (str(e), task_id),
        )


def create_notebooklm_flashcards_task(
    block_ids: str | list[str], topic_id: str, user_id: str, level: str = "standard"
) -> dict:
    """Create a NotebookLM flashcards generation task.

    Args:
        block_ids: One or more content/markdown block ids to process.
        topic_id: The topic to associate the result with.
        user_id: The user creating the task.
        level: 'concise' (≤5) | 'standard' (≤15) | 'detailed' (≤25).

    Returns:
        {"task_id": str}

    Raises:
        ValueError: If input validation fails or no block has content.
    """
    if level not in FLASHCARD_LEVEL_MAX:
        raise ValueError(f"Invalid level: {level}. Use concise|standard|detailed")
    from ai.notebooklm.utils import _coerce_id, _new_task_id, _resolve_blocks_content

    content_text, source_type, source_id = _resolve_blocks_content(block_ids, user_id)
    if not content_text:
        raise ValueError("Selected blocks have no content")

    topic_id_int = _coerce_id(topic_id, "topic_id")
    task_row = execute_returning(
        """INSERT INTO ai_tasks
           (id, user_id, topic_id, task_type, format, source_type, source_id, status)
           VALUES (%s, %s, %s, 'flashcards', 'flashcards', %s, %s, 'pending')
           RETURNING id""",
        (_new_task_id(), user_id, topic_id_int, source_type, source_id),
    )
    task_id = task_row["id"]

    thread = threading.Thread(
        target=_run_opencode_flashcards_task,
        args=(task_id, content_text, FLASHCARD_LEVEL_MAX[level]),
        daemon=True,
    )
    thread.start()

    return {"task_id": task_id}