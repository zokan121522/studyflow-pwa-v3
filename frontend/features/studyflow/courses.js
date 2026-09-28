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
    // Blocks (S3)
    addBlock, toggleBlockDone, moveBlock,
  } = window.App.CoursesAPI;
  const {
    renderCourseTree, attachSidebarEvents, updateSelection,
  } = window.App.CoursesSidebar;
  const { _renderMd } = window.App.ContentBlocks;
  const Blocks = window.App.CoursesBlocks;

  function STATE() { return window.STATE; }

  // ── _inlineRenameCourse(courseId) — inline rename from sidebar ────────
  async function _inlineRenameCourse(courseId) {
    const span = document.querySelector(`.course-item[data-course-id="${courseId}"] .ci-title-text`);
    if (!span) return;
    const current = span.textContent;
    const input = document.createElement("input");
    input.type = "text";
    input.value = current;
    input.style.cssText = "font:inherit;padding:2px 4px;border:1px solid var(--primary);border-radius:2px;background:var(--bg);color:var(--text);width:100%;box-sizing:border-box;";
    span.replaceWith(input);
    input.focus();
    input.select();
    const finish = async (save) => {
      const newTitle = (input.value || "").trim();
      if (save && newTitle && newTitle !== current) {
        try {
          await renameCourse(courseId, newTitle);
          await renderStudyflow();
        } catch (err) {
          alert("❌ Error al renombrar: " + (err.message || err));
        }
      } else {
        await renderStudyflow();
      }
    };
    input.addEventListener("blur", () => finish(true));
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); input.blur(); }
      if (e.key === "Escape") { e.preventDefault(); finish(false); }
    });
  }

  // ── _inlineRenameTopic(courseId, topicId) — inline rename from sidebar ──
  async function _inlineRenameTopic(courseId, topicId) {
    const span = document.querySelector(`.topic-header[data-topic-id="${topicId}"] .topic-title, .topic-item[data-topic-id="${topicId}"] .topic-title`);
    if (!span) return;
    const current = span.textContent;
    const input = document.createElement("input");
    input.type = "text";
    input.value = current;
    input.style.cssText = "font:inherit;padding:2px 4px;border:1px solid var(--primary);border-radius:2px;background:var(--bg);color:var(--text);width:100%;box-sizing:border-box;";
    span.replaceWith(input);
    input.focus();
    input.select();
    const finish = async (save) => {
      const newTitle = (input.value || "").trim();
      if (save && newTitle && newTitle !== current) {
        try {
          await renameTopic(courseId, topicId, newTitle);
          await renderStudyflow();
        } catch (err) {
          alert("❌ Error al renombrar: " + (err.message || err));
        }
      } else {
        await renderStudyflow();
      }
    };
    input.addEventListener("blur", () => finish(true));
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); input.blur(); }
      if (e.key === "Escape") { e.preventDefault(); finish(false); }
    });
  }

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

  // ── promptEditDescription(courseId) ─────────────────────────
  // Edit (or clear) the course description shown under the title.
  async function promptEditDescription(courseId) {
    const list = await fetchCourses();
    const c = (list || []).find((x) => x.id === courseId);
    const current = c ? (c.description || "") : "";
    const next = (prompt("Descripción del curso (déjelo vacío para quitar):", current) || "").trim();
    if (next === current) return;
    try {
      await updateDescription(courseId, next);
      await renderStudyflow();
    } catch (err) {
      alert("❌ Error al editar la descripción: " + (err.message || err));
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

  // ── promptDeleteBlock(courseId, blockId) ──────────────────────
  async function promptDeleteBlock(courseId, blockId) {
    if (!confirm("¿Borrar este bloque?")) return;
    try {
      await window.App.CoursesAPI.deleteBlock(courseId, blockId);
      const evt = new CustomEvent("studyflow:blocks-changed", {
        detail: { courseId }
      });
      window.dispatchEvent(evt);
    } catch (err) {
      alert("❌ Error al borrar bloque: " + (err.message || err));
    }
  }

  // ── _updateCourseProgress(courseId) — actualiza barra de progreso ──
  function _updateCourseProgress(courseId) {
    const item = document.querySelector(`.course-item[data-course-id="${courseId}"]`);
    if (!item) return;
    const topics = STATE()._expandedCourseTopics || [];
    let doneCount = 0, totalCount = 0;
    for (const t of topics) {
      for (const b of (t.blocks || [])) {
        totalCount++;
        if (b.done) doneCount++;
      }
    }
    const pct = totalCount > 0 ? Math.round((doneCount / totalCount) * 100) : 0;
    const fill = item.querySelector('.ci-progress-fill');
    if (fill) fill.style.width = pct + '%';
    const lbl = item.querySelector('.ci-block-count');
    if (lbl) lbl.textContent = `${topics.length} temas · ${doneCount}/${totalCount}`;
  }

  // ── _toggleBlockDone(courseId, blockId) — called from sidebar checkbox ────
  async function _toggleBlockDone(courseId, blockId) {
    try {
      // Capture topics BEFORE API call (toggleBlockDone clears STATE._expandedCourseTopics via clearDetailCache)
      const s = STATE();
      const capturedTopics = (s._expandedCourseTopics || []).map(t => ({
        ...t,
        blocks: (t.blocks || []).map(b => ({ ...b }))
      }));

      const newDone = await toggleBlockDone(courseId, blockId);

      // Restore topics with updated block status
      for (const t of capturedTopics) {
        const b = (t.blocks || []).find(b => b.id === Number(blockId));
        if (b) { b.done = newDone; break; }
      }
      s._expandedCourseTopics = capturedTopics;

      _updateCourseProgress(courseId);

      // Also update the checkbox visual state in the sidebar
      const checkbox = document.querySelector(
        `.block-item[data-block-id="${blockId}"] input[type="checkbox"]`
      );
      if (checkbox) checkbox.checked = newDone;

      // Toggle strikethrough on the block item title
      const blockItem = document.querySelector(
        `.block-item[data-block-id="${blockId}"]`
      );
      if (blockItem) blockItem.classList.toggle('is-done', newDone);

      // Dispatch event so center panel refreshes if this topic is active
      const evt = new CustomEvent("studyflow:blocks-changed", {
        detail: { courseId }
      });
      window.dispatchEvent(evt);
      return newDone;
    } catch (err) {
      alert("❌ Error al cambiar estado: " + (err.message || err));
      // Revert checkbox on error
      const checkbox = document.querySelector(
        `.block-item[data-block-id="${blockId}"] input[type="checkbox"]`
      );
      if (checkbox) checkbox.checked = !checkbox.checked;
      // Revert strikethrough on error
      const blockItemRevert = document.querySelector(
        `.block-item[data-block-id="${blockId}"]`
      );
      if (blockItemRevert) blockItemRevert.classList.toggle('is-done', !checkbox.checked);
    }
  }

  // ── handleBlockClick(courseId, blockId) — select block in sidebar ──────
  async function handleBlockClick(courseId, blockId) {
    const s = STATE();
    s._view = null;
    s.currentCourseId = courseId;
    s.selectedBlockId = blockId;
    s.selectedTopicId = null;
    // Find the topic containing this block to update sidebar selection
    const detail = await fetchCourseDetail(courseId);
    for (const t of (detail.topics || [])) {
      if ((t.blocks || []).some(b => b.id === blockId)) {
        s.selectedTopicId = t.id;
        break;
      }
    }
    await updateCenter();
  }

  // ── _showMoveDialog(blockId, courseId, topicId) — minimal move dialog ────
  async function _showMoveDialog(blockId, courseId, topicId) {
    const detail = await fetchCourseDetail(courseId);
    const topics = (detail.topics || []).filter(t => t.id !== Number(topicId) || !topicId);
    if (!topics.length) {
      alert("No hay otros temas para mover el bloque.");
      return;
    }
    const options = topics.map(t => `<option value="${t.id}">${escHtml(t.title)}</option>`).join("");
    const html = `<select id="move-target-topic">${options}</select>
      <label><input type="checkbox" id="move-to-top"> Insertar al principio</label>`;
    const result = await new Promise(resolve => {
      const overlay = document.createElement("div");
      overlay.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center;z-index:10000";
      overlay.innerHTML = `<div style="background:var(--surface);padding:16px;border-radius:8px;min-width:280px;">
        <h4 style="margin:0 0 12px;">🗂️ Mover bloque</h4>
        ${html}
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px;">
          <button class="ht-btn-ghost" id="move-cancel">Cancelar</button>
          <button class="ht-btn" id="move-ok">Mover</button>
        </div>
      </div>`;
      document.body.appendChild(overlay);
      overlay.querySelector("#move-ok").onclick = () => {
        const targetId = Number(overlay.querySelector("#move-target-topic").value);
        const toTop = overlay.querySelector("#move-to-top").checked;
        document.body.removeChild(overlay);
        resolve({ targetId, toTop });
      };
      overlay.querySelector("#move-cancel").onclick = () => {
        document.body.removeChild(overlay);
        resolve(null);
      };
    });
    if (!result) return;
    try {
      await moveBlock(blockId, { target_topic_id: result.targetId, index: result.toTop ? 0 : -1 });
      const evt = new CustomEvent("studyflow:blocks-changed", { detail: { courseId } });
      window.dispatchEvent(evt);
    } catch (err) {
      alert("❌ Error al mover: " + (err.message || err));
    }
  }

  // ── _enterEditMode(blockId) — open block for editing in center ─────────
  async function _enterEditMode(blockId) {
    // The center panel will handle edit mode when the block is rendered.
    // We just need to ensure the topic is selected so the block is visible.
    // The caller (sidebar menu) should have already set selectedBlockId.
  }

  // ── _inlineRenameBlockTitle(courseId, blockId) — inline rename from sidebar ─
  async function _inlineRenameBlockTitle(courseId, blockId) {
    const span = document.querySelector(`.block-item[data-block-id="${blockId}"] .bi-title`);
    if (!span) return;
    const current = span.textContent;
    const input = document.createElement("input");
    input.type = "text";
    input.value = current;
    input.style.cssText = "font:inherit;padding:2px 4px;border:1px solid var(--primary);border-radius:2px;background:var(--bg);color:var(--text);width:100%;box-sizing:border-box;";
    span.replaceWith(input);
    input.focus();
    input.select();
    const finish = async (save) => {
      const newTitle = (input.value || "").trim();
      if (save && newTitle && newTitle !== current) {
        try {
          await window.App.CoursesAPI.updateBlock(courseId, blockId, { title: newTitle });
          const evt = new CustomEvent("studyflow:blocks-changed", { detail: { courseId } });
          window.dispatchEvent(evt);
        } catch (err) {
          alert("❌ Error al renombrar: " + (err.message || err));
        }
      } else {
        // Revert on cancel or no change
        await renderStudyflow();
      }
    };
    input.addEventListener("blur", () => finish(true));
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); input.blur(); }
      if (e.key === "Escape") { e.preventDefault(); finish(false); }
    });
  }

  // ── handleCourseClick(courseId) — select + expand ────────────
  async function handleCourseClick(courseId) {
    const s = STATE();
    // SA.2 — leaving the marketplace view re-enters the normal sidebar.
    s._view = null;
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
    // SA.2 — leaving the marketplace view re-enters the topic detail.
    s._view = null;
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
    // SA.2 — the marketplace nav lives in the #studyflow-left-stats
    // slot we just emitted. Append it after every full rebuild so it
    // survives repaints (matches v2 addons-marketplace.js pattern).
    try {
      if (window.App.AddonsManager
          && typeof window.App.AddonsManager.renderMarketplaceNav === "function") {
        window.App.AddonsManager.renderMarketplaceNav();
      }
    } catch (_) { /* nav is optional cosmetic */ }
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

    // SA.2 — Marketplace view (AddonsManager). Routes BEFORE the
    // topic/course branches so the catalog takes over the center
    // panel when STATE._view === "addons". Sidebar nav (qs-nav-item
    // [data-am-nav]) sets STATE._view and calls updateCenter().
    if (s._view === "addons" && window.App.AddonsManager
        && typeof window.App.AddonsManager.renderView === "function") {
      await window.App.AddonsManager.renderView(centerEl);
    } else if (s.selectedTopicId && s.currentCourseId) {
      await _renderTopicDetail(centerEl, s.currentCourseId, s.selectedTopicId);
    } else if (s.currentCourseId) {
      await _renderCourseLanding(centerEl, s.currentCourseId);
    } else {
      centerEl.innerHTML =
        '<div class="empty-state"><span class="big">📖</span><br>'
        + 'Selecciona un curso del panel izquierdo o crea uno nuevo.</div>';
    }
    // Sub-phase SA — defensive addon gate. Lets S5 (quiz), S6
    // (flashcards) and S9 (playground) plug in their stats panels
    // by registering App.QuizStats / FlashcardsStats / PlaygroundStats
    // modules. If App.Addons isn't loaded yet (script order) or the
    // addon is disabled / module missing, the hook is a no-op — no
    // console spam, updateCenter never breaks over addons.
    await _renderAddonSections(centerEl, s.currentCourseId, s.selectedTopicId);
  }

  // ── _renderAddonSections() — forward-declared hook for S5/S6/S9 ─
  // Each entry: {slug, render()} → render() is a thin call into the
  // addon module's optional renderer (`window.App.QuizStats?.render`
  // and friends). The gate is silent on missing pieces so S5/S6/S9
  // land without touching this file again.
  async function _renderAddonSections(centerEl, courseId, topicId) {
    if (!centerEl) return;
    const Addons = window.App.Addons;
    if (!Addons || typeof Addons.isEnabled !== "function") return;

    const hooks = [
      { slug: "quiz",
        render: () => window.App.QuizStats
          && typeof window.App.QuizStats.render === "function"
          && window.App.QuizStats.render(centerEl, courseId, topicId) },
      { slug: "flashcards",
        render: () => window.App.FlashcardsStats
          && typeof window.App.FlashcardsStats.render === "function"
          && window.App.FlashcardsStats.render(centerEl, courseId, topicId) },
      { slug: "jsplayground",
        render: () => window.App.PlaygroundStats
          && typeof window.App.PlaygroundStats.render === "function"
          && window.App.PlaygroundStats.render(centerEl, courseId, topicId) },
    ];

    for (const h of hooks) {
      try {
        if (await Addons.isEnabled(h.slug)) h.render();
      } catch (_) { /* defensive: never break updateCenter */ }
    }
  }

  // ── _renderCourseLanding(centerEl, courseId) ─────────────────
  // Course title + description + grid of topic cards. Each card opens
  // the topic detail (read-only). Cards show ONLY the topic title and
  // its block counter (v2 parity) — blocks render inside the topic detail.
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
      return `<div class="sf-cl-card" data-topic-id="${t.id}" data-course-id="${courseId}">
        <div class="sf-cl-card-head">
          <span class="sf-cl-card-icon">📁</span>
          <span class="sf-cl-card-title">${escHtml(t.title || "Sin título")}</span>
          <span class="sf-cl-card-count">${blocks.length} bloques</span>
        </div>
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
  // SF-Card wrapper around the S3 block markup). The "+ Añadir bloque"
  // bar is rendered BEFORE .sf-td-blocks so new blocks appear at the top.
  // Hook: addons (OpenCode/NotebookLM) can inject their own add-bar items
  // by listening for the `studyflow:add-bar-ready` event on the bar element.
  // Edit / save / cancel / done / delete actions are delegated by
  // CoursesBlocks._attachBlockHandlers. Up / down reorder + collapse are
  // delegated by CoursesBlocks._attachCardHandlers. The per-topic notes
  // drawer is mounted via App.CoursesNotes.render().
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
    // v2 parity: the global ➕ Añadir bar only renders on EMPTY topics.
    // On non-empty topics the create chips live inside each block's
    // unified toolbar (ai.js ➕ Añadir group, below NotebookLM/OpenZen).
    const addBar = blocks.length ? "" : Blocks._renderAddBar(courseId, topicId);

    centerEl.innerHTML = `
      <div class="sf-topic-detail">
        <div class="sf-td-back" data-course-id="${courseId}">
          ← ${escHtml(course.title || "Curso")}
        </div>
        <h2 class="sf-td-title">${escHtml(topic.title || "Tema")}</h2>
        ${topic.description
          ? `<div class="sf-td-desc md-view">${_renderMd(topic.description)}</div>`
          : ""}
        ${addBar}
        <div class="sf-td-blocks">${blocksHtml}</div>
      </div>
    `;

    // S3 handlers — edit / save / cancel / done / delete / add.
    Blocks._attachBlockHandlers(centerEl, courseId, topicId);
    // S4 handlers — collapse / up / down reorder.
    Blocks._attachCardHandlers(centerEl, courseId, topicId);
    // Phase 7.7 — AI ✨ toolbar: bind [data-ai-action] buttons + usage
    // counters. Idempotent (AI module re-binds remove+add on each render).
    if (window.App.AI && typeof window.App.AI.initSectionEvents === "function") {
      try {
        window.App.AI.initSectionEvents(centerEl);
      } catch (_) { /* AI toolbar is optional progressive enhancement */ }
    }
    // S4 — per-topic notes drawer.
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
    promptEditDescription,
    promptDeleteCourse,
    promptAddTopic,
    promptRenameTopic,
    promptDeleteTopic,
    promptDeleteBlock,
    handleCourseClick,
    handleTopicClick,
    handleBlockClick,
    _toggleBlockDone,
    _showMoveDialog,
    _enterEditMode,
    _inlineRenameBlockTitle,
    _inlineRenameCourse,
    _inlineRenameTopic,
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