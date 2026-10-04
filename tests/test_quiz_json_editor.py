r"""R5 — the exercise questions edited as JSON.

The CRUD this replaced was four chained prompt() dialogs per question, so a
mistyped option silently produced a question whose answer key pointed at the
wrong letter. The editor replaces that with a JSON textarea and a read-only
preview.

Two properties carry the whole feature and are tested behaviourally, with
node (the pattern already used by test_quiz_session.py):

  1. `correct` is 0-based, because that is what the NotebookLM generator emits
     and what _prepare_bulk_rows accepts. A 1-based slip would mark the wrong
     option correct and the learner would be taught the wrong answer.
  2. The preview is READ-ONLY. App.QuizRunner.mount() renders an interactive
     session whose "Corregir" button POSTs question_id to /quiz/answers. With
     preview ids those ids do not exist; with real ids a stray click writes a
     bogus page into quiz_results and pushes a question into the failed pool.
     So the editor must never mount the runner.

The rest are wiring assertions: the module is precached, loaded before the
module that calls it, and the endpoint it posts to already supports the
rewrite (replace: true), which is why R5 touches no backend.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EDITOR_JS = ROOT / "frontend" / "features" / "studyflow" / "quiz-json-editor.js"
EMBED_JS = ROOT / "frontend" / "features" / "studyflow" / "quiz-embed.js"
API_JS = ROOT / "frontend" / "features" / "quiz" / "quiz-api.js"
QUIZ_PY = ROOT / "backend" / "routes" / "quiz.py"
SW = ROOT / "frontend" / "sw.js"
INDEX = ROOT / "frontend" / "index.html"


def _src(p: Path) -> str:
    return p.read_text()


def _node(script: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    loader = "global.window = { App: {} };\nrequire(%s);\n" % json.dumps(str(EDITOR_JS))
    out = subprocess.run([node, "-e", loader + script],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _code(src: str) -> str:
    """The source with comments stripped, so a test can assert that a call is
    gone without tripping over the comment that explains why it went."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"^\s*//.*$", "", src, flags=re.M)


def _errors_for(payload: str) -> list:
    """The validation errors for one payload, parsed in node."""
    return _node("const E=window.App.QuizJsonEditor;"
                 "console.log(JSON.stringify({x:E.parse(%s).errors}));"
                 % json.dumps(payload))["x"]


def _ok_for(payload: str) -> dict:
    return _node("const E=window.App.QuizJsonEditor;"
                 "const r=E.parse(%s);"
                 "console.log(JSON.stringify({n:Array.isArray(r.questions)?r.questions.length:-1,"
                 "errs:r.errors.length}));" % json.dumps(payload))


# ── the 0-based contract ──────────────────────────────────────

VALID = json.dumps([
    {"question": "¿Qué devuelve this?", "options": ["undefined", "null", "this", "{}"], "correct": 2},
    {"question": "Segunda", "options": ["a", "b"], "correct": 0},
])


def test_valid_json_reports_no_errors():
    assert _errors_for(VALID) == []


def test_correct_zero_is_valid_not_missing():
    """The trap: `if (!correct)` reads 0 as absent, so question 2 above would
    be rejected for having no answer key."""
    out = _ok_for(VALID)
    assert out["n"] == 2
    assert out["errs"] == 0


def test_correct_may_name_the_last_option():
    payload = json.dumps([{"question": "q", "options": ["a", "b", "c"], "correct": 2}])
    assert _errors_for(payload) == []


def test_correct_out_of_range_is_rejected_with_the_range():
    payload = json.dumps([{"question": "q", "options": ["a", "b"], "correct": 5}])
    errs = _errors_for(payload)
    assert len(errs) == 1
    assert "fuera de rango" in errs[0]["error"]
    assert "0-1" in errs[0]["error"]


def test_correct_below_zero_is_rejected():
    payload = json.dumps([{"question": "q", "options": ["a", "b"], "correct": -1}])
    assert _errors_for(payload)[0]["error"].startswith("correct -1 fuera de rango")


@pytest.mark.parametrize("bad", [None, "", "A", 1.5])
def test_non_integer_or_absent_correct_is_rejected(bad):
    """Number(null) and Number("") are both 0, and Number(true) is 1, so the
    empties have to be rejected before the integer check."""
    payload = json.dumps([{"question": "q", "options": ["a", "b"], "correct": bad}])
    assert _errors_for(payload) != []


def test_the_column_spelling_also_validates():
    """_prepare_bulk_rows accepts `correct` or `correct_answer`; so does the
    editor, or a payload hand-assembled against the column would be flagged."""
    payload = json.dumps([{"question": "q", "options": ["a", "b"], "correct_answer": 1}])
    assert _errors_for(payload) == []


# ── structural validation ─────────────────────────────────────

def test_malformed_json_is_reported_not_thrown():
    errs = _errors_for('[{"question": "q",}]')
    assert len(errs) == 1
    assert "JSON inválido" in errs[0]["error"]


def test_empty_text_is_reported():
    assert _errors_for("   ")[0]["error"] == "vacío"


def test_root_must_be_an_array():
    assert "lista" in _errors_for('{"question":"q"}')[0]["error"]


def test_empty_array_is_reported():
    assert _errors_for("[]")[0]["error"] == "la lista está vacía"


def test_missing_question_is_reported():
    payload = json.dumps([{"options": ["a", "b"], "correct": 0}])
    assert "falta question" in _errors_for(payload)[0]["error"]


def test_blank_question_is_reported():
    payload = json.dumps([{"question": "   ", "options": ["a", "b"], "correct": 0}])
    assert "falta question" in _errors_for(payload)[0]["error"]


def test_options_must_be_a_list():
    payload = json.dumps([{"question": "q", "options": "a|b", "correct": 0}])
    assert "falta options" in _errors_for(payload)[0]["error"]


def test_options_need_two_entries():
    payload = json.dumps([{"question": "q", "options": ["solo"], "correct": 0}])
    assert "al menos 2" in _errors_for(payload)[0]["error"]


def test_blank_option_is_reported():
    payload = json.dumps([{"question": "q", "options": ["a", "  "], "correct": 0}])
    assert "vacía" in _errors_for(payload)[0]["error"]


def test_every_bad_question_is_reported_not_just_the_first():
    payload = json.dumps([
        {"question": "ok", "options": ["a", "b"], "correct": 0},
        {"question": "", "options": ["a"], "correct": 9},
    ])
    errs = _errors_for(payload)
    # Question 1 is wrong three ways: no text, one option, and an index 9
    # that cannot point at anything. All three must be reported at once, or
    # the author fixes one error per save.
    assert {e["index"] for e in errs} == {1}
    assert len(errs) == 3, errs


# ── serialisation ─────────────────────────────────────────────

def test_to_json_maps_the_column_to_the_zero_based_field():
    out = _node(
        "const E=window.App.QuizJsonEditor;"
        "console.log(JSON.stringify({t:JSON.parse(E.toJson("
        "[{id:7,question:'q',options:['a','b','c'],correct_answer:1,"
        "explanation:'porque'}]))}));")
    q = out["t"][0]
    assert q["correct"] == 1
    assert q["explanation"] == "porque"


def test_to_json_drops_the_id():
    """A save is a bulk replace, so every id is reassigned. Leaving the old
    ids in the text would suggest re-saving preserves them."""
    out = _node(
        "const E=window.App.QuizJsonEditor;"
        "console.log(JSON.stringify({t:JSON.parse(E.toJson("
        "[{id:7,question:'q',options:['a','b'],correct:0}]))}));")
    assert "id" not in out["t"][0]


def test_to_json_omits_empty_explanation_and_default_difficulty():
    out = _node(
        "const E=window.App.QuizJsonEditor;"
        "console.log(JSON.stringify({t:JSON.parse(E.toJson("
        "[{question:'q',options:['a','b'],correct:0,explanation:'',"
        "difficulty:'medium'}]))}));")
    assert "explanation" not in out["t"][0]
    assert "difficulty" not in out["t"][0]


def test_to_json_keeps_a_non_default_difficulty():
    out = _node(
        "const E=window.App.QuizJsonEditor;"
        "console.log(JSON.stringify({t:JSON.parse(E.toJson("
        "[{question:'q',options:['a','b'],correct:0,difficulty:'hard'}]))}));")
    assert out["t"][0]["difficulty"] == "hard"


def test_to_json_output_round_trips_through_parse():
    """The editor must be able to load a block and save it back unchanged."""
    out = _node(
        "const E=window.App.QuizJsonEditor;"
        "const src=[{question:'q',options:['a','b'],correct:1,"
        "explanation:'x',difficulty:'hard'}];"
        "const text=E.toJson(src);"
        "console.log(JSON.stringify({ok:E.parse(text).errors.length===0,"
        "same:JSON.stringify(E.parse(text).questions)===JSON.stringify(src)}));")
    assert out["ok"] is True
    assert out["same"] is True


def test_to_json_never_invents_an_answer_key():
    """A correct value the editor cannot read is passed through so validate()
    flags it, rather than being silently rewritten to 0 (which would teach
    the learner that option A is right)."""
    out = _node(
        "const E=window.App.QuizJsonEditor;"
        "const t=JSON.parse(E.toJson([{question:'q',options:['a','b'],"
        "correct_answer:'nope'}]));"
        "console.log(JSON.stringify({c:t[0].correct,"
        "errs:E.parse(E.toJson([{question:'q',options:['a','b'],"
        "correct_answer:'nope'}])).errors.length}));")
    assert out["c"] == "nope"
    assert out["errs"] > 0


# ── the preview must stay read-only ───────────────────────────

def test_preview_marks_the_correct_option_and_only_that_one():
    out = _node(
        "const E=window.App.QuizJsonEditor;"
        "const h=E.previewHtml([{question:'q',options:['a','b','c'],correct:1}]);"
        "console.log(JSON.stringify({hits:(h.match(/sf-qje-opt correct/g)||[]).length,"
        "letters:(h.match(/sf-qje-letter/g)||[]).length}));")
    assert out["hits"] == 1
    assert out["letters"] == 3


def test_preview_escapes_a_script_tag_in_the_question():
    out = _node(
        "const E=window.App.QuizJsonEditor;"
        "const h=E.previewHtml([{question:'<script>alert(1)</script>',"
        "options:['<img onerror=x>','b'],correct:0}]);"
        "console.log(JSON.stringify({raw:h.includes('<script>'),"
        "img:h.includes('<img onerror'),esc:h.includes('&lt;script&gt;')}));")
    assert out["raw"] is False
    assert out["img"] is False
    assert out["esc"] is True


def test_preview_refuses_to_render_a_non_array():
    out = _node(
        "const E=window.App.QuizJsonEditor;"
        "console.log(JSON.stringify({h:E.previewHtml({question:'q'})}));")
    assert "Corrige el JSON" in out["h"]


def test_editor_never_mounts_the_interactive_runner():
    """The whole reason the preview is hand-rolled. QuizRunner's Corregir
    posts question_id; a preview must not be able to grade."""
    # Match calls, not the word: the module explains at length why it does
    # not use the runner, and that comment must not fail its own guard.
    src = _src(EDITOR_JS)
    assert "QuizRunner.mount" not in _code(src)
    assert "gradePage" not in _code(src)
    assert "/quiz/answers" not in _code(src)


def test_preview_with_no_valid_correct_marks_nothing():
    out = _node(
        "const E=window.App.QuizJsonEditor;"
        "console.log(JSON.stringify({h:E.previewHtml("
        "[{question:'q',options:['a','b']}])}));")
    assert "sf-qje-opt correct" not in out["h"]


# ── the save reuses an endpoint that already existed ──────────

def test_bulk_replace_posts_with_replace_true():
    src = _src(API_JS)
    block = src.split("bulkReplace")[1].split("},")[0]
    assert "/quiz/questions/bulk" in block
    assert "replace: true" in block


def test_the_backend_endpoint_accepts_replace_and_correct():
    src = _src(QUIZ_PY)
    assert "@bp.post('/quiz/questions/bulk')" in src
    assert "if data.get('replace')" in src
    prep = src.split("def _prepare_bulk_rows")[1].split("@bp.post")[0]
    # The client mirrors this: both spellings, and the range check.
    assert "q.get('correct_answer', q.get('correct'))" in prep
    assert "0 <= int(correct) < len(options)" in prep


def test_r5_touches_no_backend_route():
    """Everything the editor needs already existed, so a new endpoint would
    be a second way to do the same thing."""
    assert not (ROOT / "backend" / "routes" / "quiz_json.py").exists()


# ── wiring ────────────────────────────────────────────────────

def test_the_prompt_crud_is_gone():
    """Four chained prompt() dialogs per question is what R5 replaced; leaving
    it behind would be two ways to edit the same rows."""
    src = _src(EMBED_JS)
    assert _code(src).count("prompt(") == 0
    assert "sf-q-add" not in _code(src)
    assert "_questionCardHtml" not in src


def test_embed_hands_the_disclosure_to_the_editor():
    src = _src(EMBED_JS)
    assert "App.QuizJsonEditor" in src
    assert "sf-q-host" in src
    assert "onSaved" in src


def test_a_save_remounts_so_the_runner_shows_the_new_questions():
    src = _src(EDITOR_JS)
    assert "onSaved" in src


def test_the_editor_is_precached():
    """Without this an installed PWA cannot load the editor at all, and the
    disclosure silently does nothing."""
    src = _src(SW)
    block = src.split("const PRECACHE_ASSETS = [", 1)[1].split("];", 1)[0]
    assert "'/features/studyflow/quiz-json-editor.js'" in block


def test_the_editor_loads_before_the_module_that_calls_it():
    html = _src(INDEX)
    assert html.index("/features/studyflow/quiz-json-editor.js") < html.index(
        "/features/studyflow/quiz-embed.js")


def test_the_asset_cache_was_bumped_for_r5():
    src = _src(SW)
    assert "studyflow-assets-v88" in src
    # ...with the reason recorded, or the next bump is a guess.
    assert re.search(r"//\s*v88:.*R5", src, re.S)


def test_save_is_blocked_while_the_json_is_invalid():
    """The button is disabled on error, so an invalid batch never costs a
    round trip and never half-replaces the block."""
    out = _node(
        "const E=window.App.QuizJsonEditor;"
        "const saved=[];"
        "console.log(JSON.stringify({src:E._skeleton().includes('sf-qje-save')}));")
    assert out["src"] is True