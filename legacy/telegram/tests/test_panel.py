"""Prueba SIN ventanas, teléfono ni Telegram de la lógica del panel (panel_bot.pyw). Debe terminar
en "TODO OK".

    iphone\\.venv\\Scripts\\python.exe tests\\test_panel.py

Trabaja en carpetas temporales: no toca el .env, el registro, la clave ni el estado reales.
"""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
_cargador = importlib.machinery.SourceFileLoader("panel_bot", str(RAIZ / "panel_bot.pyw"))
_spec = importlib.util.spec_from_loader("panel_bot", _cargador)
panel = importlib.util.module_from_spec(_spec)
_cargador.exec_module(panel)

import control_bot  # noqa: E402
import instancia_unica  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="test_panel_"))
import registro_iphones  # noqa: E402
registro_iphones.ARCHIVO = tmp / "iphones.json"       # sin iPhone registrados: nunca el iphones.json real
HOY = time.strftime("%Y-%m-%d")

# ── 1) .env: solo cambian las claves editadas, comentarios y orden intactos, .env.bak ──────────
env = tmp / ".env"
env.write_text("# comentario de arriba\nAPI_ID=123\n\n# grupo\nCHAT_ID_ORDENES=-100\n"
               "CHAT_ID_ORDENES=-999\nBDV_PASSWORD=vieja\nCHAT_ID_ORDENES_IPHONE=\n", encoding="utf-8")
v = panel.leer_env(env)
assert v["CHAT_ID_ORDENES"] == "-100", v                      # la primera aparición manda (como config.py)
assert panel.escribir_env({"CHAT_ID_ORDENES": "-200", "BDV_PASSWORD": "Ñandú1", "NUEVA": "x"}, env)
texto = env.read_text(encoding="utf-8")
assert "# comentario de arriba" in texto and "# grupo" in texto
assert "CHAT_ID_ORDENES=-200\nCHAT_ID_ORDENES=-999" in texto, texto     # solo la primera se cambia
assert texto.strip().endswith("NUEVA=x")
assert (tmp / ".env.bak").read_text(encoding="utf-8").count("BDV_PASSWORD=vieja") == 1
assert not (tmp / ".env.tmp").exists()
assert panel.leer_env(env)["BDV_PASSWORD"] == "Ñandú1"
assert panel.escribir_env({"CHAT_ID_ORDENES": "-200"}, env) is False    # sin cambios: no reescribe
for malo in ("con#numeral", "dos\nlineas"):
    try:
        panel.escribir_env({"BDV_PASSWORD": malo}, env)
        raise AssertionError("debió rechazar " + repr(malo))
    except ValueError:
        pass
print("1 ok  .env: cambia solo lo editado, conserva comentarios, .env.bak, rechaza # y saltos")

# ── 2) valores *_IPHONE: el panel edita la clave que de verdad usa el bot del iPhone ─────────────
assert panel.clave_destino({"CHAT_ID_ORDENES": "-1", "CHAT_ID_ORDENES_IPHONE": ""}, "CHAT_ID_ORDENES") == "CHAT_ID_ORDENES"
assert panel.clave_destino({"CHAT_ID_ORDENES_IPHONE": "-5"}, "CHAT_ID_ORDENES") == "CHAT_ID_ORDENES_IPHONE"
assert panel.valor_efectivo({"MONTO_MAXIMO_AUTO": "100", "MONTO_MAXIMO_AUTO_IPHONE": "30"}, "MONTO_MAXIMO_AUTO") == "30"
assert panel.clave_destino({"BDV_PASSWORD_IPHONE": "x"}, "BDV_PASSWORD") == "BDV_PASSWORD"
print("2 ok  claves *_IPHONE")

# ── 3) validaciones ─────────────────────────────────────────────────────────────────────────
errores, _ = panel.validar({})
assert len(errores) >= 6, errores
bueno = {"API_ID": "12345", "API_HASH": "abc", "TELEFONO": "+584141234567", "CUENTA_DEBITAR_DESC": "0102****1234",
         "BDV_PASSWORD": "x", "CHAT_ID_ORDENES": "-1001", "MONTO_MAXIMO_AUTO": "100", "USER_IDS_PERMITIDOS": "1,2"}
assert panel.validar(bueno) == ([], []), panel.validar(bueno)
e, a = panel.validar(dict(bueno, USER_IDS_PERMITIDOS="", MONTO_MAXIMO_AUTO="0"))
assert not e and len(a) == 2, (e, a)
e, _ = panel.validar(dict(bueno, USER_IDS_PERMITIDOS="1,abc", MONTO_MAXIMO_AUTO="1.000,5", API_ID="x"))
assert len(e) == 3, e
print("3 ok  validaciones (errores y avisos de seguridad)")

# ── 4) historial: resultados, duplicada, rechazada, reenvío pagado dos veces, resumen del día ───
orquestador_src = (RAIZ / "orquestador.py").read_text(encoding="utf-8")
FORMATOS = {
    "recibido": "Mensaje recibido de user_id=%s tipo=%s banco=%r texto=%r",
    "encolada": "Orden %s encolada. Pendientes: %s",
    "duplicada": "Orden %s duplicada, ignorada (sin aviso en el chat).",
    "no_aut": "Orden %s de remitente no autorizado (%s): ignorada sin responder.",
    "ejecutada": "Orden %s ejecutada en %.1f s: %s",
    "detalle": "Orden %s detalle: %s",
    "rechazada": "Orden %s rechazada: %s",
    "sin_enviar": "Orden %s: resultado NO enviado a Telegram (%s). Queda guardado y se reintenta",
    "tarde": "Orden %s: resultado enviado a Telegram con retraso (pendiente desde %s).",
    "fallida_banco": "Orden %s: el banco informó que la transferencia FALLÓ (operación %s).",
}
fuentes = orquestador_src + (RAIZ / "envios_pendientes.py").read_text(encoding="utf-8")
for nombre, formato in FORMATOS.items():   # si el bot cambia un texto, el panel dejaría de verlo
    assert formato in fuentes, f"el bot ya no escribe: {formato}"


def orden(n: int, medio: str, monto: str, nombre="PERSONA PRUEBA") -> str:
    return (f"00229358806646138666{n:02d}\n0000000000000000946.735\n------------------\n🏦 Venezuela\n"
            f"🆔 31899383\n🔢 {medio}\n👤 {nombre}\n{monto}")


def linea(hora: str, msg: str, dia: str = HOY, nivel: str = "INFO") -> str:
    return f"{dia} {hora},123 {nivel} {msg}"


def recibido(hora, n, medio, monto, uid=777, dia=HOY):
    from ordenes import parsear_orden
    o = parsear_orden(orden(n, medio, monto), uid, 1)
    return linea(hora, FORMATOS["recibido"] % (uid, o.tipo, o.banco, orden(n, medio, monto)), dia), o.id_orden


log = [f"===== Inicio {HOY} 08:00:00 (panel) ====="]
l, A = recibido("08:01:00", 1, "04243468273", "100,00")
log += [l, linea("08:01:00", FORMATOS["encolada"] % (A, 1)), linea("08:01:21", FORMATOS["ejecutada"] % (A, 20.6, "ÉXITO")),
        linea("08:01:21", FORMATOS["detalle"] % (A, "referencia 123456"))]
l, B = recibido("08:02:00", 2, "01050754150000056672", "2.000,50")
log += [l, linea("08:02:00", FORMATOS["encolada"] % (B, 1)), linea("08:02:30", FORMATOS["ejecutada"] % (B, 29.0, "FALLÓ")),
        linea("08:02:30", FORMATOS["detalle"] % (B, "Banco destino inactivo"))]
l, C = recibido("08:03:00", 3, "01020754150000056672", "50,00")
log += [l, linea("08:03:00", FORMATOS["encolada"] % (C, 1)), linea("08:03:40", FORMATOS["ejecutada"] % (C, 40.2, "INCIERTO"))]
l, _ = recibido("08:04:00", 1, "04243468273", "100,00")                      # misma orden A, mismo arranque
log += [l, linea("08:04:00", FORMATOS["duplicada"] % A)]
l, E = recibido("08:05:00", 5, "04243468273", "9.999,00")
log += [l, linea("08:05:00", FORMATOS["rechazada"] % (E, "monto 9999.0 supera el tope automático (100.0)"))]
l, F = recibido("08:06:00", 6, "04243468273", "10,00", uid=555)
log += [l, linea("08:06:00", FORMATOS["no_aut"] % (F, 555))]
log += [linea("08:07:00", FORMATOS["recibido"] % (777, None, None, "hola, no es una orden")),
        "08:07:01  -> TAP (585, 540) campo Monto:"]
log += [f"===== Inicio {HOY} 09:00:00 (consola) ====="]                    # reinicio: el bot olvida los IDs
l, _ = recibido("09:01:00", 1, "04243468273", "100,00")                      # A reenviada -> se paga otra vez
log += [l, linea("09:01:00", FORMATOS["encolada"] % (A, 1)), linea("09:01:20", FORMATOS["ejecutada"] % (A, 19.9, "ÉXITO"))]
l, H = recibido("09:37:19", 8, "01050762611762158388", "6.300,00")          # pagada con Telegram caído
log += [l, linea("09:37:19", FORMATOS["encolada"] % (H, 1)), linea("09:37:52", FORMATOS["ejecutada"] % (H, 33.7, "ÉXITO")),
        linea("09:38:17", FORMATOS["sin_enviar"] % (H, "TimeoutError"), nivel="WARNING"),
        linea("09:39:19", f"Error inesperado procesando la orden {H}", nivel="ERROR")]
l, G = recibido("23:00:00", 7, "04243468273", "1,00", dia="2026-01-01")      # otro día: no entra en "Hoy"
log += [l, linea("23:00:20", FORMATOS["ejecutada"] % (G, 20.0, "ÉXITO"), dia="2026-01-01")]

h = panel.Historial()
assert h.alimentar(log)
por = {}
for f in h.filas:
    por.setdefault(f.id_orden, []).append(f)
assert [f.resultado for f in por[A]] == ["ÉXITO", "duplicada", "ÉXITO"], [f.resultado for f in por[A]]
assert por[A][0].doble == por[A][2].doble == "PAGADA 2 VECES" and por[A][1].doble == ""
assert por[A][0].detalle == "referencia 123456" and por[A][0].segundos == 20.6
assert por[A][0].tipo == "Pago Móvil" and por[A][0].monto_num == 100.0 and por[A][0].beneficiario == "PERSONA PRUEBA"
assert por[B][0].tipo == "Otros bancos" and por[B][0].resultado == "FALLÓ" and "inactivo" in por[B][0].detalle
assert por[C][0].tipo == "Terceros BDV" and por[C][0].resultado == "INCIERTO"
assert por[E][0].resultado == "rechazada" and "tope" in por[E][0].detalle
assert por[F][0].resultado == "no autorizada"
assert len(h.filas) == 9, len(h.filas)                                       # el mensaje que no es orden no entra
assert por[H][0].resultado == "ÉXITO" and por[H][0].envio == "sin enviar a Telegram"   # el error de Telegram no la vuelve INCIERTA
h.alimentar([linea("09:49:00", FORMATOS["tarde"] % (H, "09:38:17"))])
assert por[H][0].envio == "enviado con retraso"
# interbancaria con comprobante (ÉXITO) que el banco marcó FALLIDA después (2026-10-26 21:55)
l, K = recibido("21:54:54", 9, "04140000000", "3.910,90")
h.alimentar([l, linea("21:55:27", FORMATOS["ejecutada"] % (K, 33.6, "ÉXITO")),
             linea("21:55:40", FORMATOS["fallida_banco"] % (K, "3701130454863"), nivel="WARNING")])
assert h._ultima[K].resultado == "FALLÓ" and "FALLIDA" in h._ultima[K].detalle, h._ultima[K]
h.filas.remove(h._ultima[K])
del h._ultima[K]
r = h.resumen()
assert r == {"ordenes": 5, "exito": 3, "fallo": 2, "incierto": 1, "bs": 6500.0, "dobles": 2}, r
assert h.resumen("2026-01-01")["exito"] == 1
assert h.remitentes_vistos() == {"777": 8, "555": 1}, h.remitentes_vistos()
assert panel.bs(346848.31) == "346.848,31"
print("4 ok  historial: ✓ ✗ ? duplicada, rechazada, no autorizada, PAGADA 2 VECES por reenvío, resumen del día")

# ── 5) lectura incremental del registro (líneas a medias, archivado del registro) ──────────────
ruta_log = tmp / "logs" / "bot.log"
ruta_log.parent.mkdir()
ruta_log.write_text("\n".join(log[:5]) + "\n", encoding="utf-8")
lector = panel.LectorLog(ruta_log)
h2 = panel.Historial()
h2.alimentar(lector.inicial())
assert [f.resultado for f in h2.filas] == ["ÉXITO"]
with open(ruta_log, "a", encoding="utf-8") as f:
    f.write(log[5] + "\n" + log[6][:20])                                     # una línea entera y media
nuevas, reinicio = lector.nuevas()
assert nuevas == [log[5]] and not reinicio, nuevas
with open(ruta_log, "a", encoding="utf-8") as f:
    f.write(log[6][20:] + "\n")
nuevas, _ = lector.nuevas()
assert nuevas == [log[6]], nuevas
assert lector.nuevas() == ([], False)
ruta_log.replace(ruta_log.with_name("bot.log.1"))                            # el bot archivó el registro al arrancar
ruta_log.write_text(log[0] + "\n", encoding="utf-8")
nuevas, reinicio = lector.nuevas()
assert reinicio and nuevas[-1] == log[0] and len(nuevas) == 8, (reinicio, len(nuevas))
print("5 ok  lectura incremental: solo líneas completas, detecta el registro archivado")

# ── 6) captura de cada orden ───────────────────────────────────────────────────────────────
capt = tmp / "capturas"
capt.mkdir()
f = por[A][0]
(capt / f"{A}_{int(f.epoch) - 600}.png").write_bytes(b"x")                  # de una corrida anterior
(capt / f"{A}_{int(f.epoch) + 19}.png").write_bytes(b"x")
(capt / f"{A}_{int(por[A][2].epoch) + 18}.png").write_bytes(b"x")
assert panel.captura_de(f, capt).name == f"{A}_{int(f.epoch) + 19}.png"
assert panel.captura_de(por[A][2], capt).name == f"{A}_{int(por[A][2].epoch) + 18}.png"
assert panel.captura_de(por[B][0], capt) is None
print("6 ok  doble clic: la captura que corresponde a cada pago")

# ── 7) clave del panel: hash y copia de la del panel anterior ─────────────────────────────────
clave = tmp / "panel_clave.json"
panel.guardar_clave("secreto1", clave)
datos = json.loads(clave.read_text())
assert "secreto1" not in clave.read_text() and set(datos) == {"sal", "iteraciones", "hash"}
assert panel.clave_correcta("secreto1", clave) and not panel.clave_correcta("secreto2", clave)
pc = tmp / "pc"
(pc / "Bot BlueStacks").mkdir(parents=True)
(pc / "Bot iPhone").mkdir()
shutil.copy2(clave, pc / "Bot BlueStacks" / "panel_clave.json")
destino = pc / "Bot iPhone" / "panel_clave.json"
assert panel.copiar_clave_anterior(destino, pc / "Bot iPhone").parent.name == "Bot BlueStacks"
assert panel.clave_correcta("secreto1", destino)
assert panel.copiar_clave_anterior(destino, pc / "Bot iPhone") is None        # ya existe: no se pisa
print("7 ok  clave como hash; reutiliza la del panel anterior de la PC")

# ── 8) otros bots de la PC: misma sesión / mismo grupo ─────────────────────────────────────────
(pc / "Bot BlueStacks" / ".env").write_text("SESSION_NAME=sesion_bs\nCHAT_ID_ORDENES=-300\nAPI_HASH=no-se-lee\n")
(pc / "Bot BlueStacks" / ".env.B").write_text("CHAT_ID_ORDENES=-400\n")
(pc / "Bot BlueStacks" / ".env.example").write_text("CHAT_ID_ORDENES=-999\n")
(pc / "Bot BlueStacks" / ".env.B.bak").write_text("CHAT_ID_ORDENES=-998\n")
(pc / "Bot iPhone" / ".env").write_text("CHAT_ID_ORDENES=-300\n")              # la propia carpeta no cuenta
configs = panel.otras_configs(pc / "Bot iPhone")
assert sorted(c["grupo"] for c in configs) == [-400, -300], configs
assert all(c["sesion"].endswith("sesion_bs") for c in configs)
mio = {"SESSION_NAME": "sesion_iphone", "CHAT_ID_ORDENES": "-300"}
b, p = panel.conflictos(mio, configs, hay_otros_bots=True)
assert len(b) == 1 and not p and "DOS VECES" in b[0]
b, p = panel.conflictos(mio, configs, hay_otros_bots=False)
assert not b and len(p) == 1
mio = {"SESSION_NAME": str(pc / "Bot BlueStacks" / "sesion_bs"), "CHAT_ID_ORDENES": "-500"}
b, _ = panel.conflictos(mio, configs, hay_otros_bots=True)
assert len(b) == 2 and "misma" in b[0]                                        # misma sesión (en .env y .env.B)
print("8 ok  avisa/bloquea si otro bot de la PC usa la misma sesión o el mismo grupo")

# ── 9) estado del bot: apagado, encendido, sesión usada por otro programa ──────────────────────
control_bot.ARCHIVO_ESTADO = tmp / "estado_bot.json"
sesion = str(tmp / "sesion_prueba_panel")
assert panel.estado_bot(sesion) == {"corriendo": False}
s = instancia_unica._abrir(sesion)
assert panel.estado_bot(sesion) == {"corriendo": True, "ajeno": True}
e = control_bot.EstadoBot()
e.fase, e.orden, e.en_cola = "escuchando", "999", 2
e.publicar()
est = panel.estado_bot(sesion)
assert est["corriendo"] and not est["ajeno"] and est["orden"] == "999" and est["en_cola"] == 2, est
s.close()
assert panel.estado_bot(sesion) == {"corriendo": False}
print("9 ok  estado: apagado / pagando con cola / sesión ajena")

# ── 10) Forzar solo mata procesos que son de verdad el bot ─────────────────────────────────────
ajeno = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
try:
    assert "no es el bot" in panel.forzar_apagado(ajeno.pid)
    assert ajeno.poll() is None
    assert "no se encontró" in panel.forzar_apagado(None)
finally:
    ajeno.kill()
print("10 ok forzar apagado no toca procesos que no son el bot")

# ── 11) motivo de "se detuvo solo" y apagado pedido desde bot_iphone.bat ──────────────────────
tramo = panel.tramo_ultimo_arranque(
    f"vieja\n===== Inicio {HOY} 10:00:00 (panel) =====\n{HOY} 10:00:01,1 INFO Dispositivo: iphone.\n"
    "Traceback (most recent call last):\n  File x\nRuntimeError: falta la variable API_ID en .env o el entorno\n")
assert "vieja" not in tramo
motivo = panel.motivo_ultimo_arranque(tramo)
assert "falta la variable API_ID" in motivo and "Traceback" in motivo, motivo
assert not panel.apagado_fue_pedido(tramo)
assert panel.apagado_fue_pedido("x\n2026 INFO Apagado pedido: no se aceptan órdenes nuevas\n")
assert "Ya hay otro programa" in panel.motivo_ultimo_arranque("\nYa hay otro programa usando la sesión 'x'.\n")
print("11 ok motivo del cuadro 'se detuvo solo'; un apagado pedido no da falsa alarma")

# ── 12) registro en colores y filtro ─────────────────────────────────────────────────────────
assert panel.clasificar_linea(log[3]) == ("exito", True)
assert panel.clasificar_linea(log[7]) == ("fallo", True)
assert panel.clasificar_linea(log[11])[0] == "incierto"
assert panel.clasificar_linea("08:07:01  -> TAP (585, 540) campo Monto:") == ("toque", False)
assert panel.clasificar_linea(log[0]) == ("marca", True)
assert panel.clasificar_linea(f"{HOY} 1,1 WARNING USER_IDS_PERMITIDOS está vacío")[0] == "aviso"
print("12 ok registro: colores y 'Solo lo importante'")

# ── 13) el bot lanzado desde el panel lee el .env RECIÉN guardado ──────────────────────────────
falso = tmp / "bot_falso"
falso.mkdir()
shutil.copy2(RAIZ / "config.py", falso / "config.py")
env_falso = falso / ".env"
env_falso.write_text("CHAT_ID_ORDENES=-111\n", encoding="utf-8")
os.environ["CHAT_ID_ORDENES"] = "-111"                     # lo que config.py cargó al abrir el panel
panel.CLAVES_CARGADAS_DEL_ENV.add("CHAT_ID_ORDENES")
panel.escribir_env({"CHAT_ID_ORDENES": "-222", "CUENTA_DEBITAR_DESC": "0102****9876 Ñ"}, env_falso)
salida = subprocess.run([sys.executable, "-c", "import config; print(config.opcional('CHAT_ID_ORDENES'), "
                         "config.opcional('CUENTA_DEBITAR_DESC'), config.opcional('DISPOSITIVO'))"],
                        cwd=str(falso), env=panel.entorno_hijo(ruta_env=env_falso), capture_output=True,
                        text=True, encoding="utf-8").stdout.strip()
assert salida == "-222 0102****9876 Ñ iphone", salida
print("13 ok el bot lanzado desde el panel lee el .env recién guardado (con tildes)")

# ── 14) respaldo: nunca credenciales, sesiones, capturas ni el entorno de Python ────────────────
P = Path
for incluido in ("orquestador.py", "panel_bot.pyw", ".env.example", "iphone/iphone_utils.py",
                 "iphone/plantillas/tipo_doc_V.png", "recursos/panel.ico", "tests/test_panel.py"):
    assert panel.incluir_en_respaldo(P(incluido)), incluido
for excluido in (".env", ".env.bak", ".env.tmp", "userbot_bdv.session", "userbot_bdv.session-journal",
                 "panel_clave.json", "panel_grupos.json", "estado_bot.json", "capturas/1_2.png",
                 "logs/bot.log", "iphone/.venv/Scripts/python.exe", "respaldos/x.zip", "__pycache__/a.pyc"):
    assert not panel.incluir_en_respaldo(P(excluido)), excluido
print("14 ok respaldo sin .env, sesiones, clave, capturas ni registro")

shutil.rmtree(tmp, ignore_errors=True)
print("\nTODO OK")
