"""Prueba de instalación SIN dinero: llena los 3 formularios con datos FALSOS, verifica lo
leído y limpia. NUNCA toca Pagar ni Transferir. Termina volviendo a la pestaña Inicio.
Usa la cuenta a debitar de ESTA PC (CUENTA_DEBITAR_DESC del .env).

    iphone\\.venv\\Scripts\\python.exe iphone\\probar_formularios.py"""
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from config import requerido  # noqa: E402
from iphone_utils import ControladorIPhone  # noqa: E402

CUENTA = requerido("CUENTA_DEBITAR_DESC")
ctrl = ControladorIPhone()


def mostrar(*rotulos):
    for r in rotulos:
        print(f"   {r:22} -> {ctrl.leer_valor_campo(r)!r}")


def paso(titulo, f):
    print(f"\n### {titulo}")
    t = time.perf_counter()
    try:
        f()
        print(f"   ok ({time.perf_counter() - t:.1f} s)")
    except Exception as exc:
        print(f"   !! FALLÓ: {exc}")
        ctrl.capturar_pantalla(str(RAIZ / "capturas" / f"falla_{titulo.split()[0]}.png"))
        raise


try:
    # ── Pago Móvil ──
    def pago_movil():
        ctrl.abrir_pago_movil()
        ctrl.limpiar_formulario()
        ctrl.llenar("Documento:", "12345678")
        ctrl.elegir_banco("0134")
        ctrl.llenar("Teléfono:", "04141234567")
        ctrl.llenar("Monto:", "100,50", cerrar_teclado=True)
        ctrl.llenar("Concepto:", "pago")
        mostrar("Operación:", "Documento:", "Banco:", "Teléfono:", "Monto:", "Concepto:")
        ctrl.limpiar_formulario()

    paso("PM Pago Movil", pago_movil)

    # ── Terceros ──
    def terceros():
        ctrl.abrir_transferencia("Cuentas de terceros")
        ctrl.limpiar_formulario()
        ctrl.elegir_cuenta_debitar(CUENTA)
        ctrl.llenar("Cuenta destino:", "01020000000000000000")
        ctrl.llenar("Monto:", "2000,00", cerrar_teclado=True)
        ctrl.llenar("Concepto:", "pago")
        mostrar("Cuenta a debitar:", "Cuenta destino:", "Monto:", "Concepto:")
        ctrl.limpiar_formulario()

    paso("TERCEROS", terceros)

    # ── Otros bancos (instrumento Teléfono) ──
    def otros_bancos():
        ctrl.abrir_transferencia("Cuentas otros bancos")
        ctrl.limpiar_formulario()
        ctrl.elegir_cuenta_debitar(CUENTA)
        ctrl.elegir_opcion("Tipo de instrumento:", "Teléfono")
        ctrl.elegir_banco("0174")
        ctrl.elegir_tipo_documento("V")
        ctrl.llenar("Instrumento destino:", "04141234567")
        ctrl.llenar("Nombre:", "JUAN PEREZ")
        ctrl.llenar("Documento:", "12345678")
        ctrl.llenar("Monto:", "2000,00", cerrar_teclado=True)
        ctrl.llenar("Concepto:", "pago")
        mostrar("Cuenta a debitar:", "Tipo de instrumento:", "Banco:", "Instrumento destino:",
                "Nombre:", "Documento:", "Monto:", "Concepto:")
        print("   tipo documento:", ctrl.leer_tipo_documento())
        ctrl.limpiar_formulario()

    paso("OTROS bancos", otros_bancos)
finally:
    try:
        ctrl.tab("Inicio")
    except Exception as exc:
        print("no se pudo volver a Inicio:", exc)
    ctrl.cerrar()
