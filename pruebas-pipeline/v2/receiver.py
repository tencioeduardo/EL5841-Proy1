#!/usr/bin/env python3
"""
receiver.py -- Aplicación receptora para el puesto de vigilancia.

Construye un pipeline simple GStreamer para recibir la señal RTP/UDP,
reproducirla con un overlay y manejar estados de conexión automáticamente.

Requisitos integrados:
- Reconstrucción/Reconexión automática ante EOS, ERROR o ausencia de UDP.
- Timeout configurable para la detección de pérdida de stream (pad probe).
- Registro (bitácora TSV) de cada evento de sesión.
- Superposición en pantalla (textoverlay) con estados: RECIBIENDO, SIN SEÑAL, RECONECTANDO.
"""

import argparse
import os
import sys
import time
import signal
from datetime import datetime

import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst, GLib  # noqa: E402

Gst.init(None)

class ReceiverLog:
    """Bitácora estilo TSV (Tab-Separated Values)."""
    def __init__(self, log_path="receptor_vigilancia.log"):
        self.log_path = log_path
        os.makedirs(os.path.dirname(os.path.abspath(self.log_path)) or ".", exist_ok=True)

    def _write(self, line: str):
        print(line)
        with open(self.log_path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def log_event(self, action: str, details: str = ""):
        stamp = datetime.now().isoformat(timespec="seconds")
        self._write(f"{stamp}\t{action}\t{details}")

class VigilanceReceiver:
    def __init__(self, port: int, timeout_secs: float = 3.0, fullscreen: bool = False):
        self.port = port
        self.timeout_secs = timeout_secs
        self.fullscreen = fullscreen
        self.log = ReceiverLog()
        
        self.pipeline = None
        self._elements = {}
        self._reconnecting = False
        self._last_packet_time = 0.0
        self._timeout_id = None
        self._loop = GLib.MainLoop()

    def _make(self, factory_name, name=None):
        el = Gst.ElementFactory.make(factory_name, name)
        if el is None:
            raise RuntimeError(f"No se pudo crear: {factory_name}")
        self.pipeline.add(el)
        self._elements[name or factory_name] = el
        return el

    def _build_pipeline(self):
        self.pipeline = Gst.Pipeline.new("vigilance-receiver")
        
        src = self._make("udpsrc", "udp_source")
        src.set_property("port", self.port)
        src.set_property(
            "caps", Gst.Caps.from_string(
                "application/x-rtp,media=video,encoding-name=H264,payload=96"
            )
        )

        # Pad probe para registrar llegada de paquetes RTP
        src_pad = src.get_static_pad("src")
        src_pad.add_probe(Gst.PadProbeType.BUFFER, self._on_buffer_received)

        jitter = self._make("rtpjitterbuffer", "jitter")
        depay = self._make("rtph264depay", "depay")
        dec = self._make("avdec_h264", "decoder")
        conv = self._make("videoconvert", "converter")
        
        overlay = self._make("textoverlay", "status_overlay")
        overlay.set_property("valignment", 2) # top
        overlay.set_property("halignment", 1) # center
        overlay.set_property("font-desc", "Sans, 32")
        self.set_overlay_text("INICIANDO")

        sink = self._make("autovideosink", "video_sink")

        # Vinculación del pipeline
        src.link(jitter)
        jitter.link(depay)
        depay.link(dec)
        dec.link(conv)
        conv.link(overlay)
        overlay.link(sink)

        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_bus_message)

    def set_overlay_text(self, text: str):
        if "status_overlay" in self._elements:
            self._elements["status_overlay"].set_property("text", text)

    def _on_buffer_received(self, pad, info):
        self._last_packet_time = time.monotonic()
        if self._elements.get("status_overlay").get_property("text") != "RECIBIENDO":
            self.set_overlay_text("RECIBIENDO")
        return Gst.PadProbeReturn.OK

    def _check_timeout(self):
        """Monitorea el pad probe periódicamente."""
        if not self._reconnecting and self.pipeline.get_state(0)[1] == Gst.State.PLAYING:
            if time.monotonic() - self._last_packet_time > self.timeout_secs:
                self.log.log_event("TIMEOUT", "Caída de recepción RTP")
                self.set_overlay_text("SIN SEÑAL")
                self.trigger_reconnect("timeout")
        return True

    def _on_bus_message(self, bus, message):
        t = message.type
        if t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            self.log.log_event("ERROR", f"{err} ({debug})")
            self.trigger_reconnect("error")
        elif t == Gst.MessageType.EOS:
            self.log.log_event("EOS", "Fin de flujo recibido")
            self.trigger_reconnect("eos")
        elif t == Gst.MessageType.STATE_CHANGED:
            if message.src == self.pipeline:
                old, new, pending = message.parse_state_changed()
                if new == Gst.State.PLAYING:
                    self._last_packet_time = time.monotonic()

    def trigger_reconnect(self, reason: str):
        """Inicia el proceso asíncrono de destrucción y reconstrucción."""
        if self._reconnecting:
            return
        self._reconnecting = True
        self.set_overlay_text("RECONECTANDO")
        self.log.log_event("RECONECTANDO", f"Iniciando ciclo por {reason}")
        
        def _reconnect_task():
            self._destroy_pipeline()
            self._build_pipeline()
            self.pipeline.set_state(Gst.State.PLAYING)
            self._reconnecting = False
            return False

        # Esperar 2 segundos antes de reintentar
        GLib.timeout_add_seconds(2, _reconnect_task)

    def _destroy_pipeline(self):
        if self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline = None
            self._elements.clear()

    def stop(self, signum=None, frame=None):
        self.log.log_event("DETENCION", "Cierre solicitado (SIGTERM/SIGINT)")
        if self._timeout_id:
            GLib.source_remove(self._timeout_id)
        
        if self.pipeline:
            self.pipeline.send_event(Gst.Event.new_eos())
            bus = self.pipeline.get_bus()
            if bus:
                bus.timed_pop_filtered(2 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
        
        self._destroy_pipeline()
        self._loop.quit()

    def run(self):
        self.log.log_event("INICIO", f"Receptor RTP en puerto {self.port}")
        self._build_pipeline()
        self.pipeline.set_state(Gst.State.PLAYING)
        
        # Iniciar verificador de timeout GLib
        self._timeout_id = GLib.timeout_add(1000, self._check_timeout)
        
        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)
        
        try:
            self._loop.run()
        except KeyboardInterrupt:
            self.stop()
        
        self.log.log_event("FIN", "Sesión terminada")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Receptor de vigilancia GStreamer")
    parser.add_argument("--port", type=int, default=5000, help="Puerto UDP RTP (default: 5000)")
    parser.add_argument("--fullscreen", action="store_true", help="Solicitar modo fullscreen (dependiente del sink)")
    args = parser.parse_args()

    receiver = VigilanceReceiver(port=args.port, fullscreen=args.fullscreen)
    receiver.run()