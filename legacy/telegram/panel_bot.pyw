"""Panel del bot de pagos BDV con iPhone 12, protegido con clave.

Se abre con el acceso directo "Panel Bot iPhone 12" (o ABRIR-PANEL.bat). Hecho a partir de
ACTUALIZAR-PANEL-OTRAS-PC.md (panel del bot Android del 2026-10-25), adaptado al iPhone:

- Iniciar: antes comprueba configuración, sesión de Telegram libre, que ningún otro bot de esta
  PC use la misma sesión o el mismo grupo y que el iPhone se vea. Arranca orquestador.py sin
  consola; todo lo que escribe va a logs\\bot.log.
- Detener: apagado ORDENADO (parar_bot.flag, igual que bot_iphone.bat): el bot deja de aceptar
  órdenes, termina la que está pagando y las de la cola, vuelve a Inicio y se cierra. Nunca se
  corta un pago. Mientras arranca (esperando al iPhone, sin Telegram) se puede cancelar.
- Forzar apagado: solo tras 3 minutos apagándose, y solo si el proceso es de verdad el bot.
- Tarjetas (Bot, iPhone, Telegram, Hoy), Registro en colores, Actividad (tabla de órdenes),
  Configuración (edita el .env) y Herramientas.

El iPhone admite UNA conexión de captura a la vez: con el bot encendido el panel solo pregunta a
usbmuxd si el iPhone está conectado (sin abrir conexión con él) y nunca mientras paga. Todo lo
que abre conexión con el iPhone o usa la sesión de Telegram exige el bot apagado.

La clave del panel se guarda como hash (PBKDF2 con sal) en panel_clave.json. Protege los
BOTONES; los archivos del bot (.env, .session) siguen siendo legibles para quien use esta
cuenta de Windows.
"""
import ast
import asyncio
import ctypes
import hashlib
import hmac
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import messagebox, ttk

RAIZ = Path(__file__).resolve().parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

# El panel es del bot del iPhone: config.py prefiere los valores *_IPHONE.
os.environ["DISPOSITIVO"] = "iphone"
_ENTORNO_ANTES = set(os.environ)
import control_bot  # noqa: E402  (importa config.py, que carga el .env en os.environ)
import instancia_unica
import plataforma
import registro_iphones  # noqa: E402
import registro_bot  # noqa: E402
from ordenes import normalizar_monto, parsear_orden  # noqa: E402

# Claves que config.py cargó del .env al abrir el panel: se quitan del entorno del bot
# (entorno_hijo) para que un cambio guardado en Configuración se aplique sin reiniciar el panel.
CLAVES_CARGADAS_DEL_ENV = set(os.environ) - _ENTORNO_ANTES

PY_BOT = plataforma.python_del_entorno(RAIZ / "iphone" / ".venv")
ORQUESTADOR = RAIZ / "orquestador.py"
HERRAMIENTAS = RAIZ / "herramientas_iphone.py"
ARCHIVO_ENV = RAIZ / ".env"
ARCHIVO_CLAVE = RAIZ / "panel_clave.json"
ARCHIVO_GRUPOS = RAIZ / "panel_grupos.json"
ARCHIVO_LOG = registro_bot.ARCHIVO_LOG
CARPETA_CAPTURAS = RAIZ / "capturas"
CARPETA_RESPALDOS = RAIZ / "respaldos"
GUIA = RAIZ / "GUIA-INSTALACION.md"
SIN_VENTANA = plataforma.SIN_VENTANA
NOMBRE_INSTANCIA_PANEL = "panel-bot-iphone|" + str(RAIZ).lower()

ITERACIONES = 600_000
ESPERA_FORZAR_SEG = 180
SESION_POR_DEFECTO = "telegram_local_pc2"      # el mismo valor por defecto que config.py
# Gancho para la prueba de arranque sin riesgo (ACTUALIZAR-PANEL-OTRAS-PC.md, paso 5):
# ENTORNO_EXTRA = {"CHAT_ID_ORDENES": "-1", "CHAT_ID_ORDENES_IPHONE": "-1"}  -> escucha un chat que no existe.
ENTORNO_EXTRA: dict[str, str] = {}

# Con DISPOSITIVO=iphone config.py usa X_IPHONE si tiene valor y, si no, X.
CLAVES_CON_IPHONE = ("CHAT_ID_ORDENES", "CHAT_ID_CAPTURAS", "MONTO_MAXIMO_AUTO", "USER_IDS_PERMITIDOS")


# ── Clave del panel ────────────────────────────────────────────────

def _hash(clave: str, sal: bytes, iteraciones: int) -> str:
    return hashlib.pbkdf2_hmac("sha256", clave.encode("utf-8"), sal, iteraciones).hex()


def guardar_clave(clave: str, archivo: Path = ARCHIVO_CLAVE):
    sal = os.urandom(16)
    archivo.write_text(json.dumps(
        {"sal": sal.hex(), "iteraciones": ITERACIONES, "hash": _hash(clave, sal, ITERACIONES)}))


def clave_correcta(clave: str, archivo: Path = ARCHIVO_CLAVE) -> bool:
    datos = json.loads(archivo.read_text())
    calculado = _hash(clave, bytes.fromhex(datos["sal"]), datos["iteraciones"])
    return hmac.compare_digest(calculado, datos["hash"])


def _clave_valida(archivo: Path) -> bool:
    try:
        datos = json.loads(archivo.read_text())
        bytes.fromhex(datos["sal"])
        return int(datos["iteraciones"]) > 0 and bool(datos["hash"])
    except (OSError, ValueError, KeyError, TypeError):
        return False


def copiar_clave_anterior(destino: Path = ARCHIVO_CLAVE, raiz: Path = RAIZ) -> Path | None:
    """Usa la MISMA clave que el panel anterior de esta PC: si aquí no hay panel_clave.json,
    copia el de la carpeta de arriba o, si no, el de un bot vecino (ej. el de BlueStacks)."""
    if destino.exists():
        return None
    candidatos = [raiz.parent / "panel_clave.json"]
    candidatos += sorted(p for p in raiz.parent.glob("*/panel_clave.json") if p.parent != raiz)
    for origen in candidatos:
        if _clave_valida(origen):
            shutil.copy2(origen, destino)
            return origen
    return None


# ── .env ───────────────────────────────────────────────────────────

_RE_CLAVE_ENV = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def leer_env(ruta: Path = ARCHIVO_ENV) -> dict[str, str]:
    """Igual que config.py: la PRIMERA aparición de una clave es la que vale y lo que va
    después de un # se descarta."""
    valores: dict[str, str] = {}
    try:
        texto = ruta.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return valores
    for linea in texto.splitlines():
        if linea.strip().startswith("#"):
            continue
        m = _RE_CLAVE_ENV.match(linea)
        if m and m.group(1) not in valores:
            valores[m.group(1)] = m.group(2).split("#")[0].strip()
    return valores


def escribir_env(cambios: dict[str, str], ruta: Path = ARCHIVO_ENV) -> bool:
    """Cambia SOLO las claves indicadas (en su primera aparición), conserva comentarios y orden,
    deja copia en .env.bak y reemplaza el archivo de forma atómica. False si no había cambios."""
    for clave, valor in cambios.items():
        if "#" in valor:
            raise ValueError(f"{clave}: no puede llevar # (el .env lo toma como comentario)")
        if "\n" in valor or "\r" in valor:
            raise ValueError(f"{clave}: no puede llevar saltos de línea")
    try:
        lineas = ruta.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except FileNotFoundError:
        lineas = []
    actuales = leer_env(ruta)
    pendientes = {c: v.strip() for c, v in cambios.items() if actuales.get(c) != v.strip()}
    if not pendientes:
        return False
    hechas = set()
    for i, linea in enumerate(lineas):
        if linea.strip().startswith("#"):
            continue
        m = _RE_CLAVE_ENV.match(linea)
        if m and m.group(1) in pendientes and m.group(1) not in hechas:
            lineas[i] = f"{m.group(1)}={pendientes[m.group(1)]}"
            hechas.add(m.group(1))
    lineas += [f"{c}={v}" for c, v in pendientes.items() if c not in hechas]
    if ruta.exists():
        shutil.copy2(ruta, ruta.with_name(".env.bak"))
    tmp = ruta.with_name(".env.tmp")
    tmp.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    os.replace(tmp, ruta)
    return True


def clave_destino(valores: dict[str, str], clave: str) -> str:
    """Dónde vive de verdad un valor para el bot del iPhone: X_IPHONE si tiene valor, si no X."""
    if clave in CLAVES_CON_IPHONE and valores.get(clave + "_IPHONE", "").strip():
        return clave + "_IPHONE"
    return clave


def valor_efectivo(valores: dict[str, str], clave: str) -> str:
    return valores.get(clave_destino(valores, clave), "").strip()


def ruta_sesion(valores: dict[str, str], raiz: Path = RAIZ) -> str:
    """El mismo texto que arma config.py: de él sale el puerto de instancia_unica."""
    return str(raiz / (valores.get("SESSION_NAME", "").strip() or SESION_POR_DEFECTO))


def _entero(texto: str) -> int | None:
    try:
        return int(texto.strip())
    except (ValueError, AttributeError):
        return None


def ids_permitidos(texto: str) -> list[int] | None:
    ids = []
    for parte in (texto or "").split(","):
        if parte.strip():
            n = _entero(parte)
            if n is None:
                return None
            ids.append(n)
    return ids


def validar(valores: dict[str, str]) -> tuple[list[str], list[str]]:
    """(errores que impiden arrancar, avisos de seguridad). Mismas reglas que config.py."""
    errores, avisos = [], []
    if _entero(valores.get("API_ID", "")) is None:
        errores.append("API_ID: falta o no es un número (my.telegram.org).")
    if not valores.get("API_HASH", "").strip():
        errores.append("API_HASH: falta (my.telegram.org).")
    tel = valores.get("TELEFONO", "").strip()
    if not re.fullmatch(r"\+?\d{8,15}", tel):
        errores.append("TELEFONO: falta o no es un teléfono con código de país (+58…).")
    # Con iPhone registrados (pestaña Teléfonos) cada uno trae su cuenta y su clave: lo del .env solo
    # hace falta para un iPhone que no esté registrado.
    if not registro_iphones.cargar():
        if not valores.get("CUENTA_DEBITAR_DESC", "").strip():
            errores.append("CUENTA_DEBITAR_DESC: falta la cuenta a debitar (ej. 0102****1234), o registra el "
                           "iPhone en la pestaña Teléfonos.")
        if not valores.get("BDV_PASSWORD", "").strip():
            errores.append("BDV_PASSWORD: falta la clave del banco, o registra el iPhone en la pestaña Teléfonos.")
    clave_chat = clave_destino(valores, "CHAT_ID_ORDENES")
    chat = _entero(valor_efectivo(valores, "CHAT_ID_ORDENES"))
    if not chat:
        errores.append(f"{clave_chat}: falta el ID del grupo de órdenes (usa «Elegir grupo…»).")
    capt = valor_efectivo(valores, "CHAT_ID_CAPTURAS")
    if capt and _entero(capt) is None:
        errores.append(f"{clave_destino(valores, 'CHAT_ID_CAPTURAS')}: no es un número.")
    tope_txt = valor_efectivo(valores, "MONTO_MAXIMO_AUTO") or "50"
    try:
        tope = float(tope_txt)
        if tope < 0:
            errores.append("MONTO_MAXIMO_AUTO: no puede ser negativo.")
        elif tope == 0:
            avisos.append("MONTO_MAXIMO_AUTO=0: SIN tope, se paga cualquier monto que llegue.")
    except ValueError:
        errores.append(f"{clave_destino(valores, 'MONTO_MAXIMO_AUTO')}: no es un número (usa punto, ej. 100.5).")
    ids = ids_permitidos(valor_efectivo(valores, "USER_IDS_PERMITIDOS"))
    if ids is None:
        errores.append(f"{clave_destino(valores, 'USER_IDS_PERMITIDOS')}: deben ser números separados por coma.")
    elif not ids:
        avisos.append("USER_IDS_PERMITIDOS vacío: CUALQUIERA en el grupo dispara un pago real.")
    return errores, avisos


# ── Otros bots de esta PC ──────────────────────────────────────────

def _archivos_env(carpeta: Path) -> list[Path]:
    salida = []
    for p in sorted(carpeta.glob(".env*")):
        n = p.name
        if n == ".env" or (n.startswith(".env.") and not n.endswith((".example", ".bak", ".tmp", "~"))
                           and ".bak" not in n):
            salida.append(p)
    return salida


def otras_configs(raiz: Path = RAIZ) -> list[dict]:
    """Sesión y grupo de los OTROS bots de esta PC (carpetas vecinas): .env y .env.<perfil>.
    Solo se leen esas dos claves; nada de credenciales."""
    salida = []
    carpetas = [raiz.parent] + sorted(p for p in raiz.parent.iterdir() if p.is_dir() and p != raiz)
    for carpeta in carpetas:
        base = leer_env(carpeta / ".env")
        for archivo in _archivos_env(carpeta):
            vals = dict(base)
            vals.update(leer_env(archivo))
            grupo = _entero(vals.get("CHAT_ID_ORDENES_IPHONE", "") or vals.get("CHAT_ID_ORDENES", ""))
            sesion = vals.get("SESSION_NAME", "").strip()
            salida.append({
                "nombre": f"{carpeta.name}\\{archivo.name}",
                "sesion": str(carpeta / sesion).lower() if sesion else "",
                "grupo": grupo,
            })
    return salida


def procesos_python() -> list[dict]:
    return plataforma.procesos_python()


def procesos_bot(excluir: set[int] = frozenset()) -> list[dict]:
    """Procesos de cualquier bot de pagos de esta PC (orquestador.py / lanzador.py), menos los
    `excluir` (el bot de este panel y su lanzador)."""
    salida = []
    for p in procesos_python():
        linea = p.get("CommandLine") or ""
        if re.search(r"orquestador\.py|lanzador\.py", linea, re.I) and p.get("ProcessId") not in excluir:
            salida.append(p)
    return salida


def linea_de_comando(pid: int) -> str:
    return plataforma.linea_de_comando(pid)


def conflictos(valores: dict[str, str], configs: list[dict], hay_otros_bots: bool) -> tuple[list[str], list[str]]:
    """(bloqueos, preguntas) por compartir sesión o grupo con otro bot de esta PC."""
    bloqueos, preguntas = [], []
    sesion = ruta_sesion(valores).lower()
    grupo = _entero(valor_efectivo(valores, "CHAT_ID_ORDENES"))
    for c in configs:
        if c["sesion"] and c["sesion"] == sesion:
            texto = (f"La sesión de Telegram es la misma que usa {c['nombre']}. Dos bots con la misma "
                     "sesión la invalidan (AuthKeyDuplicatedError).")
            (bloqueos if hay_otros_bots else preguntas).append(texto)
        if grupo and c["grupo"] == grupo:
            texto = (f"El grupo {grupo} también lo escucha {c['nombre']}. Con los dos encendidos "
                     "CADA ORDEN SE PAGARÍA DOS VECES.")
            (bloqueos if hay_otros_bots else preguntas).append(texto)
    return bloqueos, preguntas


# ── Estado del bot ─────────────────────────────────────────────────

def estado_bot(sesion: str) -> dict:
    """La verdad de "hay un bot" es el puerto de instancia_unica; estado_bot.json dice qué hace."""
    if not instancia_unica.en_uso(sesion):
        return {"corriendo": False}
    est = control_bot.leer_estado()
    if not est or not control_bot._proceso_vivo(est.get("pid")):
        return {"corriendo": True, "ajeno": True}
    return {"corriendo": True, "ajeno": False, "pid": est.get("pid"), "fase": est.get("fase"),
            "orden": est.get("orden"), "desde": est.get("desde"), "en_cola": est.get("en_cola") or 0,
            "iphone": est.get("iphone")}


def entorno_hijo(extra: dict | None = None, ruta_env: Path = ARCHIVO_ENV) -> dict:
    """Entorno para el bot y las herramientas: SIN las claves del .env, para que lean el archivo
    recién guardado y no lo que config.py cargó al abrir el panel."""
    env = dict(os.environ)
    for clave in CLAVES_CARGADAS_DEL_ENV | set(leer_env(ruta_env)):
        env.pop(clave, None)
    env.update(DISPOSITIVO="iphone", PYTHONIOENCODING="utf-8", PYTHONUTF8="1",
               PYTHONUNBUFFERED="1", BOT_DESDE_PANEL="1")
    env.update(ENTORNO_EXTRA)
    env.update(extra or {})
    return env


def iniciar_bot() -> subprocess.Popen:
    registro_bot.CARPETA_LOGS.mkdir(exist_ok=True)
    registro_bot.rotar_si_grande()
    with open(ARCHIVO_LOG, "a", encoding="utf-8") as salida:
        salida.write(registro_bot.marca_inicio("panel"))
        salida.flush()
        # python.exe (no pythonw): el bot escribe texto, que va al registro.
        return subprocess.Popen([str(PY_BOT), str(ORQUESTADOR)], cwd=str(RAIZ), stdout=salida,
                                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=entorno_hijo(),
                                creationflags=SIN_VENTANA, **plataforma.OPCIONES_DESACOPLADO)


def ultimas_lineas(log: Path = ARCHIVO_LOG, bytes_finales: int = 40000) -> str:
    try:
        with open(log, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - bytes_finales))
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def tramo_ultimo_arranque(texto: str) -> str:
    i = texto.rfind("===== Inicio ")
    return texto[i:] if i >= 0 else texto


def apagado_fue_pedido(tramo: str) -> bool:
    """El bot dejó constancia de un apagado ordenado (desde el panel o desde bot_iphone.bat)."""
    return "Apagado pedido" in tramo or "Sin órdenes pendientes: desconectando" in tramo


_RE_ERROR = re.compile(r" (ERROR|CRITICAL) |^Traceback|^[A-Za-z_.]*(Error|Exception|Exit)\b|^Ya hay otro programa")


def motivo_ultimo_arranque(tramo: str) -> str:
    lineas = [l.strip() for l in tramo.splitlines() if l.strip() and not l.startswith("=====")]
    errores = [l for l in lineas if _RE_ERROR.search(l)]
    elegidas = errores[-4:] if errores else lineas[-5:]
    return "\n".join(l[:300] for l in elegidas) or "(sin detalle en el registro)"


def forzar_apagado(pid: int | None) -> str:
    """Último recurso. Solo mata el proceso si su línea de comando es de verdad el bot."""
    if not pid:
        return "no se encontró el PID del bot (estado_bot.json)"
    linea = linea_de_comando(pid)
    if "orquestador.py" not in linea.lower():
        return f"el proceso {pid} no es el bot: no se tocó"
    plataforma.matar_arbol(pid)
    control_bot.limpiar()
    return "bot detenido a la fuerza"


# ── Historial de órdenes (pestaña Actividad) ───────────────────────

RE_LINEA = re.compile(r"^(\d{4}-\d\d-\d\d) (\d\d:\d\d:\d\d),\d+ (\w+) (.*)$")
RE_INICIO = re.compile(r"^===== Inicio (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \((\w+)\) =====")
RE_RECIBIDO = re.compile(r"^Mensaje recibido de user_id=(\S+) tipo=(\S+) banco=.*? texto=(['\"].*)$")
RE_ENCOLADA = re.compile(r"^Orden (\d+) encolada\. Pendientes: (\d+)")
RE_DUPLICADA = re.compile(r"^Orden (\d+) duplicada")
RE_NO_AUTORIZADA = re.compile(r"^Orden (\d+) de remitente no autorizado")
RE_NO_ACEPTADA = re.compile(r"^Orden (\d+) no aceptada: el bot se está apagando")
RE_RECHAZADA = re.compile(r"^Orden (\d+) rechazada: (.*)$")
RE_EJECUTADA = re.compile(r"^Orden (\d+) ejecutada en ([\d.]+) s: (ÉXITO|FALLÓ|INCIERTO)")
RE_DETALLE = re.compile(r"^Orden (\d+) detalle: (.*)$")
RE_ERROR_INESPERADO = re.compile(r"^Error inesperado procesando la orden (\d+)")
RE_FALLIDA_BANCO = re.compile(r"^Orden (\d+): el banco informó que la transferencia FALLÓ \(operación (\S+)\)")
RE_SIN_ENVIAR = re.compile(r"^Orden (\d+): resultado NO enviado a Telegram")
RE_ENVIADO_TARDE = re.compile(r"^Orden (\d+): resultado enviado a Telegram con retraso")
ARCHIVO_PENDIENTES = RAIZ / "envios_pendientes.json"

RESULTADOS_TEXTO = {
    "recibida": "recibida", "en cola": "en cola", "ÉXITO": "✓ pagada", "FALLÓ": "✗ falló",
    "INCIERTO": "? INCIERTA", "duplicada": "duplicada (ignorada)", "no autorizada": "remitente no autorizado",
    "no aceptada": "no aceptada (apagando)", "rechazada": "✗ rechazada",
}


@dataclass
class Fila:
    fecha: str
    hora: str
    id_orden: str
    tipo: str
    banco: str
    monto: str
    monto_num: float
    beneficiario: str
    destino: str
    remitente: str
    resultado: str = "recibida"
    segundos: float | None = None
    detalle: str = ""
    doble: str = ""          # "" | "PAGADA 2 VECES" | "¿2 veces?"
    envio: str = ""          # "" | "sin enviar a Telegram" | "enviado con retraso"

    @property
    def epoch(self) -> float:
        try:
            return datetime.strptime(f"{self.fecha} {self.hora}", "%Y-%m-%d %H:%M:%S").timestamp()
        except ValueError:
            return 0.0


def cargar_pendientes(archivo: Path = ARCHIVO_PENDIENTES) -> list:
    try:
        datos = json.loads(archivo.read_text(encoding="utf-8"))
        return datos if isinstance(datos, list) else []
    except (OSError, ValueError):
        return []


def _tipo_legible(orden) -> str:
    if orden.tipo == "PAGO_MOVIL":
        return "Pago Móvil"
    return "Terceros BDV" if orden.cuenta.startswith("0102") else "Otros bancos"


class Historial:
    def __init__(self):
        self.filas: list[Fila] = []
        self._ultima: dict[str, Fila] = {}

    def alimentar(self, lineas) -> bool:
        cambio = False
        for linea in lineas:
            cambio |= self._linea(linea.rstrip("\r\n"))
        if cambio:
            self._marcar_dobles()
        return cambio

    def _linea(self, linea: str) -> bool:
        m = RE_LINEA.match(linea)
        if not m:
            return False
        fecha, hora, _nivel, msg = m.groups()
        r = RE_RECIBIDO.match(msg)
        if r:
            try:
                texto = ast.literal_eval(r.group(3))
            except (ValueError, SyntaxError):
                return False
            orden = parsear_orden(texto, 0, 0)
            if orden is None:
                return False
            try:
                monto_num = normalizar_monto(orden.monto)
            except ValueError:
                monto_num = 0.0
            fila = Fila(fecha, hora, orden.id_orden, _tipo_legible(orden), orden.banco, orden.monto,
                        monto_num, orden.nombre, orden.telefono or orden.cuenta, r.group(1))
            self.filas.append(fila)
            self._ultima[orden.id_orden] = fila
            return True
        for patron, resultado in ((RE_ENCOLADA, "en cola"), (RE_DUPLICADA, "duplicada"),
                                  (RE_NO_AUTORIZADA, "no autorizada"), (RE_NO_ACEPTADA, "no aceptada")):
            e = patron.match(msg)
            if e and e.group(1) in self._ultima:
                self._ultima[e.group(1)].resultado = resultado
                return True
        e = RE_RECHAZADA.match(msg)
        if e and e.group(1) in self._ultima:
            fila = self._ultima[e.group(1)]
            fila.resultado, fila.detalle = "rechazada", e.group(2)
            return True
        e = RE_EJECUTADA.match(msg)
        if e and e.group(1) in self._ultima:
            fila = self._ultima[e.group(1)]
            fila.resultado, fila.segundos = e.group(3), float(e.group(2))
            return True
        e = RE_DETALLE.match(msg)
        if e and e.group(1) in self._ultima:
            self._ultima[e.group(1)].detalle = e.group(2)
            return True
        e = RE_ERROR_INESPERADO.match(msg)
        if e and e.group(1) in self._ultima:
            fila = self._ultima[e.group(1)]
            # Si el pago ya tiene resultado, el error fue después (ej. al avisar en Telegram):
            # no se pisa un ÉXITO con un INCIERTO.
            if fila.resultado not in ("ÉXITO", "FALLÓ", "INCIERTO"):
                fila.resultado, fila.detalle = "INCIERTO", "error inesperado: revisa el banco"
            return True
        e = RE_FALLIDA_BANCO.match(msg)
        if e and e.group(1) in self._ultima:
            # El comprobante salió (ÉXITO) pero el banco avisó después que la interbancaria falló.
            fila = self._ultima[e.group(1)]
            fila.resultado = "FALLÓ"
            fila.detalle = f"el banco la marcó FALLIDA después del comprobante (operación {e.group(2)})"
            return True
        e = RE_SIN_ENVIAR.match(msg)
        if e and e.group(1) in self._ultima:
            self._ultima[e.group(1)].envio = "sin enviar a Telegram"
            return True
        e = RE_ENVIADO_TARDE.match(msg)
        if e and e.group(1) in self._ultima:
            self._ultima[e.group(1)].envio = "enviado con retraso"
            return True
        return False

    def _marcar_dobles(self):
        """El bot recuerda los IDs solo mientras está abierto: una orden reenviada tras reiniciarlo
        se vuelve a pagar. Aquí se marcan las que salieron (o pudieron salir) más de una vez."""
        por_id: dict[str, list[Fila]] = {}
        for f in self.filas:
            f.doble = ""
            if f.resultado in ("ÉXITO", "INCIERTO"):
                por_id.setdefault(f.id_orden, []).append(f)
        for filas in por_id.values():
            if len(filas) < 2:
                continue
            exitos = sum(1 for f in filas if f.resultado == "ÉXITO")
            marca = "PAGADA 2 VECES" if exitos >= 2 else ("¿2 veces?" if exitos == 1 else "")
            for f in filas:
                f.doble = marca

    def resumen(self, dia: str | None = None) -> dict:
        dia = dia or time.strftime("%Y-%m-%d")
        hoy = [f for f in self.filas if f.fecha == dia]
        aceptadas = [f for f in hoy if f.resultado in ("en cola", "ÉXITO", "FALLÓ", "INCIERTO")]
        return {
            "ordenes": len(aceptadas),
            "exito": sum(1 for f in hoy if f.resultado == "ÉXITO"),
            "fallo": sum(1 for f in hoy if f.resultado in ("FALLÓ", "rechazada")),
            "incierto": sum(1 for f in hoy if f.resultado == "INCIERTO"),
            "bs": sum(f.monto_num for f in hoy if f.resultado == "ÉXITO"),
            "dobles": sum(1 for f in hoy if f.doble),
        }

    def remitentes_vistos(self) -> dict[str, int]:
        vistos: dict[str, int] = {}
        for f in self.filas:
            vistos[f.remitente] = vistos.get(f.remitente, 0) + 1
        return vistos


class LectorLog:
    """Lee bot.log por partes: solo lo nuevo, y solo líneas completas."""

    def __init__(self, log: Path = ARCHIVO_LOG):
        self.log = log
        self.pos = 0

    def inicial(self) -> list[str]:
        lineas = []
        viejo = self.log.with_name(self.log.name + ".1")
        for archivo in (viejo, self.log):
            try:
                lineas += archivo.read_bytes().decode("utf-8", errors="replace").splitlines()
            except OSError:
                pass
        try:
            self.pos = self.log.stat().st_size
        except OSError:
            self.pos = 0
        return lineas

    def nuevas(self) -> tuple[list[str], bool]:
        """(líneas nuevas, se_reinicio). se_reinicio=True si el archivo se archivó/achicó."""
        try:
            tam = self.log.stat().st_size
        except OSError:
            return [], False
        if tam < self.pos:
            return self.inicial(), True
        if tam == self.pos:
            return [], False
        with open(self.log, "rb") as f:
            f.seek(self.pos)
            datos = f.read(tam - self.pos)
        corte = datos.rfind(b"\n")
        if corte < 0:
            return [], False
        self.pos += corte + 1
        return datos[:corte + 1].decode("utf-8", errors="replace").splitlines(), False


def clasificar_linea(linea: str) -> tuple[str, bool]:
    """(etiqueta de color, importante) para el registro en vivo."""
    if linea.startswith("===== Inicio"):
        return "marca", True
    toque = bool(re.match(r"^\d\d:\d\d:\d\d\s+-> ", linea))
    importante = bool(RE_LINEA.match(linea)) and not toque
    if "ÉXITO" in linea or "Pago ejecutado" in linea:
        return "exito", True
    if " ERROR " in linea or "FALLÓ" in linea or "rechazada" in linea or linea.startswith("Traceback"):
        return "fallo", True
    if "INCIERTO" in linea or linea.lstrip().startswith("!!"):
        return "incierto", True
    if " WARNING " in linea or "no autorizado" in linea or "duplicada" in linea or "Apagado" in linea:
        return "aviso", True
    if re.match(r"^[A-Za-z_.]*(Error|Exception)\b", linea):
        return "fallo", True
    return ("toque" if toque else "normal"), importante


def captura_de(fila: Fila, carpeta: Path = CARPETA_CAPTURAS) -> Path | None:
    """La captura que el bot guardó para esa orden: la primera tomada desde que llegó."""
    candidatos = []
    for p in carpeta.glob(f"{fila.id_orden}_*.png"):
        try:
            candidatos.append((int(p.stem.rsplit("_", 1)[1]), p))
        except (IndexError, ValueError):
            pass
    if not candidatos:
        return None
    candidatos.sort()
    desde = fila.epoch - 5
    despues = [p for t, p in candidatos if t >= desde]
    return despues[0] if despues else candidatos[-1][1]


def bs(monto: float) -> str:
    return f"{monto:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ── iPhone (por pymobiledevice3, en un proceso aparte) ─────────────

_RE_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def correr_bot_python(args: list[str], timeout: float = 60, extra_env: dict | None = None) -> tuple[int, str]:
    try:
        r = subprocess.run([str(PY_BOT)] + args, cwd=str(RAIZ), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           env=dict(entorno_hijo(), **(extra_env or {})),
                           creationflags=SIN_VENTANA)
    except subprocess.TimeoutExpired:
        return 1, f"no respondió en {timeout:.0f} s"
    return r.returncode, _RE_ANSI.sub("", (r.stdout or "") + (r.stderr or "")).strip()


def _json_en(texto: str):
    for abre, cierra in (("[", "]"), ("{", "}")):
        i, j = texto.find(abre), texto.rfind(cierra)
        if i >= 0 and j > i:
            try:
                return json.loads(texto[i:j + 1])
            except ValueError:
                pass
    return None


def _explicar_error_iphone(salida: str) -> str:
    if "usbmuxd" in salida:
        return ("no hay servicio de Apple (usbmuxd): instala iTunes de apple.com "
                "(no la app 'Apple Devices') y comprueba el servicio 'Apple Mobile Device Service'")
    if "Developer Mode" in salida or "developer mode" in salida:
        return "el Modo de desarrollador del iPhone está apagado (guía, paso 6)"
    if "NotPaired" in salida or "pair" in salida.lower():
        return "el iPhone no confía en esta PC: desbloquéalo y toca «Confiar»"
    ultima = [l for l in salida.splitlines() if l.strip()]
    return ultima[-1][:200] if ultima else "error desconocido"


def iphone_conectados() -> list[str]:
    """UDID de los iPhone conectados por USB. Solo pregunta a usbmuxd: NO abre conexión con el
    iPhone, así que se puede usar con el bot encendido (nunca mientras paga)."""
    codigo, salida = correr_bot_python(["-m", "pymobiledevice3", "usbmux", "list", "--usb", "--simple"], 25)
    if codigo != 0:
        raise RuntimeError(_explicar_error_iphone(salida))
    datos = _json_en(salida)
    if isinstance(datos, list):
        return [d if isinstance(d, str) else str(d.get("UniqueDeviceID") or d.get("Identifier") or d)
                for d in datos]
    return re.findall(r"\b[0-9A-Fa-f]{8}-?[0-9A-Fa-f]{16}\b|\b[0-9a-f]{40}\b", salida)


def iphone_detalle() -> dict:
    """Nombre, iOS, batería y Modo de desarrollador. Abre conexión (lockdown): SOLO con el bot apagado."""
    codigo, salida = correr_bot_python(["-m", "pymobiledevice3", "usbmux", "list", "--usb"], 30)
    if codigo != 0:
        raise RuntimeError(_explicar_error_iphone(salida))
    lista = _json_en(salida)
    if not lista:
        return {"conectado": False}
    d = lista[0] if isinstance(lista, list) else lista
    info = {"conectado": True, "nombre": d.get("DeviceName", "iPhone"), "ios": d.get("ProductVersion", "?"),
            "udid": d.get("UniqueDeviceID") or d.get("Identifier", ""), "varios": len(lista) > 1}
    codigo, salida = correr_bot_python(["-m", "pymobiledevice3", "diagnostics", "battery", "single"], 30)
    bat = _json_en(salida) if codigo == 0 else None
    if isinstance(bat, dict):
        info["bateria"] = bat.get("CurrentCapacity")
        info["cargando"] = bool(bat.get("IsCharging") or bat.get("ExternalConnected"))
    codigo, salida = correr_bot_python(["-m", "pymobiledevice3", "amfi", "developer-mode-status"], 30)
    if codigo == 0:
        info["desarrollador"] = "true" in salida.lower()
    return info


# ── Telegram (con la sesión del bot; solo con el bot apagado) ──────

def con_telegram(valores: dict[str, str], accion):
    """Corre `accion(client)` (async) con la sesión del bot. connect(), nunca start(): no pide
    código ni crea una sesión nueva. Toma el candado de la sesión mientras dura."""
    sesion = ruta_sesion(valores)
    if not Path(sesion + ".session").exists():
        raise RuntimeError(f"no existe la sesión {sesion}.session. Inicia sesión una vez con:\n"
                           "iphone/.venv/bin/python login_telegram.py   (Windows: iphone\\.venv\\Scripts\\python.exe login_telegram.py)")
    if _entero(valores.get("API_ID", "")) is None or not valores.get("API_HASH"):
        raise RuntimeError("faltan API_ID / API_HASH en Configuración")
    candado = instancia_unica._abrir(sesion)
    if candado is None:
        raise RuntimeError("la sesión está en uso (¿el bot o listar_chats.py están abiertos?)")
    try:
        from telethon import TelegramClient

        async def correr():
            client = TelegramClient(sesion, int(valores["API_ID"]), valores["API_HASH"])
            await client.connect()
            try:
                if not await client.is_user_authorized():
                    raise RuntimeError("la sesión NO está autorizada: corre login_telegram.py una vez")
                return await accion(client)
            finally:
                await client.disconnect()
        return asyncio.run(correr())
    finally:
        candado.close()


async def _listar_grupos(client) -> list[tuple[int, str]]:
    grupos = []
    async for d in client.iter_dialogs():
        if d.is_group or d.is_channel:
            grupos.append((d.id, d.name or "(sin nombre)"))
    return grupos


def cargar_grupos() -> dict[str, str]:
    try:
        return json.loads(ARCHIVO_GRUPOS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def guardar_grupos(grupos: dict[str, str]):
    try:
        ARCHIVO_GRUPOS.write_text(json.dumps(grupos, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


# ── Respaldo ───────────────────────────────────────────────────────

EXCLUIR_CARPETAS = {".venv", "__pycache__", "capturas", "logs", "respaldos", ".git"}
EXCLUIR_ARCHIVOS = {"panel_clave.json", "panel_grupos.json", "estado_bot.json", "estado_bot.tmp",
                    "parar_bot.flag", "envios_pendientes.json", "envios_pendientes.tmp"}


def incluir_en_respaldo(rel: Path) -> bool:
    if any(p in EXCLUIR_CARPETAS for p in rel.parts[:-1]):
        return False
    n = rel.name
    if n in EXCLUIR_ARCHIVOS or ".session" in n:
        return False
    if n.startswith(".env") and n != ".env.example":
        return False
    return True


def hacer_respaldo(raiz: Path = RAIZ) -> Path:
    """Zip con código y documentos, SIN credenciales (.env), sesiones ni capturas."""
    CARPETA_RESPALDOS.mkdir(exist_ok=True)
    destino = CARPETA_RESPALDOS / f"{time.strftime('%Y-%m-%d_%H%M')}_panel.zip"
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(raiz.rglob("*")):
            rel = p.relative_to(raiz)
            if p.is_file() and incluir_en_respaldo(rel):
                z.write(p, rel.as_posix())
    return destino


# ── Ventanas ───────────────────────────────────────────────────────

FUENTE = ("Segoe UI", 10)
COLORES = {"exito": "#1b7f2a", "fallo": "#b00020", "incierto": "#c25e00", "aviso": "#8a6d00",
           "marca": "#1f4e9c", "toque": "#8a8a8a", "normal": "#202020"}


def al_frente(ventana: tk.Misc):
    ventana.update_idletasks()
    ventana.lift()
    ventana.attributes("-topmost", True)
    ventana.focus_force()


def pedir_texto(raiz: tk.Tk, titulo: str, mensaje: str, oculto: bool = True) -> str | None:
    resultado: list[str | None] = [None]
    ventana = tk.Toplevel(raiz)
    ventana.title(titulo)
    ventana.resizable(False, False)
    tk.Label(ventana, text=mensaje, font=("Segoe UI", 12)).pack(padx=24, pady=(16, 6))
    entrada = tk.Entry(ventana, show="*" if oculto else "", font=("Segoe UI", 14), width=26)
    entrada.pack(padx=24, pady=6)

    def aceptar(_evento=None):
        resultado[0] = entrada.get()
        ventana.destroy()

    def cancelar(_evento=None):
        ventana.destroy()

    botones = tk.Frame(ventana, pady=14)
    botones.pack()
    tk.Button(botones, text="Aceptar", width=12, font=("Segoe UI", 11), command=aceptar).pack(side="left", padx=6)
    tk.Button(botones, text="Cancelar", width=12, font=("Segoe UI", 11), command=cancelar).pack(side="left", padx=6)
    ventana.bind("<Return>", aceptar)
    ventana.bind("<Escape>", cancelar)
    ventana.protocol("WM_DELETE_WINDOW", cancelar)
    ventana.update_idletasks()
    x = (ventana.winfo_screenwidth() - ventana.winfo_reqwidth()) // 2
    y = (ventana.winfo_screenheight() - ventana.winfo_reqheight()) // 3
    ventana.geometry(f"+{x}+{y}")
    al_frente(ventana)
    entrada.focus_set()
    ventana.grab_set()
    raiz.wait_window(ventana)
    return resultado[0]


def pedir_acceso(raiz: tk.Tk) -> bool:
    if not ARCHIVO_CLAVE.exists():
        messagebox.showinfo("Panel del bot", "Primera vez: crea la clave del panel.", parent=raiz)
        while True:
            nueva = pedir_texto(raiz, "Crear clave", "Clave nueva (mínimo 6 caracteres):")
            if nueva is None:
                return False
            if len(nueva) < 6:
                messagebox.showerror("Panel del bot", "Muy corta: mínimo 6 caracteres.", parent=raiz)
                continue
            if pedir_texto(raiz, "Crear clave", "Repite la clave:") != nueva:
                messagebox.showerror("Panel del bot", "No coinciden. Intenta de nuevo.", parent=raiz)
                continue
            guardar_clave(nueva)
            return True
    for intento in range(1, 6):
        clave = pedir_texto(raiz, "Panel del bot", "Clave del panel:")
        if clave is None:
            return False
        if clave_correcta(clave):
            return True
        time.sleep(intento)  # cada intento fallido espera un poco más
        messagebox.showerror("Panel del bot", "Clave incorrecta.", parent=raiz)
    return False


def elegir_de_lista(raiz, titulo: str, mensaje: str, opciones: list[tuple[str, str]],
                    multiple: bool = False, marcados: set[str] = frozenset()) -> list[str] | None:
    """opciones = [(valor, texto)]. Devuelve los valores elegidos, o None si se cancela."""
    resultado: list = [None]
    v = tk.Toplevel(raiz)
    v.title(titulo)
    v.transient(raiz)
    tk.Label(v, text=mensaje, font=FUENTE, justify="left").pack(padx=14, pady=(12, 6), anchor="w")
    marco = tk.Frame(v)
    marco.pack(fill="both", expand=True, padx=14)
    lista = tk.Listbox(marco, font=("Consolas", 10), width=70, height=16,
                       selectmode="multiple" if multiple else "browse", exportselection=False)
    barra = tk.Scrollbar(marco, command=lista.yview)
    lista.config(yscrollcommand=barra.set)
    lista.pack(side="left", fill="both", expand=True)
    barra.pack(side="right", fill="y")
    for i, (valor, texto) in enumerate(opciones):
        lista.insert("end", texto)
        if valor in marcados:
            lista.selection_set(i)

    def aceptar(_e=None):
        resultado[0] = [opciones[i][0] for i in lista.curselection()]
        v.destroy()

    botones = tk.Frame(v, pady=10)
    botones.pack()
    tk.Button(botones, text="Aceptar", width=12, command=aceptar).pack(side="left", padx=6)
    tk.Button(botones, text="Cancelar", width=12, command=v.destroy).pack(side="left", padx=6)
    lista.bind("<Double-Button-1>", aceptar)
    v.grab_set()
    raiz.wait_window(v)
    return resultado[0]


class Panel:
    def __init__(self, raiz: tk.Tk):
        self.raiz = raiz
        try:                                 # teléfonos del zip (sin clave): se agregan solos, sin pisar nada
            registro_iphones.importar_plantilla()
        except Exception:
            pass
        self.cola: queue.Queue = queue.Queue()
        self.proceso: subprocess.Popen | None = None
        self.estaba_corriendo = False
        self.apagado_pedido = False          # Detener (o Cancelar arranque) desde este panel
        self.apagando_desde: float | None = None
        self.comprobando = False
        self.estado: dict = {"corriendo": False}
        self.valores = leer_env()
        self.grupos = cargar_grupos()
        self.historial = Historial()
        self.lector = LectorLog()
        self.registro: deque = deque(maxlen=5000)   # (línea, etiqueta, importante)
        self.info_iphone = "Comprobando…"
        self._iphone_ocupado = False
        self._ultimo_iphone = 0.0
        self.otros_bots: list[dict] = []
        self._procesos_ocupado = False
        self._ultimo_procesos = 0.0
        self._ultimo_resumen = None

        raiz.title(f"Panel Bot iPhone 12 — {RAIZ.name}")
        raiz.geometry("1180x760")
        raiz.minsize(900, 600)
        raiz.protocol("WM_DELETE_WINDOW", self.cerrar)
        estilo = ttk.Style(raiz)
        estilo.configure("Treeview", font=("Segoe UI", 9), rowheight=22)
        estilo.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))
        estilo.configure("TNotebook.Tab", font=("Segoe UI", 10), padding=(12, 4))

        self._construir_barra()
        self._construir_tarjetas()
        self.pestanas = ttk.Notebook(raiz)
        self.pestanas.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self._construir_registro()
        self._construir_actividad()
        self._construir_configuracion()
        self._construir_telefonos()
        self._construir_herramientas()

        lineas = self.lector.inicial()
        self.historial.alimentar(lineas)
        self._agregar_registro(lineas[-3000:])
        self._refrescar_actividad()
        self.raiz.after(100, self._procesar_cola)
        self.tick()

    # ── infraestructura ─────────────────────────────────────────────
    def _procesar_cola(self):
        try:
            while True:
                self.cola.get_nowait()()
        except queue.Empty:
            pass
        self.raiz.after(100, self._procesar_cola)

    def en_hilo(self, trabajo, al_terminar=None):
        """Corre `trabajo()` en un hilo; `al_terminar(resultado, error)` vuelve al hilo de la ventana."""
        def correr():
            try:
                res, err = trabajo(), None
            except Exception as exc:     # se muestra en la ventana, nunca tumba el panel
                res, err = None, exc
            if al_terminar:
                self.cola.put(lambda: al_terminar(res, err))
        threading.Thread(target=correr, daemon=True).start()

    def sesion(self) -> str:
        return ruta_sesion(self.valores)

    # ── barra de arriba ─────────────────────────────────────────────
    def _construir_barra(self):
        arriba = tk.Frame(self.raiz, padx=12, pady=8)
        arriba.pack(fill="x")
        self.lbl_estado = tk.Label(arriba, font=("Segoe UI", 15, "bold"))
        self.lbl_estado.pack(side="left")
        self.btn_forzar = tk.Button(arriba, text="Forzar apagado", fg="white", bg="#8b0000",
                                    font=("Segoe UI", 10), command=self.forzar)
        self.btn_detener = tk.Button(arriba, text="Detener", width=16, font=("Segoe UI", 11),
                                     command=self.detener)
        self.btn_detener.pack(side="right", padx=4)
        self.btn_iniciar = tk.Button(arriba, text="Iniciar", width=12, font=("Segoe UI", 11, "bold"),
                                     command=self.iniciar)
        self.btn_iniciar.pack(side="right", padx=4)

    def _construir_tarjetas(self):
        fila = tk.Frame(self.raiz, padx=10)
        fila.pack(fill="x", pady=(0, 8))
        self.tarjetas = {}
        for i, nombre in enumerate(("Bot", "iPhone", "Telegram", "Hoy")):
            marco = tk.LabelFrame(fila, text=nombre, font=("Segoe UI", 10, "bold"), padx=8, pady=6)
            marco.grid(row=0, column=i, sticky="nsew", padx=4)
            fila.columnconfigure(i, weight=1, uniform="tarjetas")
            etiqueta = tk.Label(marco, font=FUENTE, justify="left", anchor="nw", wraplength=260, height=4)
            etiqueta.pack(fill="both", expand=True)
            self.tarjetas[nombre] = etiqueta

    # ── Registro ───────────────────────────────────────────────────
    def _construir_registro(self):
        marco = tk.Frame(self.pestanas)
        self.pestanas.add(marco, text="Registro")
        barra = tk.Frame(marco)
        barra.pack(fill="x", pady=4)
        self.solo_importante = tk.BooleanVar(value=False)
        tk.Checkbutton(barra, text="Solo lo importante", variable=self.solo_importante, font=FUENTE,
                       command=self._redibujar_registro).pack(side="left")
        tk.Button(barra, text="Abrir registro completo", command=lambda: self._abrir(ARCHIVO_LOG)).pack(side="right")
        self.txt = tk.Text(marco, font=("Consolas", 9), state="disabled", wrap="none")
        sy = tk.Scrollbar(marco, command=self.txt.yview)
        sx = tk.Scrollbar(marco, orient="horizontal", command=self.txt.xview)
        self.txt.config(yscrollcommand=sy.set, xscrollcommand=sx.set)
        sy.pack(side="right", fill="y")
        sx.pack(side="bottom", fill="x")
        self.txt.pack(fill="both", expand=True)
        for etiqueta, color in COLORES.items():
            self.txt.tag_configure(etiqueta, foreground=color)
        self.txt.tag_configure("marca", foreground=COLORES["marca"], font=("Consolas", 9, "bold"))

    def _agregar_registro(self, lineas: list[str]):
        if not lineas:
            return
        nuevas = []
        for linea in lineas:
            etiqueta, importante = clasificar_linea(linea)
            self.registro.append((linea, etiqueta, importante))
            nuevas.append((linea, etiqueta, importante))
        al_final = self.txt.yview()[1] >= 0.98
        self.txt.config(state="normal")
        solo = self.solo_importante.get()
        for linea, etiqueta, importante in nuevas:
            if importante or not solo:
                self.txt.insert("end", linea + "\n", etiqueta)
        exceso = int(self.txt.index("end-1c").split(".")[0]) - 5000
        if exceso > 0:
            self.txt.delete("1.0", f"{exceso}.0")
        self.txt.config(state="disabled")
        if al_final:
            self.txt.see("end")

    def _redibujar_registro(self):
        solo = self.solo_importante.get()
        self.txt.config(state="normal")
        self.txt.delete("1.0", "end")
        for linea, etiqueta, importante in self.registro:
            if importante or not solo:
                self.txt.insert("end", linea + "\n", etiqueta)
        self.txt.config(state="disabled")
        self.txt.see("end")

    # ── Actividad ──────────────────────────────────────────────────
    def _construir_actividad(self):
        marco = tk.Frame(self.pestanas)
        self.pestanas.add(marco, text="Actividad")
        barra = tk.Frame(marco)
        barra.pack(fill="x", pady=4)
        self.solo_hoy = tk.BooleanVar(value=True)
        tk.Checkbutton(barra, text="Solo hoy", variable=self.solo_hoy, font=FUENTE,
                       command=self._refrescar_actividad).pack(side="left")
        tk.Label(barra, text="Doble clic en una orden abre su captura.", font=FUENTE, fg="#555").pack(side="left", padx=16)
        self.lbl_dobles = tk.Label(barra, font=("Segoe UI", 10, "bold"), fg=COLORES["fallo"])
        self.lbl_dobles.pack(side="right")
        columnas = ("hora", "tipo", "banco", "monto", "beneficiario", "resultado", "tiempo", "detalle")
        anchos = (130, 95, 130, 90, 190, 150, 60, 380)
        self.tabla = ttk.Treeview(marco, columns=columnas, show="headings")
        for c, a in zip(columnas, anchos):
            self.tabla.heading(c, text=c.capitalize())
            self.tabla.column(c, width=a, anchor="e" if c in ("monto", "tiempo") else "w", stretch=c == "detalle")
        sy = ttk.Scrollbar(marco, command=self.tabla.yview)
        self.tabla.config(yscrollcommand=sy.set)
        sy.pack(side="right", fill="y")
        self.tabla.pack(fill="both", expand=True)
        for etiqueta in ("exito", "fallo", "incierto", "aviso", "toque"):
            self.tabla.tag_configure(etiqueta, foreground=COLORES[etiqueta])
        self.tabla.tag_configure("doble", background="#ffd6d6")
        self.tabla.tag_configure("pagando", background="#dbe9ff")
        self.tabla.bind("<Double-1>", self._abrir_captura)
        self._filas_tabla: dict[str, Fila] = {}

    def _refrescar_actividad(self):
        hoy = time.strftime("%Y-%m-%d")
        filas = [f for f in self.historial.filas if not self.solo_hoy.get() or f.fecha == hoy][-800:]
        pagando = self.estado.get("orden")
        self.tabla.delete(*self.tabla.get_children())
        self._filas_tabla = {}
        for i, f in enumerate(reversed(filas)):
            res = RESULTADOS_TEXTO.get(f.resultado, f.resultado)
            if f.resultado == "en cola" and pagando == f.id_orden and f is self.historial._ultima.get(f.id_orden):
                res = "pagando…"
            if f.doble:
                res += f"  ⚠ {f.doble}"
            if f.envio:
                res += ("  ⚠ " if f.envio.startswith("sin") else "  · ") + f.envio
            etiqueta = {"ÉXITO": "exito", "FALLÓ": "fallo", "rechazada": "fallo", "INCIERTO": "incierto",
                        "duplicada": "toque", "no autorizada": "aviso", "no aceptada": "aviso"}.get(f.resultado, "")
            etiquetas = [t for t in (etiqueta, "doble" if f.doble else "",
                                     "pagando" if res == "pagando…" else "") if t]
            iid = str(i)
            self.tabla.insert("", "end", iid=iid, tags=etiquetas, values=(
                f"{f.fecha[5:]} {f.hora}", f.tipo, f.banco[:28], f.monto, f.beneficiario[:32], res,
                f"{f.segundos:.0f} s" if f.segundos is not None else "", f.detalle or f"id {f.id_orden}"))
            self._filas_tabla[iid] = f
        dobles = sum(1 for f in filas if f.doble)
        self.lbl_dobles.config(text=f"⚠ {dobles} orden(es) con posible pago doble: revísalas en el banco"
                               if dobles else "")

    def _abrir_captura(self, _evento=None):
        sel = self.tabla.selection()
        if not sel:
            return
        fila = self._filas_tabla.get(sel[0])
        if fila is None:
            return
        ruta = captura_de(fila)
        if ruta is None:
            messagebox.showinfo("Captura", f"No hay captura guardada de la orden {fila.id_orden}.", parent=self.raiz)
        else:
            self._abrir(ruta)

    # ── Configuración ──────────────────────────────────────────────
    CAMPOS = (
        ("API_ID", "API ID de Telegram", False),
        ("API_HASH", "API HASH de Telegram", True),
        ("TELEFONO", "Teléfono de esa cuenta de Telegram (+58…)", False),
        ("SESSION_NAME", "Sesión de Telegram (nombre sin .session)", False),
        ("CHAT_ID_ORDENES", "Grupo de órdenes (ID)", False),
        ("CHAT_ID_CAPTURAS", "Grupo de capturas (vacío = el mismo)", False),
        ("CUENTA_DEBITAR_DESC", "Cuenta a debitar (como la muestra la app)", False),
        ("BDV_PASSWORD", "Clave del banco", True),
        ("MONTO_MAXIMO_AUTO", "Tope por orden en Bs (0 = sin tope)", False),
        ("USER_IDS_PERMITIDOS", "Remitentes permitidos (IDs, separados por coma)", False),
        ("SERIAL_ADB", "UDID del iPhone (vacío = el único conectado)", False),
    )

    def _construir_configuracion(self):
        marco = tk.Frame(self.pestanas, padx=12, pady=10)
        self.pestanas.add(marco, text="Configuración")
        tk.Label(marco, text="Edita el .env de ESTA PC. Se guarda copia en .env.bak; los cambios se aplican "
                             "la próxima vez que se inicie el bot.", font=FUENTE, fg="#555").grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))
        self.entradas: dict[str, tk.Entry] = {}
        self.lbl_claves: dict[str, tk.Label] = {}
        self.ocultos: list[tk.Entry] = []
        for i, (clave, texto, oculto) in enumerate(self.CAMPOS, start=1):
            tk.Label(marco, text=texto, font=FUENTE).grid(row=i, column=0, sticky="w", pady=3)
            e = tk.Entry(marco, font=("Segoe UI", 10), width=46, show="*" if oculto else "")
            e.grid(row=i, column=1, sticky="w", padx=8)
            self.entradas[clave] = e
            if oculto:
                self.ocultos.append(e)
            extra = tk.Frame(marco)
            extra.grid(row=i, column=2, sticky="w")
            if clave == "CHAT_ID_ORDENES":
                tk.Button(extra, text="Elegir grupo…", command=self.elegir_grupo).pack(side="left")
            elif clave == "USER_IDS_PERMITIDOS":
                tk.Button(extra, text="Elegir de los vistos…", command=self.elegir_remitentes).pack(side="left")
            lbl = tk.Label(marco, font=("Segoe UI", 9), fg="#777")
            lbl.grid(row=i, column=3, sticky="w", padx=6)
            self.lbl_claves[clave] = lbl
        fila = len(self.CAMPOS) + 1
        self.ver_ocultos = tk.BooleanVar(value=False)
        tk.Checkbutton(marco, text="Mostrar claves", variable=self.ver_ocultos, font=FUENTE,
                       command=lambda: [e.config(show="" if self.ver_ocultos.get() else "*") for e in self.ocultos]
                       ).grid(row=fila, column=1, sticky="w", padx=8, pady=6)
        botones = tk.Frame(marco)
        botones.grid(row=fila + 1, column=0, columnspan=3, sticky="w", pady=8)
        tk.Button(botones, text="Guardar", width=14, font=("Segoe UI", 10, "bold"),
                  command=self.guardar_config).pack(side="left")
        tk.Button(botones, text="Deshacer cambios", width=16, command=self.cargar_config).pack(side="left", padx=8)
        self.lbl_config = tk.Label(marco, font=FUENTE, justify="left", anchor="w", wraplength=1000)
        self.lbl_config.grid(row=fila + 2, column=0, columnspan=4, sticky="w")
        self.cargar_config()

    def _entradas_actuales(self) -> dict[str, str]:
        return {clave: self.entradas[clave].get().strip() for clave, _t, _o in self.CAMPOS}

    def _quizas_recargar_config(self):
        """Si el .env cambió por fuera (Bloc de notas, otra herramienta) y aquí no hay nada editado
        sin guardar, la pestaña se recarga sola: así Guardar nunca pisa lo nuevo con lo viejo."""
        try:
            marca = ARCHIVO_ENV.stat().st_mtime
        except OSError:
            marca = None
        if marca != self._marca_env and self._entradas_actuales() == self._cargado:
            self.cargar_config()

    def cargar_config(self):
        self.valores = leer_env()
        try:
            self._marca_env = ARCHIVO_ENV.stat().st_mtime
        except OSError:
            self._marca_env = None
        for clave, _texto, _oculto in self.CAMPOS:
            destino = clave_destino(self.valores, clave)
            e = self.entradas[clave]
            e.delete(0, "end")
            e.insert(0, self.valores.get(destino, ""))
            nota = f"→ {destino}"
            if clave == "CHAT_ID_ORDENES":
                nombre = self.grupos.get(self.valores.get(destino, ""))
                if nombre:
                    nota += f"  ({nombre})"
            self.lbl_claves[clave].config(text=nota)
        self._cargado = self._entradas_actuales()
        errores, avisos = validar(self.valores)
        if not ARCHIVO_ENV.exists():
            errores.insert(0, "No existe el .env: se creará al guardar.")
        self._mostrar_validacion(errores, avisos)

    def _mostrar_validacion(self, errores, avisos):
        if errores:
            claves = [e.split(":")[0] for e in errores]
            self.lbl_config.config(text="Falta para poder iniciar: " + ", ".join(claves)
                                   + ".\n(El detalle sale al tocar Guardar o Iniciar.)", fg=COLORES["fallo"])
        elif avisos:
            self.lbl_config.config(text="Atención:\n• " + "\n• ".join(avisos), fg=COLORES["incierto"])
        else:
            self.lbl_config.config(text="Configuración completa.", fg=COLORES["exito"])

    def guardar_config(self):
        actuales = leer_env()
        # Solo lo que se editó en la ventana: un campo no tocado nunca pisa el valor del archivo.
        cambios = {clave_destino(actuales, clave): valor for clave, valor in self._entradas_actuales().items()
                   if valor != self._cargado.get(clave)}
        nuevos = dict(actuales)
        nuevos.update(cambios)
        errores, avisos = validar(nuevos)
        clave_pw = cambios.get("BDV_PASSWORD", "")
        if "#" in clave_pw:
            messagebox.showerror("Configuración", "La clave del banco no puede llevar # "
                                 "(el .env lo toma como comentario).", parent=self.raiz)
            return
        _bloq, preguntas = conflictos(nuevos, otras_configs(), hay_otros_bots=False)
        if preguntas and not messagebox.askyesno(
                "Configuración", "\n\n".join(preguntas) + "\n\n¿Guardar igual?", icon="warning", parent=self.raiz):
            return
        try:
            cambio = escribir_env(cambios)
        except (ValueError, OSError) as exc:
            messagebox.showerror("Configuración", f"No se guardó: {exc}", parent=self.raiz)
            return
        self.cargar_config()
        msg = "Guardado (copia anterior en .env.bak)." if cambio else "No había cambios."
        if cambio and self.estado.get("corriendo"):
            msg += "\nEl bot está encendido: los cambios se aplican al reiniciarlo."
        if errores:
            msg += "\n\nTodavía falta para poder iniciar:\n• " + "\n• ".join(errores)
        messagebox.showinfo("Configuración", msg, parent=self.raiz)

    def elegir_grupo(self):
        if self.estado.get("corriendo"):
            messagebox.showinfo("Elegir grupo", "Apaga el bot primero: usa su sesión de Telegram.", parent=self.raiz)
            return
        valores = leer_env()
        valores.update({c: self.entradas[c].get().strip() for c in ("API_ID", "API_HASH", "SESSION_NAME")})
        self.lbl_config.config(text="Leyendo los grupos de Telegram…", fg="#555")

        def listo(grupos, err):
            if err:
                self.lbl_config.config(text="")
                messagebox.showerror("Elegir grupo", str(err), parent=self.raiz)
                return
            self.grupos.update({str(i): n for i, n in grupos})
            guardar_grupos(self.grupos)
            usados = {c["grupo"]: c["nombre"] for c in otras_configs() if c["grupo"]}
            opciones = [(str(i), f"{i:>16}  {n}" + (f"   ← lo usa {usados[i]}" if i in usados else ""))
                        for i, n in grupos]
            elegido = elegir_de_lista(self.raiz, "Elegir grupo", "Grupos y canales de la sesión. Los marcados "
                                      "con ← ya los escucha otro bot de esta PC.", opciones)
            self.lbl_config.config(text="")
            if elegido:
                e = self.entradas["CHAT_ID_ORDENES"]
                e.delete(0, "end")
                e.insert(0, elegido[0])
                self.lbl_config.config(text="Grupo elegido: falta tocar Guardar.", fg=COLORES["aviso"])
        self.en_hilo(lambda: con_telegram(valores, _listar_grupos), listo)

    def elegir_remitentes(self):
        vistos = self.historial.remitentes_vistos()
        if not vistos:
            messagebox.showinfo("Remitentes", "Todavía no hay órdenes en el registro.\nEl ID aparece cuando "
                                "llega una orden: «Mensaje recibido de user_id=…».", parent=self.raiz)
            return
        actuales = set(str(i) for i in (ids_permitidos(self.entradas["USER_IDS_PERMITIDOS"].get()) or []))
        opciones = [(uid, f"{uid:>14}   {n} orden(es)") for uid, n in
                    sorted(vistos.items(), key=lambda x: -x[1])]
        elegidos = elegir_de_lista(self.raiz, "Remitentes permitidos",
                                   "Remitentes que mandaron órdenes (según el registro). Marca los que pueden "
                                   "dar órdenes de pago:", opciones, multiple=True, marcados=actuales)
        if elegidos is not None:
            otros = [a for a in actuales if a not in vistos]   # los que ya estaban y no aparecen en el registro
            e = self.entradas["USER_IDS_PERMITIDOS"]
            e.delete(0, "end")
            e.insert(0, ",".join(otros + elegidos))
            self.lbl_config.config(text="Remitentes elegidos: falta tocar Guardar.", fg=COLORES["aviso"])

    # ── Teléfonos: cada iPhone con su cuenta BDV y su clave ──────────────
    def _construir_telefonos(self):
        marco = tk.Frame(self.pestanas, padx=12, pady=10)
        self.pestanas.add(marco, text="Teléfonos")
        self._marco_tel = marco
        tk.Label(marco, justify="left", anchor="w", font=FUENTE, wraplength=1050, text=(
            "Cada iPhone con SU cuenta BDV y SU clave de operaciones. Al conectar uno registrado el bot lo "
            "reconoce solo (por su UDID) y paga con su cuenta y su clave, sin tocar Configuración ni reiniciar "
            "nada. Para sumar uno: conéctalo, desbloquéalo, deja la app BDV con la sesión iniciada y toca "
            "«Registrar iPhone conectado» (lee la cuenta del propio teléfono; solo pides nombre y clave)."
        )).pack(fill="x", pady=(0, 8))
        cols = ("nombre", "cuenta", "clave", "estado", "udid")
        self.tabla_tel = ttk.Treeview(marco, columns=cols, show="headings", height=11)
        for c, titulo, ancho in (("nombre", "Nombre", 180), ("cuenta", "Cuenta BDV", 130), ("clave", "Clave", 70),
                                 ("estado", "Estado", 230), ("udid", "UDID del iPhone", 300)):
            self.tabla_tel.heading(c, text=titulo)
            self.tabla_tel.column(c, width=ancho, anchor="w")
        for tag, color in (("conectado", "#e3f4e6"), ("falta", "#fdeaea")):
            self.tabla_tel.tag_configure(tag, background=color)
        self.tabla_tel.pack(fill="both", expand=True)
        botones = tk.Frame(marco)
        botones.pack(fill="x", pady=8)
        for texto, cmd in (("Registrar iPhone conectado…", self.t_registrar), ("Cambiar clave…", self.t_clave),
                           ("Cambiar nombre…", self.t_nombre), ("Eliminar", self.t_eliminar),
                           ("Actualizar", self.refrescar_telefonos)):
            tk.Button(botones, text=texto, command=cmd).pack(side="left", padx=4)
        self.lbl_tel = tk.Label(marco, font=FUENTE, justify="left", anchor="w", wraplength=1050)
        self.lbl_tel.pack(fill="x")
        self._conectados_tel: set[str] = set()
        self.refrescar_telefonos()
        self.raiz.after(15000, self._refresco_periodico_telefonos)

    def _refresco_periodico_telefonos(self):
        """Cada 15 s, solo si se está mirando la pestaña y el bot no está pagando (solo pregunta a usbmuxd)."""
        try:
            if (self.pestanas.select() == str(self._marco_tel) and not self.estado.get("orden")
                    and not getattr(self, "_tel_ocupado", False)):
                self.refrescar_telefonos()
        finally:
            self.raiz.after(15000, self._refresco_periodico_telefonos)

    def refrescar_telefonos(self):
        def trabajo():
            try:
                return {u.lower() for u in iphone_conectados()}
            except Exception:
                return None

        def listo(conectados, _err):
            if conectados is not None:
                self._conectados_tel = conectados
            self._llenar_telefonos()
        self.en_hilo(trabajo, listo)

    def _llenar_telefonos(self):
        seleccion = self.tabla_tel.selection()
        self.tabla_tel.delete(*self.tabla_tel.get_children())
        registrados = registro_iphones.cargar()
        for udid, d in registrados.items():
            faltan = registro_iphones.problemas(d)
            conectado = udid.lower() in self._conectados_tel
            estado = ("● CONECTADO" if conectado else "desconectado") + ("   ⚠ " + faltan[0] if faltan else "")
            self.tabla_tel.insert("", "end", iid=udid, tags=("conectado",) if conectado and not faltan else
                                  ("falta",) if faltan else (), values=(
                d.get("nombre", "?"), d.get("cuenta") or "(se lee al conectar)",
                "●●●●" if d.get("clave") else "FALTA", estado, udid))
        sin = [u for u in self._conectados_tel if u not in {k.lower() for k in registrados}]
        for u in sin:
            self.tabla_tel.insert("", "end", iid=u, tags=("falta",), values=(
                "(sin registrar)", "", "", "● CONECTADO — sin registrar", u))
        if seleccion and self.tabla_tel.exists(seleccion[0]):
            self.tabla_tel.selection_set(seleccion[0])
        self.lbl_tel.config(
            text=(f"{len(registrados)} iPhone registrado(s)." if registrados else "Todavía no hay iPhone registrados.")
            + (f"  ⚠ {len(sin)} conectado(s) sin registrar." if sin else ""),
            fg=COLORES["incierto"] if sin else "#555")

    def _udid_elegido(self) -> str | None:
        sel = self.tabla_tel.selection()
        return sel[0] if sel else None

    def t_registrar(self):
        if not self._exigir_apagado("Registrar iPhone"):
            return
        nombre = pedir_texto(self.raiz, "Registrar iPhone", "Nombre de este iPhone / su cuenta (ej. Ricardo):",
                             oculto=False)
        if not nombre or not nombre.strip():
            return
        clave = pedir_texto(self.raiz, "Registrar iPhone", f"Clave de operaciones BDV de «{nombre.strip()}»:")
        if not clave:
            return
        if "#" in clave:
            messagebox.showerror("Registrar iPhone", "La clave no puede llevar # (el .env lo toma como comentario).",
                                 parent=self.raiz)
            return
        self._tel_ocupado = True
        self.lbl_tel.config(text="Leyendo la cuenta del iPhone conectado… (~30 s; no lo toques)", fg="#555")

        def trabajo():
            codigo, salida = correr_bot_python([str(HERRAMIENTAS), "registrar", nombre.strip()], 240,
                                               extra_env={"REGISTRO_CLAVE": clave})
            return codigo, salida

        def listo(res, err):
            self._tel_ocupado = False
            if err:
                messagebox.showerror("Registrar iPhone", f"No se pudo: {err}", parent=self.raiz)
                return
            codigo, salida = res
            ultima = [l for l in salida.splitlines() if l.strip()][-1:] or [""]
            if codigo == 0:
                messagebox.showinfo("Registrar iPhone", ultima[0] + "\n\nYa se puede iniciar el bot con ese iPhone. "
                                    "No olvides desactivar Face ID en la app BDV.", parent=self.raiz)
            else:
                messagebox.showerror("Registrar iPhone", ultima[0] or "No se pudo registrar.", parent=self.raiz)
            self.refrescar_telefonos()
        self.en_hilo(trabajo, listo)

    def t_clave(self):
        udid = self._udid_elegido()
        if not udid or registro_iphones.buscar(udid) is None:
            messagebox.showinfo("Clave", "Elige primero un iPhone registrado de la lista.", parent=self.raiz)
            return
        reg = registro_iphones.buscar(udid)
        clave = pedir_texto(self.raiz, "Cambiar clave", f"Clave nueva de operaciones de «{reg['nombre']}»:")
        if not clave:
            return
        if "#" in clave:
            messagebox.showerror("Clave", "La clave no puede llevar #.", parent=self.raiz)
            return
        registro_iphones.guardar(udid, clave=clave)
        self._llenar_telefonos()
        messagebox.showinfo("Clave", "Clave guardada." + ("\nEl bot está encendido: tómala al reiniciarlo."
                            if self.estado.get("corriendo") else ""), parent=self.raiz)

    def t_nombre(self):
        udid = self._udid_elegido()
        if not udid or registro_iphones.buscar(udid) is None:
            messagebox.showinfo("Nombre", "Elige primero un iPhone registrado de la lista.", parent=self.raiz)
            return
        nombre = pedir_texto(self.raiz, "Cambiar nombre", "Nombre nuevo:", oculto=False)
        if nombre and nombre.strip():
            registro_iphones.guardar(udid, nombre=nombre.strip())
            self._llenar_telefonos()

    def t_eliminar(self):
        udid = self._udid_elegido()
        reg = registro_iphones.buscar(udid) if udid else None
        if reg is None:
            messagebox.showinfo("Eliminar", "Elige primero un iPhone registrado de la lista.", parent=self.raiz)
            return
        if messagebox.askyesno("Eliminar", f"¿Quitar «{reg['nombre']}» del registro?\n(Se borra su clave guardada.)",
                               parent=self.raiz):
            registro_iphones.quitar(udid)
            self._llenar_telefonos()

    # ── Herramientas ───────────────────────────────────────────────
    def _construir_herramientas(self):
        marco = tk.Frame(self.pestanas, padx=12, pady=10)
        self.pestanas.add(marco, text="Herramientas")
        grupos = (
            ("iPhone (solo con el bot apagado)", (
                ("Revisar iPhone (cambio de teléfono)", self.h_revisar),
                ("Volver la app a Inicio", self.h_inicio),
                ("Ver la pantalla del iPhone", self.h_pantalla),
                ("Probar el iPhone", self.h_probar_iphone),
                ("Montar imagen de desarrollador", self.h_montar),
            )),
            ("Telegram (solo con el bot apagado)", (
                ("Probar Telegram", self.h_probar_telegram),
            )),
            ("Archivos", (
                ("Respaldo ahora", self.h_respaldo),
                ("Abrir registro", lambda: self._abrir(ARCHIVO_LOG)),
                ("Abrir carpeta del bot", lambda: self._abrir(RAIZ)),
                ("Abrir guía", lambda: self._abrir(GUIA)),
                ("Cambiar clave del panel", self.cambiar_clave),
            )),
        )
        for fila, (titulo, botones) in enumerate(grupos):
            caja = tk.LabelFrame(marco, text=titulo, font=("Segoe UI", 10, "bold"), padx=8, pady=8)
            caja.grid(row=fila, column=0, sticky="ew", pady=4)
            for texto, cmd in botones:
                tk.Button(caja, text=texto, width=28, command=cmd).pack(side="left", padx=4)
        marco.columnconfigure(0, weight=1)
        self.salida_h = tk.Text(marco, font=("Consolas", 9), height=18, state="disabled", wrap="word")
        self.salida_h.grid(row=len(grupos), column=0, sticky="nsew", pady=(8, 0))
        marco.rowconfigure(len(grupos), weight=1)

    def _escribir_h(self, texto: str):
        self.salida_h.config(state="normal")
        self.salida_h.insert("end", f"[{time.strftime('%H:%M:%S')}] {texto}\n")
        self.salida_h.see("end")
        self.salida_h.config(state="disabled")

    def _exigir_apagado(self, que: str) -> bool:
        if self.estado.get("corriendo") or (self.proceso and self.proceso.poll() is None):
            messagebox.showinfo(que, "Apaga el bot primero: el iPhone admite una sola conexión a la vez "
                                "y la sesión de Telegram un solo programa.", parent=self.raiz)
            return False
        return True

    def _herramienta(self, que: str, trabajo):
        if not self._exigir_apagado(que):
            return
        self._escribir_h(f"{que}…")
        self._iphone_ocupado = True

        def listo(res, err):
            self._iphone_ocupado = False
            self._escribir_h(f"{que}: " + (f"ERROR: {err}" if err else str(res)))
        self.en_hilo(trabajo, listo)

    def h_revisar(self):
        """Lista de comprobación de un iPhone (nuevo o cambiado): reconoce el teléfono, monta la
        imagen de desarrollador, mira la sesión de la app BDV y la cuenta a debitar. No paga nada."""
        def trabajo():
            _codigo, salida = correr_bot_python([str(HERRAMIENTAS), "revisar"], 420)
            return "\n" + (salida or "(sin salida)")
        self._herramienta("Revisar iPhone", trabajo)

    def h_inicio(self):
        def trabajo():
            codigo, salida = correr_bot_python([str(HERRAMIENTAS), "inicio"], 150)
            if codigo != 0:
                raise RuntimeError(_explicar_error_iphone(salida))
            return salida.splitlines()[-1] if salida else "listo"
        self._herramienta("Volver a Inicio", trabajo)

    def h_pantalla(self):
        destino = CARPETA_CAPTURAS / f"panel_pantalla_{time.strftime('%Y%m%d_%H%M%S')}.png"

        def trabajo():
            CARPETA_CAPTURAS.mkdir(exist_ok=True)
            codigo, salida = correr_bot_python([str(HERRAMIENTAS), "pantalla", str(destino)], 90)
            if codigo != 0 or not destino.exists():
                raise RuntimeError(_explicar_error_iphone(salida))
            self.cola.put(lambda: self._abrir(destino))
            return str(destino)
        self._herramienta("Ver pantalla", trabajo)

    def h_probar_iphone(self):
        def trabajo():
            info = iphone_detalle()
            self.info_iphone = self._texto_iphone(info)
            if not info.get("conectado"):
                return "no hay ningún iPhone conectado por USB (o no está desbloqueado / no confía en esta PC)"
            return self.info_iphone.replace("\n", " · ")
        self._herramienta("Probar iPhone", trabajo)

    def h_montar(self):
        def trabajo():
            codigo, salida = correr_bot_python(["-m", "pymobiledevice3", "mounter", "auto-mount"], 120)
            if codigo != 0 and "already mounted" not in salida.lower():
                raise RuntimeError(_explicar_error_iphone(salida))
            return salida.splitlines()[-1] if salida else "listo"
        self._herramienta("Montar imagen de desarrollador", trabajo)

    def h_probar_telegram(self):
        valores = leer_env()

        async def probar(client):
            yo = await client.get_me()
            grupos = await _listar_grupos(client)
            return yo, grupos

        def trabajo():
            yo, grupos = con_telegram(valores, probar)
            self.grupos.update({str(i): n for i, n in grupos})
            guardar_grupos(self.grupos)
            chat = _entero(valor_efectivo(valores, "CHAT_ID_ORDENES"))
            nombre = dict(grupos).get(chat)
            texto = (f"sesión autorizada como {yo.first_name or ''} (@{yo.username or 'sin usuario'}), "
                     f"user_id={yo.id}. ")
            if not chat:
                texto += "Falta elegir el grupo de órdenes."
            elif nombre:
                texto += f"Grupo de órdenes {chat}: «{nombre}» visible."
            else:
                texto += f"OJO: el grupo {chat} NO aparece entre los grupos de esta cuenta."
            return texto
        if not self._exigir_apagado("Probar Telegram"):
            return
        self._escribir_h("Probar Telegram…")
        self.en_hilo(trabajo, lambda res, err: self._escribir_h(
            "Probar Telegram: " + (f"ERROR: {err}" if err else res)))

    def h_respaldo(self):
        self.en_hilo(hacer_respaldo, lambda res, err: self._escribir_h(
            f"Respaldo: ERROR: {err}" if err else f"Respaldo guardado en {res} (sin .env ni sesiones)."))

    def cambiar_clave(self):
        actual = pedir_texto(self.raiz, "Cambiar clave", "Clave actual:")
        if actual is None:
            return
        if not clave_correcta(actual):
            messagebox.showerror("Panel del bot", "Clave incorrecta.", parent=self.raiz)
            return
        nueva = pedir_texto(self.raiz, "Cambiar clave", "Clave nueva (mínimo 6):")
        if not nueva or len(nueva) < 6:
            messagebox.showerror("Panel del bot", "Muy corta: no se cambió.", parent=self.raiz)
            return
        if pedir_texto(self.raiz, "Cambiar clave", "Repite la clave nueva:") != nueva:
            messagebox.showerror("Panel del bot", "No coinciden: no se cambió.", parent=self.raiz)
            return
        guardar_clave(nueva)
        messagebox.showinfo("Panel del bot", "Clave cambiada.", parent=self.raiz)

    def _abrir(self, ruta: Path):
        try:
            plataforma.abrir(ruta)
        except OSError as exc:
            messagebox.showerror("Abrir", f"No se pudo abrir {ruta}:\n{exc}", parent=self.raiz)

    # ── ciclo de actualización ─────────────────────────────────────
    def tick(self):
        try:
            self._tick()
        finally:
            self.raiz.after(2000, self.tick)

    def _tick(self):
        self._quizas_recargar_config()
        self.valores = leer_env()
        est = estado_bot(self.sesion())
        self.estado = est
        corriendo = est["corriendo"]
        arrancando = not corriendo and self.proceso is not None and self.proceso.poll() is None

        lineas, reinicio = self.lector.nuevas()
        if reinicio:
            self.historial = Historial()
            self.registro.clear()
            self.txt.config(state="normal")
            self.txt.delete("1.0", "end")
            self.txt.config(state="disabled")
        cambio = self.historial.alimentar(lineas)
        self._agregar_registro(lineas[-3000:])
        clave_res = (cambio, est.get("orden"), time.strftime("%Y-%m-%d"))
        if cambio or clave_res != self._ultimo_resumen:
            self._refrescar_actividad()
            self._ultimo_resumen = clave_res

        # ¿Se detuvo sin que nadie lo pidiera? (no pudo iniciar, o murió solo)
        aviso = None
        termino = self.proceso is not None and self.proceso.poll() is not None
        if termino:
            self.proceso = None
        if not corriendo and not arrancando and (termino or self.estaba_corriendo):
            tramo = tramo_ultimo_arranque(ultimas_lineas())
            if not (self.apagado_pedido or apagado_fue_pedido(tramo)):
                aviso = ("El bot se detuvo solo o no pudo iniciar.\n\n" + motivo_ultimo_arranque(tramo)
                         + "\n\n(Detalle completo en la pestaña Registro.)")
            self.apagado_pedido = False
        self.estaba_corriendo = corriendo

        fase = est.get("fase")
        if fase == "apagando":
            self.apagando_desde = self.apagando_desde or time.monotonic()
        else:
            self.apagando_desde = None

        self._pintar_estado(est, arrancando)
        self._pintar_tarjetas(est)
        self._quizas_consultar_iphone(est)
        self._quizas_buscar_procesos(est)

        if aviso:   # al final: el cuadro bloquea y la siguiente vuelta ya quedó programada
            al_frente(self.raiz)
            self.raiz.attributes("-topmost", False)
            messagebox.showerror("Panel del bot", aviso, parent=self.raiz)

    def _pintar_estado(self, est: dict, arrancando: bool):
        fase = est.get("fase")
        if self.comprobando:
            texto, color = "●  Comprobando antes de iniciar…", "#b8860b"
        elif not est["corriendo"] and not arrancando:
            texto, color = "●  Apagado", "#8b0000"
        elif est.get("ajeno"):
            texto, color = "●  La sesión la usa otro programa", "#8b0000"
        elif arrancando or fase == "iniciando":
            texto, color = "●  Iniciando (esperando al iPhone / Telegram)…", "#b8860b"
        elif fase == "apagando":
            texto, color = "●  Apagándose (termina lo pendiente)…", "#b8860b"
        elif est.get("orden"):
            texto, color = f"●  Pagando la orden …{str(est['orden'])[-6:]}", "#1f4e9c"
        else:
            texto, color = "●  Escuchando órdenes", "#006400"
        self.lbl_estado.config(text=texto, fg=color)

        libre = not est["corriendo"] and not arrancando and not self.comprobando
        self.btn_iniciar.config(state="normal" if libre else "disabled")
        if est.get("ajeno") or (not est["corriendo"] and not arrancando):
            self.btn_detener.config(text="Detener", state="disabled")
        elif arrancando or fase == "iniciando":
            self.btn_detener.config(text="Cancelar arranque", state="normal")
        elif fase == "apagando":
            self.btn_detener.config(text="Cancelar apagado", state="normal")
        else:
            self.btn_detener.config(text="Detener", state="normal")
        tarda = self.apagando_desde is not None and time.monotonic() - self.apagando_desde > ESPERA_FORZAR_SEG
        if est["corriendo"] and not est.get("ajeno") and tarda:
            self.btn_forzar.pack(side="right", padx=4)
        else:
            self.btn_forzar.pack_forget()

    def _pintar_tarjetas(self, est: dict):
        # Bot
        if not est["corriendo"]:
            t = "Apagado."
        elif est.get("ajeno"):
            t = "La sesión de Telegram la usa otro programa (¿listar_chats.py, otro bot?)."
        else:
            fase = {"iniciando": "arrancando (esperando al iPhone o a Telegram; todavía no recibe órdenes)",
                    "escuchando": "escuchando el grupo", "apagando": "apagándose"}.get(est.get("fase"), est.get("fase"))
            t = f"Encendido: {fase}.\n"
            if est.get("orden"):
                t += f"Pagando la orden {est['orden']} (hace {int(time.time() - (est.get('desde') or time.time()))} s).\n"
            t += f"En cola: {est.get('en_cola', 0)}  ·  PID {est.get('pid')}"
        pendientes = len(cargar_pendientes())
        if pendientes:
            t += (f"\n⚠ {pendientes} resultado(s) sin enviar a Telegram: se mandan solos "
                  + ("cuando Telegram responda." if est["corriendo"] else "al encender el bot."))
        self.tarjetas["Bot"].config(text=t, fg=COLORES["fallo"] if pendientes else "black")
        # iPhone
        self.tarjetas["iPhone"].config(text=self.info_iphone)
        # Telegram
        chat = valor_efectivo(self.valores, "CHAT_ID_ORDENES")
        nombre = self.grupos.get(chat, "")
        sesion = Path(self.sesion() + ".session")
        t = f"Grupo: {nombre or '(nombre sin leer)'} {chat or '— sin elegir —'}\n"
        t += f"Sesión: {sesion.stem} " + ("✓" if sesion.exists() else "✗ no existe (login_telegram.py)") + "\n"
        if self.otros_bots:
            t += f"Otro bot de pagos corriendo en esta PC: {len(self.otros_bots)} proceso(s)."
        else:
            t += "Ningún otro bot de pagos corriendo."
        self.tarjetas["Telegram"].config(text=t)
        # Hoy
        r = self.historial.resumen()
        t = f"Órdenes: {r['ordenes']}   ✓ {r['exito']}   ✗ {r['fallo']}   ? {r['incierto']}\n"
        t += f"Bs pagados: {bs(r['bs'])}"
        if r["dobles"]:
            t += f"\n⚠ {r['dobles']} con posible pago doble"
        self.tarjetas["Hoy"].config(text=t, fg=COLORES["fallo"] if r["dobles"] or r["incierto"] else "black")

    @staticmethod
    def _texto_iphone(info: dict) -> str:
        if not info.get("conectado"):
            return "No se ve ningún iPhone por USB."
        t = f"Conectado: {info.get('nombre')} (iOS {info.get('ios')})\n"
        if info.get("bateria") is not None:
            t += f"Batería: {info['bateria']} %" + (" (cargando)" if info.get("cargando") else "") + "\n"
        if "desarrollador" in info:
            t += "Modo desarrollador: " + ("sí" if info["desarrollador"] else "NO (guía, paso 6)") + "\n"
        if info.get("varios"):
            t += "Hay más de un iPhone: pon su UDID en Configuración."
        return t.strip()

    def _quizas_consultar_iphone(self, est: dict):
        """Con el bot apagado: detalle cada 30 s. Con el bot encendido: solo "¿está conectado?"
        (sin abrir conexión con el iPhone) cada 20 s, y nunca mientras paga o tiene cola."""
        if self._iphone_ocupado:
            return
        ahora = time.monotonic()
        if est.get("orden") or est.get("en_cola"):
            return
        apagado = not est["corriendo"] and not (self.proceso and self.proceso.poll() is None)
        if ahora - self._ultimo_iphone < (30 if apagado else 20):
            return
        self._ultimo_iphone = ahora
        self._iphone_ocupado = True

        def trabajo():
            if apagado:
                return self._texto_iphone(iphone_detalle())
            udids = iphone_conectados()
            if not udids:
                return "⚠ No se ve el iPhone por USB."
            operando = est.get("iphone")
            return ((f"Operando con: {operando}\n" if operando else "")
                    + (f"⚠ Hay {len(udids)} iPhone conectados: el bot usa solo el que eligió al iniciar.\n"
                       if len(udids) > 1 else "")
                    + "Con el bot encendido solo se comprueba la conexión (el bot usa el iPhone).")

        def listo(res, err):
            self._iphone_ocupado = False
            self.info_iphone = f"⚠ {err}" if err else res
        self.en_hilo(trabajo, listo)

    def _quizas_buscar_procesos(self, est: dict):
        if self._procesos_ocupado or time.monotonic() - self._ultimo_procesos < 15:
            return
        self._ultimo_procesos = time.monotonic()
        self._procesos_ocupado = True
        propio = est.get("pid")

        def trabajo():
            todos = procesos_python()
            excluir = set()
            if propio:
                excluir.add(propio)
                excluir |= {p.get("ParentProcessId") for p in todos if p.get("ProcessId") == propio}
            if self.proceso is not None:
                excluir.add(self.proceso.pid)
                excluir |= {p.get("ProcessId") for p in todos if p.get("ParentProcessId") == self.proceso.pid}
            return [p for p in todos if re.search(r"orquestador\.py|lanzador\.py", p.get("CommandLine") or "", re.I)
                    and p.get("ProcessId") not in excluir]

        def listo(res, err):
            self._procesos_ocupado = False
            if not err:
                self.otros_bots = res
        self.en_hilo(trabajo, listo)

    # ── botones ────────────────────────────────────────────────────
    def iniciar(self):
        if self.estado.get("corriendo") or (self.proceso and self.proceso.poll() is None) or self.comprobando:
            return
        self.comprobando = True
        self.btn_iniciar.config(state="disabled")
        self.lbl_estado.config(text="●  Comprobando antes de iniciar…", fg="#b8860b")
        valores = leer_env()

        def comprobar():
            bloqueos, preguntas = [], []
            errores, avisos = validar(valores)
            if errores:
                bloqueos.append("Configuración incompleta (pestaña Configuración):\n• " + "\n• ".join(errores))
            sesion = ruta_sesion(valores)
            if not Path(sesion + ".session").exists():
                bloqueos.append(f"No existe la sesión de Telegram {Path(sesion).name}.session.\nInicia sesión "
                                "una vez (pide el código que llega a Telegram):\n"
                                "iphone/.venv/bin/python login_telegram.py   (Windows: iphone\\.venv\\Scripts\\python.exe login_telegram.py)")
            if instancia_unica.en_uso(sesion):
                bloqueos.append("La sesión de Telegram ya la usa otro programa (el bot abierto desde la "
                                "consola, listar_chats.py…). Ciérralo primero.")
            otros = procesos_bot()
            b, p = conflictos(valores, otras_configs(), hay_otros_bots=bool(otros))
            bloqueos += b
            preguntas += p
            if otros:
                preguntas.append("Hay otro bot de pagos corriendo en esta PC:\n" + "\n".join(
                    f"  PID {o.get('ProcessId')}: {(o.get('CommandLine') or '')[:110]}" for o in otros[:4])
                    + "\nNo debe usar la misma sesión ni el mismo grupo.")
            try:
                udids = iphone_conectados()
                udid = valores.get("SERIAL_ADB", "").strip()
                if not udids:
                    bloqueos.append("No se ve el iPhone por USB: conéctalo, desbloquéalo y toca «Confiar».")
                elif udid and udid not in udids and len(udids) > 1:
                    bloqueos.append(f"El iPhone {udid} (SERIAL_ADB) no está conectado y hay {len(udids)} conectados: "
                                    "desconecta el que no vas a usar.")
                elif len(udids) > 1 and not udid:
                    bloqueos.append("Hay más de un iPhone conectado: desconecta el que no vas a usar "
                                    "(o pon su UDID en Configuración).")
                else:
                    actual = udid if udid in udids else udids[0]
                    if registro_iphones.buscar(actual) is None:
                        preguntas.append(f"El iPhone conectado ({actual}) NO está registrado: el bot usará la cuenta "
                                         "y la clave del .env y se detendrá si la cuenta del iPhone es otra. "
                                         "Regístralo en la pestaña Teléfonos.")
            except Exception as exc:
                bloqueos.append(f"El iPhone no está listo: {exc}")
            preguntas += avisos
            return bloqueos, preguntas

        def listo(res, err):
            self.comprobando = False
            if err:
                messagebox.showerror("Iniciar", f"No se pudo comprobar: {err}", parent=self.raiz)
                return
            bloqueos, preguntas = res
            if bloqueos:
                messagebox.showerror("No se puede iniciar", "\n\n".join(bloqueos), parent=self.raiz)
                if any("Configuración" in b for b in bloqueos):
                    self.pestanas.select(2)
                return
            if preguntas and not messagebox.askyesno(
                    "Iniciar el bot", "\n\n".join(preguntas) + "\n\n¿Iniciar igual?", icon="warning", parent=self.raiz):
                return
            if self.estado.get("corriendo") or instancia_unica.en_uso(ruta_sesion(leer_env())):
                messagebox.showwarning("Iniciar", "El bot ya está corriendo.", parent=self.raiz)
                return
            self.apagado_pedido = False
            self.proceso = iniciar_bot()
            self.estaba_corriendo = False
            self.btn_iniciar.config(state="disabled")
            self.lbl_estado.config(text="●  Iniciando…", fg="#b8860b")
            self.pestanas.select(0)
        self.en_hilo(comprobar, listo)

    def detener(self):
        est = estado_bot(self.sesion())
        arrancando = not est["corriendo"] and self.proceso is not None and self.proceso.poll() is None
        if arrancando or est.get("fase") == "iniciando":
            if not messagebox.askyesno(
                    "Cancelar arranque", "El bot todavía está arrancando (esperando al iPhone o a Telegram) y no está "
                    "conectado a Telegram: no hay ningún pago en curso.\n\n¿Cancelar el arranque?", parent=self.raiz):
                return
            self.apagado_pedido = True
            if self.proceso is not None and self.proceso.poll() is None:
                # Lo lanzó este panel: se corta su árbol (lanzador del .venv + python del bot).
                plataforma.matar_arbol(self.proceso.pid)
            else:   # arrancado desde la consola: solo si su línea de comando es de verdad el bot
                self.en_hilo(lambda: forzar_apagado(est.get("pid")),
                             lambda res, err: self._escribir_h(f"Cancelar arranque: {err or res}"))
            return
        if est.get("fase") == "apagando":
            if messagebox.askyesno("Cancelar apagado", "El bot se está apagando (termina lo pendiente).\n\n"
                                   "¿Cancelar el apagado y dejarlo recibiendo órdenes?", parent=self.raiz):
                control_bot.cancelar_parada()
                self.apagado_pedido = False
            return
        if not est["corriendo"] or est.get("ajeno"):
            return
        pendiente = ""
        if est.get("orden") or est.get("en_cola"):
            pendiente = (f"\nAhora está pagando {'una orden' if est.get('orden') else 'nada'} y tiene "
                         f"{est.get('en_cola', 0)} en cola: las termina antes de cerrarse.\n")
        if not messagebox.askyesno(
                "Detener el bot",
                "Apagado ORDENADO: el bot deja de aceptar órdenes (contesta que se está apagando), termina "
                "la que está pagando y las de la cola, vuelve el iPhone a Inicio y se cierra solo.\n"
                + pendiente + "\nNunca se corta un pago. ¿Detener?", parent=self.raiz):
            return
        control_bot.pedir_parada()
        self.apagado_pedido = True

    def forzar(self):
        if not messagebox.askyesno(
                "Forzar apagado",
                "Esto corta el bot AL INSTANTE. Si estaba en medio de un pago, esa orden queda INCIERTA y "
                "hay que revisarla en el banco a mano.\n\n¿Forzar?", icon="warning", parent=self.raiz):
            return
        pid = self.estado.get("pid")
        self.apagado_pedido = True
        self.en_hilo(lambda: forzar_apagado(pid),
                     lambda res, err: messagebox.showinfo("Panel del bot", str(err or res), parent=self.raiz))

    def cerrar(self):
        if self.estado.get("corriendo") or (self.proceso and self.proceso.poll() is None):
            messagebox.showinfo("Panel del bot", "El panel se cierra pero el bot SIGUE corriendo.\n"
                                "Para apagarlo, abre el panel otra vez y usa Detener.", parent=self.raiz)
        self.raiz.destroy()


def main():
    plataforma.nitidez_dpi()   # ventana nítida en pantallas con escala (solo Windows)
    candado_panel = instancia_unica._abrir(NOMBRE_INSTANCIA_PANEL)   # un solo panel abierto a la vez
    raiz = tk.Tk()
    raiz.withdraw()
    if candado_panel is None:
        raiz.attributes("-topmost", True)
        messagebox.showinfo("Panel del bot", "El panel ya está abierto (búscalo en la barra de tareas).", parent=raiz)
        raiz.destroy()
        return
    copiar_clave_anterior()
    raiz.attributes("-topmost", True)
    if not pedir_acceso(raiz):
        raiz.destroy()
        return
    raiz.attributes("-topmost", False)
    icono = RAIZ / "recursos" / "panel.ico"
    if icono.exists():
        try:
            raiz.iconbitmap(default=str(icono))
        except tk.TclError:
            pass
    raiz.deiconify()
    Panel(raiz)
    al_frente(raiz)
    raiz.attributes("-topmost", False)
    raiz.mainloop()
    candado_panel.close()


if __name__ == "__main__":
    main()
