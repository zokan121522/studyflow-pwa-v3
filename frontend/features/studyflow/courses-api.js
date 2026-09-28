// ─── Courses API — fetch calls + course cache (S1 slice) ──────────
// Namespace: window.App.CoursesAPI
// Dependencies: global API (app.js), window.STATE
//
// SCOPE (this slice): courses list/detail + course/topic CRUD basics.
//   • list / detail / cache + invalidation
//   • create / rename / updateDescription / delete course
//   • add / rename / delete topic
//
// Block CRUD/edit/add/favorite/share are deferred to S3/S4.

window.App = window.App || {};
window.App.CoursesAPI = (function () {
  "use strict";

  // ── Caches (per-session; reset on hard reload) ────────────────
  let _listCache = null;
  const _detailCache = {};

  // ── fetchCourses(force) — List with topics[] ─────────────────
  async function fetchCourses(force) {
    if (!force && _listCache) return _listCache;
    try {
      const data = await API.get("/courses");
      _listCache = (data && data.courses) || [];
    } catch (_) {
      _listCache = [];
    }
    return _listCache;
  }

  // ── fetchCourseDetail(courseId, force) ───────────────────────
  // Returns { id, title, …, topics: [{ id, title, …, blocks: [] }] }
  // `blocks: []` is empty in this slice (S3 will hydrate blocks).
  async function fetchCourseDetail(courseId, force) {
    if (!force && _detailCache[courseId]) return _detailCache[courseId];
    try {
      const data = await API.get(`/courses/${courseId}`);
      const course = (data && data.course) || { topics: [] };
      // Normalise: every topic gets a blocks:[] even if backend forgot.
      course.topics = (course.topics || []).map((t) => ({
        blocks: [], ...t,
      }));
      _detailCache[courseId] = course;
      return course;
    } catch (_) {
      return { topics: [], blocks: [] };
    }
  }

  // ── clearDetailCache(courseId?) ──────────────────────────────
  // After mutations that change structure, drop caches so the next
  // fetchCourses / fetchCourseDetail hits the network.
  function clearDetailCache(courseId) {
    if (courseId != null) delete _detailCache[courseId];
    else Object.keys(_detailCache).forEach((k) => delete _detailCache[k]);
    _listCache = null;
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
  };
})();

console.log("[Studyflow] courses-api.js loaded");