# backend/routes/agenda.py
"""Agenda/sessions routes for StudyFlow PWA v3."""

from datetime import datetime
from flask import Blueprint, request, jsonify
from typing import Optional

from backend.database import execute, fetchone, fetchall
from backend.routes.auth import token_required
from backend.models import Session


bp = Blueprint('agenda', __name__)


def parse_datetime(dt_str: str) -> datetime:
    """Parse ISO datetime string."""
    return datetime.fromisoformat(dt_str.replace('Z', '+00:00'))


@bp.get('/agenda/sessions')
@token_required
def list_sessions(current_user_id: int):
    """Get all sessions for the current user, optionally filtered by date range."""
    start = request.args.get('start')
    end = request.args.get('end')

    query = 'SELECT * FROM sessions WHERE user_id = %s'
    params = [current_user_id]

    if start and end:
        query += ' AND start_time >= %s AND end_time <= %s'
        params.extend([start, end])

    query += ' ORDER BY start_time ASC'

    rows = fetchall(query, tuple(params))
    return jsonify({'sessions': [Session.from_row(r).to_dict() for r in rows]})


@bp.post('/agenda/sessions')
@token_required
def create_session(current_user_id: int):
    """Create a new session."""
    data = request.get_json() or {}

    required = ['title', 'start_time', 'end_time']
    for field in required:
        if not data.get(field):
            return jsonify({'error': f'{field} is required'}), 400

    try:
        start_time = parse_datetime(data['start_time'])
        end_time = parse_datetime(data['end_time'])
    except ValueError:
        return jsonify({'error': 'Invalid datetime format. Use ISO 8601.'}), 400

    if end_time <= start_time:
        return jsonify({'error': 'end_time must be after start_time'}), 400

    execute(
        '''INSERT INTO sessions (user_id, title, description, category, start_time, end_time, color, is_recurring, recurrence_rule)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)''',
        (
            current_user_id,
            data['title'],
            data.get('description'),
            data.get('category'),
            start_time,
            end_time,
            data.get('color'),
            data.get('is_recurring', False),
            data.get('recurrence_rule')
        )
    )

    # Get created session
    row = fetchone(
        'SELECT * FROM sessions WHERE user_id = %s ORDER BY created_at DESC LIMIT 1',
        (current_user_id,)
    )
    session = Session.from_row(row)

    return jsonify({'session': session.to_dict()}), 201


@bp.get('/agenda/sessions/<int:session_id>')
@token_required
def get_session(current_user_id: int, session_id: int):
    """Get a specific session."""
    row = fetchone('SELECT * FROM sessions WHERE id = %s AND user_id = %s', (session_id, current_user_id))
    if not row:
        return jsonify({'error': 'Session not found'}), 404

    return jsonify({'session': Session.from_row(row).to_dict()})


@bp.patch('/agenda/sessions/<int:session_id>')
@token_required
def update_session(current_user_id: int, session_id: int):
    """Update a session."""
    # Check ownership
    row = fetchone('SELECT * FROM sessions WHERE id = %s AND user_id = %s', (session_id, current_user_id))
    if not row:
        return jsonify({'error': 'Session not found'}), 404

    data = request.get_json() or {}
    updates = []
    params = []

    allowed_fields = ['title', 'description', 'category', 'start_time', 'end_time', 'color', 'is_recurring', 'recurrence_rule']
    for field in allowed_fields:
        if field in data:
            if field in ('start_time', 'end_time') and data[field]:
                try:
                    updates.append(f'{field} = %s')
                    params.append(parse_datetime(data[field]))
                except ValueError:
                    return jsonify({'error': f'Invalid {field} format. Use ISO 8601.'}), 400
            else:
                updates.append(f'{field} = %s')
                params.append(data[field])

    if not updates:
        return jsonify({'error': 'No fields to update'}), 400

    # Validate start_time < end_time if both provided
    if 'start_time' in data and 'end_time' in data:
        try:
            start = parse_datetime(data['start_time'])
            end = parse_datetime(data['end_time'])
            if end <= start:
                return jsonify({'error': 'end_time must be after start_time'}), 400
        except ValueError:
            pass  # Already handled above

    updates.append('updated_at = NOW()')
    params.append(session_id)

    execute(f'UPDATE sessions SET {", ".join(updates)} WHERE id = %s', tuple(params))

    # Return updated session
    row = fetchone('SELECT * FROM sessions WHERE id = %s', (session_id,))
    return jsonify({'session': Session.from_row(row).to_dict()})


@bp.delete('/agenda/sessions/<int:session_id>')
@token_required
def delete_session(current_user_id: int, session_id: int):
    """Delete a session."""
    result = execute('DELETE FROM sessions WHERE id = %s AND user_id = %s', (session_id, current_user_id))
    if result == 0:
        return jsonify({'error': 'Session not found'}), 404

    return jsonify({'message': 'Session deleted successfully'})