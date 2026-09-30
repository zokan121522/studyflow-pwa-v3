"""settings.py — PWA settings endpoints (S7b-B).

Endpoints:
  GET  /api/settings/scorm-credentials → {username, has_password, updated_at}
  POST /api/settings/scorm-credentials → {username, password} (upsert)

  GET    /api/settings/openzen-credentials → {has_api_key, server_url, model, …}
  POST   /api/settings/openzen-credentials → {api_key, server_url, model}
  DELETE /api/settings/openzen-credentials → clears the stored key

The password is write-only: GET never returns it, only `has_password`.
Values live in `scorm_credentials` (see backend/scorm_credentials.py);
env vars SCRAPING_USERNAME / SCRAPING_PASSWORD still take precedence for
deployments that prefer not to store anything.

Same contract for OpenZen: the API key is write-only (GET returns just
`has_api_key`) and lives in `openzen_credentials` (see
backend/openzen_credentials.py).
"""

import logging

from flask import Blueprint, jsonify, request

from database import fetchone
from routes.auth import token_required
from openzen_credentials import (
    delete_credentials,
    default_model,
    default_server_url,
    save_credentials,
)
from scorm_credentials import save_credentials as save_scorm_credentials

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
def post_scorm_credentials(user_id):
    """Upsert the SCORM credentials. Request JSON: {username, password}.

    Named post_… on purpose. A handler named `save_scorm_credentials` rebinds
    the module-level alias imported from scorm_credentials, so the call below
    resolved to the handler itself and blew up with
    `save_scorm_credentials() takes 1 positional argument but 4 were given`
    → HTTP 500 for every Moodle save. The URL is unaffected by the name.
    """
    body = request.get_json(silent=True) or {}
    username = (body.get('username') or '').strip()
    password = body.get('password') or ''

    if not username:
        return jsonify(error='El usuario es obligatorio'), 400
    if not password:
        return jsonify(error='La contraseña es obligatoria'), 400

    try:
        save_scorm_credentials(user_id, username, password)
    except Exception as exc:
        logger.error('Failed to save SCORM credentials: %s', exc)
        return jsonify(error=f'No se pudieron guardar: {exc}'), 500

    return jsonify({'ok': True, **_status(user_id)})


# ═══════════════════════════════════════════════════════════════════
# OpenZen (opencode-acp) subscription
# ═══════════════════════════════════════════════════════════════════


def _openzen_status(user_id: int) -> dict:
    """Read-only view of the OpenZen config (never the API key itself)."""
    row = fetchone(
        """
        SELECT api_key_enc, server_url, model, updated_at
        FROM openzen_credentials WHERE user_id = %s
        """,
        (user_id,),
    )
    return {
        # has_api_key is about the ROW, not the env: a deployment-wide
        # OPENCODE_API_KEY still authenticates every user, but it is not
        # something this panel saved, so it must not read as "configured".
        'has_api_key': bool(row.get('api_key_enc')) if row else False,
        'server_url': (row or {}).get('server_url') or default_server_url(),
        'model': (row or {}).get('model') or default_model(),
        'is_default': not bool(row),
        'updated_at': row.get('updated_at').isoformat()
        if row and row.get('updated_at') else None,
    }


@bp.route('/settings/openzen-credentials', methods=['GET'])
@token_required
def get_openzen_credentials(user_id):
    """Return the current OpenZen config + the defaults in effect."""
    return jsonify(_openzen_status(user_id))


@bp.route('/settings/openzen-credentials', methods=['POST'])
@token_required
def save_openzen_credentials(user_id):
    """Upsert the OpenZen subscription. JSON: {api_key, server_url, model}."""
    body = request.get_json(silent=True) or {}
    api_key = (body.get('api_key') or '').strip()
    server_url = (body.get('server_url') or '').strip()
    model = (body.get('model') or '').strip()

    if not api_key:
        return jsonify(error='La API key es obligatoria'), 400

    try:
        save_credentials(user_id, api_key, server_url, model)
    except Exception as exc:
        logger.error('Failed to save OpenZen credentials: %s', exc)
        return jsonify(error=f'No se pudo guardar: {exc}'), 500

    return jsonify({'ok': True, **_openzen_status(user_id)})


@bp.route('/settings/openzen-credentials', methods=['DELETE'])
@token_required
def clear_openzen_credentials(user_id):
    """Delete the stored key. Loads then fall back to env / defaults."""
    try:
        delete_credentials(user_id)
    except Exception as exc:
        logger.error('Failed to clear OpenZen credentials: %s', exc)
        return jsonify(error=f'No se pudo limpiar: {exc}'), 500

    return jsonify({'ok': True, **_openzen_status(user_id)})
