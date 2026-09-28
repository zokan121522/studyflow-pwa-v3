/* =========================== NOTEBOOKLM SETTINGS PANEL (7.2) ===========================
 * Settings modal for the NotebookLM integration: connection status, active
 * profile, profile switching, cookie upload, Chrome export script, verify,
 * disconnect and delete. Backed by /api/settings/notebooklm/* endpoints.
 *
 * Pattern follows scorm-settings.js (S7b-B): modal overlay + ss-settings-*
 * CSS classes, reached from the FAB menu (study-scheduler.js _menuItems).
 *
 * Public: App.NotebookLmSettings.open()
 * ========================================================================== */

window.App = window.App || {};

window.App.NotebookLmSettings = (function () {
  "use strict";

  let _overlay = null;
  let _status = null; // cached from GET /settings/notebooklm/status
  let _loginPoll = null; // login-status polling interval

  function _el(tag, attrs, children) {
    const node = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([k, v]) => {
      if (k === "class") node.className = v;
      else if (k === "style") node.style.cssText = v;
      else node.setAttribute(k, v);
    });
    (children || []).forEach((c) => node.appendChild(c));
    return node;
  }

  function _text(tag, className, text) {
    return _el(tag, { class: className }, [document.createTextNode(text)]);
  }

  function _btn(label, cls, onClick) {
    const b = _el("button", { type: "button", class: "ss-btn " + (cls || "") }, [document.createTextNode(label)]);
    b.addEventListener("click", onClick);
    return b;
  }

  function _setStatus(el, msg, kind) {
    el.textContent = msg || "";
    el.className = "ss-settings-status" + (kind ? " " + kind : "");
  }

  function _close() {
    _stopLoginPoll();
    if (_overlay) { _overlay.remove(); _overlay = null; }
  }

  function _stopLoginPoll() {
    if (_loginPoll) { clearInterval(_loginPoll); _loginPoll = null; }
  }

  // ── Actions (each re-renders the body after a state change) ────────

  async function _verify(statusEl) {
    _setStatus(statusEl, "Verificando conexión…", "info");
    try {
      const resp = await API.post("/settings/notebooklm/verify");
      _setStatus(statusEl, (resp.success ? "✅ " : "❌ ") + (resp.message || ""),
        resp.success ? "ok" : "err");
    } catch (err) {
      _setStatus(statusEl, "❌ " + err.message, "err");
    }
  }

  async function _switchProfile(email, bodyEl, statusEl) {
    _setStatus(statusEl, "Cambiando perfil…", "info");
    try {
      const resp = await API.post("/settings/notebooklm/switch", { email });
      _setStatus(statusEl, (resp.success ? "✅ " : "❌ ") + (resp.message || ""),
        resp.success ? "ok" : "err");
      if (resp.success) _refresh(bodyEl);
    } catch (err) {
      _setStatus(statusEl, "❌ " + err.message, "err");
    }
  }

  async function _updateStatus(bodyEl, statusEl) {
    _setStatus(statusEl, "Actualizando…", "info");
    try {
      const resp = await API.get("/settings/notebooklm/status");
      _status = resp;
      _renderBody(bodyEl, statusEl);
      _setStatus(statusEl, "", "");
    } catch (err) {
      _setStatus(statusEl, "❌ " + err.message, "err");
    }
  }

  function _refresh(bodyEl) {
    API.get("/settings/notebooklm/status")
      .then((s) => { _status = s; _renderBody(bodyEl); })
      .catch(() => {});
  }

  // ── Body rendering ─────────────────────────────────────────────────

  function _profileRow(p, bodyEl, statusEl) {
    const row = _el("div", { class: "nb-profile-row" }, [
      _el("span", { class: "nb-profile-email" }, [
        document.createTextNode(p.email),
      ]),
    ]);
    if (p.active) {
      row.appendChild(_text("span", "nb-profile-badge", "activo"));
    } else {
      row.appendChild(_btn("Usar", "", () => _switchProfile(p.email, bodyEl, statusEl)));
    }
    row.appendChild(_btn("🗑", "", () => _deleteProfile(p.email, bodyEl, statusEl)));
    return row;
  }

  async function _deleteProfile(email, bodyEl, statusEl) {
    if (!window.confirm(`¿Eliminar el perfil '${email}'?`)) return;
    _setStatus(statusEl, "Eliminando…", "info");
    try {
      const resp = await API.del("/settings/notebooklm/profile/" + encodeURIComponent(email));
      _setStatus(statusEl, (resp.success ? "✅ " : "❌ ") + (resp.message || ""),
        resp.success ? "ok" : "err");
      if (resp.success) _refresh(bodyEl);
    } catch (err) {
      _setStatus(statusEl, "❌ " + err.message, "err");
    }
  }

  function _renderBody(bodyEl, statusEl) {
    bodyEl.innerHTML = "";
    const s = _status || {};

    // Connection badge
    const conn = s.connected
      ? _text("div", "nb-conn-badge nb-conn-ok", "🟢 Conectado: " + (s.active_profile || "?"))
      : _text("div", "nb-conn-badge nb-conn-off", "🔴 Sin conexión activa");
    bodyEl.appendChild(conn);

    // Profiles list
    const profiles = s.profiles || [];
    const listTitle = _text("h3", "ss-settings-sub", "Perfiles (" + profiles.length + ")");
    bodyEl.appendChild(listTitle);
    if (profiles.length) {
      profiles.forEach((p) => bodyEl.appendChild(_profileRow(p, bodyEl, statusEl)));
    } else {
      bodyEl.appendChild(_text("p", "ss-settings-hint",
        "Aún no hay perfiles. Sube un storage_state.json o usa el script de Chrome."));
    }

    // Actions
    const act = _el("div", { class: "nb-actions" }, [
      _btn("🔍 Verificar", "", () => _verify(statusEl)),
      _btn("🔄 Actualizar", "", () => _updateStatus(bodyEl, statusEl)),
      _btn("🌐 Iniciar sesión", "", () => _showLogin(bodyEl, statusEl)),
      _btn("📥 Script Chrome", "", () => _showChromeExport(bodyEl, statusEl)),
      _btn("⬆️ Subir cookies", "", () => _showUpload(bodyEl, statusEl)),
    ]);
    bodyEl.appendChild(act);

    // Collapsible areas
    bodyEl.appendChild(_el("div", { id: "nb-login-area", class: "nb-collapse" }));
    bodyEl.appendChild(_el("div", { id: "nb-upload-area", class: "nb-collapse" }));
    bodyEl.appendChild(_el("div", { id: "nb-chrome-area", class: "nb-collapse" }));
  }

  function _showLogin(bodyEl, statusEl) {
    const area = bodyEl.querySelector("#nb-login-area");
    const chrome = bodyEl.querySelector("#nb-chrome-area");
    const upload = bodyEl.querySelector("#nb-upload-area");
    if (chrome) { chrome.style.display = "none"; chrome.innerHTML = ""; }
    if (upload) { upload.style.display = "none"; upload.innerHTML = ""; }
    if (!area) return;
    area.style.display = "block";

    const emailInput = _el("input", { type: "email", placeholder: "tu.email@gmail.com" });
    if (_status && _status.active_profile) emailInput.value = _status.active_profile;
    const status = _text("div", "nb-login-status", "");
    const startBtn = _btn("🌐 Abrir Chrome", "", () => _startLogin(emailInput, startBtn, status, bodyEl, statusEl));
    const hideBtn = _btn("↩ Cerrar", "", () => {
      _stopLoginPoll();
      area.style.display = "none"; area.innerHTML = "";
    });

    area.innerHTML = "";
    area.appendChild(_text("h4", "", "🌐 Iniciar sesión en Google"));
    area.appendChild(_text("p", "ss-settings-hint",
      "Se abre una ventana real de Google Chrome. Completa el login con tu cuenta "
      + "y vuelve aquí: la app detecta las cookies automáticamente."));
    area.appendChild(_text("label", "ss-settings-field", "Email de la cuenta:"));
    area.appendChild(emailInput);
    area.appendChild(_el("div", { class: "nb-inline-actions" }, [startBtn, hideBtn]));
    area.appendChild(status);
  }

  async function _startLogin(emailInput, startBtn, status, bodyEl, statusEl) {
    const account = emailInput.value.trim();
    if (!account) { status.textContent = "⚠️ Introduce el email de la cuenta primero"; return; }
    startBtn.disabled = true;
    startBtn.textContent = "⏳ Abriendo…";
    status.textContent = "Abriendo ventana de Google Chrome…";
    status.className = "nb-login-status nb-login-info";
    _stopLoginPoll();
    try {
      const resp = await API.post("/settings/notebooklm/login-start", { account });
      if (resp && resp.success) {
        status.textContent = "✅ " + (resp.message || "Ventana abierta. Completa el login en Chrome.");
        status.className = "nb-login-status nb-login-ok";
        _pollLogin(status, bodyEl, statusEl);
      } else {
        status.textContent = "❌ " + ((resp && resp.message) || "Error al abrir el login");
        status.className = "nb-login-status nb-login-err";
        startBtn.disabled = false;
        startBtn.textContent = "🌐 Abrir Chrome";
      }
    } catch (err) {
      status.textContent = "❌ " + err.message;
      status.className = "nb-login-status nb-login-err";
      startBtn.disabled = false;
      startBtn.textContent = "🌐 Abrir Chrome";
    }
  }

  function _pollLogin(status, bodyEl, statusEl) {
    _stopLoginPoll();
    _loginPoll = setInterval(async () => {
      try {
        const st = await API.get("/settings/notebooklm/login-status");
        if (st.storage_created) {
          // Cookies landed — stop polling, refresh list
          _stopLoginPoll();
          status.textContent = "✅ Cookies guardadas. Perfil actualizado.";
          status.className = "nb-login-status nb-login-ok";
          _refresh(bodyEl);
          _setStatus(statusEl, "✅ Login completado. Cookies guardadas.", "ok");
        } else if (st.login_complete) {
          // Browser closed without cookies — offer retry
          _stopLoginPoll();
          status.textContent = "⚠️ El navegador se cerró sin guardar cookies. Vuelve a intentarlo.";
          status.className = "nb-login-status nb-login-err";
        }
      } catch (err) {
        // transient — keep polling
      }
    }, 2000);
  }

  function _showChromeExport(bodyEl, statusEl) {
    const area = bodyEl.querySelector("#nb-chrome-area");
    const upload = bodyEl.querySelector("#nb-upload-area");
    const login = bodyEl.querySelector("#nb-login-area");
    if (upload) { upload.innerHTML = ""; upload.style.display = "none"; }
    if (login) { login.style.display = "none"; login.innerHTML = ""; _stopLoginPoll(); }
    if (!area) return;
    area.style.display = "block";

    const emailInput = _el("input", { type: "email", placeholder: "tu.email@gmail.com" });
    if (_status && _status.active_profile) emailInput.value = _status.active_profile;

    const commandBox = _text("code", "nb-chrome-command",
      "python3 ~/Downloads/export_storage_state.py");
    const status = _text("div", "nb-chrome-status", "");

    const downloadBtn = _btn("📥 Descargar script", "", async () => {
      const account = emailInput.value.trim();
      if (!account) { status.textContent = "⚠️ Introduce el email primero"; return; }
      try {
        const res = await fetch(
          API_URL + "/api/settings/notebooklm/export-script?account=" + encodeURIComponent(account),
          { headers: Auth.getHeaders(), credentials: "include" }
        );
        if (!res.ok) throw new Error("HTTP " + res.status);
        const blob = await res.blob();
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = "export_storage_state.py";
        a.click();
        URL.revokeObjectURL(a.href);
        status.textContent = "✅ Script descargado para " + account;
      } catch (err) {
        status.textContent = "❌ " + err.message;
      }
    });

    const copyBtn = _btn("📋 Copiar comando", "", () => {
      navigator.clipboard.writeText(commandBox.textContent)
        .then(() => { status.textContent = "✅ Comando copiado"; })
        .catch(() => { status.textContent = "❌ No se pudo copiar"; });
    });

    const refreshBtn = _btn("🔄 Buscar nuevo perfil", "", () => {
      _updateStatus(bodyEl, status);
    });

    const hideBtn = _btn("↩ Cerrar", "", () => {
      area.style.display = "none"; area.innerHTML = "";
    });

    area.innerHTML = "";
    area.appendChild(_text("h4", "", "💻 Exportar cookies desde Chrome (macOS)"));
    area.appendChild(_text("p", "ss-settings-hint",
      "1) Instala browser-cookie3 (pip3 install browser-cookie3) 2) Descarga el script "
      + "3) Ejecútalo en Terminal: cerrará Chrome y guardará las cookies en ~/.notebooklm/profiles/ "
      + "4) Vuelve y pulsa 'Buscar nuevo perfil'."));
    area.appendChild(_text("label", "ss-settings-field", "Email de la cuenta:"));
    area.appendChild(emailInput);
    area.appendChild(_el("div", { class: "nb-inline-actions" }, [downloadBtn, copyBtn, refreshBtn, hideBtn]));
    area.appendChild(commandBox);
    area.appendChild(status);
  }

  function _showUpload(bodyEl, statusEl) {
    const area = bodyEl.querySelector("#nb-upload-area");
    const chrome = bodyEl.querySelector("#nb-chrome-area");
    const login = bodyEl.querySelector("#nb-login-area");
    if (chrome) { chrome.style.display = "none"; chrome.innerHTML = ""; }
    if (login) { login.style.display = "none"; login.innerHTML = ""; _stopLoginPoll(); }
    if (!area) return;
    area.style.display = "block";

    const fileInput = _el("input", { type: "file", accept: ".json" });
    const status = _text("div", "nb-chrome-status", "");
    const uploadBtn = _btn("⬆️ Subir", "", async () => {
      const file = fileInput.files && fileInput.files[0];
      if (!file) { status.textContent = "⚠️ Selecciona un archivo"; return; }
      const fd = new FormData();
      fd.append("file", file);
      status.textContent = "⏳ Subiendo…";
      try {
        const res = await fetch(
          API_URL + "/api/settings/notebooklm/upload",
          { method: "POST", headers: Auth.getHeaders(), body: fd, credentials: "include" }
        );
        const data = await res.json().catch(() => ({}));
        status.textContent = (data.success ? "✅ " : "❌ ") + (data.message || "HTTP " + res.status);
        if (data.success) {
          area.style.display = "none"; area.innerHTML = "";
          _refresh(bodyEl);
        }
      } catch (err) {
        status.textContent = "❌ " + err.message;
      }
    });
    const hideBtn = _btn("↩ Cerrar", "", () => { area.style.display = "none"; area.innerHTML = ""; });

    area.innerHTML = "";
    area.appendChild(_text("h4", "", "⬆️ Subir storage_state.json"));
    area.appendChild(_text("p", "ss-settings-hint",
      "Exporta cookies desde cualquier navegador/equipo (extensión, Playwright, etc.) y súbelas aquí."));
    area.appendChild(fileInput);
    area.appendChild(_el("div", { class: "nb-inline-actions" }, [uploadBtn, hideBtn]));
    area.appendChild(status);
  }

  // ── Modal shell ────────────────────────────────────────────────────

  function _build() {
    const statusEl = _text("div", "ss-settings-status", "Cargando…");
    statusEl.classList.add("info");
    const bodyEl = _el("div", { class: "nb-body" });
    const refreshBtn = _btn("🔄 Refrescar", "", () => _updateStatus(bodyEl, statusEl));

    const actions = _el("div", { class: "ss-settings-actions" }, [
      statusEl, _el("div", { class: "ss-settings-spacer" }),
      refreshBtn,
      _btn("Cerrar", "", _close),
    ]);

    const modal = _el("div", { class: "ss-settings-modal", role: "dialog", "aria-modal": "true" }, [
      _text("h2", "", "🧠 NotebookLM"),
      _text("p", "ss-settings-sub",
        "Gestión de cuentas y cookies de NotebookLM. Las sesiones expiran: verifica la conexión antes de generar contenido."),
      bodyEl,
      actions,
    ]);

    const overlay = _el("div", { class: "ss-settings-overlay" }, [modal]);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) _close(); });
    overlay.addEventListener("keydown", (e) => { if (e.key === "Escape") _close(); });
    return { overlay, bodyEl, statusEl };
  }

  function open() {
    if (_overlay) return;
    const parts = _build();
    _overlay = parts.overlay;
    document.body.appendChild(_overlay);
    _renderBody(parts.bodyEl, parts.statusEl);
    API.get("/settings/notebooklm/status")
      .then((s) => { _status = s; _renderBody(parts.bodyEl, parts.statusEl); _setStatusDisconnected(parts.statusEl, s); })
      .catch((err) => { _setStatus(parts.statusEl, "❌ " + err.message, "err"); });
  }

  function _setStatusDisconnected(statusEl, s) {
    if (s && s.connected === false && (s.profiles || []).length === 0) {
      _setStatus(statusEl, "Sin conexión: sube cookies o usa el script de Chrome.", "info");
    } else if (s && s.connected === false) {
      _setStatus(statusEl, "Sesión guardada pero podría estar expirada — verifica.", "info");
    } else {
      _setStatus(statusEl, "", "");
    }
  }

  return { open };
})();

console.log("[Studyflow] notebooklm-settings.js loaded");