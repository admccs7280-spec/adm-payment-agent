# Qué usa el bot Telegram actual

Esta referencia conserva el árbol heredado para no romper los imports simples ni los caminos que el panel resuelve
desde la carpeta raíz. El generador `tools/build_telegram_package.py` crea una copia más limpia, con `runtime/`,
accesos de inicio y documentación separados, sin tocar una PC que esté pagando.

## Runtime que no se debe separar todavía

`orquestador.py` coordina Telegram y carga `config.py`, `ordenes.py`, `control_bot.py`, `ejecutor.py`,
`ejecutor_iphone.py`, `envios_pendientes.py`, `registro_bot.py`, `registro_iphones.py`, `bancos.py`,
`instancia_unica.py` y `version.py`.

Para Android, el ejecutor usa `adb_utils.py`. Para iPhone, `ejecutor_iphone.py` añade `iphone/` al camino de Python y
carga `iphone_utils.py` y `dispositivo.py` como módulos superiores; por eso esos archivos viven juntos en el runtime.
El panel usa además `plataforma.py` y `herramientas_iphone.py`.

## Mantenimiento; no arranca solo

`diag_adb.py`, `inspeccionar.py`, `listar_chats.py`, `login_telegram.py`, `reset_pantalla.py`, `empaquetar.py`, las
herramientas de exploración/calibración de iPhone y los tests sirven para instalación, diagnóstico o desarrollo. Se
mantienen en el paquete por trazabilidad, pero no son parte del arranque normal.

## Archivos locales excluidos

Nunca se copian o versionan `.env`, sesiones, `iphones.json`, claves del panel, capturas, logs, estados, bases locales,
respaldos o entornos virtuales. El bot productivo no se reorganiza directamente: se prueba primero el paquete limpio.
