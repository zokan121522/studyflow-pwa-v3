#!/usr/bin/env bash
# ────────────────────────────────────────────────────────────────────────────
#  StudyFlow — launcher para macOS y Linux (terminal o doble clic)
#
#  Uso:
#    StudyFlow.sh              arranca StudyFlow y abre el navegador
#    StudyFlow.sh stop         detiene la instancia local
#    StudyFlow.sh status       muestra el estado
#    StudyFlow.sh setup        (re)crea el entorno e instala dependencias
#
#  En macOS puedes hacer doble clic en launcher/StudyFlow.command, que no
#  hace mas que venir aqui.  La primera vez puede tardar: instala las
#  dependencias (2-3 min).  A partir de ahí es un lanzamiento y directo a
#  la 4/4.
#
#  Los datos NO viven aquí, sino en:
#    macOS : ~/Library/Application Support/studyflow/
#    Linux : ~/.local/share/studyflow/
#  así que borrar esta carpeta no destruye nada.
# ────────────────────────────────────────────────────────────────────────────
set -uo pipefail

LAUNCHER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$LAUNCHER_DIR/.." || exit 1

VENV=".venv"
VENV_PY="$VENV/bin/python"
DEFAULT_URL="http://127.0.0.1:8477/"

if [ "$(uname -s)" = "Darwin" ]; then
    DATA_DIR="$HOME/Library/Application Support/studyflow"
else
    DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/studyflow"
fi

# Espera a que el usuario cierre la ventana / pulse Intro y, SOLO si esta
# ventana arranco el servidor, lo detiene al salir: es lo que promete el
# mensaje final ("cierra esta ventana para detener"), y es lo mismo que
# pasa en Windows, donde la consola se lleva al hijo consigo.
WAIT_STOP=0
TMP_OUT=""

cleanup() {
    [ -n "$TMP_OUT" ] && rm -f "$TMP_OUT"
    if [ "$WAIT_STOP" = 1 ]; then
        echo
        echo "  Deteniendo StudyFlow..."
        "$VENV_PY" launcher/launch.py --stop >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT
trap 'exit 130' INT TERM HUP

# ── ¿estamos en la raíz del proyecto? ───────────────────────────────────────
if [ ! -f requirements-local.txt ]; then
    echo
    echo "  [X] No encuentro requirements-local.txt."
    echo
    echo "      El paquete esta incompleto o estas lanzando este script"
    echo "      fuera de la carpeta StudyFlow."
    echo
    echo "      Haz esto:"
    echo "        1. Cierra esta ventana"
    echo "        2. Descomprime el ZIP completo (doble clic en el .zip)"
    echo "        3. Entra en la carpeta StudyFlow que se ha creado"
    echo "        4. Lanza otra vez el lanzador"
    echo
    if [ -t 0 ]; then read -r -p "  Pulsa Intro para cerrar... " _; fi
    exit 1
fi

cat <<'BANNER'

  ============================================================
                   S T U D Y F L O W
  ============================================================

  Primer arranque, solo la primera vez, estos 4 pasos:
      1/4  Preparando el entorno
      2/4  Comprobando Python
      3/4  Instalando dependencias - puede tardar 2-3 min
      4/4  Arrancando la app

  A partir de aqui: directo a la 4/4.

BANNER

# ── ¿Python disponible? ─────────────────────────────────────────────────────
if ! command -v python3 >/dev/null 2>&1; then
    echo "  [X] No encuentro python3."
    echo
    echo "      Instala Python 3.12:"
    echo "        macOS : https://www.python.org/downloads/  (o brew install python3)"
    echo "        Linux : sudo apt install python3 python3-venv"
    echo
    if [ -t 0 ]; then read -r -p "  Pulsa Intro para cerrar... " _; fi
    exit 1
fi

# ── dependencias realmente instaladas? ──────────────────────────────────────
# Comprobar que existe el binario NO dice nada: un install fallido (pin en
# conflicto, corte de red) deja el venv con SOLO pip dentro y entonces este
# script lo daria por bueno para siempre. Hay que comprobar que las
# dependencias se IMPORTAN. Lista identica a la del .bat de Windows.
deps_ok() {
    [ -x "$VENV_PY" ] || return 1
    "$VENV_PY" -c "import flask, flask_cors, fitz, icalendar, jwt, requests, edge_tts, gevent, tzdata" \
        >/dev/null 2>&1
}

do_setup() {
    echo "  [1/4] Preparando el entorno (solo la primera vez, puede tardar 2-3 min).."
    echo
    if [ ! -x "$VENV_PY" ]; then
        if ! python3 -m venv "$VENV"; then
            echo
            echo "  [X] No se pudo crear el entorno virtual."
            echo
            echo "      En Linux suele faltar el modulo venv:"
            echo "        sudo apt install python3-venv"
            echo
            if [ -t 0 ]; then read -r -p "  Pulsa Intro para cerrar... " _; fi
            exit 1
        fi
    fi
    echo "  [2/4] Python listo - $(python3 --version 2>&1)."
    echo "  [3/4] Instalando dependencias - puede tardar 2-3 min.."
    echo
    # Asegurar que pip esté presente (venvs pueden no traerlo)
    if ! "$VENV_PY" -m pip --version >/dev/null 2>&1; then
        if python3 -c "import ensurepip" >/dev/null 2>&1; then
            "$VENV_PY" -m ensurepip --upgrade >/dev/null 2>&1 || true
        fi
        if ! "$VENV_PY" -m pip --version >/dev/null 2>&1; then
            curl -fsSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py >/dev/null 2>&1 || true
            "$VENV_PY" /tmp/get-pip.py --quiet >/dev/null 2>&1 || true
            rm -f /tmp/get-pip.py || true
        fi
    fi
    "$VENV_PY" -m pip install --upgrade pip --quiet
    if ! "$VENV_PY" -m pip install -r requirements-local.txt; then
        echo
        echo "  [X] La instalacion fallo. Copia el error de arriba."
        echo
        echo "  Lo mas comun: un pin en conflicto con otro paquete del fichero."
        echo "  requirements-local.txt es la lista; nada mas."
        echo
        if [ -t 0 ]; then read -r -p "  Pulsa Intro para cerrar... " _; fi
        exit 1
    fi
    echo
    echo "  Listo. Las siguientes ejecuciones solo arrancan."
    echo
}

open_url() {
    # Sin dos pestanas: launch.py se le pasa --no-browser y quien abre aqui
    # es este script, con la herramienta propia de cada sistema.
    case "$(uname -s)" in
        Darwin)
            if command -v open >/dev/null 2>&1 && open "$1" >/dev/null 2>&1; then
                return 0
            fi
            osascript -e "open location \"$1\"" >/dev/null 2>&1 || true
            ;;
        *)
            if command -v xdg-open >/dev/null 2>&1; then
                ( xdg-open "$1" >/dev/null 2>&1 & )
            else
                echo "    (no hay navegador automatico: abre $1 a mano)"
            fi
            ;;
    esac
}

ARG="${1:-}"

# ── setup forzado ───────────────────────────────────────────────────────────
if [ "$ARG" = "setup" ]; then
    do_setup
    echo "  Entorno listo. Arranca con:  bash launcher/StudyFlow.sh"
    echo
    exit 0
fi

# ── ¿entorno listo? ─────────────────────────────────────────────────────────
if ! deps_ok; then
    do_setup
fi

echo "  [4/4] Arrancando StudyFlow.."
echo

TMP_OUT="$(mktemp "${TMPDIR:-/tmp}/studyflow-launch.XXXXXX")"
# --no-browser: el navegador lo abre este script (open / xdg-open) para no
# abrir dos pestanas; el resto del comando es el mismo que usa el .bat.
"$VENV_PY" launcher/launch.py --no-browser "$@" 2>&1 | tee "$TMP_OUT"
CODE="${PIPESTATUS[0]}"

if [ "$CODE" -ne 0 ]; then
    echo
    echo "  [X] No arranco. Log en: $DATA_DIR/launcher.log"
    echo
    if [ -t 0 ]; then read -r -p "  Pulsa Intro para cerrar... " _; fi
    exit "$CODE"
fi

# stop / status: launch.py ya ha contestado, nada mas que hacer.
case "$ARG" in
    stop|--stop|status) exit 0 ;;
esac

# ── abrir el navegador ──────────────────────────────────────────────────────
# launch.py siempre usa el puerto fijo 8477 (nunca se desplaza a otro); la URL
# se lee igualmente de su salida por si el mensaje cambia.
URL="$(grep -m1 -Eo 'http://127\.0\.0\.1:[0-9]+/' "$TMP_OUT" || true)"
[ -n "$URL" ] || URL="$DEFAULT_URL"

OURS=1
grep -q "ya está corriendo" "$TMP_OUT" && OURS=0

open_url "$URL"

cat <<EOF

  ============================================================
       ***  STUDYFLOW ESTA EN MARCHA  ***
  ============================================================

  Navegador abriendose en $URL
    - si no se abre, copia la direccion a mano en la barra
      de direcciones. Listo: ya puedes usarlo.

  Datos : $DATA_DIR
  Log   : $DATA_DIR/launcher.log
EOF

if [ "$OURS" = 1 ] && [ -t 0 ]; then
    WAIT_STOP=1
    cat <<EOF

  Para detener la app: cierra esta ventana, o pulsa Intro.
  Para volver a arrancar: doble clic en StudyFlow.command
  (o bash launcher/StudyFlow.sh).

  Mientras esta ventana este abierta, StudyFlow sigue aqui.
EOF
    echo
    read -r _ || true
elif [ "$OURS" = 0 ]; then
    cat <<EOF

  StudyFlow ya estaba corriendo, asi que no lo ha arrancado
  ESTA ventana: cerrarla no lo detiene.  Para pararlo:

        bash launcher/StudyFlow.sh stop
EOF
    echo
fi
