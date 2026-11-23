"""Prueba del ARRANQUE con Telegram fallando (orquestador.py REAL, Telegram/teléfono FALSOS). Debe
terminar en "TODO OK". No toca el teléfono, no se conecta a Telegram ni paga nada.

    iphone\\.venv\\Scripts\\python.exe tests\\test_conexion_telegram.py

El 2026-10-26 el centro de datos DC1 de Telegram respondía a ratos "RPCError -500: No workers
running" y el bot se CERRABA al arrancar. Debe ocurrir:
  1) con 2 fallos seguidos al conectar, el bot reintenta y queda escuchando al tercero;
  2) mientras no conecta sigue en fase "iniciando" (no acepta órdenes);
  3) si se pide apagar mientras espera a Telegram, se cierra limpio, sin conectar.
"""
import asyncio
import os
import sys
import tempfile
import types
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
os.chdir(RAIZ)
os.environ.update(DISPOSITIVO="iphone", API_ID="1", API_HASH="x", TELEFONO="+58000000000",
                  CUENTA_DEBITAR_DESC="0102****0000", BDV_PASSWORD="x", CHAT_ID_ORDENES="-1",
                  USER_IDS_PERMITIDOS="1", MONTO_MAXIMO_AUTO="0", SESSION_NAME="sesion_prueba_conexion")

from telethon import errors  # noqa: E402

import control_bot  # noqa: E402
import envios_pendientes  # noqa: E402
import orquestador  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="test_conexion_"))
control_bot.ARCHIVO_ESTADO = tmp / "estado_bot.json"
control_bot.ARCHIVO_PARAR = tmp / "parar_bot.flag"
envios_pendientes.ARCHIVO = tmp / "envios_pendientes.json"
orquestador.ESPERA_TELEGRAM_SEG = (0.3, 0.3)
orquestador.esperar_dispositivo_listo = lambda *_a, **_k: "FALSO"
FALLOS = {"pendientes": 2, "intentos": 0}


class ClienteFalso:
    def __init__(self, *_a, **_k):
        self.desconectado = asyncio.Event()
        self.conectado = False

    def on(self, _evento):
        return lambda f: f

    async def connect(self):
        FALLOS["intentos"] += 1
        if FALLOS["pendientes"] != 0:
            FALLOS["pendientes"] -= 1
            raise errors.ServerError(None, "No workers running", -500)
        self.conectado = True

    async def __call__(self, _pedido):
        return types.SimpleNamespace(date=datetime.now(timezone.utc))

    async def start(self, **_k):
        assert self.conectado

    async def run_until_disconnected(self):
        await self.desconectado.wait()

    async def disconnect(self):
        self.desconectado.set()


orquestador.TelegramClient = ClienteFalso


def soltar_candado():
    for s in orquestador.instancia_unica._SOCKETS:
        s.close()
    orquestador.instancia_unica._SOCKETS.clear()


async def caso_reintenta():
    tarea = asyncio.create_task(orquestador.main())
    vio_iniciando = False
    while (control_bot.leer_estado() or {}).get("fase") != "escuchando":
        if FALLOS["intentos"] >= 1 and (control_bot.leer_estado() or {}).get("fase") == "iniciando":
            vio_iniciando = True
        assert not tarea.done(), "el bot se cerró en vez de reintentar"
        await asyncio.sleep(0.05)
    assert FALLOS["intentos"] == 3 and vio_iniciando, FALLOS
    control_bot.pedir_parada()
    await asyncio.wait_for(tarea, 10)

asyncio.run(asyncio.wait_for(caso_reintenta(), 30))
print("1 ok  2 fallos de Telegram al conectar -> reintenta y queda escuchando al 3.er intento")

soltar_candado()
control_bot.limpiar()
FALLOS.update(pendientes=-1, intentos=0)        # Telegram caído todo el tiempo


async def caso_apagar_esperando():
    tarea = asyncio.create_task(orquestador.main())
    while FALLOS["intentos"] < 2:
        await asyncio.sleep(0.05)
    assert (control_bot.leer_estado() or {}).get("fase") == "iniciando"
    control_bot.pedir_parada()
    await asyncio.wait_for(tarea, 5)             # se cierra solo, sin quedarse colgado

asyncio.run(asyncio.wait_for(caso_apagar_esperando(), 30))
print("2 ok  apagado pedido mientras espera a Telegram -> se cierra limpio")

soltar_candado()
control_bot.limpiar()
for f in RAIZ.glob("sesion_prueba_conexion*"):
    f.unlink()
print("\nTODO OK")
