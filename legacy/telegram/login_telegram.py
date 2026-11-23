"""Login unico de Telegram: crea <SESSION_NAME>.session en el directorio actual.
NO arranca el bot, no escucha chats, no manda mensajes. Solo autentica.
Ejecutar con cwd = carpeta del proyecto."""
import os
import sys

sys.path.insert(0, os.getcwd())

from telethon.sync import TelegramClient

import config  # carga .env en os.environ

api_id = int(config.requerido("API_ID"))
api_hash = config.requerido("API_HASH")
telefono = config.requerido("TELEFONO")
sesion = config.opcional("SESSION_NAME", "userbot_bdv")

with TelegramClient(sesion, api_id, api_hash) as client:
    client.start(phone=telefono)  # pide codigo (y clave de 2 pasos) por consola
    yo = client.get_me()
    print(f"\nOK. Sesion '{sesion}.session' creada para: {yo.first_name or ''} "
          f"(@{yo.username or 'sin_usuario'}) tel {yo.phone or '?'} user_id={yo.id}")
