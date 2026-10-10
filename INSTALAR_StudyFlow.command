#!/bin/bash
# ---------------------------------------------------------------------------
#  INSTALAR_StudyFlow.command  (macOS)
#
#  Doble clic en ESTE fichero (el de la raiz) para:
#    1. Quitar el bloqueo de Apple (Gatekeeper / quarantine)
#    2. Preparar el entorno (venv + dependencias) si falta
#    3. Arrancar StudyFlow y abrir el navegador
#
#  No hay que escribir nada: todo automatico, con mensajes grandes.
#  Fichero ASCII puro (sin tildes) por seguridad de codepage.
# ---------------------------------------------------------------------------
set -u

cd "$(dirname "$0")" || exit 1

cat <<'BANNER'

  ============================================================
          S T U D Y F L O W   -   I N S T A L A D O R
  ============================================================

   Vamos a preparar StudyFlow en este Mac.
   SOLO hay que esperar: no hace falta escribir nada.

BANNER

# ----- 1/3  Quitar el bloqueo de Apple -----
echo "  [1/3] Quitando el bloqueo de Apple... (quitando bloqueo de Apple)"
if command -v xattr >/dev/null 2>&1; then
    xattr -dr com.apple.quarantine "$(pwd)" 2>/dev/null || true
    echo "        Listo: ya no saldra el aviso de"
    echo "        'Apple no ha podido verificar que no contiene software malicioso'."
else
    echo "        (no hay xattr, se omite)"
fi
echo

# ----- 2/3  Preparar el entorno -----
echo "  [2/3] Preparando el entorno... (solo la primera vez)"

if [ ! -f requirements-local.txt ]; then
    echo
    echo "  [X] No encuentro requirements-local.txt."
    echo
    echo "      La carpeta esta incompleta. Haz esto:"
    echo "        1. Cierra esta ventana"
    echo "        2. Descomprime el ZIP COMPLETO (doble clic en el .zip)"
    echo "        3. Entra en la carpeta StudyFlow-macos que se ha creado"
    echo "        4. Vuelve a lanzar INSTALAR_StudyFlow.command"
    echo
    if [ -t 0 ]; then read -r -p "  Pulsa Intro para cerrar... " _; fi
    exit 1
fi

# Permisos de ejecucion (el unzip a veces los pierde).
chmod +x launcher/StudyFlow.sh launcher/StudyFlow.command 2>/dev/null || true

if [ ! -x .venv/bin/python ]; then
    if ! command -v python3 >/dev/null 2>&1; then
        echo
        echo "  [X] No encuentro python3."
        echo
        echo "      Instala Python 3.12 desde:"
        echo "          https://www.python.org/downloads/"
        echo "      (o  brew install python3)  y vuelve a lanzar este instalador."
        echo
        if [ -t 0 ]; then read -r -p "  Pulsa Intro para cerrar... " _; fi
        exit 1
    fi
    if ! python3 -m venv .venv; then
        echo
        echo "  [X] No se pudo crear el entorno virtual."
        echo
        echo "      En Mac con Homebrew:  brew install python3"
        echo "      (si usas el Python de python.org, reinstalalo con"
        echo "       la opcion 'Add to PATH') y vuelve a lanzar este instalador."
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
