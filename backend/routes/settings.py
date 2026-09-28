"""settings.py — PWA settings endpoints (S7b-B).

Endpoints:
  GET  /api/settings/scorm-credentials → {username, has_password, updated_at}
  POST /api/settings/scorm-credentials → {username, password} (upsert)

The password is write-only: GET never returns it, only `has_password`.
Values live in `scorm_credentials` (see backend/scorm_credentials.py);
env vars SCRAPING_USERNAME / SCRAPING_PASSWORD still take precedence for
deployments that prefer not to store anything.
"""

import logging

from flask import Blueprint, jsonify, request

from database import fetchone
from routes.auth import token_required
from scorm_credentials import save_credentials

logger = logging.getLogger(__name__)

bp = Blueprint('settings', __name__)


def _status(user_id: int) -> dict:
    """Read-only view of the stored credentials (never the password)."""
    row = fetchone(
        """
        SELECT username, password_enc, updated_at
        FROM scorm_credentials WHERE user_id = %s
        """,
        (user_id,),
    )
    if not row:
        return {"username": "", "has_password": False, "updated_at": None}
    return {
        "username": row.get("username") or "",
        "has_password": bool(row.get("password_enc")),
        "updated_at": row.get("updated_at").isoformat()
        if row.get("updated_at") else None,
    }


@bp.route('/settings/scorm-credentials', methods=['GET'])
@token_required
def get_scorm_credentials(user_id):
    """Return the current SCORM credentials (username + has_password flag)."""
    return jsonify(_status(user_id))


@bp.route('/settings/scorm-credentials', methods=['POST'])
@token_required
def save_scorm_credentials(user_id):
    """Upsert the SCORM credentials. Request JSON: {username, password}."""
    body = request.get_json(silent=True) or {}
    username = (body.get('username') or '').strip()
    password = body.get('password') or ''

    if not username:
        return jsonify(error='El usuario es obligatorio'), 400
    if not password:
        return jsonify(error='La contraseña es obligatoria'), 400

    try:
        save_credentials(user_id, username, password)
    except Exception as exc:
        logger.error('Failed to save SCORM credentials: %s', exc)
        return jsonify(error=f'No se pudieron guardar: {exc}'), 500

    return jsonify({'ok': True, **_status(user_id)})
