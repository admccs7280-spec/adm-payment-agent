"""Prueba SIN iPhone del reconocimiento/preparación de un iPhone y del arranque. Debe terminar en
"TODO OK".

    iphone\\.venv\\Scripts\\python.exe tests\\test_dispositivo.py

Cubre lo que pasó el 2026-10-29 al conectar un iPhone nuevo: faltaba la imagen de desarrollador, la
conexión fallaba a medias, el túnel quedaba abierto y TODOS los reintentos fallaban con "A userspace
tunnel is already active in this process" hasta que el bot se cerraba a los 10 minutos.
"""
import asyncio
import io
import os
import sys
import threading
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "iphone"))
os.environ["DISPOSITIVO"] = "iphone"
os.environ["CUENTA_DEBITAR_DESC"] = "0102****6449"

import registro_iphones as _reg  # noqa: E402
import tempfile as _tf  # noqa: E402
_reg.ARCHIVO = Path(_tf.mkdtemp(prefix="test_reg_")) / "iphones.json"      # nunca el iphones.json real

import dispositivo as D  # noqa: E402
import ejecutor_iphone as E  # noqa: E402
import iphone_utils as IU  # noqa: E402
from PIL import Image  # noqa: E402


def comprobar(nombre, condicion):
    print(("ok    " if condicion else "FALLA ") + nombre)
    if not condicion:
        sys.exit(1)


# ── 1) listar / elegir iPhone ───────────────────────────────────────────────
SALIDA_USBMUX = """[
    {"BuildVersion": "24A446", "ConnectionType": "USB", "DeviceClass": "iPhone", "DeviceName": "iPhone",
     "Identifier": "00008101-0016092A3608001E", "ProductType": "iPhone13,2", "ProductVersion": "27.0.1",
     "UniqueDeviceID": "00008101-0016092A3608001E"}
]"""
OTRO = """{"DeviceClass": "iPhone", "DeviceName": "iPhone de Adrian", "ProductType": "iPhone13,2",
          "ProductVersion": "27.0.1", "UniqueDeviceID": "00008101-001978E836C0001E"}"""
llamadas: list = []
respuestas: dict = {}


def pmd3_falso(args, timeout=90):
    llamadas.append(args)
    for clave, valor in respuestas.items():
        if clave in " ".join(args):
            return valor
    return 0, ""


original_pmd3 = D._pmd3
D._pmd3 = pmd3_falso

respuestas.clear()
respuestas["usbmux list"] = (0, "2026-10-29 INFO algo\n" + SALIDA_USBMUX)
lista = D.listar_iphones()
comprobar("listar_iphones lee el UDID, nombre e iOS", lista == [{"udid": "00008101-0016092A3608001E",
          "nombre": "iPhone", "ios": "27.0.1", "modelo": "iPhone13,2"}])
comprobar("elegir: el ÚNICO iPhone conectado, sin UDID configurado", D.elegir("")["udid"].endswith("3608001E"))

respuestas["usbmux list"] = (0, "[]")
try:
    D.elegir("")
    comprobar("elegir sin iPhone da un mensaje claro", False)
except D.DispositivoNoListo as exc:
    comprobar("elegir sin iPhone da un mensaje claro", "ningún iPhone" in str(exc) and "Confiar" in str(exc))

respuestas["usbmux list"] = (0, SALIDA_USBMUX.rstrip("]").rstrip() + ",\n" + OTRO + "\n]")
try:
    D.elegir("")
    comprobar("con 2 iPhone y sin UDID NO elige uno (no se paga desde un teléfono que nadie eligió)", False)
except D.DispositivoNoListo as exc:
    comprobar("con 2 iPhone y sin UDID NO elige uno (no se paga desde un teléfono que nadie eligió)",
              "2 iPhone" in str(exc) and "UDID" in str(exc))
comprobar("con 2 iPhone y UDID configurado elige ese", D.elegir("00008101-001978E836C0001E")["nombre"] == "iPhone de Adrian")
try:
    D.elegir("00008101-AAAAAAAAAAAAAAAA")
    comprobar("UDID configurado que ya no está conectado: avisa qué hay conectado", False)
except D.DispositivoNoListo as exc:
    comprobar("UDID configurado que ya no está conectado: avisa qué hay conectado y que se cambió de iPhone",
              "no está conectado" in str(exc) and "iPhone de Adrian" in str(exc) and "borra el UDID" in str(exc))

# ── 2) preparar: iOS, Modo de desarrollador, imagen ─────────────────────────
respuestas.clear()
respuestas["usbmux list"] = (0, SALIDA_USBMUX)
respuestas["developer-mode-status"] = (0, "2026-10-29 INFO x\ntrue")
respuestas["auto-mount"] = (0, "INFO DeveloperDiskImage mounted successfully")
llamadas.clear()
ident = D.preparar("", log=lambda *_a: None)
comprobar("preparar con iPhone nuevo: monta la imagen (con SU udid) y devuelve la identidad",
          ident["imagen"] == "montada" and ["mounter", "auto-mount", "--udid", "00008101-0016092A3608001E"] in llamadas)
respuestas["auto-mount"] = (0, "INFO DeveloperDiskImage already mounted; to replace it...")
comprobar("preparar con la imagen ya montada no falla", D.preparar("", log=lambda *_a: None)["imagen"] == "ya estaba montada")

respuestas["developer-mode-status"] = (0, "false")
llamadas.clear()
try:
    D.preparar("", log=lambda *_a: None)
    comprobar("Modo de desarrollador apagado: se niega y dice dónde activarlo", False)
except D.DispositivoNoListo as exc:
    comprobar("Modo de desarrollador apagado: se niega, dice dónde activarlo y hace visible la opción",
              "Modo de desarrollador" in str(exc) and "APAGADO" in str(exc)
              and any(a[:2] == ["amfi", "reveal-developer-mode"] for a in llamadas))
respuestas["developer-mode-status"] = (0, "true")
respuestas["usbmux list"] = (0, SALIDA_USBMUX.replace("27.0.1", "26.4"))
try:
    D.preparar("", log=lambda *_a: None)
    comprobar("iOS menor a 27 se rechaza", False)
except D.DispositivoNoListo as exc:
    comprobar("iOS menor a 27 se rechaza con el motivo", "iOS 26.4" in str(exc) and "27" in str(exc))
D._pmd3 = original_pmd3
comprobar("error de usbmuxd se explica (falta iTunes)", "iTunes" in D.explicar_error("Failed to connect to usbmuxd socket"))
comprobar("error de Modo de desarrollador se explica", "Modo de desarrollador" in D.explicar_error("Developer Mode is disabled"))

# ── 3) la conexión fallida NO deja el túnel abierto ────────────────────────
png = io.BytesIO()
Image.new("RGB", (1170, 2532)).save(png, "PNG")
PNG = png.getvalue()
estado = {"activo": False, "abiertos": 0, "cerrados": 0, "falla": True}


class TunelFalso:
    def __init__(self, serial=None):
        self.serial = serial

    async def aopen(self):
        if estado["activo"]:
            raise RuntimeError("A userspace tunnel is already active in this process")
        estado["activo"] = True
        estado["abiertos"] += 1
        return object()

    async def aclose(self):
        estado["activo"] = False
        estado["cerrados"] += 1


class CapturaFalsa:
    def __init__(self, rsd):
        pass

    async def __aenter__(self):
        if estado["falla"]:
            raise RuntimeError("InvalidService: no está montada la imagen de desarrollador")
        return self

    async def __aexit__(self, *a):
        return False

    async def capture_screenshot(self):
        return {"image": PNG}


class HidFalso:
    async def create_keyboard_service(self):
        return 1


class SesionFalsa:
    def __init__(self, rsd):
        pass

    async def __aenter__(self):
        return HidFalso()

    async def __aexit__(self, *a):
        return False


IU.UserspaceRsdTunnel, IU.ScreenCaptureService, IU.touch_session = TunelFalso, CapturaFalsa, SesionFalsa
IU.ESPERA_AUTORIZACION_TOQUES_SEG = 0
for intento in range(1, 4):                       # como el bot: reintenta aunque falle
    try:
        IU.ControladorIPhone("00008101-0016092A3608001E")
        comprobar("(no debía conectar)", False)
    except RuntimeError as exc:
        comprobar(f"conexión fallida {intento}: error real ('InvalidService'), NO 'túnel ya activo'",
                  "InvalidService" in str(exc) and "already active" not in str(exc))
comprobar("cada conexión fallida cierra su túnel", estado["abiertos"] == 3 and estado["cerrados"] == 3
          and not estado["activo"])
estado["falla"] = False                            # el iPhone ya tiene la imagen montada
ctrl = IU.ControladorIPhone("00008101-0016092A3608001E")
comprobar("tras montar la imagen la siguiente conexión funciona (1170x2532)", (ctrl.ancho, ctrl.alto) == (1170, 2532)
          and estado["activo"])
ctrl.cerrar()
comprobar("cerrar() libera el túnel", not estado["activo"])
time.sleep(0.3)
comprobar("los hilos de conexiones fallidas se detienen",
          sum(1 for t in threading.enumerate() if t.name == "iphone-loop") == 0)

# ── 4) arranque: esperar_dispositivo_listo ────────────────────────────────
E.time.sleep, dormir = (lambda s: None), E.time.sleep
guardado = (E.dispositivo.preparar, E._controlador, E._preparar_app, E._descartar_controlador)
try:
    IDENT = {"udid": "00008101-0016092A3608001E", "nombre": "iPhone", "ios": "27.0.1", "imagen": "montada"}
    pasos = []

    def preparar_falso(udid, log):
        pasos.append("preparar")
        if len(pasos) < 3:
            raise D.DispositivoNoListo("el Modo de desarrollador del iPhone «iPhone» está APAGADO")
        return dict(IDENT)

    class CtrlFalso:
        def nodos(self):
            return ["algo"]

    E.dispositivo.preparar = preparar_falso
    E._controlador = lambda udid=None: (pasos.append(("controlador", udid)), CtrlFalso())[1]
    E._preparar_app = lambda ctrl, log=print: None
    E._descartar_controlador = lambda: None
    avisos = []
    r = E.esperar_dispositivo_listo("", 600, 0, avisos.append)
    comprobar("arranque con iPhone nuevo: espera a que se active el Modo de desarrollador y sigue solo",
              "iPhone, iOS 27.0.1" in r and pasos.count("preparar") == 3)
    comprobar("la conexión se fija al UDID reconocido (no a 'el que haya')",
              ("controlador", "00008101-0016092A3608001E") in pasos and E.IDENTIDAD_IPHONE["udid"].endswith("3608001E"))

    # sesión cerrada al arrancar: espera y sigue cuando se inicia sesión
    intentos_app = []

    def preparar_app_falso(ctrl, log=print):
        intentos_app.append(1)
        if len(intentos_app) < 3:
            raise E.SesionCerrada(E.MENSAJE_SESION_CERRADA)

    E._preparar_app = preparar_app_falso
    E.dispositivo.preparar = lambda udid, log: dict(IDENT)
    r = E.esperar_dispositivo_listo("", 600, 0, lambda *_a: None)
    comprobar("app BDV sin sesión al arrancar: ESPERA a que la inicies (no se cierra el bot)", len(intentos_app) == 3 and "iPhone" in r)

    # cuenta distinta en el iPhone
    E._preparar_app = guardado[2]
    cuentas = {"valor": "0102 1022"}

    class CtrlCuenta:
        def nodos(self):
            return ["algo"]

        def abrir_transferencia(self, op):
            pass

        def limpiar_formulario(self):
            pass

        def elegir_cuenta_debitar(self, desc):
            raise RuntimeError(f"la cuenta a debitar quedó en {cuentas['valor']!r}, se esperaba terminada en 6449")

    E._controlador = lambda udid=None: CtrlCuenta()
    E._volver_inicio = lambda ctrl, *a, **k: None
    try:
        E.esperar_dispositivo_listo("", 600, 0, lambda *_a: None)
        comprobar("cuenta del iPhone distinta a la del .env: el bot NO arranca", False)
    except E.ConfiguracionIPhone as exc:
        comprobar("cuenta del iPhone distinta a la del .env: el bot NO arranca y dice qué corregir",
                  "0102 1022" in str(exc) and "0102****6449" in str(exc) and "Teléfonos" in str(exc))
    cuentas["valor"] = None                       # no se pudo leer la cuenta: no se bloquea, se verifica en cada orden
    guardados_log = []
    E._controlador = lambda udid=None: CtrlCuenta()
    try:
        E._preparar_app(CtrlCuenta(), guardados_log.append)
        comprobar("cuenta ilegible al arrancar: avisa y sigue (se verifica en cada orden)",
                  any("no se pudo verificar la cuenta" in a for a in guardados_log))
    except E.ConfiguracionIPhone:
        comprobar("cuenta ilegible al arrancar: avisa y sigue", False)
finally:
    E.time.sleep = dormir
    E.dispositivo.preparar, E._controlador, E._preparar_app, E._descartar_controlador = guardado

print("\nTODO OK")
