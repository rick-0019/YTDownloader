@echo off
REM Script para actualizar yt-dlp usando pip
echo ===========================================
echo INICIANDO ACTUALIZACION DE YT-DLP
echo ===========================================

REM Cambia al directorio del script para evitar problemas de ruta
cd /d "%~dp0"

REM Ejecuta la actualizacion usando pip
python -m pip install --upgrade yt-dlp

echo.
echo ===========================================
echo PROCESO FINALIZADO
echo ===========================================
echo Presiona cualquier tecla para cerrar la ventana...
pause > nul