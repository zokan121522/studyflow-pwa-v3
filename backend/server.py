# backend/server.py
"""Flask API-first application factory for StudyFlow PWA v3."""

import os
import sys

# The backend modules use flat, first-level imports (`from database import ...`,
# `from routes.courses import bp`), so this package's own directory must be on
# sys.path for those names to resolve. Bootstrap it here instead of relying on
# the caller's working directory, so `python3 -m backend.server` works from
# anywhere (repo root, a subdirectory, an IDE, or a service manager).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, jsonify
from flask_cors import CORS

from static_serve import init_static

from routes.auth import bp as auth_bp
from routes.agenda import bp as agenda_bp
from routes.agenda_sessions import bp as agenda_sessions_bp
from routes.agenda_state import bp as agenda_state_bp
from routes.agenda_categories import bp as agenda_categories_bp
from routes.quick_notes import bp as quick_notes_bp
from routes.calendar import bp as calendar_bp
from routes.habits import bp as habits_bp
from routes.courses import bp as courses_bp
from routes.courses_aliases import bp as courses_aliases_bp
from routes.blocks import bp as blocks_bp
from routes.pdf import bp as pdf_bp
from routes.quiz import bp as quiz_bp
from routes.addons import bp as addons_bp
from routes.todos import bp as todos_bp
from routes.audio import bp as audio_bp
from routes.health import bp as health_bp
from routes.tts import bp as tts_bp
from routes.settings import bp as settings_bp
from routes.notebooklm_settings import bp as notebooklm_settings_bp
from routes.notebooklm_login import bp as notebooklm_login_bp
from routes.ai import bp as ai_bp
from routes.notebooklm_content import bp as notebooklm_content_bp
from routes.yt_meta import bp as yt_meta_bp
from routes.backup import bp as backup_bp


def create_app() -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__)
    _configure_app(app)
    CORS(app, origins=_cors_origins(), supports_credentials=True)
    _register_blueprints(app)
    _register_error_handlers(app)
    init_static(app)
    return app


def _configure_app(app: Flask) -> None:
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-change-in-production')
    app.config['JWT_SECRET_KEY'] = os.environ.get('JWT_SECRET_KEY', 'jwt-secret-change-in-production')
    app.config['JWT_ACCESS_TOKEN_EXPIRES'] = int(
        os.environ.get('JWT_ACCESS_TOKEN_EXPIRES', 3600)
    )


def _cors_origins() -> list:
    return os.environ.get(
        'CORS_ORIGINS', 'http://localhost:3000,http://localhost:8080'
    ).split(',')


def _register_blueprints(app: Flask) -> None:
    blueprints = [
        health_bp, auth_bp, agenda_bp, agenda_sessions_bp,
        agenda_state_bp, agenda_categories_bp,
        quick_notes_bp, calendar_bp,
        habits_bp, courses_bp, courses_aliases_bp, blocks_bp,
        pdf_bp, quiz_bp, addons_bp,
        todos_bp, audio_bp, tts_bp, settings_bp,
        notebooklm_settings_bp, notebooklm_login_bp,
        ai_bp, notebooklm_content_bp, backup_bp,
        yt_meta_bp,
    ]
    for bp in blueprints:
        app.register_blueprint(bp, url_prefix='/api')


def _register_error_handlers(app: Flask) -> None:
    @app.errorhandler(400)
    def bad_request(e):
        return jsonify({'error': 'Bad request', 'message': str(e)}), 400

    @app.errorhandler(401)
    def unauthorized(e):
        return jsonify({'error': 'Unauthorized', 'message': 'Authentication required'}), 401

    @app.errorhandler(403)
    def forbidden(e):
        return jsonify({'error': 'Forbidden', 'message': 'Insufficient permissions'}), 403

    @app.errorhandler(404)
    def not_found(e):
        return jsonify({'error': 'Not found', 'message': 'Resource not found'}), 404

    @app.errorhandler(500)
    def internal_error(e):
        return jsonify({'error': 'Internal server error', 'message': 'An unexpected error occurred'}), 500


# Create app instance for gunicorn
app = create_app()

if __name__ == '__main__':
    # Development only
    app.run(host='0.0.0.0', port=int(os.environ.get('FLASK_RUN_PORT', '8080')), debug=True)