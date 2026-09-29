# backend/routes/courses.py
"""Courses routes for StudyFlow PWA v3.

v3 core routes (list/create/get/update/delete courses & topics) live here.
v2-shaped alias routes (rename / description / topic under courses / etc.)
live in courses_aliases.py to keep each file <500 lines.
"""

from flask import Blueprint, request, jsonify

from database import execute, fetchone, fetchall, get_db
from routes.auth import token_required
from models import Course, Topic, Block


bp = Blueprint('courses', __name__)


def _hydrate_course(course_row):
    """Attach topics[] (each with its blocks[]) to a course row dict."""
    course = Course.from_row(course_row).to_dict()
    topics_rows = fetchall(
        'SELECT * FROM topics WHERE course_id = %s ORDER BY order_index ASC',
        (course_row['id'],)
    )
    # Sub-phase S3: load all blocks for this course in one query, then
    # bucket them by topic_id so each topic gets its ordered list.
    blocks_rows = fetchall(
        'SELECT * FROM blocks WHERE course_id = %s '
        'ORDER BY topic_id NULLS FIRST, order_index ASC',
        (course_row['id'],)
    )
    by_topic = {}
    for b in blocks_rows:
        by_topic.setdefault(b.get('topic_id'), []).append(b)
    course['topics'] = []
    for t in topics_rows:
        td = Topic.from_row(t).to_dict()
        td['blocks'] = [
            Block.from_row(b).to_dict() for b in by_topic.get(t['id'], [])
        ]
        course['topics'].append(td)
    # Also expose a flat blocks[] so the v2 "course.blocks" view keeps
    # working without an extra fetch.
    course['blocks'] = [Block.from_row(b).to_dict() for b in blocks_rows]
    return course


# ============================== Courses ==============================
@bp.get('/courses')
@token_required
def list_courses(current_user_id: int):
    """Get all courses (with topics[]) for the current user."""
    # Issue #12: order_index drives the sidebar order (drag&drop), the same
    # criterion topics and blocks already use. created_at DESC stays as a
    # tiebreaker so pre-migration courses (all order_index=0) keep the exact
    # order the user sees today instead of shuffling on upgrade.
    courses_rows = fetchall(
        'SELECT * FROM courses WHERE user_id = %s '
        'ORDER BY order_index ASC, created_at DESC',
        (current_user_id,)
    )
    courses = [_hydrate_course(c) for c in courses_rows]
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
        (current_user_id, data['title'],
         data.get('description'), data.get('color'))
    )

    row = fetchone(
        'SELECT * FROM courses WHERE user_id = %s ORDER BY created_at DESC LIMIT 1',
        (current_user_id,)
    )
    course = _hydrate_course(row)
    return jsonify({'course': course}), 201


@bp.get('/courses/<int:course_id>')
@token_required
def get_course(current_user_id: int, course_id: int):
    """Get a specific course with its topics (and empty blocks per topic)."""
    row = fetchone(
        'SELECT * FROM courses WHERE id = %s AND user_id = %s',
        (course_id, current_user_id)
    )
    if not row:
        return jsonify({'error': 'Course not found'}), 404

    course = _hydrate_course(row)
    return jsonify({'course': course})


@bp.patch('/courses/<int:course_id>')
@token_required
def update_course(current_user_id: int, course_id: int):
    """Update a course (title / description / color / progress /
    is_favorite / icon).

    Issue #12: is_favorite and icon come from the v2 port. `order_index` is
    deliberately NOT editable here — it only changes through
    POST /courses/reorder, which rewrites the whole list, so a stray
    single-row write could not drift from the visible order.
    """
    row = fetchone(
        'SELECT * FROM courses WHERE id = %s AND user_id = %s',
        (course_id, current_user_id)
    )
    if not row:
        return jsonify({'error': 'Course not found'}), 404

    data = request.get_json() or {}
    updates = []
    params = []

    for field in ('title', 'description', 'color', 'progress',
                  'is_favorite', 'icon'):
        if field not in data:
            continue
        value = data[field]
        # is_favorite is a real boolean column. A JSON string like "false"
        # would be handed to psycopg2 as text and blow up as a type error
        # deep in the driver; reject it here with a useful message instead.
        if field == 'is_favorite' and not isinstance(value, bool):
            return jsonify({'error': 'is_favorite must be a boolean'}), 400
        updates.append(f'{field} = %s')
        params.append(value)

    if not updates:
        return jsonify({'error': 'No fields to update'}), 400

    updates.append('updated_at = NOW()')
    params.append(course_id)

    execute(f'UPDATE courses SET {", ".join(updates)} WHERE id = %s', tuple(params))

    row = fetchone('SELECT * FROM courses WHERE id = %s', (course_id,))
    return jsonify({'course': _hydrate_course(row)})


@bp.delete('/courses/<int:course_id>')
@token_required
def delete_course(current_user_id: int, course_id: int):
    """Delete a course."""
    result = execute(
        'DELETE FROM courses WHERE id = %s AND user_id = %s',
        (course_id, current_user_id)
    )
    if result == 0:
        return jsonify({'error': 'Course not found'}), 404
    return jsonify({'message': 'Course deleted successfully'})


@bp.post('/courses/reorder')
@token_required
def reorder_courses(current_user_id: int):
    """Persist the sidebar order after a course drag&drop (Issue #12).

    The client always sends the full visible list, so this rewrites every
    order_index. Same contract as POST /topics/reorder: a single batch
    UPDATE ... FROM (VALUES ...) instead of N round-trips, wrapped in one
    transaction so a mid-list failure can never leave a half-applied order
    (which would show up as a course jumping on the next reload).
    """
    data = request.get_json() or {}
    course_ids = data.get('course_ids', [])
    if not course_ids:
        return jsonify({'error': 'course_ids array is required'}), 400
    if len(set(course_ids)) != len(course_ids):
        return jsonify({'error': 'course_ids contains duplicates'}), 400

    placeholders = ','.join(['%s'] * len(course_ids))
    rows = fetchall(
        f'SELECT id FROM courses WHERE id IN ({placeholders}) AND user_id = %s',
        (*course_ids, current_user_id)
    )
    if len(rows) != len(course_ids):
        return jsonify({'error': 'Some courses not found or unauthorized'}), 404

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                'UPDATE courses c SET order_index = v.pos, updated_at = NOW() '
                'FROM (VALUES ' +
                ','.join(['(%s, %s)'] * len(course_ids)) +
                ') AS v(id, pos) WHERE c.id = v.id',
                [val for pair in zip(course_ids, range(len(course_ids)))
                 for val in pair]
            )
        conn.commit()
    return jsonify({'message': 'Courses reordered successfully'})


# ============================== Topics ==============================
@bp.post('/courses/<int:course_id>/topics')
@token_required
def create_topic(current_user_id: int, course_id: int):
    """Create a new topic in a course."""
    row = fetchone(
        'SELECT * FROM courses WHERE id = %s AND user_id = %s',
        (course_id, current_user_id)
    )
    if not row:
        return jsonify({'error': 'Course not found'}), 404

    data = request.get_json() or {}
    if not data.get('title'):
        return jsonify({'error': 'title is required'}), 400

    max_order = fetchone(
        'SELECT COALESCE(MAX(order_index), -1) AS max_order '
        'FROM topics WHERE course_id = %s',
        (course_id,)
    )
    order_index = (max_order['max_order'] if max_order else -1) + 1

    execute(
        '''INSERT INTO topics
             (course_id, user_id, title, description, order_index,
              status, estimated_minutes)
           VALUES (%s, %s, %s, %s, %s, %s, %s)''',
        (course_id, current_user_id, data['title'],
         data.get('description'), order_index,
         data.get('status', 'pending'), data.get('estimated_minutes'))
    )

    row = fetchone(
        'SELECT * FROM topics WHERE course_id = %s ORDER BY created_at DESC LIMIT 1',
        (course_id,)
    )
    topic = Topic.from_row(row).to_dict()
    topic['blocks'] = []
    return jsonify({'topic': topic}), 201


@bp.patch('/topics/<int:topic_id>')
@token_required
def update_topic(current_user_id: int, topic_id: int):
    """Update a topic (by topic_id)."""
    row = fetchone(
        'SELECT * FROM topics WHERE id = %s AND user_id = %s',
        (topic_id, current_user_id)
    )
    if not row:
        return jsonify({'error': 'Topic not found'}), 404

    data = request.get_json() or {}
    updates = []
    params = []

    for field in ('title', 'description', 'order_index',
                  'status', 'estimated_minutes', 'actual_minutes'):
        if field in data:
            updates.append(f'{field} = %s')
            params.append(data[field])

    if not updates:
        return jsonify({'error': 'No fields to update'}), 400

    updates.append('updated_at = NOW()')
    params.append(topic_id)

    execute(f'UPDATE topics SET {", ".join(updates)} WHERE id = %s', tuple(params))

    row = fetchone('SELECT * FROM topics WHERE id = %s', (topic_id,))
    topic = Topic.from_row(row).to_dict()
    topic['blocks'] = []
    return jsonify({'topic': topic})


@bp.delete('/topics/<int:topic_id>')
@token_required
def delete_topic(current_user_id: int, topic_id: int):
    """Delete a topic (by topic_id)."""
    result = execute(
        'DELETE FROM topics WHERE id = %s AND user_id = %s',
        (topic_id, current_user_id)
    )
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

    placeholders = ','.join(['%s'] * len(topic_ids))
    rows = fetchall(
        f'SELECT id FROM topics WHERE id IN ({placeholders}) AND user_id = %s',
        (*topic_ids, current_user_id)
    )
    if len(rows) != len(topic_ids):
        return jsonify({'error': 'Some topics not found or unauthorized'}), 404

    for index, topic_id in enumerate(topic_ids):
        execute(
            'UPDATE topics SET order_index = %s, updated_at = NOW() WHERE id = %s',
            (index, topic_id)
        )
    return jsonify({'message': 'Topics reordered successfully'})


# ============================== Topic notes (S4) ==============================
# Per-topic freeform notes panel. Stored in topics.notes (TEXT, '' default).
# v2 has a similar concept at the COURSE level (/courses/<id>/notes). For v3
# we keep it topic-scoped — that's where the Studyflow notes drawer lives.
@bp.get('/courses/<int:course_id>/topics/<int:topic_id>/notes')
@token_required
def get_topic_notes(current_user_id: int, course_id: int, topic_id: int):
    """Return {notes: {content: string}} for the given topic."""
    row = fetchone(
        'SELECT notes FROM topics '
        'WHERE id = %s AND course_id = %s AND user_id = %s',
        (topic_id, course_id, current_user_id)
    )
    if not row:
        return jsonify({'error': 'Topic not found'}), 404
    return jsonify({'notes': {'content': row.get('notes') or ''}})


@bp.put('/courses/<int:course_id>/topics/<int:topic_id>/notes')
@token_required
def set_topic_notes(current_user_id: int, course_id: int, topic_id: int):
    """Persist the notes text. Accepts {content, notes} (v2-compatible)."""
    body = request.get_json() or {}
    content = body.get('content')
    if content is None:
        content = body.get('notes') or ''
    row = fetchone(
        'SELECT id FROM topics '
        'WHERE id = %s AND course_id = %s AND user_id = %s',
        (topic_id, course_id, current_user_id)
    )
    if not row:
        return jsonify({'error': 'Topic not found'}), 404
    execute(
        'UPDATE topics SET notes = %s, updated_at = NOW() '
        'WHERE id = %s AND course_id = %s AND user_id = %s',
        (content, topic_id, course_id, current_user_id)
    )
    return jsonify({'notes': {'content': content}})