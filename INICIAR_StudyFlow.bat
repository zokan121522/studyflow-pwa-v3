@echo off
REM ---------------------------------------------------------------------------
REM  StudyFlow - punto de entrada para el usuario final.
REM
REM  Doble clic en este fichero y nada mas. Toda la logica vive en
REM  launcher\StudyFlow.bat; aqui solo se comprueba que el ZIP se extrajo
REM  bien y se le pasa el testigo (junto con los argumentos %*).
REM
REM  Datos: %LOCALAPPDATA%\studyflow\  (borrar esta carpeta no pierde nada)
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

echo.
echo   ==================================================
echo      STUDYFLOW  -  arrancando...
echo   ==================================================
echo.

REM Windows deja abrir un .bat DENTRO del ZIP: lo extrae a una carpeta
REM temporal con SOLO este fichero, sin el resto del paquete. En ese caso
REM no hay carpeta launcher\ al lado y hay que avisar antes de seguir.
if not exist "launcher\StudyFlow.bat" goto sin_lanzador
if not exist "requirements-local.txt" goto sin_paquete

call "launcher\StudyFlow.bat" %*
exit /b %ERRORLEVEL%

:sin_lanzador
echo   [X] No encuentro la carpeta launcher\.
echo.
echo       Casi seguro estas ejecutando esto DENTRO del ZIP, o la
echo       extraccion esta incompleta.
echo.
echo       Haz esto:
echo         1. Cierra esta ventana
echo         2. Click derecho en el ZIP y elige "Extraer todo" (Extract All)
echo         3. Entra en la carpeta que se ha creado
echo         4. Doble clic en INICIAR_StudyFlow.bat
echo.
pause
exit /b 1

:sin_paquete
echo   [X] No encuentro requirements-local.txt.
echo.
echo       El paquete esta incompleto o te saltaste el paso 1
echo       (extraer el ZIP completo).
echo.
echo       Haz esto:
echo         1. Cierra esta ventana
echo         2. Abre el ZIP y pulsa "Extraer todo" (Extract All)
echo         3. Entra en la carpeta StudyFlow-windows
echo         4. Doble clic en INICIAR_StudyFlow.bat
echo.
pause
exit /b 1
