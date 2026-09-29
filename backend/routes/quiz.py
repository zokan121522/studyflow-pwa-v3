# backend/routes/quiz.py
"""Quiz routes for StudyFlow PWA v3."""

from flask import Blueprint, request, jsonify
import json

from database import execute, fetchone, fetchall
from routes.auth import token_required
from models import QuizQuestion, QuizResult


bp = Blueprint('quiz', __name__)


@bp.get('/quiz/questions')
@token_required
def list_questions(current_user_id: int):
    """Get quiz questions, optionally filtered by course/topic/block."""
    course_id = request.args.get('course_id', type=int)
    topic_id = request.args.get('topic_id', type=int)
    block_id = request.args.get('block_id', type=int)
    limit = request.args.get('limit', type=int, default=50)
    offset = request.args.get('offset', type=int, default=0)

    query = 'SELECT * FROM quiz_questions WHERE user_id = %s'
    params = [current_user_id]

    if course_id:
        query += ' AND course_id = %s'
        params.append(course_id)
    if topic_id:
        query += ' AND topic_id = %s'
        params.append(topic_id)
    if block_id:
        query += ' AND block_id = %s'
        params.append(block_id)

    query += ' ORDER BY created_at DESC LIMIT %s OFFSET %s'
    params.extend([limit, offset])

    rows = fetchall(query, tuple(params))
    questions = []
    for r in rows:
        q = QuizQuestion.from_row(r).to_dict()
        # Don't expose correct_answer in list view
        q.pop('correct_answer', None)
        q.pop('explanation', None)
        questions.append(q)

    return jsonify({'questions': questions})


@bp.post('/quiz/questions')
@token_required
def create_question(current_user_id: int):
    """Create a quiz question (optionally tied to a block for issue #10)."""
    data = request.get_json() or {}

    question = data.get('question')
    options = data.get('options')
    correct_answer = data.get('correct_answer')
    if not question or not isinstance(options, list) or len(options) < 2:
        return jsonify({'error': 'question and options (>=2) are required'}), 400
    if correct_answer is None or not (0 <= correct_answer < len(options)):
        return jsonify({'error': 'correct_answer out of range'}), 400

    row = fetchone(
        '''INSERT INTO quiz_questions
             (user_id, course_id, topic_id, block_id, question, options,
              correct_answer, explanation, difficulty)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
           RETURNING *''',
        (
            current_user_id,
            data.get('course_id'),
            data.get('topic_id'),
            data.get('block_id'),
            question,
            json.dumps(options),  # jsonb column: pass a JSON string
            correct_answer,
            data.get('explanation'),
            data.get('difficulty', 'medium'),
        )
    )
    # fetchone() opens a read transaction that ROLLS BACK on close —
    # use execute() instead so INSERT is committed, then re-read.
    execute(
        '''INSERT INTO quiz_questions
             (user_id, course_id, topic_id, block_id, question, options,
              correct_answer, explanation, difficulty)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)''',
        (
            current_user_id,
            data.get('course_id'),
            data.get('topic_id'),
            data.get('block_id'),
            question,
            json.dumps(options),
            correct_answer,
            data.get('explanation'),
            data.get('difficulty', 'medium'),
        )
    )
    row = fetchone(
        'SELECT * FROM quiz_questions WHERE user_id = %s '
        'ORDER BY id DESC LIMIT 1',
        (current_user_id,)
    )
    return jsonify({'question': QuizQuestion.from_row(row).to_dict()}), 201


def _prepare_bulk_rows(data: dict, user_id: int):
    """Validate a batch of questions and build quiz_questions rows.

    Returns (rows, errors). A malformed question is reported in `errors`
    instead of failing the whole import, so one bad question out of twenty
    does not cost the user the other nineteen.

    Both key spellings for the correct option are accepted: the NotebookLM
    generator emits `correct`, the column is `correct_answer`.
    """
    block_id = data.get('block_id')
    questions = data.get('questions')
    if block_id is None or not isinstance(questions, list) or not questions:
        return [], [{'index': None, 'error': 'block_id and a non-empty questions[] are required'}]

    rows, errors = [], []
    for i, q in enumerate(questions):
        if not isinstance(q, dict):
            errors.append({'index': i, 'error': 'not an object'})
            continue
        text = (q.get('question') or '').strip()
        options = q.get('options')
        correct = q.get('correct_answer', q.get('correct'))
        if not text or not isinstance(options, list) or len(options) < 2:
            errors.append({'index': i, 'error': 'question and options (>=2) are required'})
            continue
        if correct is None or not (0 <= int(correct) < len(options)):
            errors.append({'index': i, 'error': 'correct answer out of range'})
            continue
        rows.append((
            user_id, data.get('course_id'), data.get('topic_id'), block_id,
            text, json.dumps(options, ensure_ascii=False), int(correct),
            q.get('explanation'), q.get('difficulty', 'medium'),
        ))
    return rows, errors


@bp.post('/quiz/questions/bulk')
@token_required
def bulk_create_questions(current_user_id: int):
    """Import a batch of questions for one block (AI-generated tests).

    create_question handles a single question, so importing a 20-question
    NotebookLM test meant 20 round-trips from the browser. Replaces the
    block's questions in one call, which is what a regenerated test needs.
    """
    data = request.get_json() or {}
    rows, errors = _prepare_bulk_rows(data, current_user_id)
    if not rows:
        return jsonify({'error': 'no valid questions', 'invalid': errors}), 400
    block_id = data.get('block_id')

    # A regenerated test replaces the previous one, otherwise every run
    # duplicates the same questions on the block.
    if data.get('replace'):
        execute('DELETE FROM quiz_questions WHERE user_id = %s AND block_id = %s',
                (current_user_id, block_id))

    # database.execute() runs a single statement with one params tuple, so
    # batch with a multi-row INSERT instead of N round-trips. Chunked to keep
    # the placeholder list bounded for large batches.
    inserted = 0
    CHUNK = 50
    cols = ('user_id, course_id, topic_id, block_id, question, options, '
            'correct_answer, explanation, difficulty')
    for start in range(0, len(rows), CHUNK):
        chunk = rows[start:start + CHUNK]
        placeholders = ', '.join(['(%s,%s,%s,%s,%s,%s,%s,%s,%s)'] * len(chunk))
        params = tuple(v for row in chunk for v in row)
        inserted += execute(
            f'INSERT INTO quiz_questions ({cols}) VALUES {placeholders}', params)
    return jsonify({'inserted': inserted, 'invalid': errors}), 201


@bp.put('/quiz/questions/<int:question_id>')
@token_required
def update_question(current_user_id: int, question_id: int):
    """Update an existing quiz question."""
    data = request.get_json() or {}
    q_row = fetchone(
        'SELECT * FROM quiz_questions WHERE id = %s AND user_id = %s',
        (question_id, current_user_id)
    )
    if not q_row:
        return jsonify({'error': 'Question not found'}), 404

    options = data.get('options', q_row['options'])
    if isinstance(options, str):
        options = __import__('json').loads(options)
    correct_answer = data.get('correct_answer', q_row['correct_answer'])
    if not isinstance(options, list) or len(options) < 2:
        return jsonify({'error': 'options must be a list (>=2)'}), 400
    if not (0 <= correct_answer < len(options)):
        return jsonify({'error': 'correct_answer out of range'}), 400

    # Same commit caveat as create: fetchone() rolls back on close —
    # run UPDATE via execute() (commits), then re-read.
    execute(
        '''UPDATE quiz_questions SET
             course_id = COALESCE(%s, course_id),
             topic_id = %s,
             block_id = COALESCE(%s, block_id),
             question = %s,
             options = %s,
             correct_answer = %s,
             explanation = %s,
             difficulty = %s
           WHERE id = %s AND user_id = %s''',
        (
            data.get('course_id'),
            data.get('topic_id'),
            data.get('block_id'),
            data.get('question', q_row['question']),
            json.dumps(options),  # jsonb column: pass a JSON string
            correct_answer,
            data.get('explanation', q_row.get('explanation')),
            data.get('difficulty', q_row.get('difficulty', 'medium')),
            question_id,
            current_user_id,
        )
    )
    row = fetchone(
        'SELECT * FROM quiz_questions WHERE id = %s AND user_id = %s',
        (question_id, current_user_id)
    )
    return jsonify({'question': QuizQuestion.from_row(row).to_dict()})


@bp.delete('/quiz/questions/<int:question_id>')
@token_required
def delete_question(current_user_id: int, question_id: int):
    """Delete a quiz question (cascades quiz_results via FK)."""
    count = execute(
        'DELETE FROM quiz_questions WHERE id = %s AND user_id = %s',
        (question_id, current_user_id)
    )
    if not count:
        return jsonify({'error': 'Question not found'}), 404
    return jsonify({'deleted': True})


@bp.post('/quiz/answer')
@token_required
def submit_answer(current_user_id: int):
    """Submit an answer to a quiz question."""
    data = request.get_json() or {}

    question_id = data.get('question_id')
    selected_answer = data.get('selected_answer')
    time_taken_ms = data.get('time_taken_ms')

    if question_id is None or selected_answer is None:
        return jsonify({'error': 'question_id and selected_answer are required'}), 400

    # Get question
    q_row = fetchone('SELECT * FROM quiz_questions WHERE id = %s AND user_id = %s', (question_id, current_user_id))
    if not q_row:
        return jsonify({'error': 'Question not found'}), 404

    question = QuizQuestion.from_row(q_row)
    is_correct = selected_answer == question.correct_answer

    # Save result
    execute(
        '''INSERT INTO quiz_results (user_id, course_id, topic_id, block_id, question_id, selected_answer, is_correct, time_taken_ms)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)''',
        (current_user_id, question.course_id, question.topic_id, question.block_id,
         question_id, selected_answer, is_correct, time_taken_ms)
    )

    result_row = fetchone(
        'SELECT * FROM quiz_results WHERE user_id = %s ORDER BY created_at DESC LIMIT 1',
        (current_user_id,)
    )

    return jsonify({
        'result': QuizResult.from_row(result_row).to_dict(),
        'correct_answer': question.correct_answer,
        'explanation': question.explanation
    })


@bp.get('/quiz/results')
@token_required
def get_results(current_user_id: int):
    """Get quiz results for the current user."""
    course_id = request.args.get('course_id', type=int)
    topic_id = request.args.get('topic_id', type=int)
    block_id = request.args.get('block_id', type=int)
    limit = request.args.get('limit', type=int, default=100)

    query = 'SELECT * FROM quiz_results WHERE user_id = %s'
    params = [current_user_id]

    if course_id:
        query += ' AND course_id = %s'
        params.append(course_id)
    if topic_id:
        query += ' AND topic_id = %s'
        params.append(topic_id)
    if block_id:
        query += ' AND block_id = %s'
        params.append(block_id)

    query += ' ORDER BY created_at DESC LIMIT %s'
    params.append(limit)

    rows = fetchall(query, tuple(params))
    return jsonify({'results': [QuizResult.from_row(r).to_dict() for r in rows]})


@bp.get('/quiz/stats')
@token_required
def get_stats(current_user_id: int):
    """Get quiz statistics for the current user."""
    course_id = request.args.get('course_id', type=int)

    base_query = 'FROM quiz_results WHERE user_id = %s'
    params = [current_user_id]

    if course_id:
        base_query += ' AND course_id = %s'
        params.append(course_id)

    # Total answered
    total = fetchone(f'SELECT COUNT(*) as count {base_query}', tuple(params))
    total_count = total['count'] if total else 0

    # Correct answers
    correct = fetchone(f'SELECT COUNT(*) as count {base_query} AND is_correct = true', tuple(params))
    correct_count = correct['count'] if correct else 0

    # By topic
    topic_stats = fetchall(
        f'''SELECT topic_id, COUNT(*) as total, 
            SUM(CASE WHEN is_correct THEN 1 ELSE 0 END) as correct
            {base_query} AND topic_id IS NOT NULL
            GROUP BY topic_id''',
        tuple(params)
    )

    return jsonify({
        'total_answered': total_count,
        'correct': correct_count,
        'accuracy': round(correct_count / total_count * 100, 1) if total_count > 0 else 0,
        'by_topic': topic_stats
    })