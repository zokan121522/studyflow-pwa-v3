#!/bin/bash
# ---------------------------------------------------------------------------
#  install-autostart.command  (macOS)
#
#  Instala StudyFlow como SERVICIO del sistema para que el usuario NO tenga
#  que abrir nunca una Terminal: al iniciar sesion, macOS levanta solo el
#  servidor (127.0.0.1:8477) y el helper de control (127.0.0.1:8478), de modo
#  que el boton rojo "API" de la PWA siempre encuentra algo con quien hablar.
#
#  Que hace:
#    1. Copia el programa a una ruta ESTABLE (~/Applications/StudyFlow) para
#       que el LaunchAgent no apunte nunca a una carpeta descomprimida que
#       puede desaparecer.
#    2. Crea ahi el entorno virtual (.venv) e instala dependencias si faltan.
#    3. Escribe ~/Library/LaunchAgents/com.studyflow.launcher.plist.
#    4. Lo carga con launchctl (RunAtLoad + KeepAlive).
#
#  Tus DATOS nunca se tocan: viven en
#      ~/Library/Application Support/studyflow   y   ~/.studyflow
#
#  Uso:
#    install-autostart.command              instala y arranca el servicio
#    install-autostart.command --dry-run    imprime el plist y NO cambia nada
#
#  Fichero ASCII puro (sin tildes) por seguridad de codepage.
# ---------------------------------------------------------------------------
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$(cd "$SCRIPT_DIR/.." && pwd)"

STABLE="$HOME/Applications/StudyFlow"
VENV="$STABLE/.venv"
VENV_PY="$VENV/bin/python"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST="$PLIST_DIR/com.studyflow.launcher.plist"
LOG_DIR="$HOME/Library/Logs"
LOG_FILE="$LOG_DIR/studyflow-launcher.log"
LABEL="com.studyflow.launcher"
UID_NUM="$(id -u)"

DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

# El plist completo, en una variable: --dry-run solo lo imprime.
#
# EnvironmentVariables: launchd gives jobs a bare environment (no
# DYLD_LIBRARY_PATH, a minimal PATH), and without these two the md->PDF path
# takes the backend down: cffi cannot dlopen Homebrew pango (OSError at
# `import weasyprint`) and `node` (md->html) is not on PATH. launch.py
# re-applies the same fix for any run started outside this plist, but the
# template carries it too so a fresh install or a re-run cannot regress.
read -r -d '' PLIST_XML <<EOF || true
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>$LABEL</string>
	<key>ProgramArguments</key>
	<array>
		<string>$VENV_PY</string>
		<string>$STABLE/launcher/launch.py</string>
		<string>--headless</string>
		<string>--no-browser</string>
	</array>
	<key>EnvironmentVariables</key>
	<dict>
		<key>DYLD_LIBRARY_PATH</key>
		<string>/opt/homebrew/lib</string>
		<key>PATH</key>
		<string>/opt/homebrew/bin:/usr/local/bin:/usr/sbin:/sbin:/usr/bin:/bin</string>
	</dict>
	<key>RunAtLoad</key>
	<true/>
	<key>KeepAlive</key>
	<true/>
	<key>ThrottleInterval</key>
	<integer>10</integer>
	<key>WorkingDirectory</key>
	<string>$STABLE</string>
	<key>StandardOutPath</key>
	<string>$LOG_FILE</string>
	<key>StandardErrorPath</key>
	<string>$LOG_FILE</string>
</dict>
</plist>
EOF

if [ "$DRY_RUN" = 1 ]; then
    # Solo imprime el plist. No copia, no crea venv, no toca launchctl.
    printf '%s\n' "$PLIST_XML"
    exit 0
fi

cat <<'BANNER'

  ============================================================
        S T U D Y F L O W   -   AUTOARRANQUE (macOS)
  ============================================================

   Vamos a montar StudyFlow como servicio de este Mac.
   A partir de ahora arranca SOLO al iniciar sesion: no haras
   falta abrir ninguna Terminal nunca mas.

BANNER

# ----- 0/4  Comprobaciones previas -----
if [ ! -f "$SRC/requirements-local.txt" ]; then
    echo "  [X] No encuentro requirements-local.txt en:"
    echo "        $SRC"
    echo
    echo "      Lanza este instalador desde la carpeta StudyFlow completa."
    if [ -t 0 ]; then read -r -p "  Pulsa Intro para cerrar... " _; fi
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "  [X] No encuentro python3."
    echo "      Instala Python 3.12 desde https://www.python.org/downloads/"
    echo "      (o  brew install python3)  y vuelve a lanzar este instalador."
    if [ -t 0 ]; then read -r -p "  Pulsa Intro para cerrar... " _; fi
    exit 1
fi

# ----- 1/4  Copiar el programa a una ruta estable -----
echo "  [1/4] Copiando el programa a una ruta estable..."
echo "        $STABLE"
mkdir -p "$STABLE"
mkdir -p "$LOG_DIR" "$PLIST_DIR"

# -a preserva permisos de ejecucion. --delete mantiene el codigo sincronizado
# con el repo; los DATOS del usuario NO viven aqui, asi que nada suyo se
# pierde. Nunca se copian .git / tests / docs: solo lo necesario para servir.
for d in launcher backend frontend; do
    if [ -d "$SRC/$d" ]; then
        rsync -a --delete \
            --exclude '.git' --exclude '__pycache__' --exclude '*.pyc' \
            "$SRC/$d/" "$STABLE/$d/" >/dev/null 2>&1 || {
            echo "  [X] Fallo al copiar $d/."
            if [ -t 0 ]; then read -r -p "  Pulsa Intro para cerrar... " _; fi
            exit 1
        }
    fi
done
for f in requirements-local.txt requirements.txt; do
    [ -f "$SRC/$f" ] && cp -p "$SRC/$f" "$STABLE/$f"
done
chmod +x "$STABLE/launcher/StudyFlow.sh" "$STABLE/launcher/StudyFlow.command" 2>/dev/null || true
echo "        Listo."

# ----- 2/4  Entorno virtual + dependencias -----
deps_ok() {
    [ -x "$VENV_PY" ] || return 1
    "$VENV_PY" -c "import flask, flask_cors, fitz, icalendar, jwt, requests, edge_tts, gevent, tzdata" \
        >/dev/null 2>&1
}

if deps_ok; then
    echo "  [2/4] Entorno y dependencias ya listos."
else
    if [ ! -x "$VENV_PY" ]; then
        echo "  [2/4] Creando el entorno virtual (solo la primera vez)..."
        if ! python3 -m venv "$VENV"; then
            echo "  [X] No se pudo crear el entorno virtual."
            echo "      En Mac con Homebrew:  brew install python3"
            if [ -t 0 ]; then read -r -p "  Pulsa Intro para cerrar... " _; fi
            exit 1
        fi
    else
        echo "  [2/4] Entorno presente; faltan dependencias, instalando..."
    fi
    if ! "$VENV_PY" -m pip --version >/dev/null 2>&1; then
        if python3 -c "import ensurepip" >/dev/null 2>&1; then
            "$VENV_PY" -m ensurepip --upgrade >/dev/null 2>&1 || true
        fi
    fi
    "$VENV_PY" -m pip install --upgrade pip --quiet
    echo "        Instalando dependencias - puede tardar 2-3 min..."
    if ! "$VENV_PY" -m pip install -r "$STABLE/requirements-local.txt"; then
        echo "  [X] La instalacion de dependencias fallo. Copia el error de arriba."
        if [ -t 0 ]; then read -r -p "  Pulsa Intro para cerrar... " _; fi
        exit 1
    fi
fi
echo "        Entorno listo."

# ----- 3/4  Escribir el LaunchAgent -----
echo "  [3/4] Instalando el servicio (LaunchAgent)..."
printf '%s\n' "$PLIST_XML" > "$PLIST"
echo "        $PLIST"

# ----- 4/4  Cargar el servicio -----
echo "  [4/4] Cargando el servicio con launchctl..."
launchctl bootout "gui/$UID_NUM" "$PLIST" >/dev/null 2>&1 || true
if ! launchctl bootstrap "gui/$UID_NUM" "$PLIST" 2>/dev/null; then
    # Ya podria estar cargado; forzar recarga.
    launchctl bootout "gui/$UID_NUM/$LABEL" >/dev/null 2>&1 || true
    launchctl bootstrap "gui/$UID_NUM" "$PLIST" >/dev/null 2>&1 || true
fi
launchctl enable "gui/$UID_NUM/$LABEL" >/dev/null 2>&1 || true

cat <<EOF

  ============================================================
        ***  STUDYFLOW YA ARRANCA SOLO  ***
  ============================================================

   Servicio : $LABEL
   Programa : $STABLE
   Entorno  : $VENV
   Log      : $LOG_FILE

   StudyFlow se levanta ahora al iniciar sesion y se mantiene
   en marcha. Para comprobarlo:

       launchctl list | grep studyflow
       lsof -nP -iTCP:8477 -sTCP:LISTEN
       lsof -nP -iTCP:8478 -sTCP:LISTEN

   Tus datos NO se han tocado:
       ~/Library/Application Support/studyflow

   Para quitarlo:  launcher/uninstall-autostart.command

EOF
