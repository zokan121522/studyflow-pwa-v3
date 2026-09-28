// ─── Courses Sidebar — HTML render + delegated events (S1 slice) ───
// Namespace: window.App.CoursesSidebar
// Dependencies: window.App.UI (escHtml), window.App.CoursesAPI,
//               window.App.Courses (renderStudyflow, callbacks).
//
// SCOPE (this slice): course list with nested topics, expand/collapse,
// selection, "+ Nuevo curso" button, per-item rename/delete menu.
// Drag/drop, inline rename, share/export are deferred to S3/S4.

window.App = window.App || {};
window.App.CoursesSidebar = (function () {
  "use strict";

  const { escHtml } = window.App.UI;

  // ── renderCourseTree(courses, STATE) → HTML string ────────────
  function renderCourseTree(courses, STATE) {
    STATE = STATE || window.STATE || {};
    const list = courses || [];
    let html = "";
    html += '<div class="col-title">📖 Cursos</div>';

    if (!list.length) {
      html += '<div class="empty-state" style="padding:12px;">'
        + '<span class="big">📚</span><br>'
        + '<span style="font-size:11px;">Sin cursos aún</span></div>';
    } else {
      for (const c of list) {
        html += _renderCourseItem(c, STATE);
      }
    }

    // "+ Nuevo curso" button + bottom spacer (for mobile scroll)
    html += '<div class="cs-new-row">'
      + '<button class="ht-btn cs-new-btn" id="create-course-btn">'
      + '📁 + Nuevo curso</button>'
      + '</div>'
      + '<div style="height:120px;flex-shrink:0;"></div>';

    return html;
  }

  // ── _renderCourseItem(course, STATE) ──────────────────────────
  function _renderCourseItem(c, STATE) {
    const isExpanded = c.id === STATE.expandedCourseId;
    const isActive = c.id === STATE.currentCourseId;
    const arrow = isExpanded ? "▼" : "▶";
    const safeTitle = escHtml(c.title || "Sin título").replace(/'/g, "\\'");
    const desc = escHtml(
      c.description
        || `${c.topics ? c.topics.length : 0} temas — click para ver`
    );
    const topics = (isExpanded && STATE._expandedCourseTopics)
      ? STATE._expandedCourseTopics : (c.topics || []);

    let html = `<div class="course-item ${isActive ? "active" : ""} `
      + `${isExpanded ? "expanded" : ""}" data-course-id="${c.id}">`
      + `<div class="ci-body">`
      + `<div class="ci-title">`
      + `<span class="course-arrow">${arrow}</span>`
      + `<span class="ci-title-text">${escHtml(c.title || "Sin título")}</span>`
      + `<span class="ci-block-count">${topics.length} temas</span>`
      + `<div class="topic-menu-wrap">`
      + `<button class="topic-menu-toggle" `
      + `onclick="event.stopPropagation();`
      + `this.nextElementSibling.classList.toggle('open')">⋮</button>`
      + `<div class="topic-menu">`
      + `<div class="topic-menu-item" onclick="event.stopPropagation();`
      + `this.closest('.topic-menu').classList.remove('open');`
      + `window.App.Courses.promptRenameCourse('${c.id}')">`
      + `✏️ Renombrar</div>`
      + `<div class="topic-menu-sep"></div>`
      + `<div class="topic-menu-item danger" onclick="event.stopPropagation();`
      + `this.closest('.topic-menu').classList.remove('open');`
      + `window.App.Courses.promptDeleteCourse('${c.id}', `
      + `'${safeTitle}')">🗑️ Borrar curso</div>`
      + `</div></div>`
      + `</div>` // ci-title
      + `<div class="ci-sub">${desc}</div>`
      + `</div></div>`; // ci-body, course-item

    if (isExpanded) {
      html += _renderCourseChildren(c.id, topics, STATE);
    }
    return html;
  }

  // ── _renderCourseChildren(courseId, topics, STATE) ───────────
  function _renderCourseChildren(courseId, topics, STATE) {
    let html = `<div class="course-children" data-course-id="${courseId}">`;
    if (!topics.length) {
      html += '<div class="empty-blocks">Sin temas aún</div>';
    } else {
      for (const t of topics) {
        html += _renderTopicItem(courseId, t, STATE);
      }
    }
    html += `<div class="add-topic-btn" data-course-id="${courseId}">`
      + `➕ Añadir tema</div>`;
    html += "</div>";
    return html;
  }

  // ── _renderTopicItem(courseId, topic, STATE) ─────────────────
  function _renderTopicItem(courseId, t, STATE) {
    const isActive = t.id === STATE.selectedTopicId;
    const safeTitle = escHtml(t.title || "Sin título").replace(/'/g, "\\'");
    return `<div class="topic-item ${isActive ? "active" : ""}" `
      + `data-topic-id="${t.id}" data-course-id="${courseId}">`
      + `<span class="topic-arrow">▶</span>`
      + `<span class="topic-title">${escHtml(t.title || "Sin título")}</span>`
      + `<div class="topic-menu-wrap">`
      + `<button class="topic-menu-toggle" `
      + `onclick="event.stopPropagation();`
      + `this.nextElementSibling.classList.toggle('open')">⋮</button>`
      + `<div class="topic-menu">`
      + `<div class="topic-menu-item" onclick="event.stopPropagation();`
      + `this.closest('.topic-menu').classList.remove('open');`
      + `window.App.Courses.promptRenameTopic('${courseId}','${t.id}')">`
      + `✏️ Renombrar</div>`
      + `<div class="topic-menu-sep"></div>`
      + `<div class="topic-menu-item danger" onclick="event.stopPropagation();`
      + `this.closest('.topic-menu').classList.remove('open');`
      + `window.App.Courses.promptDeleteTopic('${courseId}','${t.id}',`
      + `'${safeTitle}')">🗑️ Borrar tema</div>`
      + `</div></div>`
      + `</div>`;
  }

  // ── updateSelection(leftEl, STATE) — CSS-only selection update ─
  function updateSelection(leftEl, STATE) {
    STATE = STATE || window.STATE || {};
    leftEl.querySelectorAll(".course-item").forEach((el) => {
      const cid = Number(el.dataset.courseId);
      el.classList.toggle("active", cid === STATE.currentCourseId);
      el.classList.toggle("expanded", cid === STATE.expandedCourseId);
      const arrow = el.querySelector(".course-arrow");
      if (arrow) arrow.textContent = cid === STATE.expandedCourseId ? "▼" : "▶";
    });
    leftEl.querySelectorAll(".topic-item").forEach((el) => {
      el.classList.toggle(
        "active", Number(el.dataset.topicId) === STATE.selectedTopicId
      );
    });
  }

  // ── attachSidebarEvents(leftEl, callbacks) — delegated ────────
  // callbacks: { renderStudyflow, STATE }
  // All other actions (create/rename/delete) route through window.App.Courses
  // so this file stays focused on rendering.
  function attachSidebarEvents(leftEl, callbacks) {
    if (!leftEl || leftEl.dataset._csDelegated) return;
    leftEl.dataset._csDelegated = "1";
    leftEl._csCallbacks = callbacks || {};

    // 1) Create course / course click / topic click
    leftEl.addEventListener("click", async (e) => {
      // Create-course button
      const createBtn = e.target.closest("#create-course-btn");
      if (createBtn) {
        if (window.App.Courses && window.App.Courses.promptCreateCourse) {
          await window.App.Courses.promptCreateCourse();
        }
        return;
      }

      // Course row → expand/collapse + select
      const courseItem = e.target.closest(".course-item");
      if (courseItem && !e.target.closest(".topic-menu-wrap")) {
        if (window.App.Courses && window.App.Courses.handleCourseClick) {
          await window.App.Courses.handleCourseClick(
            Number(courseItem.dataset.courseId)
          );
        }
        return;
      }

      // Topic row → select topic
      const topicItem = e.target.closest(".topic-item");
      if (topicItem && !e.target.closest(".topic-menu-wrap")) {
        if (window.App.Courses && window.App.Courses.handleTopicClick) {
          await window.App.Courses.handleTopicClick(
            Number(topicItem.dataset.courseId),
            Number(topicItem.dataset.topicId)
          );
        }
        return;
      }
    });

    // 2) Add-topic button
    if (!leftEl.dataset._addTopicDelegated) {
      leftEl.dataset._addTopicDelegated = "1";
      leftEl.addEventListener("click", async (e) => {
        const btn = e.target.closest(".add-topic-btn");
        if (!btn) return;
        e.stopPropagation();
        const cid = Number(btn.dataset.courseId);
        if (window.App.Courses && window.App.Courses.promptAddTopic) {
          await window.App.Courses.promptAddTopic(cid);
        }
      });
    }

    // 3) Close any open menu when clicking elsewhere
    if (!leftEl.dataset._menuCloseDelegated) {
      leftEl.dataset._menuCloseDelegated = "1";
      document.addEventListener("click", () => {
        leftEl.querySelectorAll(".topic-menu.open").forEach((m) =>
          m.classList.remove("open")
        );
      });
    }
  }

  // ── Public API ───────────────────────────────────────────────
  return {
    renderCourseTree,
    attachSidebarEvents,
    updateSelection,
  };
})();

console.log("[Studyflow] courses-sidebar.js loaded");