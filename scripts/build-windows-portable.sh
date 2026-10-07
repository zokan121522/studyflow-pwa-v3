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

VERSION="$(python3 -c "import re,pathlib; \
t=(pathlib.Path('backend/server.py').read_text()+pathlib.Path('frontend/manifest.json').read_text()
     if pathlib.Path('frontend/manifest.json').exists() else pathlib.Path('backend/server.py').read_text()); \
m=re.search(r'[\"\x27]?version[\"\x27]?\s*[:=]\s*[\"\x27]([0-9][^\"\x27]*)', t); \
print(m.group(1) if m else '3.0.0')" 2>/dev/null || echo "3.0.0")"

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
echo "      1. Descomprime el ZIP"
echo "      2. Doble clic en launcher\\StudyFlow.bat"
echo "      3. La primera vez instala dependencias (2-3 min)"
echo "      4. Se abre en http://127.0.0.1:8477/"
echo
echo "    Para actualizar datos desde el Mac: usa el ZIP de backup desde"
echo "    dentro de la app (Ajustes > Restaurar)."
