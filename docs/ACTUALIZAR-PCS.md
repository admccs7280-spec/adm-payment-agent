# Actualizar los agentes de pago en las PCs

## Regla principal

Todas las PCs ejecutan una versión etiquetada del mismo repositorio. Ninguna PC se modifica a mano para "arreglar"
un caso local: ese hallazgo vuelve al repositorio, se prueba y se libera como una nueva versión.

## Primera instalación de una PC piloto

1. Iniciar sesión en GitHub Desktop con la cuenta autorizada de ADM y clonar el repositorio privado
   `adm-payment-agent`.
2. Instalar Python 3.11 y crear el entorno del paquete.
3. Guardar la configuración del dispositivo, las credenciales y las capturas fuera de la carpeta clonada.
4. Ejecutar las pruebas locales antes de conectar un dispositivo real.
5. Portar y probar un adaptador únicamente en una copia de laboratorio; la primera versión no debe activar pagos desde
   ADM.

## Actualización ordinaria

1. Verificar en el panel que el agente no tiene una orden en curso ni una cola pendiente.
2. Solicitar apagado ordenado y conservar la configuración local fuera de Git.
3. Obtener la nueva etiqueta liberada, por ejemplo `vX.Y.Z`; no usar cambios sin etiqueta para una PC operativa.
4. Ejecutar las pruebas de esa versión y el chequeo del dispositivo sin enviar pagos.
5. Reiniciar el agente, comprobar versión y latido, y registrar el resultado en ADM.
6. Si algo falla, volver a la etiqueta anterior; no editar archivos de código en esa PC.

## Qué nunca sube a GitHub

- claves, tokens, sesiones o archivos `.env`;
- capturas, registros, bases SQLite y outbox local;
- números completos de cuentas o datos de clientes;
- coordenadas/calibración específica de una pantalla que todavía no haya pasado por el proceso de adaptador.

## Estado de `v0.1.0`

Es una base de cola durable y pruebas. No contiene adaptadores de banco, integración Binance ni transporte de red;
por eso todavía no reemplaza un bot que esté pagando en producción.
