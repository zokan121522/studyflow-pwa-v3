// courses-dnd.js — drag & drop reorder for the studyflow sidebar.
//
// Three levels, one set of listeners on the sidebar root:
//   course → POST /courses/reorder            (Issue #12, own fav group)
//   topic  → POST /courses/:id/topics/reorder
//   block  → PATCH /courses/:id/blocks        (same topic) or
//            POST /courses/blocks/:id/move    (cross-topic)
//
// The nesting is the trap: .block-item sits inside .topic-item, which
// sits inside .course-item. `closest()` walks UPWARD, so the dragstart
// checks MUST run innermost-first — block, then topic, then course —
// or every block drag is captured as a course drag.
(function () {
  "use strict";

  function attach(leftEl) {
let dragSrc = null; // {type:'topic'|'block', courseId, id, topicId}

leftEl.addEventListener("dragstart", (e) => {
  // NOTE: .block-item lives INSIDE .topic-item, so the block check
  // MUST come first — otherwise every block drag is captured as a
  // topic drag (blocks would reorder topics or silently no-op).
  const block = e.target.closest(".block-item");
  const topic = block ? null : e.target.closest(".topic-item");
  const course = topic ? null : e.target.closest(".course-item");
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
  } else if (course) {
    // Issue #12 — course-level drag. MUST be checked LAST: a
    // .course-item is the ANCESTOR of every .topic-item and
    // .block-item, so testing it first would swallow every topic
    // and block drag into a course drag.
    dragSrc = {
      type: "course",
      id: Number(course.dataset.courseId),
      group: course.closest(".course-drop-group")
        ? course.closest(".course-drop-group").dataset.dropGroup
        : "rest",
    };
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", String(dragSrc.id));
    course.classList.add("dragging");
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
    dragSrc.type === "topic"
      ? ".topic-item"
      : dragSrc.type === "course"
        ? ".course-item, .course-drop-group"
        : ".block-item, .topic-item"
  );
  if (t) {
    // A block dropped on its own collapsed topic header is a no-op —
    // don't highlight it as a valid target.
    const sameTopic = dragSrc.type === "block"
      && t.classList.contains("topic-item")
      && Number(t.dataset.topicId) === dragSrc.topicId;
    if (sameTopic) return;
    // Dropping a course on ITS OWN group is a no-op too — highlight
    // only groups the course could actually move into.
    if (dragSrc.type === "course" && t.classList.contains("course-drop-group")
        && t.dataset.dropGroup === dragSrc.group) {
      return;
    }
    t.classList.add("drag-over");
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
    src.type === "topic"
      ? ".topic-item"
      : src.type === "course"
        ? ".course-item, .course-drop-group"
        : ".block-item, .topic-item"
  );
  if (!over) return _cleanupDrag();
  const callbacks = leftEl._csCallbacks || {};
  try {
    if (src.type === "course") {
      // Issue #12 — course reorder. A drop on a .course-item means
      // "insert before it"; a drop on a group container means
      // "append to that group". Crossing groups also flips the
      // favorite flag, so the star and the section can never
      // disagree about where a course lives.
      const overGroup = over.classList.contains("course-drop-group")
        ? over
        : over.closest(".course-drop-group");
      if (!overGroup) return _cleanupDrag();
      const targetGroup = overGroup.dataset.dropGroup;
      const isFavGroup = targetGroup === "fav";

      const groupIds = (g) => [...leftEl.querySelectorAll(
        `.course-drop-group[data-drop-group="${g}"] .course-item`
      )].map((el) => Number(el.dataset.courseId));

      const fav = groupIds("fav").filter((id) => id !== src.id);
      const rest = groupIds("rest").filter((id) => id !== src.id);
      const dest = isFavGroup ? fav : rest;

      const overId = over.classList.contains("course-item")
        ? Number(over.dataset.courseId) : null;
      let at = overId != null ? dest.indexOf(overId) : dest.length;
      if (at < 0) at = dest.length;
      dest.splice(at, 0, src.id);

      // Persist order FIRST, then the flag. If the flag write fails
      // the order is still valid; doing it the other way round would
      // leave a course starred but sitting in the wrong section.
      await window.App.CoursesAPI.reorderCourses(fav.concat(rest));
      if (isFavGroup !== (src.group === "fav")) {
        await window.App.CoursesAPI.setFavorite(src.id, isFavGroup);
      }
      if (typeof callbacks.onDataChanged === "function") {
        await callbacks.onDataChanged();
      } else {
        await window.App.Courses.renderStudyflow();
      }
      return _cleanupDrag();
    }
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

  window.App = window.App || {};
  window.App.CoursesDND = { attach };
})();

console.log("[Studyflow] courses-dnd.js loaded");
