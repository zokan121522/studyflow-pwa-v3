// frontend/features/quiz/quiz-center.js
// S5 F3+F4 — the dedicated Quiz center view (v2's "📊 Quiz" nav section
// with "Resumen por Card" and "Preguntas Falladas").
//
// Wiring, mirroring the AddonsManager seam that already existed:
//   • renderNav() appends a sidebar section into #studyflow-left-stats
//     and is re-run after every sidebar rebuild (courses.js calls it).
//   • renderView(centerEl) takes over the center panel when
//     STATE._view === "quiz" (courses.js routes it before the
//     topic/course branches).
//
// Unlike the center panel, #studyflow-left-stats is NOT wiped on every
// render, so the nav has to clean up after itself: renderNav() removes
// its own section first and only re-injects it when the addon is live.
// Gating on Addons.isEnabled + listening to "addons:changed" is what
// makes 📊 Quiz / Resumen por Card / Preguntas Falladas disappear the
// moment the user uninstalls the quiz addon (v2 behaviour, Phase 46).

(function () {
  if (window.App === undefined) window.App = {};
  if (window.App.QuizCenter !== undefined) return;

  let _tab = "summary";

  // ── addon gate ─────────────────────────────────────────────
  // null = not probed yet. Cached so a sidebar rebuild (which happens on
  // every course/topic switch) doesn't refetch the catalog.
  let _quizActive = null;

  async function _isQuizActive() {
    if (_quizActive !== null) return _quizActive;
    const A = window.App && window.App.Addons;
    if (!A || typeof A.isEnabled !== "function") return (_quizActive = true);
    try {
      _quizActive = await A.isEnabled("quiz");
    } catch (_) {
      _quizActive = true; // fail-open: never hide the nav on a probe error
    }
    return _quizActive;
  }

  function _removeNav() {
    const statsEl = document.getElementById("studyflow-left-stats");
    if (!statsEl) return;
    // All of them, not just the first: an older build (or a render that
    // raced) can leave duplicates behind, and a lone orphan would keep
    // showing the quiz nav forever.
    statsEl.querySelectorAll("[data-qc-nav]").forEach((n) => n.remove());
  }

  // Uninstalling must also eject the user from the quiz view, otherwise
  // they're stranded in a center panel whose nav item just vanished.
  function _ejectIfStranded() {
    try {
      if (window.STATE && window.STATE._view === "quiz") {
        window.STATE._view = null;
        const C = window.App.Courses;
        if (C && typeof C.updateCenter === "function") C.updateCenter();
      }
    } catch (_) { /* cosmetic */ }
  }

  window.addEventListener("addons:changed", () => {
    _quizActive = null;  // invalidate: enable/disable/install/uninstall
    renderNav().catch(() => {});  // nav is optional cosmetic
  });

  // ── sidebar nav ─────────────────────────────────────────────
  function _injectNav(statsEl) {
    const html = `<div class="sf-stats-section" data-qc-nav>
        <div class="col-title">📊 Quiz</div>
        <div class="qs-nav-item" data-qc-view="summary">
          <span class="qs-nav-icon">📈</span>
          <span class="qs-nav-title">Resumen por Card</span>
        </div>
        <div class="qs-nav-item" data-qc-view="pool">
          <span class="qs-nav-icon">❌</span>
          <span class="qs-nav-title">Preguntas Falladas</span>
        </div>
      </div>`;
    statsEl.insertAdjacentHTML("beforeend", html);
    statsEl.querySelectorAll("[data-qc-view]").forEach((el) => {
      el.addEventListener("click", async () => {
        _tab = el.dataset.qcView;
        try { window.STATE._view = "quiz"; } catch (_) {}
        await renderNav();
        if (window.App.Courses && typeof window.App.Courses.updateCenter === "function") {
          await window.App.Courses.updateCenter();
        }
      });
    });
  }

  // ── sidebar nav ─────────────────────────────────────────────
  async function _renderOnce() {
    const statsEl = document.getElementById("studyflow-left-stats");
    if (!statsEl) return;

    // Always clean up first — a disabled addon must leave nothing behind.
    _removeNav();
    if (!(await _isQuizActive())) {
      _ejectIfStranded();
      return;
    }

    // Re-query AFTER the async gate: a sidebar rebuild may have swapped
    // the element out while we were awaiting.
    const el = document.getElementById("studyflow-left-stats");
    if (!el) return;
    _injectNav(el);
    const active = window.STATE && window.STATE._view === "quiz";
    el.querySelectorAll("[data-qc-view]").forEach((n) => {
      n.classList.toggle("active", !!active && n.dataset.qcView === _tab);
    });
  }

  // The gate above is async, so two callers can both pass the removal and
  // then both inject — the sidebar ends up with a duplicate 📊 Quiz section.
  // Serialise instead: a call arriving mid-render flags a re-run instead of
  // returning, so an install/uninstall that lands during a render is never
  // dropped. A plain `if (busy) return` (v2's approach) loses that update.
  let _rendering = false;
  let _renderAgain = false;

  async function renderNav() {
    if (_rendering) { _renderAgain = true; return; }
    _rendering = true;
    try {
      do {
        _renderAgain = false;
        await _renderOnce();
      } while (_renderAgain);
    } finally {
      _rendering = false;
    }
  }

  // ── center view ─────────────────────────────────────────────
  function _tabsHtml() {
    const mk = (id, label) => `<button class="sf-qc-tab${_tab === id ? " active" : ""}"
        data-tab="${id}">${label}</button>`;
    return `<div class="sf-qc-tabs">${mk("summary", "📈 Resumen")}${mk("pool", "❌ Falladas")}</div>`;
  }

  function _renderTab(container) {
    const slot = container.querySelector(".sf-qc-slot");
    if (!slot) return;
    if (_tab === "pool") {
      window.App.QuizPool.render(slot, {});
    } else {
      window.App.QuizSummary.render(slot, {});
    }
  }

  async function renderView(centerEl) {
    if (!centerEl) return;
    centerEl.innerHTML = `<div class="sf-qc">
        <div class="sf-qc-head">
          <h2>📊 Quiz</h2>
          ${_tabsHtml()}
        </div>
        <div class="sf-qc-slot"></div>
      </div>`;
    centerEl.querySelectorAll(".sf-qc-tab").forEach((b) => {
      b.addEventListener("click", () => {
        _tab = b.dataset.tab;
        renderView(centerEl);
      });
    });
    _renderTab(centerEl);
  }

  window.App.QuizCenter = { renderNav, renderView, _tab: () => _tab };
})();
