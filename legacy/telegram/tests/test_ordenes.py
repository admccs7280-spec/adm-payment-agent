"""Prueba del lector de órdenes (ordenes.py), sin teléfono ni Telegram. Debe terminar en "TODO OK".

    iphone\\.venv\\Scripts\\python.exe tests\\test_ordenes.py

Incluye los mensajes reales que el bot NO reconocía el 2026-10-25 (sin emojis, y con documento y
teléfono invertidos) y los formatos de siempre, que deben seguir leyéndose igual.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ordenes import parsear_orden, validar_orden  # noqa: E402


def leer(texto):
    return parsear_orden(texto, 1, 1)


# 1) formatos reales de siempre (con emojis): sin cambios
o = leer("0022929364591556145153\n0000000000000000933.735\n------------------\n🏦 BancoDeVenezuela\n"
         "🆔 23798062\n🔢 CTA: 01020754150000056672\n👤 Barbara Requena\n350,00")
assert (o.tipo, o.banco, o.documento, o.cuenta, o.nombre, o.monto) == (
    "TRANSFERENCIA", "BancoDeVenezuela", "23798062", "01020754150000056672", "Barbara Requena", "350,00"), o
o = leer("0022928300824416772096\n0000000000000000932.725\n------------------\n🏦 Banco de Venezuela\n"
         "🆔 29610348\n📱 04120549029\n👤 MARIN COLLANTES YORGELY KAROLAY\n9.308,59")
assert (o.tipo, o.documento, o.telefono, o.monto) == ("PAGO_MOVIL", "29610348", "04120549029", "9.308,59"), o
o = leer("0022945662833617936384\n0000000000000000982.735\n------------------\n🏦 BancoDeVenezuela\n"
         "🆔 31337432\n📱 04244081266\n👤 ORELLANA MORENO LUIS ALEJANDRO\n4.854,71")      # del registro BlueStacks
assert (o.tipo, o.documento, o.telefono) == ("PAGO_MOVIL", "31337432", "04244081266"), o
print("1 ok  formatos con emojis de siempre")

# 2) SIN emojis (al copiar y pegar se pierden): mensajes reales del 2026-10-25 23:15 y 23:16
o = leer("0022935880664613814321\n0000000000000000946.735\n------------------\n 0102\n 123456554\n"
         " 01020467450001765658\n BARBARA REQUENA\n100,00")
assert (o.tipo, o.banco, o.documento, o.cuenta, o.nombre, o.monto) == (
    "TRANSFERENCIA", "0102", "123456554", "01020467450001765658", "BARBARA REQUENA", "100,00"), o
o = leer("0022947829343124717569\n0000000000000000968.725\n------------------\n 0102\n 31899383\n"
         " 04243468273\n NAVA SECO JOSE JAVIER\n100,00")
assert (o.tipo, o.banco, o.documento, o.telefono, o.nombre) == (
    "PAGO_MOVIL", "0102", "31899383", "04243468273", "NAVA SECO JOSE JAVIER"), o
validar_orden(o, set(), 0)
print("2 ok  órdenes sin emojis (por posición)")

# 3) documento y teléfono INVERTIDOS: mensaje real del 2026-10-25 23:20
o = leer("0022947829343124717569\n0000000000000000968.725\n------------------\n 🏦 0102\n 🆔 04243468273\n"
         " 📱 31899383\n 👤 napo\n100,00")
assert (o.tipo, o.documento, o.telefono, o.nombre) == ("PAGO_MOVIL", "31899383", "04243468273", "napo"), o
o = leer("0022947829343124717569\n0000000000000000968.725\n------------------\n0102\n04243468273\n31899383\nnapo\n100,00")
assert (o.tipo, o.documento, o.telefono) == ("PAGO_MOVIL", "31899383", "04243468273"), o
print("3 ok  documento y teléfono invertidos se corrigen por su forma")

# 3b) separador con rayas largas (el teclado del celular cambia "--" por "—"): reales de 23:30 a 01:02
for texto, doc, monto in (
        ("0022947829343124717571\n0000000000000000968.725\n——————\n  0102\n  04243468273\n  31899383\n  napo\n100.000,00",
         "31899383", "100.000,00"),
        ("0022947829343124717572\n0000000000000000968.725\n——————\n  0102\n 31899383\n  04243468273\n  napo\n1.000,00",
         "31899383", "1.000,00"),
        ("0022947829343124717573\n0000000000000000968.725\n–––––\n🏦 0102\n🆔 31899383\n📱 04243468273\n👤 napo\n5,00",
         "31899383", "5,00")):
    o = leer(texto)
    assert o is not None, texto
    assert (o.tipo, o.documento, o.telefono, o.monto) == ("PAGO_MOVIL", doc, "04243468273", monto), o
print("3b ok separador con rayas largas (— –)")

# 4) lo que NO es una orden sigue sin serlo
NO = [
    "...",
    "hola",
    "0022947829343124717569\n0000000000000000968.725\n------------------\n0102\n31899383\n04243468273\n100,00",  # falta nombre
    "0022947829343124717569\n0000000000000000968.725\n------------------\n0102\n31899383\n04243468273\nnapo\nextra\n100,00",
    "0022947829343124717569\n0000000000000000968.725\nxxxx\n0102\n31899383\n04243468273\nnapo\n100,00",  # sin separador
    "0022947829343124717569\n0000000000000000968.725\n------------------\n0102\n31899383\n12345\nnapo\n100,00",  # ni tel ni cuenta
    "0022947829343124717569\n0000000000000000968.725\n------------------\n0102\n00000000\n04243468273\nnapo\n100,00",  # basura
    "0022947829343124717569\n0000000000000000968.725\n------------------\n0102\n31899383\n04243468273\nnapo\n0,00",  # monto 0
    "0022947829343124717569\n0000000000000000968.725\n------------------\n0102\n31899383\n04243468273\nnapo\ncien",
]
for texto in NO:
    assert leer(texto) is None, texto
print("4 ok  mensajes que no son órdenes se siguen ignorando")

# 5) una transferencia con documento 0... no se confunde con teléfono (no se invierte nada)
o = leer("0022947829343124717569\n0000000000000000968.725\n------------------\n🏦 Mercantil\n🆔 12345678\n"
         "🔢 01050754150000056672\n👤 ALGUIEN\n10,00")
assert (o.tipo, o.documento, o.cuenta) == ("TRANSFERENCIA", "12345678", "01050754150000056672"), o
print("5 ok  transferencias normales intactas")

print("\nTODO OK")
