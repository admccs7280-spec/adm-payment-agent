"""Catálogo de bancos de Venezuela: nombre + código, y normalización de
texto libre (banco tal como llega en el mensaje, ej. 'BancoDeVenezuela' o
'Banesco') a ese par.

Copia reducida del catálogo del proyecto principal (mesa/core/banks.py),
para que esta carpeta siga siendo un proyecto independiente. Si un código
cambia o falta un banco, ajusta aquí Y allá — no hay una sola fuente hoy.
"""
import re
import unicodedata

# clave (código o alias en minúsculas, sin espacios) -> (nombre canónico, código)
BANCOS = {
    "0102": ("Banco de Venezuela", "0102"),
    "bancodevenezuela": ("Banco de Venezuela", "0102"),
    "banco de venezuela": ("Banco de Venezuela", "0102"),
    "bdv": ("Banco de Venezuela", "0102"),
    # "Venezuela" sola: visto en mensajes reales de Pago Móvil (2026-09-22),
    # el bot de origen a veces manda solo el país/nombre corto, no
    # "Banco de Venezuela" completo. No choca con ningún otro banco del
    # catálogo (ninguno más se llama o contiene "venezuela").
    "venezuela": ("Banco de Venezuela", "0102"),
    "0134": ("Banesco", "0134"), "banesco": ("Banesco", "0134"),
    "0105": ("Mercantil", "0105"), "mercantil": ("Mercantil", "0105"),
    "0108": ("BBVA Provincial", "0108"), "provincial": ("BBVA Provincial", "0108"),
    "bbva": ("BBVA Provincial", "0108"),
    "0191": ("BNC", "0191"), "bnc": ("BNC", "0191"),
    "banconacionaldecredito": ("BNC", "0191"),
    # "banco nacional DEL credito": visto en una orden real el 2026-09-27;
    # sin este alias el Pago Móvil fallaba como "banco no reconocido".
    "banconacionaldelcredito": ("BNC", "0191"),
    "nacionaldecredito": ("BNC", "0191"),
    "nacionaldelcredito": ("BNC", "0191"),
    # 0175 confirmado por captura real de la app del BDV: Banco Digital de los
    # Trabajadores, no Bicentenario (ver bitacora/DECISIONES.md del proyecto
    # principal). El código real de Bicentenario no está confirmado.
    "0175": ("Banco Digital de los Trabajadores", "0175"),
    "banco digital de los trabajadores": ("Banco Digital de los Trabajadores", "0175"),
    "0163": ("Banco del Tesoro", "0163"), "tesoro": ("Banco del Tesoro", "0163"),
    "0114": ("Bancaribe", "0114"), "bancaribe": ("Bancaribe", "0114"),
    "0174": ("Banplus", "0174"), "banplus": ("Banplus", "0174"),
    "0104": ("Venezolano de Crédito", "0104"),
    "venezolanodecredito": ("Venezolano de Crédito", "0104"),
    "banco venezolano de crédito": ("Venezolano de Crédito", "0104"),
    "banco venezolano de credito": ("Venezolano de Crédito", "0104"),
    # "venezolana de crédito": llegó así en una orden real el 2026-10-26 y fallaba.
    "venezolanadecredito": ("Venezolano de Crédito", "0104"),
    "0115": ("Banco Exterior", "0115"), "exterior": ("Banco Exterior", "0115"),
    "0172": ("Bancamiga", "0172"), "bancamiga": ("Bancamiga", "0172"),
    # Variantes reales que llegaron el 2026-10-26 y fallaban como "banco no reconocido":
    # "Bancaamiga", "Banca amiga", "Bancamig1". "bancamig" cubre las que se cortan o traen
    # un carácter de más al final.
    "bancaamiga": ("Bancamiga", "0172"), "bancamig": ("Bancamiga", "0172"),
    "0171": ("Banco Activo", "0171"), "activo": ("Banco Activo", "0171"),
    # Confirmado contra la propia lista de bancos de la app BDV el
    # 2026-09-23. Faltaba por completo -- una orden real con "Fondo común"
    # falló como "banco no reconocido" (fallo limpio, sin dinero movido).
    "0151": ("Banco Fondo Común", "0151"),
    "fondocomun": ("Banco Fondo Común", "0151"),
    "banco fondo común": ("Banco Fondo Común", "0151"),
    "banco fondo comun": ("Banco Fondo Común", "0151"),
    "0156": ("100% Banco", "0156"), "0157": ("Banco del Sur", "0157"),
    # 2026-10-26: "100% BANCO", "plaza" y "SOFITASA" llegaron en órdenes reales y fallaban.
    # El selector de la app se busca por el CÓDIGO: si un código no estuviera en la lista
    # de la app, la orden falla limpio ("no aparece en el selector"), nunca elige otro.
    "100banco": ("100% Banco", "0156"), "delsur": ("Banco del Sur", "0157"),
    "0138": ("Banco Plaza", "0138"), "plaza": ("Banco Plaza", "0138"),
    "0137": ("Sofitasa", "0137"), "sofitasa": ("Sofitasa", "0137"),
    "0128": ("Banco Caroní", "0128"), "caroni": ("Banco Caroní", "0128"),
    "0168": ("Bancrecer", "0168"), "bancrecer": ("Bancrecer", "0168"),
    "0169": ("Mi Banco", "0169"), "mibanco": ("Mi Banco", "0169"),
    "0166": ("Banco Agrícola de Venezuela", "0166"), "agricola": ("Banco Agrícola de Venezuela", "0166"),
    "0177": ("Banfanb", "0177"), "banfanb": ("Banfanb", "0177"),
}


# código -> nombre canónico, para resolver un código suelto en O(1).
_CODIGOS = {codigo: nombre for nombre, codigo in BANCOS.values()}


def _norm(s: str) -> str:
    """Minúsculas, sin espacios/puntuación NI acentos: 'Crédito' -> 'credito'.
    Bug real, visto en vivo el 2026-09-23: la regex [^a-z0-9] por sí sola
    BORRA una letra con tilde en vez de quitarle solo el acento ('crédito'
    quedaba 'crdito', sin la 'e'), así que 'Banco Venezolano de Crédito'
    nunca se reconocía — ni siquiera contra su propia entrada del catálogo.
    Se descompone primero (NFKD) y se quitan los acentos como marcas
    combinantes, ANTES de filtrar lo que no es letra/dígito."""
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", s)


def normalizar_banco(texto: str):
    """'BancoDeVenezuela' / 'Banco de Venezuela' / '0102' / '102' ->
    ('0102', 'Banco de Venezuela'). Sin coincidencia: (None, None)."""
    if not texto:
        return None, None
    clave = _norm(texto)
    if clave in BANCOS:
        nombre, codigo = BANCOS[clave]
        return codigo, nombre

    # Código de banco suelto en el campo, con o sin el cero inicial (el
    # código oficial son 4 dígitos, pero a veces llega como "102" en vez de
    # "0102"). Exige que TODO el campo sean dígitos, para no confundir un
    # código con un fragmento de un texto más largo que también tenga números.
    if clave.isdigit() and 3 <= len(clave) <= 4:
        codigo = clave.zfill(4)
        if codigo in _CODIGOS:
            return codigo, _CODIGOS[codigo]

    for k, (nombre, codigo) in BANCOS.items():
        if k.isdigit() and k in texto:
            return codigo, nombre
    for k, (nombre, codigo) in BANCOS.items():
        # _norm(k), no k crudo: algunas claves del catálogo tienen espacios
        # o acentos propios (ej. "banco venezolano de crédito") y nunca
        # matchearían contra `clave` (que no tiene ninguno de los dos) si se
        # comparan tal cual.
        if not k.isdigit() and _norm(k) in clave:
            return codigo, nombre
    return None, None
