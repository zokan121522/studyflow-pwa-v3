// ─── Courses API — fetch calls + course cache (S1+S3 slice) ──────────
// Namespace: window.App.CoursesAPI
// Dependencies: global API (app.js), window.STATE
//
// SCOPE:
//   • list / detail / cache + invalidation
//   • create / rename / updateDescription / delete course
//   • add / rename / delete topic
//   • block CRUD (S3): addBlock, updateBlock, deleteBlock,
//     toggleBlockDone, reorderBlocks, moveBlock, listTopicBlocks

window.App = window.App || {};
window.App.CoursesAPI = (function () {
  "use strict";

  // ── Caches (per-session; reset on hard reload) ────────────────
  // TTL bounds how stale a cached read can ever be. The mutation helpers
  // below all call clearDetailCache(), but plenty of writes never pass
  // through this module: the knowledge-pipeline skill writes straight to
  // PostgreSQL over SSH, the PDF/SCORM importer below uses a raw fetch, and
  // a second tab can change anything. Without a TTL those reads stay stale
  // for the rest of the session and the only cure is a full page reload —
  // which is exactly the "I have to refresh before I can edit it" report.
  const CACHE_TTL_MS = 4000;
  let _listCache = null;
  let _listCacheAt = 0;
  const _detailCache = {};
  const _detailCacheAt = {};

  const _fresh = (at) => (Date.now() - at) < CACHE_TTL_MS;
  let _wiredRevalidate = false;

  // Coming back to the tab is the most likely moment for data to have
  // changed elsewhere (another tab, a terminal running the pipeline, a
  // generation that finished while the user was away). Re-reading then is
  // cheap and removes the "reload to make it appear" trap.
  function _wireRevalidateOnFocus() {
    if (_wiredRevalidate || typeof window === "undefined") return;
    _wiredRevalidate = true;
    const drop = () => {
      if (document.visibilityState === "hidden") return;
      clearDetailCache();
    };
    window.addEventListener("focus", drop);
    window.addEventListener("visibilitychange", drop);
  }

  // ── fetchCourses(force) — List with topics[] ─────────────────
  async function fetchCourses(force) {
    _wireRevalidateOnFocus();
    if (!force && _listCache && _fresh(_listCacheAt)) return _listCache;
    try {
      const data = await API.get("/courses");
      _listCache = (data && data.courses) || [];
    } catch (_) {
      _listCache = [];
    }
    _listCacheAt = Date.now();
    return _listCache;
  }

  // ── fetchCourseDetail(courseId, force) ───────────────────────
  // Returns { id, title, …, topics: [{ id, title, …, blocks: [...] }] }
  // Topics are hydrated with their ordered blocks (S3+).
  async function fetchCourseDetail(courseId, force) {
    _wireRevalidateOnFocus();
    if (!force && _detailCache[courseId] && _fresh(_detailCacheAt[courseId])) {
      return _detailCache[courseId];
    }
    try {
      const data = await API.get(`/courses/${courseId}`);
      const course = (data && data.course) || { topics: [], blocks: [] };
      // Normalise: every topic gets a blocks:[] even if backend forgot.
      course.topics = (course.topics || []).map((t) => ({
        blocks: [], ...t,
      }));
      _detailCache[courseId] = course;
      _detailCacheAt[courseId] = Date.now();
      return course;
    } catch (_) {
      return { topics: [], blocks: [] };
    }
  }

  // ── clearDetailCache(courseId?) ──────────────────────────────
  // After mutations that change structure, drop caches so the next
  // fetchCourses / fetchCourseDetail hits the network.
  function clearDetailCache(courseId) {
    if (courseId != null) {
      delete _detailCache[courseId];
      delete _detailCacheAt[courseId];
    } else {
      Object.keys(_detailCache).forEach((k) => delete _detailCache[k]);
      Object.keys(_detailCacheAt).forEach((k) => delete _detailCacheAt[k]);
    }
    _listCache = null;
    _listCacheAt = 0;
    if (typeof STATE !== "undefined") {
      STATE._expandedCourseBlocks = [];
      STATE._expandedCourseTopics = [];
    }
  }

  // ── createCourse(title) ──────────────────────────────────────
  async function createCourse(title) {
    const data = await API.post("/courses", { title });
    clearDetailCache();
    return data && data.course;
  }

  // ── renameCourse(courseId, title) ────────────────────────────
  async function renameCourse(courseId, title) {
    await API.patch(`/courses/${courseId}/rename`, { title });
    _patchListCache(courseId, (c) => { c.title = title; });
    if (_detailCache[courseId]) _detailCache[courseId].title = title;
  }

  // ── updateDescription(courseId, description) ────────────────
  // Pass null / "" to clear.
  async function updateDescription(courseId, description) {
    await API.patch(`/courses/${courseId}/description`, { description });
    _patchListCache(courseId, (c) => { c.description = description; });
    if (_detailCache[courseId]) {
      _detailCache[courseId].description = description;
    }
  }

  // ── deleteCourse(courseId) ───────────────────────────────────
  async function deleteCourse(courseId) {
    await API.del(`/courses/${courseId}`);
    clearDetailCache(courseId);
    if (typeof STATE !== "undefined") {
      if (STATE.currentCourseId === courseId) {
        STATE.currentCourseId = null;
        STATE.expandedCourseId = null;
        STATE.selectedTopicId = null;
        STATE.selectedBlockId = null;
      }
    }
  }

  // ── addTopic(courseId, title) ────────────────────────────────
  async function addTopic(courseId, title) {
    const data = await API.post(`/courses/${courseId}/topics`, { title });
    clearDetailCache(courseId);
    return data && data.topic;
  }

  // ── renameTopic(courseId, topicId, title) ────────────────────
  async function renameTopic(courseId, topicId, title) {
    await API.patch(
      `/courses/${courseId}/topics/${topicId}/rename`, { title }
    );
    if (_detailCache[courseId]) {
      const t = (_detailCache[courseId].topics || [])
        .find((tt) => tt.id === topicId);
      if (t) t.title = title;
    }
  }

  // ── deleteTopic(courseId, topicId) ────────────────────────────
  async function deleteTopic(courseId, topicId) {
    await API.del(`/courses/${courseId}/topics/${topicId}`);
    clearDetailCache(courseId);
    if (typeof STATE !== "undefined") {
      if (STATE.selectedTopicId === topicId) STATE.selectedTopicId = null;
      if (STATE.expandedCourseId === courseId) {
        STATE._expandedCourseTopics = [];
      }
    }
  }

  // ============================== Blocks (S3) ==============================
  // ── listTopicBlocks(courseId, topicId) ───────────────────────
  async function listTopicBlocks(courseId, topicId) {
    const data = await API.get(
      `/courses/${courseId}/topics/${topicId}/blocks`
    );
    return (data && data.blocks) || [];
  }

  // ── addBlock(courseId, opts) ─────────────────────────────────
  // opts: { topic_id?, type, title?, content?, url? }
  // When topic_id omitted, posts to the course-level endpoint.
  async function addBlock(courseId, opts) {
    const topicId = opts && opts.topic_id;
    const path = topicId
      ? `/courses/${courseId}/topics/${topicId}/blocks`
      : `/courses/${courseId}/blocks`;
    const data = await API.post(path, opts || {});
    clearDetailCache(courseId);
    return data && data.block;
  }

  // ── updateBlock(courseId, blockId, fields) ───────────────────
  async function updateBlock(courseId, blockId, fields) {
    const data = await API.put(
      `/courses/${courseId}/blocks/${blockId}`,
      fields || {}
    );
    clearDetailCache(courseId);
    return data && data.block;
  }

  // ── deleteBlock(courseId, blockId) ───────────────────────────
  async function deleteBlock(courseId, blockId) {
    await API.del(`/courses/${courseId}/blocks/${blockId}`);
    clearDetailCache(courseId);
    if (typeof STATE !== "undefined"
        && STATE.selectedBlockId === blockId) {
      STATE.selectedBlockId = null;
    }
  }

  // ── toggleBlockDone(courseId, blockId) ───────────────────────
  // Returns the new done boolean.
  async function toggleBlockDone(courseId, blockId) {
    const data = await API.patch(
      `/courses/${courseId}/blocks/${blockId}/done`
    );
    clearDetailCache(courseId);
    return !!(data && data.done);
  }

  // ── reorderBlocks(courseId, ids) ─────────────────────────────
  // ids: array of block ids in the desired order.
  async function reorderBlocks(courseId, ids) {
    await API.patch(
      `/courses/${courseId}/blocks`,
      { blocks: ids || [] }
    );
    clearDetailCache(courseId);
  }

  // ── moveBlock(blockId, {target_topic_id, index}) ─────────────
  async function moveBlock(blockId, opts) {
    const data = await API.post(
      `/courses/blocks/${blockId}/move`,
      opts || {}
    );
    // Move crosses course → drop ALL detail caches.
    clearDetailCache();
    return data && data.block;
  }

  // ── reorderTopics(courseId, topicIds) ─────────────────────────
  // topicIds: array of topic ids in the desired order (same course).
  // Uses the v2 alias endpoint scoped to the course.
  async function reorderTopics(courseId, topicIds) {
    await API.post(
      `/courses/${courseId}/topics/reorder`,
      { topic_ids: topicIds || [] }
    );
    clearDetailCache(courseId);
  }

  // ── toggleFavorite(courseId, favorite) ────────────────────────
  // Issue #12. Optimistic in the caller (the star flips instantly);
  // here we just persist and keep both caches honest so a later
  // sidebar re-render doesn't snap the star back to the stale value.
  async function setFavorite(courseId, favorite) {
    await API.patch(`/courses/${courseId}/favorite`, { favorite: !!favorite });
    _patchListCache(courseId, (c) => { c.is_favorite = !!favorite; });
    if (_detailCache[courseId]) {
      _detailCache[courseId].is_favorite = !!favorite;
    }
  }

  // ── reorderCourses(courseIds) ─────────────────────────────────
  // Full visible sidebar order after a course drag&drop. Reorders the
  // list CACHE in place rather than dropping it: fetchCourses() serves
  // that same array, so a refetch-based re-render would otherwise show
  // the old order until the page is reloaded.
  async function reorderCourses(courseIds) {
    const order = courseIds || [];
    await API.post("/courses/reorder", { course_ids: order });
    if (!_listCache) return;
    const rank = new Map(order.map((id, i) => [id, i]));
    _listCache.sort((a, b) =>
      (rank.has(a.id) ? rank.get(a.id) : Number.MAX_SAFE_INTEGER)
      - (rank.has(b.id) ? rank.get(b.id) : Number.MAX_SAFE_INTEGER));
  }

  // ── getTopicNotes(courseId, topicId) ──────────────────────────
  // S4: returns the freeform notes string for a topic.
  async function getTopicNotes(courseId, topicId) {
    const data = await API.get(
      `/courses/${courseId}/topics/${topicId}/notes`
    );
    const notes = (data && data.notes) || {};
    return notes.content || "";
  }

  // ── saveTopicNotes(courseId, topicId, content) ───────────────
  async function saveTopicNotes(courseId, topicId, content) {
    const data = await API.put(
      `/courses/${courseId}/topics/${topicId}/notes`,
      { content: content || "" }
    );
    const notes = (data && data.notes) || {};
    // Patch cached detail so the next re-render is consistent.
    if (_detailCache[courseId]) {
      const t = (_detailCache[courseId].topics || [])
        .find((tt) => tt.id === topicId);
      if (t) t.notes = notes.content || "";
    }
    return notes.content || "";
  }

  // ── Helper: in-place list-cache patch ────────────────────────
  function _patchListCache(courseId, mutator) {
    if (!_listCache) return;
    const c = _listCache.find((x) => x.id === courseId);
    if (c) mutator(c);
  }

  // ── Public API ───────────────────────────────────────────────
  return {
    fetchCourses,
    fetchCourseDetail,
    clearDetailCache,
    createCourse,
    renameCourse,
    updateDescription,
    deleteCourse,
    addTopic,
    renameTopic,
    deleteTopic,
    // Blocks (S3)
    listTopicBlocks,
    addBlock,
    updateBlock,
    deleteBlock,
    toggleBlockDone,
    reorderBlocks,
    moveBlock,
    reorderTopics,
    // Favorites + course order (Issue #12)
    setFavorite,
    reorderCourses,
    // Topic notes (S4)
    getTopicNotes,
    saveTopicNotes,
  };
})();

console.log("[Studyflow] courses-api.js loaded");