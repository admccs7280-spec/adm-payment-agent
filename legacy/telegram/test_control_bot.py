"""Prueba SIN Telegram ni teléfono de control_bot.py (iniciar/apagar). Debe terminar en "TODO OK".

    iphone\\.venv\\Scripts\\python.exe test_control_bot.py

Usa una sesión inventada y una carpeta temporal: no toca el estado ni la sesión reales.
"""
import builtins
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ["SESSION_NAME"] = "sesion_de_prueba_control_bot"
import control_bot  # noqa: E402
import instancia_unica  # noqa: E402

SESSION = str(control_bot.RAIZ / os.environ["SESSION_NAME"])
tmp = Path(tempfile.mkdtemp(prefix="test_control_bot_"))
control_bot.ARCHIVO_ESTADO = tmp / "estado_bot.json"
control_bot.ARCHIVO_PARAR = tmp / "parar_bot.flag"
control_bot.ESPERA_APAGADO_SEG = 15


def responder(*respuestas):
    cola = list(respuestas)
    builtins.input = lambda _p="": cola.pop(0)


def publicar(**campos):
    e = control_bot.EstadoBot()
    for k, v in campos.items():
        setattr(e, k, v)
    e.publicar()
    return e


# 1) estado: libre/ocupado y ida y vuelta por archivo
e = control_bot.EstadoBot()
assert e.libre
e.en_cola = 1
assert not e.libre
e.en_cola, e.orden = 0, "123"
assert not e.libre
e.orden = None
assert e.libre
e.fase, e.orden, e.en_cola = "escuchando", "999", 2
e.publicar()
leido = control_bot.leer_estado()
assert leido["pid"] == os.getpid() and leido["orden"] == "999" and leido["en_cola"] == 2, leido
control_bot.limpiar()
assert control_bot.leer_estado() is None
print("1 ok  estado publicado/leído/limpiado")

# 2) proceso vivo / muerto
hijo = subprocess.Popen([sys.executable, "-c", "pass"])
hijo.wait()
assert control_bot._proceso_vivo(os.getpid())
assert not control_bot._proceso_vivo(hijo.pid)
assert not control_bot._proceso_vivo(None) and not control_bot._proceso_vivo(0)
print("2 ok  detección de proceso vivo/muerto")

# 3) sesión en uso
assert not instancia_unica.en_uso(SESSION)
s = instancia_unica._abrir(SESSION)
assert s is not None and instancia_unica.en_uso(SESSION)
assert instancia_unica._abrir(SESSION) is None          # un segundo bot no puede tomarla
s.close()
assert not instancia_unica.en_uso(SESSION)
print("3 ok  sesión libre/en uso")

# 4) main(): no hay bot -> pide arrancar (y borra restos de un cierre brusco)
publicar(fase="escuchando")
control_bot.pedir_parada()
assert control_bot.main() == control_bot.SALIR_INICIAR
assert control_bot.leer_estado() is None and not control_bot.parada_pedida()
print("4 ok  sin bot -> código", control_bot.SALIR_INICIAR, "y limpia restos")

# 5) main(): sesión tomada por algo que no es un bot nuestro -> no hace nada
s = instancia_unica._abrir(SESSION)
assert control_bot.main() == 1 and not control_bot.parada_pedida()
publicar(fase="escuchando")
control_bot.ARCHIVO_ESTADO.write_text('{"pid": %d, "fase": "escuchando"}' % hijo.pid)   # pid muerto
assert control_bot.main() == 1 and not control_bot.parada_pedida()
print("5 ok  sesión ajena / estado de un proceso muerto -> no apaga nada")

# 6) main(): bot arrancando -> no ofrece apagar
publicar(fase="iniciando")
assert control_bot.main() == 1 and not control_bot.parada_pedida()
print("6 ok  bot arrancando -> solo avisa")

# 7) main(): bot libre, el usuario dice que no -> no se pide apagado
publicar(fase="escuchando")
responder("n")
assert control_bot.main() == 0 and not control_bot.parada_pedida()
print("7 ok  respuesta 'n' -> no se apaga")

# 8) main(): bot OCUPADO, el usuario dice que sí -> pide apagado y espera a que el bot cierre
publicar(fase="escuchando", orden="555", desde=time.time() - 12, en_cola=1)
responder("s")


def bot_simulado():
    """Cierra recién DESPUÉS del pedido, como haría el bot al quedar sin órdenes."""
    while not control_bot.parada_pedida():
        time.sleep(0.1)
    time.sleep(2.0)
    s.close()


hilo = threading.Thread(target=bot_simulado)
hilo.start()
t0 = time.time()
assert control_bot.main() == 0
hilo.join()
assert control_bot.parada_pedida() and time.time() - t0 >= 2.0
print("8 ok  'sí' con bot ocupado -> pidió apagado y esperó a que cerrara (%.1f s)" % (time.time() - t0))

# 9) main(): ya hay apagado pedido y el usuario cancela -> se borra el pedido
s = instancia_unica._abrir(SESSION)
publicar(fase="apagando", orden="555", en_cola=0)
control_bot.pedir_parada()
responder("s")
assert control_bot.main() == 0 and not control_bot.parada_pedida()
print("9 ok  cancelar apagado -> el pedido se borra")

# 10) main(): apagado pedido y el usuario NO cancela, pero el bot no cierra a tiempo -> avisa, sin matar nada
publicar(fase="apagando", orden="555", desde=time.time())
control_bot.pedir_parada()
responder("n")
t0 = time.time()
assert control_bot.main() == 1 and control_bot.parada_pedida() and time.time() - t0 >= 15
s.close()
print("10 ok el bot sigue terminando -> avisa y deja el pedido en pie")

control_bot.limpiar()
print("\nTODO OK")
