r"""Tests for the S5 quiz ecosystem (phases F1-F4): paged session, final
results table, failed pool and per-block summary.

v2 had all of this in `quiz.js` + a sidecar keyed by the "Course > Block"
text slug. v3 rebuilt it on normalised ids because that text key is what
orphaned 12 quiz_errors rows during the v2→v3 migration (Engram #4064).

The suite has no Flask client and no database, so: the pure grading
helpers are imported and exercised directly, the SQL invariants are
asserted on the source, and the frontend wiring is asserted statically
(as `test_nb_test_quiz.py` does). The paging arithmetic is checked for
real by running the runner's `_create` under node.
"""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from routes.quiz import _parse_answer_batch, _grade_attempts  # noqa: E402
from models import QuizQuestion  # noqa: E402

QUIZ_PY = ROOT / "backend" / "routes" / "quiz.py"
DB_PY = ROOT / "backend" / "database.py"
FEAT = ROOT / "frontend" / "features" / "quiz"
RUNNER_JS = FEAT / "quiz-runner.js"
POOL_JS = FEAT / "quiz-pool.js"
SUMMARY_JS = FEAT / "quiz-summary.js"
CENTER_JS = FEAT / "quiz-center.js"
API_JS = FEAT / "quiz-api.js"
EMBED_JS = ROOT / "frontend" / "features" / "studyflow" / "quiz-embed.js"
COURSES_JS = ROOT / "frontend" / "features" / "studyflow" / "courses.js"
INDEX_HTML = ROOT / "frontend" / "index.html"


def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _flat(src: str) -> str:
    """Collapse whitespace AND Python's adjacent-string-literal joins.

    `_POST_INDEXES` spells the DDL as `"...open " "ON quiz_errors(...)"`, so
    whitespace normalisation alone still leaves a `" "` between the halves.
    Order matters: joining the literals leaves the literal's own trailing
    space plus the join, so all runs must be collapsed afterwards.
    """
    return re.sub(r"\s+", " ", re.sub(r'"\s+"', " ", src))


# ─── _parse_answer_batch ────────────────────────────────────────

def test_answer_batch_requires_a_list():
    for data in ({}, {"answers": []}, {"answers": "nope"}, {"answers": {}}):
        attempts, errors = _parse_answer_batch(data)
        assert attempts == [] and errors, data


def test_answer_batch_accepts_a_page():
    attempts, errors = _parse_answer_batch({"answers": [
        {"question_id": 1, "selected_answer": 0},
        {"question_id": 2, "selected_answer": 3, "time_taken_ms": 1500},
    ]})
    assert errors == []
    assert [a["question_id"] for a in attempts] == [1, 2]
    assert attempts[1]["time_taken_ms"] == 1500


def test_answer_batch_rejects_malformed_entries():
    """One bad answer must not cost the rest of the page."""
    attempts, errors = _parse_answer_batch({"answers": [
        {"question_id": 1, "selected_answer": 0},
        {"question_id": "2", "selected_answer": 0},   # id must be int
        {"question_id": 3},                            # no answer chosen
        {"question_id": 4, "selected_answer": -1},      # impossible index
        {"selected_answer": 0},                        # no id
        "not an object",
    ]})
    assert [a["question_id"] for a in attempts] == [1]
    assert [e["index"] for e in errors] == [1, 2, 3, 4, 5]


def test_answer_batch_treats_booleans_as_invalid_ints():
    """True == 1 in Python; accepting it would log a phantom answer."""
    attempts, errors = _parse_answer_batch({"answers": [
        {"question_id": True, "selected_answer": 0},
        {"question_id": 2, "selected_answer": True},
    ]})
    assert attempts == []
    assert len(errors) == 2


def test_answer_batch_drops_unusable_time_taken():
    attempts, _ = _parse_answer_batch({"answers": [
        {"question_id": 1, "selected_answer": 0, "time_taken_ms": "rápido"},
    ]})
    assert attempts[0]["time_taken_ms"] is None


def test_answer_batch_rejects_a_repeated_question():
    """The pool upsert cannot touch one row twice in a statement, and a
    double grade would double-count the question in the stats."""
    attempts, errors = _parse_answer_batch({"answers": [
        {"question_id": 7, "selected_answer": 0},
        {"question_id": 7, "selected_answer": 3},
    ]})
    assert [a["selected_answer"] for a in attempts] == [0]   # first wins
    assert [e["index"] for e in errors] == [1]
    assert "duplicate" in errors[0]["error"]


# ─── _grade_attempts ───────────────────────────────────────────

def _q(qid, correct):
    return QuizQuestion(id=qid, user_id=1, course_id=1, question=f"Q{qid}",
                        options=["a", "b", "c", "d"], correct_answer=correct)


def test_grading_marks_correct_and_wrong():
    questions = {1: _q(1, 2), 2: _q(2, 0)}
    graded, unknown = _grade_attempts(questions, [
        {"question_id": 1, "selected_answer": 2},
        {"question_id": 2, "selected_answer": 1},
    ])
    assert unknown == []
    assert [(q.id, p["is_correct"]) for q, p in graded] == [(1, True), (2, False)]


def test_grading_isolates_unknown_questions():
    """A question deleted mid-test must not abort the page."""
    graded, unknown = _grade_attempts({1: _q(1, 0)}, [
        {"question_id": 1, "selected_answer": 0},
        {"question_id": 404, "selected_answer": 0},
    ])
    assert unknown == [404]
    assert len(graded) == 1


def test_grading_carries_timing_through():
    graded, _ = _grade_attempts({1: _q(1, 0)}, [
        {"question_id": 1, "selected_answer": 0, "time_taken_ms": 900},
    ])
    assert graded[0][1]["time_taken_ms"] == 900


# ─── the pool is anchored to question_id, not to text ──────────

def test_pool_table_is_keyed_by_question_id():
    """The whole reason #4064 lost data: v2 had no block_id/course_id."""
    ddl = _src(DB_PY)
    assert "CREATE TABLE IF NOT EXISTS quiz_errors" in ddl
    # Cut on the closing quote-string, not on ")" — SERIAL PRIMARY KEY
    # would truncate the block at the first paren.
    block = ddl.split("CREATE TABLE IF NOT EXISTS quiz_errors")[1].split('"""')[0]
    assert "question_id INTEGER NOT NULL REFERENCES quiz_questions(id)" in block
    assert "resolved_at TIMESTAMP WITH TIME ZONE" in block
    assert "wrong_count" in block
    # No text slugs: that is the v2 defect.
    for banned in ("topic TEXT", "tpl TEXT", "tpl VARCHAR"):
        assert banned not in block


def test_pool_allows_only_one_open_row_per_question():
    """v2 appended a duplicate row per wrong attempt."""
    flat = _flat(_src(DB_PY))
    assert ("CREATE UNIQUE INDEX IF NOT EXISTS uq_quiz_errors_open "
            "ON quiz_errors(user_id, question_id) WHERE resolved_at IS NULL") in flat


def test_wrong_answer_opens_the_pool_and_right_one_resolves_it():
    src = _src(QUIZ_PY)
    sync = src.split("def _sync_pool")[1].split("def _record_page")[0]
    assert "ON CONFLICT (user_id, question_id) WHERE resolved_at IS NULL" in sync
    # The predicate must be repeated or the upsert cannot find the index.
    assert "quiz_errors.wrong_count + 1" in sync
    assert "UPDATE quiz_errors SET resolved_at = NOW()" in sync
    assert "AND resolved_at IS NULL" in sync


def test_every_attempt_is_persisted_before_the_page_advances():
    """v2 only saved at the end, so abandoning mid-test lost the run."""
    src = _src(QUIZ_PY)
    record = src.split("def _record_page")[1].split("@bp.post('/quiz/answers')")[0]
    assert "_log_results" in record and "_sync_pool" in record


def test_batch_route_grades_a_page_in_one_call():
    src = _src(QUIZ_PY)
    assert "@bp.post('/quiz/answers')" in src
    handler = src.split("@bp.post('/quiz/answers')")[1].split("# ─── S5: failed pool")[0]
    assert "_record_page(current_user_id, attempts)" in handler
    assert "'correct_answer': q.correct_answer" in handler
    assert "'score'" in handler


def test_single_answer_route_still_works_and_keeps_the_pool_consistent():
    assert "@bp.post('/quiz/answer')" in _src(QUIZ_PY)
    src = _src(QUIZ_PY)
    handler = src.split("def submit_answer")[1].split("# ─── S5: paged test session")[0]
    assert "_record_page" in handler


def test_pool_routes_exist():
    src = _src(QUIZ_PY)
    for route in ("@bp.get('/quiz/errors')",
                  "@bp.post('/quiz/errors/resolve')",
                  "@bp.post('/quiz/errors/reopen')",
                  "@bp.post('/quiz/errors/clear')"):
        assert route in src, route


def test_resolved_questions_can_come_back():
    """v2 deleted the row: the badge lost its history and undo was impossible."""
    src = _src(QUIZ_PY)
    assert "SET resolved_at = NULL" in src


def test_owner_route_exposes_the_answer_key():
    """The edit form cannot prefill without it, and the study route hides it."""
    src = _src(QUIZ_PY)
    assert "@bp.get('/quiz/questions/manage')" in src
    manage = src.split("def list_questions_manage")[1].split("@bp.post('/quiz/questions')")[0]
    assert "correct_answer" in manage
    # ...and the study-facing list must keep hiding it.
    listing = src.split("def list_questions")[1].split("@bp.get('/quiz/questions/manage')")[0]
    assert "q.pop('correct_answer', None)" in listing


# ─── stats: grouped by block, with the missing ko/open_errors ───

def test_stats_report_ko_and_open_errors():
    src = _src(QUIZ_PY)
    assert "'ko': total_count - correct_count" in src
    assert "'open_errors': open_errors" in src
    assert "'by_block': by_block" in src


def test_stats_group_by_block_id_not_by_text():
    src = _src(QUIZ_PY)
    assert "GROUP BY r.block_id" in src
    assert "b.title AS block_title" in src


def test_stats_predicates_carry_no_from_clause():
    """A shared 'FROM quiz_results WHERE ...' fragment spliced a second
    FROM into by_block, which already had FROM + three joins."""
    stats = _src(QUIZ_PY).split("def get_stats")[1]
    assert "where = 'WHERE user_id = %s'" in stats
    assert "FROM quiz_results WHERE user_id = %s'" not in stats
    joined = stats.split("by_block = fetchall")[1].split("open_errors = fetchone")[0]
    assert joined.count("FROM quiz_results") == 1


def test_stats_by_block_qualifies_user_id():
    """blocks, courses and topics each own a user_id, so the joined
    query needs the aliased predicate or Postgres calls it ambiguous."""
    stats = _src(QUIZ_PY).split("def get_stats")[1]
    assert "where_r = 'WHERE r.user_id = %s'" in stats
    assert "{where_r} AND r.block_id IS NOT NULL" in stats
    joined = stats.split("by_block = fetchall")[1].split("open_errors = fetchone")[0]
    assert "{where} " not in joined


# ─── frontend: the runner pages five at a time ─────────────────

def _node(script: str) -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    out = subprocess.run([node, "-e", script], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


_LOADER = """
global.window = { App: {} };
require({runner});
const R = window.App.QuizRunner;
const q = (n) => ({ id: n, question: 'Q' + n, options: ['a', 'b'] });
const mk = (n) => Array.from({length: n}, (_, i) => q(i + 1));
"""


def _run_paging(n: int) -> dict:
    # .format() would choke on the JS braces, so substitute the path only.
    loader = _LOADER.replace("{runner}", json.dumps(str(RUNNER_JS)))
    script = loader + f"""
const st = R._create(mk({n}), {{}});
console.log(JSON.stringify({{
  PAGE_SIZE: R.PAGE_SIZE,
  pages: st.pages.length,
  sizes: st.pages.map((p) => p.length),
  first: st.pages[0].map((x) => x.id),
}}));
"""
    return json.loads(_node(script))


def test_runner_pages_five_at_a_time():
    out = _run_paging(20)
    assert out["PAGE_SIZE"] == 5
    assert out["pages"] == 4
    assert out["sizes"] == [5, 5, 5, 5]
    assert out["first"] == [1, 2, 3, 4, 5]


def test_runner_keeps_the_remainder_page():
    out = _run_paging(7)
    assert out["pages"] == 2
    assert out["sizes"] == [5, 2]


def test_runner_handles_a_single_page():
    out = _run_paging(3)
    assert out["pages"] == 1
    assert out["sizes"] == [3]


def test_runner_grades_a_page_not_one_question():
    src = _src(RUNNER_JS)
    assert "gradePage" in src
    check = src.split("function _check")[1].split("function _next")[0]
    # One call per page...
    assert check.count("gradePage(") == 1
    # ...built from every question on the page.
    assert "_collect(st, el)" in check


def test_runner_keeps_the_v2_results_table():
    src = _src(RUNNER_JS)
    for col in ("Pregunta", "Respuesta correcta", "Explicación", "Tu respuesta"):
        assert col in src, col


def test_runner_has_a_final_score_and_retry():
    src = _src(RUNNER_JS)
    assert "function _finish" in src
    assert "sf-qs-score" in src
    assert "sf-qs-retry" in src
    assert "Repetir" in src


def test_runner_requires_every_answer_before_grading():
    src = _src(RUNNER_JS)
    check = src.split("function _check")[1].split("function _next")[0]
    assert "if (answers.length < _pageQuestions(st).length)" in check


def test_runner_measures_real_answer_time():
    """The old embed measured inside the fetch call, so it logged ~0ms."""
    src = _src(RUNNER_JS)
    assert "st.pageT0" in src
    assert "Date.now() - t0" in src


def test_runner_keeps_the_progress_bar_across_pages():
    """The bar was a child of .sf-qs-body, which _renderPage replaced, so it
    disappeared on page 1 and page 2 threw on a null querySelector."""
    src = _src(RUNNER_JS)
    scaffold = src.split("function _ensureScaffold")[1].split("function _renderPage")[0]
    # Both slots are created together, as siblings.
    assert '<div class="sf-qs-bar">' in scaffold
    assert '<div class="sf-qs-page">' in scaffold
    render = src.split("function _renderPage")[1].split("function _collect")[0]
    # ...and the page renderer writes to the page slot, never to the body.
    assert 'el.querySelector(".sf-qs-page").innerHTML' in render
    assert 'querySelector(".sf-qs-body").innerHTML' not in render


def test_runner_final_screen_keeps_the_scaffold_for_retry():
    finish = _src(RUNNER_JS).split("function _finish")[1].split("function _retry")[0]
    assert "_ensureScaffold(el)" in finish
    assert 'el.querySelector(".sf-qs-page").innerHTML' in finish
    assert "el.innerHTML = `<div class=\"sf-qs-body\">" not in finish


# ─── frontend: pool, summary, center ───────────────────────────

def test_pool_view_renders_the_failed_list_and_a_practice_session():
    src = _src(POOL_JS)
    assert "render(container" in src
    assert "QuizAPI.pool(" in src
    assert "Repasar todas" in src
    assert "Practicar seleccionadas" in src
    # The practice run reuses the paged runner, not a second implementation.
    assert "QuizRunner.mount" in src
    assert 'mode: "practice"' in src


def test_pool_supports_resolve_reopen_and_clear():
    src = _src(POOL_JS)
    for call in ("QuizAPI.resolvePool", "QuizAPI.reopenPool", "QuizAPI.clearPool"):
        assert call in src, call


def test_pool_rows_carry_the_question_for_practice():
    src = _src(POOL_JS)
    assert "id: r.question_id" in src
    assert "options: r.options || []" in src


def test_pool_prunes_the_selection_against_the_loaded_rows():
    """_selected is module state and render() is also reached by switching
    course, so stale ids made 'Practicar seleccionadas' a silent no-op."""
    src = _src(POOL_JS)
    then = src.split("window.App.QuizAPI.pool(_scope")[1]
    assert "const present = new Set(rows.map((r) => r.question_id))" in then
    assert "if (!present.has(id)) _selected.delete(id)" in then


def test_summary_view_renders_the_per_block_table():
    src = _src(SUMMARY_JS)
    assert "QuizAPI.stats(" in src
    assert "s.by_block" in src
    assert "Precisión" in src
    assert "sin repasar" in src


def test_summary_answers_the_forward_declared_addon_hook():
    """courses.js:594-607 already calls App.QuizStats.render."""
    assert "window.App.QuizStats = { render: renderInline }" in _src(SUMMARY_JS)
    courses = _src(COURSES_JS)
    assert 'slug: "quiz"' in courses
    assert "window.App.QuizStats" in courses


def test_summary_inline_appends_instead_of_replacing():
    """The hook runs after the topic renderer: replacing the panel would
    wipe the topic the user is looking at."""
    src = _src(SUMMARY_JS)
    inline = src.split("function renderInline")[1].split("window.App.QuizSummary")[0]
    assert "appendChild" in inline
    assert "centerEl.innerHTML" not in inline


def test_summary_inline_tolerates_string_ids():
    """topic_id arrives as a number from JSON but as a string from data-*.
    Strict === made the strip disappear for click-handler topics."""
    script = """
global.window = { App: {} };
require({summary});
const S = window.App.QuizSummary;
console.log(JSON.stringify({
  numNum: S._sameId(3, 3), numStr: S._sameId(3, '3'),
  strNum: S._sameId('3', 3), diff: S._sameId(3, 4),
  nullId: S._sameId(null, 3), nullNull: S._sameId(null, null),
}));
""".replace("{summary}", json.dumps(str(SUMMARY_JS)))
    out = json.loads(_node(script))
    assert out == {"numNum": True, "numStr": True, "strNum": True, "diff": False,
                   "nullId": False, "nullNull": False}


def test_summary_inline_filters_through_the_tolerant_comparison():
    inline = _src(SUMMARY_JS).split("function renderInline")[1]
    assert "_sameId(r.topic_id, topicId)" in inline
    assert "r.topic_id === topicId" not in inline


def test_center_view_is_routed_from_courses():
    assert 's._view === "quiz"' in _src(COURSES_JS)
    assert "QuizCenter.renderView" in _src(COURSES_JS)
    assert "QuizCenter.renderNav()" in _src(COURSES_JS)


def test_center_nav_offers_both_tabs():
    src = _src(CENTER_JS)
    assert "Resumen por Card" in src
    assert "Preguntas Falladas" in src
    # It must land in the sidebar slot courses.js repaints.
    assert "studyflow-left-stats" in src


def test_index_html_loads_the_quiz_modules():
    html = _src(INDEX_HTML)
    for name in ("quiz-api.js", "quiz-runner.js", "quiz-pool.js",
                 "quiz-summary.js", "quiz-center.js", "quiz-embed.js"):
        assert f"/features/quiz/{name}" in html or f"/{name}" in html, name
    assert "/features/quiz/quiz.css" in html
    # Load order: the embed mounts the runner, so the runner must precede it.
    assert html.index("quiz-runner.js") < html.index("quiz-embed.js")


def test_the_quiz_modules_are_precached():
    """index.html pulls its modules with <script src>; anything the precache
    misses only works while online. S5 shipped this bug (six quiz files, zero
    entries) and it fails silently — the app loads, the feature does not.

    Scoped to the quiz files on purpose. The same audit found 29 modules from
    index.html outside the precache across agenda/, habits/ and shared/ — a
    pre-existing project-wide gap, tracked separately rather than fixed inside
    a quiz commit. Widen this list deliberately, not by accident.
    """
    sw = _src(ROOT / "frontend" / "sw.js")
    precache = sw.split("PRECACHE_ASSETS = [")[1].split("\n];")[0]
    quiz = sorted(set(re.findall(r'(?:src|href)="(/features/quiz/[^"]+)"',
                                 _src(INDEX_HTML))))
    assert len(quiz) == 6, quiz
    missing = [p for p in quiz if f"'{p}'" not in precache]
    assert not missing, f"fuera del precache: {missing}"


def test_embed_delegates_to_the_runner_instead_of_a_flat_list():
    src = _src(EMBED_JS)
    assert "QuizRunner.mount" in src
    # The old per-question "Responder" button is gone: that was the
    # one-question-per-request flow S5 replaced.
    assert "sf-q-submit" not in src
    assert "_submitAnswer" not in src


def test_owner_editing_lives_in_the_json_editor():
    """R5 replaced the per-question CRUD with the JSON editor.

    What has to survive the change: the owner still has a route into the
    questions, and the answer key is still only reachable through the owner's
    route. What is gone is the four-prompt()-dialogs flow — with it, a block's
    rows had exactly one writer.
    """
    src = _src(EMBED_JS)
    assert "Gestionar preguntas" in src
    assert "App.QuizJsonEditor" in src
    editor = (ROOT / "frontend" / "features" / "studyflow"
              / "quiz-json-editor.js").read_text()
    # The owner route is what carries correct_answer, so the editor has to read
    # its questions from /manage, not from the study route that strips it.
    assert "questionsForManage" in editor
    assert "questionsForBlock" not in editor


def test_runner_and_manager_read_from_different_endpoints():
    """The study route strips correct_answer; reusing its list for the editor
    printed 'Correcta: ?' on every row."""
    render = _src(EMBED_JS).split("async function _renderBin")[1]
    render = render.split("// ── create / edit")[0]
    assert "questionsForBlock(blockId)" in render
    assert "_managerHtml(blockId, qs.length)" in render


def test_manager_loads_the_answer_key_lazily():
    """Not eagerly: a topic render must not pay for the owner's route, and
    the key should not sit in the DOM until the section is opened."""
    src = _src(EMBED_JS)
    manager = src.split("function _managerHtml")[1].split("// ── render one bin")[0]
    assert "questionsForManage" not in manager
    assert 'data-loaded="0"' in manager
    toggle = src.split('det.addEventListener("toggle"')[1].split("// ── public API")[0]
    assert 'if (!det.open) return' in toggle
    # Opening the section is what mounts the editor, and mounting it is what
    # fetches the answer key. Nothing may fetch it at render time.
    assert "editor.mount" in toggle
    assert 'host.dataset.loaded === "1"' in toggle


def test_manager_stays_open_after_saving():
    """Add/edit/delete rebuild the whole bin, which would slam the manager
    shut after every save."""
    remount = _src(EMBED_JS).split("function _remount")[1]
    remount = remount.split("function _createQuestion")[0]
    assert "wasOpen" in remount
    assert 'dispatchEvent(new Event("toggle"))' in remount
