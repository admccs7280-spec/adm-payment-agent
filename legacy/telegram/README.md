# Bot actual con Telegram — referencia saneada

Esta carpeta preserva el código del bot que ADM usa hoy como punto de partida para estandarizar las PCs. Conserva el
camino Telegram actual y, entre otros, incluye:

- `orquestador.py`: recepción y coordinación mediante Telegram;
- `ordenes.py`: lectura y normalización de órdenes;
- `ejecutor.py` y `adb_utils.py`: ejecución para Android/emulador;
- `ejecutor_iphone.py` e `iphone/`: ejecución para iPhone;
- `panel_bot.pyw`, utilidades, scripts de inicio y pruebas.

## Qué no está aquí

No se versionan `.env`, sesiones Telegram, números de chats, contraseñas, claves de banco, registros de teléfonos,
capturas, logs, estados, bases locales ni respaldos. Los archivos de ejemplo son solo marcadores seguros y no sirven
para operar hasta que cada PC se configure de forma local y autorizada.

## Uso durante la transición

Esta es una **referencia**, no una instrucción para sustituir la carpeta de un bot que está pagando. No ejecutar este
código contra una cuenta o teléfono real desde el repositorio. Cualquier ajuste se hace primero en una copia de
laboratorio, sin envío de dinero, y después se publica como una versión revisada.

La ruta de futuro no reutiliza Telegram: está en [`../../src/adm_payment_agent/`](../../src/adm_payment_agent/).
