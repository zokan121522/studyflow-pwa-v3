"""
Task management — cancel, list active, poll tasks, and list content sources.

Handles all ai_tasks DB operations. Flask-free — accepts user_id as parameter,
returns plain dicts (routes handle jsonify/request parsing).

Exports:
    cancel_task, list_active_tasks, get_task, get_sources
"""

import json
import time

from database import execute, query, query_one
from serial import iso
from ai.generation.helpers import _check_cancelled


def cancel_task(task_id: str, user_id: str) -> dict:
    """Cancel a running/pending task by its id.

    Aborts the active streaming connection if the task is generating.
    The background thread detects the abort and marks the task as 'cancelled'.

    Args:
        task_id: The UUID of the task to cancel.
        user_id: The user requesting the cancellation (ownership check).

    Returns:
        {"status": str, "message": str, "stream_aborted": bool}

    Raises:
        ValueError: If the task does not exist or does not belong to the user.
    """
    row = query_one(
        "SELECT id, status FROM ai_tasks WHERE id = %s AND user_id = %s",
        (task_id, user_id),
    )
    if not row:
        raise ValueError("Task not found")

    if row["status"] in ("done", "cancelled", "error"):
        return {"status": "already", "message": "La tarea ya ha terminado"}

    # Mark as cancelled in DB (background threads check _check_cancelled)
    execute(
        "UPDATE ai_tasks SET status = 'cancelled', updated_at = NOW() WHERE id = %s",
        (task_id,),
    )

    return {
        "status": "cancelled",
        "message": "Tarea cancelada",
    }


def list_active_tasks(user_id: str) -> list[dict]:
    """List tasks that are still running or pending for the given user.

    Args:
        user_id: The user whose active tasks to list.

    Returns:
        List of dicts with keys: id, task_type, status, model_used, format,
        source_id, topic_id, error_message, elapsed.
    """
    rows = query(
        """SELECT id, task_type, status, model_used, format,
                  source_id, topic_id, error_message, created_at, updated_at
           FROM ai_tasks
           WHERE user_id = %s AND status IN ('queued', 'processing', 'pending')
           ORDER BY created_at DESC
           LIMIT 20""",
        (user_id,),
    )

    result = []
    for r in rows:
        now = time.time()
        created = r["created_at"]
        elapsed_secs = (now - created.timestamp()) if created else 0
        mins = int(elapsed_secs // 60)
        secs = int(elapsed_secs % 60)

        result.append({
            "id": r["id"],
            "task_type": r["task_type"],
            "status": r["status"],
            "model_used": r["model_used"],
            "format": r["format"],
            "source_id": r["source_id"] or "",
            "topic_id": r["topic_id"] or "",
            "error_message": r["error_message"] or "",
            "elapsed": f"{mins}:{secs:02d}",
        })

    return result


def get_task(task_id: str, user_id: str) -> dict:
    """Poll the status of an AI generation task.

    Args:
        task_id: The UUID of the task to poll.
        user_id: The user requesting the status (ownership check).

    Returns:
        Dict with task fields including status, result_content, coverage_data.

    Raises:
        ValueError: If the task does not exist or does not belong to the user.
    """
    row = query_one(
        "SELECT * FROM ai_tasks WHERE id = %s AND user_id = %s",
        (task_id, user_id),
    )
    if not row:
        raise ValueError("Task not found")

    coverage_raw = row["coverage_data"]
    try:
        coverage_data = json.loads(coverage_raw) if coverage_raw else None
    except (json.JSONDecodeError, TypeError):
        coverage_data = None

    # ── Large content threshold ──────────────────────────────────
    # Cloudflared tunnel has a ~50MB RPC response limit.
    # If result_content exceeds MAX_INLINE_BYTES we exclude it from
    # the JSON poll response and return a content_url instead so the
    # frontend can fetch it as raw text (no JSON overhead).
    MAX_INLINE_BYTES = 4 * 1024 * 1024  # 4 MB

    raw_content: str | None = row["result_content"]
    content_url: str | None = None

    if raw_content and len(raw_content) > MAX_INLINE_BYTES:
        content_url = f"/api/ai/tasks/{task_id}/content"
        raw_content = None  # fetch separately via content_url

    return {
        "id": row["id"],
        "task_type": row["task_type"],
        "format": row["format"],
        "model_used": row["model_used"],
        "status": row["status"],
        "result_content": raw_content,
        "content_url": content_url,
        "error_message": row["error_message"],
        "coverage_data": coverage_data,
        "source_id": row["source_id"],
        "created_at": iso(row["created_at"]),
        "completed_at": iso(row["completed_at"]),
    }


def get_task_content(task_id: str, user_id: str) -> str | None:
    """Return the raw result_content for download (bypasses JSON size limits).

    Args:
        task_id: The UUID of the task.
        user_id: The user requesting (ownership check).

    Returns:
        The raw result_content string, or None if the task has no content.

    Raises:
        ValueError: If the task does not exist or does not belong to the user.
    """
    row = query_one(
        "SELECT result_content FROM ai_tasks WHERE id = %s AND user_id = %s",
        (task_id, user_id),
    )
    if not row:
        raise ValueError("Task not found")
    return row["result_content"]


def get_sources(topic_id: str, user_id: str) -> list[dict]:
    """List available content sources for test generation.

    Args:
        topic_id: The topic to get sources for.
        user_id: The user requesting (ownership check).

    Returns:
        List of source dicts with keys: id, type, label, created_at.

    Raises:
        ValueError: If the topic does not exist or does not belong to the user.
    """
    # Verify topic belongs to user
    topic = query_one(
        "SELECT id FROM topics WHERE id = %s AND user_id = %s",
        (topic_id, user_id),
    )
    if not topic:
        raise ValueError("Topic not found")

    # Get all blocks for this topic (pdf-ref, content, markdown types).
    # v3 stores PDFs in a separate `pdfs` table: pdf-ref blocks carry
    # url=/api/pdf/<id>; LEFT JOIN pulls the human filename for the label.
    rows = query(
        """SELECT b.id, b.type, b.title, b.url, b.content, b.created_at,
                  COALESCE(p.original_name, '') AS pdf_name
           FROM blocks b
           LEFT JOIN LATERAL (
               SELECT original_name FROM pdfs
               WHERE id = NULLIF(substring(b.url FROM '/api/pdf/([0-9]+)'), '')::int
           ) p ON TRUE
           WHERE b.topic_id = %s AND b.user_id = %s
             AND b.type IN ('pdf-ref', 'content', 'markdown')
           ORDER BY b.order_index ASC, b.created_at DESC""",
        (topic_id, user_id),
    )

    sources = []
    for r in rows:
        label = r["title"] or f"{r['type']} block"
        if r["type"] == "pdf-ref":
            pdf_name = r["pdf_name"] or label
            label = f"PDF — {pdf_name}"
        elif r["type"] == "content":
            label = f"HTML — {label}"
        elif r["type"] == "markdown":
            label = f"Markdown — {label}"

        sources.append({
            "id": r["id"],
            "type": r["type"],
            "label": label,
            "created_at": iso(r["created_at"]),
        })

    return sources
