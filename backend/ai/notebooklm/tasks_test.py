"""
NotebookLM Test Generation task (v3 port of v2 ai/notebooklm/tasks.py,
split #2/4 — Gemini Chat API → JSON test).

Synchronous task creator. Runs inside Flask request context (blocking),
validates input, creates the DB record, and starts a background thread.

Exports:
    create_notebooklm_test_task
"""

import asyncio
import json
import os
import threading
import time

from database import execute, execute_returning

from ai.notebooklm.tasks_pdf import _repair_invalid_escapes, _update_progress
from ai.notebooklm.utils import _new_task_id

_TEMP_DIR = "/tmp/notebooklm-test"
_NOTEBOOK_PREFIX = "tmp-test"
_SOURCE_WAIT_TIMEOUT = 120.0


def _split_questions_evenly(num_questions: int, n_blocks: int) -> list[int]:
    """Split `num_questions` into `n_blocks` quotas as evenly as possible.

    Mathematically exact: every block gets base or base+1, never more.
    Examples:
        20 / 3 → [7, 7, 6]
        30 / 4 → [8, 8, 7, 7]
        10 / 1 → [10]
    """
    if n_blocks <= 1:
        return [num_questions]
    base, rest = divmod(num_questions, n_blocks)
    return [base + 1 if i < rest else base for i in range(n_blocks)]


def _run_notebooklm_test_task(task_id: str, blocks: list[dict]) -> None:
    """Background thread: generate a test via NotebookLM Chat API.

    One source + one chat.ask PER BLOCK, each asked for its exact quota,
    then the resulting question arrays are concatenated. This guarantees a
    mathematically even distribution across the selected blocks (the model
    never decides how many questions a block gets).

    Args:
        task_id: The ai_tasks row id to update.
        blocks: List of {"id", "title", "text"} — one entry per selected block.
    """
    os.makedirs(_TEMP_DIR, exist_ok=True)

    try:
        execute(
            "UPDATE ai_tasks SET status = 'processing', updated_at = NOW() WHERE id = %s",
            (task_id,),
        )
        quotas = [b["quota"] for b in blocks]
        total = sum(quotas)
        _update_progress(
            task_id,
            f"⏳ Repartiendo {total} preguntas entre {len(blocks)} bloques…",
        )

        # ── 1. Save each block's content to a temp text file ───────
        from ai.notebooklm.prompts import make_test_prompt
        from ai.notebooklm.utils import get_active_profile as _get_active_profile
        from notebooklm import NotebookLMClient

        ts = int(time.time())
        temp_paths: list[str] = []
        for i, blk in enumerate(blocks):
            temp_path = os.path.join(_TEMP_DIR, f"content_{ts}_{i}.txt")
            with open(temp_path, "w", encoding="utf-8") as f:
                f.write(blk["text"])
            temp_paths.append(temp_path)

        _update_progress(
            task_id,
            f"✅ Contenido preparado\n⏳ Generando {total} preguntas con NotebookLM…",
        )

        # ── 2. One notebook per block, one ask per block (exact quota) ─
        async def _run():
            profile = _get_active_profile()
            kwargs = {"profile": profile} if profile else {}
            async with NotebookLMClient.from_storage(**kwargs) as client:
                results: list[str] = []
                for i, (blk, temp_path) in enumerate(zip(blocks, temp_paths)):
                    nb = await client.notebooks.create(f"{_NOTEBOOK_PREFIX}-{ts}-{i}")
                    try:
                        source = await client.sources.add_file(
                            nb.id, temp_path, wait=True,
                            wait_timeout=_SOURCE_WAIT_TIMEOUT,
                        )
                        if not source or not source.id:
                            raise RuntimeError(f"Failed to add content to NotebookLM (bloque {i + 1})")

                        quota = quotas[i]
                        prompt = make_test_prompt(quota, blk["title"])
                        answer = await client.chat.ask(nb.id, prompt)
                        if not answer or not answer.answer:
                            raise RuntimeError(f"NotebookLM returned empty test result (bloque {i + 1})")
                        results.append(answer.answer)
                    finally:
                        try:
                            await client.notebooks.delete(nb.id)
                        except Exception:
                            pass

                return results

        results_json = asyncio.run(_run())

        # ── 3. Validate + merge the per-block JSON arrays ──────────
        merged: list[dict] = []
        for i, result_json in enumerate(results_json):
            try:
                parsed = json.loads(result_json)
            except json.JSONDecodeError:
                parsed = json.loads(_repair_invalid_escapes(result_json))
            if not isinstance(parsed, list):
                # Maybe wrapped in an object
                if isinstance(parsed, dict) and "questions" in parsed:
                    parsed = parsed["questions"]
                else:
                    raise ValueError(f"Response for bloque {i + 1} is not a JSON array")

            quota = quotas[i]
            if len(parsed) > quota:
                # Model over-produced: trim to the exact quota so the final
                # distribution stays mathematically exact (trim whole trailing
                # questions — the first `quota` are kept in order).
                parsed = parsed[:quota]
            elif len(parsed) < quota:
                raise ValueError(
                    f"Bloque {i + 1} generó {len(parsed)} preguntas, "
                    f"se pedían exactamente {quota}"
                )
            merged.extend(parsed)

        if not merged:
            raise RuntimeError("Generated test has no questions")

        result_json = json.dumps(merged, ensure_ascii=False)

        # Clean up temp files
        for temp_path in temp_paths:
            try:
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

    except Exception as e:
        execute(
            """UPDATE ai_tasks
               SET status = 'error', error_message = %s,
                   completed_at = NOW(), updated_at = NOW()
               WHERE id = %s""",
            (str(e), task_id),
        )


def create_notebooklm_test_task(block_ids: str | list[str], topic_id: str, user_id: str, num_questions: int = 10) -> dict:
    """Create a NotebookLM test generation task.

    Uses the Gemini Chat API to generate a multiple-choice test
    in the same JSON format as Ollama-generated tests.

    Questions are split EVENLY across the selected blocks by the backend
    (mathematically exact, e.g. 20 questions / 3 blocks → 7/7/6) — one
    NotebookLM ask per block with its exact quota, so the model never
    decides the distribution.

    Args:
        block_ids: One or more content/markdown block ids to process
            (multi-markdown selection; a single id keeps the legacy
            single-block flow, including PDF extraction).
        topic_id: The topic to associate the result with.
        user_id: The user creating the task.
        num_questions: Total question count — must be 10, 20 or 30.

    Returns:
        {"task_id": str}

    Raises:
        ValueError: If input validation fails or no block has content.
    """
    if num_questions not in (10, 20, 30):
        raise ValueError("num_questions must be 10, 20 or 30")

    from ai.notebooklm.utils import _coerce_id, _resolve_blocks_content_per_block

    blocks = _resolve_blocks_content_per_block(block_ids, user_id)
    quotas = _split_questions_evenly(num_questions, len(blocks))
    for blk, quota in zip(blocks, quotas):
        blk["quota"] = quota

    topic_id_int = _coerce_id(topic_id, "topic_id")
    source_ids = [str(b["id"]) for b in blocks]
    if len(blocks) == 1:
        source_type, source_id = blocks[0]["type"], source_ids[0]
    else:
        source_type, source_id = "blocks", json.dumps(source_ids)

    task_row = execute_returning(
        """INSERT INTO ai_tasks
           (id, user_id, topic_id, task_type, format, source_type, source_id, status)
           VALUES (%s, %s, %s, 'notebooklm_test', 'test', %s, %s, 'pending')
           RETURNING id""",
        (_new_task_id(), user_id, topic_id_int, source_type, source_id),
    )
    task_id = task_row["id"]

    thread = threading.Thread(
        target=_run_notebooklm_test_task,
        args=(task_id, blocks),
        daemon=True,
    )
    thread.start()

    return {"task_id": task_id}