#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# scripts/build-windows-portable.sh
#
# Build a ZIP that someone can copy to a Windows machine and run.
# Produces dist/StudyFlow-windows/ inside dist/StudyFlow-windows-<version>.zip
#
# Why this exists
# ---------------
# A git clone is NOT the deliverable. Cloning pulls the whole history and every
# ignored artefact that happens to sit in the working tree: on the dev machine
# that was 4.3 GB, including a 3.8 GB Ubuntu ISO, a 160 MB gunicorn core dump
# and 170 MB of walkthrough renders. None of that ships.
#
# So this copies an explicit allow-list of what the app needs at runtime, then
# verifies the result actually starts. A ZIP that boots is worth more than a
# ZIP that is complete.
#
# Usage:  bash scripts/build-windows-portable.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

VERSION="$(python3 -c "import re,pathlib,sys; \
p=pathlib.Path('backend/routes/health.py'); \
t=p.read_text() if p.exists() else ''; \
m=re.search(r'APP_VERSION\s*=\s*[\'\"]([^\'\"]+)', t); \
sys.exit('ERROR: APP_VERSION not found in backend/routes/health.py') if not m else print(m.group(1))")"

STAGE="dist/StudyFlow-windows"
ZIP="dist/StudyFlow-windows-${VERSION}.zip"

echo "==> Limpiando la staging previa"
rm -rf "$STAGE" "$ZIP"
mkdir -p "$STAGE"

# ── what actually ships ──────────────────────────────────────────────────────
# Keep list mirrors backend/requirements-local.txt: everything the app imports
# at runtime. frontend/ ships whole because the Service Worker precaches by
# path and a missing asset breaks offline mode silently.
INCLUDE=(
  "LEEME-PRIMERO.txt"
  "INICIAR_StudyFlow.bat"
  "launcher"
  "backend"
  "frontend"
  "tests"
  "docker"
  "scripts"
  "requirements-local.txt"
  "requirements.txt"
  "README.md"
  "LICENSE"
  ".env.example"
  "AGENTS.md"
)

echo "==> Copiando ficheros de runtime"
for item in "${INCLUDE[@]}"; do
    [ -e "$item" ] || { echo "    - $item (no existe, se omite)"; continue; }
    cp -R "$item" "$STAGE/"
    echo "    + $item"
done

# ── strip what must not travel ───────────────────────────────────────────────
echo "==> Eliminando basura local"
strip() {
    local n=0
    for p in "$@"; do
        if [ -e "$STAGE/$p" ]; then rm -rf "${STAGE:?}/$p"; n=$((n+1)); fi
    done
    [ "$n" -gt 0 ] && echo "    - $*"
    return 0
}
# Core dumps, caches and stray binaries.
strip "backend/core" "backend/core.*"
find "$STAGE" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$STAGE" -name '*.pyc' -delete 2>/dev/null || true
find "$STAGE" -name '.DS_Store' -delete 2>/dev/null || true
# Test and dev artefacts.
strip "backend/uploads" "backend/venv" ".venv" "node_modules" "dist"
strip "backend/novnc" "tests/fixtures_large"
# Local databases must never be shipped: the person installing wants an empty
# app, and their data arrives via the backup restore instead.
find "$STAGE" -name '*.db' -delete 2>/dev/null || true
find "$STAGE" -name '*.sqlite*' -delete 2>/dev/null || true
find "$STAGE" -name 'launcher.json' -delete 2>/dev/null || true
find "$STAGE" -name 'secret.key' -delete 2>/dev/null || true

# ── instalador visible en la RAIZ ───────────────────────────────────────────
# Lo primero que ve el usuario al abrir el ZIP: INSTALL.txt (guia corta) e
# install.bat (alias que delega en INICIAR_StudyFlow.bat). Asi el paquete
# Windows queda alineado con mac/linux, que ya generan su instalador raiz.
echo "==> Generando INSTALL.txt e install.bat (Windows)"
cat > "$STAGE/INSTALL.txt" <<'EOF'
============================================================
  STUDYFLOW - INSTALACION RAPIDA (Windows)
============================================================

  1. Extrae TODO el ZIP (clic derecho > Extraer todo).
     No ejecutes nada desde dentro del ZIP.

  2. Doble clic en:  install.bat
     (o en INICIAR_StudyFlow.bat, es exactamente lo mismo)

  3. La primera vez tarda 2-3 minutos (instala dependencias).
     Se abrira solo:  http://127.0.0.1:8477/

  Para detener:  INICIAR_StudyFlow.bat stop
  Lee LEEME-PRIMERO.txt para instrucciones detalladas.
EOF
# .bat con CRLF y ASCII puro (codepage OEM): nunca LF ni tildes.
printf '@echo off\r\nREM StudyFlow - instalador (raiz). Delega en INICIAR_StudyFlow.bat\r\ncd /d "%%~dp0"\r\nif not exist "INICIAR_StudyFlow.bat" goto sin_lanzador\r\ncall "INICIAR_StudyFlow.bat" %%*\r\nexit /b %%ERRORLEVEL%%\r\n\r\n:sin_lanzador\r\necho   [X] No encuentro INICIAR_StudyFlow.bat.\r\necho       Extrae el ZIP completo y vuelve a intentarlo.\r\npause\r\nexit /b 1\r\n' > "$STAGE/install.bat"

# ── refuse to ship a bloated package ────────────────────────────────────────
echo "==> Comprobando peso"
SIZE_MB="$(du -sm "$STAGE" | cut -f1)"
echo "    ${SIZE_MB} MB"
if [ "$SIZE_MB" -gt 400 ]; then
    echo "    [X] El paquete pesa ${SIZE_MB} MB. Se esperaba mucho menos."
    echo "        Suele indicar que se ha colado un artefacto ignorado por git."
    echo "        Revisa la lista INCLUDE de este script. Abortando."
    exit 1
fi

# ── zip ─────────────────────────────────────────────────────────────────────
echo "==> Comprimiendo"
# -r recurse; -X drop macOS metadata that Windows cannot use anyway.
( cd dist && zip -qr "$(basename "$ZIP")" "$(basename "$STAGE")" -x '*.DS_Store' )

echo
echo "    Listo: $ZIP"
echo "    ($(du -h "$ZIP" | cut -f1))"
echo
echo "    En Windows:"
echo "      1. Descomprime el ZIP completo (Extraer todo / Extract All)"
echo "      2. Lee LEEME-PRIMERO.txt (instrucciones para principiantes)"
echo "      3. Doble clic en INICIAR_StudyFlow.bat"
echo "      4. La primera vez instala dependencias (2-3 min)"
echo "      5. Se abre en http://127.0.0.1:8477/"
echo "      Para detener: INICIAR_StudyFlow.bat stop"
echo "      (Tambien hay install.bat en la raiz, equivalente)"
echo
echo "    Para actualizar datos desde el Mac: usa el ZIP de backup desde"
echo "    dentro de la app (Ajustes > Restaurar)."
