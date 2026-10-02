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

(function () {
  if (window.App === undefined) window.App = {};
  if (window.App.QuizCenter !== undefined) return;

  let _tab = "summary";

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
        renderNav();
        if (window.App.Courses && typeof window.App.Courses.updateCenter === "function") {
          await window.App.Courses.updateCenter();
        }
      });
    });
  }

  function renderNav() {
    const statsEl = document.getElementById("studyflow-left-stats");
    if (!statsEl) return;
    if (!statsEl.querySelector("[data-qc-nav]")) _injectNav(statsEl);
    const active = window.STATE && window.STATE._view === "quiz";
    statsEl.querySelectorAll("[data-qc-view]").forEach((el) => {
      el.classList.toggle("active", !!active && el.dataset.qcView === _tab);
    });
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
