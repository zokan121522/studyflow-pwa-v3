"""
NotebookLM Content Generation API — YouTube, Audio, Infographic, Test, MD routes.

All routes live here (not in ai.py) to keep file sizes manageable.
Business logic in backend/ai/notebooklm/ modules (flat v3 imports).

Registered by the server under the ``/api`` prefix, so full routes are:
  POST /api/ai/notebooklm/youtube-to-markdown   → YouTube → Markdown
  POST /api/ai/notebooklm/youtube-to-html       → YouTube → HTML
  POST /api/ai/notebooklm/generate-audio        → AI script (NotebookLM/OpenZEN) + transcript → edge-tts TTS
  POST /api/ai/notebooklm/infographic           → NotebookLM artifact → PNG
  POST /api/ai/notebooklm/generate-test         → Gemini Chat API → JSON
  POST /api/ai/notebooklm/enhance-markdown      → Gemini Chat API → enhanced MD
  POST /api/ai/notebooklm/markdown-to-html      → Gemini Chat API → HTML
  GET  /api/ai/notebooklm/audio/<filename>      → serve generated audio
  GET  /api/ai/notebooklm/infographic/<filename>→ serve generated PNG

YouTubeZen routes (Phase 8):
POST /api/ai/notebooklm/youtube-zen              → local yt-dlp + OpenZEN
POST /api/ai/notebooklm/youtube-zen/queue        → enqueue up to 20 URLs (FIFO)
GET  /api/ai/notebooklm/youtube-zen/queue/status → snapshot for the queue panel
POST /api/ai/notebooklm/youtube-zen/<id>/retry-chunk
POST /api/ai/notebooklm/youtube-zen/<id>/continue-without
POST /api/ai/notebooklm/youtube-zen/<id>/retry-now
"""

import os

from flask import Blueprint, request, jsonify, send_file

from routes.auth import token_required
from ai.notebooklm.audio import _AUDIO_DIR
from ai.notebooklm.infographic import _INFOGRAPHIC_DIR

bp = Blueprint("notebooklm_content", __name__)


def _parse_block_ids(data: dict) -> list[str] | None:
    """Parse ``block_ids`` with ``block_id`` fallback.

    Accepts:
        {"block_ids": "abc"}        → ["abc"]
        {"block_ids": ["a", "b"]}   → ["a", "b"]
        {"block_id": "abc"}         → ["abc"]   (legacy)

    Returns a non-empty list of trimmed ids, or None when missing/invalid.
    """
    raw = data.get("block_ids")
    if isinstance(raw, str):
        raw = [raw]
    elif isinstance(raw, list):
        raw = [str(i) for i in raw]
    else:
        raw = None
    if not raw:
        legacy = (data.get("block_id") or "").strip()
        if not legacy:
            return None
        raw = [legacy]
    ids = [b.strip() for b in raw if b and b.strip()]
    return ids or None


# ═══════════════════════════════════════════════════════════════════
# YouTube → Markdown
# ═══════════════════════════════════════════════════════════════════


@bp.route("/ai/notebooklm/youtube-to-markdown", methods=["POST"])
@token_required
def youtube_to_markdown(current_user_id: int):
    """POST /api/ai/notebooklm/youtube-to-markdown

    Body: { "url": "https://youtube.com/watch?v=...", "topic_id": "...", "block_id": "..." }
    Returns: { "task_id": "..." }
    """
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    topic_id = (data.get("topic_id") or "").strip()
    block_id = (data.get("block_id") or "").strip()

    if not url:
        return jsonify(error="Missing required field: url"), 400

    try:
        from ai.notebooklm.youtube import create_youtube_md_task
        result = create_youtube_md_task(
            url=url, topic_id=topic_id, block_id=block_id, user_id=current_user_id,
        )
        return jsonify(result)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except RuntimeError as e:
        return jsonify(error=str(e)), 500


# ═══════════════════════════════════════════════════════════════════
# YouTube → HTML
# ═══════════════════════════════════════════════════════════════════


@bp.route("/ai/notebooklm/youtube-to-html", methods=["POST"])
@token_required
def youtube_to_html(current_user_id: int):
    """POST /api/ai/notebooklm/youtube-to-html

    Body: { "url": "...", "topic_id": "...", "block_id": "..." }
    Returns: { "task_id": "..." }
    """
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    topic_id = (data.get("topic_id") or "").strip()
    block_id = (data.get("block_id") or "").strip()

    if not url:
        return jsonify(error="Missing required field: url"), 400

    try:
        from ai.notebooklm.youtube import create_youtube_html_task
        result = create_youtube_html_task(
            url=url, topic_id=topic_id, block_id=block_id, user_id=current_user_id,
        )
        return jsonify(result)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except RuntimeError as e:
        return jsonify(error=str(e)), 500


# ═══════════════════════════════════════════════════════════════════
# Audio — OpenZEN script + transcript → edge-tts TTS
# ═══════════════════════════════════════════════════════════════════


@bp.route("/ai/notebooklm/generate-audio", methods=["POST"])
@token_required
def generate_audio(current_user_id: int):
    """POST /api/ai/notebooklm/generate-audio

    Body: {
        "block_ids": "abc" | ["abc", "def"],   (multi-markdown)
        "topic_id": "...",
        "language": "es"|"en",
        "duration": "complete"|"5"|"10"|"20",  (default "complete")
        "provider": "notebooklm"|"opencode-acp" (default "notebooklm")
    }
    duration "complete" is verbatim edge-tts (no AI). Duration 5/10/20
    rewrites the content into a timed script using the given AI provider:
    "notebooklm" (Gemini, default) works now; "opencode-acp" (OpenZEN)
    fails with a clear error until its deferred sub-phase.
    Returns: { "task_id": "..." }
    """
    data = request.get_json(silent=True) or {}
    block_ids = _parse_block_ids(data)
    topic_id = (data.get("topic_id") or "").strip()
    language = (data.get("language") or "es").strip()
    duration = (data.get("duration") or "complete").strip()
    provider_name = (data.get("provider") or "notebooklm").strip()

    if language not in ("es", "en"):
        language = "es"
    if provider_name not in ("notebooklm", "opencode-acp"):
        return jsonify(error="Unsupported provider"), 400
    if not block_ids:
        return jsonify(error="Missing required field: block_ids"), 400

    try:
        from ai.notebooklm.audio import create_audio_task
        result = create_audio_task(
            block_ids=block_ids,
            topic_id=topic_id,
            user_id=current_user_id,
            language=language,
            duration=duration,
            provider_name=provider_name,
        )
        return jsonify(result)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except RuntimeError as e:
        return jsonify(error=str(e)), 500


# ═══════════════════════════════════════════════════════════════════
# Infographic — NotebookLM artifact → PNG
# ═══════════════════════════════════════════════════════════════════


@bp.route("/ai/notebooklm/infographic", methods=["POST"])
@token_required
def generate_infographic(current_user_id: int):
    """POST /api/ai/notebooklm/infographic

    Body: {
        "block_ids": "abc" | ["abc", "def"],   (multi-markdown)
        "topic_id": "...",
        "orientation": "landscape" | "portrait" | "square",
        "detail_level": "concise" | "standard" | "detailed",
        "style": "auto_select" | "sketch_note" | "professional" | ...,
        "instructions": "optional custom text"
    }
    Returns: { "task_id": "..." }
    """
    data = request.get_json(silent=True) or {}
    block_ids = _parse_block_ids(data)
    topic_id = (data.get("topic_id") or "").strip()

    if not block_ids:
        return jsonify(error="Missing required field: block_ids"), 400

    orientation = (data.get("orientation") or "landscape").strip().lower()
    detail_level = (data.get("detail_level") or "standard").strip().lower()
    style = (data.get("style") or "auto").strip().lower()
    instructions = (data.get("instructions") or "").strip()
    language = (data.get("language") or "").strip().lower()  # e.g. "en", "es", "auto"

    try:
        from ai.notebooklm.infographic import create_infographic_task
        result = create_infographic_task(
            block_ids=block_ids,
            topic_id=topic_id,
            user_id=current_user_id,
            orientation=orientation,
            detail_level=detail_level,
            style=style,
            instructions=instructions or None,
            language=language or None,
        )
        return jsonify(result)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except RuntimeError as e:
        return jsonify(error=str(e)), 500


# ═══════════════════════════════════════════════════════════════════
# Test — Gemini Chat API → JSON (same format as Ollama tests)
# ═══════════════════════════════════════════════════════════════════


@bp.route("/ai/notebooklm/generate-test", methods=["POST"])
@token_required
def generate_test(current_user_id: int):
    """POST /api/ai/notebooklm/generate-test

    Body: { "block_ids": "abc" | ["abc", "def"], "topic_id": "...",
            "num_questions": 10 | 20 | 30 }
    Questions are split EVENLY across the selected blocks by the backend
    (e.g. 20 questions / 3 blocks → 7/7/6).
    Returns: { "task_id": "..." }
    """
    data = request.get_json(silent=True) or {}
    block_ids = _parse_block_ids(data)
    topic_id = (data.get("topic_id") or "").strip()

    if not block_ids:
        return jsonify(error="Missing required field: block_ids"), 400

    raw_q = data.get("num_questions", 10)
    try:
        num_questions = int(raw_q)
    except (TypeError, ValueError):
        return jsonify(error="num_questions must be 10, 20 or 30"), 400
    if num_questions not in (10, 20, 30):
        return jsonify(error="num_questions must be 10, 20 or 30"), 400

    try:
        from ai.notebooklm.tasks_test import create_notebooklm_test_task
        result = create_notebooklm_test_task(
            block_ids=block_ids, topic_id=topic_id, user_id=current_user_id,
            num_questions=num_questions,
        )
        return jsonify(result)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except RuntimeError as e:
        return jsonify(error=str(e)), 500


# ═══════════════════════════════════════════════════════════════════
# Markdown Enhancement — Gemini Chat API
# ═══════════════════════════════════════════════════════════════════


@bp.route("/ai/notebooklm/enhance-markdown", methods=["POST"])
@token_required
def enhance_markdown(current_user_id: int):
    """POST /api/ai/notebooklm/enhance-markdown

    Body: { "block_id": "...", "topic_id": "...",
            "template_id": "..." (optional),
            "language": "auto"|"es"|"en" (optional),
            "length": "concise"|"standard"|"detailed" (optional) }
    Returns: { "task_id": "..." }
    """
    data = request.get_json(silent=True) or {}
    block_id = (data.get("block_id") or "").strip()
    topic_id = (data.get("topic_id") or "").strip()
    template_id = (data.get("template_id") or "").strip() or None
    language = (data.get("language") or "auto").strip()
    length = (data.get("length") or "standard").strip()

    if not block_id:
        return jsonify(error="Missing required field: block_id"), 400

    try:
        from ai.notebooklm.tasks_md import create_enhance_md_task
        result = create_enhance_md_task(
            block_id=block_id, topic_id=topic_id, user_id=current_user_id,
            template_id=template_id,
            language=language,
            length=length,
        )
        return jsonify(result)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except RuntimeError as e:
        return jsonify(error=str(e)), 500


@bp.route("/ai/notebooklm/markdown-to-html", methods=["POST"])
@token_required
def markdown_to_html(current_user_id: int):
    """POST /api/ai/notebooklm/markdown-to-html

    Converts markdown content to dark-themed HTML using Gemini Chat API.
    Body: { "block_id": "...", "topic_id": "..." }
    Returns: { "task_id": "..." }
    """
    data = request.get_json(silent=True) or {}
    block_id = (data.get("block_id") or "").strip()
    topic_id = (data.get("topic_id") or "").strip()

    if not block_id:
        return jsonify(error="Missing required field: block_id"), 400

    try:
        from ai.notebooklm.tasks_md import create_md_to_html_task
        result = create_md_to_html_task(
            block_id=block_id, topic_id=topic_id, user_id=current_user_id,
        )
        return jsonify(result)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except RuntimeError as e:
        return jsonify(error=str(e)), 500


# ═══════════════════════════════════════════════════════════════════
# Audio / Infographic file serving
# ═══════════════════════════════════════════════════════════════════


def _serve_audio_file(filename: str):
    """Shared body for both audio URLs. Basename-only: no path traversal."""
    safe = os.path.basename(filename)
    path = os.path.join(_AUDIO_DIR, safe)
    if not os.path.isfile(path):
        return jsonify(error="File not found"), 404
    return send_file(path, mimetype="audio/mpeg")


@bp.route("/ai/notebooklm/audio/<filename>", methods=["GET"])
@token_required
def serve_audio(current_user_id: int, filename: str):
    """Serve a generated audio file from the local audio dir."""
    return _serve_audio_file(filename)


@bp.route("/audio/serve/<filename>", methods=["GET"])
@token_required
def serve_audio_legacy(current_user_id: int, filename: str):
    """Legacy URL alias.

    backend/ai/notebooklm/audio.py persisted `/api/audio/serve/<name>.mp3` into
    ai_tasks.result_content and blocks.content, but no such route was ever
    registered -- every one of those 131 stored URLs 404'd. Rather than rewrite
    the stored rows, serve the same file under the path they already point at.
    New writes still go through audio.py, which now uses the canonical route.
    """
    return _serve_audio_file(filename)


@bp.route("/ai/notebooklm/infographic/<filename>", methods=["GET"])
@token_required
def serve_infographic(current_user_id: int, filename: str):
    """Serve a generated infographic image from the local infographics dir."""
    safe = os.path.basename(filename)
    path = os.path.join(_INFOGRAPHIC_DIR, safe)
    if not os.path.isfile(path):
        return jsonify(error="File not found"), 404
    return send_file(path, mimetype="image/png")


# ═══════════════════════════════════════════════════════════════════
# YouTubeZen — Local yt-dlp + OpenZEN structuring
# ═══════════════════════════════════════════════════════════════════


@bp.route("/ai/notebooklm/youtube-zen", methods=["POST"])
@token_required
def youtube_zen(current_user_id: int):
    """POST /api/ai/notebooklm/youtube-zen

    Body: { "url": "https://youtube.com/watch?v=...", "topic_id": "...",
    "block_id": "...", "format": "markdown",
    "depth": "standard", "mode": "unitema", "language": "es",
    "template_id": "tutorial" }  // optional — template from MD_TEMPLATES
    Returns: { "task_id": "..." }
    """
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    topic_id = (data.get("topic_id") or "").strip()
    block_id = (data.get("block_id") or "").strip()
    fmt = (data.get("format") or "markdown").strip().lower()
    depth = (data.get("depth") or "standard").strip().lower()
    mode = (data.get("mode") or "unitema").strip().lower()
    language = (data.get("language") or "es").strip().lower()
    template_id = (data.get("template_id") or "").strip() or None

    if not url:
        return jsonify(error="Missing required field: url"), 400

    try:
        from ai.notebooklm.youtube_zen import create_youtube_zen_task
        result = create_youtube_zen_task(
            url=url, topic_id=topic_id, block_id=block_id,
            user_id=current_user_id, fmt=fmt,
            depth=depth, mode=mode, language=language,
            template_id=template_id,
        )
        return jsonify(result)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except RuntimeError as e:
        return jsonify(error=str(e)), 500


@bp.route("/ai/notebooklm/youtube-zen/queue", methods=["POST"])
@token_required
def youtube_zen_queue(current_user_id: int):
    """POST /api/ai/notebooklm/youtube-zen/queue

    Enqueue up to 20 YouTube URLs as FIFO tasks (Phase 58, issue #225).
    A single daemon worker processes them one at a time in order.

    Body: { "urls": ["https://youtube.com/watch?v=..."],
    "topic_id": "...", "block_id": "...", "format": "markdown",
    "depth": "standard", "mode": "unitema", "language": "es",
    "template_id": "tutorial" }
    Returns: { "task_ids": ["...", "..."] }
    """
    data = request.get_json(silent=True) or {}
    urls = [u.strip() for u in (data.get("urls") or []) if u and u.strip()]
    if not urls:
        return jsonify(error="Missing required field: urls (non-empty array)"), 400
    if len(urls) > 20:
        return jsonify(error="Máximo 20 URLs por lote"), 400

    topic_id = (data.get("topic_id") or "").strip()
    block_id = (data.get("block_id") or "").strip()
    fmt = (data.get("format") or "markdown").strip().lower()
    depth = (data.get("depth") or "standard").strip().lower()
    mode = (data.get("mode") or "unitema").strip().lower()
    language = (data.get("language") or "es").strip().lower()
    template_id = (data.get("template_id") or "").strip() or None

    # Security (audit run-1, ytzen-queue-unbounded): enforce queue depth
    # limits — per-user (50) and global (500) — so one user cannot flood
    # the single FIFO worker and starve everyone else.
    try:
        from backend import database as db
        queued = db.query_one(
            "SELECT COUNT(*) AS cnt FROM ai_tasks WHERE user_id = %s AND status = 'queued'",
            (current_user_id,),
        )
        if queued and (queued["cnt"] or 0) + len(urls) > 50:
            return jsonify(error="Límite de cola alcanzado (máx 50 tareas en cola por usuario)"), 400

        global_q = db.query_one(
            "SELECT COUNT(*) AS cnt FROM ai_tasks WHERE status = 'queued' AND task_type = 'youtube_zen'"
        )
        if global_q and (global_q["cnt"] or 0) + len(urls) > 500:
            return jsonify(error="Cola global llena, inténtelo más tarde"), 429
    except Exception as e:
        return jsonify(error=f"Error al comprobar la cola: {str(e)}"), 500

    try:
        from ai.notebooklm.youtube_queue import enqueue_many
        task_ids = enqueue_many(
            urls=urls, topic_id=topic_id, block_id=block_id,
            user_id=current_user_id, fmt=fmt,
            depth=depth, mode=mode, language=language,
            template_id=template_id,
        )
        return jsonify(task_ids=task_ids)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except RuntimeError as e:
        return jsonify(error=str(e)), 500


@bp.route("/ai/notebooklm/youtube-zen/queue/status", methods=["GET"])
@token_required
def youtube_zen_queue_status(current_user_id: int):
    """GET /api/ai/notebooklm/youtube-zen/queue/status

    Snapshot for the floating queue panel:
    { "queued": [...], "processing": [...], "recent_done": [...], "recent_error": [...] }
    Done items carry result_content + coverage_data so the frontend can
    insert the block via window.App.AI._onContentSuccess.
    """
    try:
        from ai.notebooklm.youtube_queue import get_queue_status
        return jsonify(get_queue_status(current_user_id))
    except RuntimeError as e:
        return jsonify(error=str(e)), 500


@bp.route("/ai/notebooklm/youtube-zen/<task_id>/retry-chunk", methods=["POST"])
@token_required
def youtube_zen_retry_chunk(current_user_id: int, task_id: str):
    """POST /api/ai/notebooklm/youtube-zen/<id>/retry-chunk

    Regenerates the failed chunk of a detailed youtube_zen task and continues
    the remaining chunks, reusing the saved plan (no re-plan, no re-download).

    Returns: {"task_id": str} — poll GET /api/ai/tasks/<id> again.
    """
    try:
        from ai.notebooklm.youtube_zen import resume_youtube_zen_task
        result = resume_youtube_zen_task(
            task_id, current_user_id, action="retry",
        )
        return jsonify(result)
    except ValueError as e:
        return jsonify(error=str(e)), 409


@bp.route("/ai/notebooklm/youtube-zen/<task_id>/continue-without", methods=["POST"])
@token_required
def youtube_zen_continue_without(current_user_id: int, task_id: str):
    """POST /api/ai/notebooklm/youtube-zen/<id>/continue-without

    Continues the generation skipping the failed chunk (its content is
    omitted), reusing the saved plan and healthy chunks.

    Returns: {"task_id": str} — poll GET /api/ai/tasks/<id> again.
    """
    try:
        from ai.notebooklm.youtube_zen import resume_youtube_zen_task
        result = resume_youtube_zen_task(
            task_id, current_user_id, action="skip",
        )
        return jsonify(result)
    except ValueError as e:
        return jsonify(error=str(e)), 409


@bp.route("/ai/notebooklm/youtube-zen/<task_id>/retry-now", methods=["POST"])
@token_required
def youtube_zen_retry_now(current_user_id: int, task_id: str):
    """POST /api/ai/notebooklm/youtube-zen/<id>/retry-now

    While a detailed task is streaming, asks it to abort the CURRENT chunk so
    it lands in the recoverable chunk_error state — the user can then
    retry-chunk it immediately (not just after a real failure).

    Returns: {"task_id": str} — poll GET /api/ai/tasks/<id> again.
    """
    try:
        from ai.notebooklm.youtube_zen import request_chunk_retry
        result = request_chunk_retry(task_id, current_user_id)
        return jsonify(result)
    except ValueError as e:
        return jsonify(error=str(e)), 409