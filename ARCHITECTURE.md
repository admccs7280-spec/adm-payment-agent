# Dos rutas para ADM Automatizado

Este repositorio separa explícitamente la operación actual de la arquitectura que la reemplazará. Las dos conviven
durante la transición, pero no se instalan ni evolucionan del mismo modo.

## 1. `legacy/telegram/`: operación actual con Telegram

Es una **referencia saneada de código** tomada del bot operativo más completo de la familia actual. Conserva la ruta que
está funcionando hoy:

```text
Telegram → orquestador.py → ordenes.py → ejecutor.py / ejecutor_iphone.py → teléfono o emulador
```

Incluye el orquestador Telegram, parser de órdenes, ejecución Android/ADB, ejecución iPhone, panel y pruebas. Sirve
para auditar, comparar las variantes de las PCs y extraer piezas hacia el agente estándar.

No contiene ni contendrá datos de una PC real: `.env`, sesión Telegram, chats, claves bancarias, registros de iPhone,
capturas, logs, estados, respaldos ni bases locales. Por eso es una referencia de código, no una carpeta lista para
copiar encima de un bot en producción. Las PCs que hoy pagan continúan sin cambios hasta una migración probada.

## 2. `src/adm_payment_agent/`: destino sin Telegram

Es el nuevo agente estándar. Su flujo objetivo es:

```text
Binance / cliente
        ↓
bot controlador ADM
        ↓ API autenticada, directa y con idempotencia
agente de pago de cada PC
        ↓
teléfono o emulador
        ↓ resultado + captura mediante outbox durable
bot controlador ADM
```

El controlador será dueño de la orden Binance, conversación con el cliente, marcado de pago y entrega de la captura.
El agente local solo acepta un trabajo identificado, ejecuta una vez y devuelve un resultado verificable. No habrá
dependencia de Telegram en esta ruta.

El núcleo ya implementa SQLite local, recuperación ante reinicio, idempotencia, estados y outbox. Aún no integra la
API real del controlador ni un adaptador bancario: esas piezas se incorporarán desde `legacy/telegram/` en copias de
laboratorio y con pruebas que no envían dinero.

## Reglas de transición

1. No se modifica un bot productivo desde este repositorio ni se ejecuta una prueba que pague dinero.
2. Cada componente extraído de `legacy/telegram/` se prueba primero sin enviar ni confirmar pagos.
3. Configuración, sesiones y claves viven solo en cada PC y fuera de Git.
4. Una PC se actualiza únicamente con una versión etiquetada y un procedimiento de reversión probado.
5. `legacy/telegram/` queda congelado salvo correcciones necesarias para mantener la operación actual; las mejoras de
   arquitectura nuevas van al agente sin Telegram.
