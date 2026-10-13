#!/usr/bin/env bash
# Rewrite ONLY the "beta" block of public/version.json from the built dist zips.
#
# Stable fields (version, platforms, sha256, ...) are loaded and re-emitted in
# the same key order, so `git diff` on version.json shows only the beta block.
#
# Usage:  write-beta-json.sh [version] [date]
#   version  default 3.1.2
#   date     default today (dd/mm/YYYY)
#
# Reads dist/StudyFlow-<os>-<version>.zip for windows/macos/linux and fails
# loudly if any of them is missing. Never writes/reads any *-latest.zip.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PUBLIC_DIR="$SCRIPT_DIR/public"
VJ="$PUBLIC_DIR/version.json"
DIST_DIR="$SCRIPT_DIR/../../dist"

BETA_VER="${1:-3.1.2}"
BETA_DATE="${2:-$(date +%d/%m/%Y)}"
BASE_URL="${BASE_URL:-https://studyflowhub.dev}"

[ -f "$VJ" ] || { echo "[X] Missing $VJ" >&2; exit 1; }
[ -d "$DIST_DIR" ] || { echo "[X] Missing dist dir: $DIST_DIR" >&2; exit 1; }

python3 - "$VJ" "$BETA_VER" "$BETA_DATE" "$BASE_URL" "$DIST_DIR" <<'PY'
import hashlib
import json
import os
import sys

vj_path, bver, bdate, base_url, dist = sys.argv[1:6]

with open(vj_path, encoding="utf-8") as fh:
    v = json.load(fh)

# Preserve the stable/top-level fields in their original order.
stable = {}
for key in ("file", "versioned", "version", "size", "date", "sha256",
            "app_url", "platforms", "notes"):
    if key in v:
        stable[key] = v[key]

notes = [
    "macOS: la app se instala como servicio y se enciende SOLA al arrancar el Mac (ya no hay que abrir el lanzador).",
    "Si se cae, se reanima sola: se acabó el aviso \"servidor no disponible\".",
    "El botón \"API\" del panel ya reconecta siempre (ayudante local en 127.0.0.1:8478).",
    "Instalador de un solo doble clic, sin Terminal: prepara el entorno, registra el servicio y abre el navegador.",
    "Cerrar la ventana del lanzador ya no apaga la app.",
    "Comando para quitar el arranque automático sin perder datos.",
    "El arranque automático es solo de macOS: Windows y Linux mantienen el lanzador manual.",
]

beta_plats = {}
for platform in ("windows", "macos", "linux"):
    zpath = os.path.join(dist, f"StudyFlow-{platform}-{bver}.zip")
    if not os.path.isfile(zpath):
        sys.exit(f"[X] Missing zip: {zpath}")
    fname = f"StudyFlow-{platform}-{bver}-beta.zip"
    with open(zpath, "rb") as zh:
        sha = hashlib.sha256(zh.read()).hexdigest()
    beta_plats[platform] = {
        "file": fname,
        "size": os.path.getsize(zpath),
        "sha256": sha,
        "url": f"{base_url}/{fname}",
    }

stable["beta"] = {
    "version": bver,
    "date": bdate,
    "notes": notes,
    "platforms": beta_plats,
}

with open(vj_path, "w", encoding="utf-8") as fh:
    json.dump(stable, fh, ensure_ascii=False, indent=2)
    fh.write("\n")

print(f"OK beta {bver}")
PY
