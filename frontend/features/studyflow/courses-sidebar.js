// ─── Courses Sidebar — HTML render + delegated events (S1 slice) ───
// Namespace: window.App.CoursesSidebar
// Dependencies: window.App.UI (escHtml), window.App.CoursesAPI,
//               window.App.Courses (renderStudyflow, callbacks).
//
// SCOPE (this slice): course list with nested topics, expand/collapse,
// selection, "+ Nuevo curso" button, per-item rename/delete menu.
// Topic expansion, block tree with checkbox/progress/menu.
// Drag/drop, inline rename, share/export are deferred to S3/S4.

window.App = window.App || {};
window.App.CoursesSidebar = (function () {
  "use strict";

  const { escHtml } = window.App.UI;

  // ── Block type → icon mapping (mirrors v2 typeIcons) ───────────
  const TYPE_ICONS = {
    content: "📄", exercise: "❓", markdown: "📝",
    "pdf-ref": "📕", youtube: "▶️", image: "🖼",
    separator: "➖", interactive: "🌐"
  };

  function getBlockIcon(block) {
    if (TYPE_ICONS[block.type]) return TYPE_ICONS[block.type];
    // content-type title prefixes from v2 AI blocks
    if (block.type === "content" && block.title) {
      if (block.title.startsWith("🎵 ")) return "🎵";
      if (block.title.startsWith("📊 ")) return "📊";
      if (block.title.startsWith("🤖 ")) return "🤖";
      if (block.title.startsWith("✨ ")) return "✨";
      if (block.title.startsWith("🌙 ")) return "🌙";
    }
    return "📄";
  }

  // ── renderCourseTree(courses, STATE) → HTML string ────────────
  function renderCourseTree(courses, STATE) {
    STATE = STATE || window.STATE || {};
    const list = courses || [];
    let html = "";
    // SA.2 (v2 pattern) — stats container FIRST, before the «📖 Cursos»
    // title. Addons Marketplace / quiz stats / flashcards navs append
    // their own .sf-stats-section into this slot via renderMarketplaceNav
    // and friends (see addons-marketplace.js / quiz.js).
    html += '<div id="studyflow-left-stats"></div>';
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
    const topics = (isExpanded && STATE._expandedCourseTopics && STATE._expandedCourseTopics.length)
      ? STATE._expandedCourseTopics : (c.topics || []);

    // Progress: done/total blocks across all topics (only when expanded)
    let doneCount = 0, totalCount = 0, topicCount = 0;
    if (isExpanded) {
      topicCount = topics.length;
      for (const t of topics) {
        const blocks = t.blocks || [];
        totalCount += blocks.length;
        doneCount += blocks.filter(b => b.done).length;
      }
    }
    const pct = totalCount > 0 ? (doneCount / totalCount * 100) : 0;
    const countLabel = isExpanded
      ? `${topicCount} temas · ${doneCount}/${totalCount}`
      : `${c.topics ? c.topics.length : 0} temas`;

    let html = `<div class="course-item ${isActive ? "active" : ""} `
      + `${isExpanded ? "expanded" : ""}" data-course-id="${c.id}">`
      + `<div class="ci-body">`
      + `<div class="ci-title">`
      + `<span class="course-arrow">${arrow}</span>`
      + `<span class="ci-title-text">${escHtml(c.title || "Sin título")}</span>`
      + `<span class="ci-block-count">${countLabel}</span>`
      + `<div class="topic-menu-wrap">`
      + `<button class="topic-menu-toggle" `
      + `onclick="event.stopPropagation();`
      + `this.nextElementSibling.classList.toggle('open')">⋮</button>`
      + `<div class="topic-menu">`
      + `<div class="topic-menu-item" onclick="event.stopPropagation();`
      + `this.closest('.topic-menu').classList.remove('open');`
      + `window.App.Courses.promptRenameCourse('${c.id}')">`
      + `✏️ Renombrar</div>`
      + `<div class="topic-menu-item" onclick="event.stopPropagation();`
      + `this.closest('.topic-menu').classList.remove('open');`
      + `window.App.Courses.promptEditDescription('${c.id}')">`
      + `📝 Editar descripción</div>`
      + `<div class="topic-menu-sep"></div>`
      + `<div class="topic-menu-item" onclick="event.stopPropagation();`
      + `this.closest('.topic-menu').classList.remove('open');`
      + `window.App.Courses.addBlock('${c.id}', {type:'markdown',title:'Nuevo Markdown',content:''})`
      + `.then(id=>{window.App.Courses._enterEditMode(id);window.App.Courses.renderStudyflow()})">`
      + `📝 Añadir markdown</div>`
      + `<div class="topic-menu-item" onclick="event.stopPropagation();`
      + `this.closest('.topic-menu').classList.remove('open');`
      + `window.App.Courses.addBlock('${c.id}', {type:'pdf-ref',title:'Nuevo PDF',url:''})`
      + `.then(id=>{window.App.Courses._enterEditMode(id);window.App.Courses.renderStudyflow()})">`
      + `📕 Añadir PDF</div>`
      + `<div class="topic-menu-sep"></div>`
      + `<div class="topic-menu-item danger" onclick="event.stopPropagation();`
      + `this.closest('.topic-menu').classList.remove('open');`
      + `window.App.Courses.promptDeleteCourse('${c.id}', `
      + `'${safeTitle}')">🗑️ Borrar curso</div>`
      + `</div></div>`
      + `</div>` // ci-title
      + `${isExpanded ? `<div class="ci-progress-bar"><div class="ci-progress-fill" style="width:${pct}%"></div></div>` : ''}`
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
      html += _renderTopicTree(topics, courseId, STATE);
    }
    html += `<div class="add-topic-btn" data-course-id="${courseId}">`
      + `➕ Añadir tema</div>`;
    html += "</div>";
    return html;
  }

  // ── _renderTopicTree(topics, courseId, STATE) — Topic tree with blocks ────
  function _renderTopicTree(topics, courseId, STATE) {
    let html = `<div class="course-topics" data-expanded-course="${courseId}">`;
    for (let i = 0; i < topics.length; i++) {
      const t = topics[i];
      const isTopicExpanded = STATE._expandedTopics?.[t.id] === true;
      const tArrow = isTopicExpanded ? "▼" : "▶";
      const safeTitle = escHtml(t.title || "Sin título").replace(/'/g, "\\'");
      const blocks = t.blocks || [];
      const tDone = blocks.filter(b => b.done).length;
      const tTotal = blocks.length;

      html += `<div class="topic-item" data-topic-id="${t.id}" data-course-id="${courseId}" data-topic-idx="${i}" draggable="true">
        <div class="topic-header">
          <span class="topic-arrow">${tArrow}</span>
          <span class="topic-title">${escHtml(t.title || "Sin título")}</span>
          <span class="topic-count">${tDone}/${tTotal}</span>
          <div class="topic-menu-wrap">
            <button class="topic-menu-toggle" onclick="event.stopPropagation();this.nextElementSibling.classList.toggle('open')">⋮</button>
            <div class="topic-menu">
              <div class="topic-menu-item" onclick="event.stopPropagation();this.closest('.topic-menu').classList.remove('open');window.App.Courses.promptRenameTopic('${courseId}','${t.id}')">✏️ Renombrar</div>
              <div class="topic-menu-sep"></div>
              <div class="topic-menu-item" onclick="event.stopPropagation();this.closest('.topic-menu').classList.remove('open');window.App.Courses.addBlock('${courseId}',{type:'markdown',title:'Nuevo Markdown',content:'',topic_id:${t.id}}).then(id=>{window.App.Courses._enterEditMode(id);window.App.Courses.renderStudyflow()})">📝 Añadir markdown</div>
              <div class="topic-menu-item" onclick="event.stopPropagation();this.closest('.topic-menu').classList.remove('open');window.App.Courses.addBlock('${courseId}',{type:'pdf-ref',title:'Nuevo PDF',url:'',topic_id:${t.id}}).then(id=>{window.App.Courses._enterEditMode(id);window.App.Courses.renderStudyflow()})">📕 Añadir PDF</div>
              <div class="topic-menu-item" onclick="event.stopPropagation();this.closest('.topic-menu').classList.remove('open');window.App.Courses.addBlock('${courseId}',{type:'separator',title:'',content:'',topic_id:${t.id}}).then(async id=>{await window.App.Courses.renderStudyflow();window.App.Courses._inlineRenameBlockTitle('${courseId}',id)})">➖ Añadir separador</div>
              <div class="topic-menu-sep"></div>
              <div class="topic-menu-item danger" onclick="event.stopPropagation();this.closest('.topic-menu').classList.remove('open');window.App.Courses.promptDeleteTopic('${courseId}','${t.id}','${safeTitle}')">🗑️ Borrar tema</div>
            </div>
          </div>
        </div>
        <div class="topic-blocks" style="display:${isTopicExpanded ? 'block' : 'none'}">
          ${blocks.length
            ? blocks.map((b, bIdx) => _renderBlockItem(courseId, t.id, b, bIdx, STATE)).join('')
            : '<div class="empty-blocks">Sin bloques aún</div>'}
        </div>
      </div>`;
    }
    html += `</div>`;
    return html;
  }

  // ── _renderBlockItem(courseId, topicId, block, idx, STATE) ───────────
  function _renderBlockItem(courseId, topicId, b, idx, STATE) {
    const isActive = b.id === STATE.selectedBlockId;
    const icon = getBlockIcon(b);
    const checked = b.done ? "checked" : "";
    const safeBlockTitle = escHtml(b.title || "Sin título").replace(/'/g, "\\'");
    const isSep = b.type === "separator";
    const sepCls = isSep ? ` separator${(b.title || "").trim() ? " has-label" : " no-label"}` : "";
    const dblClick = isSep
      ? `ondblclick="event.stopPropagation();window.App.Courses._inlineRenameBlockTitle('${courseId}','${b.id}')" title="Doble clic para editar"`
      : "";

    return `<div class="block-item ${isActive ? "active" : ""}${sepCls}" data-block-id="${b.id}" data-course-id="${courseId}" data-topic-id="${topicId}" data-block-idx="${idx}" draggable="true">
      <label class="bi-check" onclick="event.stopPropagation()">
        <input type="checkbox" ${checked} onchange="window.App.Courses._toggleBlockDone('${courseId}','${b.id}')">
      </label>
      <span class="bi-icon">${icon}</span>
      <span class="bi-title" ${dblClick}>${escHtml(isSep ? (b.title || "") : (b.title || "Sin título"))}</span>
      <div class="topic-menu-wrap">
        <button class="topic-menu-toggle" onclick="event.stopPropagation();this.nextElementSibling.classList.toggle('open')">⋮</button>
        <div class="topic-menu">
          ${isSep ? `<div class="topic-menu-item" onclick="event.stopPropagation();this.closest('.topic-menu').classList.remove('open');window.App.Courses._inlineRenameBlockTitle('${courseId}','${b.id}')">✏️ Editar título</div>` : ""}
          <div class="topic-menu-item" onclick="event.stopPropagation();this.closest('.topic-menu').classList.remove('open');window.App.Courses._showMoveDialog('${b.id}','${courseId}','${topicId}')">🗂️ Mover bloque</div>
          <div class="topic-menu-sep"></div>
          <div class="topic-menu-item danger" onclick="event.stopPropagation();this.closest('.topic-menu').classList.remove('open');window.App.Courses.promptDeleteBlock('${courseId}','${b.id}')">🗑️ Borrar bloque</div>
        </div>
      </div>
    </div>`;
  }

  // ── _renderTopicItem(courseId, topic, STATE) — kept for flat blocks fallback ────
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
    // Topic headers (active class on header)
    leftEl.querySelectorAll(".topic-header").forEach((header) => {
      const topicItem = header.closest(".topic-item");
      if (topicItem) {
        header.classList.toggle("active", topicItem.dataset.topicId === String(STATE.selectedTopicId));
      }
    });
    // Block items
    leftEl.querySelectorAll(".block-item").forEach((el) => {
      el.classList.toggle("active", el.dataset.blockId === String(STATE.selectedBlockId));
    });
    // Topic block containers visibility (expand/collapse)
    leftEl.querySelectorAll(".topic-item").forEach((item) => {
      const tid = item.dataset.topicId;
      const isExpanded = STATE._expandedTopics?.[tid] === true;
      const blocksDiv = item.querySelector(".topic-blocks");
      if (blocksDiv) blocksDiv.style.display = isExpanded ? "block" : "none";
      const arrow = item.querySelector(".topic-arrow");
      if (arrow) arrow.textContent = isExpanded ? "▼" : "▶";
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

    // 1) Create course / course click / topic header click / block click
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

      // Topic header → expand/collapse or select topic
      const topicHeader = e.target.closest(".topic-header");
      if (topicHeader && !e.target.closest(".topic-menu-wrap")) {
        e.stopPropagation();
        const topicItem = topicHeader.closest(".topic-item");
        const courseId = Number(topicItem.dataset.courseId);
        const topicId = Number(topicItem.dataset.topicId);

        // Click on topic title → select topic
        if (e.target.closest(".topic-title")) {
          if (window.App.Courses && window.App.Courses.handleTopicClick) {
            await window.App.Courses.handleTopicClick(courseId, topicId);
          }
          return;
        }

        // Click elsewhere on header → toggle expand/collapse
        const s = window.STATE || {};
        s._expandedTopics = s._expandedTopics || {};
        const expanded = s._expandedTopics[topicId] === true;
        s._expandedTopics[topicId] = !expanded;
        const blocksDiv = topicItem.querySelector(".topic-blocks");
        if (blocksDiv) blocksDiv.style.display = !expanded ? "block" : "none";
        const arrow = topicItem.querySelector(".topic-arrow");
        if (arrow) arrow.textContent = !expanded ? "▼" : "▶";
        return;
      }

      // Block item → select block (not on menu, checkbox, drag handle)
      const blockItem = e.target.closest(".block-item");
      if (blockItem
        && !e.target.closest(".topic-menu-wrap")
        && !e.target.closest(".bi-check")
        && !e.target.closest(".bi-drag-handle")) {
        const courseId = Number(blockItem.dataset.courseId);
        const blockId = Number(blockItem.dataset.blockId);
        if (window.App.Courses && window.App.Courses.handleBlockClick) {
          await window.App.Courses.handleBlockClick(courseId, blockId);
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

    // 4) Drag & drop reorder — topics and blocks (V3-DND).
    // Topics → POST /topics/reorder {topic_ids:[...]}; Blocks →
    // PATCH /courses/:id/blocks {blocks:[...]} (same-topic) or
    // POST /courses/blocks/:id/move (cross-topic). Dragged items
    // are the whole .topic-item / .block-item (draggable=true).
    {
      let dragSrc = null; // {type:'topic'|'block', courseId, id, topicId}

      leftEl.addEventListener("dragstart", (e) => {
        // NOTE: .block-item lives INSIDE .topic-item, so the block check
        // MUST come first — otherwise every block drag is captured as a
        // topic drag (blocks would reorder topics or silently no-op).
        const block = e.target.closest(".block-item");
        const topic = block ? null : e.target.closest(".topic-item");
        if (block) {
          dragSrc = {
            type: "block",
            courseId: Number(block.dataset.courseId),
            topicId: Number(block.dataset.topicId),
            id: Number(block.dataset.blockId),
          };
          e.dataTransfer.effectAllowed = "move";
          e.dataTransfer.setData("text/plain", String(dragSrc.id));
          block.classList.add("dragging");
        } else if (topic) {
          dragSrc = {
            type: "topic",
            courseId: Number(topic.dataset.courseId),
            id: Number(topic.dataset.topicId),
          };
          e.dataTransfer.effectAllowed = "move";
          e.dataTransfer.setData("text/plain", String(dragSrc.id));
          topic.classList.add("dragging");
        }
      });

      leftEl.addEventListener("dragover", (e) => {
        if (!dragSrc) return;
        e.preventDefault(); // allow drop
        e.dataTransfer.dropEffect = "move";
        // Clear previous highlights
        leftEl.querySelectorAll(".drag-over").forEach((el) =>
          el.classList.remove("drag-over")
        );
        const t = e.target.closest(
          dragSrc.type === "topic" ? ".topic-item" : ".block-item, .topic-item"
        );
        if (t) {
          // A block dropped on its own collapsed topic header is a no-op —
          // don't highlight it as a valid target.
          const sameTopic = dragSrc.type === "block"
            && t.classList.contains("topic-item")
            && Number(t.dataset.topicId) === dragSrc.topicId;
          if (!sameTopic) t.classList.add("drag-over");
        }
      });

      leftEl.addEventListener("dragleave", (e) => {
        if (e.target.closest(".drag-over")) {
          e.target.closest(".drag-over").classList.remove("drag-over");
        }
      });

      leftEl.addEventListener("drop", async (e) => {
        if (!dragSrc) return;
        e.preventDefault();
        e.stopPropagation();
        const src = dragSrc;
        const over = e.target.closest(
          src.type === "topic" ? ".topic-item" : ".block-item, .topic-item"
        );
        if (!over) return _cleanupDrag();
        const callbacks = leftEl._csCallbacks || {};
        try {
          if (src.type === "topic") {
            const overId = Number(over.dataset.topicId);
            if (overId === src.id) return _cleanupDrag();
            // Rebuild the ordered topic_ids of THIS course from the DOM.
            const ids = [...leftEl.querySelectorAll(
              `.course-topics[data-expanded-course="${src.courseId}"] .topic-item`
            )].map((el) => Number(el.dataset.topicId));
            const fromIdx = ids.indexOf(src.id);
            const toIdx = ids.indexOf(overId);
            if (fromIdx < 0 || toIdx < 0) return _cleanupDrag();
            ids.splice(fromIdx, 1);
            ids.splice(toIdx, 0, src.id);
            await window.App.CoursesAPI.reorderTopics(src.courseId, ids);
          } else {
            const overBlock = over.classList.contains("block-item");
            if (overBlock && Number(over.dataset.blockId) === src.id) {
              return _cleanupDrag();
            }
            const overTopic = Number(over.dataset.topicId);
            if (overTopic === src.topicId) {
              if (!overBlock) return _cleanupDrag(); // own collapsed header
              // Same-topic reorder — order all blocks of the topic.
              const ids = [...leftEl.querySelectorAll(
                `.block-item[data-topic-id="${src.topicId}"]`
              )].map((el) => Number(el.dataset.blockId));
              const fromIdx = ids.indexOf(src.id);
              const toIdx = ids.indexOf(Number(over.dataset.blockId));
              if (fromIdx < 0 || toIdx < 0) return _cleanupDrag();
              ids.splice(fromIdx, 1);
              ids.splice(toIdx, 0, src.id);
              await window.App.CoursesAPI.reorderBlocks(src.courseId, ids);
            } else {
              // Cross-topic move — into the target topic.
              let tIdx;
              if (overBlock) {
                // Position within the target topic's block list.
                tIdx = [...over.parentElement.querySelectorAll(
                  ".block-item"
                )].indexOf(over);
              } else {
                // Dropped on the topic header (collapsed target) →
                // append at the end of the destination topic. Blocks of
                // collapsed topics are still in the DOM, just hidden, so
                // their count gives the insertion index.
                tIdx = leftEl.querySelectorAll(
                  `.block-item[data-topic-id="${overTopic}"]`
                ).length;
              }
              await window.App.CoursesAPI.moveBlock(src.id, {
                target_topic_id: overTopic,
                index: tIdx,
              });
              // Expand the destination topic so the user can see the move.
              const s = window.STATE || {};
              s._expandedTopics = s._expandedTopics || {};
              s._expandedTopics[overTopic] = true;
            }
          }
          if (callbacks.renderStudyflow) await callbacks.renderStudyflow();
        } catch (err) {
          console.error("[Sidebar] drag&drop failed:", err);
          alert("❌ Error al reordenar: " + (err.message || err));
        } finally {
          _cleanupDrag();
        }
      });

      leftEl.addEventListener("dragend", _cleanupDrag);

      function _cleanupDrag() {
        dragSrc = null;
        leftEl.querySelectorAll(".dragging, .drag-over").forEach((el) =>
          el.classList.remove("dragging", "drag-over")
        );
      }
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