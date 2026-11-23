"""Prueba del aviso FINAL del banco en una interbancaria (orquestador.py REAL, Telegram/teléfono
FALSOS). Debe terminar en "TODO OK". No toca el teléfono, no se conecta a Telegram ni paga nada.

    iphone\\.venv\\Scripts\\python.exe tests\\test_avisos_banco.py

Escenario del 2026-10-26 21:55: una transferencia a otro banco sale con comprobante (✅), y
segundos después el banco avisa "Transferencia interbancaria fallida". Debe ocurrir:
  1) tras el ✅ el bot vigila el aviso (solo con la cola vacía);
  2) al ver "fallida" responde EN LA ORDEN: "⚠️ TRANSFERENCIA FALLIDA según el banco";
  3) un pago que no es interbancaria no se vigila.
"""
import asyncio
import os
import sys
import tempfile
import time
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
os.chdir(RAIZ)
os.environ.update(DISPOSITIVO="iphone", API_ID="1", API_HASH="x", TELEFONO="+580000000",
                  CUENTA_DEBITAR_DESC="0102****0000", BDV_PASSWORD="x", CHAT_ID_ORDENES="-1",
                  USER_IDS_PERMITIDOS="1", MONTO_MAXIMO_AUTO="0", SESSION_NAME="sesion_prueba_avisos_banco")

import control_bot  # noqa: E402
import ejecutor_iphone as E  # noqa: E402
import envios_pendientes  # noqa: E402
import orquestador  # noqa: E402
from ejecutor import ResultadoPago  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="test_avisos_"))
control_bot.ARCHIVO_ESTADO = tmp / "estado_bot.json"
control_bot.ARCHIVO_PARAR = tmp / "parar_bot.flag"
envios_pendientes.ARCHIVO = tmp / "envios_pendientes.json"
CAPTURA = tmp / "c.png"
CAPTURA.write_bytes(b"x")
vigiladas: list = []


class ClienteFalso:
    handler = None
    instancia = None

    def __init__(self, *_a, **_k):
        ClienteFalso.instancia = self
        self.enviados: list = []
        self.desconectado = asyncio.Event()

    def on(self, _e):
        def reg(f):
            ClienteFalso.handler = f
            return f
        return reg

    async def connect(self):
        pass

    async def __call__(self, _p):
        return types.SimpleNamespace(date=datetime.now(timezone.utc))

    async def start(self, **_k):
        pass

    async def run_until_disconnected(self):
        await self.desconectado.wait()

    async def disconnect(self):
        self.desconectado.set()

    async def send_message(self, chat, texto, reply_to=None, **_k):
        self.enviados.append(("texto", reply_to, texto))

    async def send_file(self, chat, ruta, caption="", reply_to=None, **_k):
        self.enviados.append(("foto", reply_to, caption))


class EventoFalso:
    def __init__(self, n, banco, medio):
        self.message = types.SimpleNamespace(
            date=datetime.now(timezone.utc) + timedelta(seconds=1), id=100 + n,
            message=(f"00229482151039726755{n:02d}\n0000000000000000975.725\n------------------\n"
                     f"🏦 {banco}\n🆔 2482035{n}\n📱 {medio}\n👤 PRUEBA\n3.910,90"))
        self.sender_id = 1

    async def reply(self, _t):
        pass


def pago_falso(orden, _s, _c):
    r = ResultadoPago(True, False, "ok", "3701130454863", str(CAPTURA), volver_inicio_pendiente=True)
    if orden.banco != "Banco de Venezuela":
        r.interbancaria = True
    return r


def vigilar_falso(segundos, hay_mas, _serial):
    vigiladas.append((segundos, hay_mas()))
    E._AVISOS_BANCO.append({"id_orden": orquestador_ultimo[0], "mensaje_id": 101, "referencia": "3701130454863",
                            "monto": 3910.9, "texto": "Transferencia interbancaria fallida"})


orquestador_ultimo = [None]
_pago = pago_falso


def pago_y_anota(orden, s, c):
    orquestador_ultimo[0] = orden.id_orden
    return _pago(orden, s, c)


orquestador.TelegramClient = ClienteFalso
orquestador.esperar_dispositivo_listo = lambda *_a, **_k: "FALSO"
orquestador.ejecutar_pago = pago_y_anota
orquestador.volver_inicio_tras_pago = lambda _s: None
orquestador.vigilar_avisos_banco = vigilar_falso


async def escenario():
    while (control_bot.leer_estado() or {}).get("fase") != "escuchando":
        await asyncio.sleep(0.05)
    await ClienteFalso.handler(EventoFalso(1, "Venezolano de crédito", "04140000000"))   # interbancaria
    t0 = time.time()
    while not any("TRANSFERENCIA FALLIDA" in c for _t, _r, c in ClienteFalso.instancia.enviados):
        assert time.time() - t0 < 10, ClienteFalso.instancia.enviados
        await asyncio.sleep(0.1)
    enviados = ClienteFalso.instancia.enviados
    assert enviados[0][0] == "foto" and "✅ Pago ejecutado" in enviados[0][2], enviados
    aviso = next(e for e in enviados if "TRANSFERENCIA FALLIDA" in e[2])
    assert aviso[1] == 101 and "3701130454863" in aviso[2] and "sin efecto" in aviso[2], aviso
    assert vigiladas == [(20.0, False)], vigiladas
    print("1 ok  interbancaria con ✅ -> vigila el aviso -> 'fallida' se responde EN la orden")

    await ClienteFalso.handler(EventoFalso(2, "Banco de Venezuela", "04140000001"))      # no interbancaria
    while (control_bot.leer_estado() or {}).get("orden") or len(ClienteFalso.instancia.enviados) < 3:
        await asyncio.sleep(0.1)
    await asyncio.sleep(0.5)
    assert len(vigiladas) == 1, vigiladas
    print("2 ok  un pago que no es interbancaria no se vigila")
    control_bot.pedir_parada()


async def todo():
    tarea = asyncio.create_task(orquestador.main())
    try:
        await asyncio.wait_for(escenario(), 30)
        await asyncio.wait_for(tarea, 15)
    finally:
        if not tarea.done():
            tarea.cancel()

asyncio.run(todo())
control_bot.limpiar()
for f in RAIZ.glob("sesion_prueba_avisos_banco*"):
    f.unlink()
print("\nTODO OK")
