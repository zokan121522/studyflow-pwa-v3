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
    addBlock, toggleBlockDone, moveBlock, updateBlock,
    // Favorites + course order (Issue #12)
    setFavorite, reorderCourses,
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

  // ── toggleFavorite(courseId) ────────────────────────────────
  // Issue #12. Flips the star, re-renders (which re-sorts the groups),
  // and puts the flag back if the server rejected the write — a star
  // that lies is worse than one that didn't move.
  async function toggleFavorite(courseId) {
    const list = await fetchCourses();
    const c = (list || []).find((x) => x.id === courseId);
    if (!c) return;
    const next = !c.is_favorite;
    c.is_favorite = next; // optimistic
    try {
      await setFavorite(courseId, next);
      await renderStudyflow();
      refreshDashboardFavorites();
    } catch (err) {
      c.is_favorite = !next; // revert
      refreshDashboardFavorites();
      alert("❌ No se pudo marcar como favorita: " + (err.message || err));
    }
  }

  // The dashboard strip mirrors the same flag. It lives in its own
  // module, so this is a soft dependency: during boot CoursesDashboard
  // may not be loaded yet, and a missing strip is not an error.
  function refreshDashboardFavorites() {
    if (window.App.CoursesDashboard) {
      window.App.CoursesDashboard.renderFavoritesRow();
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
    // Keep the current topic until we know better: nulling it up front made a
    // failed lookup drop updateCenter() into the course landing, so the click
    // looked like a no-op.
    const prevTopicId = s.selectedTopicId;
    s.selectedTopicId = null;
    try {
      const detail = await fetchCourseDetail(courseId);
      for (const t of (detail.topics || [])) {
        if ((t.blocks || []).some(b => b.id === blockId)) {
          s.selectedTopicId = t.id;
          break;
        }
      }
    } catch (err) {
      console.warn("[Courses] handleBlockClick: no se pudo resolver el tema", err);
    }
    if (s.selectedTopicId == null) s.selectedTopicId = prevTopicId;
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
    // Opening a topic also unfolds it in the nav. Otherwise you click a topic
    // in the body and the sidebar keeps it folded behind a ▶, so the blocks you
    // just opened look absent from the outline. The flag survives any re-render
    // (renderCourseTree reads _expandedTopics), and setTopicExpanded also flips
    // the live markup, so the nav is correct before the tree is rebuilt.
    if (window.App.CoursesSidebar && window.App.CoursesSidebar.setTopicExpanded) {
      window.App.CoursesSidebar.setTopicExpanded(topicId, true);
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
    // S5 — same treatment for the Quiz center nav (Resumen / Falladas).
    try {
      if (window.App.QuizCenter
          && typeof window.App.QuizCenter.renderNav === "function") {
        window.App.QuizCenter.renderNav();
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

  // ── _mountQuizBins(centerEl) — Issue #10 hook ─────────────────
  // Exercise blocks render a .sf-quiz-bin placeholder; App.QuizEmbed
  // fills it with the block's questions (quiz_questions.block_id).
  // Idempotent and safe to call on every topic re-render.
  function _mountQuizBins(centerEl) {
    if (!centerEl) return;
    const QE = window.App && window.App.QuizEmbed;
    if (!QE || typeof QE.mountBins !== "function") return;
    try { QE.mountBins(centerEl); }
    catch (err) { console.warn("[QuizEmbed.mountBins]", err); }
  }

  // ── _mountInteractiveBins(centerEl) — Issue #11 hook ──────────
  // Interactive blocks render a .sf-it-bin placeholder carrying the
  // embedded HTML as JSON (inert <script type="application/json">).
  // App.ContentBlocks.renderInteractive mounts it in a sandboxed
  // iframe with allow-scripts (opaque origin — no same-origin access,
  // no top-level navigation). Idempotent via dataset.mounted.
  function _mountInteractiveBins(centerEl) {
    if (!centerEl) return;
    const CB = window.App && window.App.ContentBlocks;
    if (!CB || typeof CB.renderInteractive !== "function") return;
    centerEl.querySelectorAll(".sf-it-bin").forEach((bin) => {
      if (bin.dataset.mounted === "1") return;
      bin.dataset.mounted = "1";
      try {
        const src = bin.querySelector(".sf-it-src");
        const html = src ? JSON.parse(src.textContent) : "";
        CB.renderInteractive(bin, html);
      } catch (err) {
        bin.innerHTML = `<div class="sf-empty">⚠️ Error cargando página web: ${escHtml(err.message || err)}</div>`;
      }
    });
  }

  // ── updateCenter() — lightweight: only the center panel ──────
  async function updateCenter() {
    const centerEl = document.getElementById("studyflow-center");
    if (!centerEl) return;
    const s = STATE();

    // Center views that take over the panel, routed BEFORE the
    // topic/course branches. Sidebar nav items (qs-nav-item [data-am-nav]
    // for the marketplace, [data-qc-view] for S5) set STATE._view and call
    // updateCenter().
    //   "addons" → AddonsManager (SA.2 marketplace catalog)
    //   "quiz"   → QuizCenter (S5 Resumen por Card / Preguntas Falladas)
    if (s._view === "quiz" && window.App.QuizCenter
        && typeof window.App.QuizCenter.renderView === "function") {
      await window.App.QuizCenter.renderView(centerEl);
    } else if (s._view === "addons" && window.App.AddonsManager
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
    // v2 parity — clicking a block in the sidebar narrows the center panel
    // to that single block. STATE.selectedBlockId is set by
    // handleBlockClick; clearing it (topic title, course landing) restores
    // the full list.
    const s = STATE();
    const focusId = s.selectedBlockId;
    const allBlocks = topic.blocks || [];
    const blocks = focusId
      ? allBlocks.filter((b) => b.id === focusId)
      : allBlocks;
    const blocksHtml = blocks.length
      ? blocks.map((b) => Blocks._renderBlockCard(b, courseId)).join("")
      : (focusId
        ? '<div class="empty-state"><span class="big">🔍</span><br>'
          + 'Ese bloque ya no existe en este tema.</div>'
        : '<div class="empty-state"><span class="big">📝</span><br>'
          + 'Este tema no tiene bloques aún.<br>'
          + 'Crea uno con “+ Añadir bloque”.</div>');
    // ➕ Añadir is a TOPIC-level bar: it renders on EVERY topic, empty or not,
    // right under the topic title. The old code only rendered it for empty
    // topics and pushed the create chips into each block's toolbar (ai.js),
    // which made the whole feature invisible on any normal topic.
    const addBar = Blocks._renderAddBar(courseId, topicId);
    // The fold-all control reads the whole topic, not the filtered view, so
    // "desplegar todo" from a single focused block would otherwise be a lie.
    const allCollapsed = allBlocks.length > 0 && allBlocks.every((b) => !!b.collapsed);

    centerEl.innerHTML = `
      <div class="sf-topic-detail" data-topic-id="${topicId}" data-course-id="${courseId}">
        <div class="sf-td-back" data-course-id="${courseId}">
          ← ${escHtml(course.title || "Curso")}
        </div>
        <h2 class="sf-td-title">${escHtml(topic.title || "Tema")}</h2>
        ${addBar}
        ${focusId
          ? `<button type="button" class="sf-td-show-all ht-btn"
               data-course-id="${courseId}" data-topic-id="${topicId}">
               ☰ Ver todos los bloques (${allBlocks.length})
             </button>`
          : (allBlocks.length > 1
            ? `<button type="button" class="sf-td-fold-all ht-btn"
                 data-course-id="${courseId}" data-topic-id="${topicId}"
                 title="Plegar o desplegar todos los bloques de golpe">
                 ${allCollapsed ? "☰ Desplegar todo" : "☰ Plegar todo"}
               </button>`
            : "")}
        ${topic.description
          ? `<div class="sf-td-desc md-view">${_renderMd(topic.description)}</div>`
          : ""}
        <div class="sf-td-blocks">${blocksHtml}</div>
      </div>
    `;

    // S3 handlers — edit / save / cancel / done / delete / add.
    Blocks._attachBlockHandlers(centerEl, courseId, topicId);
    // S4 handlers — collapse / up / down reorder.
    Blocks._attachCardHandlers(centerEl, courseId, topicId);

    // v2 parity — exit the single-block focus and show the whole topic again.
    const showAllBtn = centerEl.querySelector(".sf-td-show-all");
    if (showAllBtn) {
      showAllBtn.addEventListener("click", async () => {
        const st = STATE();
        st.selectedBlockId = null;
        await updateCenter();
      });
    }
    // Fold / unfold every block in the topic in one go. Goes through the same
    // PUT {collapsed} the per-card arrow uses, so the server stays the source
    // of truth and the flag survives a reload. Sequential on purpose: one
    // request per block, and a 13-block topic should not be 13 parallel writes
    // that can interleave with a collapse-all/expand-all double tap.
    const foldAllBtn = centerEl.querySelector(".sf-td-fold-all");
    if (foldAllBtn) {
      foldAllBtn.addEventListener("click", async () => {
        const cId = Number(foldAllBtn.dataset.courseId);
        const tId = Number(foldAllBtn.dataset.topicId);
        const cards = [...centerEl.querySelectorAll(".sf-block-card")];
        if (!cards.length) return;
        // Fold if anything is still open; unfold only when all are already
        // folded. Anything else is a coin toss the user did not ask for.
        const targetCollapsed = cards.some((c) => !c.classList.contains("is-collapsed"));
        foldAllBtn.disabled = true;
        try {
          for (const card of cards) {
            const bid = Number(card.dataset.blockId);
            if (!bid) continue;
            card.classList.toggle("is-collapsed", targetCollapsed);
            const arrow = card.querySelector(".sf-bc-collapse");
            if (arrow) arrow.textContent = targetCollapsed ? "▶" : "▼";
            try {
              await updateBlock(cId, bid, { collapsed: targetCollapsed });
            } catch (_) { /* one failure must not abort the rest */ }
          }
        } finally {
          foldAllBtn.disabled = false;
        }
        // Re-render so the button's own label flips to the other action.
        await updateCenter();
      });
    }
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
    // Issue #10 — exercise blocks mount their embedded quiz.
    _mountQuizBins(centerEl);
    // Issue #11 — interactive blocks mount their embedded web page.
    _mountInteractiveBins(centerEl);

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
  //
  // Ids arrive with two different types depending on the emitter: the API
  // returns `359` (number) so STATE holds a number, but the AI action buttons
  // carry `data-topic-id="359"` and read it back as a *string*. A strict
  // `!==` therefore never matches and the panel silently failed to re-render —
  // the new block only appeared after navigating away and back. Compare as
  // strings so both origins agree.
  function _sameId(a, b) {
    if (a == null || b == null) return false;
    return String(a) === String(b);
  }

  // ── _refreshSidebarNav() — rebuild ONLY the left tree ─────────
  // renderStudyflow() does sidebar + center + the addon/quiz navs, which is
  // far too much to run on every block mutation: it refetches the course
  // list and re-renders the marketplace panels, which visibly fights the
  // user while the AI is streaming results in.
  //
  // What the user needs after a block is inserted is for it to appear in the
  // outline. Two details make this non-obvious:
  //
  //  1. The tree does NOT read the block list off `fetchCourses()`. It reads
  //     `STATE._expandedCourseTopics`, which is only filled by
  //     `fetchCourseDetail()`. Refetching the course list alone therefore
  //     re-renders the tree with the SAME stale blocks — the outline looks
  //     like it refreshed but the inserted block is still missing.
  //  2. `fetchCourseDetail()` is cached, so the detail has to be invalidated
  //     first or we re-read the pre-insert snapshot.
  async function _refreshSidebarNav(courseId) {
    const leftEl = document.getElementById("studyflow-left");
    if (!leftEl || courseId == null) return;
    const s = STATE();
    // Only the open course carries blocks in the tree; refreshing other
    // courses would be wasted work on every mutation.
    if (!s.expandedCourseId || !_sameId(s.expandedCourseId, courseId)) return;
    try {
      if (typeof clearDetailCache === "function") clearDetailCache();
      const [courses, full] = await Promise.all([
        fetchCourses(),
        fetchCourseDetail(courseId),
      ]);
      if (!courses) return;
      s._expandedCourseTopics = (full && full.topics) || [];
      s._expandedCourseBlocks = (full && full.blocks) || [];
      const scrollTop = leftEl.scrollTop;
      leftEl.innerHTML = renderCourseTree(courses, s);
      // Re-rendering resets the scroll position; put it back so a long tree
      // does not jump to the top every time a block is inserted.
      leftEl.scrollTop = scrollTop;
      if (window.App.CoursesSidebar && window.App.CoursesSidebar.updateSelection) {
        window.App.CoursesSidebar.updateSelection(leftEl, s);
      }
    } catch (err) {
      console.error("[blocks-changed] sidebar refresh failed:", err);
    }
  }

  function _onBlocksChanged(e) {
    const detail = (e && e.detail) || {};
    const s = STATE();
    if (detail.courseId != null && !_sameId(s.currentCourseId, detail.courseId)) return;
    if (detail.topicId != null && !_sameId(s.selectedTopicId, detail.topicId)) return;
    // Fire-and-forget: don't block the event handler.
    //
    // The sidebar and the center are refreshed TOGETHER but independently:
    // the center renders the inserted block, while the tree is what makes it
    // discoverable. Only refreshing the center left the outline stale, so the
    // course looked unchanged until something else happened to re-render the
    // nav. Neither is awaited by the other — a slow or failing tree render
    // must not delay the block the user is waiting for.
    updateCenter().catch((err) => console.error("[blocks-changed]", err));
    _refreshSidebarNav(detail.courseId != null ? detail.courseId : s.currentCourseId);
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
    toggleFavorite,
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
    // Re-exportado desde CoursesAPI: el menú lateral de curso y de tema lo
    // invoca por onclick como window.App.Courses.addBlock. Sin esto,
    // "Añadir markdown" / "Añadir PDF" eran botones muertos (fallo silencioso).
    addBlock,
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