"""Carga de configuración desde .env (nunca subas .env ni el .session a git)."""
import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parent


def _cargar_env():
    env = RAIZ / ".env"
    if env.exists():
        # utf-8-sig: el panel escribe el .env en UTF-8 y PowerShell en UTF-8 con BOM; con la
        # codificación por defecto de Windows (cp1252) una tilde en un valor llegaba cambiada.
        for linea in env.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            clave, valor = linea.split("=", 1)
            os.environ.setdefault(clave.strip(), valor.split("#")[0].strip())


_cargar_env()


def requerido(nombre: str) -> str:
    valor = os.environ.get(nombre, "").strip()
    if not valor:
        raise RuntimeError(f"falta la variable {nombre} en .env o el entorno")
    return valor


def opcional(nombre: str, default: str = "") -> str:
    return os.environ.get(nombre, default).strip()


class Settings:
    def __init__(self):
        # Credenciales de la app de Telegram (de my.telegram.org)
        self.api_id = int(requerido("API_ID"))
        self.api_hash = requerido("API_HASH")
        self.telefono = requerido("TELEFONO")  # con código de país, ej +58414...
        self.session_name = str(RAIZ / opcional("SESSION_NAME", "telegram_local_pc2"))
        self.cuenta_debitar_desc = requerido("CUENTA_DEBITAR_DESC")

        # Misma sesión de Telegram para los dos teléfonos, pero cada uno puede
        # escuchar su propio grupo: con DISPOSITIVO=iphone se prefieren
        # CHAT_ID_ORDENES_IPHONE / CHAT_ID_CAPTURAS_IPHONE y, si no están, los de siempre.
        sufijo = "_IPHONE" if opcional("DISPOSITIVO", "android").lower() == "iphone" else ""
        ordenes_str = opcional(f"CHAT_ID_ORDENES{sufijo}", "") or requerido("CHAT_ID_ORDENES")
        self.chat_id_ordenes = int(ordenes_str)
        chat_capturas_str = opcional(f"CHAT_ID_CAPTURAS{sufijo}", "") or opcional("CHAT_ID_CAPTURAS", "")
        self.chat_id_capturas = int(chat_capturas_str) if chat_capturas_str else self.chat_id_ordenes

        # Igual que los grupos: con DISPOSITIVO=iphone se prefieren los valores
        # *_IPHONE (tope y remitentes propios, para pruebas) sin tocar los de Android.
        permitidos = opcional(f"USER_IDS_PERMITIDOS{sufijo}", "") or opcional("USER_IDS_PERMITIDOS", "")
        self.usuarios_permitidos = {int(u) for u in permitidos.split(",") if u.strip()}

        self.bdv_password = requerido("BDV_PASSWORD")
        self.serial_adb = opcional("SERIAL_ADB", "")  # vacío = autodetectar
        tope = opcional(f"MONTO_MAXIMO_AUTO{sufijo}", "") or opcional("MONTO_MAXIMO_AUTO", "50")
        self.monto_maximo_auto = float(tope)


def load_settings() -> Settings:
    return Settings()
