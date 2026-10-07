#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# scripts/build-frontend.sh — production build for studyflow-pwa-v3.
#
# ── What it does ─────────────────────────────────────────────────────────────
#   1. frontend/ → frontend-dist/ , minifying JS/CSS/HTML in place so every
#      path stays identical to the source tree
#   2. optionally bumps ASSET_CACHE and pins its digest (--bump)
#   3. runs scripts/verify-frontend.sh on the result
#
# ── Deliberate design choices ────────────────────────────────────────────────
#
#   No hashed filenames. The service worker precaches by literal path and
#   tests/test_sw_asset_version.py resolves those paths against frontend/.
#   Hashing would force index.html, sw.js and that test to change in lockstep,
#   and a mismatch there fails *silently* — install uses Promise.allSettled
#   over cache.add(), so a 404 just leaves a hole. That is how v20, v24, v27,
#   v29, v30 and v86 shipped code no installed PWA could load.
#
#   sw.js is copied verbatim. The browser byte-compares it to detect updates,
#   and the update-guard test greps it for the precache list.
#
#   The source tree is never written to, except for --bump, which edits
#   sw.js and the pin file on purpose.
#
# ── Usage ────────────────────────────────────────────────────────────────────
#   ./scripts/build-frontend.sh                 # build + verify
#   ./scripts/build-frontend.sh --bump          # also bump ASSET_CACHE
#   ./scripts/build-frontend.sh --bump --note "added quiz-embed v2"
#   ./scripts/build-frontend.sh --dry-run       # report sizes, write nothing
#   ./scripts/build-frontend.sh --no-verify     # skip the gate (not advised)
# ─────────────────────────────────────────────────────────────────────────────

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

SRC="frontend"
DIST="frontend-dist"
BUMP=0
DRY_RUN=0
VERIFY=1
NOTE=""

RED=$'\033[31m'; GRN=$'\033[32m'; YLW=$'\033[33m'; DIM=$'\033[2m'; RST=$'\033[0m'

while [ $# -gt 0 ]; do
  case "$1" in
    --bump)     BUMP=1 ;;
    --dry-run)  DRY_RUN=1 ;;
    --no-verify) VERIFY=0 ;;
    --note)     shift; NOTE="${1:-}" ;;
    -h|--help)  sed -n '2,30p' "$0"; exit 0 ;;
    *)          printf '%s✗ opción desconocida: %s%s\n' "$RED" "$1" "$RST"; exit 2 ;;
  esac
  shift
done

printf '\n%s╭─ build studyflow-pwa-v3%s\n' "$DIM" "$RST"

# ── Preflight ────────────────────────────────────────────────────────────────

if [ ! -d "$SRC" ]; then
  printf '%s✗ no existe %s/%s%s\n' "$RED" "$ROOT" "$SRC" "$RST"
  exit 1
fi

if [ ! -f package.json ] || [ ! -d node_modules/esbuild ]; then
  printf '%s! faltan dependencias de build (esbuild, html-minifier-terser)%s\n' "$YLW" "$RST"
  printf '  %sEjecuta: npm install%s\n\n' "$DIM" "$RST"
  exit 1
fi

if ! command -v node >/dev/null 2>&1; then
  printf '%s✗ node no está en el PATH%s\n' "$RED" "$RST"
  exit 1
fi

# ── 1 · ASSET_CACHE ──────────────────────────────────────────────────────────

CUR_VER=$(grep -oE "const ASSET_CACHE = '[^']+'" "$SRC/sw.js" 2>/dev/null | head -1 | sed "s/.*'\(.*\)'/\1/")
printf '%s│%s ASSET_CACHE actual: %s%s%s\n' "$DIM" "$RST" "$DIM" "${CUR_VER:-<no encontrado>}" "$RST"

if [ "$BUMP" -eq 1 ]; then
  printf '%s│%s %s\n' "$DIM" "$RST" ""
  if [ "$DRY_RUN" -eq 1 ]; then
    python3 scripts/build/bump_asset_cache.py --dry-run ${NOTE:+--note "$NOTE"}
  else
    python3 scripts/build/bump_asset_cache.py ${NOTE:+--note "$NOTE"}
  fi
  rc=$?
  if [ $rc -ne 0 ]; then
    printf '%s╰─ falló el bump de ASSET_CACHE (código %d)%s\n\n' "$RED" "$rc" "$RST"
    exit $rc
  fi
  NEW_VER=$(grep -oE "const ASSET_CACHE = '[^']+'" "$SRC/sw.js" | head -1 | sed "s/.*'\(.*\)'/\1/")
  printf '%s│%s ASSET_CACHE nueva:  %s%s%s\n' "$DIM" "$RST" "$GRN" "$NEW_VER" "$RST"
else
  printf '%s│%s sin bump (usa --bump si has tocado assets precacheados)%s\n' "$YLW" "$RST"
  NEW_VER="$CUR_VER"
fi

# ── 2 · Tests ────────────────────────────────────────────────────────────────

# The guide's step 1 is tests; running them here as well means a broken tree
# never reaches a dist. md_to_pdf is excluded for an unrelated reason (it needs
# weasyprint system libs), not because it is known-broken.
if [ "$DRY_RUN" -eq 0 ]; then
  printf '%s│%s %s\n' "$DIM" "$RST" ""
  printf '%s│%s ejecutando tests…%s\n' "$DIM" "$RST"

  TEST_OUT=$(mktemp)
  # Keep the whole run, not just the last line: when the suite is red the
  # failing test names are the only useful output, and swallowing them behind
  # "TESTS EN ROJO" is how a red suite gets ignored for a week.
  python3 -m pytest --ignore=tests/test_md_to_pdf.py -q >"$TEST_OUT" 2>&1
  TEST_RC=$?
  SUMMARY=$(grep -E '^[0-9]+ (passed|failed)' "$TEST_OUT" | tail -1)

  if [ $TEST_RC -eq 0 ] && printf '%s' "$SUMMARY" | grep -qE '^[0-9]+ passed'; then
    printf '%s│%s %stests en verde — %s%s\n' "$DIM" "$RST" "$GRN" "$SUMMARY" "$RST"
  else
    printf '%s│%s %sTESTS EN ROJO%s\n' "$DIM" "$RST" "$RED" "$RST"
    printf '%s╰─%s\n' "$RED" "$RST"
    grep -E '^(FAILED|ERROR)' "$TEST_OUT" | sed "s/^/  ${RED}/;s/$/${RST}/" | head -20
    [ -n "$SUMMARY" ] && printf '  %s%s%s\n' "$DIM" "$SUMMARY" "$RST"
    printf '\n  %sPara detalle: python3 -m pytest --ignore=tests/test_md_to_pdf.py -q%s\n' "$DIM" "$RST"
    printf '  %sLos tests ya están en rojo, con o sin este build. Ver §6 de la guía.%s\n\n' "$DIM" "$RST"
    rm -f "$TEST_OUT"
    exit 1
  fi
  rm -f "$TEST_OUT"
fi

# ── 3 · Minify ───────────────────────────────────────────────────────────────

printf '%s│%s %s\n' "$DIM" "$RST" ""
printf '%s│%s minificando %s → %s%s\n' "$DIM" "$RST" "$SRC" "$DIST" "$RST"

MINIFY_ARGS=("$SRC" "$DIST")
[ "$DRY_RUN" -eq 1 ] && MINIFY_ARGS+=("--dry-run")

if ! node scripts/build/minify.mjs "${MINIFY_ARGS[@]}"; then
  printf '%s╰─ falló la minificación%s\n\n' "$RED" "$RST"
  exit 1
fi

# ── 4 · Verify ───────────────────────────────────────────────────────────────

if [ "$DRY_RUN" -eq 1 ]; then
  printf '\n%s╰─ dry-run completado. No se escribió nada.%s\n\n' "$DIM" "$RST"
  exit 0
fi

if [ "$VERIFY" -eq 0 ]; then
  printf '\n%s╰─ build OK (verificación omitida con --no-verify)%s\n\n' "$YLW" "$RST"
  exit 0
fi

printf '\n'
if ! ./scripts/verify-frontend.sh "$DIST"; then
  printf '%s╰─ el build pasó pero la verificación falló. NO desplegar.%s\n\n' "$RED" "$RST"
  exit 1
fi

# ── 5 · Resumen ──────────────────────────────────────────────────────────────

printf '%s╭─ build listo%s\n' "$GRN"
printf '%s│%s dist:      %s%s%s\n' "$DIM" "$RST" "$GRN" "$DIST/" "$RST"
printf '%s│%s versión:   %s%s%s\n' "$DIM" "$RST" "$GRN" "$NEW_VER" "$RST"
printf '%s╰─%s\n\n' "$DIM" "$RST"

cat <<EOF
${DIM}Siguiente paso — desplegar:${RST}

  1. docker-compose.yml → FRONTEND_DIR=/srv/frontend-dist
  2. docker compose up -d --build app
  3. ${YLW}Abre una PWA YA INSTALADA${RST} y comprueba que sale el banner
     "Nueva versión disponible". Si no sale, el SW no se ha re-instalado.

${DIM}Volver a desarrollo:${RST}

  FRONTEND_DIR=/srv/frontend  &&  docker compose up -d app

EOF
