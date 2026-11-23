@echo off
rem INSTALADOR del bot de pagos del iPhone 12 para una PC NUEVA (pasos 3 y 4 de GUIA-INSTALACION.md).
rem Doble clic. Se puede repetir sin peligro: no borra nada que ya exista.
rem   1. Crea el entorno de Python (iphone\.venv) e instala las dependencias.
rem   2. Crea el .env desde .env.example (si no existe) y lo abre en el Bloc de notas.
rem   3. Pone en el Escritorio el acceso "Bot iPhone 12 - Iniciar o Apagar".
title Instalar bot iPhone 12
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo No se encontro Python. Instala Python 3.12 de https://www.python.org/downloads/
  echo marcando "Add python.exe to PATH", y vuelve a abrir este instalador.
  pause
  exit /b 1
)

if not exist "iphone\.venv\Scripts\python.exe" (
  echo [1/3] Creando el entorno de Python en iphone\.venv ...
  python -m venv iphone\.venv
  if errorlevel 1 goto error
) else (
  echo [1/3] El entorno de Python ya existe.
)
echo       Instalando dependencias (puede tardar unos minutos)...
"iphone\.venv\Scripts\python.exe" -m pip install --upgrade pip
"iphone\.venv\Scripts\python.exe" -m pip install -r iphone\requirements.txt
if errorlevel 1 goto error

if not exist ".env" (
  echo [2/3] Creando .env desde .env.example. Llena los valores de ESTA PC y guarda.
  copy ".env.example" ".env" >nul
  start "" notepad ".env"
) else (
  echo [2/3] Ya existe un .env: no se toca.
)

if not exist "iphones.json" if exist "iphones.plantilla.json" (
  echo       Cargando la lista de telefonos registrados (sin claves) en iphones.json...
  copy "iphones.plantilla.json" "iphones.json" >nul
)

echo [3/3] Creando los accesos directos en el Escritorio (bot y panel)...
powershell -NoProfile -Command "$ws = New-Object -ComObject WScript.Shell; $l = $ws.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\Bot iPhone 12 - Iniciar o Apagar.lnk'); $l.TargetPath = '%~dp0bot_iphone.bat'; $l.WorkingDirectory = '%~dp0'; $l.Save(); $p = $ws.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\Panel Bot iPhone 12.lnk'); $p.TargetPath = '%~dp0iphone\.venv\Scripts\pythonw.exe'; $p.Arguments = '\"%~dp0panel_bot.pyw\"'; $p.WorkingDirectory = '%~dp0'; $p.IconLocation = '%~dp0recursos\panel.ico,0'; $p.Save()"

echo.
echo Listo. Sigue con el paso 5 de LEEME-PRIMERO.txt (sesion de Telegram e ID del grupo).
pause
exit /b 0

:error
echo.
echo Hubo un error. Revisa el mensaje de arriba y la seccion "Si algo falla" de GUIA-INSTALACION.md
pause
exit /b 1
