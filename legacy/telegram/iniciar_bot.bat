@echo off
rem Arranca el bot de pagos BDV con el emulador Android (BlueStacks + ADB). Alternativa al iPhone.
rem Doble clic para iniciar; Ctrl+C para detener. Rutas relativas a este archivo.
rem Nunca lo abras a la vez que iniciar_bot_iphone.bat: con la misma sesion de Telegram
rem solo puede haber UN bot.
title Bot de pagos BDV (Android)
cd /d "%~dp0"
set DISPOSITIVO=android
if not exist ".env" (
  echo Falta el archivo .env con las credenciales de ESTA PC. Sigue GUIA-INSTALACION.md
  pause
  exit /b 1
)
python orquestador.py
if errorlevel 1 pause
