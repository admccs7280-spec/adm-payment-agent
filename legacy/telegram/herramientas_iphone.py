"""Herramientas del panel para el iPhone. Solo con el bot APAGADO: el iPhone admite una sola
conexión de captura a la vez y el bot mantiene la suya abierta mientras corre.

    iphone\\.venv\\Scripts\\python.exe herramientas_iphone.py inicio
    iphone\\.venv\\Scripts\\python.exe herramientas_iphone.py pantalla capturas\\pantalla.png
    iphone\\.venv\\Scripts\\python.exe herramientas_iphone.py revisar

inicio    vuelve la app BDV a Inicio (lo mismo que hace el bot después de cada orden).
pantalla  guarda una captura de la pantalla del iPhone en la ruta indicada. No toca nada.
registrar NOMBRE   REGISTRA el iPhone conectado: lee su cuenta BDV del propio teléfono y la guarda en
          iphones.json con el nombre y la clave (variable de entorno REGISTRO_CLAVE, o se pide).
listar    muestra los iPhone registrados y cuáles están conectados.
revisar   CAMBIO DE iPhone: comprueba todo lo que el bot necesita de un iPhone (reconocerlo, iOS,
          Modo de desarrollador, imagen de desarrollador, conexión, sesión de la app BDV, cuenta a
          debitar del .env, formularios de Pago Móvil y Transferencias) y dice si está LISTO. No
          paga nada: nunca toca Pagar, Transferir ni Confirmar.
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "iphone"))
os.environ.setdefault("DISPOSITIVO", "iphone")

import dispositivo  # noqa: E402
import ejecutor_iphone  # noqa: E402
import registro_iphones  # noqa: E402
from config import opcional  # noqa: E402

A_MANO = (
    "Comprueba a mano en el iPhone (el bot no puede verlo desde aquí):",
    "  - Ajustes > Desarrollador > «Automatización de interfaz»: APAGADO (encendido muestra avisos de automatización).",
    "  - En la app BDV: Face ID / biometría DESACTIVADO (si no, tras Confirmar pide el código del iPhone).",
    "  - Ajustes > Notificaciones > BDV: apagadas.",
    "  - Ajustes > Accesibilidad > Tocar > AssistiveTouch: APAGADO.",
    "  - Ajustes > Pantalla y brillo > Bloqueo automático: NUNCA.",
    "  - En el panel > Configuración: «Cuenta a debitar» y «Clave del banco» de ESTE iPhone, guardados.",
)


def _linea(ok: bool | None, texto: str) -> None:
    print(("  OK       " if ok else ("  ATENCIÓN " if ok is None else "  FALLA    ")) + texto, flush=True)


def revisar() -> int:
    """0 si el iPhone quedó listo para operar, 1 si hay algo que arreglar."""
    udid_cfg = opcional("SERIAL_ADB", "")
    fallas = 0
    print("Revisando el iPhone conectado (no se paga nada)...", flush=True)
    try:
        iphones = dispositivo.listar_iphones()
    except dispositivo.DispositivoNoListo as exc:
        _linea(False, str(exc))
        return 1
    _linea(bool(iphones), f"iPhone conectados por USB: {len(iphones)}" +
           "".join(f"\n             - {d['nombre']}  iOS {d['ios']}  UDID {d['udid']}" for d in iphones))
    try:
        d = dispositivo.elegir(udid_cfg)
    except dispositivo.DispositivoNoListo as exc:
        _linea(False, str(exc))
        return 1
    _linea(True, f"iPhone elegido: «{d['nombre']}» ({d['udid']})" + (" [UDID de Configuración]" if udid_cfg else
                                                                      " [el único conectado]"))
    ios_ok = dispositivo._ios_mayor(d["ios"]) >= dispositivo.IOS_MINIMO
    _linea(ios_ok, f"iOS {d['ios']} (se necesita {dispositivo.IOS_MINIMO} o superior)")
    if not ios_ok:
        return 1
    dev = dispositivo.modo_desarrollador(d["udid"])
    if dev is False:
        dispositivo._pmd3(["amfi", "reveal-developer-mode", "--udid", d["udid"]], 40)
    _linea(dev, "Modo de desarrollador " + ("ACTIVO" if dev else "APAGADO: Ajustes > Privacidad y seguridad > "
                                            "Modo de desarrollador > activar (el iPhone se reinicia)"))
    if not dev:
        return 1
    ok, texto = dispositivo.montar_imagen(d["udid"])
    _linea(ok, f"Imagen de desarrollador: {texto}")
    if not ok:
        return 1

    t0 = time.time()
    try:
        ctrl = ejecutor_iphone.ControladorIPhone(d["udid"])
    except Exception as exc:
        _linea(False, f"no se pudo abrir la conexión de control: {dispositivo.explicar_error(str(exc))}")
        return 1
    _linea(True, f"Conexión de control abierta en {time.time() - t0:.1f} s")
    try:
        lista = ctrl.nodos()
        _linea(bool(lista), "Lectura de la pantalla (OCR en español)" + ("" if lista else
               ": no devolvió texto (¿iPhone bloqueado?). Desbloquéalo y vuelve a revisar."))
        if not lista:
            return 1
        try:
            ejecutor_iphone._volver_inicio(ctrl)
            _linea(True, "App BDV con la sesión iniciada y en Inicio")
        except ejecutor_iphone.SesionCerrada:
            _linea(False, ejecutor_iphone.MENSAJE_SESION_CERRADA)
            return 1
        except Exception as exc:
            _linea(None, f"no se pudo dejar la app en Inicio ({exc}). Déjala en Inicio a mano y repite.")
            return 1
        reg = registro_iphones.buscar(d["udid"])
        cuenta = (reg or {}).get("cuenta") or ("" if reg else opcional("CUENTA_DEBITAR_DESC", ""))
        if not cuenta:
            try:
                cuenta = ejecutor_iphone.leer_cuenta_del_iphone(ctrl)
                _linea(None, f"iPhone sin cuenta registrada: la app tiene {cuenta} (se registra con «registrar»)")
            except Exception as exc:
                _linea(False, f"no se pudo leer la cuenta del iPhone: {exc}")
                return 1
        _linea(True, ("Registro: «%s»" % reg["nombre"]) if reg else
               "iPhone SIN registrar: se usa la cuenta y la clave del .env (regístralo para no depender de eso)")
        try:
            ctrl.abrir_transferencia("Cuentas de terceros")
            ctrl.limpiar_formulario()
            ctrl.elegir_cuenta_debitar(cuenta)
            _linea(True, f"Cuenta a debitar: el iPhone tiene {cuenta} (la de su registro/Configuración)")
            ctrl.limpiar_formulario()
        except Exception as exc:
            _linea(False, f"Cuenta a debitar ({cuenta}) NO coincide o no se pudo leer: {exc}")
            fallas += 1
        try:
            ejecutor_iphone._volver_inicio(ctrl)
            ctrl.abrir_pago_movil()
            ctrl.limpiar_formulario()
            _linea(True, "Formulario de Pago Móvil abre y se limpia")
        except Exception as exc:
            _linea(False, f"Formulario de Pago Móvil: {exc}")
            fallas += 1
        try:
            ejecutor_iphone._volver_inicio(ctrl)
            ctrl.abrir_transferencia("Cuentas otros bancos")
            ctrl.limpiar_si_hace_falta("Tipo de instrumento:")
            _linea(True, "Formulario de Transferencias a otros bancos abre")
        except Exception as exc:
            _linea(False, f"Formulario de Transferencias a otros bancos: {exc}")
            fallas += 1
        else:
            # El paso que falló en un iPhone en modo claro (2026-10-29): elegir el tipo de documento
            # y leerlo de vuelta, con otra letra ya elegida (la lista se abre en otra posición).
            try:
                ctrl.elegir_cuenta_debitar(cuenta)
                ctrl.elegir_opcion("Tipo de instrumento:", "Cuenta")
                ctrl.elegir_banco("0134")
                for letra in ("V", "J", "C", "V"):
                    ctrl.elegir_tipo_documento(letra)
                _linea(True, "Tipo de documento (V/J/C) se elige y se lee de vuelta (apariencia clara u oscura)")
                ctrl.limpiar_formulario()
            except Exception as exc:
                _linea(False, f"Tipo de documento: {exc}")
                fallas += 1
    finally:
        try:
            ctrl.cerrar_teclado()
            ejecutor_iphone._volver_inicio(ctrl)
        except Exception:
            pass
        ctrl.cerrar()
    print()
    print("\n".join(A_MANO))
    print()
    print("RESULTADO: " + ("el iPhone está LISTO para operar. Revisa la lista de arriba y enciende el bot."
                           if not fallas else "hay cosas por arreglar (líneas FALLA). Corrígelas y vuelve a revisar."))
    return 1 if fallas else 0


def listar() -> int:
    reg = registro_iphones.cargar()
    try:
        conectados = {d["udid"].lower(): d for d in dispositivo.listar_iphones()}
    except dispositivo.DispositivoNoListo as exc:
        print(f"  (no se pudo ver los conectados: {exc})")
        conectados = {}
    print(f"iPhone registrados: {len(reg)}")
    for u, d in reg.items():
        marca = "CONECTADO" if u.lower() in conectados else "-"
        print(f"  {d.get('nombre', '?'):22s} {d.get('cuenta') or '(cuenta sin leer)':14s} {u}  {marca}"
              + ("" if d.get("clave") else "  FALTA LA CLAVE"))
    for u, d in conectados.items():
        if not any(k.lower() == u for k in reg):
            print(f"  SIN REGISTRAR: «{d['nombre']}» {u}")
    return 0


def registrar(nombre: str) -> int:
    """Registra el iPhone conectado. 0 = listo; 1 = no se pudo (dice por qué)."""
    clave = os.environ.get("REGISTRO_CLAVE")
    if clave is None:
        import getpass
        clave = getpass.getpass("Clave de operaciones BDV de este iPhone: ")
    if not nombre.strip() or not clave.strip():
        print("FALLA: hacen falta el nombre y la clave.")
        return 1
    if "#" in clave:
        print("FALLA: la clave no puede llevar '#'.")
        return 1
    try:
        d = dispositivo.preparar("", print)
    except dispositivo.DispositivoNoListo as exc:
        print(f"FALLA: {exc}")
        return 1
    try:
        ctrl = ejecutor_iphone.ControladorIPhone(d["udid"])
    except Exception as exc:
        print(f"FALLA: no se pudo abrir la conexión con el iPhone: {dispositivo.explicar_error(str(exc))}")
        return 1
    try:
        try:
            ejecutor_iphone._volver_inicio(ctrl)
        except ejecutor_iphone.SesionCerrada:
            print("FALLA: " + ejecutor_iphone.MENSAJE_SESION_CERRADA)
            return 1
        cuenta = ejecutor_iphone.leer_cuenta_del_iphone(ctrl)
        ctrl.limpiar_formulario()
        ejecutor_iphone._volver_inicio(ctrl)
    except Exception as exc:
        print(f"FALLA: no se pudo leer la cuenta BDV del iPhone: {exc}")
        return 1
    finally:
        try:
            ctrl.cerrar()
        except Exception:
            pass
    registro_iphones.guardar(d["udid"], nombre=nombre, cuenta=cuenta, clave=clave)
    print(f"REGISTRADO: «{nombre}» | cuenta {cuenta} | UDID {d['udid']}")
    return 0


def importar() -> int:
    """Carga la lista de teléfonos del zip (iphones.plantilla.json) sin pisar los ya registrados."""
    nuevos = registro_iphones.importar_plantilla()
    print(f"Teléfonos agregados desde la plantilla: {len(nuevos)}")
    return listar()


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in ("inicio", "pantalla", "revisar", "registrar", "listar", "importar"):
        print(__doc__)
        return 2
    if sys.argv[1] == "importar":
        return importar()
    if sys.argv[1] == "revisar":
        return revisar()
    if sys.argv[1] == "listar":
        return listar()
    if sys.argv[1] == "registrar":
        return registrar(" ".join(sys.argv[2:]))
    udid = opcional("SERIAL_ADB", "") or None
    ctrl = ejecutor_iphone.ControladorIPhone(udid)
    try:
        if sys.argv[1] == "inicio":
            ejecutor_iphone._volver_inicio(ctrl)
            print("OK: la app quedó en Inicio.")
        else:
            destino = sys.argv[2] if len(sys.argv) > 2 else "capturas/pantalla.png"
            print("OK:", ctrl.capturar_pantalla(destino))
    finally:
        ctrl.cerrar()
    return 0


if __name__ == "__main__":
    sys.exit(main())
