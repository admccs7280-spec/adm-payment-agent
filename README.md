# ADM Payment Agent

Base única para los agentes de pago que viven en las PCs con teléfono o emulador de ADM. El repositorio contiene dos
rutas separadas para que la operación actual no se mezcle con la migración:

| Ruta | Propósito | Telegram |
| --- | --- | --- |
| [`legacy/telegram/`](legacy/telegram/README.md) | Referencia saneada del bot que hoy opera en las PCs. Contiene el orquestador y los adaptadores actuales. | Sí |
| [`src/adm_payment_agent/`](src/adm_payment_agent/) | Núcleo del agente ADM futuro: trabajos durables, idempotencia y salida directa al controlador. | No |

Lee [`ARCHITECTURE.md`](ARCHITECTURE.md) antes de instalar, mover o modificar cualquiera de las dos rutas.

## Ruta futura sin Telegram

Esta primera versión del núcleo futuro **no controla un banco, no llama Binance, no abre Telegram y no puede ejecutar
pagos**. Implementa la base que todas las PCs deben compartir antes de portar adaptadores reales:

- trabajos con identificador inmutable y protección contra duplicados;
- SQLite local para cola y recuperación tras reinicio;
- estados de negocio: aceptada, ejecutando, pago verificado, captura recibida, fallo limpio e incierto;
- outbox durable de eventos/capturas para entregar al controlador ADM;
- regla estricta: una interrupción o fallo tras iniciar ejecución acaba en `INCIERTO`; no hay reintento automático.

## Estructura de la ruta futura

```text
src/adm_payment_agent/
  models.py    contrato de trabajo y estados
  store.py     SQLite, idempotencia y outbox
  agent.py     orquestación de un trabajo a la vez
tests/         simuladores sin teléfono, banco ni red
```

Los adaptadores de Android, iPhone y BDV se portarán de la referencia `legacy/telegram/`, uno por uno, después de
cubrirlos con pruebas sin envío. Nunca copies al repositorio `.env`, sesiones, capturas, registros, calibración de
pantallas ni credenciales.

## Prueba local

```bash
python3.11 -m unittest discover -s tests -v
```

## Publicación y actualización

El repositorio es privado. Cada versión aprobada se etiqueta; las PCs instalan una versión liberada y mantienen su
configuración local fuera de Git. Los cambios no se hacen directamente en una PC de operación: se prueban aquí, se
publican como una nueva versión y luego se despliegan de forma igual en todas las PCs.
