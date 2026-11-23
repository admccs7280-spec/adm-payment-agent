"""Registro de iPhone (iphones.json) y cambio de teléfono, SIN teléfono ni Telegram. Debe decir "TODO OK".

    iphone\\.venv\\Scripts\\python.exe tests\\test_registro_iphones.py
"""
import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "iphone"))
os.environ["DISPOSITIVO"] = "iphone"
os.environ["CUENTA_DEBITAR_DESC"] = "0102****1022"
os.environ["BDV_PASSWORD"] = "ClaveEnv1"

import registro_iphones as R  # noqa: E402
R.ARCHIVO = Path(tempfile.mkdtemp(prefix="test_reg_")) / "iphones.json"      # nunca el iphones.json real
import ejecutor_iphone as E  # noqa: E402

UDID_A, UDID_B, UDID_C = "00008101-AAAA", "00008101-BBBB", "00008101-CCCC"


def comprobar(nombre, condicion):
    print(("ok    " if condicion else "FALLA ") + nombre)
    if not condicion:
        sys.exit(1)


# 1) el archivo
R.guardar(UDID_A, nombre="Ricardo", cuenta="0102****1022", clave="Cl@ve1")
R.guardar(UDID_B, nombre="Nathalie", clave="OtraClave2")                    # sin cuenta todavía
comprobar("se guarda y se busca por UDID (sin distinguir mayúsculas)",
          R.buscar(UDID_A.lower())["nombre"] == "Ricardo" and R.buscar(UDID_B)["cuenta"] == "")
R.guardar(UDID_B, cuenta="0102****7788")                                    # solo cambia la cuenta
comprobar("actualizar un campo no borra los demás", R.buscar(UDID_B)["clave"] == "OtraClave2"
          and R.buscar(UDID_B)["cuenta"] == "0102****7788")
comprobar("problemas: sin clave / '#' / cuenta mal escrita",
          R.problemas({"clave": ""}) and R.problemas({"clave": "a#b"}) and R.problemas({"clave": "x", "cuenta": "1022"})
          and not R.problemas({"clave": "x", "cuenta": "0102****1022"}))

# 1b) plantilla del zip: nombre y cuenta, SIN clave; nunca pisa lo ya registrado
_plantilla = R.ARCHIVO.with_name("iphones.plantilla.json")
_plantilla.write_text('{"00008101-AAAA": {"nombre": "Otro", "cuenta": "0102****0000", "clave": ""},'
                      ' "00008101-NUEVO": {"nombre": "Simon", "cuenta": "0102****7983", "clave": ""}}', encoding="utf-8")
_agregados = R.importar_plantilla(_plantilla)
comprobar("importar plantilla agrega solo el teléfono que falta", _agregados == ["00008101-NUEVO"])
comprobar("importar plantilla no pisa nombre, cuenta ni clave de uno ya registrado",
          R.buscar(UDID_A)["nombre"] == "Ricardo" and R.buscar(UDID_A)["clave"] == "Cl@ve1"
          and R.buscar(UDID_A)["cuenta"] == "0102****1022")
comprobar("el teléfono importado queda sin clave y el bot no paga con él",
          R.buscar("00008101-NUEVO")["clave"] == "" and "falta la clave de operaciones" in R.problemas(R.buscar("00008101-NUEVO")))
comprobar("importar dos veces no agrega nada", R.importar_plantilla(_plantilla) == [])

# 2) cada iPhone usa SU cuenta y SU clave
E.IDENTIDAD_IPHONE.update(udid=UDID_A, nombre="iPhone")
E._resolver_perfil(E.IDENTIDAD_IPHONE)
p = E.perfil_activo()
comprobar("iPhone registrado: usa su cuenta y su clave, no las del .env",
          (p["nombre"], p["cuenta"], p["clave"], p["origen"]) == ("Ricardo", "0102****1022", "Cl@ve1", "registro"))
E.IDENTIDAD_IPHONE.update(udid=UDID_B)
E._resolver_perfil(E.IDENTIDAD_IPHONE)
comprobar("otro iPhone registrado: otra cuenta y otra clave",
          (E.perfil_activo()["cuenta"], E.perfil_activo()["clave"]) == ("0102****7788", "OtraClave2"))
E.IDENTIDAD_IPHONE.update(udid=UDID_C, nombre="iPhone nuevo")
E._resolver_perfil(E.IDENTIDAD_IPHONE)
comprobar("iPhone sin registrar: lo del .env", E.perfil_activo()["origen"] == ".env"
          and E.perfil_activo()["clave"] == "ClaveEnv1")
R.guardar(UDID_C, nombre="Sin clave")
try:
    E._resolver_perfil(dict(udid=UDID_C, nombre="x"))
    comprobar("registrado sin clave: el bot NO arranca", False)
except E.ConfiguracionIPhone as exc:
    comprobar("registrado sin clave: el bot NO arranca y dice qué falta", "falta la clave" in str(exc) and "Teléfonos" in str(exc))
R.quitar(UDID_C)


# 3) arranque con un iPhone: verifica la cuenta, aprende la que falta y registra al nuevo
class CtrlFalso:
    def __init__(self, cuenta_en_telefono):
        self.cuenta = cuenta_en_telefono

    def abrir_transferencia(self, *_a): pass
    def limpiar_formulario(self, *_a): pass

    def elegir_cuenta_debitar(self, esperada):
        digitos = esperada[-4:]
        if digitos not in self.cuenta:
            raise RuntimeError(f"la cuenta a debitar quedó en {self.cuenta!r}, se esperaba terminada en {digitos}")


E._volver_inicio = lambda ctrl, *a, **k: None
registro_log = []

# 3a) registrado SIN cuenta: se lee del teléfono y se guarda
E.IDENTIDAD_IPHONE.clear(); E.IDENTIDAD_IPHONE.update(udid="00008101-DDDD", nombre="iPhone")
R.guardar("00008101-DDDD", nombre="Simon", clave="ClaveSimon")
E._resolver_perfil(E.IDENTIDAD_IPHONE)
E.leer_cuenta_del_iphone = lambda ctrl: "0102****9911"
E._preparar_app(CtrlFalso("0102****9911"), registro_log.append)
comprobar("registrado sin cuenta: la lee del iPhone y la guarda",
          R.buscar("00008101-DDDD")["cuenta"] == "0102****9911" and E.perfil_activo()["cuenta"] == "0102****9911")

# 3b) iPhone NUEVO sin registrar cuya cuenta es la del .env: queda registrado solo
E.IDENTIDAD_IPHONE.clear(); E.IDENTIDAD_IPHONE.update(udid="00008101-EEEE", nombre="iPhone de Luis")
E._resolver_perfil(E.IDENTIDAD_IPHONE)
E._preparar_app(CtrlFalso("0102****1022"), registro_log.append)
reg = R.buscar("00008101-EEEE")
comprobar("iPhone nuevo con la cuenta del .env: queda registrado solo", reg and reg["cuenta"] == "0102****1022"
          and reg["clave"] == "ClaveEnv1" and reg["nombre"] == "iPhone de Luis")

# 3c) iPhone nuevo con OTRA cuenta que la del .env: no arranca y manda a registrarlo
E.IDENTIDAD_IPHONE.clear(); E.IDENTIDAD_IPHONE.update(udid="00008101-FFFF", nombre="iPhone")
E._resolver_perfil(E.IDENTIDAD_IPHONE)
try:
    E._preparar_app(CtrlFalso("0102****5555"), registro_log.append)
    comprobar("iPhone nuevo con otra cuenta: NO arranca", False)
except E.ConfiguracionIPhone as exc:
    comprobar("iPhone nuevo con otra cuenta: NO arranca y manda a registrarlo",
              "NO está registrado" in str(exc) and "5555" in str(exc) and "Registrar" in str(exc))

# 3d) registrado, pero el iPhone tiene OTRA cuenta que la registrada: no arranca
E.IDENTIDAD_IPHONE.clear(); E.IDENTIDAD_IPHONE.update(udid=UDID_A, nombre="iPhone")
E._resolver_perfil(E.IDENTIDAD_IPHONE)
try:
    E._preparar_app(CtrlFalso("0102****0001"), registro_log.append)
    comprobar("cuenta distinta a la registrada: NO arranca", False)
except E.ConfiguracionIPhone as exc:
    comprobar("cuenta distinta a la registrada: NO arranca y nombra al iPhone",
              "Ricardo" in str(exc) and "registrada" in str(exc))

# 4) ejecutar_pago: se cambia de iPhone y la orden se paga con la cuenta y la clave del NUEVO
usados = {}


def pagar_falso(orden, cuenta, ctrl, clave, por_transferencia):
    usados.update(cuenta=cuenta, clave=clave)
    return E.ResultadoPago(exito=True, incierto=False, mensaje="ok", referencia="r", ruta_captura="x.png")


E._pagar_con_reintento_clave = pagar_falso
E._controlador = lambda udid=None: object()
E._base.modo_transferencia_activo = lambda: False
orden = E.OrdenPago(id_orden="O1", tipo="PAGO_MOVIL", numero_orden="O1", tasa="1", banco="Banco de Venezuela",
                    documento="23798062", nombre="X", monto="1,00", remitente_id=1, mensaje_id=1, telefono="04243468273")


def reidentificar_a(udid):
    def f(log=print):
        E.IDENTIDAD_IPHONE.clear(); E.PERFIL_ACTIVO.clear()
        E.IDENTIDAD_IPHONE.update(udid=udid, nombre="iPhone")
        E._resolver_perfil(E.IDENTIDAD_IPHONE)
        return "listo"
    return f


E.IDENTIDAD_IPHONE.clear(); E.PERFIL_ACTIVO.clear()                           # el iPhone anterior se desconectó
E._reidentificar = reidentificar_a(UDID_B)
r = E.ejecutar_pago(orden, "UDID-VIEJO-EN-ENV", "clave-del-env")
comprobar("iPhone cambiado: reconoce el nuevo y paga con SU cuenta y SU clave (no con el UDID viejo ni el .env)",
          r.exito and usados == {"cuenta": "0102****7788", "clave": "OtraClave2"})


def reidentificar_falla(log=print):
    raise E.ConfiguracionIPhone("el iPhone «X» NO está registrado")


E.IDENTIDAD_IPHONE.clear(); E.PERFIL_ACTIVO.clear(); usados.clear()
E._reidentificar = reidentificar_falla
r = E.ejecutar_pago(orden, None, "x")
comprobar("iPhone nuevo sin registrar: la orden NO se intenta (no se toca el teléfono) y lo dice",
          not r.exito and not r.incierto and "NO SE INTENTÓ" in r.mensaje and not usados)

# 5) un iPhone desconectado a media orden se olvida: la siguiente orden reconoce el que haya
E._pagar_con_reintento_clave = lambda *a: E.ResultadoPago(
    exito=False, incierto=False, mensaje="Falló antes de confirmar el pago: Connection was terminated abruptly.",
    referencia=None, ruta_captura="x.png")
E.IDENTIDAD_IPHONE.clear(); E.IDENTIDAD_IPHONE.update(udid=UDID_A, nombre="iPhone")
E._resolver_perfil(E.IDENTIDAD_IPHONE)
E.ejecutar_pago(orden, None, "x")
comprobar("conexión perdida (iPhone desconectado): olvida el iPhone para reconocer el siguiente", not E.IDENTIDAD_IPHONE)

print("\nTODO OK")
