r"""Regression tests for bug #1: "Test no hace el test".

The NotebookLM test pipeline generated correct questions all along — the 20
questions of the shipped test sit in `ai_tasks.result_content`. The defect was
where they landed: `_onTestSuccess` wrote the raw JSON into a `content` block,
so the user got a wall of JSON text instead of a test.

v3 only renders a test for `exercise` blocks, and that rendering
(`App.QuizEmbed`) reads the questions from `quiz_questions` keyed by
`block_id`. So the fix has two halves that must stay wired together:

1. Backend `POST /quiz/questions/bulk` imports a batch in one statement.
   `create_question` was one-question-per-round-trip, so a 20-question test
   cost 20 requests from the browser. `_prepare_bulk_rows` is the pure part
   and is unit-tested here.
2. Frontend `_onTestSuccess` must create an `exercise` block (not `content`),
   must not put the JSON in `content` (that is the stem the renderer paints),
   and must POST the questions to the bulk route.

These are static assertions on the source because the suite has no Flask test
client and no database; a syntax check cannot see any of this.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from routes.quiz import _prepare_bulk_rows  # noqa: E402

QUIZ_PY = ROOT / "backend" / "routes" / "quiz.py"
AI_JS = ROOT / "frontend" / "features" / "studyflow" / "ai.js"


def _route_src() -> str:
    return QUIZ_PY.read_text(encoding="utf-8")


def _on_test_success_src() -> str:
    """The body of _onTestSuccess, up to the public-exports marker."""
    src = AI_JS.read_text(encoding="utf-8")
    start = src.index("async function _onTestSuccess")
    return src[start:src.index("─── Public exports", start)]


# ─── _prepare_bulk_rows: mapping and validation ─────────────────────

def test_bulk_accepts_generator_key_correct():
    """The generator emits `correct`; the column is `correct_answer`."""
    rows, errors = _prepare_bulk_rows(
        {"block_id": 7, "questions": [
            {"question": "Q", "options": ["a", "b", "c"], "correct": 2},
        ]}, 1)
    assert not errors
    assert len(rows) == 1
    # (user, course, topic, block, question, options, correct_answer, expl, diff)
    assert rows[0][6] == 2


def test_bulk_accepts_canonical_correct_answer():
    rows, _ = _prepare_bulk_rows(
        {"block_id": 7, "questions": [
            {"question": "Q", "options": ["a", "b"], "correct_answer": 1},
        ]}, 1)
    assert rows[0][6] == 1


def test_bulk_keeps_explanation_and_default_difficulty():
    rows, _ = _prepare_bulk_rows(
        {"block_id": 7, "questions": [
            {"question": "Q", "options": ["a", "b"], "correct": 0,
             "explanation": "porque si"},
        ]}, 1)
    assert rows[0][7] == "porque si"
    assert rows[0][8] == "medium"
    rows, _ = _prepare_bulk_rows(
        {"block_id": 7, "questions": [
            {"question": "Q", "options": ["a", "b"], "correct": 0, "difficulty": "hard"},
        ]}, 1)
    assert rows[0][8] == "hard"


def test_bulk_options_are_stored_as_json_text():
    """options is a jsonb column, so it must be serialised for the driver."""
    rows, _ = _prepare_bulk_rows(
        {"block_id": 7, "questions": [
            {"question": "Q", "options": ["á", "b"], "correct": 0},
        ]}, 1)
    assert rows[0][5] == '["á", "b"]'


def test_bulk_keeps_valid_questions_when_one_is_malformed():
    """One bad question out of twenty must not cost the other nineteen."""
    rows, errors = _prepare_bulk_rows(
        {"block_id": 7, "questions": [
            {"question": "OK", "options": ["a", "b"], "correct": 0},
            {"question": "sin opciones", "options": ["solo una"], "correct": 0},
            {"question": "indice fuera de rango", "options": ["a", "b"], "correct": 9},
            {"question": "sin correct", "options": ["a", "b"]},
            "no soy un objeto",
            {"question": "OK2", "options": ["a", "b"], "correct": 1},
        ]}, 1)
    assert [r[4] for r in rows] == ["OK", "OK2"]
    assert [e["index"] for e in errors] == [1, 2, 3, 4]


def test_bulk_rejects_empty_batch():
    for data in ({}, {"block_id": 7}, {"block_id": 7, "questions": []},
                 {"questions": [{"question": "Q", "options": ["a", "b"], "correct": 0}]}):
        rows, errors = _prepare_bulk_rows(data, 1)
        assert rows == [] and errors, data


# ─── the route stays wired to the pure helper ───────────────────────

def test_bulk_route_is_registered_and_uses_helper():
    src = _route_src()
    assert "@bp.post('/quiz/questions/bulk')" in src
    assert "_prepare_bulk_rows(data, current_user_id)" in src
    # Batch in one statement: database.execute() takes a single params tuple,
    # so the naive fix (passing a list) would raise at runtime.
    assert "VALUES {placeholders}" in src
    # Regenerating a test must not duplicate the previous questions.
    assert "DELETE FROM quiz_questions" in src


# ─── _onTestSuccess must target the renderer that exists ───────────

def test_test_success_creates_exercise_not_content_block():
    src = _on_test_success_src()
    assert 'type: "exercise"' in src
    # The old bug: the raw JSON went into a content block nobody renders.
    assert 'type: "content"' not in src


def test_test_success_keeps_json_out_of_the_stem():
    """content is the stem QuizEmbed paints above the questions."""
    src = _on_test_success_src()
    assert re.search(r'content:\s*"",', src), "content should be an empty stem"


def test_test_success_imports_questions_via_bulk_route():
    src = _on_test_success_src()
    assert "/quiz/questions/bulk" in src
    assert "replace: true" in src
    assert "_addBlockAfterSource" in src


def test_test_success_reports_import_failure_honestly():
    """A created block with no questions renders an empty quiz — say so."""
    src = _on_test_success_src()
    assert "falló la importación" in src
