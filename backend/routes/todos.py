# backend/routes/todos.py
"""Todos routes for StudyFlow PWA v3."""

from datetime import datetime
from flask import Blueprint, request, jsonify

from backend.database import execute, fetchone, fetchall
from backend.routes.auth import token_required
from backend.models import Todo


bp = Blueprint('todos', __name__)


@bp.get('/todos')
@token_required
def list_todos(current_user_id: int):
    """Get all todos for the current user."""
    completed = request.args.get('completed', type=lambda v: v.lower() == 'true')

    query = 'SELECT * FROM todos WHERE user_id = %s'
    params = [current_user_id]

    if completed is not None:
        query += ' AND completed = %s'
        params.append(completed)

    query += ' ORDER BY created_at DESC'

    rows = fetchall(query, tuple(params))
    return jsonify({'todos': [Todo.from_row(r).to_dict() for r in rows]})


@bp.post('/todos')
@token_required
def create_todo(current_user_id: int):
    """Create a new todo."""
    data = request.get_json() or {}

    if not data.get('title'):
        return jsonify({'error': 'title is required'}), 400

    due_date = None
    if data.get('due_date'):
        try:
            due_date = datetime.fromisoformat(data['due_date'].replace('Z', '+00:00'))
        except ValueError:
            return jsonify({'error': 'Invalid due_date format. Use ISO 8601.'}), 400

    execute(
        '''INSERT INTO todos (user_id, title, description, priority, due_date)
           VALUES (%s, %s, %s, %s, %s)''',
        (
            current_user_id,
            data['title'],
            data.get('description'),
            data.get('priority', 'medium'),
            due_date
        )
    )

    row = fetchone(
        'SELECT * FROM todos WHERE user_id = %s ORDER BY created_at DESC LIMIT 1',
        (current_user_id,)
    )
    return jsonify({'todo': Todo.from_row(row).to_dict()}), 201


@bp.patch('/todos/<int:todo_id>')
@token_required
def update_todo(current_user_id: int, todo_id: int):
    """Update a todo."""
    row = fetchone('SELECT * FROM todos WHERE id = %s AND user_id = %s', (todo_id, current_user_id))
    if not row:
        return jsonify({'error': 'Todo not found'}), 404

    data = request.get_json() or {}
    updates = []
    params = []

    allowed_fields = ['title', 'description', 'completed', 'priority', 'due_date']
    for field in allowed_fields:
        if field in data:
            if field == 'due_date' and data[field]:
                try:
                    updates.append('due_date = %s')
                    params.append(datetime.fromisoformat(data[field].replace('Z', '+00:00')))
                except ValueError:
                    return jsonify({'error': 'Invalid due_date format. Use ISO 8601.'}), 400
            else:
                updates.append(f'{field} = %s')
                params.append(data[field])

    if not updates:
        return jsonify({'error': 'No fields to update'}), 400

    updates.append('updated_at = NOW()')
    params.append(todo_id)

    execute(f'UPDATE todos SET {", ".join(updates)} WHERE id = %s', tuple(params))

    row = fetchone('SELECT * FROM todos WHERE id = %s', (todo_id,))
    return jsonify({'todo': Todo.from_row(row).to_dict()})


@bp.delete('/todos/<int:todo_id>')
@token_required
def delete_todo(current_user_id: int, todo_id: int):
    """Delete a todo."""
    result = execute('DELETE FROM todos WHERE id = %s AND user_id = %s', (todo_id, current_user_id))
    if result == 0:
        return jsonify({'error': 'Todo not found'}), 404

    return jsonify({'message': 'Todo deleted successfully'})