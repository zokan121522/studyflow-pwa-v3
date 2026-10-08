/* Backup selector — modal de selección para el backup personal.

Carga GET /api/backup/mine/options (árbol + agenda + scopes + media con
bytes) y pinta un selector con tri-state. Al confirmar construye el body
que POST /api/backup/mine ya entiende (formato del modelo `Selection` del
backend) y llama a window.downloadBackup(body).

LAS REGLAS QUE REPLICA (idénticas a backup_resolve.py):
  * Un curso marcado arrastra sus temas y sus bloques.
  * Un tema marcado arrastra sus bloques.
  * Un bloque marcado arrastra su tema (y el tema, su curso) — padres
    viajan con hijos, porque un tema sin su curso no restaura.
  * "agenda" es un switch aparte del árbol. Marcarla envía weeks/days/
    sessions; los huérfanos (día sin semana, sesión sin día) viajan en su
    propio contenedor si están marcados.
  * Sin selección (todo vacío) el body se omite: POST sin body = backup
    completo exacto, que es el comportamiento histórico.
La estimación en vivo suma bloques (content_bytes), filas de scopes
(est. por fila del servidor) y media (bytes reales por categoría).
*/
(() => {
  "use strict";

  // Media: el endpoint sirve claves internas (CATEGORY_OF.values()),
  // el POST espera las del modelo Selection (MEDIA_CATEGORIES).
  const MEDIA_LABEL = {
    pdfs: "PDFs subidos",
    audio: "Audio generado (TTS)",
    infographics: "Infografías generadas",
  };
  const MEDIA_TO_BODY = { pdfs: "pdf", audio: "audio", infographics: "infographic" };
  const SCOPE_LABEL = {
    habits: "Hábitos",
    flashcards: "Flashcards",
    jsp: "JS Playground",
    agents_ai: "Agentes + IA",
    preferences: "Preferencias",
  };

  const el = (id) => document.getElementById(id);

  let options = null;           // payload de GET /mine/options
  // Modo restauración: cuando no es null, el modal viene desde inspect y el
  // botón GO entrega el body a este callback en vez de descargar un backup.
  // El handler de GO lo COPIA antes de llamar a close(), que lo limpia.
  let reviewConfirm = null;
  const treeSel = new Set();    // ids marcados del árbol (courses/topics/blocks)
  const weekSel = new Set();
  const daySel = new Set();
  const sessionSel = new Set();
  const scopeSel = new Set();
  const mediaSel = new Set();
  let unattributedOn = false;

  const fmt = (n) => {
    if (!n) return "0 B";
    const u = ["B", "KB", "MB", "GB"];
    const i = Math.min(Math.floor(Math.log(n) / Math.log(1024)), u.length - 1);
    return `${(n / 1024 ** i).toFixed(i === 0 ? 0 : 1)} ${u[i]}`;
  };

  // ── helpers de estado ────────────────────────────────────────────

  function hasAnything() {
    return treeSel.size > 0 || weekSel.size > 0 || daySel.size > 0 ||
      sessionSel.size > 0 || scopeSel.size > 0 || mediaSel.size > 0 ||
      unattributedOn;
  }

  function isFullSelection() {
    if (!options) return false;
    for (const c of options.tree.courses) {
      if (!treeSel.has(c.id)) return false;
      for (const t of c.topics) {
        if (!treeSel.has(t.id)) return false;
        for (const b of t.block_ids) if (!treeSel.has(b)) return false;
      }
      for (const b of c.blocks) if (!treeSel.has(b)) return false;
    }
    for (const m of options.tree.misc_blocks) {
      if (!treeSel.has(m.id)) return false;
    }
    for (const w of options.agenda.weeks) if (!weekSel.has(w.id)) return false;
    for (const d of options.agenda.orphan_days) if (!daySel.has(d.date)) return false;
    for (const s of options.agenda.orphan_sessions) if (!sessionSel.has(s.id)) return false;
    for (const s of Object.keys(SCOPE_LABEL)) {
      if (options.scopes[s] && !scopeSel.has(s)) return false;
    }
    for (const m of Object.keys(MEDIA_LABEL)) {
      if (options.media[m] && !mediaSel.has(m)) return false;
    }
    return true;
  }

  // ── estimación ────────────────────────────────────────────────────

  function treeEstimate() {
    // Bloques marcados directamente, sumando sus bytes.
    let blocks = 0, bytes = 0;
    for (const course of options.tree.courses) {
      const cOn = treeSel.has(course.id);
      for (const topic of course.topics) {
        const tOn = cOn || treeSel.has(topic.id);
        for (const bid of topic.block_ids || []) {
          const on = tOn || cOn || treeSel.has(bid);
          if (!on) continue;
          blocks += 1;
          bytes += (topic.block_sizes || {})[bid] || 0;
        }
      }
      if (cOn) {
        blocks += course.blocks.length;
        bytes += course.loose_bytes || 0;
      }
    }
    for (const mb of options.tree.misc_blocks) {
      if (treeSel.has(mb.id)) {
        blocks += 1;
        bytes += mb.content_bytes;
      }
    }
    return { blocks, bytes };
  }

  function estimate() {
    const t = treeEstimate();
    let scopeBytes = 0;
    for (const s of scopeSel) {
      scopeBytes += (options.scopes[s]?.rows || 0) * 512;
    }
    let mediaBytes = 0, mediaFiles = 0;
    for (const m of mediaSel) {
      const info = options.media[m];
      if (info) { mediaBytes += info.bytes; mediaFiles += info.files; }
    }
    let unatBytes = 0, unatFiles = 0;
    if (unattributedOn) {
      unatBytes = options.unattributed.bytes;
      unatFiles = options.unattributed.files;
    }
    return {
      blocks: t.blocks,
      bytes: t.bytes + scopeBytes + mediaBytes + unatBytes,
      scopeRows: [...scopeSel].reduce((a, s) => a + (options.scopes[s]?.rows || 0), 0),
      mediaFiles: mediaFiles + unatFiles,
    };
  }

  function renderEstimate() {
    const e = estimate();
    const total = fmt(e.bytes);
    const any = hasAnything();
    const mode = !any ? "sin selección" : isFullSelection() ? "completo" : "parcial";
    const isReview = reviewConfirm !== null;
    el("bksel-estimate").innerHTML =
      `<div class="bk-est-item"><span class="bk-est-label">Total estimado</span><b>${total}</b></div>` +
      `<div class="bk-est-item"><span class="bk-est-label">Bloques</span>${e.blocks}</div>` +
      `<div class="bk-est-item"><span class="bk-est-label">Filas extras</span>${e.scopeRows}</div>` +
      `<div class="bk-est-item"><span class="bk-est-label">Ficheros media</span>${e.mediaFiles}</div>` +
      `<div class="bk-est-item"><span class="bk-est-label">Modo</span><b>${
        !any ? "—" : mode === "completo" ? "completo" : "⚠️ parcial"
      }</b></div>`;
    const go = el("bksel-go");
    go.disabled = !any;
    go.title = any ? "" : "Marca al menos un elemento (o usa ☑️ Todo)";
    el("bksel-summary").textContent = !any
      ? (isReview
        ? "Nada seleccionado — no hay nada que restaurar"
        : "Nada seleccionado — el backup no se puede generar así")
      : isReview
        ? `Restauración ${mode} · ${e.blocks} bloques · ${fmt(e.bytes)}`
        : `Backup ${mode} · ${e.blocks} bloques · ${fmt(e.bytes)}`;
  }

  // El mismo modal sirve para exportar y para restaurar: solo cambian los
  // textos. El h3 no tiene id, así que se localiza por estructura.
  function setMode(mode) {
    const isReview = mode === "restore";
    const go = el("bksel-go");
    if (go) go.textContent = isReview ? "♻️ Restaurar selección" : "⬇️ Generar backup";
    const title = document.querySelector("#backup-selector-overlay .omodal-head h3");
    if (title) title.textContent = isReview
      ? "♻️ Restaurar backup — qué recupero"
      : "📦 Backup personal — qué incluyo";
  }

  // ── tri-state helpers ─────────────────────────────────────────────

  function setChecked(input, on) {
    input.checked = !!on;
    input.indeterminate = false;
  }

  // ── render ────────────────────────────────────────────────────────

  function renderTree() {
    const root = el("bksel-tree");
    const frag = document.createDocumentFragment();

    if (!options.tree.courses.length && !options.tree.misc_blocks.length) {
      frag.appendChild(document.createTextNode("Sin asignaturas todavía."));
    }

    for (const course of options.tree.courses) {
      const wrap = document.createElement("div");
      wrap.className = "bk-node";

      const label = document.createElement("div");
      label.className = "bk-node-label";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = treeSel.has(course.id);
      cb.addEventListener("change", () => {
        if (cb.checked) {
          treeSel.add(course.id);
          for (const t of course.topics) treeSel.add(t.id);
          for (const t of course.topics) for (const b of t.block_ids) treeSel.add(b);
          for (const b of course.blocks) treeSel.add(b);
        } else {
          treeSel.delete(course.id);
          for (const t of course.topics) treeSel.delete(t.id);
          for (const t of course.topics) for (const b of t.block_ids) treeSel.delete(b);
          for (const b of course.blocks) treeSel.delete(b);
        }
        renderTree();
        renderEstimate();
      });

      const title = document.createElement("span");
      title.textContent = course.title || course.id;
      title.style.fontWeight = "600";
      const meta = document.createElement("span");
      meta.className = "bk-meta";
      const nBlocks = course.blocks.length +
        course.topics.reduce((a, t) => a + t.blocks, 0);
      const cBytes = course.topics.reduce((a, t) => a + t.content_bytes, 0) +
        (course.loose_bytes || 0);
      meta.textContent = `${nBlocks} bloques · ${fmt(cBytes)}`;

      label.append(cb, title, meta);
      wrap.appendChild(label);

      const children = document.createElement("div");
      children.className = "bk-node-children";

      for (const topic of course.topics) {
        const tw = document.createElement("div");
        tw.className = "bk-node";
        const tl = document.createElement("div");
        tl.className = "bk-node-label";

        const tcb = document.createElement("input");
        tcb.type = "checkbox";
        tcb.checked = treeSel.has(topic.id);
        tcb.addEventListener("change", () => {
          if (tcb.checked) {
            treeSel.add(topic.id); treeSel.add(course.id);
            for (const b of topic.block_ids) treeSel.add(b);
          } else {
            treeSel.delete(topic.id);
            for (const b of topic.block_ids) treeSel.delete(b);
            // course stays only if no other topic of it is selected
            const any = course.topics.some((t) => treeSel.has(t.id));
            if (!any && !course.blocks.some((b) => treeSel.has(b))) {
              treeSel.delete(course.id);
            }
          }
          renderTree();
          renderEstimate();
        });

        const tt = document.createElement("span");
        tt.textContent = topic.title || topic.id;
        const tmeta = document.createElement("span");
        tmeta.className = "bk-meta";
        tmeta.textContent = `${topic.blocks} bloques · ${fmt(topic.content_bytes)}`;
        tl.append(tcb, tt, tmeta);
        tw.appendChild(tl);

        // one checkbox per block, indented — el endpoint da los ids y su
        // tamaño por separado (block_ids + block_sizes), sin labels.
        for (const bid of topic.block_ids || []) {
          const size = (topic.block_sizes || {})[bid] || 0;
          const bl = document.createElement("div");
          bl.className = "bk-node";
          const blabel = document.createElement("div");
          blabel.className = "bk-node-label bk-leaf";
          const bcb = document.createElement("input");
          bcb.type = "checkbox";
          bcb.checked = treeSel.has(bid);
          bcb.addEventListener("change", () => {
            if (bcb.checked) {
              treeSel.add(bid);
              treeSel.add(topic.id); treeSel.add(course.id);
            } else {
              treeSel.delete(bid);
              const anyBlock = (topic.block_ids || []).some((b) => treeSel.has(b));
              if (!anyBlock) treeSel.delete(topic.id);
              const anyTopic = course.topics.some((t) => treeSel.has(t.id));
              const anyLoose = course.blocks.some((b) => treeSel.has(b));
              if (!anyTopic && !anyLoose) treeSel.delete(course.id);
            }
            renderTree();
            renderEstimate();
          });
          const btxt = document.createElement("span");
          btxt.textContent = `${bid} · ${fmt(size)}`;
          btxt.style.color = "var(--text-secondary)";
          blabel.append(bcb, btxt);
          bl.appendChild(blabel);
          tw.appendChild(bl);
        }
        children.appendChild(tw);
      }

      // loose blocks (no topic) hang directly off the course
      for (const bid of course.blocks) {
        const bl = document.createElement("div");
        bl.className = "bk-node";
        const blabel = document.createElement("div");
        blabel.className = "bk-node-label bk-leaf";
        const bcb = document.createElement("input");
        bcb.type = "checkbox";
        bcb.checked = treeSel.has(bid);
        bcb.addEventListener("change", () => {
          if (bcb.checked) { treeSel.add(bid); treeSel.add(course.id); }
          else {
            treeSel.delete(bid);
            const anyLoose = course.blocks.some((b) => treeSel.has(b));
            const anyTopic = course.topics.some((t) => treeSel.has(t.id));
            if (!anyLoose && !anyTopic) treeSel.delete(course.id);
          }
          renderTree();
          renderEstimate();
        });
        const btxt = document.createElement("span");
        btxt.textContent = `${bid} · suelto`;
        btxt.style.color = "var(--text-secondary)";
        blabel.append(bcb, btxt);
        bl.appendChild(blabel);
        children.appendChild(bl);
      }

      wrap.appendChild(children);
      frag.appendChild(wrap);
    }

    // misc blocks: known id, unknown/nonexistent course
    if (options.tree.misc_blocks.length) {
      const wrap = document.createElement("div");
      wrap.className = "bk-node";
      const label = document.createElement("div");
      label.className = "bk-node-label";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = options.tree.misc_blocks.every((m) => treeSel.has(m.id));
      cb.addEventListener("change", () => {
        for (const m of options.tree.misc_blocks) {
          if (cb.checked) treeSel.add(m.id); else treeSel.delete(m.id);
        }
        renderTree();
        renderEstimate();
      });
      const title = document.createElement("span");
      title.textContent = "Bloques sin asignatura";
      title.style.fontWeight = "600";
      const meta = document.createElement("span");
      meta.className = "bk-meta";
      meta.textContent = `${options.tree.misc_blocks.length}`;
      label.append(cb, title, meta);
      wrap.appendChild(label);
      const children = document.createElement("div");
      children.className = "bk-node-children";
      for (const m of options.tree.misc_blocks) {
        const bl = document.createElement("div");
        bl.className = "bk-node";
        const blabel = document.createElement("div");
        blabel.className = "bk-node-label bk-leaf";
        const bcb = document.createElement("input");
        bcb.type = "checkbox";
        bcb.checked = treeSel.has(m.id);
        bcb.addEventListener("change", () => {
          if (bcb.checked) treeSel.add(m.id); else treeSel.delete(m.id);
          renderTree();
          renderEstimate();
        });
        const btxt = document.createElement("span");
        btxt.textContent = `${m.label || m.id} · ${fmt(m.content_bytes)}`;
        btxt.style.color = "var(--text-secondary)";
        blabel.append(bcb, btxt);
        bl.appendChild(blabel);
        children.appendChild(bl);
      }
      wrap.appendChild(children);
      frag.appendChild(wrap);
    }

    root.replaceChildren(frag);
  }

  function renderAgenda() {
    const root = el("bksel-agenda");
    const frag = document.createDocumentFragment();

    if (options.agenda.weeks.length ||
        options.agenda.orphan_days.length ||
        options.agenda.orphan_sessions.length) {

      const all = document.createElement("div");
      all.className = "bk-node";
      const lab = document.createElement("div");
      lab.className = "bk-node-label";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      const allOn = options.agenda.weeks.every((w) => weekSel.has(w.id)) &&
        options.agenda.orphan_days.every((d) => daySel.has(d.date)) &&
        options.agenda.orphan_sessions.every((s) => sessionSel.has(s.id)) &&
        options.agenda.weeks.length + options.agenda.orphan_days.length +
          options.agenda.orphan_sessions.length > 0;
      cb.checked = allOn;
      cb.addEventListener("change", () => {
        if (cb.checked) {
          for (const w of options.agenda.weeks) weekSel.add(w.id);
          for (const d of options.agenda.orphan_days) daySel.add(d.date);
          for (const s of options.agenda.orphan_sessions) sessionSel.add(s.id);
        } else {
          weekSel.clear(); daySel.clear(); sessionSel.clear();
        }
        renderAgenda();
        renderEstimate();
      });
      const title = document.createElement("span");
      title.textContent = "Agenda completa";
      title.style.fontWeight = "600";
      const meta = document.createElement("span");
      meta.className = "bk-meta";
      const t = options.agenda.totals;
      meta.textContent =
        `${t.weeks} semanas · ${t.days} días · ${t.sessions} sesiones`;
      lab.append(cb, title, meta);
      all.appendChild(lab);
      frag.appendChild(all);
    } else {
      frag.appendChild(document.createTextNode("Sin agenda todavía."));
    }

    // weeks are the granular unit; days/sessions ride with them
    for (const w of options.agenda.weeks) {
      const wnode = document.createElement("div");
      wnode.className = "bk-node";
      const wlab = document.createElement("div");
      wlab.className = "bk-node-label";
      const wcb = document.createElement("input");
      wcb.type = "checkbox";
      wcb.checked = weekSel.has(w.id);
      wcb.addEventListener("change", () => {
        if (wcb.checked) weekSel.add(w.id); else weekSel.delete(w.id);
        renderAgenda();
        renderEstimate();
      });
      const wt = document.createElement("span");
      wt.textContent = w.id;
      const wmeta = document.createElement("span");
      wmeta.className = "bk-meta";
      wmeta.textContent = `${w.days} días · ${w.sessions} sesiones`;
      wlab.append(wcb, wt, wmeta);
      wnode.appendChild(wlab);
      frag.appendChild(wnode);
    }

    // orphans (day without week, session without day)
    if (options.agenda.orphan_days.length) {
      const dlab = document.createElement("div");
      dlab.className = "bk-node-label";
      const dcb = document.createElement("input");
      dcb.type = "checkbox";
      dcb.checked = options.agenda.orphan_days.every((d) => daySel.has(d.date));
      dcb.addEventListener("change", () => {
        for (const d of options.agenda.orphan_days) {
          if (dcb.checked) daySel.add(d.date); else daySel.delete(d.date);
        }
        renderAgenda();
        renderEstimate();
      });
      const dt = document.createElement("span");
      dt.textContent =
        `${options.agenda.orphan_days.length} días huérfanos (sin semana)`;
      dt.style.color = "var(--text-secondary)";
      dlab.append(dcb, dt);
      frag.appendChild(dlab);
    }
    if (options.agenda.orphan_sessions.length) {
      const slab = document.createElement("div");
      slab.className = "bk-node-label";
      const scb = document.createElement("input");
      scb.type = "checkbox";
      scb.checked =
        options.agenda.orphan_sessions.every((s) => sessionSel.has(s.id));
      scb.addEventListener("change", () => {
        for (const s of options.agenda.orphan_sessions) {
          if (scb.checked) sessionSel.add(s.id); else sessionSel.delete(s.id);
        }
        renderAgenda();
        renderEstimate();
      });
      const st = document.createElement("span");
      st.textContent =
        `${options.agenda.orphan_sessions.length} sesiones huérfanas (sin día)`;
      st.style.color = "var(--text-secondary)";
      slab.append(scb, st);
      frag.appendChild(slab);
    }

    root.replaceChildren(frag);
  }

  function renderScopes() {
    const root = el("bksel-scopes");
    const frag = document.createDocumentFragment();
    for (const [name, label] of Object.entries(SCOPE_LABEL)) {
      const info = options.scopes[name];
      if (!info) continue;
      const card = document.createElement("label");
      card.className = "bk-scope-card";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = scopeSel.has(name);
      cb.addEventListener("change", () => {
        if (cb.checked) scopeSel.add(name); else scopeSel.delete(name);
        renderScopes();
        renderEstimate();
      });
      const sp = document.createElement("span");
      sp.className = "bk-scope-name";
      sp.textContent = label;
      const meta = document.createElement("span");
      meta.className = "bk-meta";
      meta.textContent = `${info.rows} filas`;
      card.append(cb, sp, meta);
      frag.appendChild(card);
    }
    root.replaceChildren(frag);
  }

  function renderMedia() {
    const root = el("bksel-media");
    const frag = document.createDocumentFragment();
    for (const [key, label] of Object.entries(MEDIA_LABEL)) {
      const info = options.media[key];
      if (!info) continue;
      const card = document.createElement("label");
      card.className = "bk-scope-card";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = mediaSel.has(key);
      cb.addEventListener("change", () => {
        if (cb.checked) mediaSel.add(key); else mediaSel.delete(key);
        renderMedia();
        renderEstimate();
      });
      const sp = document.createElement("span");
      sp.className = "bk-scope-name";
      sp.textContent = label;
      const meta = document.createElement("span");
      meta.className = "bk-meta";
      meta.textContent = `${info.files} ficheros · ${fmt(info.bytes)}`;
      card.append(cb, sp, meta);
      frag.appendChild(card);
    }
    // unattributed bucket: reported always, shipped on opt-in
    const uinfo = options.unattributed || { files: 0, bytes: 0 };
    if (uinfo.files > 0) {
      const card = document.createElement("label");
      card.className = "bk-scope-card";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = unattributedOn;
      cb.addEventListener("change", () => {
        unattributedOn = cb.checked;
        renderMedia();
        renderEstimate();
      });
      const sp = document.createElement("span");
      sp.className = "bk-scope-name";
      sp.textContent = "🧹 Sin atribuir (huérfanos)";
      sp.style.color = "var(--amber)";
      const meta = document.createElement("span");
      meta.className = "bk-meta";
      meta.textContent = `${uinfo.files} ficheros · ${fmt(uinfo.bytes)}`;
      card.append(cb, sp, meta);
      frag.appendChild(card);
    }
    root.replaceChildren(frag);
  }

  function renderAll() {
    renderTree();
    renderAgenda();
    renderScopes();
    renderMedia();
    renderEstimate();
  }

  // ── shortcuts ─────────────────────────────────────────────────────

  function selectAll() {
    treeSel.clear(); weekSel.clear(); daySel.clear(); sessionSel.clear();
    scopeSel.clear(); mediaSel.clear(); unattributedOn = false;
    for (const c of options.tree.courses) {
      treeSel.add(c.id);
      for (const t of c.topics) { treeSel.add(t.id); for (const b of t.block_ids) treeSel.add(b); }
      for (const b of c.blocks) treeSel.add(b);
    }
    for (const m of options.tree.misc_blocks) treeSel.add(m.id);
    for (const w of options.agenda.weeks) {
      weekSel.add(w.id);
    }
    for (const d of options.agenda.orphan_days) daySel.add(d.date);
    for (const s of options.agenda.orphan_sessions) sessionSel.add(s.id);
    for (const s of Object.keys(SCOPE_LABEL)) {
      if (options.scopes[s]) scopeSel.add(s);
    }
    for (const m of Object.keys(MEDIA_LABEL)) {
      if (options.media[m]) mediaSel.add(m);
    }
    // "todo" incluye los huérfanos; el bucket sin atribuir solo viaja si
    // el usuario lo marca (es un opt-in explícito por diseño).
    unattributedOn = false;
    renderAll();
  }

  function selectDatabaseOnly() {
    // Todo el árbol + todos los scopes + agenda, sin un byte de media.
    treeSel.clear(); weekSel.clear(); daySel.clear(); sessionSel.clear();
    scopeSel.clear(); mediaSel.clear(); unattributedOn = false;
    for (const c of options.tree.courses) {
      treeSel.add(c.id);
      for (const t of c.topics) { treeSel.add(t.id); for (const b of t.block_ids) treeSel.add(b); }
      for (const b of c.blocks) treeSel.add(b);
    }
    for (const m of options.tree.misc_blocks) treeSel.add(m.id);
    for (const w of options.agenda.weeks) weekSel.add(w.id);
    for (const d of options.agenda.orphan_days) daySel.add(d.date);
    for (const s of options.agenda.orphan_sessions) sessionSel.add(s.id);
    for (const s of Object.keys(SCOPE_LABEL)) {
      if (options.scopes[s]) scopeSel.add(s);
    }
    // media queda vacío a propósito
    renderAll();
  }

  function selectNone() {
    treeSel.clear(); weekSel.clear(); daySel.clear(); sessionSel.clear();
    scopeSel.clear(); mediaSel.clear(); unattributedOn = false;
    renderAll();
  }

  // ── body → Selection ──────────────────────────────────────────────

  function buildBody() {
    // La closure de "agenda completa" solo vale si hay agenda real;
    // el backend interpreta agenda:true como scope agenda activo.
    const courses = new Set(), topics = new Set(), blocks = new Set();
    for (const c of options.tree.courses) {
      if (treeSel.has(c.id)) courses.add(c.id);
      for (const t of c.topics) {
        if (treeSel.has(t.id)) topics.add(t.id);
        for (const b of t.block_ids) if (treeSel.has(b)) blocks.add(b);
      }
      for (const b of c.blocks) if (treeSel.has(b)) blocks.add(b);
    }
    for (const m of options.tree.misc_blocks) {
      if (treeSel.has(m.id)) blocks.add(m.id);
    }
    // parents: tema seleccionado arrastra su curso; bloque su tema/curso.
    for (const c of options.tree.courses) {
      const cHas = courses.has(c.id);
      if (!cHas) {
        const anyTopic = c.topics.some((t) => topics.has(t.id));
        const anyBlock = c.topics.some((t) => t.block_ids.some((b) => blocks.has(b)));
        const anyLoose = c.blocks.some((b) => blocks.has(b));
        if (anyTopic || anyBlock || anyLoose) courses.add(c.id);
      }
    }

    const weeks = new Set(weekSel);
    const days = new Set(daySel);
    const sessions = new Set(sessionSel);
    const scopes = new Set([...scopeSel].filter((s) => s !== "agenda"));
    if (weeks.size || days.size || sessions.size) scopes.add("agenda");

    // The selector never issues a bare full backup: an empty selection is a
    // state the Go button refuses. (Jarvis: el backend trata body vacío como
    // backup completo exacto — pero aquí preferimos exigir selección explícita
    // antes de mandar nada raro.)
    if (!hasAnything()) return null;

    const body = {};
    if (courses.size) body.courses = [...courses].sort();
    if (topics.size) body.topics = [...topics].sort();
    if (blocks.size) body.blocks = [...blocks].sort();
    if (weeks.size) body.weeks = [...weeks].sort();
    if (days.size) body.days = [...days].sort();
    if (sessions.size) body.sessions = [...sessions].sort();
    if (scopes.size) body.scopes = [...scopes].sort();
    if (mediaSel.size) body.media = [...mediaSel].map((k) => MEDIA_TO_BODY[k] || k).sort();
    if (unattributedOn) body.unattributed = true;
    return body;
  }

  // ── open/close ────────────────────────────────────────────────────

  async function open() {
    reviewConfirm = null;
    setMode("export");
    el("bksel-error").classList.add("hidden");
    el("bksel-loading").classList.remove("hidden");
    el("bksel-content").classList.add("hidden");
    el("backup-selector-overlay").classList.remove("hidden");

    try {
      const resp = await fetch("/api/backup/mine/options", {
        headers: { ...(window.App?.Auth?.authHeaders ? window.App.Auth.authHeaders() : {}) },
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ error: resp.statusText }));
        throw new Error(err.error || `HTTP ${resp.status}`);
      }
      options = await resp.json();
      // estado inicial: nada marcado = backup completo (comportamiento
      // histórico). "Todo" es un atajo explícito.
      selectNone();
      el("bksel-loading").classList.add("hidden");
      el("bksel-content").classList.remove("hidden");
      renderAll();
    } catch (e) {
      el("bksel-loading").classList.add("hidden");
      const errBox = el("bksel-error");
      errBox.textContent = "❌ No se pudieron cargar las opciones: " + e.message;
      errBox.classList.remove("hidden");
    }
  }

  // Restore: the caller already has the options (built from the zip by
  // /api/backup/mine/inspect). Default is "everything" — a full restore of
  // whatever the archive holds, which is the historical behaviour.
  function openReview(optionsPayload, onConfirm) {
    if (!optionsPayload || !optionsPayload.tree) return false;
    reviewConfirm = typeof onConfirm === "function" ? onConfirm : null;
    setMode("restore");
    el("bksel-error").classList.add("hidden");
    el("bksel-loading").classList.add("hidden");
    options = optionsPayload;
    selectAll(); // renders and ticks everything inside the archive
    el("bksel-content").classList.remove("hidden");
    el("backup-selector-overlay").classList.remove("hidden");
    return true;
  }

  function close() {
    reviewConfirm = null;
    setMode("export");
    el("backup-selector-overlay").classList.add("hidden");
  }

  function wire() {
    el("bksel-close")?.addEventListener("click", close);
    el("backup-selector-overlay")?.addEventListener("click", (e) => {
      if (e.target === e.currentTarget) close();
    });
    el("bksel-all")?.addEventListener("click", selectAll);
    el("bksel-db")?.addEventListener("click", selectDatabaseOnly);
    el("bksel-none")?.addEventListener("click", selectNone);
    el("bksel-go")?.addEventListener("click", () => {
      const body = buildBody();
      if (!body) return; // botón deshabilitado cuando no hay selección
      // Capture the callback BEFORE close(): close() clears reviewConfirm.
      const confirmFn = reviewConfirm;
      close();
      if (confirmFn) {
        confirmFn(body);
      } else if (typeof window.downloadBackup === "function") {
        window.downloadBackup(body);
      }
    });
  }

  // Export + restore share the same modal, so both entry points are exposed.
  window.BackupSelector = { open, openReview, close };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", wire);
  } else {
    wire();
  }
})();