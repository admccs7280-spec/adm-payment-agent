"""Reconocimiento y preparación del iPhone que está conectado, para que cualquier iPhone 12 que se
conecte (o se cambie por otro) arranque igual, en cualquier PC.

Todo se hace con comandos de pymobiledevice3 EN OTRO PROCESO (sin abrir el túnel del bot): se puede
llamar antes de que el bot abra su propia conexión con el iPhone.

preparar() deja listo, sin intervención, lo que se puede dejar listo solo:
  - hay UN iPhone por USB (con 2 o más y sin UDID configurado se niega: no se paga desde un
    teléfono que nadie eligió);
  - tiene iOS 27 o superior (con menos, el control remoto no funciona);
  - la imagen de desarrollador queda montada (iPhone nuevo o reiniciado: se monta sola);
y avisa con un mensaje claro de lo que SOLO puede hacer una persona en el teléfono (Modo de
desarrollador, confiar en la PC).
"""
import json
import re
import subprocess
import sys
import time

SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
IOS_MINIMO = 27


class DispositivoNoListo(RuntimeError):
    """El iPhone no está en condiciones de operar y no se puede arreglar solo."""


def _pmd3(args: list[str], timeout: float = 90) -> tuple[int, str]:
    try:
        r = subprocess.run([sys.executable, "-m", "pymobiledevice3", *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout, creationflags=SIN_VENTANA)
    except subprocess.TimeoutExpired:
        return 1, f"no respondió en {timeout:.0f} s"
    return r.returncode, _ANSI.sub("", (r.stdout or "") + (r.stderr or "")).strip()


def _json_en(texto: str):
    i, j = texto.find("["), texto.rfind("]")
    if i >= 0 and j > i:
        try:
            return json.loads(texto[i:j + 1])
        except ValueError:
            return None
    return None


def explicar_error(salida: str) -> str:
    """Mensaje claro para el error de pymobiledevice3 más común."""
    if "usbmuxd" in salida:
        return ("la PC no ve el servicio de Apple (usbmuxd): instala iTunes de apple.com (no la app "
                "'Apple Devices') y comprueba el servicio 'Apple Mobile Device Service'")
    if "Developer Mode" in salida or "developer mode" in salida.lower():
        return ("el Modo de desarrollador del iPhone está APAGADO: Ajustes > Privacidad y seguridad > "
                "Modo de desarrollador > activar (el iPhone se reinicia)")
    if "NotPaired" in salida or "pair" in salida.lower() or "trust" in salida.lower():
        return "el iPhone no confía en esta PC: desbloquéalo y toca «Confiar» (pone el código del iPhone)"
    if "iOS 27" in salida or "requires iOS" in salida:
        return f"el iPhone tiene un iOS menor a {IOS_MINIMO}: el control remoto exige iOS {IOS_MINIMO} o superior"
    ultima = [l for l in salida.splitlines() if l.strip()]
    return ultima[-1][:200] if ultima else "error desconocido"


def listar_iphones() -> list[dict]:
    """iPhone conectados por USB: [{udid, nombre, ios, modelo}]. Lanza DispositivoNoListo si la PC no
    puede hablar con el servicio de Apple."""
    codigo, salida = _pmd3(["usbmux", "list", "--usb"], 40)
    if codigo != 0:
        raise DispositivoNoListo(explicar_error(salida))
    lista = _json_en(salida) or []
    return [{"udid": d.get("UniqueDeviceID") or d.get("Identifier", ""), "nombre": d.get("DeviceName", "iPhone"),
             "ios": d.get("ProductVersion", "?"), "modelo": d.get("ProductType", "?")}
            for d in lista if d.get("DeviceClass", "iPhone") == "iPhone"]


def _ios_mayor(version: str) -> int:
    try:
        return int(str(version).split(".")[0])
    except ValueError:
        return 0


def modo_desarrollador(udid: str) -> bool | None:
    """True/False, o None si no se pudo leer."""
    codigo, salida = _pmd3(["amfi", "developer-mode-status", "--udid", udid], 40)
    if codigo != 0:
        return None
    ultima = salida.strip().splitlines()[-1].strip().lower() if salida.strip() else ""
    return True if ultima == "true" else (False if ultima == "false" else None)


def montar_imagen(udid: str) -> tuple[bool, str]:
    """Monta la imagen de desarrollador si no lo está (iPhone nuevo, reiniciado o actualizado).
    Es idempotente: si ya estaba montada no hace nada."""
    codigo, salida = _pmd3(["mounter", "auto-mount", "--udid", udid], 150)
    ya = "already mounted" in salida.lower()
    ok = codigo == 0 or ya
    texto = ("ya estaba montada" if ya else ("montada" if ok else explicar_error(salida)))
    return ok, texto


def elegir(udid_configurado: str = "") -> dict:
    """El iPhone con el que se va a operar. Con UDID configurado, ese; sin él, el ÚNICO conectado."""
    iphones = listar_iphones()
    if udid_configurado:
        for d in iphones:
            if d["udid"].lower() == udid_configurado.strip().lower():
                return d
        conectados = ", ".join(f"{d['nombre']} ({d['udid']})" for d in iphones) or "ninguno"
        if len(iphones) == 1:
            # 2026-10-30: se cambió de iPhone y el UDID del anterior seguía en la configuración: el bot se
            # caía ("no hay dispositivo con ese UDID"). Con UN solo iPhone conectado se usa ese; la cuenta
            # y la clave salen del registro (registro_iphones) y la cuenta se verifica contra el teléfono.
            return dict(iphones[0], udid_configurado_ausente=udid_configurado.strip())
        raise DispositivoNoListo(
            f"el iPhone {udid_configurado} (UDID en Configuración) no está conectado. Conectados: {conectados}. "
            "Si cambiaste de iPhone, borra el UDID en Configuración o pon el del nuevo.")
    if not iphones:
        raise DispositivoNoListo("no hay ningún iPhone conectado por USB (conéctalo, desbloquéalo y toca «Confiar»)")
    if len(iphones) > 1:
        raise DispositivoNoListo(
            f"hay {len(iphones)} iPhone conectados ({', '.join(d['nombre'] + ' ' + d['udid'] for d in iphones)}) y no "
            "hay UDID en Configuración: desconecta el que no vas a usar, o pon el UDID del que sí.")
    return iphones[0]


def preparar(udid_configurado: str = "", log=print) -> dict:
    """Deja listo el iPhone o lanza DispositivoNoListo con lo que hay que hacer. Devuelve su identidad."""
    d = elegir(udid_configurado)
    if _ios_mayor(d["ios"]) < IOS_MINIMO:
        raise DispositivoNoListo(f"el iPhone «{d['nombre']}» tiene iOS {d['ios']}: el control remoto exige iOS "
                                 f"{IOS_MINIMO} o superior. Actualízalo.")
    dev = modo_desarrollador(d["udid"])
    if dev is False:
        # Hace aparecer la opción en Ajustes (en un iPhone nuevo no se ve hasta entonces).
        _pmd3(["amfi", "reveal-developer-mode", "--udid", d["udid"]], 40)
        raise DispositivoNoListo(
            f"el Modo de desarrollador del iPhone «{d['nombre']}» está APAGADO: Ajustes > Privacidad y seguridad "
            "> Modo de desarrollador > activar (el iPhone se reinicia y al prender pide confirmar).")
    ok, texto = montar_imagen(d["udid"])
    if not ok:
        raise DispositivoNoListo(f"no se pudo montar la imagen de desarrollador del iPhone «{d['nombre']}»: {texto}")
    log(f"iPhone reconocido: «{d['nombre']}», iOS {d['ios']}, UDID {d['udid']} (imagen de desarrollador: {texto}).")
    d["imagen"] = texto
    d["listo_en"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return d
