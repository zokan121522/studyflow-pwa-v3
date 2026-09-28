# backend/routes/health.py
"""Health check route for StudyFlow PWA v3."""

from flask import Blueprint, jsonify
from backend.database import get_db


bp = Blueprint('health', __name__)


@bp.get('/health')
def health_check():
    """Health check endpoint."""
    # Check database connection
    db_healthy = False
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT 1')
                cur.fetchone()
        db_healthy = True
    except Exception:
        pass

    return jsonify({
        'status': 'healthy' if db_healthy else 'degraded',
        'database': 'connected' if db_healthy else 'disconnected',
        'version': '3.0.0'
    }), 200 if db_healthy else 503