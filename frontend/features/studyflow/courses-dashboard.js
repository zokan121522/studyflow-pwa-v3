// courses-dashboard.js — favourites row on the home dashboard (Issue #12).
//
// Port of the v2 dashboard row (fase 72 F2): a horizontal strip of cards
// for the starred assignments, so they are reachable without opening the
// Studyflow tab. Kept out of courses.js (already 795 lines) and out of
// app.js, which owns tab switching but knows nothing about courses.
(function () {
  "use strict";

  const CARD_LIMIT = 8; // past this it is a shelf, not a summary

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  // ── renderFavoritesRow() ──────────────────────────────────────
  // Fetches the course list and paints #dashboard-favorites. Silently
  // no-ops when the node is absent (e.g. a future page without a
  // dashboard) rather than throwing on a null query.
  async function renderFavoritesRow() {
    const host = document.getElementById("dashboard-favorites");
    if (!host || !window.App.CoursesAPI) return;
    let list = [];
    try {
      list = (await window.App.CoursesAPI.fetchCourses()) || [];
    } catch (_) {
      return; // offline / API down — leave whatever is on screen
    }
    const favs = list.filter((c) => c.is_favorite);
    if (!favs.length) {
      host.classList.add("hidden");
      host.innerHTML = "";
      return;
    }

    const shown = favs.slice(0, CARD_LIMIT);
    const extra = favs.length - shown.length;

    let html = '<div class="dfv-head"><span class="dfv-title">⭐ Favoritas</span>'
      + `<span class="dfv-count">${favs.length}</span></div>`
      + '<div class="dfv-strip">';
    for (const c of shown) {
      const topics = (c.topics || []).length;
      html += `<button class="dfv-card" data-course-id="${c.id}">`
        + `<span class="dfv-icon">${esc(c.icon || "📘")}</span>`
        + `<span class="dfv-name">${esc(c.title || "Sin título")}</span>`
        + `<span class="dfv-meta">${topics} ${topics === 1 ? "tema" : "temas"}</span>`
        + `</button>`;
    }
    if (extra > 0) {
      html += `<button class="dfv-card dfv-more" data-tab-jump="studyflow">`
        + `<span class="dfv-icon">⋯</span>`
        + `<span class="dfv-name">+${extra} más</span>`
        + `<span class="dfv-meta">ver todas</span></button>`;
    }
    html += "</div>";
    host.innerHTML = html;
    host.classList.remove("hidden");
  }

  // ── wire once ─────────────────────────────────────────────────
  function bind() {
    const host = document.getElementById("dashboard-favorites");
    if (!host || host.dataset.bound === "1") return;
    host.dataset.bound = "1";
    // One delegated listener for the whole strip instead of N per card.
    host.addEventListener("click", (e) => {
      const jump = e.target.closest("[data-tab-jump]");
      if (jump) {
        const btn = document.querySelector(`.tab-btn[data-tab="${jump.dataset.tabJump}"]`);
        if (btn) btn.click();
        return;
      }
      const card = e.target.closest("[data-course-id]");
      if (!card) return;
      const id = Number(card.dataset.courseId);
      const tab = document.querySelector('.tab-btn[data-tab="studyflow"]');
      if (tab) tab.click();
      // Same entry point the sidebar row uses, so a dashboard card
      // expands + selects exactly like clicking it in the nav does.
      if (window.App.Courses && window.App.Courses.handleCourseClick) {
        window.App.Courses.handleCourseClick(id);
      }
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    bind();
    renderFavoritesRow();
  });

  window.App = window.App || {};
  window.App.CoursesDashboard = { renderFavoritesRow, bind };
})();

console.log("[Studyflow] courses-dashboard.js loaded");
