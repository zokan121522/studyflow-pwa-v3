/* ============================== COURSES MODULE ============================== */

const CoursesModule = {
    name: 'Studyflow',
    
    async render() {
        const center = document.getElementById('studyflow-center');
        if (!center) return;
        
        center.innerHTML = '<div class="loading-spinner"></div><p>Cargando cursos...</p>';
        
        try {
            const data = await API.get('/courses');
            this.courses = data.courses || data;
            this.renderCourses(center);
        } catch (e) {
            console.error('[Courses] Failed to load courses:', e);
            center.innerHTML = '<div class="empty-state">⚠️ No se pudieron cargar los cursos</div>';
        }
    },
    
    renderCourses(container) {
        const courses = this.courses || [];
        
        if (courses.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <h2>📖 No hay cursos aún</h2>
                    <p>Crear un curso para comenzar a estudiar</p>
                </div>
            `;
            return;
        }
        
        const html = courses.map(c => `
            <div class="course-card" data-course-id="${c.id}">
                <div class="course-icon">${this.getCourseIcon(c.category)}</div>
                <div class="course-info">
                    <h3>${c.title || 'Sin título'}</h3>
                    <p>${c.description || 'Sin descripción'}</p>
                    <div class="course-meta">
                        <span class="badge">${c.blocks || 0} bloques</span>
                        <span class="badge badge-green">${c.progress || 0}%</span>
                    </div>
                </div>
            </div>
        `).join('');
        
        container.innerHTML = `
            <div class="courses-header">
                <h2>📖 Studyflow — Cursos</h2>
                <button class="btn btn-primary" id="new-course-btn">+ Nuevo Curso</button>
            </div>
            <div class="courses-grid">${html}</div>
        `;
        
        const btn = document.getElementById('new-course-btn');
        if (btn) {
            btn.addEventListener('click', () => {
                alert('Formulario de nuevo curso (por implementar)');
            });
        }
    },
    
    getCourseIcon(category) {
        const icons = { java: '☕', python: '🐍', web: '💻', data: '📊', general: '📚' };
        return icons[category] || '📚';
    }
};

window.CoursesModule = CoursesModule;
