@echo off
rem Arranca el bot de pagos BDV con el iPhone 12 (iOS 27+) conectado por cable.
rem Doble clic para iniciar; Ctrl+C para detener. Funciona desde cualquier carpeta/PC:
rem las rutas salen de donde esta este archivo. En el Escritorio pon un ACCESO DIRECTO a
rem este archivo (clic derecho > Enviar a > Escritorio), NO una copia del .bat.
rem Nunca lo abras dos veces ni a la vez que iniciar_bot.bat: con la misma sesion de
rem Telegram solo puede haber UN bot (el segundo avisa y se cierra).
title Bot de pagos BDV (iPhone 12)
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
"iphone\.venv\Scripts\python.exe" orquestador.py
if errorlevel 1 pause
