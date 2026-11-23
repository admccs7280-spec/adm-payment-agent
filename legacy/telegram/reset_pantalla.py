"""Deja el emulador en Inicio, sea cual sea el estado actual: cierra
diálogos de error, cierra menús tipo hoja inferior (Scrim) y navega con la
pestaña Inicio. Solo lectura + navegación segura, nunca toca campos de
dinero ni contraseña. Uso manual entre pruebas.

    python reset_pantalla.py
"""
from adb_utils import ControladorADB, detectar_serial
from config import opcional
from ejecutor import _volver_inicio_seguro


def main():
    serial = opcional("SERIAL_ADB", "") or detectar_serial()
    ctrl = ControladorADB(serial)
    _volver_inicio_seguro(ctrl)
    print("Listo.")


if __name__ == "__main__":
    main()
