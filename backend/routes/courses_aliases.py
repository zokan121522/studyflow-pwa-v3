# backend/routes/courses_aliases.py
"""v2-shaped alias routes for courses/topics.

v2's CoursesSidebar / CoursesAPI front-end posts to paths like:
  PATCH /api/courses/<id>/rename           {title}
  PATCH /api/courses/<id>/description      {description}
  PATCH /api/courses/<id>/topics/<tid>/rename {title}
  DELETE /api/courses/<id>/topics/<tid>
  POST   /api/courses/<id>/topics/reorder  (v2 shape, sibling of the
                                            v3 /api/topics/reorder)

v3 already exposes the canonical routes (PATCH /api/courses/<id>,
PATCH /api/topics/<id>, DELETE /api/topics/<id>, POST /api/topics/reorder).
These aliases preserve the v2 contract so the ported v2-shape front-end
works without changes, while leaving the canonical routes intact.
"""

from flask import Blueprint, request, jsonify

from backend.database import execute, fetchone
from backend.routes.auth import token_required


bp = Blueprint('courses_aliases', __name__)


def _user_course(course_id: int, user_id: int):
    return fetchone(
        'SELECT * FROM courses WHERE id = %s AND user_id = %s',
        (course_id, user_id)
    )


def _user_topic(topic_id: int, user_id: int):
    return fetchone(
        'SELECT * FROM topics WHERE id = %s AND user_id = %s',
        (topic_id, user_id)
    )


# ============================== Course aliases ==============================
@bp.patch('/courses/<int:course_id>/rename')
@token_required
def rename_course_alias(current_user_id: int, course_id: int):
    """v2 alias: PATCH /courses/<id>/rename {title}."""
    if not _user_course(course_id, current_user_id):
        return jsonify({'error': 'Course not found'}), 404

    data = request.get_json() or {}
    title = (data.get('title') or '').strip()
    if not title:
        return jsonify({'error': 'title is required'}), 400

    execute(
        'UPDATE courses SET title = %s, updated_at = NOW() WHERE id = %s',
        (title, course_id)
    )
    return jsonify({'message': 'Course renamed', 'title': title})


@bp.patch('/courses/<int:course_id>/description')
@token_required
def update_description_alias(current_user_id: int, course_id: int):
    """v2 alias: PATCH /courses/<id>/description {description}.

    Accepts null / empty string to clear the description.
    """
    if not _user_course(course_id, current_user_id):
        return jsonify({'error': 'Course not found'}), 404

    data = request.get_json() or {}
    description = data.get('description')  # may be None or empty string

    execute(
        'UPDATE courses SET description = %s, updated_at = NOW() '
        'WHERE id = %s',
        (description, course_id)
    )
    return jsonify({'message': 'Description updated'})


# ============================== Topic aliases (scoped under course) ========
@bp.patch('/courses/<int:course_id>/topics/<int:topic_id>/rename')
@token_required
def rename_topic_alias(
    current_user_id: int, course_id: int, topic_id: int
):
    """v2 alias: PATCH /courses/<id>/topics/<tid>/rename {title}.

    Validates that the topic belongs to the course AND to the user.
    """
    topic = _user_topic(topic_id, current_user_id)
    if not topic or topic['course_id'] != course_id:
        return jsonify({'error': 'Topic not found in this course'}), 404

    data = request.get_json() or {}
    title = (data.get('title') or '').strip()
    if not title:
        return jsonify({'error': 'title is required'}), 400

    execute(
        'UPDATE topics SET title = %s, updated_at = NOW() WHERE id = %s',
        (title, topic_id)
    )
    return jsonify({'message': 'Topic renamed', 'title': title})


@bp.delete('/courses/<int:course_id>/topics/<int:topic_id>')
@token_required
def delete_topic_alias(
    current_user_id: int, course_id: int, topic_id: int
):
    """v2 alias: DELETE /courses/<id>/topics/<tid>.

    Validates that the topic belongs to the course AND to the user.
    """
    topic = _user_topic(topic_id, current_user_id)
    if not topic or topic['course_id'] != course_id:
        return jsonify({'error': 'Topic not found in this course'}), 404

    execute(
        'DELETE FROM topics WHERE id = %s AND user_id = %s',
        (topic_id, current_user_id)
    )
    return jsonify({'message': 'Topic deleted successfully'})


@bp.post('/courses/<int:course_id>/topics/reorder')
@token_required
def reorder_topics_under_course_alias(
    current_user_id: int, course_id: int
):
    """v2 alias: POST /courses/<id>/topics/reorder {topic_ids: [...]}.

    Same semantics as POST /topics/reorder, but constrained to one course.
    Validates ownership of both the course and every topic_id in the list.
    """
    if not _user_course(course_id, current_user_id):
        return jsonify({'error': 'Course not found'}), 404

    data = request.get_json() or {}
    topic_ids = data.get('topic_ids') or []
    if not topic_ids:
        return jsonify({'error': 'topic_ids array is required'}), 400

    placeholders = ','.join(['%s'] * len(topic_ids))
    rows = fetchall(
        f'SELECT id FROM topics '
        f'WHERE id IN ({placeholders}) '
        f'AND course_id = %s AND user_id = %s',
        (*topic_ids, course_id, current_user_id)
    )
    if len(rows) != len(topic_ids):
        return jsonify({
            'error': 'Some topics not found in this course or unauthorized'
        }), 404

    for index, topic_id in enumerate(topic_ids):
        execute(
            'UPDATE topics SET order_index = %s, updated_at = NOW() '
            'WHERE id = %s',
            (index, topic_id)
        )
    return jsonify({'message': 'Topics reordered successfully'})