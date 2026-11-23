"""Vuelca los elementos de la pantalla ACTUAL de BDV en el emulador.

Solo lee, no toca nada. Uso: deja el emulador en la pantalla que quieres
mapear (por ejemplo el formulario de transferencia) y corre:

    python inspeccionar.py            # solo elementos con texto/rótulo/clic
    python inspeccionar.py --todo     # todos los nodos

Con eso sacamos los rótulos exactos y las coordenadas para ejecutor.py.
"""
import sys

from adb_utils import ControladorADB, detectar_serial
from config import load_settings


def main():
    todo = "--todo" in sys.argv
    serial = load_settings().serial_adb or detectar_serial()
    ctrl = ControladorADB(serial)
    for a in ctrl.nodos():
        desc = a.get("content-desc", "").replace("\n", " | ")
        texto = a.get("text", "")
        clic = a.get("clickable") == "true"
        editable = "EditText" in a.get("class", "")
        if not (todo or desc or texto or clic or editable):
            continue
        marca = "CLIC" if clic else "    "
        tipo = "EDIT" if editable else "    "
        print(f"{marca} {tipo} {ctrl.centro(a)} desc={desc!r} text={texto!r} bounds={a.get('bounds')}")


if __name__ == "__main__":
    main()
