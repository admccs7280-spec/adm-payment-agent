@echo off
rem ACCESO UNICO para INICIAR y APAGAR el bot de pagos del iPhone 12.
rem   - Si el bot NO esta corriendo: lo inicia (esta ventana pasa a ser la consola del bot).
rem   - Si el bot YA esta corriendo: muestra su estado y pregunta si lo apaga. El apagado es
rem     ORDENADO: no corta ningun pago, termina lo pendiente y recien entonces se cierra.
rem Funciona desde cualquier carpeta/PC: las rutas salen de donde esta este archivo. En el
rem Escritorio pon un ACCESO DIRECTO a este archivo, NO una copia del .bat.
title Bot iPhone 12 - iniciar o apagar
cd /d "%~dp0"
set DISPOSITIVO=iphone
if not exist "iphone\.venv\Scripts\python.exe" (
  echo Falta el entorno de Python del iPhone. Sigue el paso 3 de GUIA-INSTALACION.md
  pause
  exit /b 1
)
if not exist ".env" (
  echo Falta el archivo .env con las credenciales de ESTA PC. Sigue el paso 4 de GUIA-INSTALACION.md
  pause
  exit /b 1
)
"iphone\.venv\Scripts\python.exe" control_bot.py
if %errorlevel%==20 goto iniciar
if errorlevel 1 pause
exit /b 0

:iniciar
title Bot de pagos BDV (iPhone 12)
"iphone\.venv\Scripts\python.exe" orquestador.py
if errorlevel 1 pause
exit /b 0
