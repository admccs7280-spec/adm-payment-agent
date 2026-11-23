"""Arma el zip para instalar/actualizar el bot en OTRAS PC, sin ningún secreto dentro.

    iphone\\.venv\\Scripts\\python.exe empaquetar.py

1. Genera `iphones.plantilla.json` (UDID, nombre y cuenta de cada iPhone registrado, clave VACÍA) y
   `CONFIGURACION-TELEFONOS.md` (la misma lista legible) desde el `iphones.json` de esta PC.
2. Guarda un respaldo local de `iphones.json` (con claves) en respaldos\\ — ese NO viaja.
3. Crea respaldos\\bot-iphone12-<versión>.zip sin .env, sesiones, claves, capturas, logs ni .venv.
4. Revisa el zip: falla si trae un archivo prohibido o cualquier valor secreto de esta PC
   (API_HASH, claves del banco, teléfono de Telegram) dentro de un archivo de texto.
"""
import json
import re
import shutil
import sys
import time
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
import registro_iphones  # noqa: E402
from version import VERSION  # noqa: E402

DIRS_FUERA = {".venv", "__pycache__", "capturas", "logs", "respaldos", ".git"}
NOMBRES_FUERA = {".env", ".env.bak", ".env.tmp", "iphones.json", "panel_clave.json", "panel_grupos.json",
                 "estado_bot.json", "estado_bot.tmp", "parar_bot.flag"}
PATRONES_FUERA = (".session", ".session-journal", ".pyc", ".lock", ".pid", ".flag", ".tmp")
PLANTILLA = RAIZ / "iphones.plantilla.json"
LISTA_MD = RAIZ / "CONFIGURACION-TELEFONOS.md"


def _fuera(ruta: Path) -> bool:
    rel = ruta.relative_to(RAIZ)
    if any(p in DIRS_FUERA for p in rel.parts[:-1]):
        return True
    n = ruta.name
    return n in NOMBRES_FUERA or n.startswith("envios_pendientes") or n.endswith(PATRONES_FUERA) \
        or ".session" in n


def generar_lista() -> dict:
    reg = registro_iphones.cargar()
    if not reg:
        sys.exit("No hay iPhone registrados en iphones.json: registra los teléfonos antes de empaquetar.")
    sin_clave = {u: {"nombre": d.get("nombre", "iPhone"), "cuenta": d.get("cuenta", ""), "clave": ""}
                 for u, d in reg.items()}
    PLANTILLA.write_text(json.dumps(sin_clave, ensure_ascii=False, indent=1), encoding="utf-8")
    filas = "\n".join(f"| {d['nombre']} | `{d['cuenta']}` | `{u}` |" for u, d in sin_clave.items())
    LISTA_MD.write_text(
        f"# Teléfonos registrados (versión {VERSION})\n\n"
        "Lista de los iPhone 12 de esta operación: a qué cuenta pertenece cada uno y su UDID. "
        "**No lleva claves del banco**: cada PC las escribe una vez (ver `ACTUALIZAR-Y-REGISTRAR-TELEFONOS.md`).\n\n"
        "| Nombre | Cuenta en la app | UDID |\n|---|---|---|\n" + filas + "\n\n"
        "Al conectar uno de estos teléfonos, el bot lo reconoce por su UDID y comprueba que la app muestre "
        "esa cuenta. Si la cuenta de la pantalla no coincide, no paga.\n", encoding="utf-8")
    return reg


def respaldar_registro() -> Path:
    destino = RAIZ / "respaldos" / f"iphones-{time.strftime('%Y%m%d-%H%M')}.json"
    destino.parent.mkdir(exist_ok=True)
    shutil.copy2(registro_iphones.ARCHIVO, destino)
    return destino


def secretos() -> set[str]:
    """Valores que jamás deben aparecer dentro del zip."""
    vals = set()
    env = RAIZ / ".env"
    if env.exists():
        for linea in env.read_text(encoding="utf-8-sig").splitlines():
            m = re.match(r"\s*(API_HASH|API_ID|TELEFONO|BDV_PASSWORD\w*)\s*=\s*(.+?)\s*$", linea)
            if m and len(m.group(2)) >= 5:
                vals.add(m.group(2).strip("\"'"))
    for d in registro_iphones.cargar().values():
        if len((d.get("clave") or "")) >= 4:
            vals.add(d["clave"])
    return vals


def main() -> None:
    reg = generar_lista()
    respaldo = respaldar_registro()
    zip_ruta = RAIZ / "respaldos" / f"bot-iphone12-{VERSION}.zip"
    zip_ruta.unlink(missing_ok=True)
    n = 0
    with zipfile.ZipFile(zip_ruta, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(RAIZ.rglob("*")):
            if f.is_file() and not _fuera(f):
                z.write(f, Path("Bot iPhone 12") / f.relative_to(RAIZ))
                n += 1
    # --- revisión ---
    prohibidos, filtrados, claves = [], [], secretos()
    with zipfile.ZipFile(zip_ruta) as z:
        for info in z.infolist():
            nombre = Path(info.filename).name
            if nombre in NOMBRES_FUERA or ".session" in nombre or nombre.endswith(".env"):
                prohibidos.append(info.filename)
            if info.filename.endswith((".py", ".pyw", ".md", ".txt", ".json", ".bat", ".example", ".toml")):
                texto = z.read(info).decode("utf-8", "ignore")
                filtrados += [f"{info.filename} contiene un valor secreto" for s in claves if s in texto]
    if prohibidos or filtrados:
        zip_ruta.unlink(missing_ok=True)
        sys.exit("ZIP RECHAZADO y borrado:\n  " + "\n  ".join(prohibidos + filtrados))
    mb = zip_ruta.stat().st_size / 1024 / 1024
    print(f"OK  {zip_ruta}  ({n} archivos, {mb:.1f} MB)")
    print(f"OK  {len(reg)} iPhone en la plantilla, sin claves. Respaldo local con claves: {respaldo}")
    print("OK  revisión: ningún archivo prohibido ni valor secreto dentro del zip.")


if __name__ == "__main__":
    main()
