"""
Funciones de bajo nivel para controlar la app bancaria en el emulador vía ADB.

Todo esto es agnóstico al emulador (BlueStacks, LDPlayer, etc.) SALVO las
coordenadas de los campos de texto (ver config.py) y el SERIAL, que se
detecta automáticamente en detectar_serial().
"""
import re
import shlex
from config import opcional

ADB_PATH = opcional("ADB_PATH", "adb")
import subprocess
import time

PAQUETE = "com.bancodevenezuela.bdvdigital"

# Ninguna llamada a `adb` puede tardar más que esto. Sin un timeout, un
# comando adb colgado (visto en vivo el 2026-09-30: el arranque se quedó
# trabado en "Esperando a que el emulador responda..." mucho más de los 3
# minutos configurados) bloquea el hilo para SIEMPRE -- el bucle de reintento
# de más arriba nunca vuelve a chequear su propio límite de tiempo porque
# está esperando esa única llamada que nunca termina. subprocess.run() sin
# `timeout=` puede colgarse así si el servidor adb queda en mal estado
# (ej. por llamadas adb concurrentes desde otro proceso mientras este
# bucle está sondeando). Con timeout, esa llamada puntual falla rápido y
# el reintento de arriba sigue su curso normal.
TIMEOUT_ADB_SEG = 15


def detectar_serial() -> str:
    """Detecta el serial ADB del emulador conectado. Evita hardcodear
    'emulator-5554', que cambia según el emulador y cuántas instancias
    corren a la vez."""
    salida = subprocess.run(
        [ADB_PATH, "devices"], capture_output=True, text=True, timeout=TIMEOUT_ADB_SEG
    ).stdout
    dispositivos = [
        linea.split()[0]
        for linea in salida.splitlines()[1:]
        if linea.strip().endswith("device")
    ]
    if not dispositivos:
        raise RuntimeError("no hay ningún dispositivo/emulador conectado por adb")
    if len(dispositivos) > 1:
        raise RuntimeError(
            f"hay varios dispositivos conectados, especificá SERIAL en .env: {dispositivos}"
        )
    return dispositivos[0]


def _reiniciar_servidor_adb():
    """Mata el servidor local de `adb` (el próximo comando lo vuelve a
    levantar solo). Visto en vivo el 2026-09-30: tras reiniciar BlueStacks,
    el servidor de `adb` -- vivo desde hacía horas, de antes del reinicio --
    se quedó sin ver la instancia nueva: `adb devices` daba la lista
    VACÍA sin importar cuánto se esperara, no porque BlueStacks tardara en
    levantar sino porque el servidor en sí había quedado en un estado
    viejo. Ningún tiempo de espera arregla eso solo; hace falta matarlo
    para que el siguiente comando lo reinicie de cero y vuelva a enumerar
    los dispositivos. Confirmado a mano ese día: `adb kill-server` +
    `adb devices` bastó para que apareciera el emulador al instante."""
    try:
        subprocess.run([ADB_PATH, "kill-server"], capture_output=True, timeout=TIMEOUT_ADB_SEG)
    except Exception:
        pass  # si ni esto funciona, el próximo intento normal lo va a reportar


def esperar_dispositivo_listo(serial: str = "", timeout_seg: float = 600.0,
                              intervalo: float = 8.0, log=None) -> str:
    """Bloquea hasta que haya un emulador conectado por adb Y responda de
    verdad (un `nodos()` real, no solo `adb devices`) -- o hasta agotar
    `timeout_seg`. Devuelve el serial listo para usar.

    Se usa al arrancar el orquestador. Visto en vivo el 2026-09-30: tras
    reiniciar la PC, `orquestador.py` se lanzó antes de que BlueStacks
    terminara de levantar; la primera orden que llegó encontró el emulador
    sin responder (ni uiautomator ni screencap), así que falló completa y
    sin captura. `adb devices` por sí solo no alcanza como chequeo: puede
    listar el dispositivo como 'device' mientras el sistema Android de
    adentro todavía está arrancando y no atiende comandos de UI todavía.

    10 minutos, no 3: visto en vivo el mismo día, con la pantalla YA
    encendida y la app YA abierta (confirmado a ojo), `uiautomator` siguió
    sin responder más allá de los 3 minutos anteriores y recién funcionó
    unos minutos después -- que se VEA lista no significa que el servicio
    de accesibilidad que usa uiautomator ya esté enganchado, sobre todo en
    una PC recién reiniciada con todo compitiendo por CPU/disco.

    Intervalo de 8 s, no 3: visto en vivo el mismo día, el MISMO comando
    exacto (`uiautomator dump ... && cat ...`) funcionaba perfecto cada vez
    que se corría suelto a mano, pero fallaba siempre (salida vacía, sin
    error) cuando este bucle lo repetía cada ~3 s -- `uiautomator dump`
    necesita unos segundos para soltar su conexión de accesibilidad antes
    de poder arrancar de nuevo; llamarlo demasiado seguido en un bucle
    apretado lo hace fallar en silencio, algo que el uso normal (una
    llamada por acción de UI, con tiempo real entre medio) nunca había
    expuesto.

    `log`: callable tipo logging.Logger.info, opcional. Sin avisos de
    progreso, una espera larga es indistinguible de un cuelgue real -- eso
    fue exactamente la confusión en vivo ese día. Con `log`, avisa cada
    ~30 s con el motivo del último fallo, para que quien mira la consola
    sepa que sigue vivo y por qué sigue esperando.

    Cada ~45 s sin éxito, reinicia el servidor de `adb`
    (`_reiniciar_servidor_adb()`) antes de seguir reintentando. Visto en
    vivo el 2026-09-30: sin esto, `adb devices` podía devolver la lista
    vacía PARA SIEMPRE si el servidor había quedado en mal estado tras un
    reinicio de BlueStacks -- ningún monto de reintentos pasivos lo
    arregla solo, hace falta esta patada activa."""
    limite = time.time() + timeout_seg
    ultimo_error = None
    intentos = 0
    ultimo_aviso = time.time()
    ultimo_reinicio_server = time.time()
    while time.time() < limite:
        try:
            s = serial or detectar_serial()
            ControladorADB(s).nodos(intentos=1)
            return s
        except Exception as exc:
            ultimo_error = exc
            intentos += 1
            if time.time() - ultimo_reinicio_server >= 45:
                ultimo_reinicio_server = time.time()
                if log is not None:
                    log("  ... reiniciando el servidor de adb (sin dispositivo tras 45s)")
                _reiniciar_servidor_adb()
            if log is not None and time.time() - ultimo_aviso >= 30:
                ultimo_aviso = time.time()
                log(f"  ... el emulador todavía no responde ({intentos} intentos, "
                    f"último motivo: {exc})")
            time.sleep(intervalo)
    raise RuntimeError(
        f"el emulador no respondió tras {timeout_seg:.0f}s esperando "
        f"(¿BlueStacks sigue arrancando?): {ultimo_error}"
    )


class ControladorADB:
    """Agrupa todas las acciones sobre un emulador identificado por su serial."""

    def __init__(self, serial: str):
        self.serial = serial

    # ── plomería básica ────────────────────────────────────────────
    def adb(self, *args):
        subprocess.run(
            [ADB_PATH, "-s", self.serial] + list(args), check=True, timeout=TIMEOUT_ADB_SEG
        )

    def adb_out(self, *args) -> str:
        # Decodifica SIEMPRE como UTF-8, nunca con la codificación por
        # defecto del sistema (`text=True` sin más usa la del entorno — en
        # esta PC es cp1252, no UTF-8). adb manda UTF-8 real (rótulos con
        # tildes como "PagomóvilBDV", emojis, etc.); con cp1252 esos
        # caracteres se decodifican mal y dejan de coincidir con el texto
        # exacto que busca tap_desc(), aunque todo lo demás esté bien.
        r = subprocess.run(
            [ADB_PATH, "-s", self.serial] + list(args),
            capture_output=True, timeout=TIMEOUT_ADB_SEG,
        )
        salida = r.stdout.decode("utf-8", errors="replace")
        # Si no hubo stdout, cualquier stderr es del CLIENTE adb (ej. "device
        # '<serial>' not found"), no del comando remoto -- un error así nunca
        # llega al dispositivo, así que no tiene sentido buscarlo ahí. Visto
        # en vivo el 2026-09-30: con un SERIAL_ADB desactualizado en .env,
        # esto se descartaba en silencio y nodos()/capturar_pantalla()
        # reportaban "(sin salida)" sin la razón real, hasta que hubo que
        # reproducir el fallo a mano fuera del bot para verla.
        if not salida and r.stderr:
            salida = r.stderr.decode("utf-8", errors="replace")
        return salida

    def nodos(self, intentos: int = 3):
        """Lista de nodos de la pantalla actual (dump de uiautomator).
        Reintenta porque el dump falla intermitentemente en varios emuladores.

        Un solo proceso adb por lectura (antes eran tres: rm, dump y cat).
        El `rm` previo se mantiene para no leer nunca un volcado viejo, y
        `&&` hace que solo se lea el archivo si el dump de ESTA vez salió
        bien; si no, no hay '<hierarchy' y se reintenta igual que antes."""
        f = "/sdcard/ui_debug.xml"
        xml = ""
        for intento in range(intentos):
            xml = self.adb_out(
                "exec-out", f"rm -f {f}; uiautomator dump {f} 2>&1 && cat {f}"
            )
            if "<hierarchy" in xml:
                break
            if intento == intentos - 1:
                # Antes el mensaje era genérico ("no volcó la pantalla") y
                # descartaba lo que uiautomator/adb hubieran dicho al
                # fallar -- imposible diagnosticar sin reproducir el fallo
                # a mano. Ahora el `2>&1` de arriba mete el error real (si
                # lo hay) en `xml`, y acá se incluye un recorte en la
                # excepción para que quede en el log sin tener que tocar
                # el emulador de nuevo.
                detalle = xml.strip()[:300] or "(sin salida)"
                raise RuntimeError(
                    f"uiautomator no volcó la pantalla tras {intentos} intentos: {detalle}"
                )
            time.sleep(0.5)
        fuera = []
        for m in re.finditer(r"<node\b([^>]*?)/?>", xml):
            a = dict(re.findall(r'(\S+?)="([^"]*)"', m.group(1)))
            if a.get("package") != PAQUETE:
                continue
            a["content-desc"] = a.get("content-desc", "").replace("&#10;", "\n").replace(
                "&amp;", "&"
            )
            fuera.append(a)
        return fuera

    @staticmethod
    def centro(a):
        b = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", a.get("bounds", ""))
        if not b:
            return None
        x1, y1, x2, y2 = map(int, b.groups())
        return (x1 + x2) // 2, (y1 + y2) // 2

    def leer_campo(self, punto, lista=None) -> str:
        """Devuelve el texto ACTUAL del nodo MÁS ESPECÍFICO cuyo cuadro
        contiene `punto` (el mismo (x, y) que se usa para tocarlo con
        tap()/llenar()) -- no el primero que aparezca en el volcado.

        Bug real, visto en vivo el 2026-09-23: un contenedor padre (la
        tarjeta del campo, o la pantalla entera) casi siempre TAMBIÉN tiene
        el punto dentro de sus límites, y en el XML aparece ANTES que su
        hijo (los nodos padres se abren antes que los suyos) -- quedarse
        con el primer match encontraba ese contenedor vacío, nunca el
        EditText real, así que el monto se leía '' el 100% de las veces
        (no un fallo intermitente de tecleo, sino este bug). Se usa el
        nodo de área más chica entre los que contienen el punto, que es
        el más anidado -- el campo real, no su contenedor."""
        x, y = punto
        mejor, mejor_area = None, None
        for a in lista if lista is not None else self.nodos():
            b = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", a.get("bounds", ""))
            if not b:
                continue
            x1, y1, x2, y2 = map(int, b.groups())
            if not (x1 <= x <= x2 and y1 <= y <= y2):
                continue
            area = (x2 - x1) * (y2 - y1)
            if mejor_area is None or area < mejor_area:
                mejor, mejor_area = a, area
        if mejor is None:
            return ""
        return mejor.get("text") or mejor.get("content-desc", "")

    def buscar(self, rotulo: str, lista=None):
        for a in lista if lista is not None else self.nodos():
            desc = a.get("content-desc", "")
            exacto = desc == rotulo or (
                rotulo.startswith("Tab ") and desc.endswith("\n" + rotulo)
            )
            if not exacto:
                continue
            if a.get("clickable") != "true" or a.get("enabled") != "true":
                continue
            return self.centro(a)
        return None

    def esperar(self, condicion, descripcion: str, intentos: int = 16, intervalo: float = 0.4):
        """Sondea la pantalla hasta que `condicion(lista_de_nodos)` devuelva
        algo verdadero (o hasta agotar los intentos). El primer chequeo es
        inmediato, sin dormir — si el control ya está ahí no se pierde
        tiempo; solo se espera cuando hace falta.

        Esto reemplaza mirar la pantalla una sola vez: la app a veces tarda
        unos instantes en terminar de dibujar un formulario o un menú, y una
        sola lectura en ese momento no ve nada todavía aunque el control
        aparezca un segundo después.

        Presupuesto subido (antes 8 x 0,25 s ~= 2 s + el costo real de cada
        `nodos()`): visto en vivo el 2026-09-30, con la PC muy justa de
        memoria (1,8 GB libres de 15,8 GB, muchas pestañas de Chrome +
        BlueStacks + otros programas abiertos), la app tardaba más que ese
        presupuesto en dibujar un desplegable -- el bot se rendía
        ("no se encontró la opción 'Teléfono'", "el selector de banco no se
        abrió") antes de que la UI terminara de aparecer, no porque el
        control no fuera a aparecer nunca. Con más margen, un sistema lento
        tarda más en confirmar pero no falla en falso; uno rápido no nota
        la diferencia (la primera lectura sigue siendo inmediata)."""
        for intento in range(intentos):
            if intento:
                time.sleep(intervalo)
            lista = self.nodos()
            resultado = condicion(lista)
            if resultado:
                return resultado
        return None

    def existe_desc(self, texto: str, lista=None) -> bool:
        """True si algún nodo (tocable o no) tiene ese content-desc EXACTO.
        A diferencia de buscar(), no exige que sea clicable: sirve para
        verificar títulos/marcadores de pantalla, ej. 'Confirmar Operación'
        (que no es un botón, solo confirma que se llegó a la revisión)."""
        for a in lista if lista is not None else self.nodos():
            if a.get("content-desc", "") == texto:
                return True
        return False

    def escanear_errores_validacion(self, lista=None):
        """Mensajes de validación de la propia app BDV (ej. 'Debes introducir
        el nombre', 'Selecciona el tipo de documento'), tal cual los muestra,
        confirmados contra la app real. Se usan para reportar datos
        incompletos, nunca se inventan ni se traducen."""
        prefijos = ("Debes introducir", "Selecciona", "Seleccione")
        encontrados = []
        for a in lista if lista is not None else self.nodos():
            texto = a.get("text") or a.get("content-desc", "")
            if texto and texto.startswith(prefijos):
                encontrados.append(texto)
        return encontrados

    # ── acciones ───────────────────────────────────────────────────
    def tap(self, x, y, que="", espera: float = 0.15):
        """Espera tras el toque: 0.15 s por defecto, no 1 s. Medido contra
        el emulador real: el toque de ADB en sí tarda ~30 ms: 1 s era 30
        veces más de lo que hace falta para que la app registre el foco.
        Mismo orden de magnitud que usa el proyecto principal para el
        equivalente (0.02-0.2 s según el caso)."""
        print(f"  -> TAP ({x}, {y}) {que}")
        self.adb("shell", "input", "tap", str(x), str(y))
        time.sleep(espera)

    def tap_desc(self, rotulo: str, espera: float = 0.08, lista=None):
        """Espera a que '{rotulo}' esté visible y clicable, y lo toca. No
        asume que ya está ahí: si la pantalla anterior todavía se está
        dibujando, reintenta en vez de rendirse en el primer vistazo.

        `lista`: un volcado que el llamador YA tiene de esta misma pantalla
        (sin nada tocado desde entonces). Si el rótulo está ahí, se usa sin
        volver a volcar (~2 s menos); si no está, se sondea como siempre."""
        punto = self.buscar(rotulo, lista) if lista is not None else None
        if punto is None:
            punto = self.esperar(lambda l: self.buscar(rotulo, l), f"'{rotulo}' visible")
        if punto is None:
            raise RuntimeError(f"'{rotulo}' no apareció tras esperar; no se tocó nada")
        print(f"  -> TAP '{rotulo}' {punto}")
        self.adb("shell", "input", "tap", str(punto[0]), str(punto[1]))
        time.sleep(espera)

    def tap_desc_any(self, rotulos, espera: float = 0.08, lista=None) -> str:
        """Como tap_desc, pero con varios rótulos candidatos (el primero que
        aparezca, se toca). `lista`: igual que en tap_desc."""
        def _cualquiera(l):
            for rotulo in rotulos:
                punto = self.buscar(rotulo, l)
                if punto is not None:
                    return (rotulo, punto)
            return None

        encontrado = _cualquiera(lista) if lista is not None else None
        if encontrado is None:
            encontrado = self.esperar(_cualquiera, f"alguno de {rotulos}")
        if encontrado is None:
            raise RuntimeError(f"ninguno de {rotulos} apareció tras esperar; no se tocó nada")
        rotulo, punto = encontrado
        print(f"  -> TAP '{rotulo}' {punto}")
        self.adb("shell", "input", "tap", str(punto[0]), str(punto[1]))
        time.sleep(espera)
        return rotulo

    def limpiar_campo(self):
        self.adb("shell", "input", "keyevent", "KEYCODE_MOVE_END", *(["KEYCODE_DEL"] * 40))

    def text(self, t: str):
        print("  -> Escribiendo texto (contenido oculto)")
        partes = t.split("-")
        for i, trozo in enumerate(partes):
            if trozo:
                self.adb("shell", "input", "text", shlex.quote(trozo.replace(" ", "%s")))
            if i < len(partes) - 1:
                self.adb("shell", "input", "keyevent", "KEYCODE_MINUS")
        time.sleep(0.05)  # antes 0.5 s; el proyecto principal usa 0.02-0.05 s aquí

    def llenar(self, punto, valor: str, que: str):
        print(f"  Campo {que} {punto}")
        self.tap(*punto, que=que)
        self.limpiar_campo()
        self.text(valor)

    def scroll_down(self, x, y_from, y_to, duration_ms: int = 300):
        """Desliza de abajo hacia arriba (y_from > y_to) para bajar en una
        lista larga, ej. el selector de bancos."""
        self.adb("shell", "input", "swipe", str(x), str(y_from), str(x), str(y_to), str(duration_ms))

    def seleccionar_opcion_popup(self, punto_selector, opcion_desc: str, que: str = ""):
        """Abre un desplegable (tap en punto_selector) y espera a que
        aparezca la opción con ese content-desc EXACTO antes de tocarla.
        Usado para 'Tipo de instrumento' y 'Tipo de documento'."""
        self.tap(*punto_selector, que=que or f"abrir selector")
        opt = self.esperar(lambda lista: self.buscar(opcion_desc, lista),
                           f"opción '{opcion_desc}' visible")
        if opt is None:
            raise RuntimeError(f"no se encontró la opción '{opcion_desc}' en el desplegable")
        print(f"  -> TAP opción '{opcion_desc}' {opt}")
        self.adb("shell", "input", "tap", str(opt[0]), str(opt[1]))
        time.sleep(0.15)

    @staticmethod
    def _opciones_banco(lista):
        return [a for a in lista
                if a.get("clickable") == "true" and a.get("enabled") == "true"
                and re.match(r"^\d{4}\s*-\s*\S", a.get("content-desc") or a.get("text", ""))]

    @staticmethod
    def _hubo_error_carga_banco(lista) -> bool:
        """True si el selector de banco muestra 'Error al cargar, pulsa
        para reintentar' en vez de la lista -- visto en vivo el 2026-09-30:
        un fallo real de la app (de red, no del bot) al pedir el catálogo
        de bancos. Confirmado contra una captura real de ese día."""
        for a in lista:
            texto = (a.get("text") or a.get("content-desc", "")).lower()
            if "error al cargar" in texto:
                return True
        return False

    def _esperar_lista_bancos(self, punto_selector, intentos: int = 16, intervalo: float = 0.4):
        """Como esperar(), pero reacciona a 'Error al cargar' reintentando el
        toque (el mismo campo dice 'pulsa para reintentar') en vez de agotar
        el presupuesto sin hacer nada -- antes ese error se confundía con
        'el selector nunca abrió' y no había forma de recuperarse sola."""
        for intento in range(intentos):
            if intento:
                time.sleep(intervalo)
            lista = self.nodos()
            if self._opciones_banco(lista):
                return lista
            if self._hubo_error_carga_banco(lista):
                print("  !! 'Error al cargar' en el selector de banco, reintentando el toque")
                self.adb("shell", "input", "tap", str(punto_selector[0]), str(punto_selector[1]))
                time.sleep(0.5)
        return None

    def seleccionar_banco(self, punto_selector, codigo_banco: str):
        """Selecciona una fila EXACTA por código en el selector de banco, con
        hasta cuatro scrolls. Nunca por posición fija: la lista es
        virtualizada y el orden cambia. Verifica el banco quedó seleccionado
        antes de devolver el control."""
        self.tap(*punto_selector, que="selector de banco")
        lista = self._esperar_lista_bancos(punto_selector)
        if lista is None:
            raise RuntimeError("el selector de banco no se abrió (la lista nunca apareció)")

        for pagina in range(5):
            opciones = self._opciones_banco(lista)
            if not opciones:
                raise RuntimeError("el selector de banco dejó de estar visible")
            objetivo = next(
                (a for a in opciones if re.match(
                    rf"^{re.escape(codigo_banco)}\s*-", a.get("content-desc") or a.get("text", ""))),
                None,
            )
            if objetivo is not None:
                punto = self.centro(objetivo)
                print(f"  -> TAP banco '{codigo_banco}' {punto}")
                self.adb("shell", "input", "tap", str(punto[0]), str(punto[1]))
                time.sleep(0.15)
                verif = self.nodos()
                if any(re.match(rf"^Banco\s*:\s*{re.escape(codigo_banco)}\s*-", a.get("content-desc", ""))
                       for a in verif):
                    return
                raise RuntimeError("no se pudo verificar el banco seleccionado")
            if pagina == 4:
                break
            contenedor = next((a for a in lista if a.get("scrollable") == "true"
                               and a.get("enabled") == "true"), None)
            if contenedor is None:
                raise RuntimeError("no se encontró la lista desplazable de bancos")
            b = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", contenedor.get("bounds", ""))
            if not b:
                raise RuntimeError("no se pudo leer los límites de la lista de bancos")
            x1, y1, x2, y2 = map(int, b.groups())
            margen = max(1, (y2 - y1) // 8)
            self.scroll_down((x1 + x2) // 2, y2 - margen, y1 + margen)
            time.sleep(0.2)
            lista = self.nodos()
        raise RuntimeError(f"el banco (código {codigo_banco}) no aparece tras cuatro scrolls")

    def campo_password(self, lista=None):
        for a in lista if lista is not None else self.nodos():
            if a.get("password") == "true":
                return self.centro(a)
        return None

    def _cerrar_revision_trabada(self, lista):
        """El botón 'X' de la pantalla 'Confirmar Operación' no tiene
        content-desc, así que no se puede buscar por rótulo. Se identifica
        por descarte: el único botón clicable sin content-desc NI texto que
        cae en la franja superior (y < 200), donde vive el ícono de cerrar
        en esta pantalla. None si no hay ninguno así."""
        for a in lista:
            if (a.get("clickable") == "true" and not a.get("content-desc")
                    and not a.get("text")):
                centro = self.centro(a)
                if centro and centro[1] < 200:
                    return centro
        return None

    def volver_inicio(self, intentos: int = 30, espera_reintento: float = 1.0):
        """Nunca usa KEYCODE_BACK: puede sacar de la app o dejar pantallas a medias.

        Mientras el banco procesa una operación, la app tapa toda la pantalla
        con un modal de "Realizando operación" (o similar) — ni la pestaña
        Inicio ni la flecha Back quedan visibles un momento. Antes, si ese
        chequeo no encontraba NINGUNO de los dos, se rendía en el acto sin
        usar el resto de `intentos` — el parámetro no servía para nada en
        ese caso. Ahora reintenta: si el modal se cierra unos segundos
        después (que es lo normal), se recupera solo.

        Caso aparte: los menús tipo hoja inferior (ej. el que abre "Pagos"
        con PagomóvilBDV/Tarjeta de crédito/etc.) tapan la barra de pestañas
        por completo — mientras están abiertos NO existe ni "Tab 1 of 6" ni
        "Back" en la pantalla, así que sin este caso el bot se queda
        reintentando para siempre. Se identifican por un nodo 'Scrim' (el
        fondo oscuro clicable detrás del menú); tocarlo los cierra. Visto en
        vivo el 2026-09-22: una orden que falla DESPUÉS de abrir ese menú
        pero ANTES de elegir una opción (ej. Pago Móvil con un banco no
        reconocido) deja al bot exactamente en este estado.

        Tercer caso: la pantalla de revisión ("Confirmar Operación") tras
        un rechazo de clave. Al cerrar el aviso "Atención: Error al
        autenticar con contraseña", la app NO vuelve al formulario: se
        queda en "Confirmar Operación" con los datos todavía cargados,
        para reintentar la clave sin rellenar todo de nuevo. El botón para
        salir de ahí es una "X" arriba a la derecha SIN content-desc (visto
        en vivo el 2026-09-23, bounds=[984,48][1080,160]) — no se puede
        buscar por rótulo como Tab/Back/Scrim. Se reconoce por descarte:
        único botón clicable sin content-desc NI texto en la franja
        superior de la pantalla (y < 200). Sin este caso, el reintento
        automático tras una clave rechazada (ver ErrorAutenticacion en
        ejecutor.py) se queda trabado aquí para siempre, y cada orden
        siguiente hereda la misma pantalla y falla igual, en cadena.

        Presupuesto subido de 10x0,4s (~4 s) a 30x1,0s (~30 s, más lo que
        tarde cada `nodos()`): visto en vivo el 2026-09-30, una orden llegó
        con la pantalla TODAVÍA tapada por "Realizando operación" de un
        pago anterior -- el banco de verdad sigue procesando ahí, no es
        solo una animación de UI, así que puede tardar más que una espera
        corta. Con el presupuesto viejo el bot se rendía ("no hay un
        control visible para volver a Inicio") mientras el banco todavía
        estaba trabajando. Mismo orden de magnitud que usa
        `_esperar_comprobante()` en ejecutor.py para la misma clase de
        espera (confirmar que el banco terminó de procesar)."""
        for intento in range(intentos):
            lista = self.nodos()
            tab = self.buscar("Tab 1 of 6", lista)
            en_inicio = any(
                a.get("content-desc") == "Inicio" and a.get("clickable") != "true"
                for a in lista
            )
            if tab and en_inicio:
                print("  -> ya en Inicio")
                return lista  # el llamador puede ubicar las pestañas sin volver a volcar
            destino = tab or self.buscar("Back", lista)
            if destino is None:
                scrim = self.buscar("Scrim", lista)
                if scrim is not None:
                    print(f"  -> cerrando menú (Scrim) {scrim}")
                    self.adb("shell", "input", "tap", str(scrim[0]), str(scrim[1]))
                    time.sleep(espera_reintento)
                    continue
                if self.existe_desc("Confirmar Operación", lista):
                    cerrar = self._cerrar_revision_trabada(lista)
                    if cerrar is not None:
                        print(f"  -> cerrando revisión trabada (X) {cerrar}")
                        self.adb("shell", "input", "tap", str(cerrar[0]), str(cerrar[1]))
                        time.sleep(espera_reintento)
                        continue
                if intento < intentos - 1:
                    print("  -> nada navegable visible todavía (¿modal de procesando?), reintentando")
                    time.sleep(espera_reintento)
                    continue
                raise RuntimeError(
                    f"no hay un control visible para volver a Inicio tras {intentos} intentos")
            print(f"  -> volviendo a Inicio {destino}")
            self.adb("shell", "input", "tap", str(destino[0]), str(destino[1]))
            time.sleep(0.15)  # antes 0.8 s; el proyecto principal usa 0.08 s aquí
        raise RuntimeError("no se pudo verificar la pantalla de Inicio")

    def capturar_pantalla(self, destino_png: str, intentos: int = 3) -> str:
        """Guarda un screenshot de la pantalla ACTUAL en destino_png. Se usa
        siempre al final del flujo, sin importar si el pago salió bien o mal,
        para que un humano lo revise en Telegram.

        Verifica la firma PNG antes de escribir: si el dispositivo no
        respondió (offline, emulador colgado), `screencap` puede devolver
        stdout vacío o un mensaje de error de adb en vez de una imagen, y
        antes eso se escribía tal cual a disco como si fuera un PNG válido.
        Ese archivo corrupto llegaba hasta Telegram, que lo rechazaba con
        'SendMediaRequest ... file parts is invalid' -- un segundo error
        confuso que tapaba el problema real. Ahora se lanza acá, con el
        motivo real, y el llamador decide qué hacer sin intentar subir nada.

        Reintenta como nodos() (mismo fallo intermitente de adb): antes era
        de un solo intento, así que un hipo puntual de adb -- el dispositivo
        respondiendo a todo lo demás pero fallando justo este exec-out --
        ya dejaba la orden sin captura aunque el pago se hubiera visto bien
        en pantalla un segundo antes."""
        detalle = ""
        for intento in range(intentos):
            r = subprocess.run(
                [ADB_PATH, "-s", self.serial, "exec-out", "screencap", "-p"],
                capture_output=True, timeout=TIMEOUT_ADB_SEG,
            )
            if r.stdout.startswith(b"\x89PNG"):
                with open(destino_png, "wb") as f:
                    f.write(r.stdout)
                return destino_png
            detalle = r.stderr.decode("utf-8", errors="replace").strip() or repr(r.stdout[:200])
            if intento < intentos - 1:
                time.sleep(0.5)
        raise RuntimeError(f"screencap no devolvió un PNG válido tras {intentos} intentos: {detalle}")

    def hubo_comprobante(self, lista=None) -> tuple[bool, str]:
        titulos = {
            "comprobante",
            "comprobante de operación",
            "comprobante de operacion",
            "comprobante de la operación",
            "comprobante de la operacion",
        }
        lista = lista if lista is not None else self.nodos()
        rotulos = [" ".join(a.get("content-desc", "").split()).casefold() for a in lista]
        if any(r in titulos for r in rotulos):
            ref = next(
                (a.get("content-desc") for a in lista if a.get("content-desc", "").startswith("Operación:")),
                "sin referencia",
            )
            return True, ref
        return False, ""
