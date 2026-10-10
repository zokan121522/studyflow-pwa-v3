#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# StudyFlow — publica los paquetes (windows/macos/linux) en deploy/downloads/public/
#
# Qué hace:
#   1. Si no existe dist/StudyFlow-windows-<version>.zip, lo construye llamando
#      a scripts/build-windows-portable.sh (desde la raíz del repo).  Lo mismo
#      con los de macOS y Linux, con sus scripts build-*-portable.sh.
#   2. Copia cada ZIP a deploy/downloads/public/ con DOS nombres:
#        · StudyFlow-<plataforma>-<version>.zip  (histórico real)
#        · StudyFlow-<plataforma>-latest.zip     (enlace estable)
#   3. Genera deploy/downloads/public/version.json (lo lee el index.html y la
#      pill de /api/status del backend).  Los campos top-level (version, date,
#      size, sha256) siguen siendo los de WINDOWS para no tocar el backend;
#      el desglose por sistema está en "platforms".
#   4. Muestra un resumen. Si PUBLISH_HOST está definido, sincroniza public/
#      por rsync; si no, imprime el comando para lanzarlo más tarde.
#
# Uso:
#   bash deploy/downloads/publish.sh                # última versión de dist/
#   bash deploy/downloads/publish.sh 3.0.0          # forzar una versión
#   PUBLISH_HOST=server-deploy bash deploy/downloads/publish.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# Ruta del propio script → funciona desde cualquier sitio.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"     # deploy/downloads → raíz
DIST="$REPO_ROOT/dist"
PUBLIC_DIR="$SCRIPT_DIR/public"
BUILD_SCRIPT="$REPO_ROOT/scripts/build-windows-portable.sh"

# Plataformas publicadas y sus scripts de build (nombre = build-<p>-portable).
PLATFORMS="windows macos linux"
# La pill de /api/status (backend/routes/health.py) hace fetch de
# $DOWNLOADS_URL/version.json; si no hay env, el mismo default del backend.
BASE_URL="${DOWNLOADS_URL:-https://studyflowhub.dev}"

# ── 1. Versión ───────────────────────────────────────────────────────────────
# Se puede forzar con $1; si no, la más nueva (sort -V) de dist/.
VERSION="${1:-}"

newest_zip() {
  ls "$DIST"/StudyFlow-windows-*.zip 2>/dev/null \
    | grep -v 'StudyFlow-windows-latest\.zip$' \
    | sort -V | tail -n 1 || true
}

if [ -z "$VERSION" ]; then
  NEWEST="$(newest_zip)"
  if [ -n "$NEWEST" ]; then
    VERSION="$(basename "$NEWEST")"
    VERSION="${VERSION#StudyFlow-windows-}"
    VERSION="${VERSION%.zip}"
  fi
fi

# ── 2. Construir si hace falta ───────────────────────────────────────────────
ZIP_SRC=""
if [ -n "$VERSION" ] && [ -f "$DIST/StudyFlow-windows-$VERSION.zip" ]; then
  ZIP_SRC="$DIST/StudyFlow-windows-$VERSION.zip"
fi

if [ -z "$ZIP_SRC" ]; then
  echo "» No hay ZIP en dist/ para la versión '${VERSION:-desconocida}' → construyendo..."
  bash "$BUILD_SCRIPT"          # produce dist/StudyFlow-windows-<version>.zip
  NEWEST="$(newest_zip)"
  [ -n "$NEWEST" ] || { echo "[X] La construcción no produjo ningún ZIP en dist/."; exit 1; }
  ZIP_SRC="$NEWEST"
  VERSION="$(basename "$ZIP_SRC")"
  VERSION="${VERSION#StudyFlow-windows-}"
  VERSION="${VERSION%.zip}"
  # Si se pidió una versión concreta y sigue sin estar, abortar.
  if [ -n "${1:-}" ] && [ ! -f "$DIST/StudyFlow-windows-$1.zip" ]; then
    echo "[X] No existe dist/StudyFlow-windows-$1.zip (el build generó $VERSION)."; exit 1
  fi
fi

# ── 2b. macOS y Linux: construir si falta el ZIP de esta versión ────────────
# Un fallo aquí no debe dejar sin publicar el Windows: se avisa y se publica
# lo que haya.  (El build de Windows de arriba sí es obligatorio.)
for PLATFORM in macos linux; do
  [ -f "$DIST/StudyFlow-$PLATFORM-$VERSION.zip" ] && continue
  echo "» Falta el ZIP de $PLATFORM para $VERSION → construyendo..."
  if ! bash "$REPO_ROOT/scripts/build-$PLATFORM-portable.sh"; then
    echo "[!] build-$PLATFORM-portable.sh falló → se publica SIN $PLATFORM."
  fi
done

mkdir -p "$PUBLIC_DIR"

# ── 3. Copiar con ambos nombres (versionado + latest) ────────────────────────
for PLATFORM in $PLATFORMS; do
  SRC="$DIST/StudyFlow-$PLATFORM-$VERSION.zip"
  if [ ! -f "$SRC" ]; then
    echo "» (no hay ZIP de $PLATFORM en dist/: no se publica)"
    continue
  fi
  cp "$SRC" "$PUBLIC_DIR/StudyFlow-$PLATFORM-$VERSION.zip"
  cp "$SRC" "$PUBLIC_DIR/StudyFlow-$PLATFORM-latest.zip"
done

[ -f "$PUBLIC_DIR/StudyFlow-windows-$VERSION.zip" ] \
  || { echo "[X] Falta el ZIP de Windows en public/."; exit 1; }

# ── 4. sha256 + tamaño + fecha ───────────────────────────────────────────────
sha_of() { shasum -a 256 "$1" | awk '{print $1}'; }
size_of() { wc -c < "$1" | tr -d ' '; }

# Campos legacy top-level = Windows (los lee /api/status tal cual están hoy).
SHA256="$(sha_of "$PUBLIC_DIR/StudyFlow-windows-latest.zip")"
SIZE="$(size_of "$PUBLIC_DIR/StudyFlow-windows-latest.zip")"
FECHA="$(date +%d/%m/%Y)"

# URL de la app web (opcional): si APP_URL no está definida, queda cadena vacía.
APP_URL_VALUE="${APP_URL:-}"
APP_URL_VALUE="${APP_URL_VALUE//\\/\\\\}"
APP_URL_VALUE="${APP_URL_VALUE//\"/\\\"}"

# Una entrada de "platforms": {"file", "size", "sha256", "url"}.
platform_json() {  # $1 = plataforma
  local p="$1" f="StudyFlow-$1-$VERSION.zip" path entry
  path="$PUBLIC_DIR/$f"
  [ -f "$path" ] || return 1
  entry="\"$p\": {\"file\": \"$f\", \"size\": $(size_of "$path"), \"sha256\": \"$(sha_of "$path")\", \"url\": \"$BASE_URL/$f\"}"
  printf '%s' "$entry"
}

# Notas de versión (opcional): release-notes.json[$VERSION] → array JSON.
# Si la versión no está en el fichero (o el fichero falta), el campo "notes"
# se omite y el publish sigue: las notas nunca pueden romper una publicación.
NOTES_SRC="$SCRIPT_DIR/release-notes.json"
NOTES_JSON="$(
  python3 - "$NOTES_SRC" "$VERSION" <<'PY' 2>/dev/null || true
import json
import sys

try:
    notes = json.load(open(sys.argv[1], encoding='utf-8'))
except Exception:
    sys.exit(0)
items = notes.get(sys.argv[2])
if isinstance(items, list) and items:
    print(json.dumps(items, ensure_ascii=False))
PY
)"

# version.json → lo consume el index.html (fetch con cache: no-store) y la
# pill de versión del backend (top-level version/date/size, sin cambios).
VJ="$PUBLIC_DIR/version.json"
{
  cat <<EOF
{
  "file": "StudyFlow-windows-latest.zip",
  "versioned": "StudyFlow-windows-$VERSION.zip",
  "version": "$VERSION",
  "size": $SIZE,
  "date": "$FECHA",
  "sha256": "$SHA256",
  "app_url": "$APP_URL_VALUE",
  "platforms": {
EOF
  N=0
  for PLATFORM in $PLATFORMS; do
    ENTRY="$(platform_json "$PLATFORM" || true)"
    [ -n "$ENTRY" ] || continue
    N=$((N + 1))
    [ "$N" -gt 1 ] && printf ',\n'
    printf '    %s' "$ENTRY"
  done
  printf '\n  }'
  [ -n "$NOTES_JSON" ] && printf ',\n  "notes": %s' "$NOTES_JSON"
  printf '\n}\n'
} > "$VJ"

# ── 4b. JSON-LD del index.html → versión recién publicada ────────────────────
# Se reescribe aquí porque es la única forma de que softwareVersion no quede
# obsoleto en el siguiente release (los href ya usan StudyFlow-<os>-latest.zip).
if [ -f "$PUBLIC_DIR/index.html" ]; then
  python3 - "$PUBLIC_DIR/index.html" "$VERSION" <<'PY'
import re
import sys

path, version = sys.argv[1], sys.argv[2]
src = open(path, encoding="utf-8").read()
dst = re.sub(
    r'("softwareVersion"\s*:\s*")[^"]+(")',
    lambda m: m.group(1) + version + m.group(2),
    src,
    count=1,
)
if dst != src:
    open(path, "w", encoding="utf-8").write(dst)
    print(f"  JSON-LD softwareVersion → {version}")
PY
fi

# ── 5. Resumen ───────────────────────────────────────────────────────────────
echo
echo "Publicado en deploy/downloads/public/:"
for PLATFORM in $PLATFORMS; do
  PZIP="$PUBLIC_DIR/StudyFlow-$PLATFORM-$VERSION.zip"
  [ -f "$PZIP" ] || continue
  MB="$(awk -v s="$(size_of "$PZIP")" 'BEGIN{printf "%.1f", s/1048576}')"
  MB="${MB//./,}"
  printf '  %-34s %10s MB\n' "StudyFlow-$PLATFORM-$VERSION.zip" "$MB"
done
printf '  %-34s %10s\n'   "Versión"         "$VERSION"
printf '  %-34s %10s\n'   "Fecha"           "$FECHA"
printf '  %-34s %10s\n'   "SHA256 (win)"    "$SHA256"
printf '  %-34s %10s\n'   "Ruta"            "$PUBLIC_DIR"

# ── 6. (Opcional) rsync al servidor ──────────────────────────────────────────
echo
if [ -n "${PUBLISH_HOST:-}" ]; then
  REMOTE_DIR="${PUBLISH_REMOTE_DIR:-~/studyflow-downloads/public}"
  echo "» Sincronizando public/ → $PUBLISH_HOST:$REMOTE_DIR ..."
  rsync -avz --delete "$PUBLIC_DIR/" "$PUBLISH_HOST:$REMOTE_DIR/"
  echo "» listo. nginx NO necesita reiniciarse: el bind mount (./public) ya lo ve."
  echo "» recuerda: el hostname del Cloudflare Tunnel debe apuntar al puerto 8090."
  # NOTA (estadísticas internas): /stats.html se rige por los mounts
  # ./nginx/default.conf + ./nginx/.htpasswd del docker-compose del server.
  # Si el deploy recrea el contenedor sin esos mounts, reaplica con
  #   bash scripts/stats/install-nginx-stats.sh
  # y comprueba el cron de regeneración (cada 5 min):
  #   crontab -l | grep stats.sh
  #   bash ~/studyflow-downloads/stats.sh   # regeneración manual
  # Detalles y credenciales: scripts/stats/README.md
else
  echo "» PUBLISH_HOST no está definido. Cuando quieras subirlo, lanza (desde deploy/downloads/):"
  echo "    rsync -avz --delete public/ \"<host>:${PUBLISH_REMOTE_DIR:-~/studyflow-downloads/public}/\""
fi
