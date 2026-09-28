# backend/server.py
"""Flask API-first application factory for StudyFlow PWA v3."""

import os
from flask import Flask, jsonify
from flask_cors import CORS

from backend.routes.auth import bp as auth_bp
from backend.routes.agenda import bp as agenda_bp
from backend.routes.habits import bp as habits_bp
from backend.routes.courses import bp as courses_bp
from backend.routes.pdf import bp as pdf_bp
from backend.routes.quiz import bp as quiz_bp
from backend.routes.todos import bp as todos_bp
from backend.routes.audio import bp as audio_bp
from backend.routes.health import bp as health_bp
from backend.routes.tts import bp as tts_bp


def create_app() -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__)

    # Configuration
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-change-in-production')
    app.config['JWT_SECRET_KEY'] = os.environ.get('JWT_SECRET_KEY', 'jwt-secret-change-in-production')
    app.config['JWT_ACCESS_TOKEN_EXPIRES'] = int(os.environ.get('JWT_ACCESS_TOKEN_EXPIRES', 3600))

    # CORS for frontend (dev on 3000, prod on 8080)
    cors_origins = os.environ.get('CORS_ORIGINS', 'http://localhost:3000,http://localhost:8080').split(',')
    CORS(app, origins=cors_origins, supports_credentials=True)

    # Register blueprints with /api prefix
    app.register_blueprint(health_bp, url_prefix='/api')
    app.register_blueprint(auth_bp, url_prefix='/api')
    app.register_blueprint(agenda_bp, url_prefix='/api')
    app.register_blueprint(habits_bp, url_prefix='/api')
    app.register_blueprint(courses_bp, url_prefix='/api')
    app.register_blueprint(pdf_bp, url_prefix='/api')
    app.register_blueprint(quiz_bp, url_prefix='/api')
    app.register_blueprint(todos_bp, url_prefix='/api')
    app.register_blueprint(audio_bp, url_prefix='/api')
    app.register_blueprint(tts_bp, url_prefix='/api')

    # Global error handlers
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

    return app


# Create app instance for gunicorn
app = create_app()

if __name__ == '__main__':
    # Development only
    app.run(host='0.0.0.0', port=int(os.environ.get('FLASK_RUN_PORT', '8080')), debug=True)