FROM python:3.12-slim

# ─────────────────────────────────────────────────────────────────────
# studyflow-pwa-v3 · imagen del backend
#
# No tocar solo el requirements: hay tres dependencias que NO son de pip
# y que sin ellas fallan en runtime (no en build, que es lo que confunde):
#
#   1. edge-tts  → routes/tts.py lo invoca como binario del sistema
#                  (`cmd = ['edge-tts', ...]`). El paquete de pip instala
#                  la librería, no el ejecutable.
#   2. playwright→ NotebookLM abre un Chromium headless para las
#                  infografías. Sin `playwright install chromium` el
#                  import funciona y la llamada revienta.
#   3. libpq     → psycopg2 (aunque uses -binary, el pool de Postgres
#                  necesita las herramientas en runtime para el DDL).
# ─────────────────────────────────────────────────────────────────────

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PATH=/usr/local/bin:$PATH \
    # El repo se monta en /srv (ver compose). server.py hace imports
    # planos ("from routes... import") Y algunos módulos usan
    # "from backend.routes... import". scripts/dev-server.sh resuelve
    # ambos con PYTHONPATH="$REPO_ROOT/backend:$REPO_ROOT"; aquí igual.
    PYTHONPATH=/srv/backend:/srv

WORKDIR /srv/backend

# Capa de sistema: una sola vez, cacheada mientras el Dockerfile no cambie.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        curl \
        # nodejs para la CLI de edge-tts (no está en slim)
        nodejs \
        npm \
        # Chromium necesita estas librerías en Debian
        libnss3 \
        libnspr4 \
        libatk1.0-0 \
        libatk-bridge2.0-0 \
        libcups2 \
        libdrm2 \
        libxkbcommon0 \
        libxcomposite1 \
        libxdamage1 \
        libxfixes3 \
        libxrandr2 \
        libgbm1 \
        libasound2 \
        libpangocairo-1.0-0 \
        libpango-1.0-0 \
        libcairo2 \
    && rm -rf /var/lib/apt/lists/*

# Dependencias Python primero: cambia menos que el código, así que la
# capa de pip se reutiliza en cada rebuild.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright: descarga el Chromium en la ruta que fijamos arriba.
RUN playwright install --with-deps chromium

# El código llega por bind-mount en tiempo de ejecución (ver compose), pero
# se copia también para que la imagen sea autonomous (build sin compose).
COPY . .

# Carpetas de almacenamiento: las monta compose como volúmenes, pero se
# crean aquí para que la imagen no falle si alguien la ejecuta sola.
RUN mkdir -p /srv/backend/uploads/pdfs /srv/backend/uploads/audio \
             /srv/backend/uploads/infographics /srv/backend/uploads/notebooklm \
             /srv/backend/uploads/scraped_pdfs

EXPOSE 8080

# gunicorn en vez de app.run(debug=True): el reloader de Flask con el
# bind-mount ./backend:/app provocaría un bucle de reinicios en Docker.
CMD ["gunicorn", \
     "--chdir", "/srv/backend", \
     "--bind", "0.0.0.0:8080", \
     "--workers", "2", \
     "--threads", "4", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "server:app"]
