"""
Quick Notes API — persistent text blob per user — port from studyflow-hub v2.
"""
from flask import Blueprint, jsonify, request

from backend import database as db
from backend.routes.auth import token_required


bp = Blueprint("quick_notes", __name__)


# ─── GET /api/quick-note ─────────────────────────────────────────
@bp.get("/quick-note")
@token_required
def get_quick_note(current_user_id: int):
    row = db.query_one(
        "SELECT content FROM quick_notes WHERE user_id = %s",
        (current_user_id,),
    )
    return jsonify({"content": row["content"] if row else ""})


# ─── PUT /api/quick-note ─────────────────────────────────────────
@bp.put("/quick-note")
@token_required
def save_quick_note(current_user_id: int):
    body = request.get_json(silent=True) or {}
    content = body.get("content", "")
    db.execute(
        "INSERT INTO quick_notes (user_id, content, updated_at) "
        "VALUES (%s, %s, NOW()) "
        "ON CONFLICT (user_id) DO UPDATE SET "
        "  content = EXCLUDED.content, updated_at = NOW()",
        (current_user_id, str(content) if content else ""),
    )
    return jsonify(ok=True)