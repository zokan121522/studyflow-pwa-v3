# backend/routes/blocks.py
"""Blocks routes for StudyFlow PWA v3.

Sub-phase S3 ports the v2 content-blocks CRUD into v3 (which previously
returned blocks=[] from courses). Endpoints (v2-shape + v3-specific):
  GET    /courses/<cid>/topics/<tid>/blocks   → {blocks:[…]} ordered
  GET    /courses/<cid>/blocks                → {blocks:[…]} flat
  POST   /courses/<cid>/topics/<tid>/blocks   → created block
  POST   /courses/<cid>/blocks                → course-level block
  PUT    /courses/<cid>/blocks/<bid>          → updated block
  PATCH  /courses/<cid>/blocks/<bid>/done     → toggle done → {done:bool}
  DELETE /courses/<cid>/blocks/<bid>          → deleted
  PATCH  /courses/<cid>/blocks                → full-array reorder
  POST   /courses/blocks/<bid>/move           → move + reindex

All endpoints JSON-only. Single-user local mode uses @token_required
which falls back to LOCAL_USER_ID (1) when no JWT is present.
"""

from flask import Blueprint, request, jsonify

from database import execute, fetchone, fetchall
from routes.auth import token_required
from models import Block


bp = Blueprint('blocks', __name__)


# ============================== Helpers ==============================
# Whitelist of columns the front-end is allowed to write. Any other key in
# the payload is silently ignored — this prevents accidental schema
# leakage from v2's bulk-PATCH endpoint shape.
_WRITABLE_FIELDS = (
    'title', 'content', 'url', 'type', 'collapsed',
    'done', 'color', 'order_index',
)


def _serialize(row) -> dict:
    """Convert a raw blocks row dict → API dict."""
    return Block.from_row(row).to_dict()


def _own_course(course_id: int, user_id: int):
    return fetchone(
        'SELECT id FROM courses WHERE id = %s AND user_id = %s',
        (course_id, user_id)
    )


def _own_topic(topic_id: int, course_id: int, user_id: int):
    return fetchone(
        'SELECT id, course_id FROM topics '
        'WHERE id = %s AND course_id = %s AND user_id = %s',
        (topic_id, course_id, user_id)
    )


def _own_block(block_id: int, course_id: int, user_id: int):
    return fetchone(
        'SELECT * FROM blocks '
        'WHERE id = %s AND course_id = %s AND user_id = %s',
        (block_id, course_id, user_id)
    )


def _next_order(course_id: int, topic_id) -> int:
    """Return next order_index within a (course, topic) bucket."""
    row = fetchone(
        'SELECT COALESCE(MAX(order_index), -1) + 1 AS n '
        'FROM blocks WHERE course_id = %s '
        'AND (%s IS NULL OR topic_id IS NOT DISTINCT FROM %s)',
        (course_id, topic_id, topic_id)
    )
    return (row['n'] if row else 0) or 0


# ============================== List endpoints ==============================
@bp.get('/courses/<int:course_id>/topics/<int:topic_id>/blocks')
@token_required
def list_topic_blocks(current_user_id: int, course_id: int, topic_id: int):
    """List blocks belonging to a topic, ordered by order_index ASC."""
    if not _own_course(course_id, current_user_id):
        return jsonify({'error': 'Course not found'}), 404
    if not _own_topic(topic_id, course_id, current_user_id):
        return jsonify({'error': 'Topic not found in this course'}), 404
    rows = fetchall(
        'SELECT * FROM blocks WHERE course_id = %s '
        'AND topic_id = %s AND user_id = %s ORDER BY order_index ASC',
        (course_id, topic_id, current_user_id)
    )
    return jsonify({'blocks': [_serialize(r) for r in rows]})


@bp.get('/courses/<int:course_id>/blocks')
@token_required
def list_course_blocks(current_user_id: int, course_id: int):
    """List ALL blocks for a course (flat), ordered by topic then order."""
    if not _own_course(course_id, current_user_id):
        return jsonify({'error': 'Course not found'}), 404
    rows = fetchall(
        'SELECT * FROM blocks WHERE course_id = %s AND user_id = %s '
        'ORDER BY topic_id NULLS FIRST, order_index ASC',
        (course_id, current_user_id)
    )
    return jsonify({'blocks': [_serialize(r) for r in rows]})


# ============================== Create endpoints ==============================
@bp.post('/courses/<int:course_id>/topics/<int:topic_id>/blocks')
@token_required
def create_topic_block(
    current_user_id: int, course_id: int, topic_id: int
):
    """Create a block under a specific topic."""
    if not _own_course(course_id, current_user_id):
        return jsonify({'error': 'Course not found'}), 404
    if not _own_topic(topic_id, course_id, current_user_id):
        return jsonify({'error': 'Topic not found in this course'}), 404

    data = request.get_json() or {}
    payload = {k: data.get(k) for k in _WRITABLE_FIELDS if k in data}
    payload.setdefault('type', 'markdown')
    order_index = payload.get('order_index')
    if order_index is None:
        order_index = _next_order(course_id, topic_id)

    execute(
        '''INSERT INTO blocks
             (user_id, course_id, topic_id, type, title, content, url,
              order_index, color, collapsed, done)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)''',
        (current_user_id, course_id, topic_id,
         payload.get('type', 'markdown'),
         payload.get('title', ''),
         payload.get('content', ''),
         payload.get('url', ''),
         order_index,
         payload.get('color', ''),
         bool(payload.get('collapsed', False)),
         bool(payload.get('done', False)))
    )

    row = fetchone(
        'SELECT * FROM blocks WHERE course_id = %s AND user_id = %s '
        'ORDER BY id DESC LIMIT 1',
        (course_id, current_user_id)
    )
    return jsonify({'block': _serialize(row)}), 201


@bp.post('/courses/<int:course_id>/blocks')
@token_required
def create_course_block(current_user_id: int, course_id: int):
    """Create a course-level block (topic_id optional in body)."""
    if not _own_course(course_id, current_user_id):
        return jsonify({'error': 'Course not found'}), 404

    data = request.get_json() or {}
    payload = {k: data.get(k) for k in _WRITABLE_FIELDS if k in data}
    payload.setdefault('type', 'markdown')
    topic_id = data.get('topic_id')

    if topic_id and not _own_topic(topic_id, course_id, current_user_id):
        return jsonify({'error': 'Topic not found in this course'}), 404

    order_index = payload.get('order_index')
    if order_index is None:
        order_index = _next_order(course_id, topic_id)

    execute(
        '''INSERT INTO blocks
             (user_id, course_id, topic_id, type, title, content, url,
              order_index, color, collapsed, done)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)''',
        (current_user_id, course_id, topic_id,
         payload.get('type', 'markdown'),
         payload.get('title', ''),
         payload.get('content', ''),
         payload.get('url', ''),
         order_index,
         payload.get('color', ''),
         bool(payload.get('collapsed', False)),
         bool(payload.get('done', False)))
    )

    row = fetchone(
        'SELECT * FROM blocks WHERE course_id = %s AND user_id = %s '
        'ORDER BY id DESC LIMIT 1',
        (course_id, current_user_id)
    )
    return jsonify({'block': _serialize(row)}), 201


# ============================== Update / Done / Delete ==============================
@bp.put('/courses/<int:course_id>/blocks/<int:block_id>')
@token_required
def update_block(
    current_user_id: int, course_id: int, block_id: int
):
    """Partial update of writable block fields + optional order_index."""
    block = _own_block(block_id, course_id, current_user_id)
    if not block:
        return jsonify({'error': 'Block not found'}), 404

    data = request.get_json() or {}
    sets, params = [], []
    for f in _WRITABLE_FIELDS:
        if f in data:
            sets.append(f'{f} = %s')
            v = data[f]
            if f in ('done', 'collapsed'):
                v = bool(v)
            params.append(v)

    if not sets:
        return jsonify({'error': 'No fields to update'}), 400

    sets.append('updated_at = NOW()')
    params.append(block_id)
    execute(
        f'UPDATE blocks SET {", ".join(sets)} WHERE id = %s',
        tuple(params)
    )

    row = fetchone(
        'SELECT * FROM blocks WHERE id = %s AND user_id = %s',
        (block_id, current_user_id)
    )
    return jsonify({'block': _serialize(row)})


@bp.patch('/courses/<int:course_id>/blocks/<int:block_id>/done')
@token_required
def toggle_done(
    current_user_id: int, course_id: int, block_id: int
):
    """Flip done flag and return its new value."""
    block = _own_block(block_id, course_id, current_user_id)
    if not block:
        return jsonify({'error': 'Block not found'}), 404

    new_done = not bool(block['done'])
    execute(
        'UPDATE blocks SET done = %s, updated_at = NOW() '
        'WHERE id = %s AND user_id = %s',
        (new_done, block_id, current_user_id)
    )
    return jsonify({'done': new_done})


@bp.delete('/courses/<int:course_id>/blocks/<int:block_id>')
@token_required
def delete_block(
    current_user_id: int, course_id: int, block_id: int
):
    """Hard-delete a block."""
    result = execute(
        'DELETE FROM blocks WHERE id = %s AND course_id = %s '
        'AND user_id = %s',
        (block_id, course_id, current_user_id)
    )
    if result == 0:
        return jsonify({'error': 'Block not found'}), 404
    return jsonify({'message': 'Block deleted successfully'})


# ============================== Bulk reorder / Move ==============================
@bp.patch('/courses/<int:course_id>/blocks')
@token_required
def reorder_course_blocks(current_user_id: int, course_id: int):
    """Full-array reorder for a course.

    Body: {blocks:[id1, id2, id3]} — every id in the array becomes the
    sequential order_index 0..N-1. Any block in the course NOT in the
    list is left untouched (preserves course-level blocks).
    """
    if not _own_course(course_id, current_user_id):
        return jsonify({'error': 'Course not found'}), 404

    data = request.get_json() or {}
    ids = data.get('blocks') or []
    if not isinstance(ids, list) or not ids:
        return jsonify({'error': 'blocks array is required'}), 400

    placeholders = ','.join(['%s'] * len(ids))
    rows = fetchall(
        f'SELECT id FROM blocks '
        f'WHERE id IN ({placeholders}) '
        f'AND course_id = %s AND user_id = %s',
        (*ids, course_id, current_user_id)
    )
    if len(rows) != len(ids):
        return jsonify({
            'error': 'Some blocks not found in this course or unauthorized'
        }), 404

    for index, bid in enumerate(ids):
        execute(
            'UPDATE blocks SET order_index = %s, updated_at = NOW() '
            'WHERE id = %s',
            (index, bid)
        )
    return jsonify({'message': 'Blocks reordered successfully'})


@bp.post('/courses/blocks/<int:block_id>/move')
@token_required
def move_block(current_user_id: int, block_id: int):
    """Move a block to a different topic (and reindex within target).

    Body: {target_topic_id: int|null, index: int?}
    If `target_topic_id` is null, the block is moved to course-level.
    The destination topic is validated against the user + course.
    """
    block = fetchone(
        'SELECT * FROM blocks WHERE id = %s AND user_id = %s',
        (block_id, current_user_id)
    )
    if not block:
        return jsonify({'error': 'Block not found'}), 404

    data = request.get_json() or {}
    target_topic_id = data.get('target_topic_id')  # may be None

    if target_topic_id is not None:
        topic = fetchone(
            'SELECT id, course_id FROM topics '
            'WHERE id = %s AND user_id = %s',
            (target_topic_id, current_user_id)
        )
        if not topic:
            return jsonify({'error': 'Target topic not found'}), 404

    index = data.get('index')
    order_index = int(index) if index is not None else 0

    execute(
        'UPDATE blocks SET topic_id = %s, order_index = %s, '
        'updated_at = NOW() WHERE id = %s',
        (target_topic_id, order_index, block_id)
    )
    row = fetchone(
        'SELECT * FROM blocks WHERE id = %s', (block_id,)
    )
    return jsonify({'block': _serialize(row)})