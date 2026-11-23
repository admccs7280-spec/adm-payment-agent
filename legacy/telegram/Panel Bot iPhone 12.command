#!/bin/bash
# Abre el PANEL del bot del iPhone 12 (pide la clave del panel). Doble clic.
cd "$(dirname "$0")" || exit 1
if [ ! -x iphone/.venv/bin/python ]; then
  echo "Falta el entorno de Python del iPhone. Sigue el paso 3 de LEEME-MAC.md"; read -r -p "Enter para cerrar"; exit 1
fi
nohup iphone/.venv/bin/python panel_bot.pyw >/dev/null 2>&1 &
disown
