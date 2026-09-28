# backend/scorm_stream.py
"""SSE progress streaming for the unified SCORM import (S7b-A).

Ports v2's routes/scraping.py::_stream_import_scorm to the v3 unified
endpoint: run the import on a worker thread and stream its progress as
Server-Sent Events.

Event protocol (mirrors v2 so clients stay interchangeable):
    {"step": "…", "ts": 1727…}          → one terminal line
    {"done": true, "result": {…}}       → success envelope (soft errors too)
    {"done": true, "error": "…"}        → hard failure (worker exception)
    ": ping"                            → SSE comment, ignored by clients

The 15s comment heartbeat is not cosmetic: Cloudflare tunnels cut an
origin that sends nothing for 100s with HTTP 524, which is exactly what
happened to the blocking (non-streamed) import. Bytes must keep flowing.
"""

import json
import queue
import logging
import threading
import time
from typing import Any, Callable, Dict, Generator

from flask import Response, stream_with_context

logger = logging.getLogger(__name__)

HEARTBEAT_S = 15

ProgressEmit = Callable[[str], None]
ImportWork = Callable[[ProgressEmit], Dict[str, Any]]


def stream_import(work: ImportWork) -> Response:
    """Run `work(emit)` on a background thread and stream its progress.

    `work` receives an `emit(msg)` callback and returns the final JSON
    envelope; exceptions become a hard-error frame. Returns a Flask
    text/event-stream response that flushes per event.
    """
    q: "queue.Queue[Dict[str, Any]]" = queue.Queue()

    def emit(msg: str) -> None:
        try:
            q.put_nowait({"step": msg, "ts": int(time.time())})
        except Exception:  # pragma: no cover — queue full never happens
            pass

    def worker() -> None:
        try:
            q.put_nowait({"done": True, "result": work(emit)})
        except Exception as exc:
            logger.exception("Streamed SCORM import failed")
            q.put_nowait({"done": True, "error": str(exc)})

    thread = threading.Thread(target=worker, daemon=True, name="scorm-import")
    thread.start()

    return Response(
        stream_with_context(_frames(q, thread)),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _frames(
    q: "queue.Queue[Dict[str, Any]]", thread: threading.Thread
) -> Generator[str, None, None]:
    """Drain the queue as SSE frames until the worker reports done."""
    last_emit = time.time()
    while True:
        try:
            item = q.get(timeout=1.0)
        except queue.Empty:
            if not thread.is_alive():
                yield _frame({"done": True, "error": "scraper thread died"})
                return
            if time.time() - last_emit >= HEARTBEAT_S:
                last_emit = time.time()
                yield ": ping\n\n"
            continue
        last_emit = time.time()
        yield _frame(item)
        if item.get("done"):
            return


def _frame(payload: Dict[str, Any]) -> str:
    """Serialise one payload as an SSE `data:` frame."""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
