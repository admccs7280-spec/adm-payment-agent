"""Lee los textos de una captura del iPhone con el OCR de Apple (framework Vision de macOS).

Misma interfaz que ocr_windows.py: devuelve cada línea de texto visible con su rectángulo en
píxeles de la captura (1170x2532 en el iPhone 12, origen arriba a la izquierda).
"""
import asyncio
from dataclasses import dataclass

import Quartz
import Vision
from Foundation import NSData

IDIOMAS_OCR = ["es-ES", "en-US"]


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


def leer_textos(png: bytes) -> list[Texto]:
    datos = NSData.dataWithBytes_length_(png, len(png))
    fuente = Quartz.CGImageSourceCreateWithData(datos, None)
    imagen = Quartz.CGImageSourceCreateImageAtIndex(fuente, 0, None) if fuente else None
    if imagen is None:
        raise RuntimeError("OCR: no se pudo decodificar la captura")
    ancho = Quartz.CGImageGetWidth(imagen)
    alto = Quartz.CGImageGetHeight(imagen)

    pedido = Vision.VNRecognizeTextRequest.alloc().init()
    pedido.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    pedido.setUsesLanguageCorrection_(False)   # montos y cuentas: nada de "corregir" dígitos
    try:
        pedido.setRecognitionLanguages_(IDIOMAS_OCR)
    except Exception:
        pass
    manejador = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(imagen, None)
    ok, error = manejador.performRequests_error_([pedido], None)
    if not ok:
        raise RuntimeError(f"OCR de Apple falló: {error}")

    textos = []
    for obs in pedido.results() or []:
        candidatos = obs.topCandidates_(1)
        if not candidatos:
            continue
        caja = obs.boundingBox()      # normalizada, origen abajo a la izquierda
        x1 = int(caja.origin.x * ancho)
        x2 = int((caja.origin.x + caja.size.width) * ancho)
        y1 = int((1 - caja.origin.y - caja.size.height) * alto)
        y2 = int((1 - caja.origin.y) * alto)
        textos.append(Texto(texto=str(candidatos[0].string()), x1=x1, y1=y1, x2=x2, y2=y2))
    textos.sort(key=lambda t: (t.y1, t.x1))
    return textos


async def leer_textos_async(png: bytes) -> list[Texto]:
    return await asyncio.get_running_loop().run_in_executor(None, leer_textos, png)


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
