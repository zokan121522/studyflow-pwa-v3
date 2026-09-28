# backend/routes/courses.py
"""Courses routes for StudyFlow PWA v3."""

from flask import Blueprint, request, jsonify

from backend.database import execute, fetchone, fetchall
from backend.routes.auth import token_required
from backend.models import Course, Topic


bp = Blueprint('courses', __name__)


@bp.get('/courses')
@token_required
def list_courses(current_user_id: int):
    """Get all courses (with topics) for the current user."""
    courses_rows = fetchall(
        'SELECT * FROM courses WHERE user_id = %s ORDER BY created_at DESC',
        (current_user_id,)
    )

    courses = []
    for c in courses_rows:
        course = Course.from_row(c).to_dict()
        topics_rows = fetchall(
            'SELECT * FROM topics WHERE course_id = %s ORDER BY order_index ASC',
            (c['id'],)
        )
        course['topics'] = [Topic.from_row(t).to_dict() for t in topics_rows]
        courses.append(course)

    return jsonify({'courses': courses})


@bp.post('/courses')
@token_required
def create_course(current_user_id: int):
    """Create a new course."""
    data = request.get_json() or {}

    if not data.get('title'):
        return jsonify({'error': 'title is required'}), 400

    execute(
        '''INSERT INTO courses (user_id, title, description, color)
           VALUES (%s, %s, %s, %s)''',
        (
            current_user_id,
            data['title'],
            data.get('description'),
            data.get('color')
        )
    )

    row = fetchone(
        'SELECT * FROM courses WHERE user_id = %s ORDER BY created_at DESC LIMIT 1',
        (current_user_id,)
    )
    course = Course.from_row(row).to_dict()
    course['topics'] = []

    return jsonify({'course': course}), 201


@bp.get('/courses/<int:course_id>')
@token_required
def get_course(current_user_id: int, course_id: int):
    """Get a specific course with its topics."""
    row = fetchone('SELECT * FROM courses WHERE id = %s AND user_id = %s', (course_id, current_user_id))
    if not row:
        return jsonify({'error': 'Course not found'}), 404

    course = Course.from_row(row).to_dict()

    topics_rows = fetchall(
        'SELECT * FROM topics WHERE course_id = %s ORDER BY order_index ASC',
        (course_id,)
    )
    course['topics'] = [Topic.from_row(t).to_dict() for t in topics_rows]

    return jsonify({'course': course})


@bp.patch('/courses/<int:course_id>')
@token_required
def update_course(current_user_id: int, course_id: int):
    """Update a course."""
    row = fetchone('SELECT * FROM courses WHERE id = %s AND user_id = %s', (course_id, current_user_id))
    if not row:
        return jsonify({'error': 'Course not found'}), 404

    data = request.get_json() or {}
    updates = []
    params = []

    allowed_fields = ['title', 'description', 'color', 'progress']
    for field in allowed_fields:
        if field in data:
            updates.append(f'{field} = %s')
            params.append(data[field])

    if not updates:
        return jsonify({'error': 'No fields to update'}), 400

    updates.append('updated_at = NOW()')
    params.append(course_id)

    execute(f'UPDATE courses SET {", ".join(updates)} WHERE id = %s', tuple(params))

    row = fetchone('SELECT * FROM courses WHERE id = %s', (course_id,))
    course = Course.from_row(row).to_dict()

    topics_rows = fetchall(
        'SELECT * FROM topics WHERE course_id = %s ORDER BY order_index ASC',
        (course_id,)
    )
    course['topics'] = [Topic.from_row(t).to_dict() for t in topics_rows]

    return jsonify({'course': course})


@bp.delete('/courses/<int:course_id>')
@token_required
def delete_course(current_user_id: int, course_id: int):
    """Delete a course."""
    result = execute('DELETE FROM courses WHERE id = %s AND user_id = %s', (course_id, current_user_id))
    if result == 0:
        return jsonify({'error': 'Course not found'}), 404

    return jsonify({'message': 'Course deleted successfully'})


# ============================== Topics ==============================
@bp.post('/courses/<int:course_id>/topics')
@token_required
def create_topic(current_user_id: int, course_id: int):
    """Create a new topic in a course."""
    # Verify course ownership
    row = fetchone('SELECT * FROM courses WHERE id = %s AND user_id = %s', (course_id, current_user_id))
    if not row:
        return jsonify({'error': 'Course not found'}), 404

    data = request.get_json() or {}

    if not data.get('title'):
        return jsonify({'error': 'title is required'}), 400

    # Get max order_index
    max_order = fetchone(
        'SELECT COALESCE(MAX(order_index), -1) as max_order FROM topics WHERE course_id = %s',
        (course_id,)
    )
    order_index = (max_order['max_order'] if max_order else -1) + 1

    execute(
        '''INSERT INTO topics (course_id, user_id, title, description, order_index, status, estimated_minutes)
           VALUES (%s, %s, %s, %s, %s, %s, %s)''',
        (
            course_id,
            current_user_id,
            data['title'],
            data.get('description'),
            order_index,
            data.get('status', 'pending'),
            data.get('estimated_minutes')
        )
    )

    row = fetchone(
        'SELECT * FROM topics WHERE course_id = %s ORDER BY created_at DESC LIMIT 1',
        (course_id,)
    )
    return jsonify({'topic': Topic.from_row(row).to_dict()}), 201


@bp.patch('/topics/<int:topic_id>')
@token_required
def update_topic(current_user_id: int, topic_id: int):
    """Update a topic."""
    row = fetchone('SELECT * FROM topics WHERE id = %s AND user_id = %s', (topic_id, current_user_id))
    if not row:
        return jsonify({'error': 'Topic not found'}), 404

    data = request.get_json() or {}
    updates = []
    params = []

    allowed_fields = ['title', 'description', 'order_index', 'status', 'estimated_minutes', 'actual_minutes']
    for field in allowed_fields:
        if field in data:
            updates.append(f'{field} = %s')
            params.append(data[field])

    if not updates:
        return jsonify({'error': 'No fields to update'}), 400

    updates.append('updated_at = NOW()')
    params.append(topic_id)

    execute(f'UPDATE topics SET {", ".join(updates)} WHERE id = %s', tuple(params))

    row = fetchone('SELECT * FROM topics WHERE id = %s', (topic_id,))
    return jsonify({'topic': Topic.from_row(row).to_dict()})


@bp.delete('/topics/<int:topic_id>')
@token_required
def delete_topic(current_user_id: int, topic_id: int):
    """Delete a topic."""
    result = execute('DELETE FROM topics WHERE id = %s AND user_id = %s', (topic_id, current_user_id))
    if result == 0:
        return jsonify({'error': 'Topic not found'}), 404

    return jsonify({'message': 'Topic deleted successfully'})


@bp.post('/topics/reorder')
@token_required
def reorder_topics(current_user_id: int):
    """Reorder topics within a course."""
    data = request.get_json() or {}
    topic_ids = data.get('topic_ids', [])

    if not topic_ids:
        return jsonify({'error': 'topic_ids array is required'}), 400

    # Verify all topics belong to user
    placeholders = ','.join(['%s'] * len(topic_ids))
    rows = fetchall(
        f'SELECT id FROM topics WHERE id IN ({placeholders}) AND user_id = %s',
        (*topic_ids, current_user_id)
    )
    if len(rows) != len(topic_ids):
        return jsonify({'error': 'Some topics not found or unauthorized'}), 404

    # Update order_index for each topic
    for index, topic_id in enumerate(topic_ids):
        execute(
            'UPDATE topics SET order_index = %s, updated_at = NOW() WHERE id = %s',
            (index, topic_id)
        )

    return jsonify({'message': 'Topics reordered successfully'})