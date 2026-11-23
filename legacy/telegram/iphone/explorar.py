"""Herramienta de mapeo: ejecuta una secuencia de acciones en el iPhone.

Uso: python explorar.py "dump" "toca Cuentas de terceros" "xy 585 540" "foto form"
Acciones:
  dump              textos visibles con su rectángulo
  foto NOMBRE       guarda capturas/NOMBRE.png
  toca TEXTO        toca el texto exacto (sin tildes ni mayúsculas)
  tocap TEXTO       toca el primer texto que CONTIENE TEXTO
  tab TEXTO         toca la pestaña de abajo que empieza con TEXTO
  xy X Y            toca en píxeles de la captura (1170x2532)
  escribe TEXTO     teclea TEXTO (ASCII)
  borra N           N retrocesos
  espera S          duerme S segundos
  desliza X1 Y1 X2 Y2
"""
import sys
import time

from iphone_utils import ControladorIPhone

ctrl = ControladorIPhone()
try:
    for paso in sys.argv[1:]:
        orden, _, resto = paso.partition(" ")
        print(f"## {paso}")
        if orden == "dump":
            for t in ctrl.nodos():
                print(f"[{t.x1},{t.y1}][{t.x2},{t.y2}] {t.texto}")
        elif orden == "foto":
            print(ctrl.capturar_pantalla(f"capturas/{resto}.png"))
        elif orden == "toca":
            ctrl.tap_texto(resto, espera=0.8)
        elif orden == "tocap":
            ctrl.tap_texto(resto, exacto=False, espera=0.8)
        elif orden == "tab":
            tab = next((t for t in ctrl.nodos() if t.texto.startswith(resto) and t.y1 > 2200), None)
            if tab is None:
                raise SystemExit(f"no veo la pestaña {resto!r}")
            ctrl.tap(*tab.centro, que=f"pestaña {resto}", espera=0.8)
        elif orden == "campo":
            punto = ctrl.ubicar_campo(resto)
            if punto is None:
                raise SystemExit(f"no veo el campo {resto!r}")
            ctrl.tap(*punto, que=f"campo {resto}", espera=0.6)
        elif orden == "valor":
            print(f"valor de {resto!r}: {ctrl.valor_campo(resto, ctrl.nodos())!r}")
        elif orden == "xy":
            x, y = map(int, resto.split())
            ctrl.tap(x, y, espera=0.8)
        elif orden == "escribe":
            ctrl.escribir(resto)
        elif orden == "borra":
            ctrl.borrar(int(resto))
        elif orden == "espera":
            time.sleep(float(resto))
        elif orden == "desliza":
            ctrl.deslizar(*map(int, resto.split()))
            time.sleep(0.8)
        else:
            raise SystemExit(f"acción desconocida: {orden}")
finally:
    ctrl.cerrar()
