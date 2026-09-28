/* ============================== AGENDA MODULE ============================== */

const CATEGORIES = [
  { value: 'formal_study', label: 'Estudio formal',     cls: 'formal' },
  { value: 'self_study',   label: 'Estudio libre',      cls: 'self'   },
  { value: 'work',         label: 'Trabajo',            cls: 'work'   },
  { value: 'language',     label: 'Idioma',             cls: 'lang'   },
  { value: 'health',       label: 'Salud / Cuerpo',     cls: 'health' },
  { value: 'mind',         label: 'Mente / Ocio',       cls: 'mind'   },
  { value: 'project',      label: 'Proyecto personal',  cls: 'project'}
];

const CAT_BY_VALUE = CATEGORIES.reduce((m, c) => (m[c.value] = c, m), {});

const AgendaModule = {
  name: 'Agenda',

  sessions: [],
  modalOpen: false,
  editingId: null,

  // ---------- Lifecycle ----------
  async render() {
    const center = document.getElementById('agenda-center');
    if (!center) return;

    this.mountShell(center);
    try {
      await this.loadSessions();
      this.renderSessions();
    } catch (e) {
      console.error('[Agenda] load failed:', e);
      this.showLoadError();
    }
  },

  mountShell(center) {
    center.innerHTML = `
      <div class="agenda-header">
        <h2>📅 Agenda</h2>
        <button class="btn btn-primary" id="new-session-btn">+ Nueva Sesión</button>
      </div>
      <div id="sessions-list" class="sessions-list">
        <div class="empty-state">Cargando sesiones…</div>
      </div>
    `;
    document.getElementById('new-session-btn')
      ?.addEventListener('click', () => this.openModal(null));
  },

  showLoadError() {
    const list = document.getElementById('sessions-list');
    if (list) list.innerHTML = '<div class="empty-state">⚠️ No se pudieron cargar las sesiones</div>';
  },

  // ---------- Data ----------
  async loadSessions() {
    const data = await API.get('/agenda/sessions');
    this.sessions = Array.isArray(data?.sessions) ? data.sessions : [];
  },

  // ---------- List rendering ----------
  renderSessions() {
    const list = document.getElementById('sessions-list');
    if (!list) return;

    if (this.sessions.length === 0) {
      list.innerHTML = `
        <div class="empty-state">
          <span class="big">🗓️</span>
          <h2>No hay sesiones todavía</h2>
          <p>Pulsa <strong>+ Nueva Sesión</strong> para crear la primera.</p>
        </div>`;
      return;
    }

    const esc = (window.App && window.App.UI && window.App.UI.escHtml) || (s => String(s ?? ''));
    list.innerHTML = this.sessions.map(s => this.renderCardHtml(s, esc)).join('');
    this.attachCardHandlers();
  },

  renderCardHtml(s, esc) {
    const start = this.parseISO(s.start_time);
    const end   = this.parseISO(s.end_time);
    const cat   = CAT_BY_VALUE[s.category];
    const catCls = cat ? cat.cls : 'formal';
    const catLabel = cat ? cat.label : (s.category || '—');
    const dateStr = start ? this.formatDate(start) : '—';
    const timeStr = (start && end)
      ? `${this.formatTime(start)} – ${this.formatTime(end)}`
      : '—';

    return `
      <article class="session-card" data-id="${s.id}">
        <div class="session-card-top">
          <div class="session-card-title">${esc(s.title || 'Sin título')}</div>
          <div class="session-card-actions">
            <button class="edit"   data-act="edit"   data-id="${s.id}" aria-label="Editar">✏️</button>
            <button class="delete" data-act="delete" data-id="${s.id}" aria-label="Eliminar">🗑</button>
          </div>
        </div>
        <div class="session-card-meta">
          <span>📅 ${esc(dateStr)}</span>
          <span>⏰ ${esc(timeStr)}</span>
          <span class="cat bg-${catCls}">${esc(catLabel)}</span>
        </div>
        ${s.description ? `<div class="session-card-desc">${esc(s.description)}</div>` : ''}
      </article>`;
  },

  attachCardHandlers() {
    document.querySelectorAll('.session-card-actions [data-act="edit"]').forEach(btn => {
      btn.addEventListener('click', () => {
        const id = Number(btn.dataset.id);
        const session = this.sessions.find(x => x.id === id);
        if (session) this.openModal(session);
      });
    });
    document.querySelectorAll('.session-card-actions [data-act="delete"]').forEach(btn => {
      btn.addEventListener('click', () => {
        const id = Number(btn.dataset.id);
        const session = this.sessions.find(x => x.id === id);
        this.confirmDelete(session);
      });
    });
  },

  // ---------- Delete ----------
  async confirmDelete(session) {
    if (!session) return;
    const yes = window.confirm(`¿Eliminar la sesión "${session.title || 'Sin título'}"?`);
    if (!yes) return;
    try {
      await API.del(`/agenda/sessions/${session.id}`);
      await this.loadSessions();
      this.renderSessions();
    } catch (e) {
      console.error('[Agenda] delete failed:', e);
      alert(`No se pudo eliminar: ${e.message || e}`);
    }
  },

  // ---------- Modal ----------
  openModal(session) {
    this.editingId = session ? session.id : null;
    const isEdit = Boolean(session);
    const initial = this.formInitialState(session);

    const overlay = document.createElement('div');
    overlay.className = 'overlay open';
    overlay.id = 'session-modal-overlay';
    overlay.innerHTML = `
      <div class="omodal" role="dialog" aria-modal="true" aria-label="Nueva sesión">
        <div class="omodal-head">
          <h3>${isEdit ? '✏️ Editar sesión' : '➕ Nueva sesión'}</h3>
          <button class="btn" data-act="close" aria-label="Cerrar">✕</button>
        </div>
        <div class="omodal-body">
          <form class="modal-form" id="session-form" autocomplete="off">
            <div class="form-group">
              <label for="sf-title">Título *</label>
              <input id="sf-title" name="title" type="text" required maxlength="255" placeholder="Ej. Estudio Java" />
            </div>

            <div class="form-group">
              <label for="sf-category">Categoría</label>
              <select id="sf-category" name="category">
                ${CATEGORIES.map(c =>
                  `<option value="${c.value}">${c.label}</option>`).join('')}
              </select>
            </div>

            <div class="form-group">
              <label for="sf-date">Fecha *</label>
              <input id="sf-date" name="date" type="date" required />
            </div>

            <div class="form-row">
              <div class="form-group">
                <label for="sf-start">Hora inicio *</label>
                <input id="sf-start" name="start_time" type="time" required />
              </div>
              <div class="form-group">
                <label for="sf-end">Hora fin *</label>
                <input id="sf-end" name="end_time" type="time" required />
              </div>
            </div>

            <div class="form-group">
              <label for="sf-desc">Notas</label>
              <textarea id="sf-desc" name="description" rows="3" maxlength="2000" placeholder="Detalles, objetivos…"></textarea>
            </div>

            <div class="form-error" id="sf-error"></div>

            <div class="form-actions">
              <button type="button" class="btn" data-act="close">Cancelar</button>
              <button type="submit" class="btn btn-primary">${isEdit ? 'Guardar cambios' : 'Crear sesión'}</button>
            </div>
          </form>
        </div>
      </div>
    `;
    document.body.appendChild(overlay);
    this.modalOpen = true;
    this.fillForm(initial);
    this.bindModalEvents(overlay);
  },

  formInitialState(session) {
    if (!session) {
      return { date: this.todayStr(), start_time: '09:00', end_time: '10:00', category: 'formal_study' };
    }
    const start = this.parseISO(session.start_time);
    const end   = this.parseISO(session.end_time);
    return {
      title:       session.title || '',
      category:    session.category || 'formal_study',
      date:        start ? this.toDateInput(start) : this.todayStr(),
      start_time:  start ? this.toTimeInput(start) : '09:00',
      end_time:    end   ? this.toTimeInput(end)   : '10:00',
      description: session.description || ''
    };
  },

  fillForm(state) {
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.value = v ?? ''; };
    set('sf-title', state.title);
    set('sf-category', state.category);
    set('sf-date', state.date);
    set('sf-start', state.start_time);
    set('sf-end', state.end_time);
    set('sf-desc', state.description);
  },

  bindModalEvents(overlay) {
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay || e.target.dataset.act === 'close') {
        this.closeModal();
      }
    });
    document.getElementById('session-form').addEventListener('submit', (e) => {
      e.preventDefault();
      this.submitForm();
    });
    document.addEventListener('keydown', this.escClose);
  },

  escClose: (e) => {
    if (e.key === 'Escape' && AgendaModule.modalOpen) AgendaModule.closeModal();
  },

  closeModal() {
    const overlay = document.getElementById('session-modal-overlay');
    if (overlay) overlay.remove();
    this.modalOpen = false;
    this.editingId = null;
    document.removeEventListener('keydown', this.escClose);
  },

  showFieldError(msg) {
    const el = document.getElementById('sf-error');
    if (el) { el.textContent = msg; el.style.display = 'block'; }
  },

  // ---------- Form submit ----------
  async submitForm() {
    const errEl = document.getElementById('sf-error');
    if (errEl) errEl.style.display = 'none';

    const get = (id) => (document.getElementById(id)?.value ?? '').trim();

    const title       = get('sf-title');
    const category    = get('sf-category');
    const date        = get('sf-date');
    const start_time  = get('sf-start');
    const end_time    = get('sf-end');
    const description = get('sf-desc');

    if (!title)    { this.showFieldError('El título es obligatorio.'); return; }
    if (!date)     { this.showFieldError('La fecha es obligatoria.');  return; }
    if (!start_time || !end_time) { this.showFieldError('Indica hora de inicio y fin.'); return; }

    const startISO = this.combineDateTime(date, start_time);
    const endISO   = this.combineDateTime(date, end_time);

    if (new Date(endISO) <= new Date(startISO)) {
      this.showFieldError('La hora de fin debe ser posterior a la de inicio.');
      return;
    }

    const payload = {
      title,
      category,
      start_time: startISO,
      end_time: endISO,
      description: description || null
    };

    const submitBtn = document.querySelector('#session-form button[type="submit"]');
    if (submitBtn) submitBtn.disabled = true;

    try {
      if (this.editingId) {
        await API.patch(`/agenda/sessions/${this.editingId}`, payload);
      } else {
        await API.post('/agenda/sessions', payload);
      }
      this.closeModal();
      await this.loadSessions();
      this.renderSessions();
    } catch (e) {
      console.error('[Agenda] submit failed:', e);
      this.showFieldError(`No se pudo guardar: ${e.message || e}`);
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  },

  // ---------- Time helpers ----------
  parseISO(s) {
    if (!s) return null;
    const d = new Date(s);
    return isNaN(d.getTime()) ? null : d;
  },

  todayStr() {
    const d = new Date();
    const pad = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  },

  toDateInput(d) {
    const pad = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  },

  toTimeInput(d) {
    const pad = n => String(n).padStart(2, '0');
    return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  },

  combineDateTime(date, time) {
    // Returns ISO 8601 local datetime (no Z suffix) → backend parses via fromisoformat.
    return `${date}T${time}:00`;
  },

  formatDate(d) {
    return d.toLocaleDateString('es-ES', { weekday: 'short', day: 'numeric', month: 'short' });
  },

  formatTime(d) {
    const pad = n => String(n).padStart(2, '0');
    return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }
};

window.AgendaModule = AgendaModule;
