/* ============================== OPENZEN CREDENTIALS PANEL ==============================
 * The v3 port of the v2 "Configuración → ⛅ OpenZen" tab ("👤 Mi suscripción").
 *
 * It hangs off the same floating-button menu as the NotebookLM panel, so
 * authenticating OpenZen is two clicks from anywhere in the app — which is
 * the prerequisite for YouTubeZen and "Gen. Contenido", both of which need
 * the opencode-acp sidecar before they can run.
 *
 * Backed by GET/POST/DELETE /api/settings/openzen-credentials. The API key
 * is write-only, so this panel only ever sees `has_api_key`.
 *
 * Public: App.OpenZenSettings.open()
 * ========================================================================== */

window.App = window.App || {};

window.App.OpenZenSettings = (function () {
  "use strict";

  let _overlay = null;

  function _el(tag, attrs, children) {
    const node = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([k, v]) => {
      if (k === "class") node.className = v;
      // Event handlers MUST go through setAttribute-free property
      // assignment: setAttribute("onclick", fn) stores the function's
      // SOURCE as a string, so the click silently does nothing. This is
      // why the Cerrar button has to be wired here and not via attrs.
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

  function _build() {
    const key = _el("input", {
      type: "password",
      placeholder: "sk-…",
      autocomplete: "off",
    });
    const server = _el("input", { type: "text", placeholder: "http://opencode:54321" });
    const model = _el("input", { type: "text", placeholder: "big-pickle" });
    const status = _text("div", "ss-settings-status", "");
    const saveBtn = _el("button", { type: "button", class: "ss-btn primary" },
      [document.createTextNode("Guardar")]);
    const clearBtn = _el("button", { type: "button", class: "ss-btn danger" },
      [document.createTextNode("Limpiar")]);

    const actions = _el("div", { class: "ss-settings-actions" }, [
      status, _el("div", { class: "ss-settings-spacer" }),
      clearBtn,
      _el("button", { type: "button", class: "ss-btn", onclick: _close },
        [document.createTextNode("Cerrar")]),
      saveBtn,
    ]);

    const modal = _el("div", { class: "ss-settings-modal", role: "dialog", "aria-modal": "true" }, [
      _text("h2", "", "⛅ OpenZen"),
      _text("p", "ss-settings-sub",
        "Tu suscripción a OpenZen (opencode-acp). Sin esto, YouTubeZen y "
        + "«Gen. Contenido» no pueden ejecutarse. La clave se guarda cifrada "
        + "en el servidor."),
      _field("API Key", key, "No se muestra tras guardar: solo se confirma que existe."),
      _field("Servidor", server, "Opcional. Vacío = el valor por defecto del servidor."),
      _field("Modelo", model, "Opcional. Vacío = el valor por defecto del servidor."),
      actions,
    ]);

    const overlay = _el("div", { class: "ss-settings-overlay" }, [modal]);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) _close(); });
    overlay.addEventListener("keydown", (e) => { if (e.key === "Escape") _close(); });
    saveBtn.addEventListener("click", () => _save(key, server, model, status, saveBtn, clearBtn));
    clearBtn.addEventListener("click", () => _clear(status, clearBtn));
    return { overlay, key, server, model, status, saveBtn, clearBtn };
  }

  // ── Load current status (server_url + model + has_api_key) ──────────
  async function _load(server, model, status, clearBtn) {
    _setStatus(status, "Cargando…", "info");
    try {
      const data = await API.get("/settings/openzen-credentials");
      server.value = data.server_url || "";
      model.value = data.model || "";
      // The key is write-only, so an existing key means the field stays
      // empty: saving without typing a new one keeps the stored one.
      _setStatus(status,
        data.has_api_key ? "API Key guardada ✓" : "Sin API Key guardada",
        data.has_api_key ? "ok" : "info");
      clearBtn.disabled = !data.has_api_key;
    } catch (err) {
      _setStatus(status, "No se pudo leer la configuración: " + err.message, "err");
    }
  }

  async function _save(key, server, model, status, saveBtn, clearBtn) {
    const apiKey = key.value.trim();
    if (!apiKey) {
      _setStatus(status, "La API Key es obligatoria.", "err");
      return;
    }
    saveBtn.disabled = true;
    _setStatus(status, "Guardando…", "info");
    try {
      await API.post("/settings/openzen-credentials", {
        api_key: apiKey,
        server_url: server.value.trim(),
        model: model.value.trim(),
      });
      key.value = "";
      _setStatus(status, "Guardado ✓ Ya puedes usar YouTubeZen.", "ok");
    } catch (err) {
      _setStatus(status, "Error al guardar: " + err.message, "err");
    } finally {
      saveBtn.disabled = false;
      // A key now exists even if the panel was opened with none, so
      // "Limpiar" has to become usable without reopening the panel.
      if (clearBtn) clearBtn.disabled = false;
    }
  }

  async function _clear(status, clearBtn) {
    clearBtn.disabled = true;
    _setStatus(status, "Limpiando…", "info");
    try {
      await API.del("/settings/openzen-credentials");
      _setStatus(status, "API Key eliminada. Se usará el valor del servidor.", "ok");
    } catch (err) {
      _setStatus(status, "Error al limpiar: " + err.message, "err");
    }
  }

  function open() {
    if (_overlay) return;
    const parts = _build();
    _overlay = parts.overlay;
    document.body.appendChild(_overlay);
    _load(parts.server, parts.model, parts.status, parts.clearBtn);
  }

  return { open };
})();

console.log("[Studyflow] openzen-settings.js loaded");
