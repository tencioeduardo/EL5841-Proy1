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

Justificacion de las decisiones de diseno:
- Politica ante falla de camara (E3): Si el bus reporta ERROR (cámara
  desconectada, encoder no disponible), la aplicación debe morir con
  código distinto de cero. Se delega a systemd la responsabilidad de
  reiniciarlo. Reconstruir el pipeline tras perder /dev/videoX en el
  mismo proceso es más frágil que un arranque limpio.
- Segmentos más cortos (B.3): Se reduce de 300 s a 60 s para acotar la
  pérdida máxima de evidencia ante un corte abrupto de energía; el moov
  del segmento en curso se pierde, así que segmentos más cortos acotan
  la exposición. Un segmento de 60 s a la tasa medida (~1.33 Mbps) ocupa
  ~10 MB[cite: 1].
- GOP a 20 fps reales (B.1): Se ajusta a 40 cuadros (2 s) debido a que
  la cámara entrega 19.93 fps reales[cite: 1]. Esto equilibra el bitrate
  con el tiempo máximo que un receptor tardío espera por un keyframe.
- Queues sin leaky en captura y codificación: No se aplica leaky a estas
  ramas porque perder cuadros arbitrarios en el encoder rompe el bitstream
  H.264. Si el encoder se atrasa, la contrapresión frena la captura,
  evitando grabar evidencia corrupta.
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
    pass

from gi.repository import Gst, GLib  # noqa: E402

Gst.init(None)

ENCODER_PROFILES = {
    "host": {
        "factory": "x264enc",
        "properties": {
            "tune": "zerolatency",
            "speed-preset": "ultrafast",
            "bitrate": 2000,
            "key-int-max": 40,      # GOP de 2s asumiendo 20 fps reales
        },
    },
    "produccion": {
        "factory": "v4l2h264enc",
        "properties": {
            "extra-controls": (
                "controls,video_bitrate=2000000,"
                "h264_i_frame_period=40,h264_profile=4,h264_level=11"
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
DEFAULT_SEGMENT_SECONDS = 60  # 1 minuto por segmento para acotar perdida de moov


class AccessControlPipeline:
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

        self.current_segment_path = None
        self.pipeline = None
        self._elements = {}
        self._stopping = False
        self._build_pipeline()

    def _make(self, factory_name, name=None):
        el = Gst.ElementFactory.make(factory_name, name)
        if el is None:
            raise RuntimeError(f"No se pudo crear el elemento '{factory_name}'.")
        self.pipeline.add(el)
        self._elements[name or factory_name] = el
        return el

    def _build_pipeline(self):
        self.pipeline = Gst.Pipeline.new("access-control-pipeline")
        os.makedirs(self.evidence_dir, exist_ok=True)

        src = self._make("v4l2src", "camera_source")
        src.set_property("device", self.device)

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

        src.link(caps_raw)
        caps_raw.link(conv1)
        conv1.link(caps1)
        caps1.link(q_capture)
        q_capture.link(tee0)

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

        q_stream = self._make("queue", "q_stream")
        q_stream.set_property("leaky", 2)
        q_stream.set_property("max-size-time", 1 * Gst.SECOND)

        payer = self._make("rtph264pay", "rtp_payer")
        payer.set_property("config-interval", 1)
        payer.set_property("pt", 96)

        udpsink = self._make("udpsink", "rtp_sink")
        udpsink.set_property("host", self.stream_host)
        udpsink.set_property("port", self.stream_port)
        udpsink.set_property("sync", False)
        udpsink.set_property("async", False)

        tee1.link(q_stream)
        q_stream.link(payer)
        payer.link(udpsink)

        q_analysis = self._make("queue", "q_analysis")
        q_analysis.set_property("leaky", 2)
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

        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_bus_message)

    def _on_format_location(self, splitmux, fragment_id):
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"acceso_{stamp}_{fragment_id:05d}.mp4"
        path = os.path.join(self.evidence_dir, filename)
        self.current_segment_path = path
        return path

    def _on_new_sample(self, appsink):
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
            import numpy as np

            frame = np.frombuffer(mapinfo.data, dtype=np.uint8)
            frame = frame.reshape((height, width, 3)).copy()
        finally:
            buf.unmap(mapinfo)

        timestamp_ns = buf.pts
        if self.on_frame is not None:
            self.on_frame(frame, timestamp_ns)

        return Gst.FlowReturn.OK

    def _on_bus_message(self, bus, message):
        t = message.type
        if t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            texto = f"ERROR de GStreamer: {err} ({debug})"
            print(f"[pipeline] {texto}", file=sys.stderr)
            self.stop()
            if self.on_error is not None:
                self.on_error(texto)
        elif t == Gst.MessageType.EOS:
            if self._stopping:
                return
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
        self._stopping = False
        self.pipeline.set_state(Gst.State.PLAYING)

    def stop(self):
        """Ambos. Detiene el pipeline de forma ordenada.
        Envía un EOS antes de pasar el pipeline a NULL y espera
        (máximo 5 s) a que el bus lo reporte. Esto permite a splitmuxsink
        cerrar correctamente el moov atom.
        """
        if self.pipeline is not None and not self._stopping:
            self._stopping = True
            self.pipeline.send_event(Gst.Event.new_eos())
            bus = self.pipeline.get_bus()
            if bus:
                bus.timed_pop_filtered(5 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
            self.pipeline.set_state(Gst.State.NULL)


if __name__ == "__main__":
    import cv2

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
        # E3: Terminar proceso para que systemd lo reinicie limpio
        sys.exit(1)

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
# - Bug Fix de `stop()`: Ahora envía EOS y espera en el bus (timeout de 5 s)
#   antes de transicionar a NULL. Utiliza `self._stopping` para que
#   `_on_bus_message` no trate el EOS voluntario como error.
# - Decisión E3: `al_fallar` ahora ejecuta `sys.exit(1)` en lugar de sólo
#   imprimir. Se delega la reconstrucción a systemd ante fallas de bus.
# - Tamaño de Segmento: `DEFAULT_SEGMENT_SECONDS` se redujo de 300 s a 60 s
#   para limitar la pérdida de video por cortes de energía (el segmento
#   abierto pierde el moov).
# - Intervalo de GOP: ajustado `key-int-max` y `h264_i_frame_period` a 40.
#   Dado que la cámara funciona a ~20 fps, 40 asegura 2 segundos de
#   intervalo sin disparar la tasa de bits.