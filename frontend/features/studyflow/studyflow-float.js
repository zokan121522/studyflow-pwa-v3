// ─── Studyflow Float — sticky floating block toolbar (S4.7) ─────
// Namespace: window.App.StudyflowFloat
// Dependencies: window.STATE, window.App.CoursesAPI
//               (re-uses no event handlers — see _syncFloatingToolbar).
//
// S4.7 — Phase 59 v2 port. While the topic detail is rendered the user
// would otherwise lose access to the per-block actions (▼ ⬆️ ⬇️ ✏️
// 🗑️) once the block scrolls out of view. v2 solved this with a
// `position: sticky` toolbar at the top of the scroll container
// whose content is cloned from the active block. v3 ports that.
//
// Implementation note (delegation-to-original-click trick):
// the slot does NOT re-bind events on its cloned buttons. Instead,
// each slot button carries a `data-act` matching the original
// card-head button's `data-act`. On click we look up the matching
// button on the active .sf-block-card and call .click() on it,
// letting the existing delegated handlers in
// App.CoursesBlocks._attachBlockHandlers / _attachCardHandlers
// process the action exactly as if the user clicked it in place.
//
// This keeps the slot fully independent of the block handlers
// (no leaks, no double-binding, no stale references) and falls
// back to a no-op if the active block lacks a matching action.

window.App = window.App || {};

window.App.StudyflowFloat = (function () {
  "use strict";

  let _spyRegistered = false;
  let _slot = null;
  let _lastActiveId = null;
  let _lastScrollTop = -1;

  // ── _ensureSlot(container) — create the sticky slot if missing ──
  function _ensureSlot(container) {
    if (!container) return null;
    if (_slot && _slot.parentElement === container) return _slot;
    // Look for an existing slot (idempotent across re-renders).
    let existing = container.querySelector(".block-toolbar-float");
    if (existing) {
      _slot = existing;
      return _slot;
    }
    const slot = document.createElement("div");
    slot.className = "block-toolbar-float";
    slot.hidden = true;
    slot.setAttribute("aria-label", "Acciones del bloque activo");
    container.prepend(slot);
    _slot = slot;
    return _slot;
  }

  // ── _getActiveCardId(container) — scroll-spy: topmost visible ──
  function _getActiveCardId(container) {
    if (!container) return null;
    const cards = container.querySelectorAll(
      ".sf-block-card[data-block-id]"
    );
    if (!cards.length) return null;
    const cRect = container.getBoundingClientRect();
    const threshold = cRect.top + 90; // a bit below the sticky slot
    let activeId = null;
    for (const card of cards) {
      if (card.getBoundingClientRect().top <= threshold) {
        activeId = card.dataset.blockId;
      }
    }
    if (!activeId) activeId = cards[0].dataset.blockId;
    return activeId;
  }

  // ── _renderActionsFor(card) → [{act, label}] from card-head btns ──
  // Read the actual card-head action buttons (.sf-bc-collapse,
  // .sf-td-edit, .sf-td-del) and build a label list. We don't clone
  // the buttons — we synthesise a thin replica keyed by data-act so
  // the click delegation can find the original.
  function _readCardActions(card) {
    const actions = [];
    const sel = [
      ".sf-bc-collapse",
      ".sf-td-edit",
      ".sf-td-del",
    ].join(",");
    card.querySelectorAll(sel).forEach((btn) => {
      let act = null;
      if (btn.classList.contains("sf-bc-collapse")) act = "collapse";
      else if (btn.classList.contains("sf-td-edit")) act = "edit";
      else if (btn.classList.contains("sf-td-del")) act = "delete";
      if (!act) return;
      actions.push({ act, label: btn.textContent || "" });
    });
    return actions;
  }

  // ── _syncFloatingToolbar(container, activeId) — populate the slot ─
  // Re-renders the slot's contents based on the active block. If the
  // active card has no actions (separator / blank / unknown type) we
  // hide the slot. Subsequent calls with the same activeId are
  // a no-op (avoids redundant DOM churn during scroll).
  function _syncFloatingToolbar(container, activeId) {
    const slot = _ensureSlot(container);
    if (!slot) return;
    if (!activeId) return _hideSlot(slot, null);
    const card = container.querySelector(
      `.sf-block-card[data-block-id="${activeId}"]`
    );
    if (!card) return _hideSlot(slot, null);
    if (_lastActiveId === activeId && !slot.hidden) return;

    const acts = _readCardActions(card);
    if (!acts.length) return _hideSlot(slot, activeId);

    // Build the slot markup. Each button has a unique data-act key
    // that delegates its click to the matching button on the original
    // card (see _onSlotClick).
    const btns = acts
      .map((a) =>
        `<button type="button" class="btf-btn" `
        + `data-act="${a.act}" `
        + `title="${_titleFor(a.act)}" `
        + `aria-label="${_titleFor(a.act)}">${a.label}</button>`
      )
      .join("");
    slot.innerHTML =
      `<span class="btf-label">📌 Bloque activo</span>${btns}`;
    slot.dataset.activeId = activeId;
    slot.hidden = false;
    _lastActiveId = activeId;
  }

  // _hideSlot(slot, activeId) — collapse the slot (hidden = true).
  // Tracks lastActiveId so the next _syncFloatingToolbar call knows
  // whether the active card changed.
  function _hideSlot(slot, activeId) {
    if (!slot.hidden) slot.hidden = true;
    _lastActiveId = activeId;
    if (activeId) slot.dataset.activeId = activeId;
  }

  // ── _titleFor(act) — accessible title per action ───────────────
  function _titleFor(act) {
    const titles = {
      collapse: "Plegar / desplegar",
      edit: "Editar",
      delete: "Borrar",
    };
    return titles[act] || act;
  }

  // ── _onSlotClick(ev) — delegate .click() to the original card ───
  // Look up the matching button on the active .sf-block-card and
  // call .click() on it. The delegated handlers in
  // App.CoursesBlocks._attachBlockHandlers / _attachCardHandlers
  // process the action exactly as if the user clicked in place —
  // no event rebinding, no stale closures.
  function _onSlotClick(ev) {
    const btn = ev.target.closest(".btf-btn[data-act]");
    if (!btn) return;
    const slot = btn.closest(".block-toolbar-float");
    if (!slot || slot.hidden) return;
    const activeId = slot.dataset.activeId;
    if (!activeId) return;
    const container = slot.parentElement;
    if (!container) return;
    const card = container.querySelector(
      `.sf-block-card[data-block-id="${activeId}"]`
    );
    if (!card) return;
    const act = btn.dataset.act;
    const selMap = {
      collapse: ".sf-bc-collapse",
      edit:     ".sf-td-edit",
      delete:   ".sf-td-del",
    };
    const sel = selMap[act];
    if (!sel) return;
    const original = card.querySelector(sel);
    if (!original) return;
    ev.preventDefault();
    ev.stopPropagation();
    original.click();
  }

  // ── _applyScrollSpy() — pick active card, sync slot ────────────
  function _applyScrollSpy() {
    const container = document.getElementById("studyflow-center");
    if (!container) return;
    const activeId = _getActiveCardId(container);
    if (activeId) _syncFloatingToolbar(container, activeId);
  }

  // ── _initScrollSpy() — register scroll listener (once) ─────────
  function _initScrollSpy() {
    if (_spyRegistered) return;
    _spyRegistered = true;
    const container = document.getElementById("studyflow-center");
    if (!container) return;
    _wireSlotClick(container);
    container.addEventListener("scroll", _onContainerScroll, { passive: true });
    window.addEventListener("resize", () => {
      requestAnimationFrame(_applyScrollSpy);
    });
  }

  // _wireSlotClick(container) — install the slot's click delegation
  // exactly once. Subsequent calls are no-ops (the slot is reused
  // across re-renders, the listener survives innerHTML reassignments).
  function _wireSlotClick(container) {
    const slot = _ensureSlot(container);
    if (!slot || slot.dataset._btfClick) return;
    slot.dataset._btfClick = "1";
    slot.addEventListener("click", _onSlotClick);
  }

  // _onContainerScroll — rAF-throttled scroll-spy update. Skips
  // when scrollTop hasn't moved (avoids rAF thrash on tiny detents).
  function _onContainerScroll() {
    if (_scrollTicking) return;
    _scrollTicking = true;
    requestAnimationFrame(() => {
      _scrollTicking = false;
      const container = document.getElementById("studyflow-center");
      if (!container) return;
      if (container.scrollTop === _lastScrollTop && _lastActiveId) return;
      _lastScrollTop = container.scrollTop;
      _applyScrollSpy();
    });
  }
  let _scrollTicking = false;

  // ── mount() — wire scroll-spy after a topic detail render ─────
  // Called from courses.js after _renderTopicDetail. Idempotent:
  // the scroll listener is only attached once, but every call
  // re-syncs the slot against the current topic.
  function mount() {
    const container = document.getElementById("studyflow-center");
    if (!container) return;
    // Skip when the center panel is showing the marketplace (no
    // .sf-block-card there — slot would just hide).
    if (!container.querySelector(".sf-block-card")) {
      const slot = container.querySelector(".block-toolbar-float");
      if (slot && !slot.hidden) slot.hidden = true;
      _lastActiveId = null;
      return;
    }
    _ensureSlot(container);
    _initScrollSpy();
    _lastScrollTop = -1; // force first sync
    _applyScrollSpy();
  }

  // ── unmount() — hide slot on view changes (marketplace, etc.) ──
  function unmount() {
    if (_slot && !_slot.hidden) _slot.hidden = true;
    _lastActiveId = null;
  }

  return { mount, unmount };
})();

console.log("[Studyflow] float loaded");