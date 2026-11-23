"""Prueba de una CAÍDA DE TELEGRAM con el orquestador.py REAL y Telegram/teléfono/pago FALSOS.
Debe terminar en "TODO OK". No toca el teléfono, no se conecta a Telegram ni paga nada.

    iphone\\.venv\\Scripts\\python.exe tests\\test_envios_pendientes.py

Escenario (el del 2026-10-26 9:26-9:48): llegan 2 órdenes con Telegram caído (no acepta mandar
nada). Debe ocurrir:
  - las 2 se pagan igual, una tras otra, y la cola NO se queda esperando a Telegram;
  - cada resultado queda guardado en envios_pendientes.json (no se pierde);
  - cuando Telegram vuelve, los 2 resultados llegan solos, como respuesta a su orden, con la
    captura y la nota "Enviado con retraso", y el archivo queda vacío;
  - ningún resultado se convierte en "Error inesperado" ni se paga nada dos veces.
Usa una sesión inventada y archivos temporales; no lo corras con el bot encendido.
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
                  USER_IDS_PERMITIDOS="1", MONTO_MAXIMO_AUTO="0", SESSION_NAME="sesion_prueba_envios_pendientes")

import control_bot  # noqa: E402
import envios_pendientes  # noqa: E402
import orquestador  # noqa: E402
from ejecutor import ResultadoPago  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="test_envios_"))
control_bot.ARCHIVO_ESTADO = tmp / "estado_bot.json"
control_bot.ARCHIVO_PARAR = tmp / "parar_bot.flag"
envios_pendientes.ARCHIVO = tmp / "envios_pendientes.json"
envios_pendientes.INTERVALO_SEG = 0.5
envios_pendientes.PRIMER_INTENTO_SEG = 1.0
CAPTURA = tmp / "captura.png"
CAPTURA.write_bytes(b"\x89PNG falsa")

eventos: list[str] = []
pagos: list[str] = []
telegram_caido = True


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

    async def __call__(self, _pedido):
        return types.SimpleNamespace(date=datetime.now(timezone.utc))

    async def start(self, **_k):
        pass

    async def run_until_disconnected(self):
        await self.desconectado.wait()

    async def disconnect(self):
        self.desconectado.set()

    async def _caida(self):
        if telegram_caido:
            await asyncio.sleep(5)            # como Telethon: insiste un rato antes de rendirse
            raise ValueError("Request was unsuccessful 6 time(s)")

    async def send_message(self, chat, texto, reply_to=None, **_k):
        await self._caida()
        self.enviados.append(("texto", reply_to, texto))

    async def send_file(self, chat, ruta, caption="", reply_to=None, **_k):
        await self._caida()
        self.enviados.append(("foto", reply_to, caption))


class EventoFalso:
    def __init__(self, n: int):
        self.message = types.SimpleNamespace(
            date=datetime.now(timezone.utc) + timedelta(seconds=1), id=100 + n,
            message=(f"00229480294878457733{n:02d}\n0000000000000000989.725\n------------------\n"
                     f"🏦 Mercantil\n🆔 2482035{n}\n🔢 CTA: 0105076261176215838{n}\n👤 PRUEBA CAIDA\n6.300,00"))
        self.sender_id = 1

    async def reply(self, texto):
        eventos.append("respuesta: " + texto)


def pago_falso(orden, _serial, _clave):
    pagos.append(orden.id_orden)
    time.sleep(0.3)
    return ResultadoPago(True, False, "ok", f"REF{orden.id_orden[-2:]}", str(CAPTURA))


orquestador.TelegramClient = ClienteFalso
orquestador.esperar_dispositivo_listo = lambda *_a, **_k: "FALSO"
orquestador.ejecutar_pago = pago_falso
orquestador.volver_inicio_tras_pago = lambda _s: eventos.append("vuelve a Inicio")


async def escenario():
    global telegram_caido
    while (control_bot.leer_estado() or {}).get("fase") != "escuchando":
        await asyncio.sleep(0.05)
    t0 = time.time()
    await ClienteFalso.handler(EventoFalso(1))
    await ClienteFalso.handler(EventoFalso(2))
    while len(pagos) < 2 or (control_bot.leer_estado() or {}).get("orden"):
        await asyncio.sleep(0.05)
    t_cola = time.time() - t0
    pendientes = envios_pendientes.cargar()
    assert len(pagos) == 2, pagos
    assert [p["id_orden"][-2:] for p in pendientes] == ["01", "02"], pendientes
    assert all(p["ruta"] == str(CAPTURA) and p["reply_to"] in (101, 102) for p in pendientes)
    assert not ClienteFalso.instancia.enviados
    print(f"1 ok  con Telegram caído: 2 pagos en {t_cola:.1f} s sin esperar a Telegram; 2 resultados guardados")

    telegram_caido = False                                        # Telegram vuelve
    t1 = time.time()
    while envios_pendientes.cargar() and time.time() - t1 < 15:
        await asyncio.sleep(0.1)
    enviados = ClienteFalso.instancia.enviados
    assert not envios_pendientes.cargar() and not envios_pendientes.ARCHIVO.exists()
    assert [(t, r) for t, r, _ in enviados] == [("foto", 101), ("foto", 102)], enviados
    assert all("✅ Pago ejecutado" in c and "Enviado con retraso" in c for _t, _r, c in enviados), enviados
    assert not any("Error inesperado" in c for _t, _r, c in enviados)
    print(f"2 ok  Telegram volvió: los 2 resultados llegaron solos en {time.time() - t1:.1f} s, con captura y aviso de retraso")
    control_bot.pedir_parada()


async def todo():
    tarea = asyncio.create_task(orquestador.main())
    try:
        await asyncio.wait_for(escenario(), 60)
        await asyncio.wait_for(tarea, 15)
    finally:
        if not tarea.done():
            tarea.cancel()

asyncio.run(todo())
assert pagos == sorted(set(pagos)) and len(pagos) == 2, pagos     # ningún pago repetido
print("3 ok  ningún pago repetido; el bot se apagó ordenado")


# 4) apagado con resultados pendientes: quedan en disco y salen al volver a encender
async def reinicio():
    aviso = envios_pendientes.nuevo("0022948029836524302336", -1, 555, "✅ Pago ejecutado. ID: 0022948029836524302336",
                                    str(CAPTURA))
    envios_pendientes.agregar(aviso)
    control_bot.limpiar()
    tarea = asyncio.create_task(orquestador.main())
    t0 = time.time()
    while envios_pendientes.cargar() and time.time() - t0 < 10:
        await asyncio.sleep(0.1)
    assert not envios_pendientes.cargar()
    assert ClienteFalso.instancia.enviados[-1][:2] == ("foto", 555)
    control_bot.pedir_parada()
    await asyncio.wait_for(tarea, 15)

orquestador.ORDENES_PROCESADAS.clear()
for s in orquestador.instancia_unica._SOCKETS:     # el proceso real lo suelta al terminar
    s.close()
orquestador.instancia_unica._SOCKETS.clear()
asyncio.run(reinicio())
print("4 ok  lo pendiente de una corrida anterior se manda solo al encender")

control_bot.limpiar()
for f in RAIZ.glob("sesion_prueba_envios_pendientes*"):
    f.unlink()
print("\nTODO OK")
