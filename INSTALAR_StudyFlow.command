#!/bin/bash
# ---------------------------------------------------------------------------
#  INSTALAR_StudyFlow.command  (macOS)
#
#  Doble clic en ESTE fichero (el de la raiz) para:
#    1. Quitar el bloqueo de Apple (Gatekeeper / quarantine)
#    2. Preparar el entorno (venv + dependencias) si falta
#    3. Instalar el AUTOARRANQUE: StudyFlow queda como servicio del Mac
#       y arranca solo al encenderlo (sin volver a ver una Terminal)
#    4. (si el autoarranque fallara) Arrancar StudyFlow de la forma
#       clasica y abrir el navegador
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
echo "  [1/4] Quitando el bloqueo de Apple... (quitando bloqueo de Apple)"
if command -v xattr >/dev/null 2>&1; then
    xattr -dr com.apple.quarantine "$(pwd)" 2>/dev/null || true
    echo "        Listo: ya no saldra el aviso de"
    echo "        'Apple no ha podido verificar que no contiene software malicioso'."
else
    echo "        (no hay xattr, se omite)"
fi
echo

# ----- 2/3  Preparar el entorno -----
echo "  [2/4] Preparando el entorno... (solo la primera vez)"

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
chmod +x launcher/StudyFlow.sh launcher/StudyFlow.command \
    launcher/install-autostart.command launcher/uninstall-autostart.command \
    launcher/install-opencode-sidecar.command launcher/uninstall-opencode-sidecar.command \
    2>/dev/null || true

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

# ----- 3/4  Instalar el autoarranque (servicio del sistema) -----
# launcher/install-autostart.command copia el programa a una ruta estable
# (~/Applications/StudyFlow), crea alli su entorno, y lo registra con
# launchctl (RunAtLoad + KeepAlive).  La primera vez instala dependencias
# (2-3 min), igual que hacia StudyFlow.sh.
echo "  [3/4] Instalando el autoarranque (StudyFlow arrancara solo)..."
echo "        (la primera vez puede tardar 2-3 min: instala dependencias)"
echo

if bash launcher/install-autostart.command; then

    # ----- 3.5  Sidecar local de OpenZen (opencode serve) -----
    # NO critico: si falla, StudyFlow funciona igual y seguimos. En macOS,
    # sin esto nadie arranca `opencode serve` y la PWA da "Connection
    # refused" al pedir IA; este paso lo deja como servicio.
    echo "  [3.5] Instalando el sidecar local de OpenZen (opencode serve)..."
    if bash launcher/install-opencode-sidecar.command; then
        echo "        Sidecar de OpenZen listo."
    else
        echo "  [!] El sidecar de OpenZen no se pudo instalar."
        echo "      StudyFlow funciona igual; la IA local puede no responder."
        echo "      Reintenta luego: launcher/install-opencode-sidecar.command"
    fi
    echo

    # ----- 4/4  Esperar a que StudyFlow responda -----
    echo "  [4/4] Esperando a que StudyFlow responda..."
    listo=0
    intento=0
    while [ "$intento" -lt 45 ]; do
        if curl -fsS http://127.0.0.1:8477/api/health >/dev/null 2>&1; then
            listo=1
            break
        fi
        intento=$((intento + 1))
        sleep 1
    done

    echo
    echo "  ============================================================"
    echo "        ***  STUDYFLOW YA QUEDA INSTALADO  ***"
    echo "  ============================================================"
    echo
    if [ "$listo" = 1 ]; then
        echo "   StudyFlow arranca SOLO cada vez que enciendes el Mac."
        echo "   Ahora mismo ya esta funcionando."
    else
        echo "   StudyFlow queda instalado y arranca solo al encender"
        echo "   el Mac. Puede tardar unos segundos en estar listo."
    fi
    echo
    echo "   Abre el navegador en:    http://127.0.0.1:8477/"
    echo
    echo "   Ya no volveras a ver esta ventana: no hace falta Terminal."
    echo "   Para quitarlo del todo: launcher/uninstall-autostart.command"
    echo
    echo "   (Puedes cerrar esta ventana.)"
    echo
    exit 0
fi

# ----- si el autoarranque falla: arranque clasico (no dejar tirado) -----
# El servicio no se pudo montar (p. ej. launchctl bloqueado).  Arrancamos
# de la forma de siempre: esta ventana mantiene StudyFlow en marcha.
echo
echo "  [!] No se pudo instalar el autoarranque."
echo "      Arranco StudyFlow de la forma normal: MANTEN ESTA"
echo "      VENTANA ABIERTA mientras lo uses."
echo
echo "      Para intentar el autoarranque mas tarde:"
echo "          launcher/install-autostart.command"
echo
exec bash launcher/StudyFlow.sh
