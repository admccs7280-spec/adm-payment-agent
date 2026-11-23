"""Nombre que queda vacío tras escribirlo (2026-10-30: «Debes introducir el nombre»), sin iPhone.
Debe decir "TODO OK"."""
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "iphone"))
os.environ["DISPOSITIVO"] = "iphone"
import ejecutor_iphone as E  # noqa: E402


class Ctrl:
    def __init__(self, actual):
        self.actual, self.llamadas = actual, []

    def leer_campo(self, rotulo):
        return self.actual

    def llenar(self, rotulo, valor, **kw):
        self.llamadas.append((rotulo, valor, kw))


# Lectura por lote con el nombre visto: no se toca nada ni se pide otra captura.
c = Ctrl("BARBARA REQUENA")
E._rellenar_si_vacio(c, {"Nombre:": "BARBARA REQUENA"}, "Nombre:", "BARBARA REQUENA", doble_toque=True)
assert c.llamadas == []
print("ok    nombre visto: no se vuelve a llenar")

# Vacío (el toque se perdió): se vuelve a llenar, con doble toque.
c = Ctrl(None)
E._rellenar_si_vacio(c, {"Nombre:": None}, "Nombre:", "BARBARA REQUENA", doble_toque=True)
assert c.llamadas == [("Nombre:", "BARBARA REQUENA", {"doble_toque": True})], c.llamadas
print("ok    nombre vacío: se vuelve a llenar con doble toque")

# El lote no lo vio pero la lectura aparte sí: tampoco se llena.
c = Ctrl("BARBARA")
E._rellenar_si_vacio(c, {"Nombre:": None}, "Nombre:", "BARBARA REQUENA", doble_toque=True)
assert c.llamadas == []
print("ok    lote sin verlo pero lectura aparte sí: no se llena")
print("\nTODO OK")
