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
#   4. chrome    → el login de NotebookLM (`notebooklm login --browser
#                  chrome`, backend/routes/notebooklm_login.py:191) pide el
#                  Chrome DE ESCRITORIO, que Playwright NO incluye: son dos
#                  navegadores distintos. Sin esto el botón «Iniciar sesión»
#                  moría con "Google Chrome not found" y el perfil se quedaba
#                  en 0 bytes — el usuario conectaba y la tarea siguiente
#                  fallaba por sesión caducada. OJO: esto solo afecta al
#                  login. PDF→MD, audio, infografías y flashcards usan el
#                  Chromium bundled de arriba y nunca lo necesitan.
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
        # Display virtual + servidor VNC + WM para el login embebido de
        # NotebookLM (routes/notebooklm_login.py, modo VNC). x11vnc expone el
        # display por RFB y fluxbox da un gestor de ventanas para que el
        # Chromium headed tenga dónde dibujar. xauth lo pide Xvfb.
        xvfb \
        x11vnc \
        fluxbox \
        xauth \
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

# noVNC: los estáticos (HTML/JS) que sirve routes/novnc_proxy.py. Sólo los
# ficheros; el bridge WebSocket ↔ RFB lo hace el propio Flask con gevent, así
# que NO instalamos websockify ni el script novnc_proxy del sistema (a
# diferencia de v2, donde corrían como proceso aparte en el puerto 6080).
ARG NOVNC_VERSION=1.5.0
RUN curl -fsSL "https://github.com/novnc/noVNC/archive/refs/tags/v${NOVNC_VERSION}.tar.gz" \
        | tar xz -C /opt \
    && mv "/opt/noVNC-${NOVNC_VERSION}" /opt/novnc \
    && ln -sf /opt/novnc/vnc.html /opt/novnc/index.html

# Playwright: Chrome de escritorio, para el login de NotebookLM.
#
# `notebooklm login --browser chrome` lanza el Chrome del sistema vía el
# `channel` de Playwright. El bundled Chromium NO sirve aquí: la librería lo
# documenta en CHANNEL_BROWSERS ("system-installed, not the bundled
# Chromium"). Sin esta línea el login falla al arrancar, deja el
# storage_state.json a 0 bytes y el usuario ve un error que culpa a su
# cuenta de Google en lugar de a la falta del navegador.
#
# `--no-sandbox`: corre como root dentro del contenedor.
#
# El canal `chrome` de Google NO existe para Linux Arm64: Playwright aborta
# con "ERROR: not supported on Linux Arm64 / Failed to install browsers", y
# como era una capa del build, el fallo tumbaba la imagen entera — no solo
# NotebookLM, sino también el scraper de Selenium y cualquier otra cosa. Por
# eso se salta en arm64 (Apple Silicon) y solo se instala donde existe.
# Chromium, que sí tiene build arm64, se instala arriba y es el que usa el
# scraper de Selenium vía _chromium_binary().
RUN if [ "$(uname -m)" = "x86_64" ]; then \
        playwright install --with-deps chrome \
        && playwright install-deps chrome; \
    else \
        echo "arm64: se omite el canal chrome (no existe para esta arquitectura)"; \
    fi \
    && rm -rf /var/lib/apt/lists/*

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
#
# Worker gevent (GeventWebSocketWorker) con UN solo worker: es lo que permite
# el upgrade WebSocket del bridge noVNC (/novnc/ws). El worker gevent hace
# monkey-patch y atiende concurrencia con greenlets, así que 1 worker basta.
# `--timeout 0` desactiva el timeout del worker: si no, gunicorn mataría el
# worker por «inactividad» mientras el WebSocket de noVNC está abierto
# esperando a que el usuario complete el login de Google.
CMD ["gunicorn", \
     "--chdir", "/srv/backend", \
     "--bind", "0.0.0.0:8080", \
     "--worker-class", "gevent_ws_worker.GeventWebSocketWorker", \
     "--workers", "1", \
     "--timeout", "0", \
     "--graceful-timeout", "30", \
     "--access-logfile", "-", \
     "server:app"]
