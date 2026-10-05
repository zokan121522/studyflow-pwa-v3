# backend/routes/health.py
"""Health check route for StudyFlow PWA v3."""

from flask import Blueprint, jsonify
import engine
from database import get_db


bp = Blueprint('health', __name__)


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
        'version': '3.0.0',
        'engine': engine.describe(),
    }
    if db_error:
        # The message can name a file path, which the app is otherwise
        # careful not to echo. It is the whole point of a degraded health
        # check, and this endpoint is loopback-only.
        payload['error'] = db_error
    return jsonify(payload), 200 if db_healthy else 503