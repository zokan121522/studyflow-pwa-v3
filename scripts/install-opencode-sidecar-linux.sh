#!/usr/bin/env bash
# ---------------------------------------------------------------------------
#  install-opencode-sidecar-linux.sh  (Linux / contenedor-sandbox)
#
#  Levanta el sidecar local de OpenZen (un `opencode serve` escuchando en
#  127.0.0.1:54321) como SERVICIO systemd, para que la PWA encuentre su
#  backend de IA sin que nadie abra una Terminal ni arranque nada a mano.
#
#  Es el hermano Linux de launcher/install-opencode-sidecar.command (macOS):
#  mismo fichero de credenciales (~/.studyflow/opencode_sidecar.env, que lee
#  backend/openzen_sidecar.py) pero en lugar de un LaunchAgent escribe una
#  unidad systemd (opencode-sidecar.service).
#
#  Que hace:
#    1. Instala el binario `opencode` si no esta (instalador oficial,
#       detecta la arquitectura solo).
#    2. Crea (idempotente) ~/.studyflow/opencode_sidecar.env con
#       OPENCODE_SERVER_URL / OPENCODE_SERVER_USERNAME / OPENCODE_SERVER_PASSWORD
#       (chmod 600). NUNCA pisa la password de un fichero ya existente.
#    3. Escribe /etc/systemd/system/opencode-sidecar.service
#       (Restart=always, EnvironmentFile apuntando al .env).
#    4. systemctl daemon-reload + enable --now y comprueba que el puerto responde.
#
#  Uso:
#    scripts/install-opencode-sidecar-linux.sh              instala y arranca
#    scripts/install-opencode-sidecar-linux.sh --dry-run    imprime lo que haria
#    scripts/install-opencode-sidecar-linux.sh --uninstall  quita el servicio
#
#  NOTA: `opencode serve` NO tiene flags de autenticacion (solo --port /
#  --hostname). Las credenciales viven en el .env para paridad con macOS y
#  Windows; las consume el cliente de StudyFlow, no el propio binario.
#
#  Fichero ASCII puro (sin tildes) por seguridad de codepage.
# ---------------------------------------------------------------------------
set -u

OC_UNIT="opencode-sidecar"
OC_PORT=54321
OC_HOST="127.0.0.1"
OC_URL="http://127.0.0.1:54321"
OC_USER_NAME="studyflow-ce01"

ENV_DIR="$HOME/.studyflow"
ENV_FILE="$ENV_DIR/opencode_sidecar.env"
UNIT_PATH="/etc/systemd/system/${OC_UNIT}.service"

DRY_RUN=0
UNINSTALL=0
for arg in "$@"; do
    case "$arg" in
        --dry-run)   DRY_RUN=1 ;;
        --uninstall) UNINSTALL=1 ;;
        -h|--help)
            sed -n '2,45p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "ERROR: opcion desconocida: $arg" >&2
            echo "Uso: $(basename "$0") [--dry-run|--uninstall]" >&2
            exit 2
            ;;
    esac
done

# sudo solo cuando no somos root (en el sandbox suele correrse como root).
if [ "$(id -u)" -eq 0 ]; then SUDO=""; else SUDO="sudo"; fi

# ------------------------------------------------------------------ binary
# Resuelve el binario de opencode: ruta estable primero, luego el PATH.
resolve_binary() {
    if [ -x "$HOME/.opencode/bin/opencode" ]; then
        OC_BIN="$HOME/.opencode/bin/opencode"
        return 0
    fi
    if command -v opencode >/dev/null 2>&1; then
        OC_BIN="$(command -v opencode)"
        return 0
    fi
    return 1
}

# ------------------------------------------------------------------ uninstall
if [ "$UNINSTALL" = 1 ]; then
    echo "  Quitando el sidecar de OpenZen ($OC_UNIT)..." >&2
    if [ "$DRY_RUN" = 1 ]; then
        echo "  (dry-run) $SUDO systemctl disable --now $OC_UNIT" >&2
        echo "  (dry-run) rm -f $UNIT_PATH" >&2
        echo "  (dry-run) NO se toca $ENV_FILE (se conservan las credenciales)" >&2
        exit 0
    fi
    $SUDO systemctl disable --now "$OC_UNIT" >/dev/null 2>&1 || true
    $SUDO rm -f "$UNIT_PATH"
    $SUDO systemctl daemon-reload >/dev/null 2>&1 || true
    echo "  Sidecar descargado. Unidad borrada: $UNIT_PATH" >&2
    echo "  (Se conserva $ENV_FILE.)" >&2
    exit 0
fi

# ------------------------------------------------------------------ opencode
# Instala el binario si falta (instalador oficial, arch-aware). No lo
# reinstalamos si ya esta: `curl | bash` pisaria una version ya gestionada.
if ! resolve_binary; then
    if [ "$DRY_RUN" = 1 ]; then
        echo "  [dry-run] No hay 'opencode'; se instalaria con:" >&2
        echo "  [dry-run]   curl -fsSL https://opencode.ai/install | bash" >&2
        OC_BIN="$HOME/.opencode/bin/opencode"
    else
        echo "  [1/5] Instalando opencode (instalador oficial)..."
        curl -fsSL https://opencode.ai/install | bash || {
            echo "  [X] Fallo el instalador de opencode." >&2
            exit 1
        }
        if ! resolve_binary; then
            echo "  [X] opencode no quedo en el PATH ni en ~/.opencode/bin." >&2
            exit 1
        fi
    fi
fi

# ------------------------------------------------------------------ creds
# Password aleatoria (16 bytes hex); usuario fijo del sandbox. Igual que
# macOS: se generan UNA vez y se conservan.
env_value() {
    [ -f "$ENV_FILE" ] || return 0
    sed -n "s/^$1=//p" "$ENV_FILE" | head -1
}

OC_PASS="$(env_value OPENCODE_SERVER_PASSWORD)"
ENV_EXISTS=0
if [ -f "$ENV_FILE" ]; then ENV_EXISTS=1; fi
# Solo generamos lo que falte -> nunca pisamos una password existente.
if [ -z "$OC_PASS" ]; then
    if command -v openssl >/dev/null 2>&1; then
        OC_PASS="$(openssl rand -hex 16)"
    else
        OC_PASS="$(LC_ALL=C tr -dc 'a-f0-9' </dev/urandom | head -c 32)"
    fi
fi

# El contenido exacto del fichero de entorno.
read -r -d '' ENV_BODY <<EOF || true
# OpenZen sidecar credentials (generated by install-opencode-sidecar-linux.sh).
# Read by backend/openzen_sidecar.py::apply_sidecar_env().
# Do not edit the password by hand unless you also restart the sidecar.
OPENCODE_SERVER_URL=$OC_URL
OPENCODE_SERVER_USERNAME=$OC_USER_NAME
OPENCODE_SERVER_PASSWORD=$OC_PASS
EOF

# La unidad systemd completa, en una variable: --dry-run solo la imprime.
read -r -d '' UNIT_BODY <<EOF || true
[Unit]
Description=OpenZen sidecar (opencode serve) for StudyFlow
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
EnvironmentFile=$ENV_FILE
ExecStart=$OC_BIN serve --port $OC_PORT --hostname $OC_HOST
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

# ------------------------------------------------------------------ dry-run
if [ "$DRY_RUN" = 1 ]; then
    {
        echo "  [dry-run] Binario   : $OC_BIN"
        echo "  [dry-run] Env file  : $ENV_FILE  (chmod 600)"
        echo "  [dry-run] Unidad    : $UNIT_PATH"
        if [ "$ENV_EXISTS" = 1 ]; then
            echo "  [dry-run] El fichero de entorno ya existe -> se CONSERVA su password."
        else
            echo "  [dry-run] Se generaria este fichero de entorno:"
        fi
        printf '%s\n' "$ENV_BODY"
        echo "  [dry-run] Se escribaria esta unidad systemd:"
        printf '%s\n' "$UNIT_BODY"
        echo "  [dry-run] NADA se ha modificado (dry-run)."
    } >&2
    exit 0
fi

# ------------------------------------------------------------------ banner
cat <<'BANNER'

  ============================================================
        O P E N Z E N   -   SIDECAR LOCAL (Linux/systemd)
  ============================================================

   Vamos a arrancar el servidor local de OpenZen (opencode serve)
   como servicio systemd, para que el sandbox siempre tenga su IA.

BANNER

# ----- 2/5  Fichero de entorno -----
echo "  [2/5] Preparando credenciales ($ENV_FILE)..."
mkdir -p "$ENV_DIR"
# Reescribimos por si faltaba alguna clave, respetando la password existente.
( umask 077; printf '%s\n' "$ENV_BODY" > "$ENV_FILE" )
chmod 600 "$ENV_FILE"
if [ "$ENV_EXISTS" = 1 ]; then
    echo "        Conservadas las credenciales existentes."
else
    echo "        Credenciales generadas."
fi

# ----- 3/5  Escribir la unidad systemd -----
echo "  [3/5] Escribiendo la unidad ($UNIT_PATH)..."
printf '%s\n' "$UNIT_BODY" | $SUDO tee "$UNIT_PATH" >/dev/null
echo "        $UNIT_PATH"

# ----- 4/5  Habilitar y arrancar -----
echo "  [4/5] Habilitando el servicio (systemctl enable --now)..."
$SUDO systemctl daemon-reload
$SUDO systemctl enable --now "$OC_UNIT" || {
    echo "  [X] No se pudo habilitar $OC_UNIT." >&2
    exit 1
}

# ----- 5/5  Verificar que el puerto responde -----
echo "  [5/5] Esperando a que el sidecar responda en $OC_URL ..."
listo=0
intento=0
while [ "$intento" -lt 20 ]; do
    if curl -sS -o /dev/null "$OC_URL" >/dev/null 2>&1; then
        listo=1
        break
    fi
    intento=$((intento + 1))
    sleep 1
done

cat <<EOF

  ============================================================
        ***  OPENZEN SIDECAR INSTALADO  ***
  ============================================================

   Servicio : $OC_UNIT
   Binario  : $OC_BIN
   Endpoint : $OC_URL
   Env      : $ENV_FILE   (chmod 600)
   Unidad   : $UNIT_PATH

   Para comprobarlo:
       systemctl status $OC_UNIT
       curl -sS $OC_URL

   Para quitarlo:  scripts/install-opencode-sidecar-linux.sh --uninstall

EOF
if [ "$listo" = 1 ]; then
    echo "   El sidecar esta respondiendo."
else
    echo "   El sidecar queda instalado; puede tardar unos segundos en responder."
    echo "   Mira el log si no arranca: journalctl -u $OC_UNIT -n 50"
fi
echo
