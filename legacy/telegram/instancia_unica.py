"""Seguro de instancia única por sesión de Telegram.

Con UNA sola sesión (.session) para los dos teléfonos, solo puede haber un bot
usándola a la vez. Dos procesos con la misma sesión:
  - se pisan el archivo (sqlite: "database is locked", visto el 2026-09-30),
  - Telegram puede invalidar la clave de la sesión (AuthKeyDuplicatedError) y
    hay que volver a iniciar sesión con código,
  - y si escuchan el mismo grupo, cada orden se paga dos veces (pasó casi el
    2026-09-30 al lanzar una instancia de prueba junto a la real).

Se toma un puerto local derivado del nombre de la sesión: el sistema lo libera
solo cuando el proceso termina, aunque se cierre a la fuerza (a diferencia de
un archivo .lock, que quedaría colgado).
"""
import socket
import sys
import zlib

_SOCKETS: list[socket.socket] = []   # referencia viva mientras dure el proceso


def _puerto(session_name: str) -> int:
    return 49152 + zlib.crc32(str(session_name).encode()) % 10000


def _abrir(session_name: str) -> "socket.socket | None":
    """Intenta tomar el puerto de la sesión; None si otro proceso ya lo tiene."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):       # Windows: nadie más puede compartir el puerto
        s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        s.bind(("127.0.0.1", _puerto(session_name)))
        s.listen(1)
    except OSError:
        s.close()
        return None
    return s


def en_uso(session_name: str) -> bool:
    """True si otro proceso tiene tomada esta sesión. No la toma (la suelta al instante)."""
    s = _abrir(session_name)
    if s is None:
        return True
    s.close()
    return False


def tomar(session_name: str, quien: str = "este bot") -> None:
    """Sale con un mensaje claro si otro proceso ya usa esta sesión."""
    s = _abrir(session_name)
    if s is None:
        sys.exit(
            f"\nYa hay otro programa usando la sesión de Telegram '{session_name}'.\n"
            "Puede ser el bot de Android, el del iPhone o listar_chats.py: con la misma\n"
            "sesión solo puede haber UNO a la vez (dos a la vez invalidan la sesión y\n"
            "pueden pagar cada orden dos veces).\n"
            f"Ciérralo primero y vuelve a abrir {quien}.\n"
        )
    _SOCKETS.append(s)
