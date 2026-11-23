"""Ejecuta una orden de pago en el emulador y devuelve el resultado + captura.

Tres flujos, elegidos según los datos de la orden:
  - TRANSFERENCIA con cuenta 0102 (20 dígitos) -> "Cuentas de terceros" (BDV a BDV).
  - TRANSFERENCIA con cualquier otra cuenta    -> "Cuentas otros bancos".
  - PAGO_MOVIL (trae teléfono)                 -> "PagomóvilBDV".

Coordenadas y orden de llenado portados del proyecto principal
(mesa/banks/bluestacks_bdv.py), que ya las tiene probadas contra la app real
— en particular: en "Cuentas otros bancos" elegir "Tipo de instrumento"
REINICIA el resto del formulario, así que el orden importa (debitar -> tipo
de instrumento -> banco -> tipo de documento -> el resto).

Regla de oro: pase lo que pase, se toma una captura de la última pantalla y
se devuelve junto con el resultado, para que un humano la vea en Telegram.
Nunca se reintenta un pago automáticamente si algo falló a mitad de camino.

Línea de seguridad (antes/después de escribir la clave): todo lo que pasa
ANTES de tocar "Confirmar" + escribir la clave, si falla, es un fallo limpio
(no se tocó nada del banco). Todo lo que pasa DESDE que se escribe la clave
en adelante, si falla, es un estado INCIERTO — el dinero pudo haber salido —
y nunca se reintenta solo.
"""
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from adb_utils import ControladorADB, detectar_serial
from bancos import normalizar_banco
from ordenes import OrdenPago, normalizar_monto
from config import requerido

PREFIJO_BDV = "0102"
CONCEPTO_FIJO = "pago"  # concepto de TODAS las operaciones, a pedido del usuario

# Coordenadas de ESTA PC (PC de produccion, BlueStacks 1080x1920, densidad
# 240), medidas con inspeccionar.py el 2026-09-25 con "Cuenta a debitar" YA
# elegida (los campos de terceros y otros bancos bajan 36 px; Pago Movil no
# tiene ese selector). Paso de 117 px entre campos; en la otra PC es de
# 156 px, así que al actualizar ejecutor.py NO se traen sus valores.
CAMPO_CUENTA_DEBITAR = (540, 287)  # común a terceros y otros_bancos

CAMPOS_TERCEROS = {
    "cuenta_destino": (500, 440),
    "monto": (540, 557),
    "concepto": (540, 674),
}

CAMPOS_OTROS_BANCOS = {
    "tipo_instrumento": (540, 440),
    "banco": (540, 557),
    "cuenta_destino": (500, 674),
    "nombre": (540, 791),
    "tipo_documento": (197, 908),
    "documento": (715, 908),
    "monto": (540, 1025),
    "concepto": (540, 1142),
}

CAMPOS_PAGO_MOVIL = {
    "documento": (500, 404),
    "banco": (540, 521),
    "telefono": (540, 638),
    "monto": (540, 755),
    "concepto": (540, 872),
}

CARPETA_CAPTURAS = Path(__file__).resolve().parent / "capturas"
CARPETA_CAPTURAS.mkdir(exist_ok=True)


@dataclass
class ResultadoPago:
    exito: bool
    incierto: bool  # True = no se pudo confirmar si el pago salió o no
    mensaje: str
    referencia: str | None
    ruta_captura: str
    # True si un Pago Móvil a otro banco se hizo por "Transferencias a otros
    # bancos" con instrumento Teléfono (límite diario de Pago Móvil agotado).
    via_transferencia: bool = False
    # True si el ejecutor dejó la app en una pantalla intermedia a propósito para
    # devolver el resultado YA (ejecutor_iphone): el orquestador llama a
    # volver_inicio_tras_pago() después de mandar la captura. Android no lo usa.
    volver_inicio_pendiente: bool = False


def _letra_tipo_documento(documento: str) -> str:
    """'V-12345678' -> 'V'. Sin prefijo reconocible, se asume 'V' (cédula
    venezolana, el caso más común). Los mensajes reales de este bot no
    traen la letra en el campo 🆔, así que casi siempre cae en el default."""
    if documento:
        m = re.match(r"\s*([VEJPGCvejpgc])[-\s]?\d", documento)
        if m:
            return m.group(1).upper()
    return "V"


def _monto_para_campo(monto: str) -> str:
    """El monto del mensaje ('2.000,00', con punto de miles) NO se puede
    escribir tal cual: la app lo lee como 20,00. Visto en vivo por Rower el
    2026-09-22 con dinero real. Pasa siempre por normalizar_monto() (el mismo
    cálculo de la validación del tope) y devuelve el número sin separador de
    miles y con coma decimal: '2000,00'."""
    return f"{normalizar_monto(monto):.2f}".replace(".", ",")


def _extraer_numero(texto: str) -> str | None:
    """Saca el primer tramo con pinta de número ('2.500,00', '250,00', ...)
    de un texto que puede traer un prefijo de moneda u otro adorno del
    campo. None si no hay nada parecido a un número."""
    m = re.search(r"\d[\d.,]*", texto)
    return m.group() if m else None


def _llenar_monto_verificado(ctrl: ControladorADB, punto, monto_orden: str,
                             intentos: int = 3) -> list:
    """Como ctrl.llenar(), pero SOLO para el campo de monto: después de
    escribir, LEE lo que quedó en el campo y lo compara contra el monto
    real de la orden (los dos pasados por normalizar_monto(), porque la
    app puede reformatear con separador de miles al mostrarlo). Si no
    coincide, reintenta escribiendo de nuevo; si tras `intentos` sigue sin
    coincidir, se rinde con un error — esto pasa siempre ANTES de tocar
    Confirmar, así que es un fallo limpio, no un estado incierto.

    Visto en vivo el 2026-09-23: una orden de 2.500,00 Bs se ejecutó por
    250,00 Bs y el bot la reportó como éxito. El cálculo en Python daba
    bien (2500,00); el fallo fue al escribir en el campo — un caso aislado
    entre más de diez órdenes con el mismo formato de monto ese mismo día,
    así que no es un bug de fórmula sino un fallo puntual de tecleo (ADB o
    el propio campo con máscara). El código nunca comprobaba qué había
    quedado escrito antes de enviar el pago; ahora sí.

    Devuelve el volcado con el que verificó: se llena al FINAL del
    formulario (después del concepto), así que ese volcado es el estado
    definitivo antes de enviar y sirve también para buscar errores de
    validación y ubicar el botón de envío sin volver a leer la pantalla."""
    esperado = normalizar_monto(monto_orden)
    texto_campo = _monto_para_campo(monto_orden)
    actual_texto = ""
    for intento in range(intentos):
        ctrl.tap(*punto, que="monto")
        ctrl.limpiar_campo()
        ctrl.text(texto_campo)
        lista = ctrl.nodos()
        actual_texto = ctrl.leer_campo(punto, lista)
        numero = _extraer_numero(actual_texto)
        try:
            actual = normalizar_monto(numero) if numero else None
        except ValueError:
            actual = None
        if actual == esperado:
            return lista
        print(f"  !! el campo de monto quedó en {actual_texto!r} "
              f"(se esperaba {texto_campo!r}), reintentando")
    raise RuntimeError(
        f"no se pudo escribir el monto correctamente tras {intentos} intentos "
        f"(quedó {actual_texto!r}, se esperaba {texto_campo!r}) — no se envía el pago"
    )


def _nombre_para_teclear(nombre: str) -> str:
    """`adb shell input text` no puede escribir letras con tilde ni la ñ:
    Android lanza NullPointerException y el campo queda vacío (visto en
    vivo el 2026-09-26 con "NAPOLEÓN"). Se escribe sin tildes (NAPOLEON,
    NUNEZ) y sin espacios de sobra; solo letras, espacios y . ' -"""
    t = unicodedata.normalize("NFKD", nombre or "")
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = "".join(ch for ch in t if ch.isascii() and (ch.isalnum() or ch in " .'-"))
    return " ".join(t.split())


def _capturar(ctrl: ControladorADB, id_orden: str) -> str:
    """Nunca lanza: si el propio screencap falla (dispositivo sin responder,
    visto en vivo cuando uiautomator tampoco vuelca la pantalla), devuelve
    '' en vez de dejar pasar un PNG corrupto -- mejor un mensaje sin
    captura que un archivo inválido que Telegram rechaza aparte."""
    destino = str(CARPETA_CAPTURAS / f"{id_orden}_{int(time.time())}.png")
    try:
        return ctrl.capturar_pantalla(destino)
    except Exception as exc:
        print(f"  !! no se pudo capturar la pantalla: {exc}")
        return ""


def _mensaje_error(ctrl: ControladorADB, lista=None) -> str | None:
    """Si hay un diálogo 'Error' abierto devuelve su texto, si no None."""
    lista = lista if lista is not None else ctrl.nodos()
    descs = [a.get("content-desc", "") for a in lista]
    if "Error" not in descs:
        return None
    # Primero se descartan los nodos vacíos y DESPUÉS se toman 2: entre el
    # título y el mensaje hay nodos sin texto (visto en vivo el 2026-09-26
    # con "El monto introducido es inválido"); al revés devolvía ''.
    return " ".join([d for d in descs[descs.index("Error") + 1:] if d][:2])


class ErrorAutenticacion(RuntimeError):
    """La app rechazó la clave con su propio diálogo 'Atención' (visto en
    vivo el 2026-09-22: 'Error al autenticar con contraseña'). A diferencia
    de un fallo genérico tras escribir la clave, ESTE caso sí es seguro de
    reintentar automáticamente: la propia app confirma que la
    autenticación falló ANTES de tocar la operación — no es un estado
    incierto, es un rechazo limpio y conocido."""


def _dialogo_atencion(ctrl: ControladorADB, lista=None) -> str | None:
    """Diálogo 'Atención' de rechazo de clave. Solo reconoce ESTE mensaje
    específico (menciona "contraseña"), no cualquier diálogo titulado
    'Atención' — uno con otro texto podría significar otra cosa (ej. una
    confirmación real, no un rechazo) y merece su propio mapeo antes de
    decidir qué hacer con él; tratarlo igual sin verificar el texto sería
    inventar un comportamiento no confirmado contra la app real."""
    lista = lista if lista is not None else ctrl.nodos()
    descs = [a.get("content-desc", "") for a in lista]
    if "Atención" not in descs:
        return None
    texto = " ".join([d for d in descs[descs.index("Atención") + 1:] if d][:2])
    if "contraseñ" not in texto.lower():
        return None
    return texto


class LimitePagoMovil(RuntimeError):
    """El banco respondió 'El monto introducido es inválido' al tocar Pagar
    en un Pago Móvil a OTRO banco. Según el usuario (2026-09-26), ese aviso
    sale cuando se agota el límite diario de Pago Móvil a otros bancos. Sale
    ANTES de Confirmar y de la clave: no se movió dinero, es seguro reintentar
    la misma orden por otra ruta."""


class LimitePagoMovilTrasClave(RuntimeError):
    """El mismo aviso 'El monto introducido es inválido', pero DESPUÉS de
    escribir la clave (visto en vivo en esta PC el 2026-09-27: aquí no sale
    al tocar Confirmar sino tras la clave). ÚNICA excepción a la regla de no
    reintentar nada posterior a la clave, por decisión explícita del usuario
    (2026-09-27): el banco rechaza el monto de forma explícita, así que ESTA
    orden se reintenta por transferencia y se activa el modo transferencia.
    Riesgo aceptado: si alguna vez el banco debitara y aun así mostrara este
    aviso, la orden se pagaría dos veces."""


# Día en que se agotó el límite de Pago Móvil a otros bancos (None = no se
# agotó). Vive en memoria: se apaga solo a medianoche (el límite es diario) o
# al reiniciar el bot -- en ese caso la primera orden vuelve a probar por Pago
# Móvil, recibe el aviso y cambia sola (unos 20 s, sin mover dinero).
_LIMITE_PM_AGOTADO_EN: date | None = None


def modo_transferencia_activo() -> bool:
    return _LIMITE_PM_AGOTADO_EN == date.today()


def _activar_modo_transferencia() -> None:
    global _LIMITE_PM_AGOTADO_EN
    _LIMITE_PM_AGOTADO_EN = date.today()


def _es_aviso_limite_pm(texto: str) -> bool:
    t = unicodedata.normalize("NFKD", texto or "").lower()
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return "monto introducido es invalido" in t


def _es_pm_a_bdv(orden: OrdenPago) -> bool:
    codigo, _ = normalizar_banco(orden.banco)
    return codigo == PREFIJO_BDV


def _volver_inicio_seguro(ctrl: ControladorADB, ya_confirmado: bool = False):
    """Se llama SIEMPRE tras la captura, sea éxito o fallo: cierra un diálogo
    de error si lo hay y regresa a Inicio. Nunca lanza, para no tapar el
    resultado real de la orden.

    `ya_confirmado=True`: se llama justo después de que `hubo_comprobante()`
    ya confirmó el comprobante en pantalla — sabemos con certeza que no hay
    ningún diálogo de error, así que no hace falta un volcado más solo para
    comprobarlo (cada volcado cuesta ~2 s medido contra el emulador real;
    es el costo dominante de todo el flujo)."""
    try:
        if not ya_confirmado and _mensaje_error(ctrl) is not None:
            ctrl.tap_desc("Aceptar")
        ctrl.volver_inicio()
    except Exception as exc:
        print(f"  !! no se pudo volver a Inicio: {exc}")


# ── Llenado de cada formulario (hasta ANTES de tocar el botón de envío) ──
# Cada uno devuelve (texto del botón de envío, volcado final). El monto va
# SIEMPRE al final (después del concepto) para que su verificación sea la
# última lectura antes de enviar y ese mismo volcado se reutilice.

def _llenar_terceros(ctrl: ControladorADB, orden: OrdenPago, cuenta_debitar: str):
    ctrl.tap_desc("Cuentas de terceros")
    ctrl.tap(*CAMPO_CUENTA_DEBITAR, que="cuenta a debitar")
    ctrl.tap_desc(cuenta_debitar)
    c = CAMPOS_TERCEROS
    ctrl.llenar(c["cuenta_destino"], orden.cuenta, "cuenta destino")
    ctrl.llenar(c["concepto"], CONCEPTO_FIJO, "concepto")
    lista = _llenar_monto_verificado(ctrl, c["monto"], orden.monto)
    return "Transferir", lista


def _llenar_otros_bancos(ctrl: ControladorADB, orden: OrdenPago, cuenta_debitar: str,
                         por_telefono: bool = False):
    """`por_telefono=True`: la misma pantalla con "Tipo de instrumento" =
    "Teléfono" y el teléfono de la orden en "Instrumento destino". Medido en
    vivo el 2026-09-26: con "Teléfono" el formulario queda en las MISMAS
    coordenadas que con "Cuenta" (Banco, Instrumento destino, Nombre, Tipo,
    Documento, Monto, Concepto, Transferir), y el campo acepta el teléfono
    tal cual llega en la orden (04XXXXXXXXX)."""
    codigo, _nombre = normalizar_banco(orden.banco)
    if not codigo:
        raise RuntimeError(f"no se reconoce el banco destino: '{orden.banco}'")

    ctrl.tap_desc("Cuentas otros bancos")
    # El formulario de "Cuentas otros bancos" NO se resetea solo al volver a
    # entrar a esta pantalla -- arrastra el estado roto de un intento
    # anterior (visto en vivo el 2026-09-30: una orden encontró "Banco"
    # todavía en "Error al cargar" de la orden previa, y el desplegable de
    # "Tipo de instrumento" no abrió, probablemente porque el formulario
    # quedó en un estado inconsistente). "Limpiar" está siempre visible en
    # esta pantalla; tocarlo ANTES de elegir la cuenta a debitar garantiza
    # que se arranca de cero sin importar en qué quedó la orden anterior
    # -- va antes, no después, por si "Limpiar" también borra esa selección.
    ctrl.tap_desc("Limpiar")
    ctrl.tap(*CAMPO_CUENTA_DEBITAR, que="cuenta a debitar")
    ctrl.tap_desc(cuenta_debitar)

    c = CAMPOS_OTROS_BANCOS
    # El orden importa: "Tipo de instrumento" reinicia el resto del
    # formulario, así que va antes de tocar cualquier otro campo de abajo.
    instrumento = "Teléfono" if por_telefono else "Cuenta"
    ctrl.seleccionar_opcion_popup(c["tipo_instrumento"], instrumento, que="tipo de instrumento")
    ctrl.seleccionar_banco(c["banco"], codigo)
    letra = _letra_tipo_documento(orden.documento)
    ctrl.seleccionar_opcion_popup(c["tipo_documento"], letra, que="tipo de documento")
    if por_telefono:
        ctrl.llenar(c["cuenta_destino"], orden.telefono, "teléfono destino")
    else:
        ctrl.llenar(c["cuenta_destino"], orden.cuenta, "cuenta destino")
    ctrl.llenar(c["nombre"], _nombre_para_teclear(orden.nombre), "nombre")
    ctrl.llenar(c["documento"], orden.documento, "documento")
    ctrl.llenar(c["concepto"], CONCEPTO_FIJO, "concepto")
    lista = _llenar_monto_verificado(ctrl, c["monto"], orden.monto)
    return "Transferir", lista


def _llenar_pago_movil(ctrl: ControladorADB, orden: OrdenPago):
    codigo, _nombre = normalizar_banco(orden.banco)
    if not codigo:
        raise RuntimeError(f"no se reconoce el banco destino: '{orden.banco}'")

    ctrl.tap_desc("PagomóvilBDV")
    c = CAMPOS_PAGO_MOVIL
    ctrl.seleccionar_banco(c["banco"], codigo)
    if orden.documento:
        ctrl.llenar(c["documento"], orden.documento, "documento")
    ctrl.llenar(c["telefono"], orden.telefono, "teléfono")
    ctrl.llenar(c["concepto"], CONCEPTO_FIJO, "concepto")
    lista = _llenar_monto_verificado(ctrl, c["monto"], orden.monto)
    return "Pagar", lista


# ── Tramo común: enviar -> revisión -> confirmar -> clave -> comprobante ──

def _esperar_revision_o_error(ctrl: ControladorADB, intentos: int = 20,
                              intervalo: float = 0.3, presupuesto_seg: float = 15.0):
    """Tras tocar el botón de enviar, sondea hasta que aparezca la pantalla
    de revisión ('Confirmar Operación') o un diálogo de error — lo que
    ocurra primero. Entre medio, el banco puede mostrar un modal de
    "Realizando operación" (o similar) que tapa todo unos segundos; eso NO
    es ni error ni revisión, así que el sondeo simplemente lo atraviesa
    reintentando.

    El presupuesto real es `presupuesto_seg`, no `intentos` — cada lectura
    de pantalla cuesta ~2,1 s medido contra el emulador real (no ~0 s), así
    que `intentos` está puesto deliberadamente alto para que el tiempo, no
    el conteo, sea el límite. Devuelve ('revision', volcado), ('error',
    texto) o ('timeout', None) — el volcado de la revisión se reutiliza
    para ubicar el botón 'Confirmar' sin volver a leer la pantalla."""
    limite = time.perf_counter() + presupuesto_seg
    for intento in range(intentos):
        if intento:
            if time.perf_counter() >= limite:
                break
            time.sleep(intervalo)
        lista = ctrl.nodos()
        error = _mensaje_error(ctrl, lista)
        if error is not None:
            return "error", error
        if ctrl.existe_desc("Confirmar Operación", lista):
            return "revision", lista
    return "timeout", None


def _enviar_y_revisar(ctrl: ControladorADB, texto_submit: str, lista=None,
                      detectar_limite_pm: bool = False):
    """Envía el formulario y llega hasta la pantalla de revisión
    ('Confirmar Operación'). Si algo falla aquí, el pago NO se intentó:
    es un fallo limpio, seguro de corregir y reenviar.

    `lista`: el volcado final del formulario (el de la verificación del
    monto, sin nada tocado después). Se usa para los errores de validación
    y para ubicar el botón de envío. Devuelve el volcado de la revisión."""
    lista = lista if lista is not None else ctrl.nodos()
    faltantes = ctrl.escanear_errores_validacion(lista)
    if faltantes:
        raise RuntimeError("datos incompletos: " + "; ".join(faltantes))

    ctrl.tap_desc(texto_submit, lista=lista)

    estado, dato = _esperar_revision_o_error(ctrl)
    if estado == "error":
        if detectar_limite_pm and _es_aviso_limite_pm(dato):
            raise LimitePagoMovil(dato)
        raise RuntimeError(f"el banco rechazó la operación: {dato}")
    if estado == "timeout":
        faltantes = ctrl.escanear_errores_validacion()
        motivo = "; ".join(faltantes) if faltantes else "el banco no avanzó a la revisión"
        raise RuntimeError(f"el banco rechazó los datos: {motivo}")
    # estado == "revision": ya se ve "Confirmar Operación", seguimos.
    return dato


def _confirmar_y_pagar(ctrl: ControladorADB, bdv_password: str, lista_revision=None,
                       detectar_limite_pm: bool = False) -> None:
    """Toca 'Confirmar', escribe la clave y toca el botón final del diálogo.

    A PARTIR DE AQUÍ cualquier excepción se trata como estado INCIERTO,
    nunca como fallo limpio: el dinero pudo haber salido. Por eso vive en
    su propia función, separada de _enviar_y_revisar.

    El diálogo 'Atención' de clave rechazada ya no se busca aquí con una
    lectura aparte: se busca en cada vuelta de _esperar_comprobante, que
    además lo detecta aunque aparezca tarde."""
    ctrl.tap_desc_any(["Confirmar", "Continuar"], lista=lista_revision)

    # Visto en vivo el 2026-09-26: el aviso de límite de Pago Móvil ("El
    # monto introducido es inválido") sale AQUÍ, al tocar Confirmar -- no al
    # tocar Pagar --, y la app vuelve al formulario con el error encima. Se
    # busca en la MISMA espera del campo de clave: si aparece, la clave nunca
    # se escribió, así que es un rechazo limpio (LimitePagoMovil), no INCIERTO.
    def _clave_o_limite(lista):
        if detectar_limite_pm:
            error = _mensaje_error(ctrl, lista)
            if error is not None and _es_aviso_limite_pm(error):
                return ("limite", error)
        punto = ctrl.campo_password(lista)
        return ("clave", punto) if punto else None

    encontrado = ctrl.esperar(_clave_o_limite, "campo de contraseña o aviso de límite")
    if encontrado is None:
        raise RuntimeError("no apareció el campo de contraseña tras Confirmar")
    if encontrado[0] == "limite":
        raise LimitePagoMovil(encontrado[1])
    punto = encontrado[1]
    ctrl.tap(*punto, que="password")
    ctrl.limpiar_campo()
    ctrl.text(bdv_password)
    ctrl.tap_desc_any(["Aceptar", "Confirmar", "Ingresar", "Continuar"])


def _esperar_comprobante(ctrl: ControladorADB, intentos: int = 30, presupuesto_seg: float = 30.0,
                         detectar_limite_pm: bool = False):
    """Sondea hasta que aparezca el comprobante, en vez de mirar la pantalla
    una sola vez. Termina apenas aparece — no agrega espera si ya está
    visible. Si el banco tarda (el modal de "Realizando operación" puede
    quedarse varios segundos mientras se procesa de verdad la
    transferencia), sigue probando dentro del presupuesto; si nunca
    aparece, se rinde y el resultado queda INCIERTO (nunca se inventa un
    éxito ni se confirma algo que no se vio).

    30 s reales, no de nombre: cada lectura de pantalla cuesta ~2,1 s
    medido contra el emulador real, así que el presupuesto anterior (12 s)
    alcanzaba para unas 5-6 lecturas reales, no las 8 que sugería
    `intentos` — en la práctica, menos margen del que parecía. `intentos`
    va alto a propósito para que el tiempo, no el conteo, sea el límite.

    En cada vuelta, con el MISMO volcado, también busca el diálogo
    'Atención' de clave rechazada: si aparece, lo cierra y lanza
    ErrorAutenticacion (rechazo limpio, la app confirma que no se movió
    dinero). Antes se miraba una sola vez 0,25 s después de la clave, con
    una lectura aparte; si el diálogo tardaba más, la orden terminaba
    como INCIERTO sin necesidad.

    También busca cualquier OTRO diálogo 'Error' genérico (ej. "Ocurrió un
    error inesperado") que la app pueda mostrar a esta altura -- visto en
    vivo el 2026-09-30: apareció DESPUÉS de confirmar con la clave y el
    bot se quedó los 30 s completos sin reconocerlo, reportando "no se
    detectó comprobante" sin decir que la app en realidad mostró un error
    en pantalla. SIGUE siendo INCIERTO igual que antes -- un error acá no
    prueba que no se movió el dinero, así que no se reclasifica como fallo
    limpio -- pero ahora el texto del diálogo viaja en el resultado para
    que el mensaje final diga lo que la app mostró, no solo "no se vio
    nada". Se cierra (Aceptar) y se sigue sondeando por si el comprobante
    aparece detrás; si no aparece, ese texto queda como el motivo."""
    limite = time.perf_counter() + presupuesto_seg
    ultimo_error_generico = None
    for intento in range(intentos):
        if intento:
            if time.perf_counter() >= limite:
                break
            time.sleep(0.3)
        lista = ctrl.nodos()
        atencion = _dialogo_atencion(ctrl, lista)
        if atencion is not None:
            ctrl.tap_desc("Aceptar", lista=lista)  # cierra el diálogo antes de que decida el llamador
            raise ErrorAutenticacion(atencion)
        error = _mensaje_error(ctrl, lista)
        if error is not None:
            if detectar_limite_pm and _es_aviso_limite_pm(error):
                raise LimitePagoMovilTrasClave(error)
            ultimo_error_generico = error
            print(f"  !! diálogo 'Error' tras confirmar: {error!r} (sigue INCIERTO, cerrando y reintentando)")
            try:
                ctrl.tap_desc("Aceptar", lista=lista)
            except Exception:
                pass
            continue
        hubo, referencia = ctrl.hubo_comprobante(lista)
        if hubo:
            return hubo, referencia, None
    return False, "", ultimo_error_generico


def _intentar_pago(orden: OrdenPago, cuenta_debitar: str, ctrl: ControladorADB,
                   bdv_password: str, por_transferencia: bool = False) -> ResultadoPago:
    """Un intento completo: navegar, llenar, enviar, confirmar, pagar,
    verificar. Devuelve un ResultadoPago normalmente. Si el banco rechaza
    la clave con su diálogo 'Atención' (ver ErrorAutenticacion), relanza
    esa excepción SIN volver a Inicio por su cuenta — deja que
    `ejecutar_pago` decida si reintenta, para no volcar la pantalla de más."""
    # ── Tramo 1: navegar y llenar. Fallo aquí = fallo limpio. ──
    try:
        # El volcado que confirma Inicio ya trae las pestañas: se reutiliza.
        lista_inicio = ctrl.volver_inicio()

        if orden.tipo == "TRANSFERENCIA":
            ctrl.tap_desc("Tab 2 of 6", lista=lista_inicio)  # pestaña "Transferencias"
            if orden.cuenta.startswith(PREFIJO_BDV) and len(orden.cuenta) == 20:
                texto_submit, lista_form = _llenar_terceros(ctrl, orden, cuenta_debitar)
            else:
                texto_submit, lista_form = _llenar_otros_bancos(ctrl, orden, cuenta_debitar)
        elif orden.tipo == "PAGO_MOVIL" and por_transferencia:
            # Límite de Pago Móvil agotado: el Pago Móvil a otro banco se hace
            # por Transferencias -> Cuentas otros bancos, instrumento Teléfono.
            ctrl.tap_desc("Tab 2 of 6", lista=lista_inicio)
            texto_submit, lista_form = _llenar_otros_bancos(
                ctrl, orden, cuenta_debitar, por_telefono=True)
        elif orden.tipo == "PAGO_MOVIL":
            ctrl.tap_desc("Tab 3 of 6", lista=lista_inicio)  # pestaña "Pagos"
            texto_submit, lista_form = _llenar_pago_movil(ctrl, orden)
        else:
            raise RuntimeError(f"tipo de transacción no soportado: {orden.tipo}")

        detectar = (orden.tipo == "PAGO_MOVIL" and not por_transferencia
                    and not _es_pm_a_bdv(orden))
        lista_revision = _enviar_y_revisar(ctrl, texto_submit, lista_form,
                                           detectar_limite_pm=detectar)

    except LimitePagoMovil:
        # Cierra el aviso (Aceptar) y vuelve a Inicio; decide ejecutar_pago.
        _volver_inicio_seguro(ctrl)
        raise
    except Exception as exc:
        ruta = _capturar(ctrl, orden.id_orden)
        _volver_inicio_seguro(ctrl)
        return ResultadoPago(
            exito=False,
            incierto=False,
            mensaje=f"Falló antes de confirmar el pago: {exc}",
            referencia=None,
            ruta_captura=ruta,
        )

    # ── Tramo 2: confirmar + clave. Fallo aquí = INCIERTO, nunca fallo limpio —
    #    EXCEPTO ErrorAutenticacion, que la app confirma que no tocó el dinero. ──
    try:
        _confirmar_y_pagar(ctrl, bdv_password, lista_revision, detectar_limite_pm=detectar)
    except LimitePagoMovil:
        # La clave no llegó a escribirse: cierra el aviso y decide ejecutar_pago.
        _volver_inicio_seguro(ctrl)
        raise
    except Exception as exc:
        ruta = _capturar(ctrl, orden.id_orden)
        _volver_inicio_seguro(ctrl)
        return ResultadoPago(
            exito=False,
            incierto=True,
            mensaje=f"Error al confirmar o pagar ({exc}). Estado INCIERTO: "
                    "revisa el banco antes de reintentar.",
            referencia=None,
            ruta_captura=ruta,
        )

    # ── Tramo 3: verificar el comprobante. Sondea, no mira una sola vez. ──
    #    ErrorAutenticacion (diálogo 'Atención' de clave rechazada, visto
    #    dentro del sondeo) se relanza tal cual: decide ejecutar_pago. ──
    try:
        hubo, referencia, error_visto = _esperar_comprobante(ctrl, detectar_limite_pm=detectar)
        ruta = _capturar(ctrl, orden.id_orden)

        if hubo:
            # Éxito: NO se vuelve a Inicio aquí, para no demorar la captura
            # en Telegram (~4,5 s). El orquestador llama a
            # volver_inicio_tras_pago() justo después de mandarla.
            return ResultadoPago(
                exito=True, incierto=False, mensaje="Comprobante detectado",
                referencia=referencia, ruta_captura=ruta,
            )
        _volver_inicio_seguro(ctrl)
        # Si la app mostró un diálogo 'Error' genérico tras confirmar (ver
        # _esperar_comprobante), se incluye su texto literal: sigue siendo
        # INCIERTO igual que antes (un error acá no prueba que no se movió
        # el dinero), pero ahora el mensaje dice lo que la app mostró en
        # vez de solo "no se detectó nada".
        motivo = (f" La app mostró: {error_visto!r}." if error_visto else "")
        return ResultadoPago(
            exito=False, incierto=True,
            mensaje="No se detectó comprobante tras confirmar." + motivo +
                    " Estado INCIERTO: revisa los movimientos del banco antes de reintentar.",
            referencia=None, ruta_captura=ruta,
        )
    except ErrorAutenticacion:
        raise  # el diálogo ya se cerró dentro de _esperar_comprobante; decide el llamador
    except LimitePagoMovilTrasClave:
        # Cierra el aviso (Aceptar) y vuelve a Inicio; ejecutar_pago reintenta
        # ESTA orden por transferencia (decisión explícita del usuario,
        # 2026-09-27 — ver LimitePagoMovilTrasClave).
        _volver_inicio_seguro(ctrl)
        raise
    except Exception as exc:
        # Ya se envió la contraseña y se tocó el botón final: el pago pudo
        # haberse ejecutado. No hay forma segura de saberlo desde aquí.
        ruta = _capturar(ctrl, orden.id_orden)
        _volver_inicio_seguro(ctrl)
        return ResultadoPago(
            exito=False, incierto=True,
            mensaje=f"Error verificando el resultado ({exc}). Estado INCIERTO: "
                    "revisa el banco antes de reintentar.",
            referencia=None, ruta_captura=ruta,
        )


def _pagar_con_reintento_clave(orden: OrdenPago, cuenta_debitar: str, ctrl: ControladorADB,
                               bdv_password: str, por_transferencia: bool) -> ResultadoPago:
    """Un intento y, SOLO si el banco rechaza la clave con su diálogo conocido
    (ErrorAutenticacion), un segundo intento automático — es seguro porque la
    propia app confirma que el primero no tocó el dinero."""
    try:
        return _intentar_pago(orden, cuenta_debitar, ctrl, bdv_password, por_transferencia)
    except ErrorAutenticacion as exc:
        print(f"  !! el banco rechazó la clave ({exc}); reintentando la operación completa una vez")
        try:
            return _intentar_pago(orden, cuenta_debitar, ctrl, bdv_password, por_transferencia)
        except ErrorAutenticacion as exc2:
            ruta = _capturar(ctrl, orden.id_orden)
            _volver_inicio_seguro(ctrl)
            return ResultadoPago(
                exito=False,
                incierto=False,
                mensaje=f"El banco rechazó la clave dos veces seguidas ({exc2}). "
                        "No se movió dinero — la app confirma que fue un rechazo de "
                        "autenticación, no de la operación — pero revisa que la "
                        "contraseña guardada siga siendo la correcta.",
                referencia=None,
                ruta_captura=ruta,
            )


def volver_inicio_tras_pago(serial: str | None = None):
    """Deja la app en Inicio después de un pago exitoso, cuando la captura ya
    salió a Telegram — así la app queda esperando la siguiente orden en vez
    de quedarse en el comprobante. Nunca lanza: si falla, la siguiente orden
    igual arranca con volver_inicio()."""
    try:
        ctrl = ControladorADB(serial or detectar_serial())
    except Exception as exc:
        print(f"  !! no se pudo volver a Inicio: {exc}")
        return
    _volver_inicio_seguro(ctrl, ya_confirmado=True)


def ejecutar_pago(orden: OrdenPago, serial: str | None = None, bdv_password: str = "") -> ResultadoPago:
    """Punto de entrada. Rutas:
      - TRANSFERENCIA: siempre igual.
      - PAGO_MOVIL a Banco de Venezuela (0102): siempre por Pago Móvil.
      - PAGO_MOVIL a otro banco: por Pago Móvil; pero si el banco responde
        "El monto introducido es inválido" (límite diario agotado), se activa
        el modo transferencia hasta medianoche, esta misma orden se reintenta
        por Transferencias -> otros bancos con instrumento Teléfono, y las
        siguientes entran directo por esa ruta. Si esa ruta también falla, se
        reporta tal cual (no se vuelve a Pago Móvil).
    Aparte de eso y del rechazo de clave, ninguna falla se reintenta sola."""
    cuenta_debitar = requerido("CUENTA_DEBITAR_DESC")
    ctrl = ControladorADB(serial or detectar_serial())

    por_transferencia = (orden.tipo == "PAGO_MOVIL" and not _es_pm_a_bdv(orden)
                         and modo_transferencia_activo())
    try:
        resultado = _pagar_con_reintento_clave(orden, cuenta_debitar, ctrl, bdv_password,
                                               por_transferencia)
    except (LimitePagoMovil, LimitePagoMovilTrasClave) as exc:
        _activar_modo_transferencia()
        print(f"  !! límite de Pago Móvil agotado ({exc}); modo transferencia activo "
              "hasta medianoche — reintentando esta orden por transferencia")
        por_transferencia = True
        resultado = _pagar_con_reintento_clave(orden, cuenta_debitar, ctrl, bdv_password, True)
    resultado.via_transferencia = por_transferencia
    return resultado
