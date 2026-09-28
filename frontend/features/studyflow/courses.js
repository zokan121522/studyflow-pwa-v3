// ─── Courses module — S1+S3+S4 slice (sidebar tree + landing + blocks + notes) ─
// Namespace: window.App.Courses + window.CoursesModule (tab router).
// Dependencies: window.STATE, API (global), window.App.UI (escHtml,
// getWeekId, todayStr, formatDateShort, getDaysOfWeek),
// window.App.CoursesAPI, window.App.CoursesSidebar,
// window.App.CoursesBlocks, window.App.CoursesNotes,
// window.App.ContentBlocks._renderMd.
//
// SCOPE:
//   • renderStudyflow(): rebuild sidebar + center
//   • updateCenter(): course landing OR topic detail (read + block CRUD + notes)
//   • create/rename/delete course + topic via prompt dialogs
//   • renderFavorites(): safe no-op (favorites ship with S4+)
//
// S3 wires the topic-detail page into the new blocks backend
// (CoursesBlocks module). S4 swaps in the SF-Card block visuals and
// adds the per-topic notes drawer (App.CoursesNotes). Drag/drop,
// share/export/AI/quiz/flashcards are deferred.

window.App = window.App || {};
window.App.Courses = (function () {
  "use strict";

  const { escHtml } = window.App.UI;
  const {
    fetchCourses, fetchCourseDetail, clearDetailCache,
    createCourse, renameCourse, updateDescription, deleteCourse,
    addTopic, renameTopic, deleteTopic,
  } = window.App.CoursesAPI;
  const {
    renderCourseTree, attachSidebarEvents, updateSelection,
  } = window.App.CoursesSidebar;
  const { _renderMd } = window.App.ContentBlocks;
  const Blocks = window.App.CoursesBlocks;
  const Notes = window.App.CoursesNotes;

  function STATE() { return window.STATE; }

  // ── promptCreateCourse() — header "+ Nuevo curso" ─────────────
  async function promptCreateCourse() {
    const title = (prompt("Nombre del nuevo curso:") || "").trim();
    if (!title) return;
    try {
      const created = await createCourse(title);
      if (created && created.id) {
        STATE().expandedCourseId = created.id;
        STATE().currentCourseId = created.id;
        STATE().selectedTopicId = null;
        STATE().selectedBlockId = null;
      }
      await renderStudyflow();
    } catch (err) {
      alert("❌ Error al crear curso: " + (err.message || err));
    }
  }

  // ── promptRenameCourse(courseId) ─────────────────────────────
  async function promptRenameCourse(courseId) {
    const list = await fetchCourses();
    const c = (list || []).find((x) => x.id === courseId);
    const next = (prompt("Nuevo nombre:", c ? c.title : "") || "").trim();
    if (!next || (c && next === c.title)) return;
    try {
      await renameCourse(courseId, next);
      await renderStudyflow();
    } catch (err) {
      alert("❌ Error al renombrar: " + (err.message || err));
    }
  }

  // ── promptDeleteCourse(courseId, title) ──────────────────────
  async function promptDeleteCourse(courseId, title) {
    if (!confirm(`¿Borrar el curso "${title}" y todos sus temas?`)) return;
    try {
      await deleteCourse(courseId);
      clearDetailCache(courseId);
      await renderStudyflow();
    } catch (err) {
      alert("❌ Error al borrar: " + (err.message || err));
    }
  }

  // ── promptAddTopic(courseId) ──────────────────────────────────
  async function promptAddTopic(courseId) {
    const title = (prompt("Nombre del tema:") || "").trim();
    if (!title) return;
    try {
      await addTopic(courseId, title);
      await renderStudyflow();
    } catch (err) {
      alert("❌ Error al crear tema: " + (err.message || err));
    }
  }

  // ── promptRenameTopic(courseId, topicId) ──────────────────────
  async function promptRenameTopic(courseId, topicId) {
    const detail = await fetchCourseDetail(courseId);
    const t = (detail.topics || []).find((x) => x.id === topicId);
    const next = (prompt("Nuevo nombre del tema:", t ? t.title : "")
      || "").trim();
    if (!next || (t && next === t.title)) return;
    try {
      await renameTopic(courseId, topicId, next);
      await renderStudyflow();
    } catch (err) {
      alert("❌ Error al renombrar tema: " + (err.message || err));
    }
  }

  // ── promptDeleteTopic(courseId, topicId, title) ──────────────
  async function promptDeleteTopic(courseId, topicId, title) {
    if (!confirm(`¿Borrar el tema "${title}"?`)) return;
    try {
      await deleteTopic(courseId, topicId);
      clearDetailCache(courseId);
      await renderStudyflow();
    } catch (err) {
      alert("❌ Error al borrar tema: " + (err.message || err));
    }
  }

  // ── handleCourseClick(courseId) — select + expand ────────────
  async function handleCourseClick(courseId) {
    const s = STATE();
    if (s.expandedCourseId === courseId) {
      // Toggle collapse
      s.expandedCourseId = null;
      s.selectedTopicId = null;
      s.selectedBlockId = null;
      s._expandedCourseTopics = [];
      s._expandedCourseBlocks = [];
      await renderStudyflow();
      return;
    }
    s.expandedCourseId = courseId;
    s.currentCourseId = courseId;
    s.selectedTopicId = null;
    s.selectedBlockId = null;
    const full = await fetchCourseDetail(courseId);
    s._expandedCourseTopics = (full && full.topics) || [];
    s._expandedCourseBlocks = (full && full.blocks) || [];
    await renderStudyflow();
  }

  // ── handleTopicClick(courseId, topicId) — select topic ───────
  async function handleTopicClick(courseId, topicId) {
    const s = STATE();
    s.currentCourseId = courseId;
    s.selectedTopicId = topicId;
    s.selectedBlockId = null;
    // Ensure we have the topics[] for this course in STATE (used by sidebar
    // re-render so it reflects current expansion).
    if (s.expandedCourseId === courseId && (!s._expandedCourseTopics || !s._expandedCourseTopics.length)) {
      const full = await fetchCourseDetail(courseId);
      s._expandedCourseTopics = (full && full.topics) || [];
    }
    await updateCenter();
  }

  // ── renderStudyflow() — full rebuild (sidebar + center) ───────
  async function renderStudyflow() {
    const leftEl = document.getElementById("studyflow-left");
    if (!leftEl) return;
    const courses = await fetchCourses();
    attachSidebarEvents(leftEl, { renderStudyflow, STATE: STATE() });
    leftEl.innerHTML = renderCourseTree(courses, STATE());
    await updateCenter();
  }

  // ── _mountPdfViewers(centerEl) — S7 pdf viewer hook ───────────
  // Iterates .pdf-container elements inside the center panel and
  // hands each to App.PdfViewer.init. The viewer fetches its own
  // bytes asynchronously and is idempotent (safe to call twice on
  // re-renders). Containers without a `data-url` are left untouched.
  function _mountPdfViewers(centerEl) {
    if (!centerEl) return;
    const PV = window.App && window.App.PdfViewer;
    if (!PV || typeof PV.init !== "function") {
      // Viewer module hasn't loaded yet — render a thin placeholder so
      // the block still looks like a PDF slot.
      centerEl.querySelectorAll(".pdf-container").forEach((c) => {
        if (!c.dataset.url) return;
        c.innerHTML = `<div class="pdf-box"><span>📕</span>`
          + `<span>Visor PDF cargando…</span></div>`;
      });
      return;
    }
    centerEl.querySelectorAll(".pdf-container[data-url]").forEach((c) => {
      try { PV.init(c); }
      catch (err) { console.warn("[PdfViewer.init]", err); }
    });
  }

  // ── updateCenter() — lightweight: only the center panel ──────
  async function updateCenter() {
    const centerEl = document.getElementById("studyflow-center");
    if (!centerEl) return;
    const s = STATE();

    if (s.selectedTopicId && s.currentCourseId) {
      await _renderTopicDetail(centerEl, s.currentCourseId, s.selectedTopicId);
      return;
    }
    if (s.currentCourseId) {
      await _renderCourseLanding(centerEl, s.currentCourseId);
      return;
    }
    centerEl.innerHTML =
      '<div class="empty-state"><span class="big">📖</span><br>'
      + 'Selecciona un curso del panel izquierdo o crea uno nuevo.</div>';
  }

  // ── _renderCourseLanding(centerEl, courseId) ─────────────────
  // Course title + description + grid of topic cards. Each card opens
  // the topic detail (read-only). Topics include their blocks rendered
  // via App.ContentBlocks._renderMd when present.
  async function _renderCourseLanding(centerEl, courseId) {
    const [list, detail] = await Promise.all([
      fetchCourses(),
      fetchCourseDetail(courseId),
    ]);
    const course = (list || []).find((c) => c.id === courseId) || {};
    const topics = (detail && detail.topics) || [];

    const safeTitle = escHtml(course.title || "Curso");
    const descHtml = course.description
      ? `<div class="sf-cl-desc md-view">${_renderMd(course.description)}</div>`
      : `<div class="sf-cl-desc sf-cl-empty">Sin descripción</div>`;

    const cards = topics.map((t) => {
      const blocks = t.blocks || [];
      const preview = blocks
        .slice(0, 3)
        .map((b) => `<div class="sf-cl-tblock md-view">`
          + `<div class="sf-cl-tblock-title">${escHtml(b.title || "Bloque")}</div>`
          + `<div class="sf-cl-tblock-content">${_renderMd(b.content || "")}</div>`
          + `</div>`).join("");
      return `<div class="sf-cl-card" data-topic-id="${t.id}" data-course-id="${courseId}">
        <div class="sf-cl-card-head">
          <span class="sf-cl-card-icon">📁</span>
          <span class="sf-cl-card-title">${escHtml(t.title || "Sin título")}</span>
          <span class="sf-cl-card-count">${blocks.length} bloques</span>
        </div>
        <div class="sf-cl-card-body">${preview
          || '<div class="sf-cl-empty">Sin bloques todavía</div>'}</div>
      </div>`;
    }).join("");

    centerEl.innerHTML =
      `<div class="sf-course-landing">
        <div class="sf-cl-header">
          <h2>${safeTitle}</h2>
          ${descHtml}
        </div>
        <div class="sf-cl-section-title">📂 Temas (${topics.length})</div>
        <div class="sf-cl-grid">${cards
          || '<div class="empty-state">Sin temas — usa “➕ Añadir tema” en el panel izquierdo.</div>'}</div>
      </div>`;

    // Card click → select topic + re-render center
    centerEl.querySelectorAll(".sf-cl-card").forEach((card) => {
      card.addEventListener("click", async () => {
        await handleTopicClick(
          Number(card.dataset.courseId),
          Number(card.dataset.topicId)
        );
      });
    });
  }

  // ── _renderTopicDetail(centerEl, courseId, topicId) ──────────
  // S4: each block is rendered via App.CoursesBlocks._renderBlockCard (the
  // SF-Card wrapper around the S3 block markup) and the "+ Añadir bloque"
  // bar is appended. Edit / save / cancel / done / delete actions are
  // delegated by CoursesBlocks._attachBlockHandlers. Up / down reorder +
  // collapse are delegated by CoursesBlocks._attachCardHandlers. The
  // per-topic notes drawer is mounted via App.CoursesNotes.render().
  async function _renderTopicDetail(centerEl, courseId, topicId) {
    const detail = await fetchCourseDetail(courseId);
    const topic = (detail.topics || []).find((t) => t.id === topicId);
    const list = await fetchCourses();
    const course = (list || []).find((c) => c.id === courseId) || {};

    if (!topic) {
      centerEl.innerHTML =
        '<div class="empty-state">⚠️ Tema no encontrado.</div>';
      return;
    }
    const blocks = topic.blocks || [];
    const blocksHtml = blocks.length
      ? blocks.map((b) => Blocks._renderBlockCard(b, courseId)).join("")
      : '<div class="empty-state"><span class="big">📝</span><br>'
        + 'Este tema no tiene bloques aún.<br>'
        + 'Crea uno con “+ Añadir bloque”.</div>';
    const addBar = Blocks._renderAddBar(courseId, topicId);

    centerEl.innerHTML = `
      <div class="sf-topic-detail">
        <div class="sf-td-back" data-course-id="${courseId}">
          ← ${escHtml(course.title || "Curso")}
        </div>
        <h2 class="sf-td-title">${escHtml(topic.title || "Tema")}</h2>
        ${topic.description
          ? `<div class="sf-td-desc md-view">${_renderMd(topic.description)}</div>`
          : ""}
        <div class="sf-td-blocks">${blocksHtml}</div>
        ${addBar}
      </div>
    `;

    // S3 handlers — edit / save / cancel / done / delete / add.
    Blocks._attachBlockHandlers(centerEl, courseId, topicId);
    // S4 handlers — collapse / up / down reorder.
    Blocks._attachCardHandlers(centerEl, courseId, topicId);
    // S4 — per-topic notes drawer.
    Notes.render(centerEl, courseId, topicId);
    // S7 — pdf-ref blocks mount a real pdf.js viewer. Iterate every
    // .pdf-container and hand it to App.PdfViewer.init; the viewer
    // fetches its own PDF bytes and renders the first page.
    _mountPdfViewers(centerEl);

    const back = centerEl.querySelector(".sf-td-back");
    if (back) {
      back.addEventListener("click", async () => {
        STATE().selectedTopicId = null;
        await updateCenter();
      });
    }
  }

  // ── renderFavorites() — used by dashboard (S4+ lands it for real)
  function renderFavorites() {
    // Safe no-op: favorites are not modelled in this slice.
  }

  // ── S3: re-render topic panel after any block mutation ──────
  // CoursesBlocks dispatches `studyflow:blocks-changed` after every
  // add / update / delete so we just need to refresh the center when
  // we're currently showing that topic.
  function _onBlocksChanged(e) {
    const detail = (e && e.detail) || {};
    const s = STATE();
    if (s.currentCourseId !== detail.courseId) return;
    if (detail.topicId != null
        && s.selectedTopicId !== detail.topicId) return;
    // Fire-and-forget: don't block the event handler.
    updateCenter().catch((err) => console.error("[blocks-changed]", err));
  }
  if (typeof window !== "undefined"
      && !window.__studyflowBlocksListenerMounted) {
    window.__studyflowBlocksListenerMounted = true;
    window.addEventListener("studyflow:blocks-changed", _onBlocksChanged);
  }

  // ── Public API ──────────────────────────────────────────────
  return {
    renderStudyflow,
    updateCenter,
    renderFavorites,
    // prompt/handlers exposed for sidebar inline onclick
    promptCreateCourse,
    promptRenameCourse,
    promptDeleteCourse,
    promptAddTopic,
    promptRenameTopic,
    promptDeleteTopic,
    handleCourseClick,
    handleTopicClick,
  };
})();

// ─── Tab-router adapter ────────────────────────────────────────
// The v3 app.js calls App.modules.Courses.render() when the user clicks
// the Studyflow tab. The legacy 71-line stub defined window.CoursesModule
// with a render() that did everything itself; we keep that surface and
// delegate to the real App.Courses shell.
window.CoursesModule = {
  name: "Studyflow",
  async render() {
    if (window.App && window.App.Courses
        && typeof window.App.Courses.renderStudyflow === "function") {
      try {
        await window.App.Courses.renderStudyflow();
      } catch (err) {
        const c = document.getElementById("studyflow-center");
        if (c) c.innerHTML = `<div class="empty-state">⚠️ ${escHtml(
          (err && err.message) || String(err)
        )}</div>`;
      }
    }
  },
};

console.log("[Studyflow] courses.js loaded");