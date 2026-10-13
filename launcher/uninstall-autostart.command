#!/bin/bash
# ---------------------------------------------------------------------------
#  uninstall-autostart.command  (macOS)
#
#  Quita el autoarranque de StudyFlow de este Mac:
#    - descarga el servicio (launchctl bootout) y con el se paran el servidor
#      (8477) y el helper de control (8478),
#    - borra ~/Library/LaunchAgents/com.studyflow.launcher.plist.
#
#  NO borra NADA mas: tus DATOS siguen intactos en
#      ~/Library/Application Support/studyflow   y   ~/.studyflow
#  y el programa copiado en ~/Applications/StudyFlow tambien se queda.
#  Volver a activarlo es tan simple como relanzar install-autostart.command.
#
#  Uso:
#    uninstall-autostart.command            quita el servicio
#    uninstall-autostart.command --dry-run  solo dice que haria, sin cambios
#
#  Fichero ASCII puro (sin tildes) por seguridad de codepage.
# ---------------------------------------------------------------------------
set -u

PLIST="$HOME/Library/LaunchAgents/com.studyflow.launcher.plist"
LABEL="com.studyflow.launcher"
UID_NUM="$(id -u)"
DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

cat <<'BANNER'

  ============================================================
       S T U D Y F L O W   -   QUITAR AUTOARRANQUE
  ============================================================

   Vamos a quitar el servicio de arranque automatico.
   Tus datos y tus apuntes NO se tocan.

BANNER

if [ ! -f "$PLIST" ]; then
    echo "  El autoarranque no estaba instalado ($LABEL)."
    echo "  Nada que hacer."
    echo
    if [ -t 0 ]; then read -r -p "  Pulsa Intro para cerrar... " _; fi
    exit 0
fi

echo "  [1/2] Descargando el servicio (esto para el servidor y el helper)..."
if [ "$DRY_RUN" = 1 ]; then
    echo "        (dry-run) launchctl bootout gui/$UID_NUM $PLIST"
else
    launchctl bootout "gui/$UID_NUM" "$PLIST" >/dev/null 2>&1 \
        || launchctl bootout "gui/$UID_NUM/$LABEL" >/dev/null 2>&1 || true
    echo "        Servicio descargado."
fi

echo "  [2/2] Borrando el fichero del servicio..."
if [ "$DRY_RUN" = 1 ]; then
    echo "        (dry-run) rm $PLIST"
else
    rm -f "$PLIST"
    echo "        Borrado: $PLIST"
fi

cat <<EOF

  ============================================================
        ***  AUTOARRANQUE QUITADO  ***
  ============================================================

   StudyFlow ya NO arranca solo. Si quieres volver a activarlo,
   lanza de nuevo:  launcher/install-autostart.command

   CONSERVADO (intacto):
     - Tus datos : ~/Library/Application Support/studyflow
     - Ajustes   : ~/.studyflow
     - Programa  : ~/Applications/StudyFlow
     - Log       : ~/Library/Logs/studyflow-launcher.log

EOF
