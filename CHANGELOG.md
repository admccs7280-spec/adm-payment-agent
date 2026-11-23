# Historial de versiones

## v0.1.1 — 2026-11-23

- Se separan explícitamente las dos rutas de ADM Automatizado.
- Se incorpora `legacy/telegram/` como referencia saneada del bot actual: orquestador, ejecutores Android/iPhone,
  panel, scripts, dependencias y pruebas.
- Se excluyen credenciales, sesiones, registros de teléfonos, capturas, estados, bases locales y respaldos.
- `src/adm_payment_agent/` se mantiene como la ruta futura sin Telegram.

## v0.1.0 — 2026-11-23

- Núcleo durable inicial: cola SQLite, idempotencia, estados seguros y outbox local para el agente futuro.
