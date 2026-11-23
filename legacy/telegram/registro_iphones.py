"""Registro de iPhone: cada teléfono (por su UDID) con su nombre, su cuenta BDV y la clave de operaciones.

Archivo `iphones.json` (junto al .env; contiene claves: no se respalda ni se copia a otra PC).

    {"IOS_UDID_LOCAL": {"nombre": "iPhone de ejemplo", "cuenta": "0102****1234", "clave": "..."}}

Con esto, al conectar CUALQUIER iPhone registrado el bot sabe solo con qué cuenta y qué clave pagar,
sin tocar el .env ni reiniciar nada. Un iPhone que no está aquí se registra con el panel
(pestaña Teléfonos) o con `herramientas_iphone.py registrar`.
"""
import json
import os
import re
import threading
from pathlib import Path

ARCHIVO = Path(__file__).resolve().parent / "iphones.json"
_LOCK = threading.Lock()
CUENTA_VALIDA = re.compile(r"^\d{4}\*{2,}\d{4}$")


def cargar(archivo: Path | None = None) -> dict[str, dict]:
    try:
        datos = json.loads((archivo or ARCHIVO).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return {u.strip(): d for u, d in datos.items() if isinstance(d, dict) and u.strip()} if isinstance(datos, dict) else {}


def buscar(udid: str, archivo: Path | None = None) -> dict | None:
    objetivo = (udid or "").strip().lower()
    for u, d in cargar(archivo).items():
        if u.lower() == objetivo:
            return dict(d, udid=u)
    return None


def guardar(udid: str, nombre: str = "", cuenta: str | None = None, clave: str | None = None,
            archivo: Path | None = None) -> dict:
    """Crea o actualiza UN iPhone cambiando solo los campos que se pasan."""
    destino = archivo or ARCHIVO
    with _LOCK:
        datos = cargar(destino)
        actual = datos.get(udid, {})
        if nombre:
            actual["nombre"] = nombre.strip()
        if cuenta is not None:
            actual["cuenta"] = cuenta.strip()
        if clave is not None:
            actual["clave"] = clave
        actual.setdefault("nombre", "iPhone")
        actual.setdefault("cuenta", "")
        actual.setdefault("clave", "")
        datos[udid] = actual
        tmp = destino.with_suffix(".tmp")
        tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, destino)
    return dict(actual, udid=udid)


def quitar(udid: str, archivo: Path | None = None) -> bool:
    destino = archivo or ARCHIVO
    with _LOCK:
        datos = cargar(destino)
        if udid not in datos:
            return False
        del datos[udid]
        tmp = destino.with_suffix(".tmp")
        tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, destino)
    return True


def importar_plantilla(plantilla: Path | None = None, archivo: Path | None = None) -> list[str]:
    """Agrega los iPhone de `iphones.plantilla.json` (la lista del zip: nombre y cuenta, SIN clave) que esta
    PC aún no tiene. Nunca pisa un iPhone ya registrado ni sus claves. Devuelve los UDID agregados."""
    origen = plantilla or ARCHIVO.with_name("iphones.plantilla.json")
    nuevos = []
    for udid, d in cargar(origen).items():
        if buscar(udid, archivo) is None:
            guardar(udid, nombre=d.get("nombre", "iPhone"), cuenta=d.get("cuenta", ""), clave="", archivo=archivo)
            nuevos.append(udid)
    return nuevos


def problemas(d: dict) -> list[str]:
    """Qué le falta a un registro para poder pagar."""
    faltan = []
    if not (d.get("clave") or "").strip():
        faltan.append("falta la clave de operaciones")
    elif "#" in d["clave"]:
        faltan.append("la clave no puede llevar '#'")
    if d.get("cuenta") and not CUENTA_VALIDA.match(d["cuenta"].strip()):
        faltan.append("la cuenta debe verse como 0102****1234")
    return faltan
