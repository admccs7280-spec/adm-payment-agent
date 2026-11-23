"""Lectura de la letra del desplegable «Tipo:» (V/E/J/P/G/C), sin iPhone. Debe decir "TODO OK".

Captura real del 2026-10-30 (iPhone de Ricardo): el bot eligió V bien pero la verificación la leyó como J,
porque la letra caía ~13 px más a la izquierda que en las plantillas y la comparación era por posición."""
import sys
from pathlib import Path

from PIL import Image

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "iphone"))
import iphone_utils as U  # noqa: E402
try:
    import ocr_mac as ocr_windows  # noqa: E402  (macOS: mismo API)
except ImportError:
    import ocr_windows  # noqa: E402

ctrl = U.ControladorIPhone.__new__(U.ControladorIPhone)          # sin abrir ningún iPhone
ctrl.captura_png = lambda: PNG
ctrl._leer_png = lambda png: ocr_windows.leer_textos(png)

PNG = (Path(__file__).resolve().parent / "capturas_ejemplo" / "tipo_documento_V_iphone_ricardo.png").read_bytes()
leida = ctrl.leer_tipo_documento()
assert leida == "V", f"la captura real tiene V y se leyó {leida!r}"
print("ok    captura real: V se lee como V (antes salía J)")

# Cada plantilla se reconoce a sí misma aunque la letra caiga desplazada dentro del recorte.
from PIL import ImageChops, ImageStat  # noqa: E402

FORMAS = {l: U._forma_letra(U._normalizar_tema(Image.open(U.CARPETA_PLANTILLAS / f"tipo_doc_{l}.png")))
          for l in U.ORDEN_TIPO_DOCUMENTO}
for letra in U.ORDEN_TIPO_DOCUMENTO:
    plantilla = Image.open(U.CARPETA_PLANTILLAS / f"tipo_doc_{letra}.png").convert("RGB")
    for dx in (-3, 0, 8):
        lienzo = Image.new("RGB", plantilla.size, plantilla.getpixel((2, 2)))
        lienzo.paste(plantilla, (dx, 0))
        forma = U._forma_letra(U._normalizar_tema(lienzo))
        assert forma is not None, (letra, dx)
        mejor = min(FORMAS, key=lambda l: ImageStat.Stat(ImageChops.difference(forma, FORMAS[l])).mean[0])
        assert mejor == letra, (letra, dx, mejor)
print("ok    las 6 letras se reconocen aunque estén desplazadas ±8 px")

# Campo vacío: no hay letra.
vacio = Image.new("RGB", (80, 80), (70, 70, 70))
assert U._forma_letra(U._normalizar_tema(vacio)) is None
print("ok    campo vacío -> None")
print("\nTODO OK")
