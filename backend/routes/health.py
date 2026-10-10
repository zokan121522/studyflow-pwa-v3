# backend/routes/health.py
"""Health check route for StudyFlow PWA v3."""

import os

import requests
from flask import Blueprint, jsonify

import engine
from database import fetchone, get_db
from openzen_credentials import default_model, default_server_url
from routes.auth import token_required
from ai.notebooklm.profiles import STORAGE_FILE, resolve_profile_dir


bp = Blueprint('health', __name__)

APP_VERSION = '3.1.1'
DEFAULT_DOWNLOADS_URL = 'https://studyflowhub.dev'


@bp.get('/health')
def health_check():
    """Health check endpoint.

    Reports which engine is actually serving, not just that a socket
    answered. Under local-first "healthy" and "sqlite" mean very different
    things to someone debugging why their data is not syncing, and the
    engine is chosen from the environment, so it is worth surfacing.
    """
    # Check database connection
    db_healthy = False
    db_error = None
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT 1')
                cur.fetchone()
        db_healthy = True
    except Exception as exc:
        db_error = f'{type(exc).__name__}: {exc}'

    payload = {
        'status': 'healthy' if db_healthy else 'degraded',
        'database': 'connected' if db_healthy else 'disconnected',
        'version': APP_VERSION,
        'engine': engine.describe(),
    }
    if db_error:
        # The message can name a file path, which the app is otherwise
        # careful not to echo. It is the whole point of a degraded health
        # check, and this endpoint is loopback-only.
        payload['error'] = db_error
    return jsonify(payload), 200 if db_healthy else 503


# ═══════════════════════════════════════════════════════════════════
# Aggregated status for the header pills (shared/status-panel.js)
# ═══════════════════════════════════════════════════════════════════

def _db_status() -> str:
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT 1')
                cur.fetchone()
        return 'connected'
    except Exception:
        return 'disconnected'


def _notebooklm_status(user_id: int) -> dict:
    """Lightweight connected check: active profile AND its storage_state.json.

    No side effects (unlike routes/notebooklm_settings.status, which migrates
    ownership) — this runs every 5 s from the header.
    """
    try:
        row = fetchone(
            'SELECT notebooklm_profile FROM user_config WHERE user_id = %s',
            (user_id,),
        )
        profile = (row or {}).get('notebooklm_profile') or None
        path = None
        if profile:
            profile_dir = resolve_profile_dir(profile)
            path = os.path.join(profile_dir, STORAGE_FILE) if profile_dir else None
        return {'connected': bool(path and os.path.exists(path)), 'profile': profile}
    except Exception:
        return {'connected': False, 'profile': None}


def _opencode_status(user_id: int) -> dict:
    """Row config + cheap reachability probe of the `opencode serve` sidecar."""
    out = {'connected': False, 'has_api_key': False,
           'server_url': None, 'model': None}
    try:
        row = fetchone(
            'SELECT api_key_enc, server_url, model '
            'FROM openzen_credentials WHERE user_id = %s',
            (user_id,),
        )
        out['has_api_key'] = bool((row or {}).get('api_key_enc'))
        out['server_url'] = (row or {}).get('server_url') or default_server_url()
        out['model'] = (row or {}).get('model') or default_model()
        try:
            requests.get(out['server_url'], timeout=2)
            out['connected'] = True   # any HTTP answer = reachable
        except Exception:
            out['connected'] = False
    except Exception:
        pass  # keep the defaults above; never 500 on a third party
    return out


def _version_tuple(value):
    try:
        return tuple(int(p) for p in str(value).split('.'))
    except (TypeError, ValueError):
        return None


def _version_status() -> dict:
    """installed vs. the version.json served by the downloads page.

    Server-side fetch on purpose: the downloads page sends no CORS headers,
    so the browser could not read it. Failure → latest/is_latest/source null.
    """
    out = {'installed': APP_VERSION, 'latest': None,
           'is_latest': None, 'source': None, 'url': None}
    try:
        base = (os.environ.get('DOWNLOADS_URL') or DEFAULT_DOWNLOADS_URL).rstrip('/')
        resp = requests.get(base + '/version.json', timeout=3)
        if resp.ok:
            data = resp.json() or {}
            latest_version = data.get('version')
            size = data.get('size')
            try:
                size = int(size) if size is not None else None
            except (TypeError, ValueError):
                size = None
            out['latest'] = {
                'version': latest_version,
                'date': data.get('date'),
                'size': size,
            }
            out['source'] = 'downloads'
            out['url'] = base
            installed_key = _version_tuple(APP_VERSION)
            latest_key = _version_tuple(latest_version)
            if installed_key is not None and latest_key is not None:
                out['is_latest'] = installed_key >= latest_key
    except Exception:
        pass
    return out


@bp.get('/status')
@token_required
def app_status(user_id: int):
    """Aggregated status for the header pills.

    Always 200: a third-party being down is data for the pills (gray/amber),
    not a server error. Every section guards its own failures.
    """
    return jsonify({
        'db': _db_status(),
        'notebooklm': _notebooklm_status(user_id),
        'opencode': _opencode_status(user_id),
        'version': _version_status(),
    }), 200