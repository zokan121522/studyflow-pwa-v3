// frontend/features/quiz/quiz-runner.js
// S5 F1+F2 — paged test session with a final results table.
//
// v2 parity, rebuilt on v3's normalised model:
//   • 5 questions per page, dot progress, "Corregir" per page
//   • a results table after every page (Pregunta | Correcta | Explicación
//     | Tu respuesta) and a full summary screen with score + retry
//
// What changed on purpose:
//   • Grading is server-side, one round-trip per PAGE (v2 read
//     data-correct client-side, so the answers were only durable at the
//     end of the run — abandon the test and the whole attempt was lost).
//   • Wrong answers feed the failed pool and right ones resolve it, from
//     /quiz/answers. The runner has no pool logic of its own.
//
// Usage:
//   App.QuizRunner.mount(el, {questions, title, mode: "block"|"practice",
//                             onExit})

(function () {
  if (window.App === undefined) window.App = {};
  if (window.App.QuizRunner !== undefined) return;

  const PAGE_SIZE = 5;
  const __auth = () => (window.App.Auth && window.App.Auth.authHeaders)
    ? window.App.Auth.authHeaders() : {};

  function _esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  function _md(s) {
    if (!s) return "";
    try {
      if (window.App.ContentBlocks && window.App.ContentBlocks._renderMd) {
        return window.App.ContentBlocks._renderMd(s);
      }
    } catch (_) { /* plain text */ }
    return _esc(s);
  }

  // ── markup helpers ───────────────────────────────────────────
  function _optHtml(q, i, letter) {
    return `<label class="sf-qs-opt" data-qid="${q.id}" data-opt="${i}">
        <input type="radio" name="sf-qs-${q.id}" value="${i}">
        <span class="sf-qs-letter">${letter}</span>
        <span class="sf-qs-opt-text">${_esc(q.options[i])}</span>
      </label>`;
  }

  function _cardHtml(q, absIdx) {
    const letters = "ABCDEFGH";
    const opts = (q.options || [])
      .map((_, i) => _optHtml(q, i, letters[i] || "?")).join("");
    return `<div class="sf-qs-card" data-qid="${q.id}">
        <div class="sf-qs-head">
          <span class="sf-qs-num">${absIdx + 1}</span>
          <span class="sf-qs-text">${_md(q.question)}</span>
        </div>
        <div class="sf-qs-opts">${opts}</div>
        <div class="sf-qs-fb"></div>
      </div>`;
  }

  function _tableRow(q, rec) {
    const letters = "ABCDEFGH";
    const right = letters[rec.correct_answer] || "—";
    const mine = rec.selected_answer == null
      ? "—"
      : `${letters[rec.selected_answer] || "?"} · ${_esc(
        (q.options || [])[rec.selected_answer] || "")}`;
    return `<tr class="${rec.is_correct ? "ok" : "bad"}">
        <td class="sf-qs-t-q">${_esc(q.question)}</td>
        <td class="sf-qs-t-right">${right} · ${_esc(
          (q.options || [])[rec.correct_answer] || "")}</td>
        <td class="sf-qs-t-exp">${rec.explanation ? _md(rec.explanation) : "—"}</td>
        <td class="sf-qs-t-mine">${mine}</td>
      </tr>`;
  }

  function _tableHtml(questions, records) {
    return `<table class="sf-qs-table">
        <thead><tr>
          <th>Pregunta</th><th>Respuesta correcta</th>
          <th>Explicación</th><th>Tu respuesta</th>
        </tr></thead>
        <tbody>${questions.map((q) => _tableRow(
          q, records[q.id])).filter(Boolean).join("")}</tbody>
      </table>`;
  }

  // ── controller ───────────────────────────────────────────────
  function _create(questions, opts) {
    const byId = {};
    questions.forEach((q) => { byId[q.id] = q; });
    const pages = [];
    for (let i = 0; i < questions.length; i += PAGE_SIZE) {
      pages.push(questions.slice(i, i + PAGE_SIZE));
    }
    return {
      questions, byId, pages, opts: opts || {},
      page: 0, records: {}, checked: false, busy: false,
    };
  }

  function _pageQuestions(st) { return st.pages[st.page] || []; }

  function _dotsHtml(st) {
    return st.pages.map((_, i) => {
      const recs = st.pages[i].map((q) => st.records[q.id]).filter(Boolean);
      const done = recs.length === st.pages[i].length;
      const ok = recs.filter((r) => r.is_correct).length;
      const cls = ["sf-qs-dot"];
      if (i === st.page) cls.push("now");
      if (done) cls.push(ok === recs.length ? "ok" : "bad");
      const label = done ? `página ${i + 1}, ${ok} de ${recs.length}` : `página ${i + 1}`;
      return `<span class="${cls.join(" ")}" title="${label}"></span>`;
    }).join("");
  }

  function _chromeHtml(st) {
    const mode = st.opts.mode === "practice" ? "Práctica" : "Test";
    return `<div class="sf-qs-bar">
        <div class="sf-qs-bar-top">
          <span class="sf-qs-mode">${mode}</span>
          <span class="sf-qs-label">Página ${st.page + 1} / ${st.pages.length}</span>
          <span class="sf-qs-dots">${_dotsHtml(st)}</span>
        </div>
        <div class="sf-qs-progress"><div class="sf-qs-fill"
          style="width:${Math.round((st.page / st.pages.length) * 100)}%"></div></div>
      </div>`;
  }

  // ── page rendering ───────────────────────────────────────────
  // The bar and the page are SIBLINGS inside .sf-qs-body, and _renderPage
  // only ever rewrites the page slot. When the bar lived inside the same
  // container that got replaced, it vanished on page 1 and page 2 threw on
  // querySelector(".sf-qs-bar") === null.
  function _ensureScaffold(el) {
    if (el.querySelector(".sf-qs-bar") && el.querySelector(".sf-qs-page")) return;
    el.innerHTML = '<div class="sf-qs-body">'
      + '<div class="sf-qs-bar"></div>'
      + '<div class="sf-qs-page"></div>'
      + '</div>';
  }

  function _renderPage(st, el) {
    const qs = _pageQuestions(st);
    _ensureScaffold(el);
    el.querySelector(".sf-qs-bar").outerHTML = _chromeHtml(st);
    el.querySelector(".sf-qs-page").innerHTML =
      qs.map((q) => _cardHtml(q, st.questions.indexOf(q))).join("")
      + `<div class="sf-qs-actions">
           <button class="sf-btn sf-qs-check">Corregir</button>
           <button class="sf-btn sf-qs-next" hidden>Siguiente →</button>
         </div>
         <div class="sf-qs-results" hidden></div>`;
    _bind(st, el);
  }

  function _collect(st, el) {
    const out = [];
    _pageQuestions(st).forEach((q) => {
      const picked = el.querySelector(
        `.sf-qs-card[data-qid="${q.id}"] input:checked`);
      if (picked) {
        out.push({ question_id: q.id, selected_answer: Number(picked.value) });
      }
    });
    return out;
  }

  function _paintFeedback(st, el, results) {
    results.forEach((r) => {
      const card = el.querySelector(`.sf-qs-card[data-qid="${r.question_id}"]`);
      if (!card) return;
      card.classList.add(r.is_correct ? "ok" : "bad");
      card.querySelectorAll(".sf-qs-opt").forEach((opt) => {
        const i = Number(opt.dataset.opt);
        const input = opt.querySelector("input");
        input.disabled = true;
        if (i === r.correct_answer) opt.classList.add("correct");
        if (i === r.selected_answer && !r.is_correct) opt.classList.add("wrong");
      });
      const fb = card.querySelector(".sf-qs-fb");
      fb.innerHTML = r.is_correct
        ? `<span class="sf-qs-verdict ok">✅ Correcto</span>`
        : `<span class="sf-qs-verdict bad">❌ Incorrecto</span>`;
    });
  }

  function _check(st, el) {
    const answers = _collect(st, el);
    if (answers.length < _pageQuestions(st).length) {
      _flash(el, "Responde todas las preguntas de la página para corregir.");
      return;
    }
    const t0 = st.pageT0 || Date.now();
    st.busy = true;
    _setBusy(el, true);
    const body = answers.map((a) => Object.assign(
      { time_taken_ms: Date.now() - t0 }, a));
    window.App.QuizAPI.gradePage(body)
      .then((j) => {
        (j.results || []).forEach((r) => { st.records[r.question_id] = r; });
        st.checked = true;
        _paintFeedback(st, el, j.results || []);
        const qs = _pageQuestions(st);
        el.querySelector(".sf-qs-results").innerHTML =
          _tableHtml(qs, st.records);
        el.querySelector(".sf-qs-results").hidden = false;
        el.querySelector(".sf-qs-check").hidden = true;
        el.querySelector(".sf-qs-next").hidden = false;
        el.querySelector(".sf-qs-next").textContent =
          st.page === st.pages.length - 1 ? "Finalizar 🏁" : "Siguiente →";
      })
      .catch((e) => _flash(el, e.message || String(e)))
      .finally(() => { st.busy = false; _setBusy(el, false); });
  }

  function _next(st, el) {
    if (!st.checked) return;
    if (st.page < st.pages.length - 1) {
      st.page += 1;
      st.checked = false;
      st.pageT0 = Date.now();
      _renderPage(st, el);
    } else {
      _finish(st, el);
    }
  }

  function _finish(st, el) {
    const recs = Object.values(st.records);
    const ok = recs.filter((r) => r.is_correct).length;
    const pct = recs.length ? Math.round((ok / recs.length) * 100) : 0;
    // Same scaffold: the bar stays (it is the last page) and _retry can go
    // back through _renderPage without rebuilding anything.
    _ensureScaffold(el);
    el.querySelector(".sf-qs-page").innerHTML = `<div class="sf-qs-final">
          <div class="sf-qs-score ${pct >= 70 ? "ok" : "bad"}">
            <span class="sf-qs-score-num">${pct}%</span>
            <span class="sf-qs-score-sub">${ok} de ${recs.length} correctas</span>
          </div>
          ${_tableHtml(st.questions, st.records)}
          <div class="sf-qs-final-actions">
            <button class="sf-btn sf-qs-retry">🔄 Repetir</button>
          </div>
        </div>`;
    el.querySelector(".sf-qs-retry").addEventListener("click", () => _retry(st, el));
  }

  function _retry(st, el) {
    st.page = 0;
    st.records = {};
    st.checked = false;
    st.pageT0 = Date.now();
    _renderPage(st, el);
  }

  // ── small UI helpers ─────────────────────────────────────────
  function _flash(el, msg) {
    // Lives in the page slot, not the body: _renderPage clears it on the
    // next page, which is exactly when the warning stops being true.
    const page = el.querySelector(".sf-qs-page");
    if (!page) return;
    let box = page.querySelector(".sf-qs-flash");
    if (!box) {
      box = document.createElement("div");
      box.className = "sf-qs-flash";
      page.prepend(box);
    }
    box.textContent = "⚠️ " + msg;
  }

  function _setBusy(el, busy) {
    const btn = el.querySelector(".sf-qs-check");
    if (btn) { btn.disabled = busy; btn.textContent = busy ? "Corrigiendo…" : "Corregir"; }
  }

  function _bind(st, el) {
    const check = el.querySelector(".sf-qs-check");
    if (check) check.addEventListener("click", () => _check(st, el));
    const next = el.querySelector(".sf-qs-next");
    if (next) next.addEventListener("click", () => _next(st, el));
  }

  // ── public API ───────────────────────────────────────────────
  function mount(el, opts) {
    if (!el) return null;
    const questions = (opts && opts.questions) || [];
    if (!questions.length) {
      el.innerHTML = '<div class="sf-qs-empty">Sin preguntas para este test.</div>';
      return null;
    }
    const st = _create(questions, opts);
    st.pageT0 = Date.now();
    el.classList.add("sf-qs-wrap");
    el.innerHTML = "";
    _renderPage(st, el);
    return st;
  }

  window.App.QuizRunner = {
    PAGE_SIZE, mount,
    _create, _collect, _finish, _retry,
  };
})();
