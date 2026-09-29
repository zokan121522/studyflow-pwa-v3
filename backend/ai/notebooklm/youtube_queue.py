"""YouTube Zen FIFO queue — single daemon worker (Phase 58, issue #225).

The legacy endpoint created one ``threading.Thread`` per task, so N URLs meant
N concurrent yt-dlp/OpenZEN runs hammering the CPU. This module replaces that
with a persistent FIFO queue:

    url(s) ──► ai_tasks.status='queued' ──► worker (oldest first) ──► done|error

Why a single daemon thread is safe:
  - gunicorn runs ONE worker process (GeventWebSocketWorker, see Dockerfile);
  - the worker re-scans the DB on boot and on every trigger, so nothing is
    lost even if the app restarts mid-queue;
  - ``_run_youtube_zen_task`` already flips 'queued' → 'processing' and never
    raises (it catches everything and marks 'error'), so the loop cannot die
    from a task failure.

The worker is fed from ``create_youtube_zen_task`` (legacy single URL) and
from ``enqueue_many`` (new /youtube-zen/queue endpoint, ≤ 20 URLs per batch).
"""

import json
import logging
import os
import queue
import threading

import database as db

logger = logging.getLogger(__name__)

# Stale-processing watchdog (Phase 59, issue #228): a task can stay in
# 'processing' forever if the worker thread died mid-task (gunicorn
# restart, crash) or if the OpenZEN stream stalled without closing.
# The worker only scans 'queued' rows, so a stuck 'processing' task
# would block the FIFO forever. Every loop iteration we reset tasks
# that have not touched `updated_at` for this long (env-configurable).
_STALE_PROCESSING_MINUTES = int(os.environ.get("YTZEN_STALE_PROCESSING_MINUTES", "25"))

# Trigger channel: any enqueue puts a token here so the blocked worker
# re-scans immediately instead of waiting for the periodic safety scan.
_trigger: "queue.Queue[None]" = queue.Queue()
_started = False
_start_lock = threading.Lock()


def ensure_running() -> None:
    """Idempotently start the queue daemon (first caller wins)."""
    global _started
    with _start_lock:
        if _started:
            return
        _started = True
    t = threading.Thread(
        target=_queue_worker_loop,
        name="youtube-zen-queue",
        daemon=True,
    )
    t.start()
    logger.info("YouTube Zen queue worker started (daemon)")


def wake() -> None:
    """Wake the worker so it re-scans queued tasks immediately."""
    try:
        _trigger.put(None, block=False)
    except queue.Full:  # pragma: no cover — unbounded queue, defensive only
        pass


# ─── Worker loop ───────────────────────────────────────────────────

def _scan_queued() -> list[dict]:
    """Fetch all 'queued' youtube_zen tasks in FIFO order (oldest first)."""
    return db.query(
        """SELECT id, user_id, topic_id, format, source_id, language, length,
                  template_id, coverage_data
           FROM ai_tasks
           WHERE status = 'queued' AND task_type = 'youtube_zen'
           ORDER BY created_at ASC"""
    )


def _process_row(row: dict) -> None:
    """Run the existing pipeline for one queued task.

    Params are reconstructed from the row: url/mode from coverage_data
    (written at enqueue time), depth from the `length` column, language from
    the `language` column, emphasis on backwards compatibility.
    """
    from ai.notebooklm.youtube_zen import _run_youtube_zen_task  # late import (avoid cycle)

    state = json.loads(row.get("coverage_data") or "{}")
    _run_youtube_zen_task(
        task_id=row["id"],
        url=state.get("url") or "",
        topic_id=row.get("topic_id") or "",
        block_id=row.get("source_id") or "",
        user_id=row["user_id"],
        fmt=row.get("format") or "markdown",
        depth=row.get("length") or "standard",
        mode=state.get("mode") or "unitema",
        language=row.get("language") or "es",
        template_id=row.get("template_id"),
    )


def _cleanup_stale_processing() -> int:
    """Reset youtube_zen tasks stuck in 'processing' for too long.

    A 'processing' task that has not updated `updated_at` in
    ``_STALE_PROCESSING_MINUTES`` minutes is declared dead and moved to
    'error' with a clear message. This is the queue watchdog: it runs on
    every worker loop iteration (interval ≤ 10s), so even if the worker
    thread crashed or the OpenZEN stream stalled forever, the FIFO can
    never be blocked for more than ``_STALE_PROCESSING_MINUTES``.

    Note: ``_set_progress`` / stream flushes refresh ``updated_at`` while
    a chunk is actively generating, so healthy long tasks are never
    misclassified — only truly silent ones.

    Returns:
        Number of stale tasks reset.
    """
    rows = db.query(
        """SELECT id FROM ai_tasks
           WHERE task_type = 'youtube_zen' AND status = 'processing'
             AND updated_at < NOW() - %s::interval
           LIMIT 20""",
        (f"{_STALE_PROCESSING_MINUTES} minutes",),
    )
    for r in rows:
        db.execute(
            """UPDATE ai_tasks
               SET status = 'error', error_message = %s, updated_at = NOW()
               WHERE id = %s""",
            (
                f"Tarea atascada: sin progreso en {_STALE_PROCESSING_MINUTES} "
                "min — vuelve a generar el vídeo",
                r["id"],
            ),
        )
        logger.warning(
            "YouTube Zen queue: reset stale processing task %s "
            "(> %s min sin progreso)",
            r["id"], _STALE_PROCESSING_MINUTES,
        )
    return len(rows)


def _queue_worker_loop() -> None:
    """Daemon loop: drain queue on boot, then wait for triggers."""
    while True:
        try:
            # Watchdog first: free the FIFO from any zombie task before
            # scanning so a stuck 'processing' row can never starve the
            # 'queued' ones behind it.
            _cleanup_stale_processing()
            rows = _scan_queued()
            for row in rows:
                try:
                    _process_row(row)
                except Exception:
                    # _run_youtube_zen_task swallows its own errors; this is
                    # a safety net so one bad row can never kill the worker.
                    logger.exception("YouTube Zen queue: task %s crashed", row["id"])
        except Exception:
            logger.exception("YouTube Zen queue: scan failed")

        # Block until woken by an enqueue; safety timeout re-scans anyway.
        try:
            _trigger.get(timeout=10)
        except queue.Empty:
            pass


# ─── Public API ────────────────────────────────────────────────────

def enqueue_many(
    urls: list[str],
    topic_id: str,
    block_id: str,
    user_id: str,
    fmt: str = "markdown",
    depth: str = "standard",
    mode: str = "unitema",
    language: str = "es",
    template_id: str | None = None,
) -> list[str]:
    """Enqueue N YouTube URLs as 'queued' tasks and wake the worker.

    Validation happens per-URL inside ``create_youtube_zen_task`` (shared
    with the legacy endpoint). Returns the list of created task ids.
    """
    from ai.notebooklm.youtube_zen import create_youtube_zen_task  # late import (avoid cycle)

    task_ids: list[str] = []
    for url in urls:
        result = create_youtube_zen_task(
            url=url,
            topic_id=topic_id,
            block_id=block_id,
            user_id=user_id,
            fmt=fmt,
            depth=depth,
            mode=mode,
            language=language,
            template_id=template_id,
        )
        task_ids.append(result["task_id"])
    wake()
    return task_ids


def get_queue_status(user_id: str) -> dict:
    """Snapshot of the user's youtube_zen queue for the floating panel.

    Returns four buckets:
      queued       — tasks waiting to be processed (FIFO order)
      processing   — the task currently being processed (at most 1)
      recent_done  — tasks completed in the last hour (with result content
                     + coverage_data so the panel can insert the block via
                     ``window.App.AI._onContentSuccess``)
      recent_error — tasks that errored in the last hour
    """
    rows = db.query(
        """SELECT id, status, format, source_id, topic_id, language, length,
                  result_content, coverage_data, error_message, created_at
           FROM ai_tasks
           WHERE user_id = %s AND task_type = 'youtube_zen'
             AND (status IN ('queued', 'processing')
                  OR (status IN ('done', 'error') AND updated_at > NOW() - INTERVAL '1 hour'))
           ORDER BY created_at ASC""",
        (user_id,),
    )

    result: dict = {"queued": [], "processing": [], "recent_done": [], "recent_error": []}
    for r in rows:
        state = json.loads(r.get("coverage_data") or "{}")
        item = {
            "id": r["id"],
            "status": r["status"],
            "format": r["format"],
            "block_id": r["source_id"] or "",
            "topic_id": r["topic_id"] or "",
            # Human label: video title (done) or the raw URL (queued/processing)
            "title": state.get("video_title") or state.get("url") or r["id"][:8],
            "depth": r["length"] or "standard",
            "mode": state.get("mode") or "unitema",
            "language": r["language"] or "es",
            "error_message": r["error_message"] or "",
        }
        if r["status"] == "done":
            # Full task payload for _onContentSuccess — coverage_data is the
            # final stats object (video_title, video_url, mode, ...).
            item["result_content"] = r["result_content"] or ""
            item["coverage_data"] = state
        bucket = (
            "queued"
            if r["status"] == "queued"
            else "processing"
            if r["status"] == "processing"
            else "recent_done"
            if r["status"] == "done"
            else "recent_error"
        )
        result[bucket].append(item)

    return result