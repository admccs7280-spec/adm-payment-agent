"""
Orquestador del bot de pagos, usando una sesión de USUARIO (Telethon), no un
bot de la Bot API. Esto es necesario porque las órdenes llegan al chat vía
otro bot, y Telegram no deja que un bot vea mensajes de otro bot — una
cuenta de usuario normal sí los ve todos.

Flujo: mensaje en el canal -> parsear -> validar -> ENCOLAR -> un único
trabajador procesa la cola en orden de llegada, una orden a la vez -> ejecutar
en el emulador -> SIEMPRE enviar al canal la captura de la última pantalla,
sea éxito, fallo o estado incierto.

Cola: cada mensaje válido se agrega a una asyncio.Queue; un solo trabajador
la consume. Mientras una orden se está pagando, las siguientes esperan en
la cola — nunca dos pagos tocan el emulador al mismo tiempo, y ninguna orden
se salta a otra (FIFO estricto). Si el proceso se reinicia, la cola (en
memoria) se pierde: las órdenes pendientes hay que reenviarlas.
"""
import asyncio
import atexit
import logging
import os
import time
from datetime import datetime, timezone

from telethon import TelegramClient, errors, events, functions

import control_bot
import envios_pendientes
import instancia_unica
import registro_bot
import version
from config import Settings, load_settings, opcional
from ordenes import OrdenInvalida, OrdenPago, parsear_orden, validar_orden

# DISPOSITIVO en .env elige el teléfono que ejecuta los pagos: "android"
# (BlueStacks por ADB, el valor por defecto) o "iphone" (iPhone 12 por cable,
# ver ejecutor_iphone.py; requiere correr con iphone\.venv).
DISPOSITIVO = opcional("DISPOSITIVO", "android").lower()
if DISPOSITIVO == "iphone":
    from ejecutor_iphone import (ConfiguracionIPhone, avisos_banco_pendientes, ejecutar_pago,
                                 esperar_dispositivo_listo, vigilar_avisos_banco, volver_inicio_tras_pago)
elif DISPOSITIVO == "android":
    from adb_utils import esperar_dispositivo_listo
    from ejecutor import ejecutar_pago, volver_inicio_tras_pago

    class ConfiguracionIPhone(RuntimeError):     # solo existe en el iPhone; aquí nunca se lanza
        pass

    def avisos_banco_pendientes():       # Android no lee los avisos finales del banco
        return []

    def vigilar_avisos_banco(*_a, **_k):
        return None
else:
    raise SystemExit(f"DISPOSITIVO={DISPOSITIVO!r} no es válido: usa 'android' o 'iphone'")

logging.basicConfig(level=logging.INFO, format=registro_bot.FORMATO)
log = logging.getLogger("orquestador")

# ids de orden ya ejecutados (o encolados) en esta sesión, para evitar duplicados
ORDENES_PROCESADAS: set[str] = set()


async def main():
    settings = load_settings()
    # Una sola sesión de Telegram para los dos teléfonos: solo un bot a la vez.
    instancia_unica.tomar(settings.session_name, f"el bot de {DISPOSITIVO}")
    # Estado visible para el acceso directo de iniciar/apagar (control_bot.py).
    # Los restos de un cierre brusco anterior se borran al arrancar y al salir.
    control_bot.limpiar()
    atexit.register(control_bot.limpiar)
    estado = control_bot.EstadoBot()
    estado.publicar()
    apagando = False   # True mientras haya un pedido de apagado: no se aceptan órdenes nuevas
    # Órdenes con fecha anterior se ignoran. Se fija con la hora del SERVIDOR de Telegram al
    # conectar (más abajo): las fechas de los mensajes son del servidor, y con el reloj o la zona
    # horaria de la PC mal (visto el 2026-10-25: zona UTC-6 con la hora de Venezuela, la PC iba
    # 2 h adelantada en UTC) TODAS las órdenes parecían "anteriores al arranque" y se ignoraban.
    arranque = datetime.now(timezone.utc)
    client = TelegramClient(settings.session_name, settings.api_id, settings.api_hash)
    if not settings.usuarios_permitidos:
        log.warning(
            "USER_IDS_PERMITIDOS está vacío: CUALQUIER mensaje con formato de orden "
            "en el canal se ejecutará sin confirmación. Llénalo antes de operar en real."
        )
    if settings.monto_maximo_auto <= 0:
        log.warning("MONTO_MAXIMO_AUTO=0: sin tope de monto, se paga cualquier monto que llegue.")

    # Espera a que el emulador responda de verdad ANTES de conectar a
    # Telegram: si se arranca el bot justo tras reiniciar la PC, BlueStacks
    # puede tardar en levantar. Sin esta espera, una orden que llega en ese
    # hueco encuentra el emulador sin responder y falla completa, sin
    # captura (visto en vivo el 2026-09-30). Conectar a Telegram recién
    # cuando el emulador ya contesta evita que se acepten órdenes que no se
    # pueden ejecutar todavía.
    log.info("Bot iPhone 12, versión %s.", version.VERSION)
    log.info("Dispositivo: %s. Esperando a que responda...", DISPOSITIVO)
    try:
        serial_listo = await asyncio.to_thread(
            esperar_dispositivo_listo, settings.serial_adb or "", 600.0, 8.0, log.info
        )
    except ConfiguracionIPhone as exc:
        log.error("NO SE INICIA: %s", exc)
        raise SystemExit(1)
    log.info("Dispositivo listo (%s).", serial_listo)
    estado.iphone = serial_listo          # lo muestra el panel (tarjeta iPhone)
    estado.publicar()

    # Una sola cola por ejecución del proceso, y un solo consumidor.
    cola_ordenes: asyncio.Queue = asyncio.Queue()

    @client.on(events.NewMessage(chats=settings.chat_id_ordenes))
    async def manejar_mensaje(event):
        # Al conectar, Telegram entrega también los mensajes que llegaron
        # con el bot apagado ("Got difference"). Esas órdenes NO se pagan:
        # mientras el bot está apagado las atiende el usuario a mano, y
        # pagarlas aquí sería pagarlas dos veces (visto en vivo 2026-09-23).
        if event.message.date < arranque:
            log.info("Mensaje anterior al arranque (%s), ignorado.", event.message.date)
            return
        texto = event.message.message or ""
        remitente_id = event.sender_id or 0
        orden = parsear_orden(texto, remitente_id, event.message.id)
        log.info(
            "Mensaje recibido de user_id=%s tipo=%s banco=%r texto=%r",
            event.sender_id, orden.tipo if orden else None,
            orden.banco if orden else None, texto,
        )
        if orden is None:
            return  # no es una orden de pago, se ignora en silencio

        if orden.id_orden in ORDENES_PROCESADAS:
            # Duplicada: se ignora sin responder en el chat (solo queda en
            # este log). El chat solo recibe UN mensaje por orden: la
            # captura final — esto no cuenta como esa orden.
            log.info("Orden %s duplicada, ignorada (sin aviso en el chat).", orden.id_orden)
            return

        # Remitente fuera de la lista: se ignora SIN responder. En un grupo con
        # órdenes de otros (u otro bot escuchándolo), contestar "rechazada" a cada
        # orden ajena llenaría el chat de ruido. No se marca como procesada.
        if settings.usuarios_permitidos and orden.remitente_id not in settings.usuarios_permitidos:
            log.info("Orden %s de remitente no autorizado (%s): ignorada sin responder.",
                     orden.id_orden, orden.remitente_id)
            return

        # Apagado ordenado en curso: no se acepta nada nuevo, pero se AVISA (callar
        # dejaría a quien manda la orden esperando un pago que nunca va a salir).
        # No se marca como procesada: reenviada con el bot encendido, se ejecuta.
        if apagando:
            log.info("Orden %s no aceptada: el bot se está apagando.", orden.id_orden)
            await event.reply("🛑 Orden NO ejecutada: el bot se está apagando. "
                              "Reenvíala cuando esté encendido.")
            return

        try:
            validar_orden(orden, settings.usuarios_permitidos, settings.monto_maximo_auto)
        except OrdenInvalida as exc:
            log.info("Orden %s rechazada: %s", orden.id_orden, exc)   # el panel la lista en "Actividad"
            await event.reply(f"🛑 Orden rechazada: {exc}")
            return

        # Se marca como procesada ANTES de encolar y nunca se desmarca, para
        # que una orden fallida o incierta no se reintente sola si se reenvía.
        ORDENES_PROCESADAS.add(orden.id_orden)
        cola_ordenes.put_nowait(orden)
        estado.en_cola = cola_ordenes.qsize()
        estado.publicar()
        log.info("Orden %s encolada. Pendientes: %s", orden.id_orden, cola_ordenes.qsize())

    async def trabajador():
        """Único consumidor de la cola: procesa una orden a la vez, en el
        orden en que llegaron. Una excepción de una orden no detiene al
        trabajador ni pierde las siguientes."""
        while True:
            orden = await cola_ordenes.get()
            # Sin ningún await entre el get() y esto: el estado nunca queda "libre"
            # mientras hay una orden en manos del trabajador.
            estado.orden, estado.desde = orden.id_orden, time.time()
            estado.en_cola = cola_ordenes.qsize()
            estado.publicar()
            try:
                await _ejecutar_orden(client, settings, orden, hay_mas=lambda: not cola_ordenes.empty())
            except Exception as exc:
                log.exception("Error inesperado procesando la orden %s", orden.id_orden)
                # Sin esto la orden queda sin ninguna respuesta en el chat
                # (visto en vivo el 2026-09-25 con adb inaccesible: ni la
                # captura se pudo tomar). No se sabe en qué tramo falló, así
                # que no se afirma que el dinero no salió.
                aviso = envios_pendientes.nuevo(
                    orden.id_orden, settings.chat_id_capturas, orden.mensaje_id,
                    f"⚠️ Error inesperado. ID: {orden.id_orden}\n{exc}\n"
                    "No se pudo confirmar el resultado: revisa el banco antes de reintentar.")
                try:
                    await envios_pendientes.enviar(client, aviso, timeout=envios_pendientes.PRIMER_INTENTO_SEG)
                except Exception as exc_envio:
                    envios_pendientes.agregar(aviso)
                    log.warning("Orden %s: resultado NO enviado a Telegram (%s). Queda guardado y se "
                                "reintenta solo.", orden.id_orden, envios_pendientes._motivo(exc_envio))
            finally:
                cola_ordenes.task_done()
                estado.orden, estado.desde = None, None
                estado.en_cola = cola_ordenes.qsize()
                estado.publicar()

    async def vigilar_apagado():
        """Atiende el pedido de apagado ordenado (control_bot.py crea parar_bot.flag):
        deja de aceptar órdenes y, cuando no queda ninguna en curso ni en cola, se
        desconecta de Telegram. Nunca corta una orden a medias."""
        nonlocal apagando
        while True:
            await asyncio.sleep(1.0)
            pedido = control_bot.parada_pedida()
            if pedido != apagando:
                apagando = pedido
                estado.fase = "apagando" if pedido else "escuchando"
                estado.publicar()
                if pedido:
                    log.info("Apagado pedido: no se aceptan órdenes nuevas; el bot se cierra "
                             "al terminar las pendientes (%s en curso, %s en cola).",
                             estado.orden or "ninguna", estado.en_cola)
                else:
                    log.info("Apagado cancelado: el bot sigue recibiendo órdenes.")
            if apagando and estado.libre:
                log.info("Sin órdenes pendientes: desconectando de Telegram y cerrando.")
                await client.disconnect()
                return

    async def reenviar_pendientes():
        """Resultados que Telegram no aceptó (caída de Telegram, sin internet): se reintentan
        solos. Al arrancar se mandan los que quedaron de la vez anterior."""
        while True:
            if envios_pendientes.cargar():
                try:
                    await envios_pendientes.reintentar(client, log)
                except Exception:
                    log.exception("Error reintentando los resultados pendientes")
            await asyncio.sleep(envios_pendientes.INTERVALO_SEG)

    async def avisar_fallidas():
        """Transferencias a otros bancos que el banco marcó FALLIDAS después del comprobante
        (el bot ya había dado ✅): se dice en el grupo, como respuesta a la orden."""
        while True:
            await asyncio.sleep(2.0)
            for aviso in avisos_banco_pendientes():
                await _avisar_transferencia_fallida(client, settings, aviso)

    tarea_trabajador = asyncio.create_task(trabajador(), name="pc2-orden-worker")

    log.info("Escuchando canal %s como usuario...", settings.chat_id_ordenes)
    arranque = await _conectar_telegram(client, settings, arranque)
    if arranque is None:          # se pidió apagar mientras se esperaba a Telegram
        tarea_trabajador.cancel()
        try:
            await tarea_trabajador
        except asyncio.CancelledError:
            pass
        return
    log.info("Conectado. Esperando órdenes...")
    estado.fase = "escuchando"
    estado.publicar()
    tarea_apagado = asyncio.create_task(vigilar_apagado(), name="vigilar-apagado")
    tarea_reenvio = asyncio.create_task(reenviar_pendientes(), name="reenviar-pendientes")
    tarea_fallidas = asyncio.create_task(avisar_fallidas(), name="avisar-fallidas")
    try:
        await client.run_until_disconnected()
    finally:
        for aviso in avisos_banco_pendientes():           # lo detectado justo antes de cerrar
            await _avisar_transferencia_fallida(client, settings, aviso)
        pendientes = len(envios_pendientes.cargar())
        if pendientes:
            log.warning("%s resultado(s) siguen sin enviar a Telegram: se mandan solos al volver a "
                        "encender el bot (envios_pendientes.json).", pendientes)
        for tarea in (tarea_apagado, tarea_reenvio, tarea_fallidas, tarea_trabajador):
            tarea.cancel()
            try:
                await tarea
            except asyncio.CancelledError:
                pass
        if not cola_ordenes.empty():
            log.warning(
                "%s orden(es) quedaron sin procesar al cerrar (la cola es en "
                "memoria y no se recupera al reiniciar).", cola_ordenes.qsize(),
            )


# Esperas entre intentos de conectar a Telegram (después se repite la última).
ESPERA_TELEGRAM_SEG = (5, 10, 20, 30, 60)


async def _conectar_telegram(client: TelegramClient, settings: Settings, arranque: datetime) -> datetime | None:
    """Conecta, toma la hora del servidor e inicia la sesión. Si Telegram no responde, REINTENTA
    en vez de cerrar el bot (visto el 2026-10-26 desde las 9:26: el centro de datos DC1 de Telegram,
    el de esta cuenta, respondía a ratos "RPCError -500: No workers running" y el bot se cerraba
    al arrancar). Mientras tanto no se acepta ninguna orden. Devuelve None si se pidió apagar."""
    intento = 0
    while True:
        try:
            await client.connect()
            arranque = await _hora_servidor(client, arranque)
            await client.start(phone=settings.telefono)
            if intento:
                log.info("Telegram respondió al intento %s.", intento + 1)
            return arranque
        except (errors.ServerError, OSError, asyncio.TimeoutError) as exc:
            espera = ESPERA_TELEGRAM_SEG[min(intento, len(ESPERA_TELEGRAM_SEG) - 1)]
            intento += 1
            log.warning("Telegram no responde al conectar (%s). Reintento %s en %s s; el bot no acepta "
                        "órdenes hasta conectar.", " ".join(str(exc).split())[:160] or type(exc).__name__,
                        intento, espera)
            try:
                await client.disconnect()
            except Exception:
                pass
            for _ in range(int(espera * 10)):
                if control_bot.parada_pedida():
                    log.info("Apagado pedido mientras se esperaba a Telegram: cerrando.")
                    return None
                await asyncio.sleep(0.1)


async def _hora_servidor(client: TelegramClient, respaldo: datetime) -> datetime:
    """Hora actual según Telegram (help.getConfig trae la fecha del servidor). Si no se puede
    leer, se usa la de la PC, como antes."""
    try:
        servidor = (await client(functions.help.GetConfigRequest())).date
    except Exception as exc:
        log.warning("No se pudo leer la hora de Telegram (%s): se usa la de la PC.", exc)
        return respaldo
    desfase = (respaldo - servidor).total_seconds()
    if abs(desfase) > 60:
        log.warning("El reloj de la PC está desfasado %+.0f s respecto de Telegram (revisa la hora y la "
                    "zona horaria de Windows). El bot usa la hora de Telegram.", desfase)
    return servidor


async def _avisar_transferencia_fallida(client: TelegramClient, settings: Settings, aviso: dict) -> None:
    """Respuesta en el grupo cuando el banco marca FALLIDA una transferencia que el bot ya había
    dado como pagada. Si Telegram no responde, queda en envios_pendientes y se reintenta sola."""
    id_orden = aviso.get("id_orden") or "sin identificar"
    log.warning("Orden %s: el banco informó que la transferencia FALLÓ (operación %s).",
                id_orden, aviso.get("referencia") or "?")
    texto = (f"⚠️ TRANSFERENCIA FALLIDA según el banco. ID: {id_orden}\n"
             f"Operación: {aviso.get('referencia') or '?'}\n"
             "El banco avisó DESPUÉS del comprobante que esta transferencia a otro banco FALLÓ: "
             "el ✅ anterior queda sin efecto. Revísala en el banco y págala de nuevo a mano.")
    if aviso.get("texto"):
        texto += f"\nAviso del banco: {aviso['texto']}"
    pendiente = envios_pendientes.nuevo(str(id_orden), settings.chat_id_capturas, aviso.get("mensaje_id"), texto)
    try:
        await envios_pendientes.enviar(client, pendiente, timeout=envios_pendientes.PRIMER_INTENTO_SEG)
    except Exception as exc:
        envios_pendientes.agregar(pendiente)
        log.warning("Orden %s: resultado NO enviado a Telegram (%s). Queda guardado y se reintenta solo.",
                    id_orden, envios_pendientes._motivo(exc))


async def _ejecutar_orden(client: TelegramClient, settings: Settings, orden: OrdenPago, hay_mas=lambda: False):
    """Ejecuta la orden y manda UN SOLO mensaje al chat, pase lo que pase:
    la captura de la última pantalla, con el resultado en el pie de foto,
    como RESPUESTA al mensaje de la orden original. Sin aviso de "en cola",
    sin aviso de "ejecutando": la orden llega, se procesa, y esa captura es
    la única respuesta — buena o mala."""
    # ejecutar_pago es bloqueante (usa subprocess/adb) -> se corre en un hilo
    # aparte para no congelar el listener de Telegram mientras se ejecuta.
    # La cola de arriba ya garantiza que solo una orden llega hasta aquí a
    # la vez; esto solo evita que Telegram se congele mientras tanto.
    t0 = time.perf_counter()
    resultado = await asyncio.to_thread(
        ejecutar_pago, orden, settings.serial_adb or None, settings.bdv_password
    )
    t_pago = time.perf_counter() - t0
    log.info("Orden %s ejecutada en %.1f s: %s", orden.id_orden, t_pago,
             "ÉXITO" if resultado.exito else ("INCIERTO" if resultado.incierto else "FALLÓ"))
    # Detalle en una sola línea (lo muestra el panel en "Actividad"): la referencia o el motivo.
    detalle = (f"referencia {resultado.referencia}" if resultado.exito and resultado.referencia
               else resultado.mensaje)
    log.info("Orden %s detalle: %s", orden.id_orden, " ".join(str(detalle).split()))

    if resultado.exito:
        titulo = f"✅ Pago ejecutado. ID: {orden.id_orden}"
        if resultado.referencia:
            titulo += f"\nReferencia: {resultado.referencia}"
    elif resultado.incierto:
        titulo = f"🟡 ESTADO INCIERTO. ID: {orden.id_orden}\n{resultado.mensaje}"
    else:
        titulo = f"🛑 Falló. ID: {orden.id_orden}\n{resultado.mensaje}"
    if resultado.via_transferencia:
        titulo += "\n(por transferencia — límite de Pago Móvil agotado)"

    if not resultado.ruta_captura and not resultado.mensaje.startswith("NO SE INTENTÓ"):
        # capturar_pantalla() ya falló (dispositivo sin responder) y devolvió '':
        # ni se intenta subir un archivo, se avisa con el texto.
        titulo += "\n⚠️ No se pudo tomar la captura de pantalla (el dispositivo/emulador no respondió)."
    aviso = envios_pendientes.nuevo(orden.id_orden, settings.chat_id_capturas, orden.mensaje_id,
                                    titulo, resultado.ruta_captura)

    # La vuelta a Inicio arranca YA, a la par del envío a Telegram (la captura ya está
    # guardada en disco: el teléfono puede dejar el comprobante). Antes se esperaba a que la
    # captura subiera (~1 s) y en ese tiempo llegaba la notificación del banco, que tapaba
    # la flecha "<": ~15 s perdidos por orden o, si el toque caía sobre ella, el bot quedaba
    # atascado en "Detalles de la notificación" (2026-10-26). Igual que antes, la orden
    # siguiente no empieza hasta que el teléfono volvió a Inicio.
    volver = None
    if resultado.exito or resultado.volver_inicio_pendiente:
        volver = asyncio.create_task(asyncio.to_thread(volver_inicio_tras_pago, settings.serial_adb or None))

    t1 = time.perf_counter()
    try:
        await envios_pendientes.enviar(client, aviso, timeout=envios_pendientes.PRIMER_INTENTO_SEG)
        log.info("Orden %s: captura enviada a Telegram en %.1f s.", orden.id_orden, time.perf_counter() - t1)
    except Exception as exc:
        # El pago ya terminó (con el resultado que sea): lo que falló es SOLO el mensaje. Se guarda
        # en disco y se reintenta solo (envios_pendientes.py); la cola sigue sin esperar a Telegram.
        envios_pendientes.agregar(aviso)
        log.warning("Orden %s: resultado NO enviado a Telegram (%s). Queda guardado y se reintenta "
                    "solo cada %s s hasta que Telegram responda.", orden.id_orden,
                    envios_pendientes._motivo(exc), envios_pendientes.INTERVALO_SEG)

    if volver is not None:
        # Antes de que el trabajador tome la siguiente orden, el teléfono tiene que estar en
        # Inicio (volver_inicio_tras_pago nunca lanza).
        await volver
    if resultado.exito and getattr(resultado, "interbancaria", False):
        # Transferencia a otro banco: el banco confirma unos segundos DESPUÉS ("exitosa" o
        # "fallida"). Con la cola vacía se mira la pantalla un rato, sin tocar nada; si llega otra
        # orden se corta y las lecturas de esa orden ven el aviso igual.
        await asyncio.to_thread(vigilar_avisos_banco, 20.0, hay_mas, settings.serial_adb or None)


if __name__ == "__main__":
    # Desde el panel toda la salida ya va a logs\bot.log; desde la consola se agrega el archivo.
    if os.environ.get("BOT_DESDE_PANEL") != "1":
        registro_bot.preparar_consola()
    asyncio.run(main())
