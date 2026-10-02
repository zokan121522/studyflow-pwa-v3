"""
AI Generation API — thin Flask route wrappers (v3 PWA).

Business logic lives in backend/ai/ modules. Routes only handle HTTP
parsing and delegate to the flat v3 task modules. Registered by the
server under the ``/api`` prefix, so full route is e.g. /api/ai/config.

NotebookLM-pure endpoints (ported 1:1 from studyflow-hub v2):
  GET     /api/ai/config                       → AI config (disk-persisted)
  POST    /api/ai/config                       → update AI config
  POST    /api/ai/tasks/<id>/cancel            → cancel running/pending task
  GET     /api/ai/tasks/active                 → list running/pending tasks
  GET     /api/ai/tasks/<id>                   → poll task status
  GET     /api/ai/tasks/<id>/content           → raw result_content download
  GET     /api/ai/sources?topic_id=X           → available content sources
  GET     /api/ai/usage                        → today's completed counts
  POST    /api/ai/notebooklm/pdf-to-markdown   → PDF → Markdown (NotebookLM)
  POST    /api/ai/notebooklm/pdf-to-html       → PDF → HTML (NotebookLM)
  POST    /api/ai/generate-flashcards          → flashcards task (OpenZEN acp)

Endpoints depending on OpenZEN / Ollama (generate-content, pdf-to-opencode,
markdown-to-opencode, study-session-plan, extract-pdf,
generate-html-from-md/dark, local-to-md, generate-test, vocabulary)
are intentionally NOT registered until their deferred sub-phases.
knowledge-pipeline (Gen. Contenido) and generate-grammar-exercises
(English) ARE registered — ported from v2 and pinned to the v3 NotebookLM
provider (the only one registered in v3).
"""

import os
from datetime import datetime, timezone

from flask import Blueprint, request, jsonify, Response

from database import query, query_one
from routes.auth import token_required
from ai.config import (
    _get_ai_config, _save_ai_config, _invalidate_ai_config_cache,
    DAILY_LIMITS,
)
from ai import tasks as ai_tasks
from ai.notebooklm.tasks_pdf import (
    create_notebooklm_md_task, create_notebooklm_html_task,
)
from ai.notebooklm.tasks_flashcards import create_notebooklm_flashcards_task
from ai.notebooklm.md_templates_catalog import list_md_templates
from ai.generation.knowledge_pipeline import create_knowledge_pipeline_task
from ai.generation.grammar import create_grammar_task
from ai.generation.openzen_source import (
    create_openzen_md_task, retry_openzen_chunk,
)

bp = Blueprint("ai", __name__)


def _is_admin(user_id: int) -> bool:
    """Admin check for global config fields (owner pattern, no roles table).

    Overridable via ADMIN_USER_IDS env var (comma-separated user ids).
    """
    env_admins = {
        x.strip() for x in os.environ.get("ADMIN_USER_IDS", "").split(",") if x.strip()
    }
    if env_admins:
        return user_id in env_admins
    owner = query_one("SELECT id FROM users ORDER BY created_at ASC LIMIT 1")
    return bool(owner) and owner["id"] == user_id


# ═══════════════════════════════════════════════════════════════════
# AI Config — persisted settings (saved to disk, survives restart)
# ═══════════════════════════════════════════════════════════════════


@bp.route("/ai/config", methods=["GET"])
@token_required
def get_ai_config(current_user_id: int):
    """GET /api/ai/config — return current AI config."""
    cfg = _get_ai_config()
    return jsonify(cfg)


@bp.route("/ai/md-templates", methods=["GET"])
@token_required
def get_md_templates(current_user_id: int):
    """GET /api/ai/md-templates — list markdown prompt templates (public UI).

    Returns public metadata (id, name, emoji, description, mock) for the
    config modal grid + live preview. Internal prompt builders are NOT exposed.

    Returns:
        {"templates": [ {"id", "name", "emoji", "description", "mock"}, ... ]}
    """
    return jsonify(templates=list_md_templates()), 200


@bp.route("/ai/config", methods=["POST"])
@token_required
def save_ai_config(current_user_id: int):
    """POST /api/ai/config — save AI config to disk.

    Request body (all fields optional — only provided fields are updated):
        vault_path       (str):   filesystem path to the Obsidian vault
        priority_context (str):   priority context injected into agent prompt
        voice_mode       (bool):  enable/disable voice mode
        api_keys         (dict):  provider → list[str] keys
        key_index        (int):   active key index per provider

    Global fields (vault_path, priority_context) require admin privileges.
    After saving, invalidates the in-memory cache.
    """
    body = request.json or {}
    config = _get_ai_config()

    touches_global = bool({"vault_path", "priority_context"}.intersection(body.keys()))
    if touches_global and not _is_admin(current_user_id):
        return jsonify(error="Only the admin can edit global AI config"), 403

    if "vault_path" in body:
        val = body["vault_path"]
        if not isinstance(val, str) or not val.strip():
            return jsonify(error="vault_path must be a non-empty string"), 400
        config["vault_path"] = val.strip()

    if "priority_context" in body:
        val = body["priority_context"]
        if not isinstance(val, str):
            return jsonify(error="priority_context must be a string"), 400
        config["priority_context"] = val

    if "voice_mode" in body:
        config["voice_mode"] = bool(body["voice_mode"])

    if "api_keys" in body:
        val = body["api_keys"]
        if not isinstance(val, dict):
            return jsonify(error="api_keys must be a dict"), 400
        for provider, keys in val.items():
            if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
                return jsonify(
                    error=f"api_keys.{provider} must be a list of strings"
                ), 400
        config["api_keys"] = val
        config["key_index"] = 0

    if "key_index" in body:
        config["key_index"] = int(body["key_index"])

    known = {"vault_path", "priority_context", "voice_mode", "api_keys", "key_index"}
    if not known.intersection(body.keys()):
        return jsonify(error="No recognised fields provided"), 400

    try:
        _save_ai_config(config)
        _invalidate_ai_config_cache()
        return jsonify({"status": "ok", "config": config})
    except RuntimeError as e:
        return jsonify(error=str(e)), 500


# ═══════════════════════════════════════════════════════════════════
# Task lifecycle — cancel / active / polling / content
# ═══════════════════════════════════════════════════════════════════


@bp.route("/ai/tasks/<task_id>/cancel", methods=["POST"])
@token_required
def cancel_task(current_user_id: int, task_id: str):
    """POST /api/ai/tasks/<id>/cancel — cancel a running/pending task."""
    try:
        return jsonify(ai_tasks.cancel_task(task_id, current_user_id))
    except ValueError as e:
        return jsonify(error=str(e)), 404


@bp.route("/ai/tasks/active", methods=["GET"])
@token_required
def list_active_tasks(current_user_id: int):
    """GET /api/ai/tasks/active — list tasks still running or pending."""
    return jsonify(ai_tasks.list_active_tasks(current_user_id))


@bp.route("/ai/tasks/<task_id>", methods=["GET"])
@token_required
def get_task(current_user_id: int, task_id: str):
    """GET /api/ai/tasks/<id> — poll the status of an AI generation task."""
    try:
        return jsonify(ai_tasks.get_task(task_id, current_user_id))
    except ValueError as e:
        return jsonify(error=str(e)), 404


@bp.route("/ai/tasks/<task_id>/content", methods=["GET"])
@token_required
def get_task_content(current_user_id: int, task_id: str):
    """GET /api/ai/tasks/<id>/content — download full result_content as raw text.

    Used when content exceeds the inline JSON size limit. The frontend
    polls ``GET /api/ai/tasks/<id>`` and if ``result_content`` is null and
    ``content_url`` is present, it fetches this endpoint as plain text.
    """
    try:
        content = ai_tasks.get_task_content(task_id, current_user_id)
        if content is None:
            return jsonify(error="Task has no content"), 404
        return Response(content, mimetype="text/plain; charset=utf-8")
    except ValueError as e:
        return jsonify(error=str(e)), 404


# ═══════════════════════════════════════════════════════════════════
# Sources / Quota
# ═══════════════════════════════════════════════════════════════════


@bp.route("/ai/sources", methods=["GET"])
@token_required
def get_sources(current_user_id: int):
    """GET /api/ai/sources?topic_id=X — list available content sources."""
    topic_id = request.args.get("topic_id")
    if not topic_id:
        return jsonify(error="Missing topic_id"), 400
    try:
        return jsonify(ai_tasks.get_sources(topic_id, current_user_id))
    except ValueError as e:
        return jsonify(error=str(e)), 404


@bp.route("/ai/usage", methods=["GET"])
@token_required
def get_usage(current_user_id: int):
    """GET /api/ai/usage — return today's completed task counts per type.

    Returns:
        {"usage": {"generate_content": {"count": 3, "limit": 50}, ...}}

    Types not in DAILY_LIMITS are returned with limit=0 (tracked but not
    displayed as quota in the UI).
    """
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        rows = query(
            """SELECT task_type, COUNT(*) AS cnt
               FROM ai_tasks
               WHERE completed_at IS NOT NULL
                 AND DATE(completed_at AT TIME ZONE 'UTC') = %s
               GROUP BY task_type""",
            (today,),
        )
        usage = {}
        for row in rows:
            tt = row["task_type"]
            usage[tt] = {
                "count": row["cnt"],
                "limit": DAILY_LIMITS.get(tt, 0),
            }
        for tt, limit in DAILY_LIMITS.items():
            if tt not in usage:
                usage[tt] = {"count": 0, "limit": limit}
        return jsonify({"usage": usage})
    except Exception as e:
        return jsonify(error=str(e)), 500


# ═══════════════════════════════════════════════════════════════════
# NotebookLM PDF conversion (Phase 24)
# ═══════════════════════════════════════════════════════════════════


@bp.route("/ai/notebooklm/pdf-to-markdown", methods=["POST"])
@token_required
def notebooklm_pdf_to_markdown(current_user_id: int):
    """POST /api/ai/notebooklm/pdf-to-markdown — PDF → Markdown via NotebookLM.

    Request body:
        block_id   (str): PDF block to process.
        topic_id   (str, optional): Topic to associate the result with.
        template_id(str, optional): MD_TEMPLATES key.
        language   (str, optional): 'auto' | 'es' | 'en'.
        length     (str, optional): 'concise' | 'standard' | 'detailed'.

    Returns:
        {"task_id": str} — 201 on success.
    """
    body = request.json or {}
    try:
        result = create_notebooklm_md_task(
            block_id=body.get("block_id"),
            topic_id=body.get("topic_id"),
            user_id=current_user_id,
            template_id=body.get("template_id"),
            language=body.get("language", "auto"),
            length=body.get("length", "standard"),
        )
        return jsonify(result), 201
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except RuntimeError as e:
        return jsonify(error=str(e)), 500


@bp.route("/ai/notebooklm/pdf-to-html", methods=["POST"])
@token_required
def notebooklm_pdf_to_html(current_user_id: int):
    """POST /api/ai/notebooklm/pdf-to-html — PDF → HTML via NotebookLM.

    Request body:
        block_id (str): PDF block to process.
        topic_id (str, optional): Topic to associate the result with.

    Returns:
        {"task_id": str} — 201 on success.
    """
    body = request.json or {}
    try:
        result = create_notebooklm_html_task(
            block_id=body.get("block_id"),
            topic_id=body.get("topic_id"),
            user_id=current_user_id,
        )
        return jsonify(result), 201
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except RuntimeError as e:
        return jsonify(error=str(e)), 500


# ═══════════════════════════════════════════════════════════════════
# Flashcards
# ═══════════════════════════════════════════════════════════════════


@bp.route("/ai/openzen/source-to-markdown", methods=["POST"])
@bp.route("/ai/openzen/pdf-to-markdown", methods=["POST"])  # alias: old clients
@token_required
def openzen_source_to_markdown(current_user_id: int):
    """POST /ai/openzen/source-to-markdown — PDF or Markdown → notes via OpenZen.

    Same contract as ``/ai/notebooklm/pdf-to-markdown``, but the source is split
    per section and each section is generated (and retried) on its own. A
    section that keeps failing is reported instead of killing the document, and
    can be re-run alone with the retry endpoint below.

    Accepts a PDF block (split by its own table of contents / headings) or a
    Markdown block (split by the headings the author wrote). Either way the
    source document is the only material the model is given.

    Request body:
        block_id   (str): PDF or Markdown block to process.
        topic_id   (str, optional): Topic to associate the result with.
        template_id(str, optional): MD_TEMPLATES key.
        language   (str, optional): 'auto' | 'es' | 'en'.
        length     (str, optional): 'concise' | 'standard' | 'detailed'.

    Returns:
        {"task_id": str} — 201 on success.
    """
    body = request.json or {}
    try:
        result = create_openzen_md_task(
            block_id=body.get("block_id"),
            topic_id=body.get("topic_id"),
            user_id=current_user_id,
            template_id=body.get("template_id"),
            language=body.get("language", "auto"),
            length=body.get("length", "standard"),
        )
        return jsonify(result), 201
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except RuntimeError as e:
        return jsonify(error=str(e)), 500


@bp.route("/ai/openzen/tasks/<task_id>/chunk/<int:chunk_num>/retry",
          methods=["POST"])
@token_required
def openzen_retry_chunk(task_id: str, chunk_num: int, current_user_id: int):
    """POST /api/ai/openzen/tasks/<id>/chunk/<n>/retry — regenerate ONE section.

    This is the "reiniciar el chunk" button in the warning that appears when a
    section exhausted its retries. Only that section is recomputed and spliced
    back between its markers; the rest of the document is left untouched.
    """
    try:
        result = retry_openzen_chunk(task_id, chunk_num, current_user_id)
        return jsonify(result), 200
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except RuntimeError as e:
        return jsonify(error=str(e)), 500


@bp.route("/ai/generate-flashcards", methods=["POST"])
@token_required
def generate_flashcards(current_user_id: int):
    """POST /api/ai/generate-flashcards — create flashcards generation task.

    Body: {topic_id, block_ids (or source_ids), level}
    level: 'concise' (≤5) | 'standard' (≤15) | 'detailed' (≤25).

    Returns {"task_id": str} — poll GET /api/ai/tasks/<id> for status.
    NOTE: depends on the opencode-acp provider (OpenZEN) — fails with a
    clear error until its deferred sub-phase registers the provider.
    """
    body = request.json or {}
    block_ids = body.get("block_ids") or body.get("source_ids")
    try:
        result = create_notebooklm_flashcards_task(
            block_ids=block_ids,
            topic_id=body.get("topic_id"),
            user_id=current_user_id,
            level=body.get("level", "standard"),
        )
        return jsonify(result), 201
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except RuntimeError as e:
        return jsonify(error=str(e)), 500


# ═══════════════════════════════════════════════════════════════════
# Knowledge Pipeline — Phase 38 (Gen. Contenido)
# ═══════════════════════════════════════════════════════════════════

@bp.route("/ai/knowledge-pipeline", methods=["POST"])
@token_required
def knowledge_pipeline_endpoint(current_user_id: int):
    """POST /api/ai/knowledge-pipeline — generate notes from topic text.

    Accepts free-text topic description + depth level and dispatches to the
    v3 NotebookLM provider (the only provider registered in v3) for
    structured markdown generation. Poll ``GET /api/ai/tasks/<id>``.

    Request body:
        topic_text (str): Topic description to generate notes about.
        depth (str, optional): 'concise', 'standard' or 'detailed'. Default: 'standard'.
        topic_id (str, optional): Topic to associate the result with.
        course_id (str, optional): Course for block insertion on completion.
        mode (str, optional): 'unitema' or 'por_tema'. Default: 'unitema'.
        language (str, optional): 'es' or 'en'. Default: 'es'.
        template_id (str, optional): MD_TEMPLATES key; empty → generic prompt.

    Returns:
        {"task_id": str} — 201 on success; 400 ``{error}`` on bad input.
    """
    body = request.json or {}
    try:
        result = create_knowledge_pipeline_task(
            topic_text=body.get("topic_text", ""),
            depth=body.get("depth", "standard"),
            topic_id=body.get("topic_id"),
            user_id=current_user_id,
            course_id=body.get("course_id"),
            language=body.get("language", "es"),
            mode=body.get("mode", "unitema"),
            template_id=body.get("template_id"),
        )
        return jsonify(result), 201
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except RuntimeError as e:
        return jsonify(error=str(e)), 500


# ═══════════════════════════════════════════════════════════════════
# English Grammar Exercises — Phase 61 (English addon)
# ═══════════════════════════════════════════════════════════════════

@bp.route("/ai/generate-grammar-exercises", methods=["POST"])
@token_required
def generate_grammar_exercises(current_user_id: int):
    """POST /api/ai/generate-grammar-exercises — english addon.

    Body: {source_id, source_type, topic_id, per_type}
    source_type: 'markdown' | 'content' (PDF out of scope in v1).
    per_type: exercises per type (1, 5, 10, 20, 40; default 10).
    Always uses the v3 NotebookLM provider (the only one registered).

    Returns {"task_id": str} — poll GET /api/ai/tasks/<id> for status.
    """
    body = request.json or {}
    try:
        result = create_grammar_task(
            topic_id=body.get("topic_id"),
            source_id=body.get("source_id"),
            source_type=body.get("source_type"),
            user_id=current_user_id,
            per_type=body.get("per_type", 10),
        )
        return jsonify(result), 201
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except RuntimeError as e:
        return jsonify(error=str(e)), 500


@bp.route("/ai/openzen-md-templates", methods=["GET"])
@token_required
def openzen_md_templates(current_user_id: int):
    """GET /api/ai/openzen-md-templates — list OpenZEN markdown prompt templates.

    Returns public metadata (id, name, emoji, description, mock) for the
    YouTubeZen dialog template grid + live preview. Internal prompt builders
    are NOT exposed.

    Returns:
        {"templates": [ {"id", "name", "emoji", "description", "mock"}, ... ]}
    """
    return jsonify(templates=list_md_templates()), 200