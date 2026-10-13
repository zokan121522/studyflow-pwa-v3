#!/bin/bash
# ---------------------------------------------------------------------------
#  uninstall-opencode-sidecar.command  (macOS)
#
#  Quita el sidecar local de OpenZen (opencode serve) de este Mac:
#    - descarga el LaunchAgent com.studyflow.opencode-sidecar y con el se
#      para el `opencode serve` del puerto 54321,
#    - borra ~/Library/LaunchAgents/com.studyflow.opencode-sidecar.plist.
#
#  NO borra las credenciales: ~/.studyflow/opencode_sidecar.env se conserva
#  (asi volver a activarlo es instantaneo y no cambia la password).
#
#  Uso:
#    uninstall-opencode-sidecar.command            quita el sidecar
#    uninstall-opencode-sidecar.command --dry-run  solo dice que haria
#
#  Fichero ASCII puro (sin tildes) por seguridad de codepage.
# ---------------------------------------------------------------------------
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cat <<'BANNER'

  ============================================================
      O P E N Z E N   -   QUITAR SIDECAR (macOS)
  ============================================================

   Vamos a quitar el servidor local de OpenZen.
   Tus credenciales NO se tocan.

BANNER

exec bash "$SCRIPT_DIR/install-opencode-sidecar.command" --uninstall "$@"
