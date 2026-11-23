"""Lectura de la cuenta a debitar enmascarada ("0102****0756"), sin iPhone. Debe decir "TODO OK".

Captura real del 2026-10-30 (iPhone de Asencio Jaramillo): la cuenta estaba bien elegida pero el OCR de
Windows devolvía VACÍO para "0102****0756" en cualquier tamaño, y toda orden fallaba con 'quedó en None'."""
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "iphone"))
os.environ["DISPOSITIVO"] = "iphone"
import iphone_utils as U  # noqa: E402
try:
    import ocr_mac as ocr_windows  # noqa: E402  (macOS: mismo API)
except ImportError:
    import ocr_windows  # noqa: E402

ctrl = U.ControladorIPhone.__new__(U.ControladorIPhone)          # sin abrir ningún iPhone
ctrl._leer_png = lambda png: ocr_windows.leer_textos(png)

png = (Path(__file__).resolve().parent / "capturas_ejemplo" / "cuenta_0756_iphone_asencio.png").read_bytes()
lista = ocr_windows.leer_textos(png)

entera = ctrl._valor_de(png, lista, "Cuenta a debitar:")
# Windows la devuelve vacía; el OCR de Apple (macOS) a veces la lee entera: las dos cosas son válidas.
assert entera in (None, "", "0102****0756"), entera
valor = ctrl._valor_de(png, lista, "Cuenta a debitar:", por_tramos=True)
assert valor == "0102****0756", valor
print("ok    cuenta 0102****0756: el OCR no la lee entera y por tramos sale", valor)

# La verificación de elegir_cuenta_debitar compara por los últimos 4 dígitos.
assert "0756" in valor and "1161" not in valor
print("ok    se distingue de otra cuenta (1161)")

# Revisión de «otros bancos» (captura real 2026-10-30): la fila «Destino:» 0105****6287 también salía vacía.
png = (Path(__file__).resolve().parent / "capturas_ejemplo" / "revision_otros_bancos_asencio.png").read_bytes()
lista = ocr_windows.leer_textos(png)
fila = ctrl.valor_en_fila(png, lista, "Destino:")
assert fila is not None and "".join(c for c in fila if c.isdigit()) == "01056287", fila
print("ok    revisión: fila Destino leída como", fila)
fila_o = ctrl.valor_en_fila(png, lista, "Origen:")
assert fila_o is not None and "0756" in fila_o, fila_o
print("ok    revisión: fila Origen leída como", fila_o)

# La verificación real de la revisión acepta esta pantalla para la orden de prueba.
import ejecutor_iphone as E  # noqa: E402
orden = E.OrdenPago(id_orden="T1", tipo="TRANSFERENCIA", numero_orden="T1", tasa="1", banco="Mercantil",
                    documento="23798062", nombre="BARBARA REQUENA", monto="1,00", remitente_id=1, mensaje_id=1,
                    cuenta="01050032080032506287")
ctrl.nodos = lambda: lista
ctrl.pantalla = lambda: (png, lista)
E._verificar_revision(ctrl, lista, orden)
print("ok    _verificar_revision acepta la pantalla real de la orden")
print("\nTODO OK")
