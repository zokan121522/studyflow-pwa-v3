#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# scripts/deploy-windows.sh — send StudyFlow to the Windows box over SSH
#
# Why a script and not a one-off command: the Windows install will need
# updating every time the app changes. Doing it by hand means remembering a
# sequence of steps that are easy to get wrong; doing it here means it is the
# same every time and it fails loudly instead of halfway.
#
# Usage
#   SSHPASS='la contraseña' bash scripts/deploy-windows.sh            # deploy
#   SSHPASS='...' bash scripts/deploy-windows.sh --rebuild           # re-install deps
#   SSHPASS='...' bash scripts/deploy-windows.sh --status            # only report
#
# The password is read from the SSHPASS environment variable and never written
# to disk by this script. It does stay in your shell history if you type it
# inline, so prefer:  read -s SSHPASS; export SSHPASS
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

HOST="${WINDOWS_HOST:-asus}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ZIP="$(ls -t "$REPO_ROOT"/dist/StudyFlow-windows-*.zip 2>/dev/null | head -1 || true)"
REMOTE_DIR='C:/Users/ASUS/StudyFlow'
REMOTE_ZIP='C:/Users/ASUS/StudyFlow-windows.zip'

MODE="deploy"
case "${1:-}" in
  --rebuild) MODE="rebuild" ;;
  --status)  MODE="status" ;;
esac

say() { printf '\n  %s\n' "$1"; }

[ -n "${SSHPASS:-}" ] || { echo "Falta SSHPASS. Uso: SSHPASS='...' bash $0"; exit 1; }

win() { sshpass -e ssh -o StrictHostKeyChecking=accept-new "$HOST" "$@"; }
winscp() { sshpass -e scp -o StrictHostKeyChecking=accept-new "$@"; }

# ── 0. prerequisites ────────────────────────────────────────────────────────
say "1/6  Comprobando el equipo remoto"
if ! win 'echo READY' >/dev/null 2>&1; then
    echo "  [X] No puedo entrar. Revise contraseña, red y que sshd siga activo."
    exit 1
fi
win 'echo "  equipo: $(hostname)  usuario: $(whoami)"'

if [ "$MODE" = "status" ]; then
    win "powershell -NoProfile -Command \"Get-Process python* -ErrorAction SilentlyContinue | Select-Object -First 3 Id,ProcessName | Format-Table -AutoSize; if (Test-Path '$REMOTE_DIR/backend/server.py') { 'app presente' } else { 'app NO presente' }\""
    exit 0
fi

# ── 1. Python ───────────────────────────────────────────────────────────────
# Everything below assumes Python exists. Without it the venv step fails with a
# message that does not obviously mean "install Python first".
say "2/6  Comprobando Python en Windows"
PY_OK="$(win 'python --version 2>&1 || py -3 --version 2>&1 || echo AUSENTE' | tr -d '\r' | tail -1)"
echo "  $PY_OK"
if echo "$PY_OK" | grep -q AUSENTE; then
    cat <<'MSG'
  [X] No hay Python en el Windows.

      Instale Python 3.12 desde https://www.python.org/downloads/
      marcando "Add Python to PATH" durante la instalación.

      Luego vuelva a ejecutar este script.
MSG
    exit 1
fi

[ -n "$ZIP" ] || { echo "  [X] No hay ZIP en dist/. Ejecuta antes: bash scripts/build-windows-portable.sh"; exit 1; }

# ── 2. transfer ─────────────────────────────────────────────────────────────
say "3/6  Subiendo $(basename "$ZIP") ($(du -h "$ZIP" | cut -f1))"
winscp "$ZIP" "$HOST:$REMOTE_ZIP"
echo "  subido"

# ── 3. extract ──────────────────────────────────────────────────────────────
say "4/6  Descomprimiendo en $REMOTE_DIR"
win "powershell -NoProfile -Command \"\
  Remove-Item -Recurse -Force '$REMOTE_DIR' -ErrorAction SilentlyContinue; \
  Expand-Archive -Path '$REMOTE_ZIP' -DestinationPath 'C:/Users/ASUS' -Force; \
  if (Test-Path 'C:/Users/ASUS/StudyFlow-windows') { \
      Rename-Item 'C:/Users/ASUS/StudyFlow-windows' 'StudyFlow'; 'ok' } else { 'fallo' }\"" | tr -d '\r' | sed 's/^/  /'

# ── 4. dependencies ─────────────────────────────────────────────────────────
if [ "$MODE" = "rebuild" ] || [ ! -f "$REMOTE_DIR/.venv/Scripts/python.exe" ]; then
    say "5/6  Instalando dependencias (2-3 min, solo la primera vez)"
    win "powershell -NoProfile -Command \"\
      Set-Location '$REMOTE_DIR'; \
      py -3 -m venv .venv; \
      .\\.venv\\Scripts\\python.exe -m pip install --upgrade pip --quiet; \
      .\\.venv\\Scripts\\python.exe -m pip install -r requirements-local.txt\"" \
      | tr -d '\r' | tail -5 | sed 's/^/  /'
else
    say "5/6  Dependencias ya instaladas (usa --rebuild para reinstalar)"
fi

# ── 5. verify ───────────────────────────────────────────────────────────────
say "6/6  Verificando que arranca"
win "powershell -NoProfile -Command \"Set-Location '$REMOTE_DIR'; \
      \$env:STUDYFLOW_DATA_DIR='C:/Users/ASUS/studyflow-data'; \
      .\\.venv\\Scripts\\python.exe launcher\\launch.py --no-browser\"" \
  | tr -d '\r' | sed 's/^/  /'

sleep 4
say "Comprobando la respuesta del servidor"
if win "powershell -NoProfile -Command \"try { (Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8477/api/health -TimeoutSec 5).StatusCode } catch { 'sin-respuesta' }\"" \
   | tr -d '\r' | grep -q 200; then
    echo "  OK  http://127.0.0.1:8477/ responde"
else
    echo "  [X] El servidor no responde en 8477."
    echo "      Log remoto: C:/Users/ASUS/studyflow-data/launcher.log"
    exit 1
fi

cat <<'MSG'

  ──────────────────────────────────────────────
   Despliegue completado.

   En el Windows, abra:
     http://127.0.0.1:8477/

   y restaure sus datos desde Ajustes > Restaurar
   usando el backup del Mac. UNA SOLA VEZ.
  ──────────────────────────────────────────────
MSG
