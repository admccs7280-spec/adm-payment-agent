"""Pruebas sin teléfono: lógica de lectura de pantallas con listas de texto simuladas.
Correr con el python de iphone/.venv:  python iphone/test_ejecutor_iphone.py"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("CUENTA_DEBITAR_DESC", "0102****0000")   # la prueba no depende del .env de la PC
import tempfile as _tf, pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parent.parent))
import registro_iphones as _reg                                      # nunca el iphones.json real
_reg.ARCHIVO = _pl.Path(_tf.mkdtemp(prefix="test_reg_")) / "iphones.json"

import ejecutor_iphone as E
E.IDENTIDAD_IPHONE.update(udid="TEST-UDID", nombre="iPhone de prueba")   # ya reconocido: ejecutar_pago no re-identifica
from iphone_utils import Texto
from ordenes import OrdenPago


def T(texto, x1=100, y1=100, x2=300, y2=140):
    return Texto(texto, x1, y1, x2, y2)


def orden_pm(**kw):
    base = dict(id_orden="X", tipo="PAGO_MOVIL", numero_orden="X", tasa="0", banco="Banco de Venezuela",
                documento="31899383", nombre="N", monto="100,00", remitente_id=0, mensaje_id=0,
                telefono="04243468273")
    base.update(kw)
    return OrdenPago(**base)


REVISION_PM = [T("Confirmar Operación", 273, 199, 896, 257), T("PagomóvilBDV Personas", 287, 399, 885, 452),
               T("100,00 Bs", 467, 567, 707, 613), T("Identificación:", 99, 711, 351, 745),
               T("31899383", 875, 714, 1071, 744), T("Destino:", 99, 807, 249, 838),
               T("04243468273", 799, 807, 1071, 837), T("Banco:", 99, 900, 221, 931),
               T("0102 - BANCO DE VENEZUELA", 468, 900, 1073, 930), T("Confirmar", 481, 1201, 691, 1235)]

fallos = []


def comprobar(nombre, condicion):
    print(("ok   " if condicion else "FALLA"), nombre)
    if not condicion:
        fallos.append(nombre)


class CtrlFalso:  # _verificar_revision solo lo usa para releer si hay discrepancia
    def __init__(self, lectura):
        self.lectura = lectura

    def nodos(self):
        return self.lectura


# ── revisión ──
comprobar("revisión PM correcta", E._verificar_revision(CtrlFalso(REVISION_PM), REVISION_PM, orden_pm()) is REVISION_PM)
for nombre, orden in [("monto distinto", orden_pm(monto="1.000,00")), ("teléfono distinto", orden_pm(telefono="04141111111")),
                      ("banco distinto", orden_pm(banco="Mercantil"))]:
    try:
        E._verificar_revision(CtrlFalso(REVISION_PM), REVISION_PM, orden)
        comprobar(f"revisión rechaza {nombre}", False)
    except RuntimeError as exc:
        comprobar(f"revisión rechaza {nombre}", "no coincide" in str(exc))
# La cédula NO es dato de pago (desde el 2026-10-24): una distinta avisa en la consola pero no bloquea.
comprobar("revisión PM con cédula distinta NO bloquea (solo avisa)",
          E._verificar_revision(CtrlFalso(REVISION_PM), REVISION_PM, orden_pm(documento="12345678")) is REVISION_PM)
mon_miles = [T("2.500,00 Bs") if t.texto == "100,00 Bs" else t for t in REVISION_PM]
comprobar("revisión con separador de miles", E._verificar_revision(CtrlFalso(mon_miles), mon_miles, orden_pm(monto="2.500,00")) is mon_miles)

# ── diálogos ──
ERROR = [T("Error", 400, 800, 600, 840), T("El monto introducido es inválido", 300, 900, 800, 940), T("Aceptar", 400, 1100, 600, 1140)]
comprobar("diálogo Error: texto", E._mensaje_error(ERROR) == "El monto introducido es inválido")
comprobar("Error es aviso de límite de Pago Móvil", E._es_aviso_limite_pm(E._mensaje_error(ERROR)))
ATENCION = [T("Atención", 400, 800, 600, 840), T("Error al autenticar con contraseña", 250, 900, 850, 940), T("Aceptar", 400, 1100, 600, 1140)]
comprobar("diálogo Atención de clave rechazada", "contraseña" in (E._dialogo_atencion(ATENCION) or ""))
OTRO_ATENCION = [T("Atención", 400, 800, 600, 840), T("Operación sujeta a verificación", 250, 900, 850, 940), T("Aceptar", 400, 1100, 600, 1140)]
comprobar("Atención sin 'contraseña' NO es rechazo de clave", E._dialogo_atencion(OTRO_ATENCION) is None)
comprobar("pantalla sin diálogos", E._mensaje_error(REVISION_PM) is None and E._dialogo_atencion(REVISION_PM) is None)

# ── comprobante ──
COMPROBANTE = [T("Comprobante de operación", 200, 200, 900, 250), T("Operación:", 100, 700, 300, 740),
               T("0123456789", 800, 700, 1050, 740), T("Origen:", 100, 800, 250, 840), T("Destino:", 100, 900, 250, 940)]
comprobar("comprobante con referencia", E._hubo_comprobante(COMPROBANTE) == (True, "0123456789"))
# Líneas REALES del OCR sobre el comprobante del 2026-10-24: el título viene con "< " pegado
# (la flecha de atrás). Con startswith("comprobante") el pago exitoso salía como INCIERTO.
COMPROBANTE_REAL = [T("2:11", 115, 57, 195, 91), T("< Comprobante de operación", 67, 196, 975, 255),
                    T("PagomóvilBDV Personas", 291, 587, 881, 640), T("100,00 Bs", 467, 827, 707, 873),
                    T("Fecha:", 99, 973, 214, 1005), T("Operación:", 98, 1064, 296, 1106),
                    T("Identificación:", 99, 1157, 351, 1191), T("Origen:", 98, 1252, 232, 1292),
                    T("Destino:", 99, 1346, 249, 1377), T("Banco:", 99, 1439, 221, 1470),
                    T("Concepto:", 98, 1531, 281, 1571), T("24/10/2026", 851, 972, 1072, 1007),
                    T("007777412378", 713, 1067, 1012, 1098), T("31899383", 875, 1160, 1071, 1191)]
comprobar("comprobante REAL con '< ' delante", E._hubo_comprobante(COMPROBANTE_REAL) == (True, "007777412378"))
comprobar("comprobante REAL se reconoce como pantalla de comprobante", E._es_comprobante(COMPROBANTE_REAL))
comprobar("palabra suelta 'comprobante' sin rótulos NO es comprobante",
          not E._es_comprobante([T("Compartir comprobante"), T("Inicio")]))
comprobar("comprobante sin referencia legible",
          E._hubo_comprobante([T("Comprobante", 200, 200, 900, 250), T("Origen:"), T("Destino:")]) == (True, "sin referencia"))
comprobar("la revisión NO es comprobante", E._hubo_comprobante(REVISION_PM) == (False, ""))
comprobar("Inicio NO es comprobante", E._hubo_comprobante([T("Inicio"), T("Saldo Cuenta Ahorro")]) == (False, ""))

# ── validación y nombres ──
comprobar("errores de validación", E._errores_validacion([T("Debes introducir el nombre"), T("Monto:"), T("Selecciona el tipo de documento")]) ==
          ["Debes introducir el nombre", "Selecciona el tipo de documento"])
comprobar("nombre con tilde falsa del OCR", E._parecido("JUAN PÉREZ", "JUAN PEREZ"))
comprobar("nombre distinto se rechaza", not E._parecido("MARIA GOMEZ", "JUAN PEREZ"))
comprobar("monto para el campo", E._monto_para_campo("2.000,00") == "2000,00")

# ── volver a Inicio, con un teléfono simulado que avanza de pantalla al tocar ──
PANTALLA_FORM_PM = [T("PagomóviIBDV", 221, 199, 654, 257), T("Documento:", 94, 755, 347, 790),
                    T("Inicio", 59, 2370, 137, 2396), T("Pagos", 433, 2364, 544, 2402)]
PANTALLA_INICIO = [T("Inicio", 221, 199, 377, 247), T("Saldo Cuenta Ahorro", 111, 407, 446, 433)]


class TelefonoSimulado:
    """nodos() devuelve la pantalla actual; cada tap pasa a la siguiente."""
    def __init__(self, pantallas):
        self.pantallas, self.i, self.toques = pantallas, 0, []

    def nodos(self):
        return self.pantallas[self.i]

    def hay_teclado_numerico(self, lista):
        return False

    def cerrar_teclado(self, lista=None):
        return False

    def esta_en_inicio(self, lista):
        return lista is PANTALLA_INICIO

    def tap(self, x, y, que="", espera=0.3):
        self.toques.append((x, y, que))
        self.i = min(self.i + 1, len(self.pantallas) - 1)

    def tab(self, nombre, lista=None):
        self.tap(0, 2383, f"pestaña {nombre}")


tel = TelefonoSimulado([COMPROBANTE_REAL, PANTALLA_FORM_PM, PANTALLA_INICIO])
E._volver_inicio(tel, espera=0)
comprobar("volver a Inicio desde el comprobante: toca '<' y luego la pestaña",
          [q for _, _, q in tel.toques] == ["cerrar comprobante (<)", "pestaña Inicio"] and tel.toques[0][:2] == (82, 225))

# ── revisión de "otros bancos" (líneas REALES del OCR: el Destino enmascarado NO sale) ──
REVISION_OTROS = [T("Confirmar Operación", 273, 199, 896, 257), T("Transferencias a otros bancos", 227, 402, 941, 442),
                  T("50,00 Bs", 481, 567, 691, 613), T("Nombre:", 99, 713, 256, 745),
                  T("Identificación:", 99, 804, 351, 838), T("Origen:", 98, 899, 232, 939),
                  T("Destino:", 99, 993, 249, 1024), T("Banco:", 99, 1086, 221, 1117),
                  T("Concepto:", 98, 1178, 281, 1218), T("NAPOLEÓN TORRES", 674, 704, 1073, 745),
                  T("v-31899383", 828, 807, 1071, 838), T("0134 - BANESCO", 737, 1086, 1072, 1117),
                  T("pago", 982, 1188, 1072, 1217), T("Confirmar", 481, 1387, 691, 1421)]


class CtrlRevision(CtrlFalso):
    """Como el teléfono: pantalla() da la captura nueva y valor_en_fila lee la fila ampliada."""
    def __init__(self, lectura, destino_ampliado):
        super().__init__(lectura)
        self.destino_ampliado = destino_ampliado

    def pantalla(self):
        return b"", self.lectura

    def valor_en_fila(self, png, lista, rotulo):
        return self.destino_ampliado if rotulo == "Destino:" else None


def orden_otros(**kw):
    base = dict(tipo="TRANSFERENCIA", banco="0134", cuenta="01340153551532183794", telefono="",
                nombre="NAPOLEON TORRES", monto="50,00", documento="31899383")
    base.update(kw)
    return orden_pm(**base)


ctrl_ok = CtrlRevision(REVISION_OTROS, "0134 3794")
comprobar("revisión de otros bancos con destino enmascarado se acepta",
          E._verificar_revision(ctrl_ok, REVISION_OTROS, orden_otros()) is REVISION_OTROS)
for nombre, ctrl_x, orden_x in [
        ("destino con otros últimos 4 dígitos", CtrlRevision(REVISION_OTROS, "0134 9999"), orden_otros()),
        ("destino de otro banco (prefijo)", CtrlRevision(REVISION_OTROS, "0105 3794"), orden_otros()),
        ("monto distinto", CtrlRevision(REVISION_OTROS, "0134 3794"), orden_otros(monto="500,00")),
        ("banco distinto", CtrlRevision(REVISION_OTROS, "0134 3794"), orden_otros(banco="0105", cuenta="01050032080032506287"))]:
    try:
        E._verificar_revision(ctrl_x, REVISION_OTROS, orden_x)
        comprobar(f"revisión de otros bancos rechaza {nombre}", False)
    except RuntimeError:
        comprobar(f"revisión de otros bancos rechaza {nombre}", True)
# El nombre es INFORMATIVO desde el 2026-10-24: un nombre distinto avisa en la consola pero NO bloquea
# (se abortaban órdenes buenas por nombres largos partidos en 2 líneas por la app).
comprobar("revisión de otros bancos con nombre distinto NO bloquea (solo avisa)",
          E._verificar_revision(CtrlRevision(REVISION_OTROS, "0134 3794"), REVISION_OTROS,
                                orden_otros(nombre="MARIA GOMEZ")) is REVISION_OTROS)
comprobar("revisión de otros bancos con identificación distinta NO bloquea (solo avisa)",
          E._verificar_revision(CtrlRevision(REVISION_OTROS, "0134 3794"), REVISION_OTROS,
                                orden_otros(documento="12345678")) is REVISION_OTROS)

# ── comprobante de INTERBANCARIA: una notificación tapa el título ──
COMPROBANTE_BANNER = [T("2:43", 108, 57, 206, 92), T("Transferencia interbancaria exitosa", 252, 203, 994, 238),
                      T('"Tu transferencia a otros bancos por', 255, 267, 927, 305),
                      T("Bs.50,OO se realizo con exito bajo et numero", 255, 324, 1078, 361),
                      T("Transferencias a otros bancos", 232, 590, 935, 630), T("Fecha:", 99, 973, 214, 1005),
                      T("Operación:", 98, 1064, 296, 1106), T("Nombre:", 99, 1159, 256, 1191),
                      T("Identificación:", 99, 1250, 351, 1284), T("Origen:", 98, 1345, 232, 1385),
                      T("Destino:", 99, 1439, 249, 1470), T("Banco:", 99, 1532, 221, 1563),
                      T("Concepto:", 98, 1624, 281, 1664), T("Estatus:", 99, 1718, 243, 1749),
                      T("50,00 Bs", 481, 827, 691, 873), T("3701130011508", 689, 1067, 1012, 1098),
                      T("En proceso", 871, 1718, 1072, 1756), T("Agregar Beneficiario", 482, 2239, 865, 2279)]
comprobar("comprobante con notificación encima (sin título) se reconoce por sus filas", E._es_comprobante(COMPROBANTE_BANNER))
comprobar("su referencia se lee", E._hubo_comprobante(COMPROBANTE_BANNER) == (True, "3701130011508"))
comprobar("se detecta la notificación", E._hay_banner(COMPROBANTE_BANNER) and not E._hay_banner(COMPROBANTE_REAL))
comprobar("la revisión NO se confunde con un comprobante (no tiene Fecha/Operación)", not E._es_comprobante(REVISION_OTROS)
          and not E._es_comprobante(REVISION_PM))

class TelefonoConBanner(TelefonoSimulado):
    """La notificación se va SOLA tras unas lecturas (deslizarla no la quita: medido en el
    iPhone el 2026-10-26, ~20 deslizamientos por orden sin efecto). Tocar la flecha "<"
    mientras está encima abre la notificación: eso no debe pasar."""
    def __init__(self, pantallas, lecturas_con_banner=2):
        super().__init__(pantallas)
        self.restan = lecturas_con_banner

    def nodos(self):
        if self.i == 0 and self.restan <= 0:
            self.i = 1                              # la notificación se fue sola
        self.restan -= 1
        return self.pantallas[self.i]

    def deslizar(self, *a, **k):
        self.toques.append((0, 0, "deslizar"))

    def tap(self, x, y, que="", espera=0.3):
        if self.i == 0:
            raise AssertionError("tocó la pantalla con la notificación encima (abriría la notificación)")
        super().tap(x, y, que, espera)

COMPROBANTE_SIN_BANNER = [t for t in COMPROBANTE_BANNER if t.y1 > 400]
tel = TelefonoConBanner([COMPROBANTE_BANNER, COMPROBANTE_SIN_BANNER, PANTALLA_FORM_PM, PANTALLA_INICIO])
E._volver_inicio(tel, espera=0)
comprobar("con la notificación tapando la flecha ESPERA (sin deslizar ni tocarla), luego '<' y va a Inicio",
          [q for _, _, q in tel.toques] == ["cerrar comprobante (<)", "pestaña Inicio"])

# Pantalla REAL (2026-10-26 13:03) que se abre si un toque cae sobre la notificación: el bot la
# confundía con una notificación encima y deslizaba sin parar (2 min, 4 órdenes fallidas).
DETALLE_NOTIFICACION = [T("1:03", 108, 57, 206, 92), T("Detalles de la notificación", 216, 199, 977, 250),
                        T("Transferencia interbancaria exitosa", 45, 372, 893, 412),
                        T("26-10-2026 01:02 PM", 45, 478, 412, 508),
                        T('"Tu transferencia a otros bancos por Bs.9000,00 se', 45, 575, 1000, 612),
                        T("realizo con exito bajo el numero de operacion", 45, 630, 980, 668)]
comprobar("la pantalla 'Detalles de la notificación' se reconoce", E._es_detalle_notificacion(DETALLE_NOTIFICACION)
          and not E._es_detalle_notificacion(COMPROBANTE_REAL) and not E._es_detalle_notificacion(PANTALLA_INICIO))
tel = TelefonoSimulado([DETALLE_NOTIFICACION, COMPROBANTE_SIN_BANNER, PANTALLA_FORM_PM, PANTALLA_INICIO])
E._volver_inicio(tel, espera=0)
comprobar("desde 'Detalles de la notificación' toca su '<' (no desliza) y llega a Inicio",
          [q for _, _, q in tel.toques] == ["cerrar detalle de notificación (<)", "cerrar comprobante (<)",
                                            "pestaña Inicio"] and tel.toques[0][:2] == (82, 225))

FORM_CON_BANNER = PANTALLA_FORM_PM + [T("Transferencia interbancaria exitosa", 252, 203, 994, 238)]
tel = TelefonoSimulado([FORM_CON_BANNER, PANTALLA_INICIO])
E._volver_inicio(tel, espera=0)
comprobar("con la barra de pestañas a la vista, una notificación arriba no frena: toca Inicio de una vez",
          [q for _, _, q in tel.toques] == ["pestaña Inicio"])

# ── el OCR leyó el guion de "0104 - BANCO VENEZOLANO" como "¯" (9 órdenes abortadas el 2026-10-26) ──
comprobar("banco con separador raro del OCR ('0104 ¯ BANCO') se acepta", E._coincide("0104 ¯ BANCO VENEZOLANO DE", "0104", "banco"))
comprobar("banco con otro código se sigue rechazando", not E._coincide("0105 ¯ MERCANTIL", "0104", "banco")
          and not E._coincide("01045 - X", "0104", "banco"))

# ── lecturas REALES del 2026-10-26 18:3x: sesión cerrada y cuenta sin saldo ──
LOGIN = [T("6:36", 107, 57, 205, 92), T("447", 100, 189, 159, 210), T("VBDV", 176, 880, 1000, 1112),
         T("PagomóvilBDV", 99, 1649, 342, 1684), T("Iniciar sesión", 449, 1262, 722, 1295),
         T("Pagos", 538, 1651, 633, 1684), T("AccessPay", 500, 1699, 670, 1732),
         T("Ver mis", 891, 1651, 1009, 1678), T("saldos", 900, 1699, 1002, 1726)]
comprobar("la pantalla de 'Iniciar sesión' se reconoce (y Inicio/formularios no)", E._es_pantalla_login(LOGIN)
          and not E._es_pantalla_login(PANTALLA_INICIO) and not E._es_pantalla_login(PANTALLA_FORM_PM))
tel = TelefonoSimulado([LOGIN])
t0 = time.perf_counter()
try:
    E._volver_inicio(tel, espera=0)
    comprobar("con la sesión cerrada, volver a Inicio avisa al instante", False)
except E.SesionCerrada as exc:
    comprobar("con la sesión cerrada, volver a Inicio avisa AL INSTANTE (no 2 min) y no toca nada",
              "inicia sesión a mano" in str(exc) and not tel.toques and time.perf_counter() - t0 < 1)

FORM_SIN_SALDO = [T("Transferencias a otros ...", 218, 199, 957, 247), T("Monto:", 93, 1297, 200, 1323),
                  T("7.000,00 Bs", 811, 1364, 1069, 1403),
                  T("EI monto no puede ser mayor al disponible en cuenta", 93, 1489, 942, 1522),
                  T("Transferir", 328, 1859, 534, 1893), T("Inicio", 59, 2370, 137, 2396)]
comprobar("'monto mayor al disponible' se reconoce como SIN SALDO",
          E._aviso_sin_saldo(FORM_SIN_SALDO) and not E._aviso_sin_saldo(REVISION_PM) and not E._aviso_sin_saldo(PANTALLA_INICIO))


class CtrlFormulario(CtrlFalso):
    def desplazar_hasta(self, rotulo):
        return self.lectura

    def buscar(self, rotulo, lista=None, exacto=True):
        raise AssertionError("tocó Transferir sin saldo")


try:
    E._enviar_y_revisar(CtrlFormulario(FORM_SIN_SALDO), "Transferir", orden_pm())
    comprobar("sin saldo NO toca Transferir", False)
except AssertionError:
    comprobar("sin saldo NO toca Transferir", False)
except RuntimeError as exc:
    comprobar("sin saldo NO toca Transferir y lo dice claro", "SIN SALDO" in str(exc))

DIALOGO_SALDO = [T("PagomóvilBDV", 221, 199, 654, 257), T("Error", 242, 1070, 369, 1114),
                 T("Saldo insuficiente", 239, 1208, 615, 1243), T("ID: bbf670e7e5a91130", 241, 1274, 742, 1309),
                 T("Aceptar", 804, 1478, 956, 1515)]
comprobar("'Saldo insuficiente' tras confirmar es rechazo LIMPIO (falló, no incierto)",
          E._rechazo_limpio(DIALOGO_SALDO) == "Saldo insuficiente")
comprobar("'venezolana de crédito' se reconoce como 0104",
          __import__("bancos").normalizar_banco("venezolana de crédito")[0] == "0104")

# ── aviso FINAL del banco en las interbancarias (lectura REAL del 2026-10-26 21:55) ──
DETALLE_FALLIDA = [T("Detalles de la notificación", 221, 200, 974, 247),
                   T("Transferencia interbancaria fallida", 44, 368, 868, 409),
                   T("26-10-2026 09:55 PM", 47, 484, 412, 511),
                   T('"Tu transferencia a otros bancos por Bs.3910,90 fue', 48, 579, 1019, 617),
                   T("fallida bajo el numero de operacion 3701130454863.", 45, 636, 1051, 674),
                   T('Intenta nuevamente desde nuestra BDVApp"', 48, 693, 887, 731)]
E._INTERBANCARIAS.clear(); E._AVISOS_BANCO.clear(); E._REFERENCIAS_AVISADAS.clear()
E._registrar_interbancaria(orden_pm(id_orden="ORD-A", mensaje_id=77, monto="3.910,90"), "3701130454863")
E._registrar_interbancaria(orden_pm(id_orden="ORD-B", mensaje_id=78, monto="9.000,00"), "3701130368614")
comprobar("hay interbancarias sin confirmar", E.hay_interbancarias_sin_confirmar())
E._revisar_avisos_banco(DETALLE_FALLIDA)
E._revisar_avisos_banco(DETALLE_FALLIDA)                    # la misma pantalla leída otra vez
avisos = E.avisos_banco_pendientes()
comprobar("aviso 'fallida' del banco -> UN aviso para la orden correcta (por número de operación)",
          len(avisos) == 1 and avisos[0]["id_orden"] == "ORD-A" and avisos[0]["mensaje_id"] == 77
          and avisos[0]["referencia"] == "3701130454863")
BANNER_EXITOSA_CORTADO = [T("Transferencia interbancaria exitosa", 252, 203, 994, 238),
                          T('"Tu transferencia a otros bancos por Bs.9000,00 se', 255, 267, 1000, 305),
                          T("realizo con exito bajo el numero", 255, 324, 900, 361)]
E._revisar_avisos_banco(BANNER_EXITOSA_CORTADO)              # sin número: se asocia por el monto
comprobar("aviso 'exitosa' sin número se asocia por el monto y NO genera aviso",
          not E.avisos_banco_pendientes() and not E.hay_interbancarias_sin_confirmar())
E._revisar_avisos_banco(COMPROBANTE_REAL + PANTALLA_INICIO)
comprobar("pantallas normales no generan avisos", not E.avisos_banco_pendientes())
OTRA_FALLIDA = [T("Transferencia interbancaria fallida", 44, 368, 868, 409),
                T("fallida bajo el numero de operacion 3709999999999.", 45, 636, 1051, 674)]
E._revisar_avisos_banco(OTRA_FALLIDA)
av = E.avisos_banco_pendientes()
comprobar("una fallida de una orden que el bot no conoce igual se avisa (sin ID)",
          len(av) == 1 and av[0]["id_orden"] is None and av[0]["referencia"] == "3709999999999")


class CtrlLecturas:
    def __init__(self, pantallas):
        self.pantallas, self.lecturas = list(pantallas), 0

    def nodos(self):
        self.lecturas += 1
        lista = self.pantallas.pop(0) if len(self.pantallas) > 1 else self.pantallas[0]
        E._revisar_avisos_banco(lista)
        return lista


E._INTERBANCARIAS.clear(); E._REFERENCIAS_AVISADAS.clear()
E._registrar_interbancaria(orden_pm(id_orden="ORD-C", mensaje_id=79, monto="3.910,90"), "3701130454863")
E._CTRL, guardado = CtrlLecturas([PANTALLA_INICIO, PANTALLA_INICIO, DETALLE_FALLIDA]), E._CTRL
E.time.sleep, dormir = (lambda s: None), E.time.sleep
try:
    E.vigilar_avisos_banco(30)
finally:
    E.time.sleep = dormir
lecturas, E._CTRL = E._CTRL.lecturas, guardado
av = E.avisos_banco_pendientes()
comprobar("vigilar tras la interbancaria: lee hasta ver el aviso y se detiene (sin tocar nada)",
          lecturas == 3 and len(av) == 1 and av[0]["id_orden"] == "ORD-C")
E._INTERBANCARIAS.clear(); E._REFERENCIAS_AVISADAS.clear()
E._registrar_interbancaria(orden_pm(id_orden="ORD-D", monto="1,00"), "1")
E._CTRL, guardado = CtrlLecturas([PANTALLA_INICIO]), E._CTRL
E.vigilar_avisos_banco(30, interrumpir=lambda: True)
lecturas, E._CTRL = E._CTRL.lecturas, guardado
comprobar("vigilar se corta si llega otra orden", lecturas == 0)
E._INTERBANCARIAS.clear()

# ── rechazo de clave REAL del 2026-10-27 ("Atención / Autenticación incorrecta") ──
AUTENTICACION = [T("Confirmar Operación", 273, 199, 896, 257), T("Transferencias a terceros", 284, 402, 883, 442),
                 T("100,00 Bs", 467, 567, 707, 613), T("x", 1078, 205, 1118, 245),
                 T("Atención", 239, 1067, 472, 1114), T("Autenticación incorrecta", 239, 1205, 751, 1243),
                 T("Aceptar", 804, 1478, 956, 1515)]
comprobar("'Atención / Autenticación incorrecta' se reconoce como clave rechazada",
          E._dialogo_atencion(AUTENTICACION) == "Autenticación incorrecta")
comprobar("otro diálogo 'Atención' NO se toma como clave rechazada",
          E._dialogo_atencion([T("Atención", 239, 1067, 472, 1114), T("Operación en proceso", 239, 1205, 751, 1243),
                               T("Aceptar", 804, 1478, 956, 1515)]) is None)
class _TelAutenticacion:
    def nodos(self):
        return AUTENTICACION

    def tap_texto(self, *a, **k):
        pass


tel = _TelAutenticacion()
try:
    E._esperar_comprobante(tel, presupuesto_seg=2)
    comprobar("tras la clave, 'Autenticación incorrecta' lanza ErrorAutenticacion al instante", False)
except E.ErrorAutenticacion:
    comprobar("tras la clave, 'Autenticación incorrecta' lanza ErrorAutenticacion al instante (no espera 40 s)", True)

# freno: nunca 3 rechazos seguidos (el banco bloquea el usuario)
intentos = []


def pago_rechazado(orden, *_a):
    intentos.append(orden.id_orden)
    raise E.ErrorAutenticacion("Autenticación incorrecta")


def pago_ok(orden, *_a):
    intentos.append(orden.id_orden)
    return E.ResultadoPago(True, False, "ok", "1", "c.png", volver_inicio_pendiente=True)


guardado = (E._intentar_pago, E._capturar, E._controlador)
E._intentar_pago, E._capturar, E._controlador = pago_rechazado, (lambda c, i: "c.png"), (lambda s=None: object())
E._FALLOS_CLAVE_SEGUIDOS = 0
try:
    r1 = E.ejecutar_pago(orden_pm(id_orden="O1"), None, "x")
    r2 = E.ejecutar_pago(orden_pm(id_orden="O2"), None, "x")
    comprobar("1.ª orden con clave rechazada: 2 intentos, FALLÓ (no incierto) y queda en pausa",
              intentos == ["O1", "O1"] and not r1.exito and not r1.incierto and "NO SE INTENTÓ" in r1.mensaje
              and E._FALLOS_CLAVE_SEGUIDOS == 2)
    comprobar("con la clave en pausa la orden siguiente NO toca el teléfono",
              intentos == ["O1", "O1"] and not r2.exito and not r2.incierto and r2.mensaje.startswith("NO SE INTENTÓ"))
    E._FALLOS_CLAVE_SEGUIDOS, intentos[:] = 0, []
    E._intentar_pago = lambda o, *a: (pago_rechazado(o) if len(intentos) == 0 else pago_ok(o))
    r3 = E.ejecutar_pago(orden_pm(id_orden="O3"), None, "x")
    comprobar("un rechazo suelto: reintenta UNA vez, paga, y el contador vuelve a cero",
              r3.exito and intentos == ["O3", "O3"] and E._FALLOS_CLAVE_SEGUIDOS == 0)
finally:
    E._intentar_pago, E._capturar, E._controlador = guardado
    E._FALLOS_CLAVE_SEGUIDOS = 0

# ── 2026-10-28: tras Confirmar, iOS pide el CÓDIGO DEL IPHONE (Face ID activado en la app) ──
CODIGO_IPHONE = [T("7:52", 109, 57, 203, 92), T("x", 87, 230, 139, 282),
                 T("Ingresa eI código del iPhone", 160, 538, 1013, 601), T("para BDVapp", 390, 626, 783, 685),
                 T("BDVapp", 474, 705, 699, 764)]
COMPROBANTE_SIN_CLAVE = [T("< Comprobante de operación", 67, 196, 975, 255),
                         T("Transferencias a otros bancos", 232, 590, 935, 630), T("3.910,90 Bs", 441, 827, 729, 873),
                         T("Fecha:", 99, 973, 214, 1005), T("Operación:", 98, 1064, 296, 1106),
                         T("3701130637539", 689, 1067, 1011, 1098), T("Nombre:", 99, 1159, 256, 1191),
                         T("Origen:", 98, 1345, 232, 1385), T("Destino:", 99, 1439, 249, 1470),
                         T("Estatus:", 99, 1718, 243, 1749), T("En proceso", 871, 1718, 1072, 1756)]
comprobar("la ventana 'Ingresa el código del iPhone' se reconoce", E._pide_codigo_iphone(CODIGO_IPHONE)
          and not E._pide_codigo_iphone(REVISION_PM) and not E._pide_codigo_iphone(COMPROBANTE_SIN_CLAVE))


class CtrlConfirmar:
    """Revisión -> 'Confirmar' -> la pantalla que se le pase. Anota si escribió la clave."""
    def __init__(self, tras_confirmar):
        self.tras, self.toques, self.escrito = tras_confirmar, [], []

    def buscar(self, rotulo, lista=None, exacto=True):
        return (586, 1218)

    def tap(self, x, y, que="", espera=0.3):
        self.toques.append(que)

    def esperar(self, condicion, descripcion="", intentos=16, intervalo=0.4):
        return condicion(self.tras)

    def ubicar_campo(self, rotulo, lista=None):
        return None

    def nodos(self):
        return self.tras

    def borrar_todo(self):
        pass

    def escribir(self, texto, oculto=False, pausa=0.04):
        self.escrito.append(texto)

    def tap_texto(self, *a, **k):
        self.toques.append("Aceptar")


ctrl_c = CtrlConfirmar(CODIGO_IPHONE)
try:
    E._confirmar_y_pagar(ctrl_c, "clave", REVISION_PM)
    comprobar("si iOS pide el código del iPhone, se detecta (no se espera el campo de contraseña)", False)
except E.CodigoIPhoneSolicitado:
    comprobar("si iOS pide el código del iPhone, se detecta al instante y NO se escribe la clave",
              ctrl_c.escrito == [])
ctrl_s = CtrlConfirmar(COMPROBANTE_SIN_CLAVE)
E._confirmar_y_pagar(ctrl_s, "clave", REVISION_PM)
comprobar("si tras Confirmar ya está el comprobante (no pidió clave), sigue sin escribir nada",
          ctrl_s.escrito == [] and ctrl_s.toques == ["'Confirmar'"])
comprobar("ese comprobante cuenta como pago HECHO", E._hubo_comprobante(COMPROBANTE_SIN_CLAVE) == (True, "3701130637539"))

guardado = E._capturar
E._capturar = lambda c, i: "c.png"
try:
    ctrl_x = CtrlConfirmar(CODIGO_IPHONE)
    ctrl_x.tap = lambda x, y, que="", espera=0.3: (ctrl_x.toques.append((x, y, que)), setattr(ctrl_x, "tras", PANTALLA_FORM_PM))
    r = E._resultado_codigo_iphone(ctrl_x, orden_pm(), E.CodigoIPhoneSolicitado(E.MENSAJE_CODIGO_IPHONE))
    comprobar("con el código del iPhone: captura, cancela con la X, FALLÓ (no incierto) y queda en pausa",
              not r.exito and not r.incierto and "NO SE PAGÓ" in r.mensaje and r.ruta_captura == "c.png"
              and ctrl_x.toques == [(113, 256, "cancelar código del iPhone (X)")] and E._CODIGO_IPHONE_PAUSA)
    r2 = E.ejecutar_pago(orden_pm(id_orden="P2"), None, "x")
    comprobar("en pausa, la orden siguiente NO toca el teléfono", r2.mensaje.startswith("NO SE INTENTÓ") and not r2.incierto)
finally:
    E._capturar = guardado
    E._CODIGO_IPHONE_PAUSA = False
tel = TelefonoSimulado([CODIGO_IPHONE, PANTALLA_FORM_PM, PANTALLA_INICIO])
E._volver_inicio(tel, espera=0)
comprobar("volver a Inicio con la ventana del código abierta: la cancela con la X",
          [q for _, _, q in tel.toques] == ["cancelar código del iPhone (X)", "pestaña Inicio"])

# ── pantalla que no se reconoce (21:55: transición tras abrirse la notificación) ──
PANTALLA_RARA = [T("6:36", 107, 57, 205, 92), T("Cargando", 400, 1200, 700, 1240)]
tel = TelefonoSimulado([PANTALLA_RARA] * 5 + [PANTALLA_INICIO])
tel.tap = lambda x, y, que="", espera=0.3: (tel.toques.append((x, y, que)), setattr(tel, "i", len(tel.pantallas) - 1))
E._volver_inicio(tel, espera=0)
comprobar("pantalla desconocida: a la 5.ª lectura prueba la flecha 'atrás' (no se rinde)",
          [q for _, _, q in tel.toques] == ["atrás (pantalla no reconocida)"] and tel.toques[0][:2] == (82, 225))

# ── "Banco inactivo" después de confirmar: fallo LIMPIO, capturado con el aviso a la vista ──
DIALOGO_BANCO_INACTIVO = [T("Error", 400, 800, 600, 840), T("Banco inactivo", 380, 900, 700, 940), T("Aceptar", 400, 1100, 600, 1140)]
comprobar("se reconoce el aviso 'Banco inactivo'", E._rechazo_limpio(DIALOGO_BANCO_INACTIVO) == "Banco inactivo")
comprobar("el aviso NO aparece en pantallas normales", E._rechazo_limpio(REVISION_PM) is None
          and E._rechazo_limpio(COMPROBANTE_REAL) is None and E._rechazo_limpio(PANTALLA_FORM_PM) is None)


class TelefonoQueNoDebeTocar:
    """Para comprobar que, ante 'Banco inactivo', el bot NO cierra el diálogo antes de capturar."""
    def __init__(self, pantalla):
        self.pantalla, self.toques = pantalla, []

    def nodos(self):
        return self.pantalla

    def tap(self, *a, **k):
        self.toques.append(a)

    def tap_texto(self, *a, **k):
        self.toques.append(a)


tel = TelefonoQueNoDebeTocar(DIALOGO_BANCO_INACTIVO)
try:
    E._esperar_comprobante(tel, presupuesto_seg=2)
    comprobar("tras la clave, 'Banco inactivo' lanza RechazoLimpio", False)
except E.RechazoLimpio as exc:
    comprobar("tras la clave, 'Banco inactivo' lanza RechazoLimpio SIN cerrar el diálogo (la captura lo muestra)",
              "inactivo" in str(exc).lower() and tel.toques == [])

DIALOGO_SIN_TITULO = [T("Banco inactivo", 380, 900, 700, 940), T("Aceptar", 400, 1100, 600, 1140)]
comprobar("también sin el título 'Error'", E._rechazo_limpio(DIALOGO_SIN_TITULO) == "Banco inactivo")

ERROR_DESCONOCIDO = [T("Error", 400, 800, 600, 840), T("Ocurrió un error inesperado", 300, 900, 800, 940), T("Aceptar", 400, 1100, 600, 1140)]
tel = TelefonoQueNoDebeTocar(ERROR_DESCONOCIDO)
r = E._esperar_comprobante(tel, presupuesto_seg=2)
comprobar("un error desconocido devuelve YA (incierto) sin cerrar el diálogo antes de capturar",
          r == (False, "", "Ocurrió un error inesperado") and tel.toques == [])

tel = TelefonoSimulado([DIALOGO_BANCO_INACTIVO, PANTALLA_INICIO])
E._volver_inicio(tel, espera=0)
comprobar("después de mandar el resultado, cierra el diálogo con su botón", tel.toques[0][2] == "cerrar diálogo")

# lista de bancos abierta (cubre toda la pantalla, sin botón de cerrar)
LISTA_BANCOS = [T("0156 - 100% BANCO", 50, 220, 520, 256), T("6000 - BANAVIH", 51, 365, 421, 400),
                T("0172 - BANCAMIGA BANCO UNIVERSAL, C.A.", 50, 508, 1075, 548), T("0114 - BANCARIBE", 50, 653, 480, 688)]
tel = TelefonoSimulado([LISTA_BANCOS, PANTALLA_FORM_PM, PANTALLA_INICIO])
E._volver_inicio(tel, espera=0)
comprobar("volver a Inicio con la lista de bancos abierta: la cierra tocando la franja de arriba",
          tel.toques[0] == (585, 70, "cerrar lista de bancos") and tel.toques[-1][2] == "pestaña Inicio")

# ── el banco de la orden tiene que coincidir con la cuenta ──
def transf(banco, cuenta):
    return orden_pm(tipo="TRANSFERENCIA", banco=banco, cuenta=cuenta, telefono="")

E._verificar_banco_vs_cuenta(transf("0102", "01020467450001765658"))
E._verificar_banco_vs_cuenta(transf("Banco de Venezuela", "01020467450001765658"))
E._verificar_banco_vs_cuenta(transf("Mercantil", "01050032080032506287"))
E._verificar_banco_vs_cuenta(orden_pm())          # un Pago Móvil no lleva cuenta: no se valida
comprobar("banco y cuenta coherentes se aceptan (BDV por código, por nombre, Mercantil y Pago Móvil)", True)
try:
    E._verificar_banco_vs_cuenta(transf("0102", "01050032080032506287"))   # la orden del 2026-10-24
    comprobar("banco 0102 con cuenta 0105… se rechaza sin tocar el teléfono", False)
except RuntimeError as exc:
    comprobar("banco 0102 con cuenta 0105… se rechaza sin tocar el teléfono", "no coincide" in str(exc) and "0105" in str(exc))

# ── verificación de campos por lote ──
class LectorSimulado:
    def __init__(self, valores):
        self.valores, self.lecturas = valores, 0

    def leer_campos(self, rotulos):
        self.lecturas += 1
        return {r: self.valores.get(r) for r in rotulos}

    def leer_campo(self, rotulo):
        return self.valores.get(rotulo)


bien = LectorSimulado({"Banco:": "0102 - BANCO DE VENEZUELA", "Teléfono:": "04243468273", "Concepto:": "pago",
                       "Documento:": "31899383"})
E._verificar_campos(bien, [("Banco:", "0102", "banco"), ("Teléfono:", "04243468273", "digitos"),
                           ("Concepto:", "pago", "texto"), ("Documento:", "31899383", "digitos")], intentos=2)
comprobar("verificación por lote: 4 campos con UNA lectura", bien.lecturas == 1)
for nombre, valores in [("banco equivocado", {"Banco:": "0134 - BANESCO"}), ("teléfono equivocado", {"Teléfono:": "04141111111"}),
                        ("campo vacío", {"Concepto:": None})]:
    base = {"Banco:": "0102 - BANCO DE VENEZUELA", "Teléfono:": "04243468273", "Concepto:": "pago"}
    base.update(valores)
    try:
        E._verificar_campos(LectorSimulado(base), [("Banco:", "0102", "banco"), ("Teléfono:", "04243468273", "digitos"),
                                                   ("Concepto:", "pago", "texto")], intentos=2)
        comprobar(f"verificación rechaza {nombre}", False)
    except RuntimeError:
        comprobar(f"verificación rechaza {nombre}", True)

print("\nTODO OK" if not fallos else f"\n{len(fallos)} FALLA(S): {fallos}")
sys.exit(1 if fallos else 0)
