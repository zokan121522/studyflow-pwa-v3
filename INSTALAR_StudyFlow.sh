#!/usr/bin/env bash
# ---------------------------------------------------------------------------
#  INSTALAR_StudyFlow.sh  (Linux)
#
#  Un comando desde la raiz del paquete para:
#    1. Comprobar que el paquete esta completo
#    2. Preparar el entorno (venv + dependencias) si falta
#    3. Arrancar StudyFlow y abrir el navegador
#
#  Uso:  bash INSTALAR_StudyFlow.sh
#  (en Linux no hay bloqueo de Apple: nada de xattr)
# ---------------------------------------------------------------------------
set -u

cd "$(dirname "$0")" || exit 1

cat <<'BANNER'

  ============================================================
          S T U D Y F L O W   -   I N S T A L A D O R
  ============================================================

   Vamos a preparar StudyFlow en este PC.
   SOLO hay que esperar: no hace falta escribir nada.

BANNER

# ----- 1/3  Comprobar paquete -----
echo "  [1/3] Comprobando el paquete..."

if [ ! -f requirements-local.txt ]; then
    echo
    echo "  [X] No encuentro requirements-local.txt."
    echo
    echo "      La carpeta esta incompleta. Haz esto:"
    echo "        1. Cierra esta terminal"
    echo "        2. Descomprime el ZIP COMPLETO (unzip StudyFlow-linux-*.zip)"
    echo "        3. Entra en la carpeta StudyFlow-linux que se ha creado"
    echo "        4. Vuelve a lanzar:  bash INSTALAR_StudyFlow.sh"
    echo
    if [ -t 0 ]; then read -r -p "  Pulsa Intro para cerrar... " _; fi
    exit 1
fi

chmod +x launcher/StudyFlow.sh 2>/dev/null || true
echo "        Paquete OK."
echo

# ----- 2/3  Preparar el entorno -----
echo "  [2/3] Preparando el entorno... (solo la primera vez)"

if [ ! -x .venv/bin/python ]; then
    if ! command -v python3 >/dev/null 2>&1; then
        echo
        echo "  [X] No encuentro python3."
        echo
        echo "      En Debian/Ubuntu instala Python 3.12 con:"
        echo "          sudo apt install python3 python3-venv"
        echo
        echo "      Y vuelve a lanzar:  bash INSTALAR_StudyFlow.sh"
        echo
        if [ -t 0 ]; then read -r -p "  Pulsa Intro para cerrar... " _; fi
        exit 1
    fi
    if ! python3 -m venv .venv; then
        echo
        echo "  [X] No se pudo crear el entorno virtual."
        echo
        echo "      Suele faltar el modulo venv:"
        echo "          sudo apt install python3-venv"
        echo
        echo "      Y vuelve a lanzar:  bash INSTALAR_StudyFlow.sh"
        echo
        if [ -t 0 ]; then read -r -p "  Pulsa Intro para cerrar... " _; fi
        exit 1
    fi
fi
echo "        Entorno listo."
echo

# ----- 3/3  Arrancar -----
# launcher/StudyFlow.sh instala las dependencias si faltan (2-3 min la
# primera vez), arranca el servidor y abre el navegador solo.
echo "  [3/3] Arrancando StudyFlow..."
echo
exec bash launcher/StudyFlow.sh
