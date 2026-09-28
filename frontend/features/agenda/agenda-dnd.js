// ─── Agenda Drag & Drop — session reorder + move-to-day ───────────
// Namespace: window.App.AgendaDnd
// Dependencies: none (DOM only — callbacks for API calls)

window.App = window.App || {};
window.App.AgendaDnd = (function () {
  "use strict";

  let _dragSessionId = null;

  // ─── Bind drop targets on left-panel day items ──────────────────
  // leftEl: the #agenda-left element
  // callbacks.onDrop(sessionId, targetDate) — called when a session is dropped on a day
  function bindDayTargets(leftEl, callbacks) {
    leftEl.querySelectorAll(".day-item").forEach((day) => {
      day.addEventListener("dragover", (e) => {
        if (!_dragSessionId) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
        day.classList.add("drag-over-day");
      });
      day.addEventListener("dragleave", (e) => {
        if (day.contains(e.relatedTarget)) return;
        day.classList.remove("drag-over-day");
      });
      day.addEventListener("drop", async (e) => {
        e.preventDefault();
        day.classList.remove("drag-over-day");
        const targetDate = day.dataset.date;
        if (!_dragSessionId || !targetDate) return;
        if (callbacks && callbacks.onDrop) {
          callbacks.onDrop(_dragSessionId, targetDate);
        }
        _dragSessionId = null;
      });
    });
  }

  // ─── Bind drag-and-drop on the session list for reordering ──────
  // sessionListEl: the .session-list element
  // callbacks.onReorder(dragId, targetId, newOrderIds) — called with the new order
  function bindSessionList(sessionListEl, callbacks) {
    if (!sessionListEl) return;

    sessionListEl.addEventListener("dragstart", (e) => {
      // Only allow dragging from the time handle (.sc-time), not the whole card
      if (!e.target.closest(".sc-time")) {
        e.preventDefault();
        return;
      }
      const card = e.target.closest(".s-card");
      if (!card) return;
      _dragSessionId = card.dataset.sessionId;
      card.style.opacity = ".4";
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", _dragSessionId);
    });

    sessionListEl.addEventListener("dragenter", (e) => {
      const card = e.target.closest(".s-card");
      if (!card || card.dataset.sessionId === _dragSessionId) return;
      card.classList.add("drag-over");
    });

    sessionListEl.addEventListener("dragover", (e) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
    });

    sessionListEl.addEventListener("dragleave", (e) => {
      const card = e.target.closest(".s-card");
      if (!card) return;
      if (card.contains(e.relatedTarget)) return;
      card.classList.remove("drag-over");
    });

    sessionListEl.addEventListener("dragend", () => {
      _dragSessionId = null;
      sessionListEl.querySelectorAll(".s-card").forEach((c) => {
        c.style.opacity = "";
        c.classList.remove("drag-over");
      });
      document.querySelectorAll("#agenda-left .day-item.drag-over-day").forEach((d) => {
        d.classList.remove("drag-over-day");
      });
    });

    sessionListEl.addEventListener("drop", async (e) => {
      e.preventDefault();
      const card = e.target.closest(".s-card");
      if (!card) return;
      card.classList.remove("drag-over");
      const targetSid = card.dataset.sessionId;
      if (!_dragSessionId || _dragSessionId === targetSid) return;

      const cards = [...sessionListEl.querySelectorAll(".s-card")];
      const srcIdx = cards.findIndex((c) => c.dataset.sessionId === _dragSessionId);
      const tgtIdx = cards.findIndex((c) => c.dataset.sessionId === targetSid);
      if (srcIdx === -1 || tgtIdx === -1) return;

      // Reorder DOM visually
      if (srcIdx < tgtIdx) {
        cards[tgtIdx].insertAdjacentElement("afterend", cards[srcIdx]);
      } else {
        cards[tgtIdx].insertAdjacentElement("beforebegin", cards[srcIdx]);
      }

      const newOrder = [...sessionListEl.querySelectorAll(".s-card")].map((c) => c.dataset.sessionId);
      _dragSessionId = null;

      if (callbacks && callbacks.onReorder) {
        callbacks.onReorder(newOrder);
      }
    });
  }

  // ─── Public API ──────────────────────────────────────────────────
  return {
    bindDayTargets,
    bindSessionList,
    reset: function () { _dragSessionId = null; },
    getDragSessionId: function () { return _dragSessionId; },
  };
})();
