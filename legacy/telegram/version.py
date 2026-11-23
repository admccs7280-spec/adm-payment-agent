"""Versión del bot del iPhone 12. Igual en todas las PC: si dos PC difieren, una está desactualizada."""
VERSION = "2026-11-01.1"
NOVEDADES = (
    "Campos vacíos vs. con valor: umbral del rótulo 0.78 (antes 0.88 tocaba el borde de Documento/Teléfono/Nombre en iPhone con texto más chico).",
    "Selector de bancos tolerante al OCR (O174 por 0174) y, si el banco no aparece, el error lista los bancos que vio.",
    "Registro de teléfonos (iphones.json + pestaña Teléfonos del panel): cada iPhone se reconoce por su UDID con su cuenta y su clave; cambiar de teléfono ya no toca el .env.",
    "Cuenta enmascarada (0102****0756) legible: se lee por tramos cuando el OCR de Windows la devuelve vacía (campo Cuenta a debitar y fila Destino de la revisión).",
    "Tipo de documento por forma de la letra (V/E/J/P/G/C), sin depender de la posición ni del tema claro/oscuro.",
    "Nombre del beneficiario: doble toque y re-llenado si iOS se traga el primer toque.",
    "Pagos más rápidos: esperas recortadas (~27 s hasta la pantalla de revisión en otros bancos).",
    "Tipo de documento (V/E/P/J/G/C) funciona en modo claro y oscuro y con la lista abierta en cualquier posición.",
    "Reconoce y prepara cualquier iPhone 12 que se conecte (monta la imagen de desarrollador sola).",
    "Cambio de iPhone sin tocar código: apagar el bot, cambiar el teléfono, cuenta y clave en Configuración, iniciar.",
    "Verifica al arrancar que la cuenta del iPhone sea la de CUENTA_DEBITAR_DESC.",
    "La conexión fallida ya no deja el túnel abierto (el bot no se queda sin arrancar).",
    "Freno de clave (2 rechazos seguidos), código del iPhone/Face ID, avisos del banco en interbancarias.",
)
