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
