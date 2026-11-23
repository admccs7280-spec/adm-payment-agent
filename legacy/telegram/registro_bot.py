"""Registro del bot en logs\\bot.log: lo lee el panel (panel_bot.pyw) para el registro en vivo,
la pestaña Actividad y el motivo de un "se detuvo solo".

- Arrancado desde la CONSOLA (bot_iphone.bat): orquestador.py agrega un FileHandler y lo que
  escribe el registro va a la consola y a bot.log.
- Arrancado desde el PANEL: el panel manda toda la salida del proceso (registro, prints de los
  toques y errores) a bot.log y avisa con BOT_DESDE_PANEL=1, para no escribir cada línea dos veces.

Cada arranque deja una marca  ===== Inicio AAAA-MM-DD HH:MM:SS (panel|consola) =====
"""
import logging
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
CARPETA_LOGS = RAIZ / "logs"
ARCHIVO_LOG = CARPETA_LOGS / "bot.log"
TAMANO_MAXIMO = 5 * 1024 * 1024     # al arrancar, si pasa de esto: bot.log -> bot.log.1 (hasta 3)
ARCHIVOS_VIEJOS = 3
FORMATO = "%(asctime)s %(levelname)s %(message)s"


def rotar_si_grande(log: Path = ARCHIVO_LOG) -> None:
    """Solo con el bot apagado (en Windows no se puede renombrar un archivo abierto)."""
    try:
        if log.stat().st_size < TAMANO_MAXIMO:
            return
    except OSError:
        return
    try:
        for n in range(ARCHIVOS_VIEJOS - 1, 0, -1):
            viejo = log.with_name(f"{log.name}.{n}")
            if viejo.exists():
                viejo.replace(log.with_name(f"{log.name}.{n + 1}"))
        log.replace(log.with_name(f"{log.name}.1"))
    except OSError:
        pass        # mejor un registro grande que un bot que no arranca


def marca_inicio(origen: str) -> str:
    return f"\n===== Inicio {time.strftime('%Y-%m-%d %H:%M:%S')} ({origen}) =====\n"


def preparar_consola() -> None:
    """Bot arrancado desde la consola: además de la pantalla, el registro va a bot.log."""
    CARPETA_LOGS.mkdir(exist_ok=True)
    rotar_si_grande()
    try:
        with open(ARCHIVO_LOG, "a", encoding="utf-8") as f:
            f.write(marca_inicio("consola"))
        manejador = logging.FileHandler(ARCHIVO_LOG, encoding="utf-8")
    except OSError:
        return      # sin registro en archivo el bot igual funciona
    manejador.setFormatter(logging.Formatter(FORMATO))
    logging.getLogger().addHandler(manejador)
