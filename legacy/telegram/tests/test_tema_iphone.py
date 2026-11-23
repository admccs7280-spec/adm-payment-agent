"""Prueba SIN iPhone de la lectura del tipo de documento (V/E/P/J/G/C) en modo OSCURO y modo CLARO, y con
la lista abierta en distintas posiciones. Debe terminar en "TODO OK".

    iphone\\.venv\\Scripts\\python.exe tests\\test_tema_iphone.py

El 2026-10-29 un iPhone con la apariencia en Automático cambió de claro a oscuro en pleno uso y el bot
(hecho en modo oscuro) falló en el tipo de documento ("letras vistas: 0"). Con una letra ya elegida la
lista se abre más arriba y el texto del formulario que queda detrás parecía una 7.ª fila: se tocaba una
fila corrida (C->G, V->G, J->P).
"""
import io
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "iphone"))

import iphone_utils as U  # noqa: E402
from PIL import Image, ImageChops, ImageDraw, ImageOps, ImageStat  # noqa: E402

LETRAS = U.ORDEN_TIPO_DOCUMENTO


def comprobar(nombre, condicion):
    print(("ok    " if condicion else "FALLA ") + nombre)
    if not condicion:
        sys.exit(1)


def captura(tema: str, y_primera: int, con_texto_detras: bool = True, y_texto: int = 1055) -> bytes:
    """Lista de las 6 letras con la primera fila en y_primera, sobre un formulario de ese tema."""
    pagina, lista, tinta = ((58, 58, 58), (48, 48, 48), 255) if tema == "oscuro" else ((242, 242, 247), (250, 250, 250), 20)
    img = Image.new("RGB", (1170, 2532), pagina)
    d = ImageDraw.Draw(img)
    if con_texto_detras:                      # "0134 - BANESCO": empieza en x=92 y se extiende, como en el formulario
        d.rectangle((92, y_texto - 18, 475, y_texto + 18), fill=(tinta,) * 3)
        d.rectangle((60, 600, 1100, 604), fill=(120, 120, 120))          # borde de un campo
    d.rectangle((10, y_primera - 70, 470, y_primera + 5 * 144 + 70), fill=lista)   # el menú desplegable
    for i in range(6):
        y = y_primera + i * 144
        d.rectangle((62 + (i % 3) * 4, y - 20, 84 + (i % 3) * 3, y + 20), fill=(tinta,) * 3)   # la "letra"
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


for tema in ("oscuro", "claro"):
    for y0 in (1569, 1206, 1000, 800):       # la lista se abre debajo o más arriba según la letra elegida
        esperado = [y0 + i * 144 for i in range(6)]
        filas = U.ControladorIPhone._filas_letras(captura(tema, y0))
        comprobar(f"modo {tema}, lista desde y={y0}: las 6 filas correctas", filas == esperado)
filas = U.ControladorIPhone._filas_letras(captura("oscuro", 1206, y_texto=1062))
comprobar("texto del formulario justo encima de la lista (a 144 px) NO cuenta como fila",
          filas == [1206 + i * 144 for i in range(6)])
vacia = io.BytesIO()
Image.new("RGB", (1170, 2532), (242, 242, 247)).save(vacia, "PNG")
comprobar("sin lista abierta no hay filas", U.ControladorIPhone._filas_letras(vacia.getvalue()) == [])

# ── las plantillas (hechas en modo oscuro) reconocen la letra en modo claro ──
plantillas = {l: Image.open(U.CARPETA_PLANTILLAS / f"tipo_doc_{l}.png").convert("L") for l in LETRAS}


def leer(recorte: Image.Image) -> str:
    rec = U._normalizar_tema(recorte)
    mejor = min(LETRAS, key=lambda l: ImageStat.Stat(ImageChops.difference(rec, U._normalizar_tema(plantillas[l]))).mean[0])
    return mejor


def version_clara(p: Image.Image) -> Image.Image:
    """El mismo campo en modo claro: fondo casi blanco y letra oscura (colores invertidos y atenuados)."""
    return ImageOps.invert(p).point(lambda v: int(25 + v * 0.88))


for l in LETRAS:
    comprobar(f"letra {l}: se lee igual en modo oscuro y en modo claro", leer(plantillas[l]) == l and leer(version_clara(plantillas[l])) == l)

# ── el fondo de un campo se mide, no se supone ──
oscuro = Image.new("RGB", (300, 100), (69, 69, 69))
claro = Image.new("RGB", (300, 100), (245, 245, 250))
ImageDraw.Draw(claro).rectangle((100, 40, 200, 70), fill=(20, 20, 20))
comprobar("fondo de un campo en modo oscuro", U._fondo_de(oscuro) == (69, 69, 69))
comprobar("fondo de un campo en modo claro (con texto en medio)", U._fondo_de(claro) == (245, 245, 250))

print("\nTODO OK")
