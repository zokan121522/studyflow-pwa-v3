/* ============================== SCORM CREDENTIALS PANEL (S7b-B) ==============================
 * Settings modal for the Moodle username/password used by the SCORM
 * scraper. Backed by GET/POST /api/settings/scorm-credentials — the
 * password is write-only, so this panel only ever sees has_password.
 *
 * Public: App.ScormSettings.open()
 * ========================================================================== */

window.App = window.App || {};

window.App.ScormSettings = (function () {
  "use strict";

  let _overlay = null;

  function _el(tag, attrs, children) {
    const node = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([k, v]) => {
      if (k === "class") node.className = v;
      // Event handlers must be assigned as properties. setAttribute
      // ("onclick", fn) stores the function's SOURCE as a string, which
      // silently does nothing on click — that is why the Cerrar button
      // never worked before.
      else if (k.startsWith("on") && typeof v === "function") node[k] = v;
      else node.setAttribute(k, v);
    });
    (children || []).forEach((c) => node.appendChild(c));
    return node;
  }

  function _text(tag, className, text) {
    return _el(tag, { class: className }, [document.createTextNode(text)]);
  }

  function _close() {
    if (_overlay) { _overlay.remove(); _overlay = null; }
  }

  function _field(labelText, inputEl, hintText) {
    const wrap = _el("div", { class: "ss-settings-field" }, [
      _el("label", {}, [document.createTextNode(labelText)]),
      inputEl,
    ]);
    if (hintText) wrap.appendChild(_text("p", "ss-settings-hint", hintText));
    return wrap;
  }

  function _setStatus(el, msg, kind) {
    el.textContent = msg || "";
    el.className = "ss-settings-status" + (kind ? " " + kind : "");
  }

  // ── Build the modal ───────────────────────────────────────────────
  function _build() {
    const user = _el("input", { type: "text", placeholder: "usuario campus", autocomplete: "username" });
    const pass = _el("input", { type: "password", placeholder: "••••••••", autocomplete: "current-password" });
    const status = _text("div", "ss-settings-status", "");
    const saveBtn = _el("button", { type: "button", class: "ss-btn primary" }, [document.createTextNode("Guardar")]);

    const actions = _el("div", { class: "ss-settings-actions" }, [
      status, _el("div", { class: "ss-settings-spacer" }),
      _el("button", { type: "button", class: "ss-btn", onclick: _close }, [document.createTextNode("Cerrar")]),
      saveBtn,
    ]);

    const modal = _el("div", { class: "ss-settings-modal", role: "dialog", "aria-modal": "true" }, [
      _text("h2", "", "⚙️ Configuración de Moodle"),
      _text("p", "ss-settings-sub",
        "Credenciales que usa el importador SCORM para iniciar sesión en el campus. Se guardan cifradas en el servidor. Si el campus no pide login, puedes dejarlo vacío."),
      _field("Usuario", user),
      _field("Contraseña", pass, "No se muestra tras guardar: solo se confirma que existe."),
      actions,
    ]);

    const overlay = _el("div", { class: "ss-settings-overlay" }, [modal]);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) _close(); });
    overlay.addEventListener("keydown", (e) => { if (e.key === "Escape") _close(); });
    saveBtn.addEventListener("click", () => _save(user, pass, status, saveBtn));
    return { overlay, user, pass, status, saveBtn };
  }

  // ── Load current status (username + has_password, never the secret) ──
  async function _load(user, status) {
    _setStatus(status, "Cargando…", "info");
    try {
      const data = await API.get("/settings/scorm-credentials");
      user.value = data.username || "";
      _setStatus(status,
        data.has_password ? "Contraseña guardada ✓" : "Sin contraseña guardada",
        data.has_password ? "ok" : "info");
    } catch (err) {
      _setStatus(status, "No se pudo leer la configuración: " + err.message, "err");
    }
  }

  async function _save(user, pass, status, saveBtn) {
    const username = user.value.trim();
    const password = pass.value;
    if (!username) { _setStatus(status, "El usuario es obligatorio.", "err"); return; }
    saveBtn.disabled = true;
    _setStatus(status, "Guardando…", "info");
    try {
      const data = await API.post("/settings/scorm-credentials", { username, password });
      pass.value = "";
      _setStatus(status, "Guardado ✓ " + (data.username ? "(" + data.username + ")" : ""), "ok");
    } catch (err) {
      _setStatus(status, "Error al guardar: " + err.message, "err");
    } finally {
      saveBtn.disabled = false;
    }
  }

  function open() {
    if (_overlay) return;
    const parts = _build();
    _overlay = parts.overlay;
    document.body.appendChild(_overlay);
    _load(parts.user, parts.status);
  }

  return { open };
})();

console.log("[Studyflow] scorm-settings.js loaded");
