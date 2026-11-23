#!/usr/bin/env python3
"""Construye una copia limpia y reversible del bot actual con Telegram.

No arranca procesos, no abre Telegram, no conecta teléfonos y no copia secretos.
El origen es la referencia saneada ``legacy/telegram`` del repositorio; el destino
debe ser una carpeta nueva y vacía. La estructura generada conserva el runtime
junto para no romper los imports absolutos del bot heredado.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "legacy" / "telegram"

LOCAL_DIRS = {".git", ".venv", "venv", "__pycache__", "capturas", "logs", "respaldos"}
LOCAL_FILES = {
    "iphones.json",
    "panel_clave.json",
    "panel_grupos.json",
    "estado_bot.json",
    "estado_bot.tmp",
    "envios_pendientes.json",
    "envios_pendientes.tmp",
    "parar_bot.flag",
}

ROOT_FILES = {
    "INICIAR-BOT-IPHONE.bat": """@echo off
setlocal
cd /d \"%~dp0runtime\"
call iniciar_bot_iphone.bat
""",
    "INICIAR-BOT-ANDROID.bat": """@echo off
setlocal
cd /d \"%~dp0runtime\"
call iniciar_bot.bat
""",
    "ABRIR-PANEL.bat": """@echo off
setlocal
cd /d \"%~dp0runtime\"
call ABRIR-PANEL.bat
""",
    "INICIAR-BOT-IPHONE.command": """#!/bin/bash
set -e
cd \"$(dirname \"$0\")/runtime\"
exec \"./Iniciar bot iPhone 12.command\"
""",
}

README = """# Bot de pagos ADM — paquete limpio

Esta carpeta es una copia de laboratorio organizada a partir de la referencia Telegram. No sustituye automáticamente
el bot que está pagando ni trae credenciales, sesiones, capturas o datos de teléfonos.

## Uso de la carpeta

- `runtime/`: código que debe permanecer junto para que los imports y rutas del bot heredado sigan funcionando.
- `INICIAR-BOT-IPHONE.bat` / `INICIAR-BOT-ANDROID.bat`: accesos de Windows.
- `ABRIR-PANEL.bat`: abre el panel local.
- `docs/`: inventario, reglas de instalación y separación entre runtime y mantenimiento.

## Antes de cualquier prueba

1. Configura localmente `runtime/.env` desde `runtime/.env.example`; nunca lo subas ni copies desde otra PC.
2. Registra el teléfono localmente en `runtime/iphones.json` si aplica; el archivo no pertenece al paquete.
3. Instala dependencias y conecta el dispositivo en un entorno de laboratorio.
4. Prueba sin enviar ni confirmar pagos. Si una ejecución alcanza una clave o confirmación, debe terminar como incierta.

La carpeta del bot operativo original queda sin modificar para poder volver atrás.
"""

INVENTORY = """# Inventario operativo

## Arranque que se conserva

- `runtime/orquestador.py`: coordinación actual mediante Telegram.
- `runtime/ejecutor.py` y `runtime/adb_utils.py`: pago por Android/emulador.
- `runtime/ejecutor_iphone.py` y `runtime/iphone/`: pago por iPhone.
- `runtime/control_bot.py`, `runtime/config.py`, `runtime/ordenes.py`, `runtime/bancos.py`,
  `runtime/envios_pendientes.py`, `runtime/registro_bot.py`, `runtime/registro_iphones.py`,
  `runtime/instancia_unica.py` y `runtime/version.py`: soporte requerido por los arranques.
- `runtime/panel_bot.pyw` y `runtime/herramientas_iphone.py`: panel y herramientas accesibles desde él.

## Mantenimiento y diagnóstico

Los scripts de diagnóstico, inspección, listado de chats, login, calibración y pruebas se mantienen dentro de
`runtime/` porque el bot heredado resuelve imports y rutas desde esa carpeta. No se ejecutan al arrancar el bot.

## Solo local; nunca se empaqueta

`.env`, sesiones Telegram, `iphones.json`, claves del panel, capturas, logs, estados, respaldos, bases SQLite y
entornos virtuales. Cada PC conserva o crea esos archivos localmente.
"""


def is_local_artifact(path: Path) -> bool:
    """True when a path must never move from a running PC into a package."""
    if any(part in LOCAL_DIRS for part in path.parts[:-1]):
        return True
    name = path.name
    return (
        name in LOCAL_FILES
        or name == ".env"
        or (name.startswith(".env.") and name != ".env.example")
        or ".session" in name
        or name.endswith((".sqlite", ".sqlite-shm", ".sqlite-wal", ".db", ".log"))
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_runtime(source: Path, runtime: Path) -> list[dict[str, str]]:
    inventory: list[dict[str, str]] = []
    for item in sorted(source.rglob("*")):
        if (
            item.is_dir()
            or item.suffix.lower() in {".md", ".txt"}
            or is_local_artifact(item.relative_to(source))
        ):
            continue
        relative = item.relative_to(source)
        target = runtime / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        inventory.append({"path": relative.as_posix(), "sha256": sha256(target)})
    return inventory


def write_text(path: Path, content: str, *, executable: bool = False) -> None:
    path.write_text(content, encoding="utf-8", newline="\n")
    if executable:
        path.chmod(path.stat().st_mode | 0o111)


def build(source: Path, output: Path) -> None:
    source = source.resolve()
    output = output.resolve()
    if not source.is_dir():
        raise ValueError(f"No existe la referencia de origen: {source}")
    if output.exists():
        raise ValueError(f"El destino ya existe; no se sobrescribe: {output}")
    if output == source or source in output.parents:
        raise ValueError("El destino no puede estar dentro del origen")

    runtime = output / "runtime"
    runtime.mkdir(parents=True)
    inventory = copy_runtime(source, runtime)
    docs = output / "docs"
    docs.mkdir()
    write_text(output / "README-PRIMERO.md", README)
    write_text(docs / "INVENTARIO-OPERATIVO.md", INVENTORY)
    for name, content in ROOT_FILES.items():
        write_text(output / name, content, executable=name.endswith(".command"))
    (output / "MANIFEST.json").write_text(
        json.dumps({"source": "referencia Telegram saneada", "files": inventory}, indent=2, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    print(f"Paquete creado: {output}")
    print(f"Archivos saneados copiados: {len(inventory)}")
    print("No se inició ningún bot ni se copiaron archivos locales.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="Carpeta nueva donde crear el paquete")
    parser.add_argument("--source", default=DEFAULT_SOURCE, type=Path, help="Referencia saneada de origen")
    args = parser.parse_args()
    try:
        build(args.source, args.output)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
