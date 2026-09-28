// ─── Addons core — registry + API client + cache ─────────────
// Namespace: window.App.Addons
// Dependencies: global API (app.js)
//
// Sub-phase SA — Addons Foundation. Frontend counterpart of
// backend/routes/addons.py. Lets S5/S6/S9 addons register themselves and
// lets the rest of the app gate on `App.Addons.isEnabled(slug)`.
//
// Catalog is a single-user (v3) in-memory list of slugs with
// installed/enabled/hidden flags. Mutations trigger an
// `addons:changed` CustomEvent so any view can react.

window.App = window.App || {};

window.App.Addons = (function () {
  "use strict";

  // ─── Registry (manifests self-register from their own modules) ──
  const _registry = new Map();

  function register(manifest) {
    if (!manifest || typeof manifest.slug !== "string" || !manifest.slug) {
      console.warn("[Addons] register() requires manifest.slug");
      return null;
    }
    _registry.set(manifest.slug, Object.assign({ views: {} }, manifest));
    return manifest.slug;
  }

  function get(slug) { return _registry.get(slug) || null; }
  function registered() { return Array.from(_registry.values()); }

  // ─── Catalog cache + refresh ────────────────────────────────────
  let _catalog = null;   // null = not loaded yet
  let _loading = null;   // promise of in-flight refresh()

  async function refresh() {
    if (_loading) return _loading;
    _loading = (async () => {
      try {
        const data = await API.get("/addons");
        _catalog = (data && data.addons) || [];
        _emit("catalog");
        return _catalog;
      } finally {
        _loading = null;
      }
    })();
    return _loading;
  }

  function catalog() { return _catalog || []; }
  function getAddon(slug) {
    return (_catalog || []).find((a) => a.slug === slug) || null;
  }

  // ─── API client (contract from backend/routes/addons.py) ──────
  async function list() {
    const data = await API.get("/addons");
    return (data && data.addons) || [];
  }
  const install   = (slug) => API.post(`/addons/${slug}/install`);
  const uninstall = (slug) => API.post(`/addons/${slug}/uninstall`);
  const enable    = (slug) => API.post(`/addons/${slug}/enable`);
  const disable   = (slug) => API.post(`/addons/${slug}/disable`);
  const setHidden = (slug, hidden) =>
    API.post(`/addons/${slug}/set-hidden`, { hidden: !!hidden });
  async function status(slug) {
    return API.get(`/addons/${slug}/status`);
  }

  // ─── Gate helpers (forward-declared for S5/S6/S9 modules) ────
  // Both swallow network errors so callers can probe freely without
  // try/catch spam. Missing addon → false (not installed → throw).
  async function isEnabled(slug) {
    let a = getAddon(slug);
    if (!a) {
      try { await refresh(); a = getAddon(slug); } catch (_) { return false; }
    }
    return !!(a && a.enabled);
  }

  async function isInstalled(slug) {
    let a = getAddon(slug);
    if (!a) {
      try { await refresh(); a = getAddon(slug); } catch (_) { return false; }
    }
    return !!(a && a.installed);
  }

  // ─── Mutate helper: call action, refresh cache, fire event ───
  async function mutate(action, slug) {
    const result = await action(slug);
    await refresh();
    _emit("state", { slug, result });
    return result;
  }

  // ─── Events ──────────────────────────────────────────────────
  function _emit(type, detail) {
    window.dispatchEvent(new CustomEvent("addons:changed",
      { detail: Object.assign({ type }, detail || {}) }));
  }

  return {
    register, get, registered,
    list, install, uninstall, enable, disable, setHidden, status,
    refresh, catalog, getAddon,
    isEnabled, isInstalled,
    mutate,
  };
})();

console.log("[Addons] core loaded");