@echo off
rem Abre el PANEL del bot del iPhone 12 (sin consola). Pide la clave del panel.
rem En el Escritorio usa el acceso directo "Panel Bot iPhone 12" (o un acceso a este archivo).
cd /d "%~dp0"
if not exist "iphone\.venv\Scripts\pythonw.exe" (
  echo Falta el entorno de Python del iPhone. Sigue el paso 3 de GUIA-INSTALACION.md
  pause
  exit /b 1
)
start "" "iphone\.venv\Scripts\pythonw.exe" panel_bot.pyw
