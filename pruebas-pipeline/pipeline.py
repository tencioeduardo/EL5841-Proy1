#!/usr/bin/env python3
"""
pipeline.py -- Pipeline de GStreamer para el sistema de control de acceso.

Aplica a: HOST (laptop x86_64, sin aceleracion por hardware) y a la
RASPBERRY PI 4 (ARM, con codificador H.264 por hardware via V4L2). El grafo
es identico en ambos entornos; lo unico que cambia es el elemento
codificador, seleccionado por el parametro `encoder_profile` ("host" vs
"produccion"). Esa es la garantia de portabilidad de este diseno.

Construido con Gst.ElementFactory / Gst.Pipeline (sin gst-launch embebido),
tal como exige el laboratorio.

Forma del grafo (una sola camara USB, cuatro salidas):

    v4l2src -> capsfilter(YUY2) -> videoconvert -> capsfilter(NV12)
        -> queue (captura) -> tee0
            |
            +-- queue (analisis, leaky) -> videoscale -> capsfilter(NV12 chico)
            |       -> videoconvert -> capsfilter(BGR) -> appsink (drop=true)
            |
            +-- queue (codificacion) -> ENCODER -> h264parse -> capsfilter(avc)
                    -> tee1
                    |
                    +-- queue -> splitmuxsink                    (grabacion)
                    +-- queue -> rtph264pay -> udpsink            (streaming)

Justificacion de las decisiones de diseno (resumen; el detalle completo
esta en notas-portabilidad.md):

- Se codifica UNA sola vez, justo despues de tee0. El resultado se fuerza a
  H.264 en formato "avc,alignment=au" UNA sola vez (un solo h264parse +
  un solo capsfilter), antes de tee1, y esa MISMA version se reparte a
  grabacion y streaming: mp4mux (dentro de splitmuxsink) solo acepta avc,
  y rtph264pay acepta avc igual que byte-stream, asi que avc satisface a
  los dos consumidores sin reconvertir por rama. (Una version anterior de
  este diseno ponia un segundo h264parse por rama para reconvertir
  avc/byte-stream; en pruebas sobre el host esa reconversion dejaba la
  rama de grabacion bloqueada sin errores en el bus -- splitmuxsink nunca
  llegaba a abrir un archivo, aunque streaming seguia funcionando en su
  propio hilo. Se elimino esa reconversion redundante en vez de intentar
  depurarla, porque avc ya resuelve ambas ramas por si solo.)
- El formato comun forzado entre camara y tee0 es NV12: lo acepta tanto
  x264enc (software, host) como v4l2h264enc (hardware V4L2 en la Pi), asi
  que el resto del grafo no cambia entre entornos.
- La etapa de captura fuerza YUY2 crudo nativo directamente sobre v4l2src
  (un capsfilter con format=YUY2 en el ancho/alto/framerate de trabajo),
  en vez de dejar que v4l2src negocie libremente y usar decodebin como
  intermediario. Se investigo con `gst-launch-1.0 -v` (ver A1-caps.txt)
  que, sin forzar nada, v4l2src+decodebin elegian MJPEG a 1280x720@30
  (la opcion de mayor prioridad que decodebin encuentra), decodificaban
  con jpegdec a I420 1280x720, y luego videoconvert+videoscale reducian a
  NV12 640x480@30 -- exactamente la resolucion final que la camara ya
  ofrece de forma nativa en crudo (YUY2 640x480@30, confirmado con
  `gst-device-monitor-1.0 Video/Source`). Es decir, se decodificaba MJPEG
  de mayor resolucion solo para volver a escalar hacia abajo al mismo
  tamano que la camara ya puede entregar sin decodificar. Se opto por:
    a) ELIMINAR decodebin por completo (no mantenerlo como "red de
       seguridad" con caps forzados delante). Mantenerlo como passthrough
       parecia mas flexible ante un cambio futuro de camara, pero en la
       practica esconde el problema en vez de resolverlo: si algun dia
       vuelve a aparecer una camara/driver que solo ofrezca MJPEG en la
       resolucion pedida, decodebin lo aceptaria en silencio y se
       reintroduciria el mismo costo de jpegdec sin ningun aviso en el
       pipeline. Forzar el caps exacto (YUY2 en self.width/height/
       framerate) hace que cualquier desviacion sea un error explicito de
       negociacion (`not-negotiated`) en vez de una regresion de
       rendimiento silenciosa -- mas facil de detectar y depurar, y
       coherente con el resto del diseno (p. ej. el capsfilter de avc
       antes de tee1, que tambien falla explicito si algo no encaja).
    b) NO usar un elemento adicional entre v4l2src y videoconvert: YUY2
       es un formato de video crudo que videoconvert consume directo por
       su sink pad; el capsfilter que fuerza el caps es suficiente, no
       hace falta decodificar ni convertir antes.
    c) Eliminar tambien el `videoscale` que antes reducia 1280x720 a
       640x480 en la rama principal: como el capsfilter de captura ya
       pide directamente self.width x self.height (el mismo valor que
       antes solo se alcanzaba al final, tras escalar), no queda ningun
       cambio de resolucion que hacer entre la camara y capsfilter(NV12);
       agregar un videoscale ahi seria un elemento de paso sin trabajo
       real. (La rama de analisis SI conserva su propio videoscale, que
       reduce a self.analysis_width/height -- una resolucion menor,
       distinta, pensada para el reconocimiento facial.)
  Consecuencia de portabilidad: la camara USB es la misma en host y Pi
  (misma clase UVC, mismos caps anunciados), asi que este cambio de
  captura no introduce ninguna diferencia nueva entre entornos -- ver
  notas-portabilidad.md, seccion 1 y 2.
- videoconvert (rama principal) ya no antecede a un videoscale propio,
  porque no hace falta escalar en esa rama (ver punto anterior). En la
  rama de analisis se mantiene el orden videoscale primero, videoconvert
  despues, porque ahi si interesa reducir resolucion cuanto antes para
  que la conversion de color (a BGR) procese menos pixeles.
- Se usan 5 queues: una tras la captura (para no bloquear v4l2src), una por
  cada rama de tee0 (codificacion y analisis) y una por cada rama de tee1
  (grabacion y streaming). Cada rama que cuelga de un tee necesita su
  propia queue para correr en su propio hilo; sin eso, un consumidor lento
  (p. ej. el analisis facial) bloquearia tambien a la grabacion.
- La rama de analisis usa queue con leaky=downstream y max-size-buffers
  bajo, ademas de appsink con drop=true y max-buffers bajo: si el
  reconocimiento facial se atrasa, se descartan cuadros viejos en vez de
  acumular latencia o congelar el resto del pipeline.
- El LED de actuacion NO esta aqui. Este modulo solo entrega cuadros BGR
  y su timestamp via el callback `on_frame`; es la aplicacion (fuera de
  este archivo) la que corre OpenCV sobre esos cuadros y, si hay una
  identificacion positiva, enciende el LED y llama a
  retention.mark_as_event() sobre `pipeline.current_segment_path`.
"""

import os
import sys
import time
import datetime

import gi

gi.require_version("Gst", "1.0")
try:
    gi.require_version("GstApp", "1.0")
except ValueError:
    # Ambos: en imagenes muy minimas el typelib de GstApp podria faltar;
    # se sigue funcionando por el camino de senales ("new-sample"), que no
    # depende de GstApp para nada mas que tipado. Se deja constancia aqui
    # para que, si esto se dispara en la Pi, sea lo primero que se revise
    # con gst-inspect-1.0.
    pass

from gi.repository import Gst, GLib  # noqa: E402

Gst.init(None)


# ---------------------------------------------------------------------------
# Perfiles de codificador. AMBOS producen H.264; NUNCA se usa x264enc en el
# perfil "produccion" (es GPL y consume CPU que la Pi necesita para el resto
# de la aplicacion -- ver notas-portabilidad.md).
# ---------------------------------------------------------------------------
ENCODER_PROFILES = {
    # HOST: laptop x86_64 sin aceleracion por hardware. x264enc en software,
    # con ajustes para baja latencia (streaming en vivo) sin exigir demasiado
    # a un equipo de desarrollo comun.
    "host": {
        "factory": "x264enc",
        "properties": {
            "tune": "zerolatency",
            "speed-preset": "ultrafast",
            "bitrate": 2000,        # kbps
            "key-int-max": 60,      # keyframe cada ~2 s a 30 fps
        },
    },
    # PRODUCCION: Raspberry Pi 4. Codificador V4L2 por hardware del SoC;
    # libera la CPU para OpenCV y el resto de la aplicacion. Los nombres de
    # los controles V4L2 (dentro de extra-controls) deben confirmarse en el
    # equipo destino con `v4l2-ctl --list-ctrls -d /dev/videoN`, porque
    # cambian segun el driver/kernel de la imagen Yocto -- no se asumen.
    "produccion": {
        "factory": "v4l2h264enc",
        "properties": {
            "extra-controls": (
                "controls,video_bitrate=2000000,"
                "h264_i_frame_period=60,h264_profile=4,h264_level=11"
            ),
        },
    },
}

DEFAULT_DEVICE = "/dev/video0"
DEFAULT_WIDTH = 640
DEFAULT_HEIGHT = 480
DEFAULT_FRAMERATE = 30
DEFAULT_ANALYSIS_WIDTH = 320
DEFAULT_ANALYSIS_HEIGHT = 240
DEFAULT_STREAM_PORT = 5000
DEFAULT_SEGMENT_SECONDS = 300  # 5 minutos por segmento


class AccessControlPipeline:
    """Pipeline unico de GStreamer con cuatro salidas: grabacion continua
    segmentada, streaming RTP/UDP en vivo, entrega de cuadros para analisis
    facial (appsink) y el punto de enganche para la actuacion del LED
    (fuera de este pipeline). Aplica a HOST y a PI; ver ENCODER_PROFILES.
    """

    def __init__(
        self,
        device=DEFAULT_DEVICE,
        encoder_profile="host",
        evidence_dir="./evidencia",
        stream_host="127.0.0.1",
        stream_port=DEFAULT_STREAM_PORT,
        width=DEFAULT_WIDTH,
        height=DEFAULT_HEIGHT,
        framerate=DEFAULT_FRAMERATE,
        analysis_width=DEFAULT_ANALYSIS_WIDTH,
        analysis_height=DEFAULT_ANALYSIS_HEIGHT,
        segment_seconds=DEFAULT_SEGMENT_SECONDS,
        on_frame=None,
        on_error=None,
        on_ready=None,
    ):
        """Ambos.

        Parametros relevantes:
          device           -- /dev/videoX de la camara UVC (host y Pi usan
                               la misma clase de dispositivo).
          encoder_profile  -- "host" (x264enc) o "produccion" (v4l2h264enc).
          evidence_dir     -- carpeta donde splitmuxsink escribe segmentos.
          stream_host/port -- destino RTP/UDP del puesto de vigilancia
                               (OTRA computadora en la red local; no asumir
                               que es la misma maquina que el emisor).
          on_frame(frame_bgr, timestamp_ns) -- callback de la aplicacion
                               para cada cuadro de analisis (numpy BGR).
          on_error(mensaje) -- callback si el bus reporta ERROR o EOS
                               inesperado; asi la app sabe cuando el
                               pipeline fallo y puede, por ejemplo, apagar
                               el LED o alertar.
          on_ready()        -- callback cuando el pipeline llega a PLAYING;
                               asi la app sabe cuando puede empezar a
                               esperar cuadros.
        """
        if encoder_profile not in ENCODER_PROFILES:
            raise ValueError(
                f"encoder_profile debe ser uno de {list(ENCODER_PROFILES)}, "
                f"recibido: {encoder_profile!r}"
            )

        self.device = device
        self.encoder_profile = encoder_profile
        self.evidence_dir = evidence_dir
        self.stream_host = stream_host
        self.stream_port = stream_port
        self.width = width
        self.height = height
        self.framerate = framerate
        self.analysis_width = analysis_width
        self.analysis_height = analysis_height
        self.segment_seconds = segment_seconds

        self.on_frame = on_frame
        self.on_error = on_error
        self.on_ready = on_ready

        # Ruta del segmento que splitmuxsink esta escribiendo en este
        # momento. La aplicacion la usa para marcar un segmento como
        # "evento" (ver retention.mark_as_event) cuando el reconocimiento
        # facial da positivo. NUNCA se borra un segmento marcado como
        # evento de forma automatica.
        self.current_segment_path = None

        self.pipeline = None
        self._elements = {}
        self._build_pipeline()

    # -- construccion -------------------------------------------------

    def _make(self, factory_name, name=None):
        """Ambos. Crea un elemento y lo agrega al pipeline, o falla con un
        mensaje claro (nunca asumir que el elemento existe: se verifica en
        el equipo destino con gst-inspect-1.0, sobre todo en la imagen
        Yocto, cuya version de GStreamer no se asume de antemano)."""
        el = Gst.ElementFactory.make(factory_name, name)
        if el is None:
            raise RuntimeError(
                f"No se pudo crear el elemento '{factory_name}'. "
                f"Verifique con 'gst-inspect-1.0 {factory_name}' en este "
                f"equipo (host o Pi) que el plugin este instalado."
            )
        self.pipeline.add(el)
        self._elements[name or factory_name] = el
        return el

    def _build_pipeline(self):
        """Ambos. Arma el grafo completo de forma totalmente estatica: la
        etapa de captura fuerza YUY2 crudo nativo de la camara antes de
        videoconvert, asi que ya no hace falta decodebin ni un pad dinamico
        (a diferencia de una version anterior que dejaba a decodebin
        decidir en tiempo de ejecucion si la camara entregaba MJPEG o
        crudo)."""
        self.pipeline = Gst.Pipeline.new("access-control-pipeline")
        os.makedirs(self.evidence_dir, exist_ok=True)

        # --- Captura (Ambos: misma camara USB/V4L2 en host y Pi) ---
        src = self._make("v4l2src", "camera_source")
        src.set_property("device", self.device)

        # Se fuerza YUY2 crudo nativo (confirmado con
        # gst-device-monitor-1.0 Video/Source) en self.width/height/
        # framerate exactos, en vez de dejar que v4l2src/decodebin elijan
        # libremente MJPEG a 1280x720@30 y luego reducir. La camara
        # REDRAGON ofrece YUY2 justo en la resolucion/framerate de trabajo
        # del pipeline (por defecto 640x480@30), asi que forzar este caps
        # evita decodificar MJPEG (jpegdec) y evita el escalado posterior
        # que antes reducia 1280x720 -> 640x480. Si la camara no negocia
        # YUY2 en esos valores exactos (otra camara sin ese modo, u otro
        # width/height/framerate que la REDRAGON no ofrece en crudo), la
        # negociacion falla aqui de forma explicita ("not-negotiated") en
        # vez de reintroducir en silencio el camino MJPEG -- ver la
        # justificacion completa en el docstring del modulo.
        caps_raw = self._make("capsfilter", "camera_caps_raw")
        caps_raw.set_property(
            "caps",
            Gst.Caps.from_string(
                f"video/x-raw,format=YUY2,width={self.width},"
                f"height={self.height},framerate={self.framerate}/1"
            ),
        )

        conv1 = self._make("videoconvert", "main_convert")
        caps1 = self._make("capsfilter", "main_caps")
        caps1.set_property(
            "caps",
            Gst.Caps.from_string(
                f"video/x-raw,format=NV12,width={self.width},"
                f"height={self.height},framerate={self.framerate}/1"
            ),
        )

        q_capture = self._make("queue", "q_capture")
        q_capture.set_property("max-size-time", 1 * Gst.SECOND)

        tee0 = self._make("tee", "tee0_raw")

        # Enlace totalmente estatico: YUY2 es un formato crudo que
        # videoconvert consume directo por su sink pad, sin decodificacion
        # previa ni pad dinamico de por medio (a diferencia de decodebin,
        # que necesitaba conectar su pad-added en tiempo de ejecucion
        # porque no sabia de antemano si iba a entregar MJPEG decodificado
        # o video crudo). Tampoco hace falta videoscale en esta rama: el
        # capsfilter de captura ya pide directamente self.width x
        # self.height, la misma resolucion final que caps1 exige.
        src.link(caps_raw)
        caps_raw.link(conv1)
        conv1.link(caps1)
        caps1.link(q_capture)
        q_capture.link(tee0)

        # --- Rama de codificacion (Ambos; el elemento cambia por perfil) ---
        q_encode = self._make("queue", "q_encode")
        q_encode.set_property("max-size-time", 1 * Gst.SECOND)

        profile = ENCODER_PROFILES[self.encoder_profile]
        encoder = self._make(profile["factory"], "encoder")
        for prop_name, prop_value in profile["properties"].items():
            if prop_name == "extra-controls" and isinstance(prop_value, str):
                encoder.set_property(
                    prop_name, Gst.Structure.new_from_string(prop_value)
                )
            else:
                encoder.set_property(prop_name, prop_value)

        parse0 = self._make("h264parse", "parse_encoded")

        # Se fuerza "avc,alignment=au" UNA sola vez, aqui, antes de tee1 --
        # no una vez por rama. mp4mux (usado dentro de splitmuxsink) SOLO
        # acepta avc; nunca acepta byte-stream. rtph264pay, en cambio,
        # acepta tanto avc como byte-stream desde hace varias versiones de
        # GStreamer (gst-plugins-good), asi que avc sirve para las DOS
        # ramas sin reconvertir. Un h264parse adicional por rama (una
        # reconversion avc/byte-stream redundante justo antes de
        # splitmuxsink) se probo en el host y quedo bloqueada sin errores
        # en el bus: splitmuxsink nunca abria ningun archivo, mientras que
        # la rama de streaming seguia funcionando con normalidad. No se
        # llego a una causa raiz 100% confirmada (candidatas: perdida de
        # la marca de keyframe en el segundo parseo, o un queue lleno
        # bloqueado en su propio hilo sin backpressure visible), pero
        # como avc satisface a ambos consumidores no hace falta arriesgar
        # ese patron: se elimina por completo.
        caps_encoded = self._make("capsfilter", "caps_encoded")
        caps_encoded.set_property(
            "caps", Gst.Caps.from_string("video/x-h264,stream-format=avc,alignment=au")
        )

        tee1 = self._make("tee", "tee1_encoded")

        tee0.link(q_encode)
        q_encode.link(encoder)
        encoder.link(parse0)
        parse0.link(caps_encoded)
        caps_encoded.link(tee1)

        # --- Rama de grabacion (Ambos) ---
        q_record = self._make("queue", "q_record")
        q_record.set_property("max-size-time", 2 * Gst.SECOND)

        splitmux = self._make("splitmuxsink", "evidence_sink")
        splitmux.set_property(
            "max-size-time", int(self.segment_seconds * Gst.SECOND)
        )
        splitmux.set_property(
            "location", os.path.join(self.evidence_dir, "segmento_%05d.mp4")
        )
        splitmux.connect("format-location", self._on_format_location)

        tee1.link(q_record)
        q_record.link(splitmux)

        # --- Rama de streaming en vivo (Ambos) ---
        q_stream = self._make("queue", "q_stream")
        q_stream.set_property("leaky", 2)  # 2 = downstream: descarta lo viejo
        q_stream.set_property("max-size-time", 1 * Gst.SECOND)

        payer = self._make("rtph264pay", "rtp_payer")
        payer.set_property("config-interval", 1)  # re-envia SPS/PPS cada 1 s
        payer.set_property("pt", 96)

        udpsink = self._make("udpsink", "rtp_sink")
        udpsink.set_property("host", self.stream_host)
        udpsink.set_property("port", self.stream_port)
        udpsink.set_property("sync", False)
        udpsink.set_property("async", False)

        tee1.link(q_stream)
        q_stream.link(payer)
        payer.link(udpsink)

        # --- Rama de analisis (Ambos; NUNCA procesa video, solo entrega) ---
        q_analysis = self._make("queue", "q_analysis")
        q_analysis.set_property("leaky", 2)  # descarta cuadros viejos
        q_analysis.set_property("max-size-buffers", 2)

        scale2 = self._make("videoscale", "analysis_scale")
        caps2a = self._make("capsfilter", "analysis_caps_small")
        caps2a.set_property(
            "caps",
            Gst.Caps.from_string(
                f"video/x-raw,format=NV12,width={self.analysis_width},"
                f"height={self.analysis_height}"
            ),
        )
        conv2 = self._make("videoconvert", "analysis_convert")
        caps2b = self._make("capsfilter", "analysis_caps_bgr")
        caps2b.set_property("caps", Gst.Caps.from_string("video/x-raw,format=BGR"))

        appsink = self._make("appsink", "analysis_sink")
        appsink.set_property("emit-signals", True)
        appsink.set_property("drop", True)
        appsink.set_property("max-buffers", 1)
        appsink.set_property("sync", False)
        appsink.connect("new-sample", self._on_new_sample)

        tee0.link(q_analysis)
        q_analysis.link(scale2)
        scale2.link(caps2a)
        caps2a.link(conv2)
        conv2.link(caps2b)
        caps2b.link(appsink)

        # --- Bus (Ambos: sin esto, un error o un EOS cuelgan la app) ---
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_bus_message)

    def _on_format_location(self, splitmux, fragment_id):
        """Ambos. Nombra cada segmento con timestamp + indice, para que
        retention.py pueda ordenarlos y para que sea facil ubicar a ojo el
        segmento vigente al momento de un evento."""
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"acceso_{stamp}_{fragment_id:05d}.mp4"
        path = os.path.join(self.evidence_dir, filename)
        self.current_segment_path = path
        return path

    # -- runtime --------------------------------------------------------

    def _on_new_sample(self, appsink):
        """Ambos. Entrega a la aplicacion un array NumPy BGR listo para
        OpenCV, junto con el timestamp del cuadro. AQUI es donde la
        aplicacion externa debe correr el reconocimiento facial; si hay
        identificacion positiva, la aplicacion (no este modulo) enciende
        el LED y llama retention.mark_as_event(self.current_segment_path).
        """
        sample = appsink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK

        buf = sample.get_buffer()
        caps = sample.get_caps()
        structure = caps.get_structure(0)
        width = structure.get_value("width")
        height = structure.get_value("height")

        ok, mapinfo = buf.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.OK
        try:
            import numpy as np  # import perezoso: solo hace falta aqui

            frame = np.frombuffer(mapinfo.data, dtype=np.uint8)
            frame = frame.reshape((height, width, 3)).copy()
        finally:
            buf.unmap(mapinfo)

        timestamp_ns = buf.pts
        if self.on_frame is not None:
            self.on_frame(frame, timestamp_ns)

        return Gst.FlowReturn.OK

    def _on_bus_message(self, bus, message):
        """Ambos. Manejo explicito del bus: ERROR, EOS y WARNING. Sin esto
        la aplicacion se cuelga cuando algo falla (camara desconectada,
        encoder no disponible, disco lleno, etc.)."""
        t = message.type
        if t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            texto = f"ERROR de GStreamer: {err} ({debug})"
            print(f"[pipeline] {texto}", file=sys.stderr)
            self.stop()
            if self.on_error is not None:
                self.on_error(texto)
        elif t == Gst.MessageType.EOS:
            texto = "Fin de flujo (EOS) inesperado en el pipeline"
            print(f"[pipeline] {texto}", file=sys.stderr)
            self.stop()
            if self.on_error is not None:
                self.on_error(texto)
        elif t == Gst.MessageType.WARNING:
            warn, debug = message.parse_warning()
            print(f"[pipeline] WARNING: {warn} ({debug})")
        elif t == Gst.MessageType.STATE_CHANGED:
            if message.src == self.pipeline:
                old, new, _pending = message.parse_state_changed()
                if old == Gst.State.PAUSED and new == Gst.State.PLAYING:
                    if self.on_ready is not None:
                        self.on_ready()

    def start(self):
        """Ambos. Arranca el pipeline (PLAYING). Requiere un GLib.MainLoop
        corriendo (o iterando el contexto por defecto) para que el bus
        despache los mensajes de _on_bus_message."""
        self.pipeline.set_state(Gst.State.PLAYING)

    def stop(self):
        """Ambos. Detiene el pipeline de forma ordenada."""
        if self.pipeline is not None:
            self.pipeline.set_state(Gst.State.NULL)


# ---------------------------------------------------------------------------
# Prueba de humo: corre el pipeline 15 s y guarda 5 cuadros de la rama de
# analisis a disco, para verificar que el appsink entrega datos utilizables.
# Aplica a Ambos (por defecto usa el perfil "host"; en la Pi, ejecutar con
# encoder_profile="produccion" tras confirmar v4l2h264enc con gst-inspect-1.0).
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import cv2  # solo hace falta para esta prueba de humo

    frames_guardados = {"n": 0}
    salida_dir = "./prueba_appsink"
    os.makedirs(salida_dir, exist_ok=True)

    def guardar_cuadro(frame_bgr, timestamp_ns):
        if frames_guardados["n"] >= 5:
            return
        nombre = os.path.join(
            salida_dir, f"cuadro_{frames_guardados['n']:02d}_{timestamp_ns}.png"
        )
        cv2.imwrite(nombre, frame_bgr)
        frames_guardados["n"] += 1
        print(f"[prueba] cuadro guardado: {nombre}")

    def al_fallar(mensaje):
        print(f"[prueba] el pipeline fallo: {mensaje}", file=sys.stderr)

    def al_estar_listo():
        print("[prueba] pipeline en PLAYING")

    pipeline = AccessControlPipeline(
        device=os.environ.get("DEVICE", DEFAULT_DEVICE),
        encoder_profile=os.environ.get("ENCODER_PROFILE", "host"),
        evidence_dir="./evidencia_prueba",
        on_frame=guardar_cuadro,
        on_error=al_fallar,
        on_ready=al_estar_listo,
    )

    loop = GLib.MainLoop()
    pipeline.start()

    def detener_por_tiempo():
        print("[prueba] 15 s cumplidos, deteniendo pipeline")
        pipeline.stop()
        loop.quit()
        return False

    GLib.timeout_add_seconds(15, detener_por_tiempo)

    try:
        loop.run()
    except KeyboardInterrupt:
        pipeline.stop()

    print(f"[prueba] total de cuadros guardados: {frames_guardados['n']}")


# ---------------------------------------------------------------------------
# Cambios respecto a la version anterior
# ---------------------------------------------------------------------------
# - Se elimino `decodebin` de la etapa de captura. En su lugar, un
#   capsfilter fuerza YUY2 crudo nativo (self.width x self.height x
#   self.framerate) directamente a la salida de v4l2src.
# - Se elimino el `videoscale` de la rama principal de captura: ya no hace
#   falta escalar, porque el capsfilter de captura pide directamente la
#   resolucion final (antes solo se llegaba a ella tras decodificar MJPEG
#   a 1280x720 y reducir con videoscale).
# - Se elimino el metodo `_on_decodebin_pad_added` y su conexion a la
#   senal "pad-added" (ya no hay pad dinamico: todo el enlace de la etapa
#   de captura es estatico).
# - Actualizado el docstring del modulo (forma del grafo y justificacion
#   de diseno) y el docstring de `_build_pipeline` para reflejar el nuevo
#   camino de captura y la justificacion de por que se elimino decodebin
#   en vez de mantenerlo como passthrough con caps forzados.
# - Sin cambios en: perfiles de encoder, valores DEFAULT_*, ramas de
#   codificacion/grabacion/streaming/analisis, manejo del bus, o stop().