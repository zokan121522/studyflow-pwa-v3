#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# scripts/verify-frontend.sh — gate between "build succeeded" and "deploy".
#
# Checks the DIST, not the source. A build that produces a dist where the
# service worker precaches files that are not there will install cleanly, show
# the update banner, and fail silently at runtime — the app appears to work
# until someone goes offline and finds a feature gone. That happened in v20,
# v24, v27, v29, v30 and v86.
#
# So this verifies the four things that actually break that way:
#
#   1. every <script src> in index.html        resolves to a file in dist
#   2. every <link rel=stylesheet>             resolves to a file in dist
#   3. every PRECACHE_ASSETS entry             resolves to a file in dist
#   4. no secret-shaped string                 is present in client JS
#
# Plus: no source maps, no stray build dirs, and the SW is byte-identical to
# the source SW (it must stay greppable for tests/test_sw_asset_version.py).
#
# Usage:
#   ./scripts/verify-frontend.sh [distDir]     (default: frontend-dist)
#   ./scripts/verify-frontend.sh frontend      (verify the source tree)
#
# Exit 0 = safe to deploy. Exit 1 = something is missing, do not deploy.
# ─────────────────────────────────────────────────────────────────────────────

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

DIST="${1:-frontend-dist}"
SRC="frontend"

RED=$'\033[31m'; GRN=$'\033[32m'; YLW=$'\033[33m'; DIM=$'\033[2m'; RST=$'\033[0m'
FAILURES=0
CHECKS=0

pass() { CHECKS=$((CHECKS+1)); printf '  %s✓%s %s\n' "$GRN" "$RST" "$1"; }
fail() { CHECKS=$((CHECKS+1)); FAILURES=$((FAILURES+1)); printf '  %s✗%s %s\n' "$RED" "$RST" "$1"; }
warn() { printf '  %s⚠%s %s\n' "$YLW" "$RST" "$1"; }
head_() { printf '\n%s%s%s\n' "$DIM" "$1" "$RST"; }

printf '\n%sVerificando %s%s\n' "$DIM" "$DIST" "$RST"

if [ ! -d "$DIST" ]; then
  printf '\n  %s✗ no existe el directorio %s%s\n' "$RED" "$DIST" "$RST"
  printf '    %sEjecuta antes: ./scripts/build-frontend.sh%s\n' "$DIM" "$RST"
  exit 1
fi

# ── 1 · Scripts referenciados ───────────────────────────────────────────────
head_ "1 · Referencias de index.html"

python3 - "$DIST" <<'PY'
import os, re, sys
dist = sys.argv[1]
idx = os.path.join(dist, "index.html")
if not os.path.isfile(idx):
    print("MISSING:index.html"); sys.exit(0)
src = open(idx, encoding="utf-8").read()
refs = re.findall(r'<script[^>]*\ssrc=["\']([^"\']+)', src)
refs += re.findall(r'<link[^>]*\srel=["\']?stylesheet["\']?[^>]*\shref=["\']([^"\']+)', src)
for r in sorted(set(refs)):
    if r.startswith(("http://", "https://", "//", "data:")):
        continue
    path = r.split("?")[0].lstrip("/")
    print(("MISSING:" if not os.path.isfile(os.path.join(dist, path)) else "OK:") + path)
PY

while IFS= read -r line; do
  case "$line" in
    OK:*)     pass "${line#OK:}" ;;
    MISSING:*) fail "no existe en dist: ${line#MISSING:}" ;;
  esac
done < <(python3 - "$DIST" <<'PY'
import os, re, sys
dist = sys.argv[1]
idx = os.path.join(dist, "index.html")
if not os.path.isfile(idx):
    print("MISSING:index.html"); sys.exit(0)
src = open(idx, encoding="utf-8").read()
refs = re.findall(r'<script[^>]*\ssrc=["\']([^"\']+)', src)
refs += re.findall(r'<link[^>]*\srel=["\']?stylesheet["\']?[^>]*\shref=["\']([^"\']+)', src)
for r in sorted(set(refs)):
    if r.startswith(("http://", "https://", "//", "data:")):
        continue
    p = r.split("?")[0].lstrip("/")
    print(("MISSING:" if not os.path.isfile(os.path.join(dist, p)) else "OK:") + p)
PY
)

# ── 2 · PRECACHE_ASSETS ─────────────────────────────────────────────────────
head_ "2 · Service Worker · PRECACHE_ASSETS"

if [ ! -f "$DIST/sw.js" ]; then
  fail "falta sw.js en dist (el SW no se puede registrar sin él)"
else
  while IFS= read -r url; do
    [ -z "$url" ] && continue
    if [ -f "$DIST/${url#/}" ]; then
      pass "$url"
    else
      fail "precacheado pero ausente en dist: $url"
    fi
  done < <(python3 - "$DIST/sw.js" <<'PY'
import re, sys
src = open(sys.argv[1], encoding="utf-8").read()
if "const PRECACHE_ASSETS = [" not in src:
    print(""); sys.exit(0)
block = src.split("const PRECACHE_ASSETS = [", 1)[1].split("];", 1)[0]
for u in re.findall(r"'([^']+)'", block):
    print(u)
PY
  )

  # The install handler must tolerate one bad entry. allSettled over cache.add()
  # keeps every asset that resolves; addAll() would empty the whole precache on
  # a single 404, which is how courses-notes.js took offline support down.
  if grep -q 'allSettled' "$DIST/sw.js"; then
    pass "install usa allSettled (un 404 no vacía el precache)"
  else
    fail "install NO usa allSettled: un solo 404 vacía el precache entero"
  fi

  if grep -q 'skipWaiting' "$DIST/sw.js"; then
    pass "sw.js gestiona el mensaje skipWaiting (el botón Actualizar funciona)"
  else
    fail "sw.js no menciona skipWaiting: el banner de actualización no activaría nada"
  fi

  # sw.js must be identical to the source: the update-guard test greps it.
  if [ -f "$SRC/sw.js" ] && cmp -s "$DIST/sw.js" "$SRC/sw.js"; then
    pass "sw.js idéntico al origen (el test de ASSET_CACHE puede leerlo)"
  else
    fail "sw.js difiere del origen: si se minificó, tests/test_sw_asset_version.py no podrá parsearlo"
  fi

  CACHE_VER=$(grep -oE "const ASSET_CACHE = '[^']+'" "$DIST/sw.js" 2>/dev/null | head -1 | sed "s/.*'\(.*\)'/\1/")
  if [ -n "$CACHE_VER" ]; then
    pass "ASSET_CACHE = $CACHE_VER"
    PINNED=$(python3 -c "
import json,sys
try: print(json.load(open('tests/sw_asset_pins.json')).get('$CACHE_VER','NO') != 'NO')
except Exception: print('False')
" 2>/dev/null)
    if [ "$PINNED" = "True" ]; then
      pass "hay pin del digest para $CACHE_VER en tests/sw_asset_pins.json"
    else
      fail "NO hay pin para $CACHE_VER en tests/sw_asset_pins.json → pytest fallará. Ver docs/pwa-workflow-guia.md §6"
    fi
  else
    fail "no se pudo leer ASSET_CACHE de sw.js"
  fi
fi

# ── 3 · Secretos en el cliente ───────────────────────────────────────────────
head_ "3 · Secretos en el código cliente"

# A PWA is public: anything in frontend/ is readable by anyone who installs it.
# The backend is where secrets belong; see docs/pwa-workflow-guia.md §7.
SECRETS=$(grep -rInE "(sk-[A-Za-z0-9]{20,}|Bearer[[:space:]]+[A-Za-z0-9._-]{20,}|api[_-]?key[[:space:]]*[:=][[:space:]]*['\"][^'\"]{16,}|JWT_SECRET_KEY[[:space:]]*[:=][[:space:]]*['\"][^'\"]+|SECRET_KEY[[:space:]]*[:=][[:space:]]*['\"][^'\"]+|postgres(ql)?://[^:]+:[^@]+@)" \
  "$DIST" --include="*.js" --include="*.html" --include="*.json" 2>/dev/null \
  | grep -v "vendor/" | grep -v "\.map:" || true)

if [ -z "$SECRETS" ]; then
  pass "ningún secreto con forma de clave en el JS del cliente"
else
  while IFS= read -r line; do
    fail "posible secreto: ${line%%:*}"
  done <<< "$SECRETS"
fi

# ── 4 · Higiene del build ───────────────────────────────────────────────────
head_ "4 · Higiene"

if find "$DIST" -name "*.map" -type f 2>/dev/null | grep -q .; then
  fail "hay source maps en dist: reconstruyen el código original tal cual"
else
  pass "sin source maps en dist"
fi

STRAY=$(find "$DIST" -maxdepth 2 -type d \( -name "dist" -o -name "node_modules" -o -name ".git" \) 2>/dev/null || true)
if [ -z "$STRAY" ]; then
  pass "sin directorios de build dentro de dist"
else
  while IFS= read -r d; do [ -n "$d" ] && fail "directorio basura en dist: $d"; done <<< "$STRAY"
fi

if [ -f "$DIST/manifest.json" ]; then
  pass "manifest.json presente"
else
  fail "falta manifest.json: sin él no hay PWA instalable"
fi

if [ -f "$DIST/sw.js" ] && [ -f "$SRC/sw.js" ]; then
  A=$(grep -oE "ASSET_CACHE = '[^']+'" "$SRC/sw.js" | head -1)
  B=$(grep -oE "ASSET_CACHE = '[^']+'" "$DIST/sw.js" | head -1)
  if [ "$A" = "$B" ]; then
    pass "ASSET_CACHE del origen y del dist coinciden"
  else
    fail "ASSET_CACHE divergen: origen='$A' dist='$B' → los clientes instalados no verán la actualización"
  fi
fi

# ── Veredicto ───────────────────────────────────────────────────────────────
printf '\n%s%s%s\n' "$DIM" "────────────────────────────────────────────────────────────────" "$RST"
if [ "$FAILURES" -eq 0 ]; then
  printf '  %s✓ %d comprobaciones, 0 fallos%s\n' "$GRN" "$CHECKS" "$RST"
  printf '  %sListo para desplegar.%s\n\n' "$DIM" "$RST"
  exit 0
else
  printf '  %s✗ %d de %d comprobaciones fallan%s\n' "$RED" "$FAILURES" "$CHECKS" "$RST"
  printf '  %sNO desplegar hasta arreglarlos.%s\n\n' "$RED" "$RST"
  exit 1
fi
