# backend/routes/auth.py
"""Authentication routes for StudyFlow PWA v3."""

import os
import jwt
import bcrypt
from datetime import datetime, timedelta
from functools import wraps
from typing import Optional          # usado en la anotación de get_current_user (L72)
from flask import Blueprint, request, jsonify, current_app

from database import execute, fetchone, fetchall
from models import User


bp = Blueprint('auth', __name__)


# ============================== JWT Helpers ==============================
def generate_token(user_id: int) -> str:
    """Generate a JWT token for a user."""
    payload = {
        'user_id': user_id,
        'exp': datetime.utcnow() + timedelta(seconds=current_app.config['JWT_ACCESS_TOKEN_EXPIRES']),
        'iat': datetime.utcnow()
    }
    return jwt.encode(payload, current_app.config['JWT_SECRET_KEY'], algorithm='HS256')


def decode_token(token: str) -> dict:
    """Decode and validate a JWT token."""
    return jwt.decode(token, current_app.config['JWT_SECRET_KEY'], algorithms=['HS256'])


# Default implicit user id for single-user local-mode fallback.
# When no Authorization header is sent (or the token is invalid/expired),
# routes attribute data to LOCAL_USER_ID instead of returning 401.
LOCAL_USER_ID = 1


def token_required(f):
    """Decorator to require valid JWT token.

    Single-user local mode: if no Authorization header is sent or the token
    is invalid/expired, fall back to LOCAL_USER_ID so the PWA can be used
    without a login screen. A valid token still scopes data to its user_id.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        auth_header = request.headers.get('Authorization')

        if auth_header and auth_header.startswith('Bearer '):
            token = auth_header.split(' ')[1]

        if not token:
            # No token → single-user local mode
            return f(LOCAL_USER_ID, *args, **kwargs)

        try:
            data = decode_token(token)
            current_user_id = data['user_id']
        except jwt.ExpiredSignatureError:
            return f(LOCAL_USER_ID, *args, **kwargs)
        except jwt.InvalidTokenError:
            return f(LOCAL_USER_ID, *args, **kwargs)

        return f(current_user_id, *args, **kwargs)

    return decorated


def get_current_user(user_id: int) -> Optional[User]:
    """Get user by ID."""
    row = fetchone('SELECT * FROM users WHERE id = %s', (user_id,))
    return User.from_row(row) if row else None


# ============================== Routes ==============================
@bp.post('/auth/register')
def register():
    """Register a new user."""
    data = request.get_json() or {}
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')
    name = data.get('name', '').strip()

    if not email or not password:
        return jsonify({'error': 'Email and password are required'}), 400

    if len(password) < 8:
        return jsonify({'error': 'Password must be at least 8 characters'}), 400

    # Check if user exists
    existing = fetchone('SELECT id FROM users WHERE email = %s', (email,))
    if existing:
        return jsonify({'error': 'Email already registered'}), 409

    # Hash password
    password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

    # Create user
    try:
        execute(
            'INSERT INTO users (email, password_hash, name) VALUES (%s, %s, %s)',
            (email, password_hash, name)
        )
    except Exception as e:
        return jsonify({'error': 'Registration failed', 'message': str(e)}), 500

    # Get created user
    user_row = fetchone('SELECT * FROM users WHERE email = %s', (email,))
    user = User.from_row(user_row)

    # Generate token
    token = generate_token(user.id)

    return jsonify({
        'user': user.to_dict(),
        'token': token
    }), 201


@bp.post('/auth/login')
def login():
    """Login user and return JWT token."""
    data = request.get_json() or {}
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')

    if not email or not password:
        return jsonify({'error': 'Email and password are required'}), 400

    # Find user
    user_row = fetchone('SELECT * FROM users WHERE email = %s', (email,))
    if not user_row:
        return jsonify({'error': 'Invalid credentials'}), 401

    # Verify password
    if not bcrypt.checkpw(password.encode('utf-8'), user_row['password_hash'].encode('utf-8')):
        return jsonify({'error': 'Invalid credentials'}), 401

    user = User.from_row(user_row)
    token = generate_token(user.id)

    return jsonify({
        'user': user.to_dict(),
        'token': token
    })


@bp.get('/auth/me')
@token_required
def me(current_user_id: int):
    """Get current authenticated user."""
    user = get_current_user(current_user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    return jsonify({'user': user.to_dict()})


@bp.patch('/auth/me')
@token_required
def update_profile(current_user_id: int):
    """Update current user profile."""
    data = request.get_json() or {}
    name = data.get('name', '').strip()
    avatar_url = data.get('avatar_url', '').strip()

    updates = []
    params = []

    if name:
        updates.append('name = %s')
        params.append(name)
    if avatar_url:
        updates.append('avatar_url = %s')
        params.append(avatar_url)

    if not updates:
        return jsonify({'error': 'No fields to update'}), 400

    updates.append('updated_at = NOW()')
    params.append(current_user_id)

    execute(f'UPDATE users SET {", ".join(updates)} WHERE id = %s', tuple(params))

    user = get_current_user(current_user_id)
    return jsonify({'user': user.to_dict()})


@bp.post('/auth/change-password')
@token_required
def change_password(current_user_id: int):
    """Change user password."""
    data = request.get_json() or {}
    current_password = data.get('current_password', '')
    new_password = data.get('new_password', '')

    if not current_password or not new_password:
        return jsonify({'error': 'Current and new password are required'}), 400

    if len(new_password) < 8:
        return jsonify({'error': 'New password must be at least 8 characters'}), 400

    # Verify current password
    user_row = fetchone('SELECT password_hash FROM users WHERE id = %s', (current_user_id,))
    if not user_row:
        return jsonify({'error': 'User not found'}), 404

    if not bcrypt.checkpw(current_password.encode('utf-8'), user_row['password_hash'].encode('utf-8')):
        return jsonify({'error': 'Current password is incorrect'}), 401

    # Hash new password
    new_hash = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    execute('UPDATE users SET password_hash = %s, updated_at = NOW() WHERE id = %s', (new_hash, current_user_id))

    return jsonify({'message': 'Password changed successfully'})