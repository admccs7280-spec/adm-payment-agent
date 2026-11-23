"""El cuadro de la clave en modo OSCURO (captura real del 2026-10-30, iPhone de Ricardo): el OCR leyó
"Contraseña." en vez de "Contraseña:" y el bot no encontraba el campo. Debe decir "TODO OK"."""
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "iphone"))
try:
    import ocr_mac as ocr_windows  # noqa: E402  (macOS: mismo API)
except ImportError:
    import ocr_windows  # noqa: E402
from iphone_utils import ControladorIPhone  # noqa: E402

png = (Path(__file__).resolve().parent / "capturas_ejemplo" / "clave_modo_oscuro.png").read_bytes()
lista = ocr_windows.leer_textos(png)
leido = [t.texto for t in lista if t.texto.lower().startswith("contrase")]
print("el OCR leyó:", leido)
ctrl = ControladorIPhone.__new__(ControladorIPhone)          # sin abrir ningún iPhone
punto = ctrl.ubicar_campo("Contraseña:", lista)
assert punto is not None, "no encontró el campo de la clave con la captura real"
assert 200 < punto[0] < 900 and 1100 < punto[1] < 1400, punto
assert ctrl.buscar("Aceptar", lista) and ctrl.buscar("Cancelar", lista)
print("ok    el campo de la clave se encuentra aunque el OCR lea 'Contraseña.' (punto", punto, ")")
print("\nTODO OK")
