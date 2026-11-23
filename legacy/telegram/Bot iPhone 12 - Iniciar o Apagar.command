#!/bin/bash
# ACCESO ÚNICO para INICIAR y APAGAR el bot del iPhone 12 (equivale a bot_iphone.bat).
#  - Si el bot NO corre: lo inicia (esta ventana pasa a ser su consola).
#  - Si YA corre: muestra su estado y pregunta si lo apaga (apagado ordenado).
cd "$(dirname "$0")" || exit 1
export DISPOSITIVO=iphone
if [ ! -x iphone/.venv/bin/python ]; then
  echo "Falta el entorno de Python del iPhone. Sigue el paso 3 de LEEME-MAC.md"; read -r -p "Enter para cerrar"; exit 1
fi
if [ ! -f .env ]; then
  echo "Falta el archivo .env con las credenciales. Sigue el paso 4 de LEEME-MAC.md"; read -r -p "Enter para cerrar"; exit 1
fi
iphone/.venv/bin/python control_bot.py
codigo=$?
if [ $codigo -eq 20 ]; then
  iphone/.venv/bin/python orquestador.py
  codigo=$?
fi
[ $codigo -ne 0 ] && read -r -p "Enter para cerrar"
exit 0
