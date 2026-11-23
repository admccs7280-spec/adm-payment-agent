"""Documentos de identidad de las órdenes: V cédula, E extranjero, J jurídico (RIF), P pasaporte, G, C.
Debe terminar en "TODO OK".   python tests/test_documentos.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ordenes import OrdenInvalida, parsear_orden, partir_documento, validar_orden  # noqa: E402

# 1) separar letra y número
casos = {
    "23798062": ("V", "23798062"), "V-23798062": ("V", "23798062"), "v23798062": ("V", "23798062"),
    "E-12345678": ("E", "12345678"), "E 12345678": ("E", "12345678"), "E.12345678": ("E", "12345678"),
    "J-301234567": ("J", "301234567"), "J-30123456-7": ("J", "301234567"), "j301234567": ("J", "301234567"),
    "G-200012345": ("G", "200012345"), "C-123456789": ("C", "123456789"),
    "P-A1234567": ("P", "A1234567"), "A1234567": ("P", "A1234567"), "p123456789": ("P", "123456789"),
}
for entrada, esperado in casos.items():
    assert partir_documento(entrada) == esperado, (entrada, partir_documento(entrada), esperado)
print("1 ok  partir_documento: V, E, J, P, G, C con guion, espacio, punto o pegado")


def orden(documento, nueva_linea_tipo=False):
    medio = "📱 04243468273"
    return ("0022948739398847791104\n0000000000000000984.725\n------------------\n🏦 Banesco\n"
            f"🆔 {documento}\n{medio}\n👤 EMPRESA PRUEBA\n100,00")


# 2) la orden completa: la letra va a tipo_documento y el campo recibe solo el número
for doc, letra, num in (("J-30123456-7", "J", "301234567"), ("E-12345678", "E", "12345678"),
                        ("V 23798062", "V", "23798062"), ("P-A1234567", "P", "A1234567"),
                        ("23798062", "V", "23798062")):
    o = parsear_orden(orden(doc), 1, 1)
    assert o is not None, doc
    assert (o.tipo_documento, o.documento) == (letra, num), (doc, o.tipo_documento, o.documento)
    validar_orden(o, set(), 0)                          # antes: "documento con formato inválido"
print("2 ok  la orden con J-, E-, P- se reconoce, se valida y trae letra y número por separado")

# 3) lo inválido se sigue rechazando
for malo in ("V-123", "J-12", "X-12345678", "12345678901234"):
    o = parsear_orden(orden(malo), 1, 1)
    if o is not None:
        try:
            validar_orden(o, set(), 0)
        except OrdenInvalida:
            continue
        raise AssertionError(f"debió rechazarse: {malo}")
print("3 ok  documentos demasiado cortos o largos o con letra desconocida se rechazan")

# 4) transferencia con cuenta y documento con letra (extranjero)
o = parsear_orden("0022948739398847791104\n0000000000000000984.725\n------------------\n🏦 Banesco\n🆔 E-12345678\n"
                  "🔢 CTA: 01340154551234567890\n👤 JOHN SMITH\n250,00", 1, 1)
assert o and o.tipo == "TRANSFERENCIA" and o.tipo_documento == "E" and o.documento == "12345678"
print("4 ok  transferencia a un extranjero (E)")

print("\nTODO OK")
