"""Lee los textos de una captura del iPhone con el OCR que trae Windows.

Es el reemplazo del volcado de `uiautomator` del bot de BlueStacks: devuelve
cada línea de texto visible con su rectángulo en píxeles de la captura
(1170x2532 en el iPhone 12), para ubicar botones y verificar pantallas.
"""
import asyncio
from dataclasses import dataclass

from winrt.windows.globalization import Language
from winrt.windows.graphics.imaging import BitmapDecoder
from winrt.windows.media.ocr import OcrEngine
from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

# Idiomas de OCR que se prueban en orden: el paquete de Windows de la PC puede ser
# es-MX, es-ES u otro. Si no hay ninguno en español se usa el idioma del perfil del
# usuario (lee los números igual, pero puede fallar en tildes: ver la guía, paso 6).
IDIOMAS_OCR = ("es-MX", "es-ES", "es-US", "es-419", "es")


@dataclass
class Texto:
    texto: str
    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def centro(self) -> tuple[int, int]:
        return (self.x1 + self.x2) // 2, (self.y1 + self.y2) // 2


_motor = None


def _motor_ocr() -> OcrEngine:
    global _motor
    if _motor is None:
        for idioma in IDIOMAS_OCR:
            try:
                _motor = OcrEngine.try_create_from_language(Language(idioma))
            except Exception:
                _motor = None
            if _motor is not None:
                break
        if _motor is None:
            _motor = OcrEngine.try_create_from_user_profile_languages()
            if _motor is not None:
                print("  !! OCR de Windows: no hay paquete en español, se usa el idioma del perfil "
                      "(instala 'Español' con la función de reconocimiento óptico: guía, paso 6)")
        if _motor is None:
            raise RuntimeError("Windows no tiene ningún motor de OCR instalado: ver GUIA-INSTALACION.md, paso 6")
    return _motor


async def leer_textos_async(png: bytes) -> list[Texto]:
    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream)
    writer.write_bytes(png)
    await writer.store_async()
    writer.detach_stream()
    stream.seek(0)

    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    resultado = await _motor_ocr().recognize_async(bitmap)

    textos = []
    for linea in resultado.lines:
        rects = [w.bounding_rect for w in linea.words]
        if not rects:
            continue
        textos.append(Texto(
            texto=linea.text,
            x1=int(min(r.x for r in rects)),
            y1=int(min(r.y for r in rects)),
            x2=int(max(r.x + r.width for r in rects)),
            y2=int(max(r.y + r.height for r in rects)),
        ))
    return textos


def leer_textos(png: bytes) -> list[Texto]:
    return asyncio.run(leer_textos_async(png))


if __name__ == "__main__":
    import sys
    import time

    with open(sys.argv[1], "rb") as f:
        datos = f.read()
    t0 = time.perf_counter()
    lineas = leer_textos(datos)
    print(f"{len(lineas)} líneas en {time.perf_counter() - t0:.2f} s")
    for t in lineas:
        print(f"[{t.x1},{t.y1}][{t.x2},{t.y2}] {t.texto}")
