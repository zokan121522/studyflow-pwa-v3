/* ============================== BACKUP / RESTORE ==============================
 * Local backup & restore (issue #7).
 *   #btn-backup  → GET /api/backup/export → ZIP download to disk
 *   #btn-restore → file picker (.zip) → POST /api/backup/upload (selection
 *                  tree) → review modal with hierarchical checkboxes +
 *                  per-conflict inline select (rename/replace/cancel) →
 *                  POST /api/backup/import → reload.
 *
 * v2 backups (issue #8) are a different format: a PostgreSQL COPY dump
 * instead of backup.json. They carry no per-item selection tree, so they
 * take a separate path: POST /api/backup/v2/scan previews the counts and
 * what will be skipped, then /api/backup/v2/import migrates everything in
 * one transaction. Both formats use the same file picker.
 * Loaded after app.js (needs window.API_URL, window.Auth).
 */
(function () {
  "use strict";

  const CONFLICT_OPTS = [
    ["rename", "Renombrar copia"],
    ["replace", "Reemplazar"],
    ["cancel", "Cancelar"],
  ];

  let _zipFile = null;      // File instance waiting for import
  let _tree = null;         // selection tree from upload
  let _sel = {              // checked temp ids per level
    courses: new Set(), topics: new Set(), blocks: new Set(), rest: new Set(),
  };
  let _conflicts = {};      // temp_id → rename|replace|cancel
  let _isV2 = false;        // current file is a v2 dump, not backup.json

  // ─── tiny toast ─────────────────────────────────────────────────────
  function toast(msg, isError) {
    let el = document.getElementById("br-toast");
    if (!el) {
      el = document.createElement("div");
      el.id = "br-toast";
      el.className = "br-toast";
      document.body.appendChild(el);
    }
    el.textContent = msg;
    el.classList.toggle("error", !!isError);
    el.classList.add("show");
    clearTimeout(el._t);
    el._t = setTimeout(() => el.classList.remove("show"), 3800);
  }

  // ─── Backup: download zip ─────────────────────────────────────────────
  async function doBackup() {
    const btn = document.getElementById("btn-backup");
    if (btn) btn.disabled = true;
    try {
      const resp = await fetch(`${window.API_URL}/backup/export`, {
        headers: window.Auth.getHeaders(),
        credentials: "include",
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.error || `HTTP ${resp.status}`);
      }
      const blob = await resp.blob();
      const cd = resp.headers.get("Content-Disposition") || "";
      const match = cd.match(/filename="?([^";]+)"?/);
      const name = match ? match[1] : `studyflow-backup-${new Date().toISOString().slice(0, 10)}.zip`;
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      toast(`📦 Backup guardado: ${name}`);
    } catch (e) {
      toast(`Backup falló: ${e.message}`, true);
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  // ─── Restore: picker → upload → review modal ──────────────────────────
  function openFilePicker() {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".zip,application/zip";
    input.onchange = async () => {
      const file = input.files && input.files[0];
      if (!file) return;
      await doUpload(file);
    };
    input.click();
  }

  // Multipart-safe headers. Auth.getHeaders() ALWAYS injects
  // `Content-Type: application/json`, which kills multipart parsing
  // (Werkzeug then sees empty request.files → "file is required").
  // Rule: on FormData uploads send ONLY Accept (+ Authorization).
  function _uploadHeaders() {
    const h = { Accept: "application/json" };
    const auth = window.Auth.getHeaders();
    if (auth && auth.Authorization) h.Authorization = auth.Authorization;
    return h;
  }

  async function doUpload(file) {
    const fd = new FormData();
    fd.append("file", file);
    toast("⬆️ Analizando backup…");
    let triedV2 = false;
    try {
      const resp = await fetch(`${window.API_URL}/backup/upload`, {
        method: "POST",
        headers: _uploadHeaders(),
        credentials: "include",
        body: fd,
      });
      const data = await resp.json();
      // v2 dumps are answered 415 + format:"v2". Branch explicitly rather
      // than relying on the catch: a v2 ZIP used to be read as a valid but
      // EMPTY v3 backup, so nothing threw and the modal showed "0 items".
      if (data.format === "v2") {
        triedV2 = true;
        if (await tryV2Scan(file)) return;
        toast(data.error || "No se pudo leer el backup v2", true);
        return;
      }
      if (!resp.ok || !data.ok) throw new Error(data.error || `HTTP ${resp.status}`);
      _zipFile = file;
      _isV2 = false;
      _tree = data.tree;
      _conflicts = {};
      preselectAll(data.tree);
      openReviewModal(data.meta || {}, data.conflicts || []);
    } catch (e) {
      // A v2 backup is not a v3 backup.json. Rather than showing an error,
      // try the v2 endpoint: the user may simply have grabbed an old ZIP.
      if (!triedV2 && (await tryV2Scan(file))) return;
      toast(`Error de análisis: ${e.message}`, true);
    }
  }

  // ─── v2 restore (issue #8) ───────────────────────────────────────────
  // Returns true when the file is a v2 dump and the modal was shown.
  async function tryV2Scan(file) {
    if (!window.V2Restore) return false;
    try {
      const plan = await window.V2Restore.scan(file);
      if (!plan) return false;
      _zipFile = file;
      _isV2 = true;
      window.V2Restore.open(plan, file, () => window.V2Restore.runImport(file));
      return true;
    } catch (err) {
      return false;
    }
  }

  function preselectAll(tree) {
    _sel = { courses: new Set(), topics: new Set(), blocks: new Set(), rest: new Set() };
    (tree.courses || []).forEach((c) => {
      _sel.courses.add(c.temp_id);
      (c.course_blocks || []).forEach((b) => _sel.blocks.add(b.temp_id));
      (c.topics || []).forEach((t) => {
        _sel.topics.add(t.temp_id);
        (t.blocks || []).forEach((b) => _sel.blocks.add(b.temp_id));
      });
    });
    if (tree.agenda && tree.agenda.count > 0) _sel.rest.add("agenda");
    if (tree.habits && (tree.habits.columns > 0 || tree.habits.entries > 0)) _sel.rest.add("habits");
    if (tree.todos && tree.todos.count > 0) _sel.rest.add("todos");
    if (tree.quiz && tree.quiz.questions > 0) _sel.rest.add("quiz");
    if (tree.quick_notes) _sel.rest.add("quick_notes");
    if (tree.user_settings) _sel.rest.add("user_settings");
  }

  // ─── Review modal ─────────────────────────────────────────────────────
  function openReviewModal(meta, conflicts) {
    const conflictMap = {};
    conflicts.forEach((c) => { conflictMap[c.temp_id] = c; });

    let overlay = document.getElementById("br-overlay");
    if (!overlay) {
      overlay = document.createElement("div");
      overlay.className = "overlay";
      overlay.id = "br-overlay";
      document.body.appendChild(overlay);
    }
    overlay.innerHTML = `
      <div class="omodal br-modal">
        <div class="omodal-head">
          <h3>⬆️ Restaurar backup</h3>
          <button class="br-close">✕</button>
        </div>
        <div class="omodal-body br-body">
          <div class="br-meta">
            ${meta.created_at ? `🕒 ${escHtml(meta.created_at.slice(0, 10))} · ` : ""}${meta.user_email ? escHtml(meta.user_email) : ""}
            <span class="br-ver">v${escHtml(String(meta.version || 1))}</span>
          </div>
          <div class="br-scroll">
            <div id="br-tree"></div>
          </div>
          <div class="br-footer">
            <span id="br-count" class="br-count"></span>
            <button class="ht-btn p" id="br-do">Restaurar selección</button>
          </div>
        </div>
      </div>`;
    overlay.classList.add("open");
    overlay.querySelector(".br-close").addEventListener("click", () =>
      overlay.classList.remove("open"));
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) overlay.classList.remove("open");
    });

    const treeEl = overlay.querySelector("#br-tree");
    renderTree(treeEl, conflictMap);
    const doBtn = overlay.querySelector("#br-do");
    doBtn.addEventListener("click", () => doImport());
    updateCount();
  }

  function conflictControl(tempId, level, existingId) {
    const choice = _conflicts[tempId] || "rename";
    const sel = CONFLICT_OPTS.map(([v, label]) =>
      `<option value="${v}" ${v === choice ? "selected" : ""}>${label}</option>`).join("");
    return `<select class="br-conflict" data-tid="${tempId}" data-level="${level}"
              data-existing="${existingId}" title="⚠️ Ya existe en tu cuenta">
              ${sel}
            </select>`;
  }

  function renderTree(treeEl, conflictMap) {
    let html = "";
    const courses = _tree.courses || [];
    if (!courses.length) {
      html += `<div class="br-empty">No hay asignaturas en este backup.</div>`;
    }
    courses.forEach((c, ci) => {
      const childTids = [];
      c.topics.forEach((t) => childTids.push(t.temp_id, ...t.blocks.map((b) => b.temp_id)));
      const allPicked = _sel.courses.has(c.temp_id);
      const cBadge = c.existing_id != null ? " ⚠️" : "";
      html += `<div class="br-row br-course" data-tid="${c.temp_id}">
        <label class="br-check">
          <input type="checkbox" data-tid="${c.temp_id}" ${allPicked ? "checked" : ""}>
          <span class="br-title">📚 ${escHtml(c.title)}${cBadge}</span>
        </label>
        ${c.existing_id != null ? conflictControl(c.temp_id, "course", c.existing_id) : ""}
      </div>`;
      (c.course_blocks || []).forEach((b, bi) => {
        const bPicked = _sel.blocks.has(b.temp_id);
        const bBadge = b.existing_id != null ? " ⚠️" : "";
        html += `<div class="br-row br-block" style="margin-left:36px;" data-tid="${b.temp_id}">
          <label class="br-check">
            <input type="checkbox" data-tid="${b.temp_id}" ${bPicked ? "checked" : ""}>
            <span class="br-title br-dim">▸ ${escHtml(b.title || b.type)}${bBadge}</span>
          </label>
          ${b.existing_id != null ? conflictControl(b.temp_id, "block", b.existing_id) : ""}
        </div>`;
      });
      c.topics.forEach((t, ti) => {
        const tPicked = _sel.topics.has(t.temp_id);
        const tBadge = t.existing_id != null ? " ⚠️" : "";
        html += `<div class="br-row br-topic" style="margin-left:18px;" data-tid="${t.temp_id}">
          <label class="br-check">
            <input type="checkbox" data-tid="${t.temp_id}" ${tPicked ? "checked" : ""}>
            <span class="br-title">📖 ${escHtml(t.title)}${tBadge}</span>
          </label>
          ${t.existing_id != null ? conflictControl(t.temp_id, "topic", t.existing_id) : ""}
        </div>`;
        (t.blocks || []).forEach((b, bi) => {
          const bPicked = _sel.blocks.has(b.temp_id);
          const bBadge = b.existing_id != null ? " ⚠️" : "";
          html += `<div class="br-row br-block" style="margin-left:36px;" data-tid="${b.temp_id}">
            <label class="br-check">
              <input type="checkbox" data-tid="${b.temp_id}" ${bPicked ? "checked" : ""}>
              <span class="br-title br-dim">▸ ${escHtml(b.title || b.type)}${bBadge}</span>
            </label>
            ${b.existing_id != null ? conflictControl(b.temp_id, "block", b.existing_id) : ""}
          </div>`;
        });
      });
    });

    // ── rest-of-data domains ─────────────────────────────
    html += `<div class="br-divider">Otros datos</div>`;
    const t = _tree;
    const restRows = [
      ["agenda", `🗓️ Agenda <span class="br-dim">(${t.agenda?.count || 0} sesiones)</span>`],
      ["habits", `✅ Hábitos <span class="br-dim">(${(t.habits?.columns || 0)} columnas</span> · <span class="br-dim">${t.habits?.entries || 0} registros)</span>`],
      ["todos", `📝 Tareas <span class="br-dim">(${t.todos?.count || 0})</span>`],
      ["quiz", `❓ Quiz <span class="br-dim">(${t.quiz?.questions || 0} preguntas)</span>`],
      ["quick_notes", "📌 Nota rápida"],
      ["user_settings", "⚙️ Calendarios / ajustes"],
    ];
    restRows.forEach(([key, label]) => {
      const show = key === "quick_notes" ? t.quick_notes : key === "user_settings" ? t.user_settings : true;
      if (!show) return;
      html += `<div class="br-row br-rest" data-tid="${key}">
        <label class="br-check">
          <input type="checkbox" data-tid="${key}" ${_sel.rest.has(key) ? "checked" : ""}>
          <span class="br-title">${label}</span>
        </label>
      </div>`;
    });

    treeEl.innerHTML = html;

    // wiring
    treeEl.querySelectorAll("input[type=checkbox]").forEach((cb) => {
      cb.addEventListener("change", () => {
        const tid = cb.dataset.tid;
        const level = resolveLevel(tid);
        if (cb.checked) _sel[level].add(tid);
        else _sel[level].delete(tid);
        // cascade to children when toggling a course or topic
        if (cb.checked && (level === "courses" || level === "topics")) {
          cascadeCheck(tid, true);
        } else if (!cb.checked && (level === "courses" || level === "topics")) {
          cascadeCheck(tid, false);
        }
        const row = cb.closest(".br-row");
        if (row) {
          const conflictSel = row.querySelector(".br-conflict");
          if (conflictSel) conflictSel.style.display = cb.checked ? "" : "none";
        }
        updateCount();
        syncCheckboxUI();
      });
    });
    treeEl.querySelectorAll(".br-conflict").forEach((sel) => {
      sel.addEventListener("change", () => {
        _conflicts[sel.dataset.tid] = sel.value;
      });
      const parentRow = sel.closest(".br-row");
      const cb = parentRow && parentRow.querySelector("input[type=checkbox]");
      if (cb && !cb.checked) sel.style.display = "none";
    });
    syncCheckboxUI();
  }

  function resolveLevel(tid) {
    if (tid.startsWith("c") && !tid.includes("t")) return "courses";
    if (tid.includes("b")) return "blocks";
    if (tid.includes("t")) return "topics";
    return "rest";
  }

  function cascadeCheck(tid, checked) {
    const courses = _tree.courses || [];
    if (resolveLevel(tid) === "courses") {
      const c = courses.find((x) => x.temp_id === tid);
      if (!c) return;
      (c.course_blocks || []).forEach((b) => _sel.blocks[checked ? "add" : "delete"](b.temp_id));
      (c.topics || []).forEach((t) => {
        _sel.topics[checked ? "add" : "delete"](t.temp_id);
        (t.blocks || []).forEach((b) => _sel.blocks[checked ? "add" : "delete"](b.temp_id));
      });
    } else {
      const allTopics = courses.flatMap((c) => c.topics || []);
      const t = allTopics.find((x) => x.temp_id === tid);
      if (!t) return;
      (t.blocks || []).forEach((b) => _sel.blocks[checked ? "add" : "delete"](b.temp_id));
    }
  }

  function syncCheckboxUI() {
    const courses = _tree.courses || [];
    const overlay = document.getElementById("br-overlay");
    if (!overlay) return;
    overlay.querySelectorAll(".br-course input[type=checkbox]").forEach((cb) => {
      const c = courses.find((x) => x.temp_id === cb.dataset.tid);
      if (!c) return;
      const any = c.topics.some((t) => _sel.topics.has(t.temp_id))
        || (c.course_blocks || []).some((b) => _sel.blocks.has(b.temp_id));
      const allTopics = c.topics.length > 0 && c.topics.every((t) => _sel.topics.has(t.temp_id))
        && c.topics.every((t) => (t.blocks || []).every((b) => _sel.blocks.has(b.temp_id)));
      const allCbs = (c.course_blocks || []).every((b) => _sel.blocks.has(b.temp_id));
      cb.checked = (c.topics.length > 0 && allTopics && allCbs)
        || (c.topics.length === 0 && allCbs && (c.course_blocks || []).length > 0);
    });
    overlay.querySelectorAll(".br-topic input[type=checkbox]").forEach((cb) => {
      const allTopics = courses.flatMap((c) => c.topics || []);
      const t = allTopics.find((x) => x.temp_id === cb.dataset.tid);
      if (!t) return;
      const blocks = t.blocks || [];
      const any = blocks.some((b) => _sel.blocks.has(b.temp_id));
      const all = blocks.length > 0 && blocks.every((b) => _sel.blocks.has(b.temp_id));
      cb.checked = all;
      cb.indeterminate = !all && any;
    });
  }

  function updateCount() {
    const total = _sel.courses.size + _sel.topics.size + _sel.blocks.size + _sel.rest.size;
    const el = document.getElementById("br-count");
    if (el) el.textContent = `${total} elementos seleccionados`;
  }

  // ─── Import ───────────────────────────────────────────────────────────
  function buildPayload() {
    const selection = {
      courses: [..._sel.courses],
      topics: [..._sel.topics],
      blocks: [..._sel.blocks],
      rest: [..._sel.rest],
    };
    const conflicts = {};
    Object.entries(_conflicts).forEach(([tid, action]) => {
      if (_sel.courses.has(tid) || _sel.topics.has(tid) || _sel.blocks.has(tid)) {
        conflicts[tid] = action;
      }
    });
    return { selection, conflicts };
  }

  async function doImport() {
    const { selection, conflicts } = buildPayload();
    if (!selection.courses.length && !selection.topics.length
        && !selection.blocks.length && !selection.rest.length) {
      toast("Selecciona al menos un elemento para restaurar", true);
      return;
    }
    const fd = new FormData();
    fd.append("file", _zipFile);
    fd.append("selection", JSON.stringify(selection));
    fd.append("conflicts", JSON.stringify(conflicts));
    const doBtn = document.getElementById("br-do");
    if (doBtn) { doBtn.disabled = true; doBtn.textContent = "Restaurando…"; }
    try {
      const resp = await fetch(`${window.API_URL}/backup/import`, {
        method: "POST",
        headers: _uploadHeaders(),
        credentials: "include",
        body: fd,
      });
      const data = await resp.json();
      if (!resp.ok || !data.ok) throw new Error(data.error || `HTTP ${resp.status}`);
      toast("✅ Restore completado. Recargando…");
      setTimeout(() => window.location.reload(), 600);
    } catch (e) {
      toast(`Restore falló: ${e.message}`, true);
      if (doBtn) { doBtn.disabled = false; doBtn.textContent = "Restaurar selección"; }
    }
  }

  // ─── wire buttons (after DOM ready) ─────────────────────────────────
  function init() {
    const btnBackup = document.getElementById("btn-backup");
    const btnRestore = document.getElementById("btn-restore");
    if (btnBackup) {
      btnBackup.title = "Backup — guardar datos locales (ZIP)";
      btnBackup.addEventListener("click", doBackup);
    }
    if (btnRestore) {
      btnRestore.title = "Restore — restaurar desde un ZIP local";
      btnRestore.addEventListener("click", openFilePicker);
    }
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();