#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# scripts/build-macos-portable.sh
#
# Build a ZIP that someone can copy to a Mac and run (misma UX que Windows).
# Produces dist/StudyFlow-macos/ inside dist/StudyFlow-macos-<version>.zip
#
# Estructura identica a build-windows-portable.sh (stage → INCLUDE → strip →
# zip); lo que cambia es el lanzador y el LEEME-PRIMERO.txt, que ESTE script
# genera porque el del repo raíz habla de INICIAR_StudyFlow.bat (Windows).
#
#   Doble clic: INSTALAR_StudyFlow.command (raiz)  ->  launcher/StudyFlow.sh
#   (quita el bloqueo de Apple + prepara entorno + arranca + abre navegador)
#
# Usage:  bash scripts/build-macos-portable.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

VERSION="$(python3 -c "import re,pathlib,sys; \
p=pathlib.Path('backend/routes/health.py'); \
t=p.read_text() if p.exists() else ''; \
m=re.search(r'APP_VERSION\s*=\s*[\'\"]([^\'\"]+)', t); \
sys.exit('ERROR: APP_VERSION not found in backend/routes/health.py') if not m else print(m.group(1))")"

STAGE="dist/StudyFlow-macos"
ZIP="dist/StudyFlow-macos-${VERSION}.zip"

echo "==> Limpiando la staging previa"
rm -rf "$STAGE" "$ZIP"
mkdir -p "$STAGE"

# ── what actually ships ──────────────────────────────────────────────────────
# Igual que el Windows menos los ficheros .bat y el LEEME de Windows:
# launcher/StudyFlow.command + launcher/StudyFlow.sh viajan SIEMPRE dentro
# de launcher/ (el guard de abajo lo comprueba), y el LEEME de esta
# plataforma lo genera este script.
INCLUDE=(
  "INSTALAR_StudyFlow.command"
  "launcher"
  "backend"
  "frontend"
  "tests"
  "docker"
  "scripts"
  "requirements-local.txt"
  "requirements.txt"
  "README.md"
  "LICENSE"
  ".env.example"
  "AGENTS.md"
)

echo "==> Copiando ficheros de runtime"
for item in "${INCLUDE[@]}"; do
    [ -e "$item" ] || { echo "    - $item (no existe, se omite)"; continue; }
    cp -R "$item" "$STAGE/"
    echo "    + $item"
done

# Sin el lanzador mac no hay paquete: comprobarlo aqui, no en la maquina del
# usuario.  El INSTALAR de la raiz tambien es obligatorio: es lo primero que
# tiene que ver el usuario (y el que quita el bloqueo de Apple).
for f in "INSTALAR_StudyFlow.command" "launcher/StudyFlow.command" "launcher/StudyFlow.sh"; do
    if [ ! -f "$STAGE/$f" ]; then
        echo "    [X] Falta $f en el paquete. Abortando."
        exit 1
    fi
done

# El doble clic exige permiso +x en el INSTALAR de la raiz (el unzip a veces
# lo pierde, pero mejor asegurarlo en staging antes de comprimir).
chmod +x "$STAGE/INSTALAR_StudyFlow.command"

# ── strip what must not travel ───────────────────────────────────────────────
echo "==> Eliminando basura local"
strip() {
    local n=0
    for p in "$@"; do
        if [ -e "$STAGE/$p" ]; then rm -rf "${STAGE:?}/$p"; n=$((n+1)); fi
    done
    [ "$n" -gt 0 ] && echo "    - $*"
    return 0
}
strip "backend/core" "backend/core.*"
find "$STAGE" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$STAGE" -name '*.pyc' -delete 2>/dev/null || true
find "$STAGE" -name '.DS_Store' -delete 2>/dev/null || true
strip "backend/uploads" "backend/venv" ".venv" "node_modules" "dist"
strip "backend/novnc" "tests/fixtures_large"
find "$STAGE" -name '*.db' -delete 2>/dev/null || true
find "$STAGE" -name '*.sqlite*' -delete 2>/dev/null || true
find "$STAGE" -name 'launcher.json' -delete 2>/dev/null || true
find "$STAGE" -name 'secret.key' -delete 2>/dev/null || true

# ── LEEME-PRIMERO.txt de ESTA plataforma ─────────────────────────────────────
# El del repo raíz describe el .bat de Windows, así que no sirve aquí.
echo "==> Generando LEEME-PRIMERO.txt (macOS)"
cat > "$STAGE/LEEME-PRIMERO.txt" <<'EOF'
============================================================
   STUDYFLOW - COMO EMPEZAR (macOS)
============================================================

  Hola: sigue estos pasos y tendras StudyFlow funcionando
  en tu Mac en unos minutos. No hace falta saber de
  informatica. Todo es automatico.

------------------------------------------------------------
  PASO A PASO
------------------------------------------------------------

PASO 0) DESCOMPRIME EL ZIP COMPLETO   (muy importante)

   En Mac basta con doble clic en el ZIP: el sistema lo
   descomprime solo. Despues ENTRA en la carpeta que se ha
   creado (StudyFlow-macos).

   No lances los ficheros desde dentro del ZIP: siempre
   PRIMERO extraer, DESPUES abrir.

PASO 1) EJECUTA ESTE FICHERO (el unico que necesitas):

        INSTALAR_StudyFlow.command

   Es el de la raiz de la carpeta, el del icono de
   engranaje / flecha  (NO hace falta entrar en launcher/).

   Haz DOBLE CLIC sobre el y espera. El:

     - quita el bloqueo de Apple ("quitando bloqueo...")
     - prepara el entorno la primera vez (2-3 min)
     - arranca StudyFlow
     - abre el navegador solo

   NO CIERRES LA VENTANA hasta que veas el mensaje final.

PASO 2) LA PRIMERA VEZ TARDA 2-3 MINUTOS   (es normal)

   Se esta instalando todo. Las siguientes veces arranca
   en pocos segundos.

PASO 3) SE ABRE SOLO EL NAVEGADOR con StudyFlow dentro de:

        http://127.0.0.1:8477/

   Si el navegador no se abre solo, copia esa direccion a
   mano en la barra de direcciones. Listo: ya puedes usarlo.

PASO 4) PARA DETENER LA APP

   - Cierra la ventana de terminal de StudyFlow, o
   - Abre una terminal dentro de esta carpeta y escribe:

         bash launcher/StudyFlow.sh stop

   Para volver a arrancar: doble clic otra vez en
   INSTALAR_StudyFlow.command (o en launcher/StudyFlow.command).

------------------------------------------------------------
  IMPORTANTE: "macOS no puede verificar que no contiene
  software malicioso"  (o "no proviene de un desarrollador
  identificado")
------------------------------------------------------------

  Es el bloqueo de Apple (Gatekeeper) para lo descargado de
  internet. NO es un virus: es una advertencia automatica.
  Tienes DOS formas de quitarlo:

  SOLUCION A)  LA MAS FACIL (solo la primera vez):

     1. Clic DERECHO sobre INSTALAR_StudyFlow.command
     2. Elige "Abrir"
     3. En el aviso, pulsa "Abrir" otra vez

     Con eso macOS recuerda que confias en el y ya despues
     puedes lanzarlo con doble clic normal.

  SOLUCION B)  DESDE TERMINAL (una vez):

     1. Abre Terminal (Buscar -> "Terminal")
     2. Escribe  cd  y arrastra la carpeta StudyFlow-macos
        a la ventana; pulsa Intro
     3. Escribe exactamente:

          xattr -dr com.apple.quarantine "carpeta-descomprimida"

        (o lo mismo: arrastra la carpeta tras las comillas)
     4. Pulsa Intro y cierra Terminal
     5. Vuelve al PASO 1: doble clic en INSTALAR_StudyFlow.command

  Tras hacerlo una vez, ya no volveras a ver ese aviso.

------------------------------------------------------------
  QUE HAY EN ESTA CARPETA
------------------------------------------------------------

  INSTALAR_StudyFlow.command ->  EL QUE USAS TU. Doble clic y listo.
  LEEME-PRIMERO.txt          ->  Este fichero.
  launcher/StudyFlow.command ->  Arranque directo (una vez
                                 instalado, tambien vale).
  launcher/StudyFlow.sh      ->  El lanzador de verdad.
  launcher/                  ->  El motor de arranque. No lo toques.
  backend/  frontend/        ->  Parte tecnica de la app. No toques.
  scripts/  tests/           ->  Herramientas de desarrollo. No toques.
  docker/                    ->  Configuracion de servidores. No toques.
  README.md (si viene)       ->  Documentacion tecnica (para
                                 programadores). Tu no la necesitas.

  Tu solo necesitas INSTALAR_StudyFlow.command. El resto se
  puede mirar, pero nada de lo de dentro debe editarse.

------------------------------------------------------------
  DONDE SE GUARDAN MIS DATOS
------------------------------------------------------------

  NO en esta carpeta. Tus datos (agenda, cursos, notas...)
  se guardan aqui:

        ~/Library/Application Support/studyflow/

  Por eso puedes BORRAR esta carpeta del proyecto sin miedo:
  no pierdes nada. Tus datos siguen ahi fuera.

------------------------------------------------------------
  SI ALGO FALLA
------------------------------------------------------------

  - Sigue apareciendo el aviso de Apple: no lanzaste el
    INSTALAR como dice la seccion IMPORTANTE de arriba
    (clic derecho -> Abrir -> Abrir).

  - "No encuentro requirements-local.txt": la carpeta esta
    incompleta. Descomprime de nuevo el ZIP entero y vuelve
    al PASO 0.

  - "No encuentro python3": instala Python 3.12 desde
    https://www.python.org/downloads/  (o  brew install python3)
    y vuelve a lanzar INSTALAR_StudyFlow.command.

  - "No se pudo crear el entorno virtual": con Homebrew,
    brew install python3, y vuelve a lanzar el instalador.

  - Cualquier otro mensaje que empiece por [X]: leelo con
    calma, dice que ha pasado y que hacer.

  Eres desarrollador? La documentacion tecnica esta en
  README.md.
EOF

# ── instalador visible en la RAIZ ───────────────────────────────────────────
# Ademas de INSTALAR_StudyFlow.command (que ya viaja), se genera un alias
# install.command y una guia corta INSTALL.txt justo en la raiz del paquete.
echo "==> Generando INSTALL.txt e install.command (macOS)"
cat > "$STAGE/INSTALL.txt" <<'EOF'
============================================================
  STUDYFLOW - INSTALACION RAPIDA (macOS)
============================================================

  1. Descomprime el ZIP completo (doble clic en el .zip).

  2. Doble clic en:  install.command
     (o en INSTALAR_StudyFlow.command, es exactamente lo mismo)

     Si macOS lo bloquea: clic derecho > Abrir > Abrir.

  3. La primera vez tarda 2-3 minutos (instala dependencias).
     Se abrira solo:  http://127.0.0.1:8477/

  Para detener:  bash launcher/StudyFlow.sh stop
  Lee LEEME-PRIMERO.txt para instrucciones detalladas.
EOF
cat > "$STAGE/install.command" <<'EOF'
#!/bin/bash
# StudyFlow - instalador (raiz). Delega en INSTALAR_StudyFlow.command.
cd "$(dirname "$0")" || exit 1
exec bash "INSTALAR_StudyFlow.command" "$@"
EOF
chmod +x "$STAGE/install.command"

# ── refuse to ship a bloated package ────────────────────────────────────────
echo "==> Comprobando peso"
SIZE_MB="$(du -sm "$STAGE" | cut -f1)"
echo "    ${SIZE_MB} MB"
if [ "$SIZE_MB" -gt 400 ]; then
    echo "    [X] El paquete pesa ${SIZE_MB} MB. Se esperaba mucho menos."
    echo "        Suele indicar que se ha colado un artefacto ignorado por git."
    echo "        Revisa la lista INCLUDE de este script. Abortando."
    exit 1
fi

# ── zip ─────────────────────────────────────────────────────────────────────
echo "==> Comprimiendo"
# -r recurse; -X drop extra metadata. Los permisos +x de .sh/.command se
# conservan: es lo que permite el doble clic tras descomprimir.
( cd dist && zip -qr "$(basename "$ZIP")" "$(basename "$STAGE")" -x '*.DS_Store' )

echo
echo "    Listo: $ZIP"
echo "    ($(du -h "$ZIP" | cut -f1))"
echo
echo "    En macOS:"
echo "      1. Descomprime el ZIP completo (doble clic en el .zip)"
echo "      2. Lee LEEME-PRIMERO.txt (instrucciones para principiantes)"
echo "      3. Doble clic en INSTALAR_StudyFlow.command (raiz del paquete)"
echo "         o en install.command (alias equivalente en la raiz)"
echo "         (si macOS lo bloquea: clic derecho -> Abrir -> Abrir)"
echo "      4. La primera vez instala dependencias (2-3 min)"
echo "      5. Se abre en http://127.0.0.1:8477/"
echo "      Para detener: cierra la ventana, o  bash launcher/StudyFlow.sh stop"
echo
echo "    Para actualizar datos desde el Mac: usa el ZIP de backup desde"
echo "    dentro de la app (Ajustes > Restaurar)."
