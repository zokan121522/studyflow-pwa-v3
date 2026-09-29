# backend/routes/quiz.py
"""Quiz routes for StudyFlow PWA v3."""

from flask import Blueprint, request, jsonify
import json

from database import execute, fetchone, fetchall
from routes.auth import token_required
from models import QuizQuestion, QuizResult, QuizError


bp = Blueprint('quiz', __name__)

# Rows per multi-row INSERT. database.execute() takes a single params tuple,
# so batching beats N round-trips; the chunk keeps the placeholder list sane.
_CHUNK = 50


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


@bp.get('/quiz/questions/manage')
@token_required
def list_questions_manage(current_user_id: int):
    """Owner's view of a block's questions, WITH the answer key.

    `list_questions` hides correct_answer/explanation because its consumer
    is the runner taking the test. The block editor is the other consumer
    and cannot prefill an edit form without it — v2's embed worked because
    it painted data-correct into the DOM. Kept as a separate route so the
    study-facing payload stays lean instead of growing a flag.
    """
    block_id = request.args.get('block_id', type=int)
    course_id = request.args.get('course_id', type=int)
    query = 'SELECT * FROM quiz_questions WHERE user_id = %s'
    params = [current_user_id]
    if block_id:
        query += ' AND block_id = %s'
        params.append(block_id)
    if course_id:
        query += ' AND course_id = %s'
        params.append(course_id)
    query += ' ORDER BY id'
    rows = fetchall(query, tuple(params))
    return jsonify({
        'questions': [QuizQuestion.from_row(r).to_dict() for r in rows]
    })


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
    """Submit a single answer (kept for the per-question flow).

    The paged runner grades a whole page through `/quiz/answers`; this
    single-answer route stays for one-off callers and now shares the same
    pool bookkeeping, so answering here feeds the failed pool too.
    """
    data = request.get_json() or {}
    graded, unknown = _record_page(current_user_id, [data])
    if unknown:
        return jsonify({'error': 'Question not found'}), 404
    q, picked = graded[0]
    return jsonify({
        'result': {'question_id': q.id, 'is_correct': picked['is_correct'],
                   'selected_answer': picked['selected_answer']},
        'correct_answer': q.correct_answer,
        'explanation': q.explanation,
    })


# ─── S5: paged test session ─────────────────────────────────────────
# v2 graded a page of 5 client-side from data-correct attributes and only
# persisted at the end, so abandoning the test lost the answers entirely.
# Here the server grades the page: one round-trip per page instead of one
# per question, and every attempt is durable the moment it is checked.
# The pure parts (_parse_answer_batch, _grade_attempts) are unit-tested
# because the suite runs without a database.

def _parse_answer_batch(data: dict):
    """Validate a batch of answers → (attempts, errors).

    One malformed entry is reported in `errors` instead of failing the
    page: a 20-question test should not collapse because a single
    time_taken_ms came back as a string.

    A repeated question_id is rejected too (first answer wins). The pool
    upsert cannot touch the same row twice in one statement — Postgres
    rejects that with "ON CONFLICT DO UPDATE command cannot affect row a
    second time" — and double-grading one question would also double-count
    it in the stats.
    """
    raw = data.get('answers')
    if not isinstance(raw, list) or not raw:
        return [], [{'index': None, 'error': 'a non-empty answers[] is required'}]

    attempts, errors, seen = [], [], set()
    for i, a in enumerate(raw):
        if not isinstance(a, dict):
            errors.append({'index': i, 'error': 'not an object'})
            continue
        qid, sel = a.get('question_id'), a.get('selected_answer')
        if qid is None or sel is None:
            errors.append({'index': i, 'error': 'question_id and selected_answer are required'})
            continue
        if not isinstance(qid, int) or isinstance(qid, bool):
            errors.append({'index': i, 'error': 'question_id must be an int'})
            continue
        if not isinstance(sel, int) or isinstance(sel, bool) or sel < 0:
            errors.append({'index': i, 'error': 'selected_answer must be a non-negative int'})
            continue
        if qid in seen:
            errors.append({'index': i, 'error': f'duplicate question_id {qid}'})
            continue
        seen.add(qid)
        t = a.get('time_taken_ms')
        if t is not None and (not isinstance(t, int) or isinstance(t, bool)):
            t = None
        attempts.append({'question_id': qid, 'selected_answer': sel, 'time_taken_ms': t})
    return attempts, errors


def _grade_attempts(questions: dict, attempts: list):
    """Match answers to questions and decide correctness.

    questions maps id → QuizQuestion. Returns (graded, unknown_ids) where
    graded is [(question, {selected_answer, is_correct, time_taken_ms})].
    Pure: no I/O, so the grading rule is testable on its own.
    """
    graded, unknown = [], []
    for a in attempts:
        q = questions.get(a['question_id'])
        if q is None:
            unknown.append(a['question_id'])
            continue
        graded.append((q, {
            'selected_answer': a['selected_answer'],
            'is_correct': a['selected_answer'] == q.correct_answer,
            'time_taken_ms': a.get('time_taken_ms'),
        }))
    return graded, unknown


def _load_questions_map(user_id: int, question_ids: list) -> dict:
    """Fetch the user's questions by id → {id: QuizQuestion}."""
    if not question_ids:
        return {}
    rows = fetchall(
        'SELECT * FROM quiz_questions WHERE user_id = %s AND id = ANY(%s)',
        (user_id, list(question_ids)),
    )
    return {r['id']: QuizQuestion.from_row(r) for r in rows}


def _log_results(user_id: int, graded: list) -> None:
    """Append one quiz_results row per attempt (one statement per chunk)."""
    if not graded:
        return
    cols = ('user_id, course_id, topic_id, block_id, question_id, '
            'selected_answer, is_correct, time_taken_ms')
    rows = [(user_id, q.course_id, q.topic_id, q.block_id, q.id,
             picked['selected_answer'], picked['is_correct'],
             picked['time_taken_ms']) for q, picked in graded]
    for start in range(0, len(rows), _CHUNK):
        chunk = rows[start:start + _CHUNK]
        placeholders = ', '.join(['(%s,%s,%s,%s,%s,%s,%s,%s)'] * len(chunk))
        execute(
            f'INSERT INTO quiz_results ({cols}) VALUES {placeholders}',
            tuple(v for row in chunk for v in row),
        )


def _sync_pool(user_id: int, graded: list) -> None:
    """Move the failed pool: wrong opens/reopens an entry, right resolves it.

    v2 appended a duplicate row per wrong attempt and only deleted the
    entry when the *practice* run got it right, so a question you aces in
    a normal test stayed in your to-review list. Here the rule is uniform
    and centralised, and the partial unique index keeps one open row per
    question while the closed rows preserve the history.
    """
    wrong = [(q, picked) for q, picked in graded if not picked['is_correct']]
    if wrong:
        cols = ('user_id, question_id, course_id, topic_id, block_id, '
                'wrong_count, last_wrong_answer, last_failed_at')
        for start in range(0, len(wrong), _CHUNK):
            chunk = wrong[start:start + _CHUNK]
            placeholders = ', '.join(['(%s,%s,%s,%s,%s,1,%s,NOW())'] * len(chunk))
            params = []
            for q, picked in chunk:
                params.extend([user_id, q.id, q.course_id, q.topic_id, q.block_id,
                               picked['selected_answer']])
            execute(
                f'''INSERT INTO quiz_errors ({cols}) VALUES {placeholders}
                    ON CONFLICT (user_id, question_id) WHERE resolved_at IS NULL
                    DO UPDATE SET wrong_count = quiz_errors.wrong_count + 1,
                                  last_wrong_answer = EXCLUDED.last_wrong_answer,
                                  last_failed_at = NOW()''',
                tuple(params),
            )
    right_ids = [q.id for q, picked in graded if picked['is_correct']]
    if right_ids:
        execute(
            '''UPDATE quiz_errors SET resolved_at = NOW()
               WHERE user_id = %s AND resolved_at IS NULL
                 AND question_id = ANY(%s)''',
            (user_id, right_ids),
        )


def _record_page(user_id: int, raw_attempts: list):
    """Grade a page, log it, and sync the pool. Returns (graded, unknown)."""
    questions = _load_questions_map(user_id, [a.get('question_id') for a in raw_attempts])
    graded, unknown = _grade_attempts(questions, raw_attempts)
    _log_results(user_id, graded)
    _sync_pool(user_id, graded)
    return graded, unknown


@bp.post('/quiz/answers')
@token_required
def submit_answers(current_user_id: int):
    """Grade a page of answers in one round-trip and sync the failed pool.

    Body: {answers: [{question_id, selected_answer, time_taken_ms?}]}
    Returns per-question feedback ({is_correct, correct_answer, explanation})
    so the runner can paint the page's results table without a second call.
    """
    data = request.get_json() or {}
    attempts, errors = _parse_answer_batch(data)
    if not attempts:
        return jsonify({'error': 'no valid answers', 'invalid': errors}), 400

    graded, unknown = _record_page(current_user_id, attempts)
    results = [{
        'question_id': q.id,
        'selected_answer': picked['selected_answer'],
        'is_correct': picked['is_correct'],
        'correct_answer': q.correct_answer,
        'explanation': q.explanation,
    } for q, picked in graded]
    return jsonify({
        'results': results,
        'invalid': errors,
        'unknown': unknown,
        'score': {'ok': sum(1 for r in results if r['is_correct']),
                  'ko': sum(1 for r in results if not r['is_correct'])},
    })


# ─── S5: failed pool ───────────────────────────────────────────────

@bp.get('/quiz/errors')
@token_required
def list_errors(current_user_id: int):
    """List the failed pool, with the question text needed to render a row.

    scope=open (default) is the to-review list; `resolved` shows what has
    already been aced, `all` shows both. Includes the question's options
    and correct answer so the UI can build a practice session straight
    from this payload — it is the user's own local study app, the same
    data v2 shipped to the client as data-correct.
    """
    scope = request.args.get('scope', 'open')
    course_id = request.args.get('course_id', type=int)
    block_id = request.args.get('block_id', type=int)
    limit = request.args.get('limit', type=int, default=200)

    query = '''SELECT e.*, q.question, q.options, q.correct_answer, q.explanation,
                      b.title AS block_title, c.title AS course_title,
                      t.title AS topic_title
               FROM quiz_errors e
               JOIN quiz_questions q ON q.id = e.question_id
               LEFT JOIN blocks b ON b.id = e.block_id
               LEFT JOIN courses c ON c.id = e.course_id
               LEFT JOIN topics t ON t.id = e.topic_id
               WHERE e.user_id = %s'''
    params = [current_user_id]
    if scope == 'open':
        query += ' AND e.resolved_at IS NULL'
    elif scope == 'resolved':
        query += ' AND e.resolved_at IS NOT NULL'
    if course_id:
        query += ' AND e.course_id = %s'
        params.append(course_id)
    if block_id:
        query += ' AND e.block_id = %s'
        params.append(block_id)
    query += ' ORDER BY e.last_failed_at DESC LIMIT %s'
    params.append(limit)

    rows = fetchall(query, tuple(params))
    errors = [QuizError.from_row(r).to_dict() for r in rows]
    return jsonify({
        'errors': errors,
        'open_count': fetchone(
            'SELECT COUNT(*) AS c FROM quiz_errors '
            'WHERE user_id = %s AND resolved_at IS NULL', (current_user_id,)
        )['c'],
    })


@bp.post('/quiz/errors/resolve')
@token_required
def resolve_errors(current_user_id: int):
    """Take questions out of the failed pool.

    Body: {question_ids: [...]} or {all: true} to clear the whole pool.
    Closes the entry instead of deleting it, so the badge keeps the count
    of what was missed over time.
    """
    data = request.get_json() or {}
    if data.get('all'):
        count = execute(
            'UPDATE quiz_errors SET resolved_at = NOW() '
            'WHERE user_id = %s AND resolved_at IS NULL', (current_user_id,))
        return jsonify({'resolved': count})
    ids = data.get('question_ids')
    if not isinstance(ids, list) or not ids:
        return jsonify({'error': 'question_ids[] or all:true is required'}), 400
    count = execute(
        '''UPDATE quiz_errors SET resolved_at = NOW()
           WHERE user_id = %s AND resolved_at IS NULL AND question_id = ANY(%s)''',
        (current_user_id, [int(i) for i in ids]),
    )
    return jsonify({'resolved': count})


@bp.post('/quiz/errors/reopen')
@token_required
def reopen_errors(current_user_id: int):
    """Put resolved questions back in the pool (undo, v2 had no equivalent)."""
    data = request.get_json() or {}
    ids = data.get('question_ids')
    if not isinstance(ids, list) or not ids:
        return jsonify({'error': 'question_ids[] is required'}), 400
    count = execute(
        '''UPDATE quiz_errors SET resolved_at = NULL, last_failed_at = NOW()
           WHERE user_id = %s AND question_id = ANY(%s)
             AND resolved_at IS NOT NULL''',
        (current_user_id, [int(i) for i in ids]),
    )
    return jsonify({'reopened': count})


@bp.post('/quiz/errors/clear')
@token_required
def clear_errors(current_user_id: int):
    """Hard-delete the pool (v2 `errors/reset` parity). Irreversible."""
    data = request.get_json() or {}
    if data.get('all'):
        count = execute('DELETE FROM quiz_errors WHERE user_id = %s', (current_user_id,))
        return jsonify({'deleted': count})
    ids = data.get('question_ids')
    if not isinstance(ids, list) or not ids:
        return jsonify({'error': 'question_ids[] or all:true is required'}), 400
    count = execute(
        'DELETE FROM quiz_errors WHERE user_id = %s AND question_id = ANY(%s)',
        (current_user_id, [int(i) for i in ids]),
    )
    return jsonify({'deleted': count})


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
    """Quiz statistics for the current user.

    Two deliberate deviations from v2's `quiz_summaries`:

    1. Aggregated on the fly from `quiz_results` instead of a counters
       table. v2 UPSERTed cumulative counters per topic, so re-attempting
       a test inflated the totals and deleting a block orphaned the row;
       the log is already there and is the truth.
    2. Grouped by block, not by the "Course > Block" text slug, so renaming
       a block no longer orphans the history (Engram #4064).

    `by_topic` and the headline counters are kept as-is for compatibility.
    """
    course_id = request.args.get('course_id', type=int)

    # Two predicates, not one: the by_block query joins blocks/courses/topics,
    # and all three carry their own user_id, so an unqualified `user_id` there
    # is ambiguous. `where` is the bare predicate (no FROM — the callers add
    # it, or the query already has FROM for its own joins).
    where = 'WHERE user_id = %s'
    where_r = 'WHERE r.user_id = %s'
    params = [current_user_id]
    if course_id:
        where += ' AND course_id = %s'
        where_r += ' AND r.course_id = %s'
        params.append(course_id)

    total_count = fetchone(
        f'SELECT COUNT(*) AS count FROM quiz_results {where}', tuple(params))['count']
    correct_count = fetchone(
        f'SELECT COUNT(*) AS count FROM quiz_results {where} AND is_correct = true',
        tuple(params))['count']

    topic_stats = fetchall(
        f'''SELECT topic_id, COUNT(*) as total,
            SUM(CASE WHEN is_correct THEN 1 ELSE 0 END) as correct
            FROM quiz_results {where} AND topic_id IS NOT NULL
            GROUP BY topic_id''',
        tuple(params),
    )

    # "Resumen por Card": one row per exercise block, with the still-open
    # failed questions for that block so the UI can flag what needs work.
    by_block = fetchall(
        f'''SELECT r.block_id, r.course_id, r.topic_id,
                   b.title AS block_title, c.title AS course_title,
                   t.title AS topic_title,
                   COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE r.is_correct) AS ok,
                   COUNT(*) - COUNT(*) FILTER (WHERE r.is_correct) AS ko,
                   (SELECT COUNT(*) FROM quiz_errors e
                     WHERE e.user_id = r.user_id AND e.block_id = r.block_id
                       AND e.resolved_at IS NULL) AS open_errors
            FROM quiz_results r
            LEFT JOIN blocks b ON b.id = r.block_id
            LEFT JOIN courses c ON c.id = r.course_id
            LEFT JOIN topics t ON t.id = r.topic_id
            {where_r} AND r.block_id IS NOT NULL
            GROUP BY r.block_id, r.course_id, r.topic_id,
                     b.title, c.title, t.title, r.user_id
            ORDER BY r.block_id''',
        tuple(params),
    )

    open_errors = fetchone(
        'SELECT COUNT(*) AS c FROM quiz_errors '
        'WHERE user_id = %s AND resolved_at IS NULL', (current_user_id,))['c']

    for row in by_block:
        t = row['total'] or 0
        row['accuracy'] = round((row['ok'] / t) * 100, 1) if t else 0

    return jsonify({
        'total_answered': total_count,
        'correct': correct_count,
        'ko': total_count - correct_count,
        'accuracy': round(correct_count / total_count * 100, 1) if total_count > 0 else 0,
        'by_topic': topic_stats,
        'by_block': by_block,
        'open_errors': open_errors,
    })
