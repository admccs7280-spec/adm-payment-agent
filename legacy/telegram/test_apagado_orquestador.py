"""Prueba del APAGADO ORDENADO con el orquestador.py REAL y Telegram/teléfono/pago FALSOS.
Debe terminar en "TODO OK". No toca el teléfono, no se conecta a Telegram ni paga nada.

    iphone\\.venv\\Scripts\\python.exe test_apagado_orquestador.py

Escenario: llegan 2 órdenes (una se está "pagando", la otra espera en cola), se pide el
apagado (como hace el acceso directo) y después llega una 3.ª. Debe ocurrir:
  - las 2 primeras terminan, en orden, y cada una manda su resultado a Telegram;
  - la 3.ª NO se ejecuta y recibe la respuesta "se está apagando";
  - recién después de la 2.ª (y de volver a Inicio) el bot se desconecta y termina solo.

Usa el estado/pedido reales de esta carpeta: no lo corras con el bot encendido.
"""
import asyncio
import os
import sys
import time
import types
from datetime import datetime, timedelta, timezone

os.environ["DISPOSITIVO"] = "iphone"

import control_bot  # noqa: E402
import instancia_unica  # noqa: E402
import orquestador  # noqa: E402
from config import load_settings  # noqa: E402
from ejecutor import ResultadoPago  # noqa: E402
from ordenes import parsear_orden  # noqa: E402

if instancia_unica.en_uso(load_settings().session_name):
    sys.exit("Hay un bot (u otro programa) usando la sesión: ciérralo antes de correr esta prueba.")

# Reloj de la PC 2 h ADELANTADO respecto de Telegram (zona horaria mal puesta, visto el
# 2026-10-25): las órdenes llevan la hora del servidor y aun así deben aceptarse.
DESFASE_RELOJ_PC = timedelta(hours=2)
SEG_POR_PAGO = 3.0
SEG_VOLVER_INICIO = 1.0
settings = load_settings()
REMITENTE = next(iter(settings.usuarios_permitidos), 1)


def texto_orden(n: int) -> str:
    return (f"00229385768741603041{n:02d}\n0000000000000000932.725\n------------------\n"
            f"🏦 Banco de Venezuela\n🆔 29610348\n📱 04120549029\n👤 PRUEBA APAGADO\n50,00")


IDS = [parsear_orden(texto_orden(n), REMITENTE, 100 + n).id_orden for n in (1, 2, 3)]
eventos: list = []          # (segundos, qué)
t0 = time.time()


def anotar(que: str) -> None:
    eventos.append((time.time() - t0, que))


class ClienteFalso:
    handler = None
    instancia = None

    def __init__(self, *_a, **_k):
        ClienteFalso.instancia = self
        self.enviados: list = []
        self.desconectado = asyncio.Event()

    def on(self, _evento):
        def registrar(f):
            ClienteFalso.handler = f
            return f
        return registrar

    async def connect(self):
        pass

    async def __call__(self, _pedido):          # help.getConfig: hora del servidor
        return types.SimpleNamespace(date=datetime.now(timezone.utc) - DESFASE_RELOJ_PC)

    async def start(self, **_k):
        pass

    async def run_until_disconnected(self):
        await self.desconectado.wait()

    async def disconnect(self):
        anotar("desconectado")
        self.desconectado.set()

    async def send_message(self, _chat, texto, **_k):
        self.enviados.append(texto)
        anotar("telegram: " + texto.splitlines()[0])

    async def send_file(self, _chat, _ruta, caption="", **_k):
        self.enviados.append(caption)
        anotar("telegram: " + caption.splitlines()[0])


class EventoFalso:
    def __init__(self, n: int):
        self.message = types.SimpleNamespace(
            date=datetime.now(timezone.utc) - DESFASE_RELOJ_PC + timedelta(seconds=1),
            message=texto_orden(n), id=100 + n)
        self.sender_id = REMITENTE
        self.respuestas: list = []

    async def reply(self, texto):
        self.respuestas.append(texto)
        anotar("respuesta en el chat: " + texto.splitlines()[0])


def pago_falso(orden, _serial, _clave):
    anotar(f"pago INICIA {orden.id_orden[-2:]}")
    time.sleep(SEG_POR_PAGO)
    anotar(f"pago TERMINA {orden.id_orden[-2:]}")
    return ResultadoPago(True, False, "ok", "REF", "")


def volver_inicio_falso(_serial):
    time.sleep(SEG_VOLVER_INICIO)
    anotar("vuelve a Inicio")


orquestador.TelegramClient = ClienteFalso
orquestador.esperar_dispositivo_listo = lambda *_a, **_k: "FALSO"
orquestador.ejecutar_pago = pago_falso
orquestador.volver_inicio_tras_pago = volver_inicio_falso


async def escenario():
    while not (control_bot.leer_estado() or {}).get("fase") == "escuchando":
        await asyncio.sleep(0.05)
    ev1, ev2, ev3 = EventoFalso(1), EventoFalso(2), EventoFalso(3)
    await ClienteFalso.handler(ev1)
    await ClienteFalso.handler(ev2)
    await asyncio.sleep(1.0)
    est = control_bot.leer_estado()
    assert est["orden"] == IDS[0] and est["en_cola"] == 1, est      # 1 pagándose, 1 en cola
    anotar("SE PIDE EL APAGADO")
    control_bot.pedir_parada()
    await asyncio.sleep(1.8)                                        # el vigía ya lo notó
    assert control_bot.leer_estado()["fase"] == "apagando"
    await ClienteFalso.handler(ev3)                                 # llega con el bot apagándose
    return ev3


async def todo():
    tarea_bot = asyncio.create_task(orquestador.main())
    try:
        ev3 = await asyncio.wait_for(escenario(), 20)
        await asyncio.wait_for(tarea_bot, 30)                       # debe cerrarse SOLO
    finally:
        if not tarea_bot.done():
            tarea_bot.cancel()
    return ev3


ev3 = asyncio.run(todo())

for seg, que in eventos:
    print(f"  {seg:5.1f} s  {que}")

orden_eventos = [q for _s, q in eventos]
ids = [i[-2:] for i in IDS]


def pos(texto):
    return next(i for i, q in enumerate(orden_eventos) if q == texto)


# las dos primeras se ejecutaron completas y en orden
assert pos(f"pago INICIA {ids[0]}") < pos(f"pago TERMINA {ids[0]}") < pos(f"pago INICIA {ids[1]}") \
    < pos(f"pago TERMINA {ids[1]}"), orden_eventos
# la 3.ª no se ejecutó, recibió la respuesta y no quedó marcada como procesada
assert not any(q.endswith(ids[2]) and q.startswith("pago") for q in orden_eventos), orden_eventos
assert len(ev3.respuestas) == 1 and "apagando" in ev3.respuestas[0], ev3.respuestas
assert IDS[2] not in orquestador.ORDENES_PROCESADAS
# cada orden aceptada mandó su resultado a Telegram
assert sum(1 for e in ClienteFalso.instancia.enviados if "✅ Pago ejecutado" in e) == 2
# el bot se desconectó DESPUÉS de la 2.ª orden y de volver a Inicio
ult_inicio = max(i for i, q in enumerate(orden_eventos) if q == "vuelve a Inicio")
assert pos("desconectado") > ult_inicio > pos(f"pago TERMINA {ids[1]}"), orden_eventos

control_bot.limpiar()   # (el bot real lo hace solo al salir del proceso, con atexit)
print("\nTODO OK")
