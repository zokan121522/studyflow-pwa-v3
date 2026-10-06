@echo off
REM ────────────────────────────────────────────────────────────────────────────
REM  StudyFlow — launcher para Windows (doble clic)
REM
REM  Uso:
REM    StudyFlow.bat              arranca StudyFlow y abre el navegador
REM    StudyFlow.bat stop         detiene la instancia local
REM    StudyFlow.bat status       muestra el estado
REM    StudyFlow.bat setup        (re)crea el entorno e instala dependencias
REM
REM  La primera vez puede tardar: descarga Python y dependencias.
REM  A partir de ahí es un doble clic y se abre la app.
REM
REM  Los datos NO viven aquí, sino en:
REM    %LOCALAPPDATA%\studyflow\
REM  así que borrar esta carpeta no destruye nada.
REM ────────────────────────────────────────────────────────────────────────────
setlocal enabledelayedexpansion
cd /d "%~dp0.."

REM ── ¿estamos en la raíz del proyecto? ───────────────────────────────────────
REM Windows deja ejecutar un .bat directamente DENTRO de un ZIP: lo extrae a
REM una carpeta temporal y lo lanza. Ahí "%~dp0.." no existe, el cd falla en
REM silencio, y el pip acaba buscando requirements-local.txt en el sitio
REM equivocado con un error que no dice nada útil.
if not exist "requirements-local.txt" (
    echo.
    echo   [X] No encuentro requirements-local.txt.
    echo.
    echo       Estas ejecutando el lanzador desde dentro del ZIP, o el ZIP esta
    echo       incompleto.
    echo.
    echo       Haz esto:
    echo         1. Cierra esta ventana
    echo         2. Abre el ZIP y pulsa "Extraer todo" / "Extract All"
    echo         3. Entra en la carpeta StudyFlow-windows
    echo         4. Doble clic en launcher\StudyFlow.bat
    echo.
    pause
    exit /b 1
)

set "VENV=.venv"
set "VENV_PY=%VENV%\Scripts\python.exe"

echo.
echo   StudyFlow
echo   ──────────────────────────────────────────────
echo.

REM ── ¿Python disponible? ────────────────────────────────────────────────────
where py >nul 2>&1 && (set "PY=py -3") || (set "PY=python")
%PY% --version >nul 2>&1
if errorlevel 1 (
    echo   [X] No encuentro Python.
    echo.
    echo       Instala Python 3.12 desde:
    echo         https://www.python.org/downloads/
    echo.
    echo       MARCA "Add Python to PATH" durante la instalacion.
    echo.
    pause
    exit /b 1
)

REM ── ¿Entorno listo? ─────────────────────────────────────────────────────────
if "%~1"=="setup" goto setup
if not exist "%VENV_PY%" goto setup
REM ── ¿Dependencias realmente instaladas? ─────────────────────────────────────
REM Comprobar que existe python.exe NO dice nada. Un install fallido (pin en
REM conflicto, corte de red) deja el venv con SOLO pip dentro, y entonces este
REM script lo daba por bueno para siempre: cada arranque posterior moria con
REM "No module named 'flask'" y sin ningun camino de recuperacion. Hay que
REM comprobar que las dependencias se IMPORTAN, que es lo unico que importa.
REM gevent y tzdata se probean porque son las que han faltado en silencio:
REM faltaban y la app arrancaba igual, en silencio.
"%VENV_PY%" -c "import flask, psycopg2, gevent, tzdata" >nul 2>&1
if errorlevel 1 goto setup
if "%~1"=="" goto run
if "%~1"=="stop"   goto run
if "%~1"=="status" goto run

:setup
echo   Preparando el entorno ^(solo la primera vez, puede tardar 2-3 min^)..
echo.
%PY% -m venv "%VENV%"
if errorlevel 1 (
    echo.
    echo   [X] No se pudo crear el entorno virtual.
    pause
    exit /b 1
)
echo   Instalando dependencias..
echo.
"%VENV_PY%" -m pip install --upgrade pip --quiet
"%VENV_PY%" -m pip install -r requirements-local.txt
if errorlevel 1 (
    echo.
    echo   [X] La instalacion fallo. Copia el error de arriba.
    echo.
    echo   Lo mas comun: un pin en conflicto con otro paquete del fichero.
    echo   requirements-local.txt es la lista; nada mas.
    echo.
    pause
    exit /b 1
)
echo.
echo   Listo. Las siguientes ejecuciones solo arrancan.
echo.
if "%~1"=="setup" (
    pause
    exit /b 0
)
if "%~1"=="" goto run

:run
echo   Arrancando StudyFlow..
echo.
"%VENV_PY%" launcher\launch.py %*
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" (
    echo.
    echo   [X] No arranco. Log en: %%LOCALAPPDATA%%\studyflow\launcher.log
    pause
)
endlocal & exit /b %CODE%
