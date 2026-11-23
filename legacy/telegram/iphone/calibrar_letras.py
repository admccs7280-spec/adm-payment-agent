"""Guarda una plantilla (recorte del campo "Tipo:") por cada letra de documento.

Requisito: estar en "Transferencias -> Cuentas otros bancos" con el formulario
visible. Solo abre la lista y elige cada letra; no envía nada.
"""
from iphone_utils import (CARPETA_PLANTILLAS, ORDEN_TIPO_DOCUMENTO, ControladorIPhone)

CARPETA_PLANTILLAS.mkdir(exist_ok=True)
ctrl = ControladorIPhone()
try:
    for letra in ORDEN_TIPO_DOCUMENTO:
        punto = ctrl.ubicar_campo("Tipo:")
        if punto is None:
            raise SystemExit("no veo el campo 'Tipo:': abrir Cuentas otros bancos primero")
        ctrl.tap(*punto, que="abrir Tipo", espera=0.8)
        filas = ctrl._filas_letras(ctrl.captura_png())
        print(f"{letra}: filas detectadas {filas}")
        if len(filas) != len(ORDEN_TIPO_DOCUMENTO):
            raise SystemExit("la lista no tiene 6 letras")
        ctrl.tap(100, filas[ORDEN_TIPO_DOCUMENTO.index(letra)], que=f"letra {letra}", espera=1.0)
        png = ctrl.captura_png()
        recorte = ctrl._recorte_tipo_documento(png, ctrl._leer_png(png))
        if recorte is None:
            raise SystemExit(f"no pude recortar el campo tras elegir {letra}")
        recorte.save(CARPETA_PLANTILLAS / f"tipo_doc_{letra}.png")
        print(f"   plantilla guardada ({recorte.size})")

    # prueba: leer de vuelta la última (C) y reelegir V
    print("lee ahora:", ctrl.leer_tipo_documento())
    ctrl.elegir_tipo_documento("V")
    print("tras elegir V:", ctrl.leer_tipo_documento())
    ctrl.elegir_tipo_documento("J")
    print("tras elegir J:", ctrl.leer_tipo_documento())
finally:
    ctrl.cerrar()
