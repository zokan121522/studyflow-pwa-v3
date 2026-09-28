# backend/routes/habits.py
"""Habits routes for StudyFlow PWA v3."""

from datetime import date, datetime
from flask import Blueprint, request, jsonify

from backend.database import execute, fetchone, fetchall
from backend.routes.auth import token_required
from backend.models import Habit, HabitEntry


bp = Blueprint('habits', __name__)


@bp.get('/habits')
@token_required
def list_habits(current_user_id: int):
    """Get all habits for the current user with today's entries."""
    today = date.today().isoformat()

    habits_rows = fetchall(
        'SELECT * FROM habits WHERE user_id = %s ORDER BY created_at DESC',
        (current_user_id,)
    )

    # Get today's entries for all habits
    habit_ids = [h['id'] for h in habits_rows]
    entries = {}
    if habit_ids:
        placeholders = ','.join(['%s'] * len(habit_ids))
        entries_rows = fetchall(
            f'SELECT * FROM habit_entries WHERE habit_id IN ({placeholders}) AND date = %s',
            (*habit_ids, today)
        )
        entries = {e['habit_id']: HabitEntry.from_row(e).to_dict() for e in entries_rows}

    habits = []
    for h in habits_rows:
        habit = Habit.from_row(h).to_dict()
        habit['today_entry'] = entries.get(h['id'])
        habits.append(habit)

    return jsonify({'habits': habits})


@bp.post('/habits')
@token_required
def create_habit(current_user_id: int):
    """Create a new habit."""
    data = request.get_json() or {}

    if not data.get('name'):
        return jsonify({'error': 'name is required'}), 400

    execute(
        '''INSERT INTO habits (user_id, name, description, frequency, target_count, color)
           VALUES (%s, %s, %s, %s, %s, %s)''',
        (
            current_user_id,
            data['name'],
            data.get('description'),
            data.get('frequency', 'daily'),
            data.get('target_count', 1),
            data.get('color')
        )
    )

    row = fetchone(
        'SELECT * FROM habits WHERE user_id = %s ORDER BY created_at DESC LIMIT 1',
        (current_user_id,)
    )
    return jsonify({'habit': Habit.from_row(row).to_dict()}), 201


@bp.patch('/habits/<int:habit_id>')
@token_required
def update_habit(current_user_id: int, habit_id: int):
    """Update a habit."""
    row = fetchone('SELECT * FROM habits WHERE id = %s AND user_id = %s', (habit_id, current_user_id))
    if not row:
        return jsonify({'error': 'Habit not found'}), 404

    data = request.get_json() or {}
    updates = []
    params = []

    allowed_fields = ['name', 'description', 'frequency', 'target_count', 'color']
    for field in allowed_fields:
        if field in data:
            updates.append(f'{field} = %s')
            params.append(data[field])

    if not updates:
        return jsonify({'error': 'No fields to update'}), 400

    updates.append('updated_at = NOW()')
    params.append(habit_id)

    execute(f'UPDATE habits SET {", ".join(updates)} WHERE id = %s', tuple(params))

    row = fetchone('SELECT * FROM habits WHERE id = %s', (habit_id,))
    return jsonify({'habit': Habit.from_row(row).to_dict()})


@bp.delete('/habits/<int:habit_id>')
@token_required
def delete_habit(current_user_id: int, habit_id: int):
    """Delete a habit."""
    result = execute('DELETE FROM habits WHERE id = %s AND user_id = %s', (habit_id, current_user_id))
    if result == 0:
        return jsonify({'error': 'Habit not found'}), 404

    return jsonify({'message': 'Habit deleted successfully'})


@bp.post('/habits/<int:habit_id>/entries')
@token_required
def upsert_habit_entry(current_user_id: int, habit_id: int):
    """Create or update a habit entry for a specific date."""
    # Verify habit ownership
    row = fetchone('SELECT * FROM habits WHERE id = %s AND user_id = %s', (habit_id, current_user_id))
    if not row:
        return jsonify({'error': 'Habit not found'}), 404

    data = request.get_json() or {}
    entry_date = data.get('date', date.today().isoformat())
    count = data.get('count', 1)
    completed = data.get('completed', count >= row['target_count'])

    # Upsert entry
    execute(
        '''INSERT INTO habit_entries (habit_id, user_id, date, count, completed)
           VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (habit_id, date) DO UPDATE SET
             count = EXCLUDED.count,
             completed = EXCLUDED.completed''',
        (habit_id, current_user_id, entry_date, count, completed)
    )

    entry_row = fetchone(
        'SELECT * FROM habit_entries WHERE habit_id = %s AND date = %s',
        (habit_id, entry_date)
    )
    return jsonify({'entry': HabitEntry.from_row(entry_row).to_dict()})


@bp.get('/habits/<int:habit_id>/entries')
@token_required
def get_habit_entries(current_user_id: int, habit_id: int):
    """Get all entries for a habit."""
    row = fetchone('SELECT * FROM habits WHERE id = %s AND user_id = %s', (habit_id, current_user_id))
    if not row:
        return jsonify({'error': 'Habit not found'}), 404

    rows = fetchall(
        'SELECT * FROM habit_entries WHERE habit_id = %s ORDER BY date DESC',
        (habit_id,)
    )
    return jsonify({'entries': [HabitEntry.from_row(r).to_dict() for r in rows]})