"""Parseo y validación de las órdenes de pago que llegan por Telegram.

Formato real del bot, dos variantes (confirmado contra el proyecto
principal, mesa/sources/telegram_parser.py, contra órdenes reales):

    Transferencia (con cuenta; puede ser BDV u otro banco):

        0022929364591556145153          <- número de orden
        0000000000000000933.735         <- tasa de cambio
        ------------------
        🏦 BancoDeVenezuela              <- banco destino
        🆔 23798062                      <- documento/identificación del titular
        🔢 CTA: 01020754150000056672    <- número de cuenta
        👤 Barbara Requena               <- nombre del titular
        350,00                          <- monto a transferir

    Pago móvil (con teléfono en vez de cuenta):

        0022928300824416772096
        0000000000000000932.725
        ------------------
        🏦 Banco de Venezuela
        🆔 29610348
        📱 04120549029
        👤 MARIN COLLANTES YORGELY KAROLAY
        9.308,59

El 🆔 es un identificador corto del bot, no una cédula: se guarda como
referencia, no se valida contra un formato de documento oficial.

El renglón entre 🆔 y 👤 trae la cuenta o el teléfono, pero el bot de origen
NO es consistente con qué emoji o etiqueta usa ahí (visto en vivo: "🔢" con
un teléfono adentro y sin "CTA:", en vez de "📱"). Por eso el tipo de orden
NUNCA se decide por el emoji ni por la palabra "CTA:" — se decide por la
FORMA del número que aparece en ese renglón: un teléfono venezolano
(04 + 9 dígitos = 11 en total) es PAGO_MOVIL; un número de 15 a 25 dígitos
es TRANSFERENCIA. Si el renglón no trae ninguno de los dos, la orden se
ignora igual que si no matcheara nada.
"""
import re
from dataclasses import dataclass

TIPOS_SOPORTADOS = {"TRANSFERENCIA", "PAGO_MOVIL"}

# Línea separadora: guion normal o cualquier raya. Los teclados del celular convierten "--" en
# raya larga "—" (visto el 2026-10-25: "——————" y la orden no se reconocía).
_GUIONES = "[-‐‑‒–—―−─━_]+"

_PATRON_ORDEN = re.compile(
    r"""
    ^\s*(?P<numero_orden>\d{15,})\s*\n          # línea 1: número de orden
    \s*(?P<tasa>[\d.]+)\s*\n                     # línea 2: tasa
    \s*""" + _GUIONES + r"""\s*\n                # línea separadora
    .*?🏦\s*(?P<banco>\S.*?)\s*\n                # línea con el banco
    .*?🆔\s*(?P<documento>\S+(?:[^\S\n]+\d[\w.\-]*)?)\s*\n   # identificación (puede ser "V 123")
    .*?(?P<medio>\S.*?)\s*\n                     # línea con la cuenta o el teléfono
    .*?👤\s*(?P<nombre>\S.*?)\s*\n               # línea con el nombre
    \s*(?P<monto>[\d.,]+)\s*$                    # última línea: monto
    """,
    re.VERBOSE | re.DOTALL,
)

# Teléfono venezolano: 04 + 9 dígitos = 11 en total. Cuenta bancaria: 15-25
# dígitos (mismo rango que exige validar_orden). Los lookaround de \d evitan
# que uno matchee un fragmento de un número más largo (ej. un "04..." que en
# realidad es parte de una cuenta de 20 dígitos).
_TELEFONO_VE = re.compile(r"(?<!\d)04\d{9}(?!\d)")
_CUENTA = re.compile(r"(?<!\d)\d{15,25}(?!\d)")
# Lo que va delante del dato en cada renglón: emoji, espacios, viñetas. \w incluye letras con
# tilde y dígitos, que nunca se quitan.
_PREFIJO_SIMBOLOS = re.compile(r"^[^\w]+")


def _sin_simbolos(renglon: str) -> str:
    return _PREFIJO_SIMBOLOS.sub("", renglon).strip()


def _por_posicion(texto: str) -> dict | None:
    """Respaldo cuando el mensaje NO trae los emojis (visto el 2026-10-25: al copiar y pegar una
    orden los emojis se pierden y queda solo un espacio). Exige EXACTAMENTE la misma forma que la
    plantilla: orden, tasa, separador y los 5 renglones (banco, documento, cuenta/teléfono,
    nombre, monto) en ese orden, sin renglones de más ni de menos."""
    renglones = [r.strip() for r in texto.splitlines() if r.strip()]
    if len(renglones) != 8:
        return None
    orden, tasa, separador, banco, documento, medio, nombre, monto = renglones
    if not (re.fullmatch(r"\d{15,}", orden) and re.fullmatch(r"[\d.]+", tasa)
            and re.fullmatch(_GUIONES, separador) and re.fullmatch(r"[\d.,]+", monto)):
        return None
    banco, documento, nombre = _sin_simbolos(banco), _sin_simbolos(documento), _sin_simbolos(nombre)
    if not (banco and documento and nombre):
        return None
    return dict(numero_orden=orden, tasa=tasa, banco=banco, documento=documento,
                medio=medio, nombre=nombre, monto=monto)


# ── Documento de identidad: V cédula venezolana · E extranjero · J jurídico (RIF) · P pasaporte ·
#    G gubernamental · C comunal ────────────────────────────────────────────────────────────────
# La orden puede traer la letra pegada al número ("J-301234567", "E 12345678", "V12345678",
# "J-30123456-7") o solo el número (cédula venezolana). La letra elige el desplegable "Tipo" de la
# app y SOLO el número se escribe en el campo "Documento:". Antes cualquier documento con letra
# se rechazaba como "formato inválido" y los pagos a extranjeros/empresas no salían.
LETRAS_DOCUMENTO = "VEJPGC"
_DOCUMENTO = re.compile(r"^\s*(?:(?P<letra>[VEJPGCvejpgc])[\s.:-]*)?(?P<num>[0-9A-Za-z][0-9A-Za-z.\-]*)\s*$")


def partir_documento(documento: str) -> tuple[str, str]:
    """'J-30123456-7' -> ('J', '301234567'); 'E 12345678' -> ('E', '12345678'); '23798062' ->
    ('V', '23798062'); 'P-A1234567' o 'A1234567' -> ('P', 'A1234567'). Si no se reconoce, ('V', texto)
    sin tocar: validar_orden lo rechaza con su mensaje."""
    desconocida = re.match(r"^\s*([A-Za-z])\s*[-.:\s]\s*\d", documento or "")
    if desconocida and desconocida.group(1).upper() not in LETRAS_DOCUMENTO:
        return "?", re.sub(r"\D", "", documento)           # p. ej. "X-12345678": validar_orden lo rechaza
    m = _DOCUMENTO.match(documento or "")
    if not m:
        return "V", (documento or "").strip()
    letra, num = (m.group("letra") or "").upper(), m.group("num")
    if re.search(r"[A-Za-z]", num):                 # con letras de por medio solo puede ser pasaporte
        return "P", re.sub(r"[^0-9A-Za-z]", "", num).upper()
    return (letra or "V"), re.sub(r"\D", "", num)


@dataclass
class OrdenPago:
    id_orden: str
    tipo: str
    numero_orden: str
    tasa: str
    banco: str
    documento: str
    nombre: str
    monto: str
    remitente_id: int
    mensaje_id: int
    cuenta: str = ""      # solo TRANSFERENCIA
    telefono: str = ""    # solo PAGO_MOVIL
    tipo_documento: str = "V"   # V/E/J/P/G/C (ver partir_documento); documento trae solo el número


def normalizar_monto(m: str) -> float:
    """Acepta '350,00', '350.00' o '2.000,00' (punto de miles + coma
    decimal, como llegan los montos grandes en los mensajes reales).
    Pública porque ejecutor.py también la necesita: el monto que se ESCRIBE
    en el campo de la app tiene que salir de este mismo cálculo, nunca del
    texto crudo del mensaje (ver _monto_para_campo en ejecutor.py)."""
    return float(m.replace(".", "").replace(",", ".")) if "," in m else float(m)


def _es_todo_ceros(valor: str) -> bool:
    """True si `valor` son puros dígitos y TODOS son '0' (ej.
    '00000000000000000000'). No confundir con una cuenta real que empiece
    por varios ceros — eso es normal en Venezuela; esto exige que el número
    ENTERO sea cero, no solo el principio."""
    return bool(valor) and valor.isdigit() and valor.strip("0") == ""


def _es_mensaje_basura(orden: "OrdenPago") -> bool:
    """True si CUALQUIERA de los campos de pago (destino, documento o monto)
    viene en cero/placeholder, aunque el número de orden sea válido y los
    demás campos sean reales.

    Un mensaje así no representa un pago real: se trata como si no
    existiera — igual que un texto que no matchea ninguna plantilla —, NO
    como una orden rechazada. La diferencia importa: una orden rechazada
    queda marcada como procesada (para no repetirla sola) y genera un aviso
    de error; esto no debe hacer ninguna de las dos cosas, porque ni
    siquiera es una orden real. Así, cuando el mismo número de orden vuelva
    corregido, se ejecuta con normalidad, sin que la versión basura cuente
    como duplicado."""
    try:
        monto_en_cero = normalizar_monto(orden.monto) == 0
    except ValueError:
        monto_en_cero = False  # formato raro: que lo maneje validar_orden, no esto

    destino = orden.cuenta if orden.tipo == "TRANSFERENCIA" else orden.telefono
    return _es_todo_ceros(destino) or _es_todo_ceros(orden.documento) or monto_en_cero


def parsear_orden(texto: str, remitente_id: int, mensaje_id: int) -> OrdenPago | None:
    """Devuelve None si el mensaje no matchea el formato exacto esperado, si
    el renglón de cuenta/teléfono no trae ni una cosa ni la otra, o si
    matchea pero es basura (campos en cero) — en todos los casos se ignora
    en silencio, para que cualquier mensaje sin datos reales no genere ruido
    ni cuente como una orden ya vista."""
    texto = texto.strip()

    m = _PATRON_ORDEN.match(texto)
    d = m.groupdict() if m else _por_posicion(texto)
    if d is None:
        return None

    # Documento y teléfono INVERTIDOS (visto el 2026-10-25: "🆔 04243468273" y "📱 31899383").
    # Por la forma no hay duda: un teléfono es 04 + 9 dígitos (11) y un documento tiene de 6 a 10,
    # así que ninguno puede pasar por el otro. Solo se corrige si el renglón del medio no trae ya
    # un teléfono ni una cuenta.
    if (_TELEFONO_VE.fullmatch(d["documento"]) and not _TELEFONO_VE.search(d["medio"])
            and not _CUENTA.search(d["medio"])):
        doc_en_medio = re.search(r"(?<!\d)\d{6,10}(?!\d)", d["medio"])
        if doc_en_medio:
            d = dict(d, documento=doc_en_medio.group(), medio=d["documento"])
    letra_doc, num_doc = partir_documento(d["documento"])
    base = dict(
        id_orden=d["numero_orden"],
        numero_orden=d["numero_orden"],
        tasa=d["tasa"],
        banco=d["banco"],
        documento=num_doc,
        tipo_documento=letra_doc,
        nombre=d["nombre"],
        monto=d["monto"],
        remitente_id=remitente_id,
        mensaje_id=mensaje_id,
    )

    # El tipo se decide por la FORMA del número en "medio", nunca por el
    # emoji ni por una etiqueta como "CTA:" — ver el docstring del módulo.
    telefono_m = _TELEFONO_VE.search(d["medio"])
    if telefono_m:
        orden = OrdenPago(tipo="PAGO_MOVIL", telefono=telefono_m.group(), **base)
        return None if _es_mensaje_basura(orden) else orden

    cuenta_m = _CUENTA.search(d["medio"])
    if cuenta_m:
        orden = OrdenPago(tipo="TRANSFERENCIA", cuenta=cuenta_m.group(), **base)
        return None if _es_mensaje_basura(orden) else orden

    return None  # el renglón no trae ni un teléfono ni una cuenta reconocibles


class OrdenInvalida(Exception):
    pass


def validar_orden(orden: OrdenPago, usuarios_permitidos: set[int], monto_maximo_auto: float):
    """Lanza OrdenInvalida con un mensaje claro si algo no cuadra.
    Se llama ANTES de encolar la orden para ejecutarla."""
    if usuarios_permitidos and orden.remitente_id not in usuarios_permitidos:
        raise OrdenInvalida(f"remitente no autorizado ({orden.remitente_id})")

    if orden.tipo not in TIPOS_SOPORTADOS:
        raise OrdenInvalida(f"tipo de transacción no soportado: {orden.tipo}")

    if orden.tipo == "TRANSFERENCIA":
        if not re.fullmatch(r"\d{15,25}", orden.cuenta):
            raise OrdenInvalida(f"número de cuenta con formato inválido: {orden.cuenta}")
    else:  # PAGO_MOVIL
        if not (orden.telefono.isdigit() and orden.telefono.startswith("04")
                and len(orden.telefono) == 11):
            raise OrdenInvalida(f"teléfono con formato inválido: {orden.telefono}")

    letra_doc, num_doc = orden.tipo_documento, orden.documento
    if letra_doc not in tuple(LETRAS_DOCUMENTO):
        raise OrdenInvalida(f"tipo de documento no reconocido: {letra_doc} (usa V, E, J, P, G o C)")
    formato = r"[0-9A-Z]{5,12}" if letra_doc == "P" else r"\d{6,10}"
    if not re.fullmatch(formato, num_doc):
        raise OrdenInvalida(f"documento con formato inválido: {letra_doc}-{num_doc}")

    try:
        monto = normalizar_monto(orden.monto)
    except ValueError:
        raise OrdenInvalida(f"monto con formato inválido: {orden.monto}")

    if monto <= 0:
        raise OrdenInvalida("el monto debe ser mayor a 0")

    # monto_maximo_auto = 0 -> sin tope (MONTO_MAXIMO_AUTO=0 en .env).
    if monto_maximo_auto > 0 and monto > monto_maximo_auto:
        raise OrdenInvalida(
            f"monto {monto} supera el tope automático ({monto_maximo_auto}); "
            "hazlo manualmente en el emulador"
        )
