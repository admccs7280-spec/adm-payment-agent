"""Resultados de órdenes que NO se pudieron mandar a Telegram: se guardan en disco y se reintentan
solos hasta que Telegram responda, también después de reiniciar el bot.

Visto el 2026-10-26 entre 9:26 y 9:48: Telegram tuvo una caída propia ("RPCError -500: No workers
running"). El bot pagó dos órdenes con éxito y, tras ~45 s de intentos, el resultado se perdió: ni
captura ni aviso en el grupo. Con esto el pago sigue su curso (la cola no se frena) y el resultado
llega en cuanto Telegram vuelve, con una nota de que va con retraso.

Un resultado guardado aquí no vuelve a tocar el teléfono ni el banco: solo es un mensaje por mandar.
"""
import asyncio
import json
import os
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
ARCHIVO = RAIZ / "envios_pendientes.json"
INTERVALO_SEG = 30           # cada cuánto se reintenta lo pendiente
PRIMER_INTENTO_SEG = 25      # el primer envío (tras el pago) no frena la cola más que esto
REINTENTO_SEG = 60
INTENTOS_CON_CAPTURA = 3     # después, si la foto es el problema, se manda solo el texto


def cargar(archivo: Path | None = None) -> list[dict]:
    try:
        datos = json.loads((archivo or ARCHIVO).read_text(encoding="utf-8"))
        return datos if isinstance(datos, list) else []
    except (OSError, ValueError):
        return []


def guardar(lista: list[dict], archivo: Path | None = None) -> None:
    archivo = archivo or ARCHIVO
    if not lista:
        try:
            archivo.unlink()
        except OSError:
            pass
        return
    tmp = archivo.with_suffix(".tmp")
    tmp.write_text(json.dumps(lista, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, archivo)


def nuevo(id_orden: str, chat: int, reply_to: int | None, titulo: str, ruta: str = "") -> dict:
    return {"clave": f"{id_orden}-{time.time():.3f}", "id_orden": id_orden, "chat": chat,
            "reply_to": reply_to, "titulo": titulo, "ruta": ruta or "",
            "desde": time.strftime("%Y-%m-%d %H:%M:%S"), "intentos": 0}


def agregar(aviso: dict, archivo: Path | None = None) -> None:
    lista = cargar(archivo)
    lista.append(aviso)
    guardar(lista, archivo)


async def enviar(client, aviso: dict, timeout: float = REINTENTO_SEG, retraso: bool = False) -> None:
    """Manda el resultado (captura con el texto, o solo el texto). Lanza si no se pudo."""
    titulo = aviso["titulo"]
    if retraso:
        titulo += f"\n⏱ Enviado con retraso: Telegram no respondía ({aviso['desde'][11:16]})."
    ruta = aviso.get("ruta") or ""
    if ruta and Path(ruta).exists() and aviso.get("intentos", 0) < INTENTOS_CON_CAPTURA:
        await asyncio.wait_for(client.send_file(aviso["chat"], ruta, caption=titulo,
                                                reply_to=aviso.get("reply_to")), timeout)
        return
    if ruta:
        titulo += "\n⚠️ No se pudo adjuntar la captura."
    await asyncio.wait_for(client.send_message(aviso["chat"], titulo, reply_to=aviso.get("reply_to")), timeout)


async def reintentar(client, log, archivo: Path | None = None) -> int:
    """Intenta mandar cada pendiente, de a uno. Devuelve cuántos siguen pendientes. Relee el archivo
    tras cada envío: el trabajador puede agregar otro mientras tanto."""
    for aviso in cargar(archivo):
        try:
            await enviar(client, aviso, retraso=True)
        except Exception as exc:
            lista = cargar(archivo)
            for p in lista:
                if p.get("clave") == aviso.get("clave"):
                    p["intentos"] = p.get("intentos", 0) + 1
            guardar(lista, archivo)
            log.info("Orden %s: Telegram sigue sin aceptar el resultado (%s); se reintenta en %s s.",
                     aviso["id_orden"], _motivo(exc), INTERVALO_SEG)
            return len(lista)      # si Telegram no responde, no tiene sentido probar los demás ahora
        guardar([p for p in cargar(archivo) if p.get("clave") != aviso.get("clave")], archivo)
        log.info("Orden %s: resultado enviado a Telegram con retraso (pendiente desde %s).",
                 aviso["id_orden"], aviso["desde"][11:19])
    return len(cargar(archivo))


def _motivo(exc: BaseException) -> str:
    texto = " ".join(str(exc).split()) or type(exc).__name__
    return texto[:160]
