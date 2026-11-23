#!/bin/bash
# Arranca el bot directo (Ctrl+C lo detiene). Nunca a la vez que otro bot con la misma sesión de Telegram.
cd "$(dirname "$0")" || exit 1
export DISPOSITIVO=iphone
if [ ! -x iphone/.venv/bin/python ] || [ ! -f .env ]; then
  echo "Falta el entorno de Python o el .env. Sigue LEEME-MAC.md"; read -r -p "Enter para cerrar"; exit 1
fi
iphone/.venv/bin/python orquestador.py
[ $? -ne 0 ] && read -r -p "Enter para cerrar"
