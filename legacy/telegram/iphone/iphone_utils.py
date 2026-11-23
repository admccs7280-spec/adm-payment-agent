"""Control del iPhone por cable desde Windows, sin Mac y sin firmar nada.

Equivalente de adb_utils.ControladorADB para el iPhone 12 (iOS 27+):
  - toques, arrastres y teclado: servicio HID de CoreDevice (el mismo del
    espejo de pantalla de Xcode); iOS solo lo acepta desde iOS 27.
  - "volcado" de pantalla: captura + OCR de Windows (ocr_windows.py), que
    devuelve textos con su rectángulo, como los bounds de uiautomator.

pymobiledevice3 es asíncrono y el bot es síncrono (ejecutar_pago corre en un
hilo), así que el controlador tiene su propio event loop en un hilo aparte y
expone métodos síncronos. El túnel y la sesión de toques se abren UNA vez y
se reutilizan: abrirlos por cada acción cuesta ~3 s.
"""
import asyncio
import io
import struct
import sys
import threading
import time
import unicodedata
from concurrent.futures import Future
from pathlib import Path
from typing import Callable, Optional

import re

from PIL import Image, ImageChops, ImageOps, ImageStat

from pymobiledevice3.remote.core_device.hid_service import (
    ASCII_TO_HID,
    DIGITIZER_SURFACE_MAIN_TOUCHSCREEN,
    KEY_BACKSPACE,
    KEY_ENTER,
    KEY_LEFT_SHIFT,
    TOUCHSCREEN_STATE_CONTACT,
    TOUCHSCREEN_STATE_RELEASE,
    IndigoHIDService,
    touch_session,
)
from pymobiledevice3.remote.core_device.screen_capture_service import ScreenCaptureService
from pymobiledevice3.remote.userspace_tunnel import UserspaceRsdTunnel

if sys.platform == "darwin":
    from ocr_mac import Texto, leer_textos_async
else:
    from ocr_windows import Texto, leer_textos_async

TIMEOUT_ACCION_SEG = 20
BOTON_INICIO = (0x0C, 0x40)
# Medido en el iPhone 12 con iOS 27.0.1 (2026-10-24): con la espera de 0,5 s
# que trae pymobiledevice3, iOS descartaba los toques en silencio (la sesión
# abría sin error y la pantalla no cambiaba); con 2 s ya llegaban.
ESPERA_AUTORIZACION_TOQUES_SEG = 2.5
ALTO_ROTULO_FLOTANTE_PX = 30
DESPLAZAMIENTO_VALOR_PX = 66

# Ancho (px de la captura) de cada rótulo con el campo VACÍO, medido en el
# iPhone 12. Al llenar un campo el rótulo "flota" al borde y se achica al 75 %:
# comparar contra este ancho dice si el campo ya tiene valor, cosa que la altura
# no dice (las letras con cola -p, j, g- cambian el alto del texto).
ANCHO_ROTULO_VACIO = {
    "cuenta a debitar:": 362,
    "tipo de instrumento:": 442,
    "banco:": 138,
    "instrumento destino:": 436,
    "nombre:": 178,
    "tipo:": 104,
    "documento:": 253,
    "monto:": 142,
    "concepto:": 208,
    "cuenta destino:": 330,
    "telefono:": 194,
}
# 2026-10-31: 0.88 fallaba en el iPhone «Ricardo»: su texto sale al ~82 % del calibrado
# (campo vacío "Documento:" 208 px de 253, "Teléfono:" 160 de 194), así que un campo VACÍO se
# tomaba por lleno, se tocaba 66 px más abajo (borde del campo) y Documento y Teléfono
# quedaban sin escribir ("el campo Teléfono: quedó en None"). Con valor el rótulo mide 75 % de
# lo suyo (62 % en ese teléfono). Entre 0.75 y 0.82 separa los dos casos en ambos teléfonos.
FACTOR_ROTULO_FLOTANTE = 0.78
# macOS: las cajas del OCR de Apple miden ~9 % más ancho que las de Windows (medido con las capturas de
# ejemplo: "Banco:" 157 px contra 138, "Monto:" 156 contra 142, "Documento:" 270 contra 253).
# Se escala la tabla de anchos de campo vacío; el umbral no cambia. Verificar con un iPhone real.
if sys.platform == "darwin":
    ANCHO_ROTULO_VACIO = {k: round(v * 1.09) for k, v in ANCHO_ROTULO_VACIO.items()}
# Franja horizontal (px) de los campos que comparten fila o tienen un botón al
# lado; los demás ocupan todo el ancho. El valor de "Monto:" va pegado a la
# derecha (máscara "2.000,00 Bs"), por eso no se puede exigir que empiece
# cerca del rótulo.
# OJO con el borde IZQUIERDO: el recorte de lectura (_valor_de) arranca 30 px dentro
# de la franja, y el valor de "Documento:" (a la derecha de "Tipo:") empieza en x≈463.
# Con 440 el recorte arrancaba en 470 y cortaba la curva del primer dígito: un 8 o un
# 9 se leían 3 ("3868923" por "8868923", "3387285" por "9387285") a CUALQUIER
# ampliación, y el bot abortaba órdenes con el formulario bien lleno (2026-10-24).
EXTENSION_CAMPO = {
    "tipo:": (40, 420),
    "documento:": (420, 1130),
    "instrumento destino:": (40, 960),
    "cuenta destino:": (40, 960),
}

KEY_END = 0x4D  # HID Keyboard usage "End"
# Rótulos de los formularios de arriba hacia abajo (los de terceros, otros bancos
# y Pago Móvil comparten este orden relativo): sirve para saber hacia dónde
# deslizar cuando un campo no está a la vista.
ORDEN_VERTICAL = ["cuenta a debitar:", "tipo de instrumento:", "banco:", "cuenta destino:",
                  "instrumento destino:", "nombre:", "tipo:", "documento:", "monto:",
                  "concepto:", "transferir", "pagar", "limpiar"]
# Franja de la pantalla donde un campo se puede tocar: por encima queda la
# barra del título y por debajo la barra de pestañas.
VISTA_MIN_Y = 270
VISTA_MAX_Y = 2150
ORDEN_TIPO_DOCUMENTO = ["V", "E", "P", "J", "G", "C"]
CARPETA_PLANTILLAS = Path(__file__).resolve().parent / "plantillas"


# El OCR de Apple (macOS) a veces devuelve letras cirílicas/griegas idénticas a las latinas
# ("Tiрo:" con р cirílica): se pasan a latinas para que los rótulos se encuentren.
_PARECIDAS = str.maketrans({"р": "p", "о": "o", "с": "c", "а": "a", "е": "e", "х": "x", "у": "y",
                            "і": "i", "ѕ": "s", "ј": "j", "к": "k", "м": "m", "т": "t", "н": "h",
                            "Р": "P", "О": "O", "С": "C", "А": "A", "Е": "E", "Х": "X", "І": "I",
                            "К": "K", "М": "M", "Т": "T", "Н": "H", "В": "B", "ο": "o", "ν": "v"})


def _normalizar(texto: str) -> str:
    t = unicodedata.normalize("NFKD", (texto or "").translate(_PARECIDAS))
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return " ".join(t.split()).casefold()


COLOR_FONDO_CAMPO = (69, 69, 69)      # fondo de un campo en modo OSCURO (valor por defecto)


def _fondo_de(img: Image.Image) -> tuple[int, int, int]:
    """Color de fondo de un recorte, sea cual sea el tema del iPhone (oscuro o claro): la mediana
    de unos puntos de su borde superior (arriba del texto)."""
    img = img.convert("RGB")
    y = min(4, img.height - 1)
    xs = [max(0, min(img.width - 1, int(img.width * f))) for f in (0.05, 0.25, 0.5, 0.75, 0.95)]
    puntos = [img.getpixel((x, y)) for x in xs]
    return tuple(sorted(p[c] for p in puntos)[len(puntos) // 2] for c in range(3))


def _borrar_cursor(img: Image.Image, fondo: tuple[int, int, int] = COLOR_FONDO_CAMPO) -> Image.Image:
    """Pinta del color del fondo los píxeles rosados/rojos (cursor de texto y
    mensajes de validación): el texto de los campos es blanco/negro o gris."""
    datos = img.load()
    for y in range(img.height):
        for x in range(img.width):
            r, g, b = datos[x, y]
            if r > g + 40 and r > b + 20:
                datos[x, y] = fondo
    return img


def _normalizar_tema(img: Image.Image) -> Image.Image:
    """Escala de grises con el fondo OSCURO y la letra clara, igual en modo claro que en modo
    oscuro: si el recorte es claro se invierte, y se estira el contraste. Así las plantillas de las
    letras (hechas en modo oscuro) sirven en cualquier iPhone."""
    g = img.convert("L")
    if ImageStat.Stat(g).mean[0] > 128:
        g = ImageChops.invert(g)
    return ImageOps.autocontrast(g, cutoff=1)


normalizar = _normalizar


def _forma_letra(g: Image.Image) -> Optional[Image.Image]:
    """La letra del recorte (ya en escala de grises, fondo oscuro) recortada por su propia caja y
    llevada a 40x40: no depende de dónde caiga en el recorte. 2026-10-30: en otro iPhone la letra
    quedaba ~13 px más a la izquierda que en las plantillas y la V se leía como J (todas las letras
    daban 10 a 14 de diferencia). None si no hay letra (campo vacío)."""
    g = g.convert("L")
    margen = 4                                             # sin el borde del campo
    centro = g.crop((margen, margen, g.width - margen, g.height - margen))
    caja = centro.point(lambda v: 255 if v > 110 else 0).getbbox()
    if caja is None or (caja[2] - caja[0]) < 6 or (caja[3] - caja[1]) < 10:
        return None
    letra = centro.crop(caja).resize((40, 40), Image.LANCZOS)
    return ImageOps.autocontrast(letra)


def _normalizar_rotulo(texto: str) -> str:
    """Como _normalizar, pero un rótulo que el OCR leyó con punto o punto y coma finales
    ("Contraseña." por "Contraseña:") se toma igual. Pasa con la letra gris sobre gris del modo
    oscuro: 2026-10-30, un iPhone nuevo no encontraba el campo de la clave y la orden quedaba
    INCIERTA aunque el cuadro estaba abierto."""
    t = _normalizar(texto)
    return t[:-1] + ":" if t.endswith((".", ";")) and len(t) > 2 else t


def _tamano_png(png: bytes) -> tuple[int, int]:
    if not png.startswith(b"\x89PNG"):
        raise RuntimeError("la captura del iPhone no es un PNG válido")
    return struct.unpack(">II", png[16:24])


class ControladorIPhone:
    def __init__(self, udid: Optional[str] = None):
        self.udid = udid
        self.ancho = self.alto = None
        self._loop = asyncio.new_event_loop()
        self._hilo = threading.Thread(target=self._loop.run_forever, name="iphone-loop", daemon=True)
        self._hilo.start()
        self._tunel = None
        self._rsd = None
        self._sesion_ctx = None
        self._hid = None
        self._teclado_id = None
        self._lock_captura = None
        self.deslizamientos_hacia_abajo = 0
        # Gancho opcional: recibe el PNG de CADA captura que el bot ya toma
        # (vigilar_iphone.py lo usa para guardarlas sin pedir capturas extra).
        self.al_capturar: Optional[Callable[[bytes], None]] = None
        # Gancho opcional: recibe los textos de CADA lectura de pantalla (ejecutor_iphone lo usa
        # para ver los avisos del banco "Transferencia interbancaria exitosa/fallida").
        self.al_leer: Optional[Callable[[list], None]] = None
        try:
            self._correr(self._conectar())
            # La sesión de toques y el teclado virtual se abren YA, no en el primer
            # toque: sin teclado "físico" registrado iOS deja su teclado de pantalla
            # (tapando botones como "Pagar") y las lecturas previas al primer toque
            # no verían el botón.
            self._correr(self._con_sesion(self._sin_accion), timeout=40)
        except BaseException:
            # Si la conexión falla a medias (iPhone nuevo sin la imagen de desarrollador, Modo de
            # desarrollador apagado...) el túnel quedaba ABIERTO en este proceso y todos los
            # reintentos fallaban con "A userspace tunnel is already active in this process"
            # (2026-10-29: el bot no arrancaba con un iPhone nuevo). Se cierra todo antes de salir.
            self._abortar()
            raise

    def _abortar(self) -> None:
        """Cierra lo que haya quedado abierto de una conexión fallida (nunca lanza)."""
        async def _limpiar():
            await self._cerrar_sesion_toques()
            if self._tunel is not None:
                try:
                    await self._tunel.aclose()
                except Exception:
                    pass
        try:
            self._correr(_limpiar(), timeout=15)
        except Exception:
            pass
        finally:
            self._tunel = self._rsd = None
            self._loop.call_soon_threadsafe(self._loop.stop)

    @staticmethod
    async def _sin_accion(hid):
        return None

    # ── plomería: loop propio + reconexión ──────────────────────────
    def _correr(self, coro, timeout: float = TIMEOUT_ACCION_SEG):
        futuro: Future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return futuro.result(timeout=timeout)

    async def _conectar(self):
        self._tunel = UserspaceRsdTunnel(serial=self.udid)
        self._rsd = await self._tunel.aopen()
        self._lock_captura = asyncio.Lock()
        png = await self._captura_async()
        self.ancho, self.alto = _tamano_png(png)

    async def _abrir_sesion_toques(self):
        if self._hid is not None:
            return self._hid
        self._sesion_ctx = touch_session(self._rsd)
        self._hid = await self._sesion_ctx.__aenter__()
        await asyncio.sleep(ESPERA_AUTORIZACION_TOQUES_SEG)
        # Mientras haya un teclado "físico" registrado iOS oculta el de
        # pantalla; sin él, un foco que quedó en la app (de una corrida
        # anterior) deja el teclado QWERTY tapando la barra de pestañas.
        self._teclado_id = await self._hid.create_keyboard_service()
        return self._hid

    async def _cerrar_sesion_toques(self):
        ctx, self._sesion_ctx, self._hid, self._teclado_id = self._sesion_ctx, None, None, None
        if ctx is not None:
            try:
                await ctx.__aexit__(None, None, None)
            except Exception:
                pass

    async def _con_sesion(self, accion: Callable):
        """Corre `accion(hid)` sobre la sesión de toques (la abre si hace falta).

        Si la acción falla a medias NO se repite: un reintento ciego podría
        escribir un texto dos veces o tocar dos veces "Transferir". Solo se
        descarta la sesión para que la acción siguiente abra una nueva."""
        hid = await self._abrir_sesion_toques()
        try:
            return await accion(hid)
        except Exception:
            await self._cerrar_sesion_toques()
            raise

    def cerrar(self):
        async def _cerrar():
            await self._cerrar_sesion_toques()
            if self._tunel is not None:
                await self._tunel.aclose()
        try:
            self._correr(_cerrar())
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)

    # ── lectura de pantalla ─────────────────────────────────────────
    async def _captura_async(self) -> bytes:
        # El iPhone solo admite UNA captura a la vez ("A screenshot capture is
        # already in progress", código 25003): el candado serializa las del
        # propio bot y evita que un hilo extra (vigilancia) le haga fallar una.
        async with self._lock_captura:
            async with ScreenCaptureService(self._rsd) as servicio:
                respuesta = await servicio.capture_screenshot()
        png = respuesta["image"]
        if self.al_capturar is not None:
            try:
                self.al_capturar(png)
            except Exception:
                pass          # el gancho es solo para diagnóstico: nunca debe afectar al bot
        return png

    def captura_png(self) -> bytes:
        return self._correr(self._captura_async())

    def pantalla(self) -> tuple[bytes, list[Texto]]:
        """Una captura nueva y su OCR, juntos (una sola captura por lectura)."""
        async def _p():
            png = await self._captura_async()
            return png, await leer_textos_async(png)
        png, lista = self._correr(_p())
        if self.al_leer is not None:
            try:
                self.al_leer(lista)
            except Exception:
                pass          # el gancho solo observa: nunca debe afectar al bot
        return png, lista

    def capturar_pantalla(self, destino_png: str) -> str:
        png = self.captura_png()
        _tamano_png(png)
        with open(destino_png, "wb") as f:
            f.write(png)
        return destino_png

    def nodos(self) -> list[Texto]:
        """Textos visibles con su rectángulo (OCR de una captura nueva)."""
        return self.pantalla()[1]

    def buscar(self, rotulo: str, lista: Optional[list[Texto]] = None,
               exacto: bool = True) -> Optional[tuple[int, int]]:
        objetivo = _normalizar_rotulo(rotulo)
        for t in lista if lista is not None else self.nodos():
            texto = _normalizar_rotulo(t.texto)
            if texto == objetivo or (not exacto and objetivo in texto):
                return t.centro
        return None

    def existe(self, rotulo: str, lista: Optional[list[Texto]] = None, exacto: bool = True) -> bool:
        return self.buscar(rotulo, lista, exacto) is not None

    @staticmethod
    def _rotulo_flotante(t: Texto) -> bool:
        """True si el rótulo ya subió al borde del campo (el campo tiene valor)."""
        vacio = ANCHO_ROTULO_VACIO.get(_normalizar(t.texto))
        if vacio is not None:
            return (t.x2 - t.x1) < vacio * FACTOR_ROTULO_FLOTANTE
        return (t.y2 - t.y1) < ALTO_ROTULO_FLOTANTE_PX

    def _etiqueta(self, rotulo: str, lista: list[Texto]) -> Optional[Texto]:
        objetivo = _normalizar_rotulo(rotulo)
        return next((t for t in lista if _normalizar_rotulo(t.texto) == objetivo), None)

    def ubicar_campo(self, rotulo: str, lista: Optional[list[Texto]] = None) -> Optional[tuple[int, int]]:
        """Punto para tocar el campo de texto cuyo rótulo es `rotulo` ("Monto:").

        En la app del banco los campos NO tienen posición fija: un mensaje de
        validación ("Número de cuenta inválido", "Debes introducir...") baja
        todo lo de abajo ~72 px (visto 2026-10-24). Por eso se ubican por su
        rótulo en la lectura actual. Campo vacío: el rótulo está DENTRO del
        campo, en grande. Campo con valor: el rótulo sube al borde, chico, y
        el valor queda ~66 px más abajo."""
        if lista is not None:
            return self._punto_de_campo(rotulo, lista)
        lista = self.asegurar_visible(rotulo)
        return None if lista is None else self._punto_de_campo(rotulo, lista)

    def asegurar_visible(self, rotulo: str) -> Optional[list[Texto]]:
        """Devuelve una lectura donde el rótulo `rotulo` está a la vista.

        El campo puede estar fuera de pantalla (los formularios son más
        largos que ella), así que se busca y, si no está, se desplaza primero
        hacia arriba y luego hacia abajo. None si no aparece en ningún lado."""
        encontrado = self._buscar_visible(rotulo)
        return None if encontrado is None else encontrado[1]

    def _buscar_visible(self, rotulo: str) -> Optional[tuple[bytes, list[Texto]]]:
        """(captura, lectura) donde `rotulo` está a la vista. Lo normal es que
        ya lo esté y se resuelva con UNA captura, sin deslizar."""
        png, lista = self.pantalla()
        if self._punto_de_campo(rotulo, lista) is not None:
            return png, lista
        # Fuera de vista: se desliza SOLO en la dirección donde está (por el orden
        # de los campos), y de a una página grande. Antes se subía primero aunque
        # el campo estuviera abajo y se deslizaba de a 1000 px: 4-5 deslizamientos
        # de más en "otros bancos" (41 s en la 1.ª orden real, 2026-10-24).
        d = self._direccion_hacia(rotulo, lista)
        pasadas = [(1, 5)] if d > 0 else [(-1, 5)] if d < 0 else [(-1, 3), (1, 5)]
        for sentido, maximo in pasadas:
            for _ in range(maximo):
                self.deslizar_pagina(sentido)
                png, lista = self.pantalla()
                if self._punto_de_campo(rotulo, lista) is not None:
                    return png, lista
        return None

    def deslizar_pagina(self, sentido: int):
        """sentido > 0: baja en el formulario (ver lo que hay más abajo);
        sentido < 0: sube. Una página grande (1450 px) por vez."""
        if sentido > 0:
            self.deslizar(585, 1900, 585, 450)
        else:
            self.deslizar(585, 450, 585, 1900)
        time.sleep(0.3)

    @staticmethod
    def _direccion_hacia(rotulo: str, lista: list[Texto]) -> int:
        """+1 si `rotulo` está más ABAJO en el formulario que lo que se ve, -1 si
        más ARRIBA, 0 si no se puede saber (ningún campo conocido a la vista)."""
        objetivo = _normalizar(rotulo)
        if objetivo not in ORDEN_VERTICAL:
            return 0
        meta = ORDEN_VERTICAL.index(objetivo)
        vistos = [ORDEN_VERTICAL.index(n) for t in lista
                  if (n := _normalizar(t.texto)) in ORDEN_VERTICAL and VISTA_MIN_Y < t.y1 < VISTA_MAX_Y]
        if not vistos:
            return 0
        if max(vistos) < meta:
            return 1
        if min(vistos) > meta:
            return -1
        return 0

    def leer_campo(self, rotulo: str) -> Optional[str]:
        """Valor del campo `rotulo` (con el cursor borrado), desplazando el
        formulario si el campo no está a la vista. None si está vacío o no se ve."""
        encontrado = self._buscar_visible(rotulo)
        if encontrado is None:
            return None
        return self._valor_de(*encontrado, rotulo)

    def leer_campos(self, rotulos: list[str]) -> dict[str, Optional[str]]:
        """Valores de VARIOS campos de una sola captura. Los que no están a la
        vista salen None: el llamador los lee aparte con leer_campo (que desplaza)."""
        png, lista = self.pantalla()
        return {r: self._valor_de(png, lista, r) for r in rotulos}

    def _punto_de_campo(self, rotulo: str, lista: list[Texto]) -> Optional[tuple[int, int]]:
        etiqueta = self._etiqueta(rotulo, lista)
        if etiqueta is None or not (VISTA_MIN_Y < etiqueta.y1 < VISTA_MAX_Y):
            return None
        x, y = etiqueta.centro
        if self._rotulo_flotante(etiqueta):
            y += DESPLAZAMIENTO_VALOR_PX
        return x, y

    def campo_lleno(self, rotulo: str, lista: list[Texto]) -> Optional[bool]:
        """True/False según el campo tenga valor; None si no se ve el rótulo."""
        etiqueta = self._etiqueta(rotulo, lista)
        return None if etiqueta is None else self._rotulo_flotante(etiqueta)

    def valor_campo(self, rotulo: str, lista: list[Texto]) -> Optional[str]:
        """Texto escrito en el campo de rótulo `rotulo`, o None si está vacío
        o no se ve. El valor es el texto que queda justo debajo del rótulo
        flotante. No lee letras sueltas (ver leer_tipo_documento)."""
        etiqueta = self._etiqueta(rotulo, lista)
        if etiqueta is None or not self._rotulo_flotante(etiqueta):
            return None
        x_min, x_max = self._extension(etiqueta)
        debajo = [t for t in lista
                  if 0 < t.y1 - etiqueta.y2 < DESPLAZAMIENTO_VALOR_PX + 50
                  and t.x1 >= x_min and t.x2 <= x_max]
        return min(debajo, key=lambda t: t.y1).texto if debajo else None

    def _leer_tramos_cuenta(self, recorte: Image.Image, fondo: tuple[int, int, int]) -> Optional[str]:
        """Plan B para una cuenta enmascarada ("0102****0756") que el OCR no lee entera: 2026-10-30, en el
        iPhone de Asencio Jaramillo el OCR devolvía VACÍO para "0102****0756" en cualquier tamaño (con
        "0102****1161" sí) aunque la cuenta estaba bien elegida, y todas las órdenes fallaban con 'quedó en
        None'. Leyendo por separado los 4 dígitos del inicio y los 4 del final sí sale. Devuelve
        "0102****0756" o None."""
        g = recorte.convert("L")
        limite = min(g.width, 900)                     # la flecha del desplegable queda fuera
        gris_fondo = sum(fondo) / 3
        # Píxeles que se apartan mucho del fondo (letra clara en tema oscuro, oscura en tema claro).
        claras = [x for x in range(limite)
                  if any(abs(g.getpixel((x, y)) - gris_fondo) > 70 for y in range(g.height))]
        if not claras:
            return None
        x0, x1 = claras[0], claras[-1]
        ancho = x1 - x0
        if ancho < 100:
            return None

        def leer(a: int, b: int) -> str:
            sub = recorte.crop((max(0, a), 0, min(recorte.width, b), recorte.height))
            for factor in (2, 1, 3):
                ampliado = sub.resize((sub.width * factor, sub.height * factor), Image.LANCZOS)
                lienzo = Image.new("RGB", (ampliado.width + 120, ampliado.height + 120), fondo)
                lienzo.paste(ampliado, (60, 60))
                buf = io.BytesIO()
                lienzo.save(buf, "PNG")
                digitos = "".join(ch for t in self._leer_png(buf.getvalue()) for ch in t.texto if ch.isdigit())
                if len(digitos) == 4:
                    return digitos
            return ""

        izq = leer(x0 - 5, x0 + int(ancho * 0.36))
        der = leer(x1 - int(ancho * 0.36), x1 + 5)
        return f"{izq}****{der}" if izq and der else None

    def leer_valor_campo(self, rotulo: str, por_tramos: bool = False) -> Optional[str]:
        """Como valor_campo, pero sobre una captura NUEVA y con el cursor de
        texto borrado de la imagen antes del OCR.

        El campo que tiene el foco muestra una barra rosada parpadeante (la
        app la deja en "Monto:" aunque se toque afuera). Medido el
        2026-10-24: con el cursor encima, 2 de cada 8 lecturas del monto
        salían mal ("2.000,04", "2.000,09 Bs" en vez de "2.000,00 Bs") o con
        un "1" pegado ("2.000,001"). Para un monto con dinero real no sirve
        una lectura que depende del parpadeo."""
        png, lista = self.pantalla()
        return self._valor_de(png, lista, rotulo, por_tramos)

    def _valor_de(self, png: bytes, lista: list[Texto], rotulo: str, por_tramos: bool = False) -> Optional[str]:
        """Valor de `rotulo` leído de una captura YA tomada (png) y su OCR (lista)."""
        etiqueta = self._etiqueta(rotulo, lista)
        if etiqueta is None or not self._rotulo_flotante(etiqueta):
            return None
        x_min, x_max = self._extension(etiqueta)
        caja = (x_min + 30, etiqueta.y2 + 8, x_max - 30, etiqueta.y2 + 118)
        recorte = Image.open(io.BytesIO(png)).convert("RGB").crop(caja)
        fondo = _fondo_de(recorte)                      # el del tema del iPhone (oscuro o claro)
        recorte = _borrar_cursor(recorte, fondo)
        lineas = []
        # Ampliado al doble el OCR lee textos que a tamaño normal se le pierden
        # (la cuenta "0102****1022" dentro del campo no sale a 1x); si por
        # algún motivo no devuelve nada, se prueba también a tamaño normal.
        for factor in (2, 1):
            ampliado = recorte.resize((recorte.width * factor, recorte.height * factor), Image.LANCZOS)
            lienzo = Image.new("RGB", (ampliado.width + 80, ampliado.height + 80), fondo)
            lienzo.paste(ampliado, (40, 40))
            buf = io.BytesIO()
            lienzo.save(buf, "PNG")
            lineas = [Texto(t.texto, t.x1 // factor, t.y1 // factor, t.x2 // factor, t.y2 // factor)
                      for t in self._leer_png(buf.getvalue())]
            if lineas:
                break
        if not lineas:
            return self._leer_tramos_cuenta(recorte, fondo) if por_tramos else None
        # El OCR parte algunos valores en varias líneas de la MISMA fila
        # (la cuenta "0102****1022" sale como "0102" y "1022"): se unen.
        primera = min(t.y1 for t in lineas)
        fila = sorted((t for t in lineas if abs(t.y1 - primera) < 25), key=lambda t: t.x1)
        return " ".join(t.texto for t in fila)

    def valor_en_fila(self, png: bytes, lista: list[Texto], rotulo: str,
                      x_min: int = 430, x_max: int = 1110) -> Optional[str]:
        """Valor de una fila "Rótulo: ....... valor" (pantallas de revisión y
        comprobante), leído de un recorte AMPLIADO de la fila.

        El OCR de pantalla completa OMITE los números enmascarados con asteriscos
        ("0134****3794"): en la revisión de "otros bancos" la fila "Destino:"
        salía sin valor y se rechazaba una orden buena (2026-10-24). Ampliado al
        doble y al triple sí se lee."""
        etiqueta = self._etiqueta(rotulo, lista)
        if etiqueta is None:
            return None
        caja = (x_min, max(0, etiqueta.y1 - 14), x_max, etiqueta.y2 + 14)
        recorte = Image.open(io.BytesIO(png)).convert("RGB").crop(caja)
        fondo = recorte.getpixel((2, 2))
        # A 2x el OCR a veces devuelve solo una parte del número enmascarado ("0105" sin
        # el "4348", 2026-10-24): se prueban varias ampliaciones y se toma la primera que
        # trae un número completo (8+ dígitos); si ninguna, la lectura con más dígitos.
        mejor = None
        for factor in (2, 3, 4):
            ampliado = recorte.resize((recorte.width * factor, recorte.height * factor), Image.LANCZOS)
            lienzo = Image.new("RGB", (ampliado.width + 80, ampliado.height + 80), fondo)
            lienzo.paste(ampliado, (40, 40))
            buf = io.BytesIO()
            lienzo.save(buf, "PNG")
            lineas = self._leer_png(buf.getvalue())
            if not lineas:
                continue
            texto = " ".join(t.texto for t in sorted(lineas, key=lambda t: t.x1))
            digitos = sum(ch.isdigit() for ch in texto)
            if digitos >= 8:
                return texto
            if mejor is None or digitos > mejor[0]:
                mejor = (digitos, texto)
        if mejor is None or mejor[0] < 8:
            # 2026-10-30: con "0105****6287" el OCR devolvía vacío a 2x, 3x y 4x (revisión de otros
            # bancos en el iPhone de Asencio Jaramillo) y se rechazaba una orden buena. Por tramos sí sale.
            tramos = self._leer_tramos_cuenta(recorte, fondo)
            if tramos:
                return tramos
        return mejor[1] if mejor else None

    @staticmethod
    def _extension(etiqueta: Texto) -> tuple[int, int]:
        """Franja horizontal del campo. "Documento:" está en dos formularios:
        a la derecha de "Tipo:" (otros bancos) o a todo lo ancho con el botón
        de contactos al lado (Pago Móvil)."""
        clave = _normalizar(etiqueta.texto)
        if clave == "documento:":
            return (420, 1130) if etiqueta.x1 > 300 else (40, 960)
        return EXTENSION_CAMPO.get(clave, (40, 1130))

    # ── tipo de documento (V/E/P/J/G/C): letras sueltas, el OCR no las lee ──
    @staticmethod
    def _filas_letras(png: bytes) -> list[int]:
        """Centros (y) de las letras de la lista desplegable de tipo de
        documento: texto que contrasta con el fondo de la lista, en una columna angosta a la
        izquierda. Debe haber 6 (V, E, P, J, G, C). Sirve en modo OSCURO (letras blancas sobre
        gris) y en modo CLARO (letras oscuras sobre blanco): el contraste se mide contra el
        fondo de la columna, no contra un blanco fijo (iPhone en modo claro, 2026-10-29)."""
        gris = Image.open(io.BytesIO(png)).convert("L")
        x1, x2, y1, y2 = 50, 110, 300, gris.height - 100
        columna = gris.crop((x1, y1, x2, y2))
        datos = columna.load()
        valores = sorted(datos[x, y] for y in range(0, columna.height, 4) for x in range(0, columna.width, 4))
        fondo = valores[len(valores) // 2]
        grupos, inicio = [], None
        for y in range(columna.height + 1):
            blanco = y < columna.height and any(abs(datos[x, y] - fondo) > 90 for x in range(columna.width))
            if blanco and inicio is None:
                inicio = y
            elif not blanco and inicio is not None:
                # Una letra es angosta (x 60 a 91 de la captura) y no toca el borde de la columna;
                # el texto del formulario que queda detrás de la lista ("0134 - BANESCO", bordes de
                # campos) llega hasta el borde. Sin este filtro, con una letra ya elegida la lista se
                # abre más arriba, ese texto parecía una 7.ª fila, se tomaba la ventana equivocada y
                # se tocaba una fila corrida (C->G, V->G, J->P, 2026-10-29).
                xs = [x for yy in range(inicio, y) for x in range(columna.width) if abs(datos[x, yy] - fondo) > 90]
                if xs and max(xs) < columna.width - 8 and max(xs) - min(xs) <= 45:
                    grupos.append(((inicio + y - 1) // 2) + y1)
                inicio = None
        # Las letras de verdad son 6 seguidas, separadas ~144 px entre sí.
        n = len(ORDEN_TIPO_DOCUMENTO)
        for i in range(len(grupos) - n + 1):
            ventana = grupos[i:i + n]
            if all(130 <= b - a <= 160 for a, b in zip(ventana, ventana[1:])):
                return ventana
        return []

    def _recorte_tipo_documento(self, png: bytes, lista: list[Texto]) -> Optional[Image.Image]:
        etiqueta = self._etiqueta("Tipo:", lista)
        if etiqueta is None or not self._rotulo_flotante(etiqueta):
            return None
        caja = (etiqueta.x1 - 5, etiqueta.y2 + 25, etiqueta.x1 + 75, etiqueta.y2 + 105)
        return _normalizar_tema(Image.open(io.BytesIO(png)).crop(caja))

    def leer_tipo_documento(self) -> Optional[str]:
        """Letra elegida en "Tipo:", comparando el recorte del campo contra
        las plantillas de plantillas/tipo_doc_*.png (calibrar_letras.py).
        None si el campo está vacío o no se parece a ninguna."""
        png = self.captura_png()
        lista = self._leer_png(png)
        recorte = self._recorte_tipo_documento(png, lista)
        if recorte is None:
            return None
        forma = _forma_letra(recorte)
        if forma is None:
            return None                                    # campo vacío: no hay letra
        difs = []
        for letra in ORDEN_TIPO_DOCUMENTO:
            ruta = CARPETA_PLANTILLAS / f"tipo_doc_{letra}.png"
            if not ruta.exists():
                raise RuntimeError(f"falta la plantilla {ruta.name}: correr calibrar_letras.py")
            plantilla = _forma_letra(_normalizar_tema(Image.open(ruta)))
            if plantilla is None:
                continue
            difs.append((ImageStat.Stat(ImageChops.difference(forma, plantilla)).mean[0], letra))
        difs.sort()
        if not difs or difs[0][0] >= 40:
            return None
        if len(difs) > 1 and difs[1][0] - difs[0][0] < 6:
            return None                                    # dos letras casi igual de parecidas: no se adivina
        return difs[0][1]

    def elegir_tipo_documento(self, letra: str):
        """Abre "Tipo:", ubica las 6 letras por píxeles, toca la pedida y
        verifica contra las plantillas que quedó esa letra."""
        if letra not in ORDEN_TIPO_DOCUMENTO:
            raise RuntimeError(f"tipo de documento no soportado: {letra!r}")
        punto = self.ubicar_campo("Tipo:")
        if punto is None:
            raise RuntimeError("no se ve el campo 'Tipo:'")
        self.tap(*punto, que="tipo de documento", espera=0.8)
        filas = self._filas_letras(self.captura_png())
        if len(filas) != len(ORDEN_TIPO_DOCUMENTO):
            raise RuntimeError(f"la lista de tipo de documento no se abrió bien (letras vistas: {len(filas)})")
        self.tap(100, filas[ORDEN_TIPO_DOCUMENTO.index(letra)], que=f"documento {letra}", espera=0.5)
        elegida = self.leer_tipo_documento()
        if elegida != letra:
            raise RuntimeError(f"el tipo de documento quedó en {elegida!r}, se esperaba {letra!r}")

    def _leer_png(self, png: bytes) -> list[Texto]:
        return self._correr(leer_textos_async(png))

    def esperar(self, condicion, descripcion: str = "", intentos: int = 16, intervalo: float = 0.4):
        for intento in range(intentos):
            if intento:
                time.sleep(intervalo)
            resultado = condicion(self.nodos())
            if resultado:
                return resultado
        return None

    # ── acciones ────────────────────────────────────────────────────
    def _a_hid(self, x: int, y: int) -> tuple[int, int]:
        return round(x * 65535 / self.ancho), round(y * 65535 / self.alto)

    def tap(self, x: int, y: int, que: str = "", espera: float = 0.3):
        """(x, y) en píxeles de la captura (1170x2532 en el iPhone 12)."""
        print(f"{time.strftime('%H:%M:%S')}  -> TAP ({x}, {y}) {que}", flush=True)
        hx, hy = self._a_hid(x, y)

        async def _tocar(hid):
            await hid.send_touchscreen(TOUCHSCREEN_STATE_CONTACT, hx, hy, DIGITIZER_SURFACE_MAIN_TOUCHSCREEN)
            await asyncio.sleep(0.05)
            await hid.send_touchscreen(TOUCHSCREEN_STATE_RELEASE, hx, hy, DIGITIZER_SURFACE_MAIN_TOUCHSCREEN)

        self._correr(self._con_sesion(_tocar))
        time.sleep(espera)

    def tap_texto(self, rotulo: str, lista=None, exacto: bool = True, espera: float = 0.3):
        punto = self.buscar(rotulo, lista, exacto) if lista is not None else None
        if punto is None:
            punto = self.esperar(lambda l: self.buscar(rotulo, l, exacto), f"'{rotulo}' visible")
        if punto is None:
            raise RuntimeError(f"'{rotulo}' no apareció tras esperar; no se tocó nada")
        self.tap(*punto, que=f"'{rotulo}'", espera=espera)

    def deslizar(self, x1: int, y1: int, x2: int, y2: int, duracion: float = 0.6, pasos: int = 24,
                 reposo: float = 0.35):
        """Arrastra de (x1,y1) a (x2,y2) y se queda quieto `reposo` s antes de
        soltar: sin ese reposo iOS aplica inercia al soltar y la lista salta
        filas enteras (medido 2026-10-24 en el selector de banco)."""
        print(f"{time.strftime('%H:%M:%S')}  -> DESLIZAR ({x1}, {y1}) -> ({x2}, {y2})", flush=True)
        a, b = self._a_hid(x1, y1)
        c, d = self._a_hid(x2, y2)

        async def _arrastrar(hid):
            for i in range(pasos):
                t = i / pasos
                await hid.send_touchscreen(TOUCHSCREEN_STATE_CONTACT, round(a + (c - a) * t),
                                           round(b + (d - b) * t), DIGITIZER_SURFACE_MAIN_TOUCHSCREEN)
                await asyncio.sleep(duracion / pasos)
            fin = time.perf_counter() + reposo
            while time.perf_counter() < fin:
                await hid.send_touchscreen(TOUCHSCREEN_STATE_CONTACT, c, d, DIGITIZER_SURFACE_MAIN_TOUCHSCREEN)
                await asyncio.sleep(0.05)
            await hid.send_touchscreen(TOUCHSCREEN_STATE_RELEASE, c, d, DIGITIZER_SURFACE_MAIN_TOUCHSCREEN)

        self._correr(self._con_sesion(_arrastrar))

    async def _teclear(self, hid, teclas: list[tuple[int, bool]], pausa: float = 0.04):
        if self._teclado_id is None:
            self._teclado_id = await hid.create_keyboard_service()
        for uso, shift in teclas:
            if shift:
                await hid.send_keyboard(self._teclado_id, (KEY_LEFT_SHIFT,))
                await asyncio.sleep(pausa)
            await hid.send_keyboard(self._teclado_id, (KEY_LEFT_SHIFT, uso) if shift else (uso,))
            await asyncio.sleep(pausa)
            await hid.send_keyboard(self._teclado_id, ())
            await asyncio.sleep(0.02)

    def escribir(self, texto: str, oculto: bool = False, pausa: float = 0.04):
        """Solo ASCII imprimible (igual que `adb shell input text`): quitar
        tildes antes, como _nombre_para_teclear() en ejecutor.py."""
        print(f"{time.strftime('%H:%M:%S')}  -> " + ("Escribiendo texto (contenido oculto)" if oculto
                                                       else f"Escribiendo {texto!r}"), flush=True)
        teclas = []
        for ch in texto:
            if ch not in ASCII_TO_HID:
                raise RuntimeError(f"carácter no soportado por el teclado virtual: {ch!r}")
            teclas.append(ASCII_TO_HID[ch])
        self._correr(self._con_sesion(lambda hid: self._teclear(hid, teclas, pausa=pausa)), timeout=60)

    def borrar(self, veces: int = 40):
        self._correr(self._con_sesion(lambda hid: self._teclear(hid, [(KEY_BACKSPACE, False)] * veces)),
                     timeout=60)

    def enter(self):
        self._correr(self._con_sesion(lambda hid: self._teclear(hid, [(KEY_ENTER, False)])))

    def boton_inicio(self):
        async def _presionar():
            async with IndigoHIDService(self._rsd) as servicio:
                await servicio.send_button(*BOTON_INICIO, 1)
                await asyncio.sleep(0.05)
                await servicio.send_button(*BOTON_INICIO, 2)
                await asyncio.sleep(0.1)
        self._correr(_presionar())

    # ── formularios de la app del banco ─────────────────────────────
    def borrar_todo(self, maximo: int = 40):
        """Lleva el cursor al final (tecla Fin) y borra hacia atrás."""
        async def _borrar(hid):
            if self._teclado_id is None:
                self._teclado_id = await hid.create_keyboard_service()
            await hid.send_keyboard(self._teclado_id, (KEY_END,))
            await asyncio.sleep(0.04)
            await hid.send_keyboard(self._teclado_id, ())
            await self._teclear(hid, [(KEY_BACKSPACE, False)] * maximo, pausa=0.02)
        self._correr(self._con_sesion(_borrar), timeout=60)

    def llenar(self, rotulo: str, valor: str, oculto: bool = False, cerrar_teclado: bool = False,
               doble_toque: bool = False, tocar_derecha: bool = False, pausa: float = 0.04):
        """Toca el campo `rotulo`, lo vacía y escribe `valor`. Los campos se
        ubican por su rótulo en una lectura nueva (la posición cambia con los
        mensajes de validación y con el desplazamiento automático).

        `cerrar_teclado=True`: el campo "Monto:" abre un teclado NUMÉRICO en
        pantalla (sin botón "Listo") que tapa la barra de pestañas y los
        botones de abajo; se cierra con Enter.

        `tocar_derecha=True` (campo "Monto:"): su valor va pegado a la derecha con la
        máscara "2.000,00 Bs" y NO responde a la tecla Fin. Tocado a la izquierda, el
        cursor queda al principio y el retroceso no borra nada: el reintento escribía
        el monto nuevo DELANTE del viejo ('250,00' -> '250.000.250,00', 26 órdenes
        fallidas el 2026-10-26). Tocado a la derecha el cursor queda al final y el
        retroceso sí lo vacía (medido en el iPhone el 2026-10-26)."""
        encontrado = self._buscar_visible(rotulo)
        if encontrado is None:
            raise RuntimeError(f"no se ve el campo {rotulo!r}")
        lista = encontrado[1]
        punto = self._punto_de_campo(rotulo, lista)
        if tocar_derecha:
            punto = (min(self.ancho - 90, 1080), punto[1])
        # Con el campo vacío el rótulo está en grande y no hay nada que borrar:
        # saltarse el borrado ahorra ~2 s por campo (40 pulsaciones de retroceso).
        con_valor = self._rotulo_flotante(self._etiqueta(rotulo, lista))
        self.tap(*punto, que=f"campo {rotulo}", espera=0.5)
        if doble_toque:
            # Tras escribir un nombre el autocorrector de iOS ("JIMENEZ" -> "JIMÉNEZ") se
            # come el primer toque en otro campo y lo escrito caía en el campo anterior
            # (2026-10-24, cédula pegada al nombre). El 2.º toque sí enfoca.
            self.tap(*punto, que=f"campo {rotulo} (2.º toque)", espera=0.4)
        if con_valor:
            if tocar_derecha:
                self.borrar(25)          # el cursor ya está al final; Fin no funciona en este campo
            else:
                self.borrar_todo()
        self.escribir(valor, oculto=oculto, pausa=pausa)
        if cerrar_teclado:
            self.cerrar_teclado()

    @staticmethod
    def hay_teclado_numerico(lista: list[Texto]) -> bool:
        letras = {"ABC", "DEF", "GHI", "JKL", "MNO", "PQRS", "TUV", "WXYZ"}
        return sum(1 for t in lista if t.y1 > 1700 and t.texto.strip() in letras) >= 4

    def cerrar_teclado(self, lista: Optional[list[Texto]] = None) -> bool:
        """Cierra el teclado numérico si está abierto. True si había uno."""
        lista = lista if lista is not None else self.nodos()
        if not self.hay_teclado_numerico(lista):
            return False
        self.enter()
        time.sleep(0.8)
        return True

    def tab(self, nombre: str, lista: Optional[list[Texto]] = None):
        """Toca una pestaña de la barra de abajo (Inicio, Transfer, Pagos, ...)."""
        objetivo = _normalizar(nombre)
        lista = lista if lista is not None else self.nodos()
        if self.cerrar_teclado(lista):
            lista = self.nodos()
        for t in lista:
            if t.y1 > 2300 and _normalizar(t.texto).startswith(objetivo):
                self.tap(*t.centro, que=f"pestaña {nombre}", espera=0.8)
                return
        raise RuntimeError(f"no se ve la pestaña {nombre!r}")

    def elegir_opcion(self, rotulo_campo: str, opcion: str):
        """Desplegable corto con opciones legibles (ej. Tipo de instrumento)."""
        punto = self.ubicar_campo(rotulo_campo)
        if punto is None:
            raise RuntimeError(f"no se ve el campo {rotulo_campo!r}")
        self.tap(*punto, que=f"abrir {rotulo_campo}", espera=0.5)
        op = self.esperar(lambda l: self.buscar(opcion, l, True), f"'{opcion}' visible", intentos=8)
        if op is None:
            # La lista no abrió (mismo caso que los menús, 2026-10-26: "'Cuenta' no apareció
            # tras esperar"). Se toca el campo una vez más; lo elegido se verifica abajo.
            print(f"  !! la lista de {rotulo_campo} no abrió; se toca el campo otra vez")
            self.tap(*punto, que=f"abrir {rotulo_campo} (2.º intento)", espera=0.8)
            op = self.esperar(lambda l: self.buscar(opcion, l, True), f"'{opcion}' visible", intentos=10)
            if op is None:
                raise RuntimeError(f"'{opcion}' no apareció tras esperar; no se tocó nada")
        self.tap(*op, que=f"'{opcion}'", espera=0.5)
        valor = self.leer_valor_campo(rotulo_campo)
        if _normalizar(valor or "") != _normalizar(opcion):
            raise RuntimeError(f"{rotulo_campo} quedó en {valor!r}, se esperaba {opcion!r}")

    def elegir_cuenta_debitar(self, descripcion: str):
        """La lista de cuentas se abre SOBRE el campo y el OCR no lee sus
        opciones (asteriscos sobre gris). La primera opción queda justo donde
        estaba el campo; se toca ahí y se verifica por los últimos 4 dígitos."""
        digitos = "".join(ch for ch in descripcion if ch.isdigit())[-4:]
        valor = None
        for intento in range(3):
            if intento:
                time.sleep(0.6)            # tras deslizar el formulario sigue moviéndose por inercia
            punto = self.ubicar_campo("Cuenta a debitar:")
            if punto is None:
                raise RuntimeError("no se ve el campo 'Cuenta a debitar:'")
            self.tap(*punto, que="abrir Cuenta a debitar", espera=0.5)
            self.tap(*punto, que="elegir la cuenta", espera=0.5)
            valor = self.leer_valor_campo("Cuenta a debitar:", por_tramos=True)
            if digitos in (valor or ""):
                return
            if (valor or "").strip():
                break                      # eligió OTRA cuenta: no se reintenta
            # 2026-10-30: el toque cayó en el hueco bajo el campo (captura tomada con el formulario aún en
            # movimiento tras deslizar) y la orden falló con 'quedó en None'. Nada elegido: se repite.
            print("  !! la cuenta a debitar no quedó elegida (el toque cayó fuera del campo); se reintenta")
        raise RuntimeError(f"la cuenta a debitar quedó en {valor!r}, se esperaba terminada en {digitos}")

    def elegir_banco(self, codigo: str, rotulo: str = "Banco:"):
        """Abre el selector, busca la fila "CODIGO - NOMBRE" (nunca por
        posición: la lista cambia), desplazándose si hace falta, y verifica
        que el campo quedó con ese código. Ya abierto, el selector puede
        mostrar "Error al cargar, pulsa para reintentar" (error de la app,
        visto con Android): se reintenta tocándolo."""
        # El OCR confunde a veces un dígito con una letra ("O174" por "0174"): cada dígito del código
        # acepta también su letra parecida (2026-10-31: Banplus 0174 «no aparece en el selector» 3 veces).
        parecidos = {"0": "0OoQD", "1": "1lI|", "2": "2Z", "5": "5S", "6": "6G", "8": "8B"}
        cod_tolerante = "".join(f"[{parecidos[c]}]" if c in parecidos else re.escape(c) for c in codigo)
        patron = re.compile(rf"^\s*{cod_tolerante}\s*[^\w\s]")
        filas_vistas: list[str] = []
        lista_campo = self.asegurar_visible(rotulo)
        if lista_campo is None:
            raise RuntimeError(f"no se ve el campo {rotulo!r}")
        punto = self._punto_de_campo(rotulo, lista_campo)
        # Con el campo VACÍO la lista abre arriba del todo: basta UNA pasada hacia
        # abajo. Con un banco ya elegido abre centrada en él: hacen falta las dos
        # (antes siempre se barría hacia abajo Y hacia arriba: ~8 deslizamientos de
        # más cuando el banco no estaba, 2026-10-24).
        sentidos = (-1,) if not self._rotulo_flotante(self._etiqueta(rotulo, lista_campo)) else (-1, 1)
        self.tap(*punto, que=f"abrir {rotulo}", espera=0.6)

        def fila_de(lista):
            return next((t for t in lista if patron.match(t.texto) and 200 < t.centro[1] < 2240), None)

        def es_fila_de_banco(t):
            return re.match(r"^\s*[\dOoQDlI|SZGB]{4}\s*[^\w\s]", t.texto) is not None

        reintentos_carga = 0
        for sentido in sentidos:                     # primero hacia abajo, luego (si hace falta) hacia arriba
            previa = None
            for _ in range(10):
                lista = self.nodos()
                error = next((t for t in lista if "error al cargar" in _normalizar(t.texto)), None)
                if error is not None and reintentos_carga < 16:
                    reintentos_carga += 1
                    self.tap(*error.centro, que="reintentar carga de bancos", espera=1.0)
                    continue
                fila = fila_de(lista)
                if fila is not None:
                    self.tap(*fila.centro, que=f"banco {codigo}", espera=0.6)
                    valor = self.leer_valor_campo(rotulo)
                    if not patron.match(valor or ""):
                        raise RuntimeError(f"el banco quedó en {valor!r}, se esperaba el código {codigo}")
                    return
                filas = [t.texto for t in lista if es_fila_de_banco(t)]
                filas_vistas.extend(f for f in filas if f not in filas_vistas)
                if not filas:
                    time.sleep(0.5)                  # la lista todavía no dibuja
                    continue
                if filas == previa:
                    break                            # el desplazamiento ya no mueve: se llegó al borde
                previa = filas
                # 1650 px por pasada (la lista muestra ~2000 px): queda traslape de
                # sobra y se llega a cualquier banco con la mitad de deslizamientos.
                self.deslizar(585, 2000 if sentido < 0 else 350, 585, 350 if sentido < 0 else 2000)
                time.sleep(0.4)
        codigos_vistos = ", ".join(sorted({f.strip()[:4] for f in filas_vistas}))
        raise RuntimeError(f"el banco (código {codigo}) no aparece en el selector "
                           f"(la lista mostró {len(filas_vistas)} bancos: {codigos_vistos or 'ninguno'})")

    def _esperar_texto(self, rotulo: str, intentos: int = 12, intervalo: float = 0.5, exacto: bool = True):
        return self.esperar(lambda l: l if self.existe(rotulo, l, exacto) else None, rotulo,
                            intentos=intentos, intervalo=intervalo)

    def _abrir_menu(self, pestana: str, texto_del_menu: str) -> list[Texto]:
        """Toca la pestaña y espera su menú. Si no abre, la toca UNA vez más: justo
        después de un pago (cuando llega la notificación del banco) el primer toque a
        veces se pierde y la orden fallaba con "no se abrió el menú" (2026-10-26)."""
        nombres = {"Transfer": "Transferencias"}
        for intento in range(2):
            self.tab(pestana)
            lista = self._esperar_texto(texto_del_menu, intentos=8)
            if lista is not None:
                return lista
            if intento == 0:
                print(f"  !! el menú de {nombres.get(pestana, pestana)} no abrió; se toca la pestaña otra vez")
        raise RuntimeError(f"no se abrió el menú de {nombres.get(pestana, pestana)}")

    def abrir_pago_movil(self):
        """Pestaña Pagos -> "PagomóvilBDV". En Inicio el historial del fondo
        también tiene filas "PagomóvilBDV", así que la opción del menú se
        distingue por estar justo encima de "PagomóvilBDV QR" (solo existe en
        el menú), y se confirma que abrió el formulario."""
        lista = self._abrir_menu("Pagos", "PagomóvilBDV QR")
        qr = next(t for t in lista if _normalizar(t.texto) == "pagomovilbdv qr")
        candidatos = [t for t in lista if _normalizar(t.texto) == "pagomovilbdv" and 100 < qr.y1 - t.y1 < 260]
        if not candidatos:
            raise RuntimeError("no se encontró la opción PagomóvilBDV en el menú de Pagos")
        self.tap(*candidatos[0].centro, que="'PagomóvilBDV'", espera=0.6)
        if self._esperar_texto("Teléfono:", exacto=True) is None:
            raise RuntimeError("no abrió el formulario de Pago Móvil")

    def abrir_transferencia(self, opcion: str):
        """Pestaña Transferencias -> "Cuentas de terceros" | "Cuentas otros bancos"."""
        self._abrir_menu("Transfer", "Cuentas propias")
        self.tap_texto(opcion, espera=0.6)
        if self._esperar_texto("Cuenta a debitar:") is None:
            raise RuntimeError(f"no abrió el formulario de {opcion}")

    def desplazar_hasta(self, rotulo: str, maximo: int = 4) -> list[Texto]:
        """Desplaza el formulario hacia abajo hasta que `rotulo` quede visible
        (y por encima de la barra de pestañas). Devuelve la lectura donde se vio.
        `deslizamientos_hacia_abajo` queda con cuántas veces hubo que deslizar."""
        self.deslizamientos_hacia_abajo = 0
        for _ in range(maximo + 1):
            lista = self.nodos()
            if self.hay_teclado_numerico(lista):
                self.cerrar_teclado(lista)
                lista = self.nodos()
            punto = self.buscar(rotulo, lista)
            if punto is not None and punto[1] < 2230:
                return lista
            self.deslizar_pagina(1)
            self.deslizamientos_hacia_abajo += 1
        raise RuntimeError(f"'{rotulo}' no apareció al desplazar el formulario")

    def ir_arriba(self, veces: int = 3):
        for _ in range(veces):
            self.deslizar(585, 700, 585, 1900)
            time.sleep(0.3)

    def limpiar_formulario(self):
        """Toca "Limpiar" aunque esté debajo del borde y deja el formulario
        otra vez desde arriba. La app NO resetea sola los formularios al
        volver a entrar (visto con Android el 2026-09-30)."""
        lista = self.desplazar_hasta("Limpiar")
        self.tap(*self.buscar("Limpiar", lista), que="'Limpiar'", espera=0.5)
        # No se sube aquí: el siguiente campo que se busque (asegurar_visible)
        # sube solo lo que haga falta, sin deslizar a ciegas.

    def limpiar_si_hace_falta(self, rotulo_tope: str) -> bool:
        """Para formularios donde elegir `rotulo_tope` (el "Tipo de instrumento:")
        REINICIA todo lo de abajo (medido el 2026-10-24: Nombre, Documento, Banco e
        Instrumento quedaron vacíos tras cambiar el tipo). Si ese campo está vacío el
        formulario ya está limpio y no se toca "Limpiar" (que en "otros bancos" está
        debajo del borde y costaba dos deslizamientos). Con un valor viejo SÍ se limpia:
        volver a elegir el MISMO tipo podría no reiniciar nada. True si limpió."""
        encontrado = self._buscar_visible(rotulo_tope)
        if encontrado is not None:
            etiqueta = self._etiqueta(rotulo_tope, encontrado[1])
            if etiqueta is not None and not self._rotulo_flotante(etiqueta):
                return False
        self.limpiar_formulario()
        return True

    def esta_en_inicio(self, lista: list[Texto]) -> bool:
        return any(_normalizar(t.texto) in ("saldo cuenta ahorro", "historico de operaciones") for t in lista)
