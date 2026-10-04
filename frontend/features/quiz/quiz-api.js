// frontend/features/quiz/quiz-api.js
// S5 — thin fetch layer for the quiz ecosystem.
//
// Every quiz endpoint the app talks to lives here, so the runner, the
// failed pool and the summary all share one place for auth headers and
// error shapes. The per-question routes (CRUD of quiz_questions) stay in
// quiz-embed.js, which owns block editing; this module covers the session,
// the pool and the stats.

(function () {
  if (window.App === undefined) window.App = {};
  if (window.App.QuizAPI !== undefined) return;

  const _auth = () => (window.App.Auth && window.App.Auth.authHeaders)
    ? window.App.Auth.authHeaders() : {};
  const _base = () => window.API_URL || "/api";

  async function _get(path) {
    const r = await fetch(`${_base()}${path}`, { headers: _auth() });
    if (!r.ok) throw new Error(`quiz ${path} → ${r.status}`);
    return r.json();
  }

  async function _post(path, body) {
    const r = await fetch(`${_base()}${path}`, {
      method: "POST",
      headers: Object.assign({ "Content-Type": "application/json" }, _auth()),
      body: JSON.stringify(body || {}),
    });
    if (!r.ok) throw new Error(`quiz ${path} → ${r.status}`);
    return r.json();
  }

  window.App.QuizAPI = {
    // ── session ────────────────────────────────────────────────
    // The list route hides correct_answer/explanation (grading is the
    // server's job), so limit is raised well past the 50 default to
    // keep a 100-question test in one page.
    questionsForBlock(blockId) {
      return _get(`/quiz/questions?block_id=${blockId}&limit=200`)
        .then((j) => j.questions || []);
    },

    // Owner's view: includes correct_answer/explanation, which the study
    // route above hides. The block editor needs it to prefill the form.
    questionsForManage(blockId) {
      return _get(`/quiz/questions/manage?block_id=${blockId}`)
        .then((j) => j.questions || []);
    },

    // Grade a whole page in one round-trip. v2 graded 5 questions
    // client-side and persisted only at the end, losing the test if the
    // user navigated away mid-run.
    gradePage(answers) {
      return _post("/quiz/answers", { answers });
    },

    // The JSON editor's save. `replace: true` makes the server delete the
    // block's questions and insert the batch, which is what a full rewrite
    // needs — otherwise every save would append a second copy. Resolves to
    // {inserted, invalid}: `invalid` is per-question and the batch still
    // succeeds, so the editor reports them instead of failing outright.
    bulkReplace(blockId, courseId, topicId, questions) {
      return _post("/quiz/questions/bulk", {
        block_id: blockId,
        course_id: courseId,
        topic_id: topicId,
        replace: true,
        questions: questions || [],
      });
    },

    // ── failed pool ────────────────────────────────────────────
    pool(scope, filters) {
      const f = filters || {};
      const qs = new URLSearchParams({ scope: scope || "open" });
      if (f.course_id) qs.set("course_id", f.course_id);
      if (f.block_id) qs.set("block_id", f.block_id);
      qs.set("limit", f.limit || 200);
      return _get(`/quiz/errors?${qs.toString()}`);
    },

    resolvePool(questionIds) {
      return _post("/quiz/errors/resolve", { question_ids: questionIds });
    },
    resolveAllPool() {
      return _post("/quiz/errors/resolve", { all: true });
    },
    reopenPool(questionIds) {
      return _post("/quiz/errors/reopen", { question_ids: questionIds });
    },
    clearPool() {
      return _post("/quiz/errors/clear", { all: true });
    },

    // ── stats ──────────────────────────────────────────────────
    stats(courseId) {
      return _get(`/quiz/stats${courseId ? `?course_id=${courseId}` : ""}`);
    },
  };
})();
