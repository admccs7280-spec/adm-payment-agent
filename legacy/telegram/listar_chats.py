"""Diagnóstico de la sesión de Telegram. Solo lee: nunca manda mensajes ni
pide código de login (usa connect(), no start()).

    python listar_chats.py

Responde tres cosas:
  1. Si el API_ID/API_HASH del .env sirven en esta máquina.
  2. A qué cuenta de Telegram pertenece la sesión guardada.
  3. Los grupos/canales visibles con su CHAT_ID, para copiar el correcto
     a CHAT_ID_ORDENES sin adivinarlo.
"""
import asyncio

from telethon import TelegramClient

import instancia_unica
from config import load_settings


async def main():
    s = load_settings()
    instancia_unica.tomar(s.session_name, "listar_chats.py")   # nunca junto a un bot con la misma sesión
    client = TelegramClient(s.session_name, s.api_id, s.api_hash)
    await client.connect()  # NO start(): no pide código ni crea sesión nueva

    if not await client.is_user_authorized():
        print(f"Sesión '{s.session_name}.session': NO autorizada.")
        print("Hay que hacer el login interactivo una vez con: python orquestador.py")
        await client.disconnect()
        return

    yo = await client.get_me()
    print(f"Sesión '{s.session_name}.session' autorizada como: "
          f"{yo.first_name or ''} (@{yo.username or 'sin_usuario'}) "
          f"tel {yo.phone or '?'} user_id={yo.id}")
    print(f"CHAT_ID_ORDENES actual en .env: {s.chat_id_ordenes}\n")
    print("Grupos y canales visibles:")

    async for dialogo in client.iter_dialogs():
        if not (dialogo.is_group or dialogo.is_channel):
            continue
        marca = "  <-- el del .env" if dialogo.id == s.chat_id_ordenes else ""
        print(f"  {dialogo.id!s:>16}  {dialogo.name}{marca}")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
