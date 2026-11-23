"""Control del bot desde fuera: iniciar y apagar con UN solo acceso directo (bot_iphone.bat).

    iphone\\.venv\\Scripts\\python.exe control_bot.py

- Si NO hay un bot usando la sesión de Telegram: sale con código 20 y el .bat lo arranca.
- Si hay uno: muestra su estado (libre u ocupado con una orden) y pregunta si se apaga.

Apagado ORDENADO: este programa crea `parar_bot.flag`. El bot (orquestador.py) deja de
aceptar órdenes nuevas (contesta que se está apagando), termina la orden que está pagando
y las que ya tenía en cola, vuelve el teléfono a Inicio y recién entonces se desconecta y
cierra. Nunca se mata el proceso: cortarlo a media orden es justo lo que hay que evitar
(después de escribir la clave el resultado quedaría INCIERTO).

El bot publica su estado en `estado_bot.json` (fase, orden en curso, órdenes en cola).
Esa información es solo para mostrar; la verdad de "hay un bot corriendo" es el puerto de
`instancia_unica.py`, que el sistema libera solo aunque el bot se cierre a la fuerza.
"""
import json
import os
import sys
import time
from pathlib import Path

import instancia_unica
from config import opcional

RAIZ = Path(__file__).resolve().parent
ARCHIVO_ESTADO = RAIZ / "estado_bot.json"
ARCHIVO_PARAR = RAIZ / "parar_bot.flag"
SALIR_INICIAR = 20          # código de salida: no hay bot, el .bat debe arrancarlo
ESPERA_APAGADO_SEG = 120    # cuánto espera este programa viendo cómo se apaga el bot


def _quitar(ruta: Path) -> None:
    try:
        ruta.unlink()
    except OSError:
        pass


def limpiar() -> None:
    """Borra el estado y el pedido de apagado (restos de un bot que ya no corre)."""
    _quitar(ARCHIVO_ESTADO)
    _quitar(ARCHIVO_PARAR)


def parada_pedida() -> bool:
    return ARCHIVO_PARAR.exists()


def pedir_parada() -> None:
    ARCHIVO_PARAR.write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")


def cancelar_parada() -> None:
    _quitar(ARCHIVO_PARAR)


def leer_estado():
    try:
        return json.loads(ARCHIVO_ESTADO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class EstadoBot:
    """Lo que el bot publica de sí mismo para que el acceso directo sepa qué mostrar."""

    def __init__(self):
        self.fase = "iniciando"     # iniciando -> escuchando -> apagando
        self.orden = None           # ID de la orden que se está pagando
        self.desde = None           # time.time() en que empezó esa orden
        self.en_cola = 0            # órdenes aceptadas que aún no empiezan
        self.iphone = None          # el iPhone con el que opera: "nombre, iOS x, UDID y"

    @property
    def libre(self) -> bool:
        return self.orden is None and self.en_cola == 0

    def publicar(self) -> None:
        datos = {"pid": os.getpid(), "fase": self.fase, "orden": self.orden,
                 "desde": self.desde, "en_cola": self.en_cola, "iphone": self.iphone}
        tmp = ARCHIVO_ESTADO.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(datos), encoding="utf-8")
            os.replace(tmp, ARCHIVO_ESTADO)
        except OSError:
            pass    # es solo informativo: nunca debe tumbar al bot


def _proceso_vivo(pid) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)       # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            codigo = ctypes.c_ulong()
            return bool(k.GetExitCodeProcess(h, ctypes.byref(codigo))) and codigo.value == 259
        finally:
            k.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _segundos(desde) -> int:
    return int(time.time() - desde) if desde else 0


def _si(pregunta: str) -> bool:
    try:
        respuesta = input(pregunta)
    except EOFError:
        return False
    # Entrada entubada desde PowerShell 5.1 llega con un BOM delante ("﻿s").
    return respuesta.replace("﻿", "").strip().lower() in ("s", "si", "sí", "y", "yes")


def _esperar_apagado(session: str) -> int:
    print("\nApagando de forma ordenada...")
    t0 = time.time()
    ultimo = 0.0
    while time.time() - t0 < ESPERA_APAGADO_SEG:
        if not instancia_unica.en_uso(session):
            print("\nListo: el bot se apagó.")
            time.sleep(3)
            return 0
        est = leer_estado() or {}
        if est.get("orden") and time.time() - ultimo >= 10:
            print(f"  ...terminando la orden {est['orden']} ({_segundos(est.get('desde'))} s)")
            ultimo = time.time()
        time.sleep(1)
    print("\nSigue terminando órdenes pendientes. No cierres nada: se apagará solo en cuanto\n"
          "termine. Abre este acceso otra vez para ver cómo va (o para cancelar el apagado).")
    return 1


def main() -> int:
    # La consola de Windows puede no soportar algún carácter: que nunca tumbe este programa.
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    session = str(RAIZ / opcional("SESSION_NAME", "telegram_local_pc2"))
    if not instancia_unica.en_uso(session):
        limpiar()
        return SALIR_INICIAR

    estado = leer_estado()
    if not estado or not _proceso_vivo(estado.get("pid")):
        print("Hay otro programa usando la sesión de Telegram, pero no es un bot que se pueda\n"
              "apagar desde aquí (puede ser listar_chats.py, el bot de Android o un bot abierto\n"
              "con una versión anterior). Ciérralo a mano y vuelve a abrir este acceso.")
        return 1

    if estado.get("fase") == "iniciando":
        print("El bot está arrancando (esperando al teléfono) y todavía no recibe órdenes.\n"
              "Para cancelar el arranque cierra la ventana del bot: es seguro, no hay ningún\n"
              "pago en curso.")
        return 1

    print("El bot está ENCENDIDO.")
    if estado.get("orden"):
        print(f"  Está pagando la orden {estado['orden']} (hace {_segundos(estado.get('desde'))} s).")
    else:
        print("  No está pagando ninguna orden.")
    if estado.get("en_cola"):
        print(f"  Hay {estado['en_cola']} orden(es) esperando en cola.")

    if estado.get("fase") == "apagando" or parada_pedida():
        print("  Ya se pidió apagarlo: está terminando lo pendiente.")
        if _si("\n¿Cancelar el apagado y dejarlo funcionando? (s/n): "):
            cancelar_parada()
            print("Apagado cancelado: el bot sigue recibiendo órdenes.")
            time.sleep(3)
            return 0
        return _esperar_apagado(session)

    if estado.get("orden") or estado.get("en_cola"):
        print("\nSi lo apagas ahora NO se corta ningún pago: deja de aceptar órdenes nuevas,\n"
              "termina lo pendiente y recién entonces se cierra.")
    if not _si("\n¿Apagar el bot? (s/n): "):
        return 0
    pedir_parada()
    return _esperar_apagado(session)


if __name__ == "__main__":
    sys.exit(main())
