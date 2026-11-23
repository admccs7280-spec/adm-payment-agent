"""Diferencias entre Windows y macOS/Linux que usan el panel y el lanzador.

En Windows se conserva el comportamiento original (PowerShell, taskkill, os.startfile); en macOS se
usan `ps`, señales y `open`. Un solo lugar para no repartir `if sys.platform` por todo el código.
"""
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

ES_WINDOWS = os.name == "nt"
SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0) if ES_WINDOWS else 0
# Para Popen: que el bot sobreviva al panel (en Windows ya lo hace; en macOS hay que soltarlo de la sesión).
OPCIONES_DESACOPLADO = {} if ES_WINDOWS else {"start_new_session": True}


def python_del_entorno(carpeta_venv: Path) -> Path:
    return carpeta_venv / ("Scripts/python.exe" if ES_WINDOWS else "bin/python")


def _powershell_json(comando: str) -> list[dict]:
    r = subprocess.run(["powershell", "-NoProfile", "-Command", comando], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=30, creationflags=SIN_VENTANA)
    texto = r.stdout.strip()
    if not texto:
        return []
    datos = json.loads(texto)
    return datos if isinstance(datos, list) else [datos]


def procesos_python() -> list[dict]:
    """[{ProcessId, ParentProcessId, CommandLine}] de los procesos cuyo nombre empieza por python."""
    if ES_WINDOWS:
        return _powershell_json(
            "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
            "Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress")
    r = subprocess.run(["ps", "-axo", "pid=,ppid=,command="], capture_output=True, text=True, timeout=30)
    salida = []
    for linea in r.stdout.splitlines():
        partes = linea.strip().split(None, 2)
        if len(partes) < 3 or not partes[0].isdigit():
            continue
        comando = partes[2]
        ejecutable = Path(comando.split()[0]).name.lower()
        if ejecutable.startswith("python"):
            salida.append({"ProcessId": int(partes[0]), "ParentProcessId": int(partes[1]), "CommandLine": comando})
    return salida


def linea_de_comando(pid: int) -> str:
    if ES_WINDOWS:
        datos = _powershell_json(f"Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}' | "
                                 "Select-Object CommandLine | ConvertTo-Json -Compress")
        return (datos[0].get("CommandLine") or "") if datos else ""
    r = subprocess.run(["ps", "-p", str(int(pid)), "-o", "command="], capture_output=True, text=True, timeout=30)
    return r.stdout.strip()


def matar_arbol(pid: int) -> None:
    """Corta el proceso y sus hijos (último recurso)."""
    if ES_WINDOWS:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, creationflags=SIN_VENTANA)
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)      # el bot se lanza como líder de su propia sesión
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def abrir(ruta) -> None:
    if ES_WINDOWS:
        os.startfile(str(ruta))     # type: ignore[attr-defined]
    else:
        subprocess.run(["open", str(ruta)], check=True)


def nitidez_dpi() -> None:
    if ES_WINDOWS:
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
