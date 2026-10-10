#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# scripts/build-linux-portable.sh
#
# Build a ZIP that someone can copy to a Linux machine and run (misma UX
# que Windows). Produces dist/StudyFlow-linux/ inside
# dist/StudyFlow-linux-<version>.zip
#
# Estructura identica a build-windows-portable.sh (stage → INCLUDE → strip →
# zip); lo que cambia es el lanzador (launcher/StudyFlow.sh, en terminal) y
# el LEEME-PRIMERO.txt, que ESTE script genera porque el del repo raíz habla
# de INICIAR_StudyFlow.bat (Windows).
#
# Usage:  bash scripts/build-linux-portable.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

VERSION="$(python3 -c "import re,pathlib,sys; \
p=pathlib.Path('backend/routes/health.py'); \
t=p.read_text() if p.exists() else ''; \
m=re.search(r'APP_VERSION\s*=\s*[\'\"]([^\'\"]+)', t); \
sys.exit('ERROR: APP_VERSION not found in backend/routes/health.py') if not m else print(m.group(1))")"

STAGE="dist/StudyFlow-linux"
ZIP="dist/StudyFlow-linux-${VERSION}.zip"

echo "==> Limpiando la staging previa"
rm -rf "$STAGE" "$ZIP"
mkdir -p "$STAGE"

# ── what actually ships ──────────────────────────────────────────────────────
# Igual que el Windows menos los ficheros .bat y el LEEME de Windows:
# el lanzador de Linux es launcher/StudyFlow.sh, que viaja dentro de
# launcher/ (el guard de abajo lo comprueba).  StudyFlow.command se incluye
# tambien porque launcher/ viaja entero: es inofensivo y deja el paquete
# mac y el linux con el mismo contenido de launcher/.
INCLUDE=(
  "INSTALAR_StudyFlow.sh"
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

# Sin el lanzador Linux no hay paquete: comprobarlo aqui, no en la maquina
# del usuario.
if [ ! -f "$STAGE/launcher/StudyFlow.sh" ]; then
    echo "    [X] Falta launcher/StudyFlow.sh en el paquete. Abortando."
    exit 1
fi
# El INSTALAR de la raiz tambien es obligatorio: es lo primero que tiene
# que ver el usuario.
if [ ! -f "$STAGE/INSTALAR_StudyFlow.sh" ]; then
    echo "    [X] Falta INSTALAR_StudyFlow.sh en el paquete. Abortando."
    exit 1
fi
chmod +x "$STAGE/INSTALAR_StudyFlow.sh"

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
echo "==> Generando LEEME-PRIMERO.txt (Linux)"
cat > "$STAGE/LEEME-PRIMERO.txt" <<'EOF'
============================================================
   STUDYFLOW — CÓMO EMPEZAR (Linux)
============================================================

  ¡Hola! Sigue estos 5 pasos y tendrás StudyFlow funcionando
  en tu PC en unos minutos. No hace falta saber de informática.

------------------------------------------------------------
  PASO A PASO
------------------------------------------------------------

1) DESCOMPRIME EL ZIP COMPLETO   (muy importante)

   - Doble clic en el ZIP con el gestor de ficheros, o
   - En una terminal:   unzip StudyFlow-linux-*.zip

   Después ENTRA en la carpeta que se ha creado
   (StudyFlow-linux). No lances los ficheros desde dentro
   del ZIP: siempre PRIMERO extraer, DESPUÉS abrir.

2) ABRE UNA TERMINAL DENTRO DE LA CARPETA y escribe:

        bash INSTALAR_StudyFlow.sh

   Es el fichero de la RAIZ de la carpeta (el unico que
   necesitas). El comprueba el paquete, prepara el entorno
   la primera vez, arranca StudyFlow y abre el navegador
   el solo.

   (Una vez instalado, para arrancar despues tambien vale:
    bash launcher/StudyFlow.sh)

3) LA PRIMERA VEZ TARDA 2-3 MINUTOS   (es normal)

   Se está preparando todo: aparecerán mensajes en la
   terminal. NO CIERRES LA TERMINAL, espera a que termine.
   Las siguientes veces arranca en pocos segundos.

4) SE ABRE SOLO EL NAVEGADOR con StudyFlow dentro de:

        http://127.0.0.1:8477/

   Si el navegador no se abre solo, copia esa dirección a
   mano en la barra de direcciones. Listo: ya puedes usarlo.

5) PARA DETENER LA APP

   - Cierra la terminal de StudyFlow, o
   - En otra terminal, dentro de esta carpeta, escribe:

         bash launcher/StudyFlow.sh stop

   Para volver a arrancar:  bash INSTALAR_StudyFlow.sh

------------------------------------------------------------
  ¿QUÉ HAY EN ESTA CARPETA?
------------------------------------------------------------

  INSTALAR_StudyFlow.sh   ->  EL QUE USAS TU. Un comando y listo.
  launcher/StudyFlow.sh   ->  El lanzador de verdad. No lo toques.
  LEEME-PRIMERO.txt       ->  Este fichero.
  launcher/               ->  El motor de arranque. No lo toques.
  backend/  frontend/     ->  Parte técnica de la app. No toques.
  scripts/  tests/        ->  Herramientas de desarrollo. No toques.
  docker/                 ->  Configuración de servidores. No toques.
  README.md (si viene)    ->  Documentación técnica (para
                              programadores). Tú no lo necesitas.

  Tú solo necesitas  bash INSTALAR_StudyFlow.sh.  El resto se puede
  mirar, pero nada de lo de dentro debe editarse.

------------------------------------------------------------
  ¿DÓNDE SE GUARDAN MIS DATOS?
------------------------------------------------------------

  NO en esta carpeta. Tus datos (agenda, cursos, notas...)
  se guardan aquí:

        ~/.local/share/studyflow/

  (si tienes definida la variable XDG_DATA_HOME, en
   $XDG_DATA_HOME/studyflow).

  Por eso puedes BORRAR esta carpeta del proyecto sin miedo:
  no pierdes nada. Tus datos siguen ahí fuera.

------------------------------------------------------------
  SI ALGO FALLA
------------------------------------------------------------

  - "No encuentro requirements-local.txt": la carpeta está
    incompleta. Descomprime de nuevo el ZIP entero y vuelve
    al paso 2.

  - "No encuentro python3": instala Python 3.12, por ejemplo
    en Debian/Ubuntu:

        sudo apt install python3 python3-venv

    y vuelve al paso 2.

  - "No se pudo crear el entorno virtual": te falta el módulo
    venv.  sudo apt install python3-venv

  - Cualquier otro mensaje que empiece por [X]: léelo con
    calma, dice qué ha pasado y qué hacer.

  ¿Eres desarrollador? La documentación técnica está en
  README.md.
EOF

# ── instalador visible en la RAIZ ───────────────────────────────────────────
# Ademas de INSTALAR_StudyFlow.sh (que ya viaja), se genera un alias
# install.sh y una guia corta INSTALL.txt justo en la raiz del paquete.
echo "==> Generando INSTALL.txt e install.sh (Linux)"
cat > "$STAGE/INSTALL.txt" <<'EOF'
============================================================
  STUDYFLOW - INSTALACION RAPIDA (Linux)
============================================================

  1. Descomprime el ZIP completo:
         unzip StudyFlow-linux-*.zip

  2. En una terminal dentro de la carpeta:
         bash install.sh
     (o bash INSTALAR_StudyFlow.sh, es exactamente lo mismo)

  3. La primera vez tarda 2-3 minutos (instala dependencias).
     Se abrira solo:  http://127.0.0.1:8477/

  Para detener:  bash launcher/StudyFlow.sh stop
  Lee LEEME-PRIMERO.txt para instrucciones detalladas.
EOF
cat > "$STAGE/install.sh" <<'EOF'
#!/usr/bin/env bash
# StudyFlow - instalador (raiz). Delega en INSTALAR_StudyFlow.sh.
cd "$(dirname "$0")" || exit 1
exec bash "INSTALAR_StudyFlow.sh" "$@"
EOF
chmod +x "$STAGE/install.sh"

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
# -r recurse; -X drop extra metadata. Los permisos +x de .sh se conservan.
( cd dist && zip -qr "$(basename "$ZIP")" "$(basename "$STAGE")" -x '*.DS_Store' )

echo
echo "    Listo: $ZIP"
echo "    ($(du -h "$ZIP" | cut -f1))"
echo
echo "    En Linux:"
echo "      1. Descomprime el ZIP completo (unzip StudyFlow-linux-*.zip)"
echo "      2. Lee LEEME-PRIMERO.txt (instrucciones para principiantes)"
echo "      3. bash INSTALAR_StudyFlow.sh   (raiz del paquete)"
echo "         o bash install.sh (alias equivalente en la raiz)"
echo "      4. La primera vez instala dependencias (2-3 min)"
echo "      5. Se abre en http://127.0.0.1:8477/"
echo "      Para detener: cierra la terminal, o  bash launcher/StudyFlow.sh stop"
echo
echo "    Para actualizar datos desde el Mac: usa el ZIP de backup desde"
echo "    dentro de la app (Ajustes > Restaurar)."
