"""
Agenda · categories — port from studyflow-hub v2.
Built-in 7 categories are merged with custom_categories rows.
"""
from flask import Blueprint, jsonify, request

from backend import database as db
from backend.routes.auth import token_required
from backend.routes.agenda import _builtin_categories


bp = Blueprint("agenda_categories", __name__)


# ─── GET /api/agenda/categories ──────────────────────────────────
@bp.get("/agenda/categories")
@token_required
def list_categories(current_user_id: int):
    builtin = _builtin_categories()
    custom_rows = db.query(
        "SELECT key, label, icon FROM custom_categories "
        "WHERE user_id = %s ORDER BY key",
        (current_user_id,),
    )
    custom = {
        r["key"]: {"label": r["label"], "icon": r["icon"], "builtin": False}
        for r in custom_rows
    }
    return jsonify({**builtin, **custom})


# ─── POST /api/agenda/categories ─────────────────────────────────
@bp.post("/agenda/categories")
@token_required
def add_category(current_user_id: int):
    body = request.get_json(silent=True) or {}
    key = (body.get("key") or "").strip().lower().replace(" ", "_")
    label = (body.get("label") or key).strip()
    icon = body.get("icon") or "📌"
    if not key:
        return jsonify(error="Category key is required"), 400
    if key in _builtin_categories():
        return jsonify(error="Category already exists"), 409
    try:
        db.execute(
            "INSERT INTO custom_categories (key, user_id, label, icon) "
            "VALUES (%s, %s, %s, %s)",
            (key, current_user_id, label, icon),
        )
    except Exception:
        return jsonify(error="Category already exists"), 409
    return jsonify({key: {"label": label, "icon": icon, "builtin": False}}), 201


# ─── DELETE /api/agenda/categories/<key> ─────────────────────────
@bp.delete("/agenda/categories/<key>")
@token_required
def delete_category(current_user_id: int, key: str):
    if key in _builtin_categories():
        return jsonify(error="Cannot delete built-in category"), 400
    db.execute(
        "DELETE FROM custom_categories WHERE key = %s AND user_id = %s",
        (key, current_user_id),
    )
    return jsonify(ok=True)