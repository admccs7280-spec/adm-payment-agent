"""Ejecuta una orden de pago en el iPhone 12 (iOS 27+) y devuelve resultado + captura.

Mismo contrato que ejecutor.py (Android/BlueStacks): ejecutar_pago(),
volver_inicio_tras_pago() y ResultadoPago, para que el orquestador use uno u otro
según DISPOSITIVO en .env. Las clases de resultado/errores y las reglas de
negocio (modo transferencia por límite de Pago Móvil, reintento por clave
rechazada) se reutilizan de ejecutor.py: NO se duplican.

Tres flujos, igual que en Android:
  - TRANSFERENCIA con cuenta 0102 (20 dígitos) -> "Cuentas de terceros".
  - TRANSFERENCIA con cualquier otra cuenta    -> "Cuentas otros bancos".
  - PAGO_MOVIL (trae teléfono)                 -> "Pagos" -> "PagomóvilBDV".

Diferencias con Android que importan:
  - No hay árbol de la pantalla: se lee con captura + OCR (iphone/iphone_utils.py).
    Los campos se ubican por su rótulo ("Monto:"), nunca por coordenadas fijas:
    la posición cambia con los mensajes de validación y con el desplazamiento.
  - La pantalla "Confirmar Operación" es texto estático que el OCR lee sin
    errores, así que AHÍ se verifica el monto y el destino contra la orden
    antes de tocar Confirmar (fallo limpio si no coinciden). Android no lo hace.
  - La lectura de campos con el cursor encima falla; iphone_utils borra el
    cursor de la imagen antes del OCR.

Línea de seguridad (igual que Android): todo lo que pasa ANTES de escribir la
clave es un fallo limpio. Desde que se escribe la clave, es INCIERTO y nunca se
reintenta solo (salvo el rechazo de clave que la propia app confirma).

Pantallas aún NO mapeadas contra una orden real (se mapearán con la primera
orden chica): el comprobante final, el diálogo "Atención" de clave rechazada y
el diálogo "Error" genérico. Mientras tanto se detectan por texto y, si no se
reconoce el comprobante, la orden queda INCIERTA con la captura enviada.
"""
import difflib
import re
import sys
import threading
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ / "iphone"))

from iphone_utils import ControladorIPhone, Texto, normalizar, _normalizar_rotulo as normalizar_rotulo  # noqa: E402
import dispositivo  # noqa: E402
import registro_iphones  # noqa: E402

import ejecutor as _base  # noqa: E402
from ejecutor import (  # noqa: E402
    CARPETA_CAPTURAS,
    CONCEPTO_FIJO,
    PREFIJO_BDV,
    ErrorAutenticacion,
    LimitePagoMovil,
    LimitePagoMovilTrasClave,
    ResultadoPago,
    _es_aviso_limite_pm,
    _es_pm_a_bdv,
    _extraer_numero,
    _letra_tipo_documento,
    _monto_para_campo,
    _nombre_para_teclear,
)
from bancos import normalizar_banco  # noqa: E402
from config import opcional, requerido  # noqa: E402
from ordenes import OrdenPago, normalizar_monto  # noqa: E402

X_CERRAR_REVISION = (1100, 225)   # "X" arriba a la derecha de "Confirmar Operación"
# Flecha "<" arriba a la izquierda de "Comprobante de operación": vuelve al
# formulario de Pago Móvil ya vacío, con la barra de pestañas visible (medido el 2026-10-24).
FLECHA_ATRAS_COMPROBANTE = (82, 225)
CERRAR_LISTA_BANCOS = (585, 70)   # franja de arriba, sobre la lista de bancos: cierra sin elegir
PREFIJOS_VALIDACION = ("debes introducir", "selecciona", "seleccione", "numero de cuenta invalido")

# ── conexión al iPhone: una sola, reutilizada entre órdenes ──────────────
_CTRL: ControladorIPhone | None = None
_CTRL_LOCK = threading.Lock()


# El iPhone elegido al arrancar (dispositivo.preparar): las reconexiones usan SIEMPRE ese UDID, aunque
# después se conecte otro iPhone.
IDENTIDAD_IPHONE: dict = {}

# Con qué cuenta y clave se paga desde el iPhone reconocido: la de su registro (iphones.json, por UDID)
# o, si ese iPhone no está registrado y su cuenta es la del .env, la del .env (y queda registrado solo).
PERFIL_ACTIVO: dict = {}
_LOCK_ORDEN = threading.Lock()          # una orden (o una re-identificación) a la vez
_ULTIMO_FALLO_REGISTRO: dict = {}       # udid -> hora del último intento fallido de reconocerlo
_VIGILANTE: list = []


def perfil_activo() -> dict:
    """{nombre, cuenta, clave, origen}. Sin iPhone reconocido todavía, lo del .env."""
    if PERFIL_ACTIVO:
        return dict(PERFIL_ACTIVO)
    return {"nombre": "(.env)", "cuenta": opcional("CUENTA_DEBITAR_DESC"), "clave": opcional("BDV_PASSWORD"),
            "origen": ".env"}


def _resolver_perfil(identidad: dict) -> None:
    """Carga el perfil del iPhone reconocido desde su registro, o del .env si no está registrado."""
    reg = registro_iphones.buscar(identidad["udid"])
    if reg is not None:
        faltan = registro_iphones.problemas(reg)
        if faltan:
            raise ConfiguracionIPhone(
                f"el iPhone «{reg.get('nombre', identidad['nombre'])}» está registrado pero {', '.join(faltan)}: "
                "corrígelo en el panel › Teléfonos.")
        PERFIL_ACTIVO.clear()
        PERFIL_ACTIVO.update(nombre=reg.get("nombre") or identidad["nombre"], cuenta=(reg.get("cuenta") or "").strip(),
                             clave=reg["clave"], origen="registro", udid=identidad["udid"])
    else:
        PERFIL_ACTIVO.clear()
        PERFIL_ACTIVO.update(nombre=identidad["nombre"], cuenta=opcional("CUENTA_DEBITAR_DESC"),
                             clave=opcional("BDV_PASSWORD"), origen=".env", udid=identidad["udid"])


def leer_cuenta_del_iphone(ctrl: ControladorIPhone) -> str:
    """La cuenta BDV con la que está iniciada la app de ESTE iPhone ("0102****1022"): abre Cuentas de
    terceros, elige la cuenta a debitar (la primera, que es la única en bolívares) y la lee. No paga."""
    ctrl.abrir_transferencia("Cuentas de terceros")
    ctrl.limpiar_formulario()
    punto = ctrl.ubicar_campo("Cuenta a debitar:")
    if punto is None:
        raise RuntimeError("no se ve el campo 'Cuenta a debitar:'")
    ctrl.tap(*punto, que="abrir Cuenta a debitar", espera=0.8)
    ctrl.tap(*punto, que="elegir la cuenta", espera=0.8)
    valor = ctrl.leer_valor_campo("Cuenta a debitar:", por_tramos=True) or ""
    m = re.search(r"(\d{4})\D{2,8}(\d{4})", valor)
    if not m:
        raise RuntimeError(f"no se pudo leer la cuenta del iPhone (leyó {valor!r})")
    return f"{m.group(1)}****{m.group(2)}"


def _controlador(udid: str | None = None) -> ControladorIPhone:
    """Abrir el túnel y la sesión de toques cuesta ~4 s; se hace una vez."""
    global _CTRL
    with _CTRL_LOCK:
        if _CTRL is None:
            _CTRL = ControladorIPhone(IDENTIDAD_IPHONE.get("udid") or udid or None)
            _CTRL.al_leer = _revisar_avisos_banco
        return _CTRL


# ── resultado FINAL de las transferencias a otros bancos ─────────────────
# El comprobante de una interbancaria sale "En proceso": el banco confirma DESPUÉS con un aviso
# "Transferencia interbancaria exitosa" o "...fallida". Visto el 2026-10-26 21:55: el bot dio ✅
# (comprobante con referencia 3701130454863) y segundos después el banco avisó que FALLÓ. Cada
# lectura de pantalla pasa por _revisar_avisos_banco: si el aviso dice "fallida" se encola para
# que el orquestador lo diga en el grupo, como respuesta a la orden.
_INTERBANCARIAS: list[dict] = []        # {id_orden, mensaje_id, referencia, monto, cuando, estado}
_AVISOS_BANCO: list[dict] = []          # fallidas detectadas, por avisar en Telegram
_REFERENCIAS_AVISADAS: set[str] = set()
_AVISOS_LOCK = threading.Lock()
VIGENCIA_INTERBANCARIA_SEG = 2 * 3600


def _registrar_interbancaria(orden: OrdenPago, referencia: str) -> None:
    try:
        monto = normalizar_monto(orden.monto)
    except ValueError:
        monto = None
    with _AVISOS_LOCK:
        ahora = time.time()
        _INTERBANCARIAS[:] = [e for e in _INTERBANCARIAS if ahora - e["cuando"] < VIGENCIA_INTERBANCARIA_SEG]
        _INTERBANCARIAS.append({"id_orden": orden.id_orden, "mensaje_id": orden.mensaje_id,
                                "referencia": _solo_digitos(referencia or ""), "monto": monto,
                                "cuando": ahora, "estado": None})


def _revisar_avisos_banco(lista: list[Texto]) -> None:
    """Gancho de cada lectura de pantalla: busca "Transferencia interbancaria exitosa/fallida"
    (la notificación arriba o la pantalla "Detalles de la notificación") y la asocia a la orden
    por el número de operación o, si la notificación lo corta, por el monto. Nunca lanza."""
    try:
        texto = " ".join(normalizar(t.texto) for t in sorted(lista, key=lambda t: (t.y1, t.x1)))
        if "interbancaria fallida" in texto or "fue fallida" in texto:
            estado = "fallida"
        elif "interbancaria exitosa" in texto:
            estado = "exitosa"
        else:
            return
        numero = re.search(r"operacion\s*(\d{8,})", texto)
        numero = numero.group(1) if numero else ""
        monto_txt = re.search(r"bs\.?\s*(\d[\d.,]*\d)", texto)
        try:
            monto = normalizar_monto(monto_txt.group(1)) if monto_txt else None
        except ValueError:
            monto = None
        with _AVISOS_LOCK:
            entrada = None
            if numero:
                entrada = next((e for e in reversed(_INTERBANCARIAS) if e["referencia"] == numero), None)
            if entrada is None and monto is not None:
                entrada = next((e for e in reversed(_INTERBANCARIAS)
                                if e["estado"] is None and e["monto"] is not None
                                and abs(e["monto"] - monto) < 0.005), None)
            if entrada is not None:
                if entrada["estado"] is not None:
                    return                                   # ya se vio este aviso
                entrada["estado"] = estado
                numero = numero or entrada["referencia"]
            if estado != "fallida":
                return
            clave = numero or f"orden-{entrada['id_orden'] if entrada else monto}"
            if clave in _REFERENCIAS_AVISADAS:
                return
            _REFERENCIAS_AVISADAS.add(clave)
            _AVISOS_BANCO.append({
                "id_orden": entrada["id_orden"] if entrada else None,
                "mensaje_id": entrada["mensaje_id"] if entrada else None,
                "referencia": numero, "monto": monto,
                "texto": " ".join(t.texto for t in lista if "fallida" in normalizar(t.texto)
                                  or "transferencia" in normalizar(t.texto))[:300]})
        print(f"  !! el banco avisó: transferencia interbancaria FALLIDA (operación {numero or '?'}, "
              f"orden {entrada['id_orden'] if entrada else 'sin identificar'})", flush=True)
    except Exception:
        pass


def avisos_banco_pendientes() -> list[dict]:
    """Las transferencias que el banco marcó FALLIDAS y todavía no se avisaron (se vacía)."""
    with _AVISOS_LOCK:
        avisos, _AVISOS_BANCO[:] = list(_AVISOS_BANCO), []
    return avisos


def hay_interbancarias_sin_confirmar(segundos: float = 60.0) -> bool:
    ahora = time.time()
    with _AVISOS_LOCK:
        return any(e["estado"] is None and ahora - e["cuando"] < segundos for e in _INTERBANCARIAS)


def vigilar_avisos_banco(segundos: float = 20.0, interrumpir=lambda: False, serial: str | None = None) -> None:
    """Tras una interbancaria y con la cola vacía: lee la pantalla cada ~2,5 s hasta ver el aviso
    del banco (exitosa/fallida) o hasta `segundos`, sin tocar nada. Termina antes si llega otra
    orden (`interrumpir()`): las lecturas de esa orden también ven el aviso. Nunca lanza."""
    try:
        ctrl = _controlador(serial)
    except Exception:
        return
    limite = time.monotonic() + segundos
    while time.monotonic() < limite and hay_interbancarias_sin_confirmar() and not interrumpir():
        try:
            ctrl.nodos()
        except Exception:
            return
        for _ in range(10):
            if interrumpir():
                return
            time.sleep(0.25)


def _descartar_controlador() -> None:
    global _CTRL
    with _CTRL_LOCK:
        ctrl, _CTRL = _CTRL, None
    if ctrl is not None:
        try:
            ctrl.cerrar()
        except Exception:
            pass


class ConfiguracionIPhone(RuntimeError):
    """Lo que hay en el iPhone no coincide con la configuración (.env) y esperar no lo arregla:
    el bot NO arranca (mejor que fallar cada orden o, peor, pagar desde otra cuenta)."""


def esperar_dispositivo_listo(udid: str = "", limite_seg: float = 600.0, intervalo: float = 8.0,
                              log=print) -> str:
    """Deja el iPhone LISTO antes de que el bot acepte órdenes, sea cual sea el iPhone 12 conectado
    (nuevo, reiniciado o cambiado por otro). Misma firma que adb_utils.esperar_dispositivo_listo.

    1. Lo reconoce y lo prepara (dispositivo.preparar): un solo iPhone, iOS 27+, Modo de desarrollador
       y imagen de desarrollador montada (se monta sola).
    2. Abre la conexión de control y lee la pantalla.
    3. Espera a que la app BDV tenga la sesión iniciada (el bot nunca inicia sesión).
    4. Verifica que la cuenta a debitar del iPhone sea la de CUENTA_DEBITAR_DESC (sin tocar Transferir).
    Devuelve una descripción del iPhone. Lanza ConfiguracionIPhone si la cuenta no coincide."""
    inicio = time.monotonic()
    ultimo_aviso = -1e9
    ultimo_error = "sin intentos"
    identidad: dict | None = None
    while True:
        try:
            if identidad is None:
                identidad = dispositivo.preparar(udid, log)
                if identidad.get("udid_configurado_ausente"):
                    log(f"aviso: el UDID de la configuración ({identidad['udid_configurado_ausente']}) no está conectado; "
                        f"se usa el único iPhone conectado ({identidad['nombre']}).")
                IDENTIDAD_IPHONE.clear()
                IDENTIDAD_IPHONE.update(identidad)
                _resolver_perfil(identidad)
                _descartar_controlador()       # una conexión abierta antes puede ser de OTRO iPhone
            ctrl = _controlador(identidad["udid"])
            if ctrl.nodos():
                _preparar_app(ctrl, log)
                _iniciar_vigilante()
                perfil = perfil_activo()
                return (f"{identidad['nombre']}, iOS {identidad['ios']}, UDID {identidad['udid']} "
                        f"[registro: {perfil['nombre']}, cuenta {perfil['cuenta']}]")
            ultimo_error = "la pantalla no devolvió texto (¿iPhone bloqueado?)"
        except dispositivo.DispositivoNoListo as exc:
            identidad = None
            ultimo_error = str(exc)
        except SesionCerrada as exc:
            ultimo_error = str(exc)                       # no se descarta la conexión: solo falta iniciar sesión
        except ConfiguracionIPhone:
            raise
        except Exception as exc:
            ultimo_error = dispositivo.explicar_error(str(exc)) if str(exc) else type(exc).__name__
            identidad = None                              # se vuelve a preparar (monta la imagen si falta)
            _descartar_controlador()
        if time.monotonic() - inicio > limite_seg:
            raise RuntimeError(f"el iPhone no quedó listo en {limite_seg:.0f} s: {ultimo_error}")
        if time.monotonic() - ultimo_aviso >= 30:
            log(f"... el iPhone todavía no está listo: {ultimo_error}")
            ultimo_aviso = time.monotonic()
        time.sleep(intervalo)


def _preparar_app(ctrl: ControladorIPhone, log=print) -> None:
    """Con la conexión ya abierta: la app BDV con la sesión iniciada (si no, SesionCerrada y el
    arranque espera) y la cuenta del iPhone igual a CUENTA_DEBITAR_DESC."""
    try:
        _volver_inicio(ctrl)
    except SesionCerrada:
        raise
    except Exception as exc:
        log(f"aviso: no se pudo dejar la app en Inicio al arrancar ({exc}); se sigue.")
        return
    perfil = perfil_activo()
    cuenta = perfil["cuenta"]
    udid = IDENTIDAD_IPHONE.get("udid", "")
    if not cuenta:
        # Registrado sin cuenta (o .env vacío): se LEE de este iPhone y se guarda en su registro.
        try:
            cuenta = leer_cuenta_del_iphone(ctrl)
        except RuntimeError as exc:
            raise ConfiguracionIPhone(f"no se pudo leer la cuenta BDV del iPhone «{perfil['nombre']}»: {exc}") from exc
        if udid:
            registro_iphones.guardar(udid, cuenta=cuenta)
        PERFIL_ACTIVO["cuenta"] = cuenta
        log(f"Cuenta del iPhone «{perfil['nombre']}» leída y guardada en su registro: {cuenta}.")
    try:
        ctrl.abrir_transferencia("Cuentas de terceros")
        ctrl.limpiar_formulario()
        ctrl.elegir_cuenta_debitar(cuenta)
        log(f"Cuenta a debitar verificada en el iPhone «{perfil['nombre']}»: {cuenta}.")
        if perfil["origen"] == ".env" and udid and registro_iphones.buscar(udid) is None and perfil["clave"]:
            # Un iPhone nuevo cuya cuenta es la del .env: queda registrado solo, con esa cuenta y esa clave.
            registro_iphones.guardar(udid, nombre=IDENTIDAD_IPHONE.get("nombre", "iPhone"), cuenta=cuenta,
                                     clave=perfil["clave"])
            log(f"iPhone «{IDENTIDAD_IPHONE.get('nombre', 'iPhone')}» registrado con la cuenta {cuenta}.")
    except RuntimeError as exc:
        visto = re.search(r"quedó en '([^']*)'", str(exc))
        if visto and visto.group(1).strip():
            if perfil["origen"] == "registro":
                raise ConfiguracionIPhone(
                    f"la cuenta del iPhone ({visto.group(1)}) NO es la registrada para «{perfil['nombre']}» "
                    f"({cuenta}). Corrígela en el panel › Teléfonos y vuelve a iniciar.") from exc
            raise ConfiguracionIPhone(
                f"el iPhone «{IDENTIDAD_IPHONE.get('nombre', 'iPhone')}» (UDID {udid}) NO está registrado y su "
                f"cuenta ({visto.group(1)}) no es la del .env ({cuenta}). Regístralo en el panel › Teléfonos › "
                "«Registrar iPhone conectado» (lee la cuenta solo; solo pide el nombre y la clave).") from exc
        log(f"aviso: no se pudo verificar la cuenta a debitar al arrancar ({exc}); se verificará en cada orden.")
    finally:
        try:
            ctrl.limpiar_formulario()
            _volver_inicio(ctrl)
        except Exception:
            pass


# ── utilidades ───────────────────────────────────────────────────────────
def _solo_digitos(texto: str) -> str:
    return re.sub(r"\D", "", texto or "")


def _parecido(a: str, b: str, umbral: float = 0.85) -> bool:
    """Comparación tolerante para nombres: el OCR a veces agrega una tilde o
    confunde una letra, y el nombre es informativo (el banco valida la cuenta)."""
    limpiar = lambda s: re.sub(r"[^a-z0-9]", "", normalizar(s))
    return difflib.SequenceMatcher(None, limpiar(a), limpiar(b)).ratio() >= umbral


def _capturar(ctrl: ControladorIPhone, id_orden: str) -> str:
    """Nunca lanza: si la captura falla devuelve '' (el orquestador avisa sin imagen)."""
    destino = str(CARPETA_CAPTURAS / f"{id_orden}_{int(time.time())}.png")
    try:
        return ctrl.capturar_pantalla(destino)
    except Exception as exc:
        print(f"  !! no se pudo capturar la pantalla: {exc}")
        return ""


def _mensaje_dialogo(lista: list[Texto], titulo: str) -> str | None:
    """Texto de un diálogo con título `titulo` ("Error", "Atención"): las líneas
    debajo del título y por encima del botón "Aceptar". None si no hay diálogo."""
    objetivo = normalizar(titulo)
    cabecera = next((t for t in lista if normalizar(t.texto) == objetivo and t.y1 > 300), None)
    if cabecera is None:
        return None
    boton = next((t for t in lista if normalizar(t.texto) == "aceptar" and t.y1 > cabecera.y1), None)
    limite = boton.y1 if boton is not None else cabecera.y2 + 400
    cuerpo = sorted((t for t in lista if cabecera.y2 <= t.y1 < limite), key=lambda t: (t.y1, t.x1))
    return " ".join(t.texto for t in cuerpo[:3]) or "(sin texto)"


def _mensaje_error(lista: list[Texto]) -> str | None:
    return _mensaje_dialogo(lista, "Error")


def _dialogo_atencion(lista: list[Texto]) -> str | None:
    """Rechazo de clave: SOLO el diálogo 'Atención' que menciona "contraseña" o dice
    "Autenticación incorrecta" (ver _dialogo_atencion en ejecutor.py para el porqué). El texto
    real del iPhone es "Autenticación incorrecta" (capturas del 2026-10-27): antes no se
    reconocía, la orden esperaba 40 s y quedaba INCIERTA (12 órdenes ese día)."""
    texto = _mensaje_dialogo(lista, "Atención")
    if texto is None:
        return None
    n = normalizar(texto)
    if "contrase" not in n and "autenticacion incorrecta" not in n:
        return None
    return texto


def _errores_validacion(lista: list[Texto]) -> list[str]:
    return [t.texto for t in lista if normalizar(t.texto).startswith(PREFIJOS_VALIDACION)]


ETIQUETAS_COMPROBANTE = {"origen:", "destino:", "operacion:", "identificacion:", "fecha:"}


def _es_comprobante(lista: list[Texto]) -> bool:
    """Comprobante = un título que CONTIENE "comprobante" (el OCR lo lee como
    "< Comprobante de operación": la flecha de atrás se le pega y un
    startswith fallaba, dejando pagos exitosos como INCIERTOS, 2026-10-24) MÁS
    al menos dos de sus rótulos (Origen/Destino/Operación/Fecha...): así un
    texto suelto con esa palabra en otra pantalla no cuenta."""
    normalizados = [normalizar(t.texto) for t in lista]
    rotulos = sum(1 for n in normalizados if n in ETIQUETAS_COMPROBANTE)
    if any("comprobante" in n for n in normalizados) and rotulos >= 2:
        return True
    # Tras una transferencia INTERBANCARIA iOS muestra una notificación arriba
    # ("Transferencia interbancaria exitosa...") que tapa el título ~15 s. Sin el
    # título el comprobante se reconoce por sus filas: "Fecha:" y "Operación:" solo
    # existen en el comprobante (la revisión no las tiene) y deben venir con otras.
    return {"fecha:", "operacion:"} <= set(normalizados) and rotulos >= 4


def _hay_banner(lista: list[Texto]) -> bool:
    """Notificación (banner) del banco sobre la parte de arriba de la pantalla."""
    claves = ("exitosa", "se realizo con exito", "tu transferencia", "tu pago", "recibiste", "inactivo")
    return any(t.y1 < 400 and any(c in normalizar(t.texto) for c in claves) for t in lista)


class SesionCerrada(RuntimeError):
    """La app BDV cerró la sesión (pantalla de "Iniciar sesión"). El bot nunca inicia sesión:
    hay que entrar a mano. Visto el 2026-10-26 18:36: el bot pasó 2 min buscando algo
    navegable y la orden siguiente falló sin decir por qué."""


MENSAJE_SESION_CERRADA = ("la app BDV cerró la sesión: inicia sesión a mano en el iPhone y déjala "
                          "en Inicio (el bot nunca inicia sesión)")


def _es_pantalla_login(lista: list[Texto]) -> bool:
    """Pantalla de entrada de la app: botón "Iniciar sesión" con los accesos de afuera
    ("PagomóvilBDV QR", "Pagos AccessPay", "Ver mis saldos")."""
    textos = {normalizar(t.texto) for t in lista}
    return "iniciar sesion" in textos and bool(textos & {"ver mis saldos", "pagos accesspay", "accesspay"})


def _es_detalle_notificacion(lista: list[Texto]) -> bool:
    """Pantalla de la app que se abre al tocar una notificación del banco."""
    return any(t.y1 < 400 and normalizar(t.texto) in ("detalles de la notificacion", "notificaciones")
               for t in lista)


def _hubo_comprobante(lista: list[Texto]) -> tuple[bool, str]:
    """La referencia es el número que acompaña a un rótulo
    "Operación/Referencia/Nro" en la misma fila."""
    if not _es_comprobante(lista):
        return False, ""
    for rotulo in lista:
        n = normalizar(rotulo.texto)
        if not re.match(r"^(operacion|referencia|nro|numero)\b", n):
            continue
        mismo = [t for t in lista if t is not rotulo and abs(t.y1 - rotulo.y1) < 25 and t.x1 > rotulo.x1]
        for t in sorted(mismo, key=lambda t: t.x1):
            digitos = _solo_digitos(t.texto)
            if len(digitos) >= 4:
                return True, digitos
        inline = re.search(r"(\d{4,})", rotulo.texto)
        if inline:
            return True, inline.group(1)
    return True, "sin referencia"


# ── navegación ────────────────────────────────────────────────────────────
def _volver_inicio(ctrl: ControladorIPhone, intentos: int = 30, espera: float = 1.0) -> list[Texto]:
    """Deja la app en la pestaña Inicio, cerrando lo que encuentre por el
    camino. Nunca usa el botón físico de inicio (sacaría de la app)."""
    desconocidas = 0
    for intento in range(intentos):
        lista = ctrl.nodos()
        if ctrl.hay_teclado_numerico(lista):
            ctrl.cerrar_teclado(lista)
            continue
        if ctrl.esta_en_inicio(lista):
            print("  -> ya en Inicio")
            return lista
        if _es_pantalla_login(lista):
            raise SesionCerrada(MENSAJE_SESION_CERRADA)
        aviso_auto = next((t for t in lista if "automatiz" in normalizar(t.texto)), None)
        if aviso_auto is not None:
            # Aviso de "automatización" sobre la app (visto en un iPhone con "Automatización de
            # interfaz" encendida en Ajustes > Desarrollador): se cierra con su botón. Lo correcto es
            # apagar ese interruptor en el iPhone (GUIA: lista de preparación de un iPhone).
            boton = next((t for t in lista if normalizar(t.texto) in ("aceptar", "ok", "cerrar", "entendido",
                                                                       "continuar")), None)
            if boton is not None:
                print(f"  -> aviso de automatización en pantalla ({aviso_auto.texto!r}); se cierra")
                ctrl.tap(*boton.centro, que="cerrar aviso de automatización", espera=espera)
                continue
        if _pide_codigo_iphone(lista):
            # Ventana de iOS "Ingresa el código del iPhone" que quedó abierta: se cancela (sin el
            # código no se autoriza ninguna operación).
            ctrl.tap(*X_CANCELAR_CODIGO_IPHONE, que="cancelar código del iPhone (X)", espera=espera)
            continue
        banner = _hay_banner(lista)
        if _es_detalle_notificacion(lista):
            # Un toque cayó sobre la notificación del banco (tapaba la flecha "<" del
            # comprobante) y la app abrió "Detalles de la notificación". Su texto
            # ("Transferencia interbancaria exitosa") parecía una notificación encima y el
            # bot deslizaba sin parar: 2 min atascado y 4 órdenes fallidas el 2026-10-26.
            ctrl.tap(*FLECHA_ATRAS_COMPROBANTE, que="cerrar detalle de notificación (<)", espera=espera)
            continue
        if sum(1 for t in lista if re.match(r"^\s*\d{4}\s*[^\w\s]", t.texto)) >= 3:
            # Lista de bancos abierta: cubre toda la pantalla y no tiene botón de
            # cerrar. Si el bot falla buscando un banco, queda así; tocar la franja
            # de arriba (la "barrera") la cierra sin elegir nada. Sin esta regla el
            # bot reintentaba 30 veces (~38 s) sin saber salir (2026-10-24).
            ctrl.tap(*CERRAR_LISTA_BANCOS, que="cerrar lista de bancos", espera=espera)
            continue
        textos = {normalizar_rotulo(t.texto): t for t in lista}
        if "ahora no" in textos:                       # aviso del sistema (ej. "Termina de configurar tu iPhone")
            ctrl.tap(*textos["ahora no"].centro, que="aviso del sistema", espera=espera)
            continue
        if ("contrasena:" in textos or ("confirmar operacion" in textos and "aceptar" in textos)) \
                and "cancelar" in textos:                        # cuadro de clave abierto
            ctrl.tap(*textos["cancelar"].centro, que="cerrar cuadro de clave", espera=espera)
            continue
        if (_mensaje_error(lista) is not None or _mensaje_dialogo(lista, "Atención") is not None
                or _rechazo_limpio(lista) is not None):
            # Diálogo de la app (título "Error", "Atención" o el de un rechazo conocido
            # como "Banco inactivo"): se cierra con su botón, sea cual sea.
            boton = next((textos[k] for k in ("aceptar", "ok", "cerrar", "entendido") if k in textos), None)
            if boton is not None:
                ctrl.tap(*boton.centro, que="cerrar diálogo", espera=espera)
                continue
        if "confirmar operacion" in textos or _es_comprobante(lista):
            if banner:
                # La notificación tapa justo la X / la flecha "<" de arriba: tocar ahí abre
                # la notificación. Deslizarla hacia arriba NO la quita (medido el 2026-10-26:
                # ~20 deslizamientos por orden sin efecto); se va sola en unos segundos.
                print("  -> notificación del banco tapando la flecha; se espera a que se vaya")
                time.sleep(1.0)
                continue
            if "confirmar operacion" in textos:        # pantalla de revisión: salir con la X
                ctrl.tap(*X_CERRAR_REVISION, que="cerrar revisión (X)", espera=espera)
            else:                                      # comprobante abierto (sin barra de pestañas)
                ctrl.tap(*FLECHA_ATRAS_COMPROBANTE, que="cerrar comprobante (<)", espera=espera)
            continue
        if "pagomovilbdv qr" in textos or "cuentas propias" in textos:   # menú inferior abierto
            ctrl.tap(585, 600, que="cerrar menú", espera=espera)
            continue
        if any(t.y1 > 2300 and normalizar(t.texto).startswith("inicio") for t in lista):
            # La barra de pestañas está abajo: una notificación arriba no estorba.
            ctrl.tab("Inicio", lista)
            continue
        if banner:
            print("  -> notificación del banco encima; se espera a que se vaya")
            time.sleep(1.0)
            continue
        desconocidas += 1
        if desconocidas == 1:
            # Para diagnosticar: qué se ve (el 2026-10-26 21:55 el bot se rindió sin saberlo).
            print("  -> pantalla no reconocida; se ve: "
                  + " | ".join(t.texto for t in sorted(lista, key=lambda t: (t.y1, t.x1))[:8]))
        if desconocidas % 5 == 0:
            # Varias lecturas seguidas sin nada conocido (2026-10-26 21:55: tras abrirse sola la
            # notificación del banco la app quedó ~30 s en una pantalla de transición y el bot se
            # rindió): se prueba la flecha "atrás" de arriba a la izquierda, que es la salida de
            # las pantallas de la app. Pasa después del pago: no toca dinero.
            ctrl.tap(*FLECHA_ATRAS_COMPROBANTE, que="atrás (pantalla no reconocida)", espera=espera)
            continue
        print("  -> nada navegable visible todavía (¿modal de procesando?), reintentando")
        time.sleep(espera)
    raise RuntimeError(f"no se pudo volver a Inicio tras {intentos} intentos")


def _volver_inicio_seguro(ctrl: ControladorIPhone) -> None:
    """Se llama SIEMPRE tras la captura. Nunca lanza, para no tapar el resultado real."""
    try:
        _volver_inicio(ctrl)
    except Exception as exc:
        print(f"  !! no se pudo volver a Inicio: {exc}")


# ── llenado de los formularios (hasta ANTES del botón de envío) ──────────
def _coincide(visto: str, esperado: str, modo: str) -> bool:
    if modo == "digitos":
        return _solo_digitos(visto) == _solo_digitos(esperado)
    if modo == "tolerante":          # nombres: el OCR agrega tildes o confunde una letra
        return _parecido(visto, esperado)
    # El campo muestra "CODIGO - NOMBRE": se compara el código. El separador puede ser cualquier
    # símbolo: el OCR leyó el guion de "0104 - BANCO VENEZOLANO" como "¯" y se abortaron 9
    # órdenes buenas el 2026-10-26 (en todo el bot: [^\w\s] en vez de solo [-–]).
    if modo == "banco":
        return re.match(rf"^\s*{re.escape(esperado)}\s*[^\w\s]", visto) is not None
    return normalizar(visto) == normalizar(esperado)


def _verificar_campos(ctrl: ControladorIPhone, campos: list[tuple[str, str, str]],
                      intentos: int = 3) -> None:
    """Compara lo que quedó escrito en varios campos contra lo esperado, todos
    de UNA captura (los que no están a la vista se leen aparte, desplazando).
    campos = [(rótulo, esperado, modo)], modo: digitos | tolerante | banco | texto |
    informativo[:digitos|:texto]. Fallo limpio (antes de enviar nada) si alguno no
    coincide tras reintentar.

    BLOQUEAN solo los datos de pago: banco, cuenta/teléfono destino y monto. Los
    modos "informativo*" (nombre, documento, concepto) NUNCA bloquean: dejan un aviso
    en la consola y no se buscan desplazando si no están a la vista. Decisión del
    dueño (2026-10-24): un nombre largo partido o un dígito mal leído por el OCR en un
    dato que no mueve el dinero no puede abortar un pago bueno (4 órdenes ese día)."""
    pendientes = list(campos)
    visto: dict[str, str | None] = {}
    for intento in range(intentos):
        valores = ctrl.leer_campos([r for r, _, _ in pendientes])
        faltan = []
        for rotulo, esperado, modo in pendientes:
            leido = valores.get(rotulo)
            if modo.startswith("informativo"):
                visto[rotulo] = leido
                comparar = modo.partition(":")[2] or "tolerante"
                if leido is not None and not _coincide(leido, esperado, comparar):
                    print(f"  !! aviso (no bloquea): {rotulo} se ve {leido!r}, se tecleó {esperado!r}")
                continue
            if leido is None:
                leido = ctrl.leer_campo(rotulo)
            visto[rotulo] = leido
            if leido is None or not _coincide(leido, esperado, modo):
                faltan.append((rotulo, esperado, modo))
        if not faltan:
            return visto
        pendientes = faltan
        if intento < intentos - 1:
            time.sleep(0.4)
    rotulo, esperado, _ = pendientes[0]
    raise RuntimeError(f"el campo {rotulo} quedó en {visto.get(rotulo)!r}, "
                       f"se esperaba {esperado!r} — no se envía el pago")


def _rellenar_si_vacio(ctrl: ControladorIPhone, leidos: dict, rotulo: str, valor: str,
                       doble_toque: bool = False) -> None:
    """Un campo informativo (cédula) que quedó VACÍO no se deja pasar: el toque no lo
    enfocó y el número se escribió en otro campo (2026-10-24: "Debes introducir el
    documento", la orden falló tras 21 s de espera). Si no se vio en la lectura por lote
    se busca (desplazando si hace falta); si de verdad está vacío, se vuelve a llenar."""
    if leidos.get(rotulo):
        return
    actual = ctrl.leer_campo(rotulo)
    if actual:
        return
    print(f"  !! aviso: {rotulo} quedó vacío, se vuelve a llenar")
    ctrl.llenar(rotulo, valor, doble_toque=doble_toque)


def _llenar_monto_verificado(ctrl: ControladorIPhone, monto_orden: str, intentos: int = 3) -> None:
    """Escribe el monto y LEE lo que quedó, comparando contra el de la orden
    (ver _llenar_monto_verificado en ejecutor.py: una orden de 2.500,00 Bs se
    pagó por 250,00 con dinero real). Fallo limpio si no coincide."""
    esperado = normalizar_monto(monto_orden)
    visto = None
    for _ in range(intentos):
        # Tocado a la DERECHA (el borrado sí funciona ahí) y tecleado más despacio: con el
        # teléfono ocupado la máscara del campo se comía una tecla (el 3.er dígito).
        ctrl.llenar("Monto:", _monto_para_campo(monto_orden), cerrar_teclado=True,
                    tocar_derecha=True, pausa=0.08)
        visto = ctrl.leer_campo("Monto:")
        numero = _extraer_numero(visto or "")
        try:
            actual = normalizar_monto(numero) if numero else None
        except ValueError:
            actual = None
        if actual is not None and abs(actual - esperado) < 0.005:
            return
        print(f"  !! el campo de monto quedó en {visto!r} (se esperaba {_monto_para_campo(monto_orden)!r}), reintentando")
    raise RuntimeError(
        f"no se pudo escribir el monto correctamente tras {intentos} intentos "
        f"(quedó {visto!r}, se esperaba {_monto_para_campo(monto_orden)!r}) — no se envía el pago"
    )


def _llenar_terceros(ctrl: ControladorIPhone, orden: OrdenPago, cuenta_debitar: str) -> str:
    ctrl.abrir_transferencia("Cuentas de terceros")
    ctrl.limpiar_formulario()
    ctrl.elegir_cuenta_debitar(cuenta_debitar)
    ctrl.llenar("Cuenta destino:", orden.cuenta)
    _llenar_monto_verificado(ctrl, orden.monto)
    ctrl.llenar("Concepto:", CONCEPTO_FIJO)
    _verificar_campos(ctrl, [("Cuenta destino:", orden.cuenta, "digitos"),
                             ("Concepto:", CONCEPTO_FIJO, "informativo:texto")])
    return "Transferir"


def _llenar_otros_bancos(ctrl: ControladorIPhone, orden: OrdenPago, cuenta_debitar: str,
                         por_telefono: bool = False) -> str:
    codigo, _nombre = normalizar_banco(orden.banco)
    if not codigo:
        raise RuntimeError(f"no se reconoce el banco destino: '{orden.banco}'")

    ctrl.abrir_transferencia("Cuentas otros bancos")
    # La pantalla arrastra el estado del intento anterior; pero con el tipo de
    # instrumento vacío ya está limpia y al elegirlo se reinicia el resto: no hace
    # falta tocar "Limpiar" (2 deslizamientos).
    ctrl.limpiar_si_hace_falta("Tipo de instrumento:")
    ctrl.elegir_cuenta_debitar(cuenta_debitar)
    # "Tipo de instrumento" reinicia el resto del formulario: va antes que lo demás.
    ctrl.elegir_opcion("Tipo de instrumento:", "Teléfono" if por_telefono else "Cuenta")
    ctrl.elegir_banco(codigo)
    letra = orden.tipo_documento            # V cédula · E extranjero · J jurídico · P pasaporte · G · C
    ctrl.elegir_tipo_documento(letra)
    destino = orden.telefono if por_telefono else orden.cuenta
    ctrl.llenar("Instrumento destino:", destino)
    nombre = _nombre_para_teclear(orden.nombre)
    ctrl.llenar("Nombre:", nombre, doble_toque=True)    # el 1.er toque tras el teclado numérico se puede perder
    ctrl.llenar("Documento:", orden.documento, doble_toque=True)   # el autocorrector se come el 1.er toque
    _llenar_monto_verificado(ctrl, orden.monto)
    ctrl.llenar("Concepto:", CONCEPTO_FIJO)
    leidos = _verificar_campos(ctrl, [("Banco:", codigo, "banco"),
                                      ("Instrumento destino:", destino, "digitos"),
                                      ("Nombre:", nombre, "informativo"),
                                      ("Documento:", orden.documento, "informativo:digitos"),
                                      ("Concepto:", CONCEPTO_FIJO, "informativo:texto")])
    # 2026-10-30: el nombre quedó vacío (toque perdido) y el banco rechazó con «Debes introducir el nombre»
    # tras ~20 s. Se ve en la misma lectura de verificación (sin captura extra) y, si falta, se vuelve a llenar.
    _rellenar_si_vacio(ctrl, leidos, "Nombre:", nombre, doble_toque=True)
    _rellenar_si_vacio(ctrl, leidos, "Documento:", orden.documento)
    return "Transferir"


def _llenar_pago_movil(ctrl: ControladorIPhone, orden: OrdenPago) -> str:
    codigo, _nombre = normalizar_banco(orden.banco)
    if not codigo:
        raise RuntimeError(f"no se reconoce el banco destino: '{orden.banco}'")

    ctrl.abrir_pago_movil()
    ctrl.limpiar_formulario()
    actual = ctrl.leer_campo("Banco:") or ""
    if not re.match(rf"^\s*{re.escape(codigo)}\s*[^\w\s]", actual):   # BDV viene preseleccionado
        ctrl.elegir_banco(codigo)
    if orden.documento:
        ctrl.llenar("Documento:", orden.documento)
    ctrl.llenar("Teléfono:", orden.telefono)
    _llenar_monto_verificado(ctrl, orden.monto)
    ctrl.llenar("Concepto:", CONCEPTO_FIJO)
    campos = [("Banco:", codigo, "banco"),
              ("Teléfono:", orden.telefono, "digitos"),
              ("Concepto:", CONCEPTO_FIJO, "informativo:texto")]
    if orden.documento:
        campos.append(("Documento:", orden.documento, "informativo:digitos"))
    leidos = _verificar_campos(ctrl, campos)
    if orden.documento:
        _rellenar_si_vacio(ctrl, leidos, "Documento:", orden.documento)
    return "Pagar"


# ── tramo común: enviar -> revisión -> confirmar -> clave -> comprobante ──
def _esperar_revision_o_error(ctrl: ControladorIPhone, presupuesto_seg: float = 20.0):
    """Tras tocar el botón de envío, sondea hasta que aparezca "Confirmar
    Operación" o un diálogo 'Error'. El modal de "Realizando operación" que
    tapa todo unos segundos no es ni una cosa ni la otra: se atraviesa."""
    limite = time.perf_counter() + presupuesto_seg
    while True:
        lista = ctrl.nodos()
        error = _mensaje_error(lista)
        if error is not None:
            return "error", error
        if any(normalizar(t.texto) == "confirmar operacion" for t in lista):
            return "revision", lista
        if time.perf_counter() >= limite:
            return "timeout", None
        time.sleep(0.3)


def _verificar_revision(ctrl: ControladorIPhone, lista: list[Texto], orden: OrdenPago) -> list[Texto]:
    """Compara la pantalla "Confirmar Operación" con la orden ANTES de
    confirmar. BLOQUEAN (falla limpio) solo los datos de pago: monto, destino
    (cuenta o teléfono) y banco. Nombre e identificación solo avisan en la consola."""
    destino = orden.cuenta if orden.tipo == "TRANSFERENCIA" else orden.telefono
    destino_dig = _solo_digitos(destino)
    codigo_banco, _ = normalizar_banco(orden.banco)
    esperado = normalizar_monto(orden.monto)
    faltan: list[str] = []
    for intento in range(3):
        faltan = []
        montos = []
        for t in lista:
            numero = _extraer_numero(t.texto) if normalizar(t.texto).endswith("bs") else None
            if numero:
                try:
                    montos.append(normalizar_monto(numero))
                except ValueError:
                    pass
        if not any(abs(m - esperado) < 0.005 for m in montos):
            faltan.append(f"monto {orden.monto} (se ve {montos or 'ninguno'})")

        # Destino. Completo (teléfono) o enmascarado ("0134****3794"): el OCR de
        # pantalla completa omite los enmascarados, así que se lee la fila ampliada
        # y se exige el mismo código de banco (4 primeros) y los 4 últimos dígitos.
        digitos = [_solo_digitos(t.texto) for t in lista]
        destino_ok = destino_dig in digitos
        if not destino_ok and orden.tipo == "TRANSFERENCIA":
            ult = destino_dig[-4:]
            if any(ult in d for d in digitos if len(d) >= 4):
                destino_ok = True
            else:
                fila = _valor_en_fila(ctrl, lista, "Destino:")
                d = _solo_digitos(fila or "")
                destino_ok = len(d) >= 8 and d[:4] == destino_dig[:4] and d[-4:] == destino_dig[-4:]
        elif not destino_ok:
            # Pago Móvil: el OCR de pantalla completa a veces OMITE el teléfono (visto
            # 2026-10-24, Bancaribe: la fila "Destino:" salió sin valor y se abortó una
            # orden buena). Se relee la fila ampliada y se exige el teléfono COMPLETO.
            fila = _valor_en_fila(ctrl, lista, "Destino:")
            destino_ok = _solo_digitos(fila or "") == destino_dig
        if not destino_ok:
            faltan.append(f"destino {destino}")

        # Identificación (cédula): INFORMATIVA. No es dato de pago (el dinero va a la cuenta o
        # al teléfono destino); un dígito mal leído por el OCR no puede abortar un pago bueno.
        # Solo se mira lo ya leído (sin otra captura): si no aparece, queda el aviso.
        if orden.documento and _solo_digitos(orden.documento) not in digitos:
            print(f"  !! aviso (no bloquea): la identificación {orden.documento} no aparece en la revisión")

        # Banco: si la pantalla lo muestra ("0134 - BANESCO"), el código tiene que ser el de la orden.
        banco_visto = _valor_en_fila(ctrl, lista, "Banco:")
        if banco_visto is not None and codigo_banco and \
                not re.match(rf"^\s*{re.escape(codigo_banco)}\s*[^\w\s]", banco_visto):
            faltan.append(f"banco {codigo_banco} (se ve {banco_visto!r})")

        # Nombre: en "otros bancos" es el que se TECLEÓ (en terceros es el titular del banco).
        # Es INFORMATIVO: nunca bloquea el pago (el banco valida la cuenta, no el nombre).
        # La app parte los nombres largos en 2 líneas ("ZAMBRANO PETIT JOLEIKAR" /
        # "ALEXANDRA"); antes se leía solo la primera y se abortaba una orden buena
        # (2026-10-24), por eso se juntan las líneas de continuación.
        if any(normalizar(t.texto) == "transferencias a otros bancos" for t in lista):
            nombre_visto = _valor_en_fila(ctrl, lista, "Nombre:", continuacion=True)
            if nombre_visto is not None and not _parecido(nombre_visto, _nombre_para_teclear(orden.nombre)):
                print(f"  !! aviso (no bloquea): el nombre en la revisión es {nombre_visto!r}, "
                      f"la orden dice {orden.nombre!r}")

        if not faltan:
            return lista
        if intento < 2:
            time.sleep(0.6)
            lista = ctrl.nodos()
    raise RuntimeError("la pantalla de revisión no coincide con la orden (falta: " + "; ".join(faltan) + ")")


def _lineas_continuacion(lista: list[Texto], etiqueta: Texto, primeras: list[Texto]) -> list[Texto]:
    """Líneas de MÁS que ocupa el valor de una fila cuando es largo: la app lo
    parte en dos ("ZAMBRANO PETIT JOLEIKAR" / "ALEXANDRA"). Son las líneas pegadas
    justo debajo, a la derecha del rótulo y antes del siguiente rótulo de la
    columna izquierda; se corta en cuanto hay un hueco mayor que un interlineado."""
    siguiente = min((t.y1 for t in lista if t is not etiqueta and t.y1 > etiqueta.y2
                     and abs(t.x1 - etiqueta.x1) < 40), default=float("inf"))
    ultimo_y2 = max(t.y2 for t in primeras)
    candidatas = sorted((t for t in lista if t.x1 > etiqueta.x2 and t.y1 >= etiqueta.y1 + 25
                         and all(t is not p for p in primeras)), key=lambda t: (t.y1, t.x1))
    extra: list[Texto] = []
    for t in candidatas:
        if t.y1 >= siguiente - 20 or t.y1 - ultimo_y2 > 30:
            break
        extra.append(t)
        ultimo_y2 = max(ultimo_y2, t.y2)
    return extra


def _valor_en_fila(ctrl: ControladorIPhone, lista: list[Texto], rotulo: str,
                   continuacion: bool = False) -> str | None:
    """Valor de una fila de la revisión: el texto a la derecha del rótulo en la
    misma fila; si el OCR no lo trae (números enmascarados), se lee la fila
    ampliada de una captura nueva. None si el rótulo no está en pantalla.
    continuacion=True suma las líneas de abajo cuando la app partió un valor largo
    (nombres); los demás valores de la revisión caben siempre en una línea."""
    objetivo = normalizar(rotulo)
    etiqueta = next((t for t in lista if normalizar(t.texto) == objetivo), None)
    if etiqueta is None:
        return None
    mismos = sorted((t for t in lista if t is not etiqueta and abs(t.y1 - etiqueta.y1) < 25
                     and t.x1 > etiqueta.x2), key=lambda t: t.x1)
    if mismos:
        partes = mismos + (_lineas_continuacion(lista, etiqueta, mismos) if continuacion else [])
        return " ".join(t.texto for t in partes)
    png, fresca = ctrl.pantalla()
    return ctrl.valor_en_fila(png, fresca, rotulo)


def _aviso_sin_saldo(lista: list[Texto]) -> str | None:
    """Aviso del formulario cuando el monto supera el saldo ("El monto no puede ser mayor al
    disponible en cuenta"). Antes no se reconocía: el bot tocaba Transferir, esperaba 20 s y
    reportaba "el banco no avanzó a la revisión" (3 órdenes el 2026-10-26)."""
    for t in lista:
        n = normalizar(t.texto)
        if "mayor al disponible" in n or "saldo insuficiente" in n:
            return t.texto
    return None


def _enviar_y_revisar(ctrl: ControladorIPhone, texto_submit: str, orden: OrdenPago,
                      detectar_limite_pm: bool = False) -> list[Texto]:
    """Envía el formulario y llega hasta "Confirmar Operación". Si algo falla
    aquí el pago NO se intentó: fallo limpio."""
    lista = ctrl.desplazar_hasta(texto_submit)
    sin_saldo = _aviso_sin_saldo(lista)
    if sin_saldo:
        raise RuntimeError(f"SIN SALDO en la cuenta: la app dice {sin_saldo!r}. No se envió nada; "
                           "recarga la cuenta")
    faltantes = _errores_validacion(lista)
    if faltantes:
        raise RuntimeError("datos incompletos: " + "; ".join(faltantes))

    ctrl.tap(*ctrl.buscar(texto_submit, lista), que=f"'{texto_submit}'", espera=0.5)

    estado, dato = _esperar_revision_o_error(ctrl)
    if estado == "error":
        if detectar_limite_pm and _es_aviso_limite_pm(dato):
            raise LimitePagoMovil(dato)
        raise RuntimeError(f"el banco rechazó la operación: {dato}")
    if estado == "timeout":
        lista = ctrl.nodos()
        sin_saldo = _aviso_sin_saldo(lista)
        if sin_saldo:
            raise RuntimeError(f"SIN SALDO en la cuenta: la app dice {sin_saldo!r}. No se envió nada; "
                               "recarga la cuenta")
        motivo = "; ".join(_errores_validacion(lista)) or "el banco no avanzó a la revisión"
        raise RuntimeError(f"el banco rechazó los datos: {motivo}")
    return _verificar_revision(ctrl, dato, orden)


class CodigoIPhoneSolicitado(RuntimeError):
    """Tras "Confirmar" la app no pidió su contraseña: iOS mostró "Ingresa el código del iPhone
    para BDVapp" (la app BDV tiene activada la confirmación con Face ID/biometría y Face ID no
    pudo). Visto el 2026-10-28 desde las 2:24: el bot esperaba el campo de contraseña, dejaba la
    orden INCIERTA y, si alguien escribía el código a mano, el pago salía igual (98 órdenes
    "inciertas" que sí se pagaron). Sin el código la operación NO está autorizada: no salió dinero."""


X_CANCELAR_CODIGO_IPHONE = (113, 256)      # la "X" arriba a la izquierda de esa ventana de iOS
MENSAJE_CODIGO_IPHONE = (
    "NO SE PAGÓ: tras Confirmar, el iPhone pidió su CÓDIGO ('Ingresa el código del iPhone para "
    "BDVapp') en vez de la contraseña del banco. La app BDV tiene activada la confirmación con "
    "Face ID / biometría: desactívala en la app (menú > Configuración/Seguridad > Face ID / "
    "Biometría) y reinicia el bot. El bot canceló esa ventana: la operación no se autorizó.")
_CODIGO_IPHONE_PAUSA = False


def _pide_codigo_iphone(lista: list[Texto]) -> bool:
    return any("codigo del iphone" in normalizar(t.texto) for t in lista)


class RechazoLimpio(RuntimeError):
    """La app rechazó la operación con un aviso conocido que, según el usuario
    (2026-10-24), significa que NO se movió dinero: "Banco inactivo" (el banco
    destino no está operativo; salió después de confirmar). Se reporta como FALLO,
    no como INCIERTO. Mismo tipo de excepción que ErrorAutenticacion y
    LimitePagoMovil: avisos que la propia app da y que no dejan dudas.
    Riesgo aceptado: si el banco alguna vez debitara y aun así mostrara el aviso."""


# "saldo insuficiente": agregado el 2026-10-26 a pedido del dueño (3 órdenes quedaron INCIERTAS
# por el diálogo "Saldo insuficiente 043" tras confirmar: el banco no hizo la operación).
FRASES_RECHAZO_LIMPIO = ("inactivo", "saldo insuficiente")


def _rechazo_limpio(lista: list[Texto]) -> str | None:
    """Texto del aviso de rechazo conocido que haya en pantalla (diálogo o
    notificación), o None. No importa el título del diálogo ni dónde salga."""
    for t in lista:
        if any(frase in normalizar(t.texto) for frase in FRASES_RECHAZO_LIMPIO):
            return t.texto
    return None


def _confirmar_y_pagar(ctrl: ControladorIPhone, bdv_password: str, lista_revision: list[Texto],
                       detectar_limite_pm: bool = False) -> None:
    """Toca 'Confirmar', escribe la clave y toca 'Aceptar'. DESDE AQUÍ cualquier
    excepción es estado INCIERTO (el dinero pudo haber salido), salvo los avisos
    de rechazo conocidos (RechazoLimpio)."""
    ctrl.tap(*ctrl.buscar("Confirmar", lista_revision), que="'Confirmar'", espera=0.5)

    def _clave_o_limite(lista):
        rechazo = _rechazo_limpio(lista)
        if rechazo is not None:                  # antes de la clave: ni siquiera se escribió
            return ("rechazo", _mensaje_error(lista) or rechazo)
        if detectar_limite_pm:
            error = _mensaje_error(lista)
            if error is not None and _es_aviso_limite_pm(error):
                return ("limite", error)
        if _pide_codigo_iphone(lista):           # iOS pide el código del teléfono (Face ID activado)
            return ("codigo_iphone", None)
        if _es_comprobante(lista):               # la app no pidió clave y ya hizo el pago
            return ("sin_clave", None)
        punto = ctrl.ubicar_campo("Contraseña:", lista)
        if punto is None:
            # El OCR no leyó la etiqueta (modo oscuro: gris sobre gris, 2026-10-30) pero el cuadro está
            # abierto: "Confirmar Operación" detrás y los botones Aceptar y Cancelar delante. El campo
            # queda ~208 px (en la captura de 1170 px de ancho) encima de esos botones.
            nombres = {normalizar(t.texto) for t in lista}
            acep, canc = ctrl.buscar("Aceptar", lista), ctrl.buscar("Cancelar", lista)
            if "confirmar operacion" in nombres and acep and canc and abs(acep[1] - canc[1]) < 40:
                punto = ((acep[0] + canc[0]) // 2, acep[1] - 208)
                print("  -> el OCR no leyó 'Contraseña:'; el cuadro de la clave está abierto: se usa su posición")
        return ("clave", punto) if punto else None

    encontrado = ctrl.esperar(_clave_o_limite, "campo de contraseña o aviso de límite",
                              intentos=20, intervalo=0.5)
    if encontrado is None:
        raise RuntimeError("no apareció el campo de contraseña tras Confirmar")
    if encontrado[0] == "limite":
        raise LimitePagoMovil(encontrado[1])
    if encontrado[0] == "rechazo":
        raise RechazoLimpio(encontrado[1])
    if encontrado[0] == "codigo_iphone":
        raise CodigoIPhoneSolicitado(MENSAJE_CODIGO_IPHONE)
    if encontrado[0] == "sin_clave":
        print("  -> la app no pidió contraseña: el comprobante ya está en pantalla")
        return
    ctrl.tap(*encontrado[1], que="password", espera=0.5)
    ctrl.borrar_todo()
    # Despacio: tecleada rápido, con el teléfono ocupado se perdía una tecla y el banco respondía
    # "Autenticación incorrecta" con la clave CORRECTA (rechazos sueltos entre pagos buenos con la
    # misma clave, 2026-10-27). La clave lleva símbolos, que van con Shift.
    ctrl.escribir(bdv_password, oculto=True, pausa=0.12)
    ctrl.tap_texto("Aceptar", espera=0.3)


def _esperar_comprobante(ctrl: ControladorIPhone, presupuesto_seg: float = 40.0,
                         detectar_limite_pm: bool = False):
    """Sondea hasta que aparezca el comprobante. Termina apenas aparece. Si
    nunca aparece, el resultado queda INCIERTO (nunca se inventa un éxito).
    Cada vuelta también busca el diálogo 'Atención' de clave rechazada (lo
    cierra y lanza ErrorAutenticacion: rechazo limpio y conocido) y cualquier
    otro 'Error' (se cierra, sigue INCIERTO y su texto viaja en el resultado)."""
    limite = time.perf_counter() + presupuesto_seg
    while True:
        lista = ctrl.nodos()
        # Avisos de rechazo conocidos ("Banco inactivo"): se lanza SIN cerrar el
        # diálogo, para que la captura que se manda a Telegram lo muestre.
        rechazo = _rechazo_limpio(lista)
        if rechazo is not None:
            raise RechazoLimpio(_mensaje_error(lista) or rechazo)
        atencion = _dialogo_atencion(lista)
        if atencion is not None:
            ctrl.tap_texto("Aceptar", lista=lista, espera=0.5)
            raise ErrorAutenticacion(atencion)
        error = _mensaje_error(lista)
        if error is not None:
            if detectar_limite_pm and _es_aviso_limite_pm(error):
                raise LimitePagoMovilTrasClave(error)
            # Error desconocido tras la clave: sigue INCIERTO, pero se devuelve YA y con
            # el diálogo todavía abierto (antes se cerraba con "Aceptar" y se seguía
            # esperando el comprobante hasta agotar ~40 s: la captura salía tarde y sin
            # el aviso). La limpieza viene después de mandar el resultado.
            print(f"  !! diálogo 'Error' tras confirmar: {error!r} (sigue INCIERTO)")
            return False, "", error
        hubo, referencia = _hubo_comprobante(lista)
        if hubo:
            return True, referencia, None
        if time.perf_counter() >= limite:
            return False, "", None
        time.sleep(0.3)


def _verificar_banco_vs_cuenta(orden: OrdenPago) -> None:
    """En una transferencia los 4 primeros dígitos de la cuenta SON el código del
    banco. Si el banco que dice la orden no coincide (ej. banco 0102 con una cuenta
    0105…) algo está mal en la orden: se rechaza SIN tocar el teléfono. Si no, el
    bot elegiría un banco que no corresponde a la cuenta (o, como pasó el
    2026-10-24, buscaría 0102 —que no existe en "otros bancos"— deslizando toda la
    lista) y perdería ~25 s antes de fallar."""
    if orden.tipo != "TRANSFERENCIA":
        return
    codigo, _ = normalizar_banco(orden.banco)
    prefijo = orden.cuenta[:4]
    if codigo and codigo != prefijo:
        raise RuntimeError(
            f"el banco de la orden ({codigo}) no coincide con el código de la cuenta "
            f"({prefijo}…): orden inconsistente, no se toca el teléfono")


def _resultado_rechazo(ctrl: ControladorIPhone, orden: OrdenPago, exc: RechazoLimpio) -> ResultadoPago:
    """FALLO (no incierto) por un aviso de rechazo conocido. La captura se toma YA, con
    el aviso a la vista (el diálogo sigue abierto: se cierra después de mandar el resultado)."""
    ruta = _capturar(ctrl, orden.id_orden)
    return ResultadoPago(
        exito=False, incierto=False,
        mensaje=f"El banco rechazó la operación: la app mostró {str(exc)!r}. "
                "Es un aviso del banco, no un error del bot: no se movió dinero.",
        referencia=None, ruta_captura=ruta, volver_inicio_pendiente=True)


def _resultado_codigo_iphone(ctrl: ControladorIPhone, orden: OrdenPago, exc: Exception) -> ResultadoPago:
    """La captura se toma CON la ventana del código a la vista; después se cancela con su X
    (sin código la operación no se autoriza) y el bot queda en pausa hasta reiniciarlo."""
    global _CODIGO_IPHONE_PAUSA
    ruta = _capturar(ctrl, orden.id_orden)
    for _ in range(3):
        try:
            lista = ctrl.nodos()
        except Exception:
            break
        if not _pide_codigo_iphone(lista):
            break
        ctrl.tap(*X_CANCELAR_CODIGO_IPHONE, que="cancelar código del iPhone (X)", espera=1.0)
    _CODIGO_IPHONE_PAUSA = True
    return ResultadoPago(exito=False, incierto=False, mensaje=str(exc), referencia=None,
                         ruta_captura=ruta, volver_inicio_pendiente=True)


def _intentar_pago(orden: OrdenPago, cuenta_debitar: str, ctrl: ControladorIPhone,
                   bdv_password: str, por_transferencia: bool = False) -> ResultadoPago:
    # ── Tramo 1: navegar y llenar. Fallo aquí = fallo limpio. ──
    interbancaria = False      # "otros bancos": el banco confirma DESPUÉS del comprobante
    try:
        _verificar_banco_vs_cuenta(orden)      # antes de tocar el teléfono
        _volver_inicio(ctrl)
        if orden.tipo == "TRANSFERENCIA":
            if orden.cuenta.startswith(PREFIJO_BDV) and len(orden.cuenta) == 20:
                texto_submit = _llenar_terceros(ctrl, orden, cuenta_debitar)
            else:
                interbancaria = True
                texto_submit = _llenar_otros_bancos(ctrl, orden, cuenta_debitar)
        elif orden.tipo == "PAGO_MOVIL" and por_transferencia:
            # Límite de Pago Móvil agotado: por Transferencias -> otros bancos, instrumento Teléfono.
            interbancaria = True
            texto_submit = _llenar_otros_bancos(ctrl, orden, cuenta_debitar, por_telefono=True)
        elif orden.tipo == "PAGO_MOVIL":
            texto_submit = _llenar_pago_movil(ctrl, orden)
        else:
            raise RuntimeError(f"tipo de transacción no soportado: {orden.tipo}")

        detectar = (orden.tipo == "PAGO_MOVIL" and not por_transferencia
                    and not _es_pm_a_bdv(orden))
        lista_revision = _enviar_y_revisar(ctrl, texto_submit, orden, detectar_limite_pm=detectar)

    except LimitePagoMovil:
        raise          # el reintento por transferencia empieza con _volver_inicio
    except Exception as exc:
        # NUNCA se limpia la pantalla antes de devolver: el resultado (con su
        # captura) tiene que llegar a Telegram YA; el orquestador vuelve a Inicio
        # después (volver_inicio_pendiente). Antes, un _volver_inicio fallido
        # retenía el mensaje ~38 s.
        ruta = _capturar(ctrl, orden.id_orden)
        return ResultadoPago(exito=False, incierto=False,
                             mensaje=f"Falló antes de confirmar el pago: {exc}",
                             referencia=None, ruta_captura=ruta, volver_inicio_pendiente=True)

    # ── Tramo 2: confirmar + clave. Fallo aquí = INCIERTO. ──
    try:
        _confirmar_y_pagar(ctrl, bdv_password, lista_revision, detectar_limite_pm=detectar)
    except LimitePagoMovil:
        raise          # la clave nunca se escribió
    except RechazoLimpio as exc:
        return _resultado_rechazo(ctrl, orden, exc)
    except CodigoIPhoneSolicitado as exc:
        return _resultado_codigo_iphone(ctrl, orden, exc)
    except Exception as exc:
        ruta = _capturar(ctrl, orden.id_orden)
        extra = ""
        try:
            if _es_pantalla_login(ctrl.nodos()):
                # 2026-10-26 18:36: la app cerró sesión justo después de escribir la clave.
                extra = (" La app BDV CERRÓ LA SESIÓN en ese momento (no se llegó a tocar 'Aceptar'): "
                         "muy probablemente no salió dinero. Inicia sesión a mano en el iPhone.")
        except Exception:
            pass
        return ResultadoPago(exito=False, incierto=True,
                             mensaje=f"Error al confirmar o pagar ({exc}).{extra} Estado INCIERTO: "
                                     "revisa el banco antes de reintentar.",
                             referencia=None, ruta_captura=ruta, volver_inicio_pendiente=True)

    # ── Tramo 3: verificar el comprobante. ──
    try:
        hubo, referencia, error_visto = _esperar_comprobante(ctrl, detectar_limite_pm=detectar)
        ruta = _capturar(ctrl, orden.id_orden)
        if hubo:
            resultado = ResultadoPago(exito=True, incierto=False, mensaje="Comprobante detectado",
                                      referencia=referencia, ruta_captura=ruta, volver_inicio_pendiente=True)
            if interbancaria:
                _registrar_interbancaria(orden, referencia)
                resultado.interbancaria = True     # el orquestador vigila el aviso final del banco
            return resultado
        motivo = f" La app mostró: {error_visto!r}." if error_visto else ""
        return ResultadoPago(exito=False, incierto=True,
                             mensaje="No se detectó comprobante tras confirmar." + motivo +
                                     " Estado INCIERTO: revisa los movimientos del banco antes de reintentar.",
                             referencia=None, ruta_captura=ruta, volver_inicio_pendiente=True)
    except ErrorAutenticacion:
        raise
    except LimitePagoMovilTrasClave:
        raise
    except RechazoLimpio as exc:
        return _resultado_rechazo(ctrl, orden, exc)
    except Exception as exc:
        ruta = _capturar(ctrl, orden.id_orden)
        return ResultadoPago(exito=False, incierto=True,
                             mensaje=f"Error verificando el resultado ({exc}). Estado INCIERTO: "
                                     "revisa el banco antes de reintentar.",
                             referencia=None, ruta_captura=ruta, volver_inicio_pendiente=True)


# ── freno contra el bloqueo del usuario del banco ────────────────────────
# Los bancos bloquean el usuario tras unos pocos rechazos de clave SEGUIDOS. El 2026-10-27 hubo
# 4 rechazos seguidos (7:14-7:18) y desde ahí fallaron todas. Con FALLOS_CLAVE_MAX rechazos
# seguidos el bot deja de usar la clave (sin tocar el teléfono) hasta que se reinicie; un pago
# con éxito vuelve el contador a cero.
FALLOS_CLAVE_MAX = 2
_FALLOS_CLAVE_SEGUIDOS = 0
MENSAJE_CLAVE_PAUSADA = (
    "NO SE INTENTÓ (no se tocó el teléfono ni salió dinero): el banco rechazó la clave {n} veces "
    "seguidas ('Autenticación incorrecta'). El bot dejó de usarla para que el banco no BLOQUEE el "
    "usuario. Revisa en la app BDV que puedas entrar con esa clave (y que el usuario no esté "
    "bloqueado); si la cambiaste, corrígela en el panel (Configuración > Clave del banco) y "
    "reinicia el bot.")


def _fallo_de_clave() -> int:
    global _FALLOS_CLAVE_SEGUIDOS
    _FALLOS_CLAVE_SEGUIDOS += 1
    return _FALLOS_CLAVE_SEGUIDOS


def _resultado_clave_rechazada(ctrl: ControladorIPhone, orden: OrdenPago, exc: Exception) -> ResultadoPago:
    ruta = _capturar(ctrl, orden.id_orden)
    return ResultadoPago(
        exito=False, incierto=False,
        mensaje=f"El banco rechazó la clave ({exc}); van {_FALLOS_CLAVE_SEGUIDOS} rechazo(s) seguidos. "
                "No se movió dinero — la app confirma que fue un rechazo de autenticación, no de la "
                "operación. " + (MENSAJE_CLAVE_PAUSADA.format(n=_FALLOS_CLAVE_SEGUIDOS)
                                 if _FALLOS_CLAVE_SEGUIDOS >= FALLOS_CLAVE_MAX else ""),
        referencia=None, ruta_captura=ruta, volver_inicio_pendiente=True)


def _pagar_con_reintento_clave(orden: OrdenPago, cuenta_debitar: str, ctrl: ControladorIPhone,
                               bdv_password: str, por_transferencia: bool) -> ResultadoPago:
    """Un intento y, SOLO si el banco rechaza la clave con su diálogo conocido, un segundo
    intento completo (la propia app confirma que el primero no tocó el dinero) — salvo que ya se
    llegó a FALLOS_CLAVE_MAX rechazos seguidos (freno contra el bloqueo del usuario)."""
    global _FALLOS_CLAVE_SEGUIDOS
    try:
        resultado = _intentar_pago(orden, cuenta_debitar, ctrl, bdv_password, por_transferencia)
    except ErrorAutenticacion as exc:
        if _fallo_de_clave() >= FALLOS_CLAVE_MAX:
            return _resultado_clave_rechazada(ctrl, orden, exc)
        print(f"  !! el banco rechazó la clave ({exc}); reintentando la operación completa una vez")
        try:
            resultado = _intentar_pago(orden, cuenta_debitar, ctrl, bdv_password, por_transferencia)
        except ErrorAutenticacion as exc2:
            _fallo_de_clave()
            return _resultado_clave_rechazada(ctrl, orden, exc2)
    if resultado.exito:
        _FALLOS_CLAVE_SEGUIDOS = 0          # la clave funcionó: el contador vuelve a cero
    return resultado


def volver_inicio_tras_pago(serial: str | None = None) -> None:
    """Deja la app en Inicio tras un pago exitoso, ya enviada la captura. Nunca lanza."""
    try:
        ctrl = _controlador(serial)
    except Exception as exc:
        print(f"  !! no se pudo volver a Inicio: {exc}")
        return
    try:
        _volver_inicio(ctrl)
    except Exception as exc:
        print(f"  !! no se pudo volver a Inicio: {exc}")
        # 2026-10-26 21:55: la red de la PC se cortó (mismo minuto que Telegram) y el bot siguió
        # leyendo una pantalla que no era la real; la orden siguiente falló igual. Se descarta la
        # conexión con el iPhone: la próxima orden abre una nueva (~4 s).
        print("  -> se descarta la conexión con el iPhone; la próxima orden abre una nueva")
        _descartar_controlador()


def _no_intentada(mensaje: str) -> ResultadoPago:
    return ResultadoPago(exito=False, incierto=False,
                         mensaje="NO SE INTENTÓ (no se tocó el teléfono ni salió dinero): " + mensaje,
                         referencia=None, ruta_captura="")


_AUSENTE = ("usbmux has no device", "DeviceNotFound", "Connection was terminated", "tunnel transport closed",
            "device disconnected")


def _iphone_ausente(texto: str) -> bool:
    return any(p.lower() in (texto or "").lower() for p in _AUSENTE)


def _reidentificar(log=print) -> str:
    """Olvida el iPhone anterior y reconoce el que está conectado AHORA (su registro, su cuenta, su clave)."""
    IDENTIDAD_IPHONE.clear()
    PERFIL_ACTIVO.clear()
    _descartar_controlador()
    return esperar_dispositivo_listo("", limite_seg=120.0, intervalo=4.0, log=log)


def _iniciar_vigilante() -> None:
    """Hilo que mira cada ~6 s qué iPhone hay conectado. Si el que usa el bot ya no está y hay OTRO
    conectado, lo reconoce solo (con su cuenta y su clave del registro), sin reiniciar el bot ni tocar
    el .env: así un iPhone cambiado o enchufado de nuevo toma el cambio antes de la siguiente orden."""
    if _VIGILANTE:
        return

    def mirar():
        while True:
            time.sleep(6.0)
            if _LOCK_ORDEN.locked():                 # pagando: no se toca nada
                continue
            try:
                conectados = {d["udid"] for d in dispositivo.listar_iphones()}
            except Exception:
                continue
            activo = IDENTIDAD_IPHONE.get("udid")
            if len(conectados) != 1 or (activo and activo in conectados):
                continue
            nuevo = next(iter(conectados))
            if time.time() - _ULTIMO_FALLO_REGISTRO.get(nuevo, 0) < 60:
                continue
            if not _LOCK_ORDEN.acquire(blocking=False):
                continue
            try:
                print(f"  -> cambió el iPhone conectado ({activo or 'ninguno'} -> {nuevo}): se reconoce el nuevo")
                print("  -> iPhone listo: " + _reidentificar(print))
            except Exception as exc:
                _ULTIMO_FALLO_REGISTRO[nuevo] = time.time()
                print(f"  !! el iPhone nuevo ({nuevo}) no quedó listo: {exc}")
            finally:
                _LOCK_ORDEN.release()

    _VIGILANTE.append(threading.Thread(target=mirar, name="vigilar-iphone", daemon=True))
    _VIGILANTE[0].start()


def ejecutar_pago(orden: OrdenPago, serial: str | None = None, bdv_password: str = "") -> ResultadoPago:
    """Punto de entrada, mismo contrato y mismas rutas que ejecutor.ejecutar_pago. Una orden a la vez
    (_LOCK_ORDEN); si el iPhone cambió, lo reconoce antes de pagar."""
    with _LOCK_ORDEN:
        if not IDENTIDAD_IPHONE:
            try:
                print("  -> reconociendo el iPhone conectado: " + _reidentificar(print))
            except ConfiguracionIPhone as exc:
                return _no_intentada(str(exc))
            except Exception as exc:
                return _no_intentada(f"el iPhone no está listo: {exc}")
        resultado = _ejecutar_pago_interno(orden, serial, bdv_password)
        if _iphone_ausente(resultado.mensaje):
            IDENTIDAD_IPHONE.clear()      # la próxima orden (o el vigilante) reconoce el iPhone que haya
        return resultado


def _ejecutar_pago_interno(orden: OrdenPago, serial: str | None = None, bdv_password: str = "") -> ResultadoPago:
    cuenta_debitar = PERFIL_ACTIVO.get("cuenta") or requerido("CUENTA_DEBITAR_DESC")
    bdv_password = PERFIL_ACTIVO.get("clave") or bdv_password
    if _FALLOS_CLAVE_SEGUIDOS >= FALLOS_CLAVE_MAX:
        print(f"  !! clave en pausa ({_FALLOS_CLAVE_SEGUIDOS} rechazos seguidos): la orden no se intenta")
        return ResultadoPago(exito=False, incierto=False,
                             mensaje=MENSAJE_CLAVE_PAUSADA.format(n=_FALLOS_CLAVE_SEGUIDOS),
                             referencia=None, ruta_captura="")
    if _CODIGO_IPHONE_PAUSA:
        print("  !! en pausa: la app pide el código del iPhone (Face ID activado); la orden no se intenta")
        return ResultadoPago(exito=False, incierto=False,
                             mensaje="NO SE INTENTÓ (no se tocó el teléfono ni salió dinero): " + MENSAJE_CODIGO_IPHONE,
                             referencia=None, ruta_captura="")
    try:
        ctrl = _controlador(serial)
    except Exception:
        _descartar_controlador()
        raise

    por_transferencia = (orden.tipo == "PAGO_MOVIL" and not _es_pm_a_bdv(orden)
                         and _base.modo_transferencia_activo())
    try:
        resultado = _pagar_con_reintento_clave(orden, cuenta_debitar, ctrl, bdv_password,
                                               por_transferencia)
    except (LimitePagoMovil, LimitePagoMovilTrasClave) as exc:
        _base._activar_modo_transferencia()
        print(f"  !! límite de Pago Móvil agotado ({exc}); modo transferencia activo "
              "hasta medianoche — reintentando esta orden por transferencia")
        por_transferencia = True
        resultado = _pagar_con_reintento_clave(orden, cuenta_debitar, ctrl, bdv_password, True)
    except Exception as exc:
        _descartar_controlador()      # la próxima orden abre una conexión nueva
        if _iphone_ausente(str(exc)):
            IDENTIDAD_IPHONE.clear()
        raise
    resultado.via_transferencia = por_transferencia
    if not resultado.ruta_captura:
        _descartar_controlador()      # no se pudo capturar: la conexión probablemente murió
    elif not resultado.exito and "volver a Inicio" in (resultado.mensaje or ""):
        _descartar_controlador()      # misma razón que en volver_inicio_tras_pago
    return resultado
