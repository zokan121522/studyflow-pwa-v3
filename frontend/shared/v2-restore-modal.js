/* ============================== V2 RESTORE MODAL ==============================
 * Preview + run a v2 → v3 migration (issue #8).
 *
 * A v2 backup is a PostgreSQL COPY dump, not a v3 backup.json. It has no
 * per-item selection tree, so there is nothing to tick: the import is
 * all-or-nothing inside one transaction. What this modal does is make the
 * consequences explicit before the user commits — especially the orphans
 * that will be dropped, which is 267 blocks in a real backup.
 *
 * Split from backup-restore.js to keep both files under the 500-line cap.
 */
window.V2Restore = (function () {
  "use strict";

  const CSS = `
  .v2m-backdrop{position:fixed;inset:0;background:rgba(0,0,0,.6);display:flex;
    align-items:center;justify-content:center;z-index:10000;padding:20px}
  .v2m{background:var(--bg,#1a1a1a);color:var(--text,#eee);border-radius:12px;
    width:min(680px,100%);max-height:88vh;overflow:auto;padding:22px;
    box-shadow:0 20px 60px rgba(0,0,0,.5)}
  .v2m h3{margin:0 0 6px;font-size:1.15em}
  .v2m-sub{opacity:.75;font-size:.85em;margin-bottom:16px}
  .v2m-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));
    gap:8px;margin:14px 0}
  .v2m-cell{background:rgba(127,127,127,.12);border-radius:8px;padding:10px 12px}
  .v2m-cell b{display:block;font-size:1.3em}
  .v2m-cell span{font-size:.75em;opacity:.75}
  .v2m-warn{background:rgba(255,170,0,.12);border-left:3px solid #fa0;
    border-radius:6px;padding:10px 12px;margin:12px 0;font-size:.85em}
  .v2m-warn b{color:#fa0}
  .v2m-ok{background:rgba(80,200,120,.12);border-left:3px solid #3c3;
    border-radius:6px;padding:10px 12px;margin:12px 0;font-size:.85em}
  .v2m-actions{display:flex;gap:10px;justify-content:flex-end;margin-top:18px}
  .v2m-actions button{padding:9px 18px;border-radius:8px;border:1px solid;
    background:transparent;cursor:pointer;font-size:.9em}
  .v2m-actions .br-cancel{border-color:#888;opacity:.8}
  .v2m-actions .br-go{background:#3b82f6;border-color:#3b82f6;color:#fff;font-weight:600}
  .v2m-actions .br-go[disabled]{opacity:.45;cursor:progress}
  .v2m-list{margin:6px 0 0;padding-left:20px;font-size:.8em;opacity:.85;
    max-height:130px;overflow:auto}
  .v2m-progress{height:5px;background:rgba(127,127,127,.2);border-radius:3px;
    overflow:hidden;margin-top:14px;display:none}
  .v2m-progress.on{display:block}
  .v2m-progress i{display:block;height:100%;width:35%;background:#3b82f6;
    animation:v2slide 1.1s infinite ease-in-out}
  @keyframes v2slide{0%{transform:translateX(-100%)}100%{transform:translateX(320%)}}`;

  function ensureStyle() {
    if (document.getElementById("v2m-style")) return;
    const s = document.createElement("style");
    s.id = "v2m-style";
    s.textContent = CSS;
    document.head.appendChild(s);
  }

  function esc(s) {
    const d = document.createElement("div");
    d.textContent = String(s == null ? "" : s);
    return d.innerHTML;
  }

  function fmt(n) {
    return typeof n === "number" ? n.toLocaleString("es-ES") : (n == null ? "0" : esc(n));
  }

  function headers() {
    const h = { Accept: "application/json" };
    const auth = window.Auth && window.Auth.getHeaders();
    if (auth && auth.Authorization) h.Authorization = auth.Authorization;
    return h;
  }

  function close(backdrop) {
    if (backdrop) backdrop.remove();
  }

  /**
   * Show the v2 preview. `plan` is the /api/backup/v2/scan payload.
   * `onConfirm(file)` runs the import when the user confirms.
   */
  function open(plan, file, onDone) {
    ensureStyle();
    const counts = plan.counts || plan.tables || {};
    const skipped = plan.skipped || {};
    const secrets = plan.secrets_dropped || plan.not_migrated || [];

    const cells = Object.entries(counts)
      .filter(([, v]) => typeof v === "number" && v > 0)
      .map(([k, v]) => `<div class="v2m-cell"><b>${fmt(v)}</b>
        <span>${esc(k)}</span></div>`).join("");

    const orphanBlocks = fmt(skipped.orphan_blocks || 0);
    const orphanTopics = fmt(skipped.orphan_topics || 0);
    const hasOrphans = (skipped.orphan_blocks || 0) > 0;

    const secretList = (Array.isArray(secrets) && secrets.length)
      ? `<ul class="v2m-list">${secrets.map((s) => `<li>${esc(s)}</li>`).join("")}</ul>`
      : "";

    const v2Only = skipped.v2_only_tables || [];

    const backdrop = document.createElement("div");
    backdrop.className = "v2m-backdrop";
    backdrop.innerHTML = `
      <div class="v2m">
        <h3>Restaurar backup de StudyFlow v2</h3>
        <div class="v2m-sub">
          Backup v2 detectado (<code>user_data.sql</code>). Se migrará al
          esquema actual en una única transacción: si algo falla, no queda
          nada a medias.
        </div>

        <div class="v2m-grid">${cells}</div>

        ${hasOrphans ? `<div class="v2m-warn">
          <b>Se omitirán ${orphanBlocks} bloques y ${orphanTopics} temas</b><br>
          Pertenecen a cursos que ya no existen en el backup (los borraste en
          v2). Su contenido no tiene curso al que pertenecer, así que no se
          importa. Si los quieres, dímelo y los recreo.
        </div>` : `<div class="v2m-ok">Sin huérfanos: se importará todo.</div>`}

        ${secrets.length ? `<div class="v2m-warn">
          <b>No se migrarán estos secretos</b>
          ${secretList}
        </div>` : ""}

        ${v2Only.length ? `<div class="v2m-warn" style="border-color:#888">
          Tablas de v2 sin equivalente en v3: <code>${esc(v2Only.join(", "))}</code>
        </div>` : ""}

        <div class="v2m-actions">
          <button class="br-cancel">Cancelar</button>
          <button class="br-go">Migrar a v3</button>
        </div>
        <div class="v2m-progress"><i></i></div>
      </div>`;

    document.body.appendChild(backdrop);

    const go = backdrop.querySelector(".br-go");
    const bar = backdrop.querySelector(".v2m-progress");
    const label = go.textContent;

    backdrop.querySelector(".br-cancel").onclick = () => close(backdrop);
    backdrop.onclick = (e) => { if (e.target === backdrop) close(backdrop); };

    go.onclick = async () => {
      go.disabled = true;
      go.textContent = "Migrando…";
      bar.classList.add("on");
      try {
        await onConfirm(file);
        close(backdrop);
      } catch (err) {
        go.disabled = false;
        go.textContent = label;
        bar.classList.remove("on");
        window.V2Restore.toast(`Error: ${err.message}`, true);
      }
    };
  }

  function toast(msg, isError) {
    let el = document.getElementById("v2m-toast");
    if (!el) {
      el = document.createElement("div");
      el.id = "v2m-toast";
      el.className = "br-toast";
      document.body.appendChild(el);
    }
    el.textContent = msg;
    el.classList.toggle("error", !!isError);
    el.classList.add("show");
    clearTimeout(el._t);
    el._t = setTimeout(() => el.classList.remove("show"), 4500);
  }

  // ─── transport (issue #8) ───────────────────────────────────────────
  // Lives here rather than in backup-restore.js so the whole v2 path
  // (scan, show, import) stays in one module.
  function _headers() {
    const t = localStorage.getItem("token");
    return t ? { Authorization: `Bearer ${t}` } : {};
  }

  async function scan(file) {
    const fd = new FormData();
    fd.append("file", file);
    const resp = await fetch(`${window.API_URL}/backup/v2/scan`, {
      method: "POST",
      headers: _headers(),
      credentials: "include",
      body: fd,
    });
    if (!resp.ok) return null;
    const data = await resp.json();
    return data.ok ? data.plan || {} : null;
  }

  async function runImport(file) {
    const fd = new FormData();
    fd.append("file", file);
    const resp = await fetch(`${window.API_URL}/backup/v2/import`, {
      method: "POST",
      headers: _headers(),
      credentials: "include",
      body: fd,
    });
    const data = await resp.json();
    if (!resp.ok || !data.ok) {
      throw new Error(data.error || `HTTP ${resp.status}`);
    }
    const n = (data.report || {}).counts || {};
    toast(
      `✅ Migrado: ${n.courses || 0} cursos, ${n.topics || 0} temas, ` +
      `${n.blocks || 0} bloques, ${n.sessions || 0} sesiones`
    );
    setTimeout(() => window.location.reload(), 1200);
    return data;
  }

  return { open, toast, scan, runImport };
})();
