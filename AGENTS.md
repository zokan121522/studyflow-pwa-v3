# studyflow-pwa-v3

> studyflow-hub v3 como Progressive Web App (PWA)
> Repo separado para SDD completo. Integración posterior en studyflow-hub.

## Stack
- **Backend**: Python 3.12 + Flask 3.1.1 + gunicorn (API)
- **Frontend**: PWA — SPA vanilla JS/CSS modular + Service Worker + Web App Manifest + cache offline
- **Database**: PostgreSQL 16 (psycopg2-binary)
- **Contenedores**: Docker Compose (Flask + PostgreSQL)
- **AI**: Ollama (qwen2.5:7b, llama3.2-3b) + edge-tts
- **PDF**: PyMuPDF, pdfplumber, weasyprint

## Variables de sesión (project-workflow)
```yaml
GITHUB_REPO: "zokan121522/studyflow-pwa-v3"
LOCAL_PATH: "/Users/jaimevilaplanarejon/orca/workspaces/studyflow-pwa-v3"
OBSIDIAN_PATH: "~/.studyflow-app/roadmap/Obsidian/02-StudyFlow/Projects/studyflow-pwa-v3"
BRANCH_PREFIX: "feat/"
BASE_BRANCH: "main"
SDD_MODE: "engram"
WORKTREE_ENABLED: true
DEPLOY_ENABLED: false
DEPLOY_SSH_HOST: "server-deploy"
```

## Convenciones
- Commits en inglés con conventional commits (`feat:`, `fix:`, `refactor:`, `chore:`)
- Una rama por fase: `feat/phase{N}-{descripcion}`
- SDD workflow completo: Consenso → Init → Planning → Apply → Verify → Archive → Retro

## 📋 Project Skills
Este proyecto tiene skills específicas en `.project-skills.md`.
Álvaro, al empezar una sesión:
1. Lee `.project-skills.md` completo
2. Durante el desarrollo, si el contexto coincide con alguna skill, cárgala con `read()`
3. Si no hay match, procede con reglas globales

## Stack del proyecto padre (studyflow-hub)
- Features existentes (levantar en v3): Agenda, Hábitos, Studyflow/Cursos, PDF Viewer, Auth, NotebookLM, Quiz, Cards, Todos, Audio/TTS
- Frontend actual: vanilla JS/CSS modular → v3 = PWA
- Backend actual: Flask + PG → v3 = API-first (Flask como API remota)
