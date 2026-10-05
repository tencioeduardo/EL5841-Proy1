#!/usr/bin/env python3
"""
puesto_vigilancia.py -- Aplicación del Puesto de Vigilancia (Host x86_64).

Integra:
1. Menú inicial de selección de entorno (Loopback vs Raspberry Pi).
2. Recepción de video UDP/RTP con conmutación a Standby automático.
3. Servidor UDP de eventos (Puerto 6001) para avisos instantáneos de escaneo.
4. Consola dinámica auto-actualizable con administración TCP (LIST, REG, DEL, PING).
5. Bitácora TSV persistente acumulativa en logs/receptor_vigilancia.log.
"""

import argparse
import os
import sys
import time
import socket
import threading
from datetime import datetime
from typing import Tuple

import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst, GLib  # noqa: E402

Gst.init(None)

# IP por defecto para la Raspberry Pi en red local
DEFAULT_RASPBERRY_PI_IP = "192.168.0.150"


class ReceiverLog:
    """Bitácora TSV acumulativa e independiente en disco."""

    def __init__(self, log_path: str = "logs/receptor_vigilancia.log"):
        self.log_path = os.path.abspath(log_path)
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        self._lock = threading.Lock()

    def log_event(self, action: str, details: str = ""):
        stamp = datetime.now().isoformat(timespec="seconds")
        line = f"{stamp}\t{action}\t{details}"
        with self._lock:
            try:
                with open(self.log_path, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except Exception as e:
                print(f"[ERROR_LOG] Error escribiendo en bitácora: {e}", file=sys.stderr)


class AutoRefreshingUI:
    """Administra la interfaz de consola interactiva con refresco ante eventos."""

    def __init__(self, ip_borde: str, stream_port: int, admin_port: int, event_port: int):
        self.ip_borde = ip_borde
        self.stream_port = stream_port
        self.admin_port = admin_port
        self.event_port = event_port

        self.video_status = "INICIALIZANDO"
        self.last_event_time = datetime.now().strftime("%H:%M:%S")
        self.last_event_msg = "Iniciando sistema de monitoreo..."
        self._lock = threading.Lock()

    def update_event(self, status: str, message: str):
        with self._lock:
            self.video_status = status
            self.last_event_time = datetime.now().strftime("%H:%M:%S")
            self.last_event_msg = message
            self.render()

    def render(self):
        sys.stdout.write("\033[H\033[J")
        sys.stdout.write("==================================================================\n")
        sys.stdout.write("       PUESTO DE VIGILANCIA - CONTROL DE ACCESO Y MONITOREO       \n")
        sys.stdout.write("==================================================================\n")
        sys.stdout.write(f" Estado Video:     [{self.video_status}]\n")
        sys.stdout.write(f" Nodo de Borde:    {self.ip_borde} (Video UDP: {self.stream_port} | TCP Admin: {self.admin_port})\n")
        sys.stdout.write(f" Último Evento:    [{self.last_event_time}] {self.last_event_msg}\n")
        sys.stdout.write("------------------------------------------------------------------\n")
        sys.stdout.write(" MENÚ DE ADMINISTRACIÓN DE ACCESO:\n")
        sys.stdout.write("   1. Consultar usuarios autorizados (LIST)\n")
        sys.stdout.write("   2. Registrar nuevo usuario (REG:)\n")
        sys.stdout.write("   3. Eliminar usuario (DEL:)\n")
        sys.stdout.write("   4. Probar conectividad TCP (PING)\n")
        sys.stdout.write("   5. Salir\n")
        sys.stdout.write("==================================================================\n")
        sys.stdout.write(" Seleccione una opción (1-5): ")
        sys.stdout.flush()


class EventListenerThread(threading.Thread):
    """Hilo secundario receptor de eventos UDP enviador por el Borde."""

    def __init__(self, port: int, ui: AutoRefreshingUI, log: ReceiverLog):
        super().__init__(name="EventListenerThread", daemon=True)
        self.port = port
        self.ui = ui
        self.log = log
        self.running = False
        self.sock = None

    def run(self):
        self.running = True
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            self.sock.bind(("0.0.0.0", self.port))
            while self.running:
                self.sock.settimeout(1.0)
                try:
                    data, _ = self.sock.recvfrom(2048)
                except socket.timeout:
                    continue

                if data:
                    msg = data.decode("utf-8").strip()
                    self.log.log_event("EVENTO_BORDE", msg)
                    self.ui.update_event(self.ui.video_status, msg)

        except Exception as e:
            if self.running:
                print(f"[event_listener_error] Error en listener UDP: {e}", file=sys.stderr)
        finally:
            if self.sock:
                self.sock.close()

    def stop(self):
        self.running = False


class VigilanceReceiverThread(threading.Thread):
    """Hilo receptor de video RTP con conmutación dinámica a Standby."""

    def __init__(
        self,
        port: int,
        ui: AutoRefreshingUI,
        display_width: int = 1280,
        display_height: int = 960,
        timeout_secs: float = 2.5,
        log_path: str = "logs/receptor_vigilancia.log",
    ):
        super().__init__(name="VigilanceReceiverThread", daemon=True)
        self.port = port
        self.ui = ui
        self.display_width = display_width
        self.display_height = display_height
        self.timeout_secs = timeout_secs
        self.log = ReceiverLog(log_path)

        self.mode = "STANDBY"
        self.pipeline = None
        self._elements = {}
        self.loop = None
        self._last_packet_time = 0.0
        self._timeout_id = None

    def _make(self, factory_name, name=None):
        el = Gst.ElementFactory.make(factory_name, name)
        if el is None:
            raise RuntimeError(f"No se pudo crear: {factory_name}")
        self.pipeline.add(el)
        self._elements[name or factory_name] = el
        return el

    def set_overlay_text(self, text: str):
        if "status_overlay" in self._elements:
            self._elements["status_overlay"].set_property("text", text)

    def _destroy_pipeline(self):
        if self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline = None
            self._elements.clear()

    def _build_standby_pipeline(self, text: str = "SIN SEÑAL"):
        self.pipeline = Gst.Pipeline.new("vigilance-standby")

        src = self._make("videotestsrc", "test_source")
        src.set_property("pattern", 0)
        src.set_property("is-live", True)

        caps = self._make("capsfilter", "test_caps")
        caps.set_property(
            "caps",
            Gst.Caps.from_string(
                f"video/x-raw,width={self.display_width},height={self.display_height},framerate=30/1"
            ),
        )

        overlay = self._make("textoverlay", "status_overlay")
        overlay.set_property("valignment", 2)
        overlay.set_property("halignment", 0)
        overlay.set_property("font-desc", "Sans, 28")
        overlay.set_property("shaded-background", True)
        overlay.set_property("text", text)

        sink = self._make("autovideosink", "video_sink")

        src.link(caps)
        caps.link(overlay)
        overlay.link(sink)

    def _build_rtp_pipeline(self):
        self.pipeline = Gst.Pipeline.new("vigilance-receiver")

        src = self._make("udpsrc", "udp_source")
        src.set_property("port", self.port)
        src.set_property(
            "caps",
            Gst.Caps.from_string("application/x-rtp,media=video,encoding-name=H264,payload=96"),
        )

        src_pad = src.get_static_pad("src")
        if src_pad:
            src_pad.add_probe(Gst.PadProbeType.BUFFER, self._on_buffer_received)

        jitter = self._make("rtpjitterbuffer", "jitter")
        depay = self._make("rtph264depay", "depay")
        dec = self._make("avdec_h264", "decoder")
        conv = self._make("videoconvert", "converter")

        scale = self._make("videoscale", "scaler")
        caps_scale = self._make("capsfilter", "caps_scale")
        caps_scale.set_property(
            "caps",
            Gst.Caps.from_string(
                f"video/x-raw,width={self.display_width},height={self.display_height}"
            ),
        )

        overlay = self._make("textoverlay", "status_overlay")
        overlay.set_property("valignment", 2)
        overlay.set_property("halignment", 0)
        overlay.set_property("font-desc", "Sans, 28")
        overlay.set_property("shaded-background", True)
        overlay.set_property("text", "RECIBIENDO")

        sink = self._make("autovideosink", "video_sink")

        src.link(jitter)
        jitter.link(depay)
        depay.link(dec)
        dec.link(conv)
        conv.link(scale)
        scale.link(caps_scale)
        caps_scale.link(overlay)
        overlay.link(sink)

        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_bus_message)

    def _on_buffer_received(self, pad, info):
        self._last_packet_time = time.monotonic()
        if self._elements.get("status_overlay") and self._elements["status_overlay"].get_property("text") != "RECIBIENDO":
            self.set_overlay_text("RECIBIENDO")
            self.log.log_event("RECIBIENDO", "Flujo de video UDP activo")
            self.ui.update_event("RECIBIENDO", "Flujo de video UDP activo H.264")
        return Gst.PadProbeReturn.OK

    def _switch_to_standby(self, reason: str, text: str = "SIN SEÑAL"):
        self.log.log_event("TIMEOUT" if "caída" in reason.lower() else "SIN_SEÑAL", reason)
        self._destroy_pipeline()
        self.mode = "STANDBY"
        self._build_standby_pipeline(text)
        self.pipeline.set_state(Gst.State.PLAYING)
        self.ui.update_event(f"STANDBY ({text})", f"Conmutado a Standby: {reason}")

    def _switch_to_rtp(self):
        self.log.log_event("RECONECTANDO", "Detectada señal UDP; enganchando RTP")
        self._destroy_pipeline()
        self.mode = "RTP"
        self._build_rtp_pipeline()
        self._last_packet_time = time.monotonic()
        self.pipeline.set_state(Gst.State.PLAYING)
        self.ui.update_event("TRANSMITIENDO", "Señal UDP detectada. Conectando decodificador RTP...")

    def _check_udp_data_available(self) -> bool:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.settimeout(0.1)
            s.bind(("0.0.0.0", self.port))
            data, _ = s.recvfrom(2048)
            s.close()
            return len(data) > 0
        except Exception:
            return False

    def _check_status_timer(self):
        if self.mode == "RTP":
            if time.monotonic() - self._last_packet_time > self.timeout_secs:
                self._switch_to_standby("Pérdida de paquetes UDP (Timeout)", "SIN SEÑAL")
        elif self.mode == "STANDBY":
            if self._check_udp_data_available():
                self._switch_to_rtp()
        return True

    def _on_bus_message(self, bus, message):
        t = message.type
        if t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            self.log.log_event("ERROR", f"{err} ({debug})")
            self._switch_to_standby(f"Error en pipeline: {err}", "SIN SEÑAL")
        elif t == Gst.MessageType.EOS:
            self.log.log_event("EOS", "Fin de flujo recibido")
            self._switch_to_standby("EOS recibido", "SIN SEÑAL")

    def run(self):
        self.log.log_event("INICIO", f"Receptor RTP en puerto {self.port}")
        self._switch_to_standby("Iniciando recepción de red...", "SIN SEÑAL")

        self._timeout_id = GLib.timeout_add(500, self._check_status_timer)
        self.loop = GLib.MainLoop()
        try:
            self.loop.run()
        except Exception as e:
            print(f"[receiver_thread] Excepción en bucle de video: {e}", file=sys.stderr)

    def stop(self):
        self.log.log_event("DETENCION", "Cierre solicitado")
        if self._timeout_id is not None:
            try:
                GLib.source_remove(self._timeout_id)
            except Exception:
                pass
            self._timeout_id = None
        self._destroy_pipeline()
        if self.loop and self.loop.is_running():
            self.loop.quit()
        self.log.log_event("FIN", "Sesión terminada")


def enviar_comando_tcp(ip_borde: str, puerto_admin: int, comando: str) -> Tuple[bool, str]:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3.0)
        s.connect((ip_borde, puerto_admin))
        s.sendall(comando.encode("utf-8"))

        respuesta = s.recv(1024).decode("utf-8").strip()
        s.close()
        return True, respuesta
    except Exception as e:
        return False, f"Error de conexión TCP con {ip_borde}:{puerto_admin} ({e})"


def seleccionar_modo_ejecucion() -> str:
    """Despliega el menú inicial para seleccionar entre Loopback y Red (Raspberry Pi)."""
    sys.stdout.write("\033[H\033[J")
    print("==================================================================")
    print("      PUESTO DE VIGILANCIA - SELECCIÓN DE ENTORNO DE RED         ")
    print("==================================================================")
    print("  1. Modo Loopback (Prueba local en este Host / 127.0.0.1)")
    print(f"  2. Modo Red (Raspberry Pi / IP predeterminada: {DEFAULT_RASPBERRY_PI_IP})")
    print("  3. Modo Red con IP personalizada")
    print("==================================================================")

    while True:
        try:
            opcion = input(" Seleccione una opción (1-3): ").strip()
        except (KeyboardInterrupt, EOFError):
            sys.exit(0)

        if opcion == "1":
            return "127.0.0.1"
        elif opcion == "2":
            return DEFAULT_RASPBERRY_PI_IP
        elif opcion == "3":
            ip_custom = input(f" Ingrese la IP de la Raspberry Pi: ").strip()
            if ip_custom:
                return ip_custom
            print(" IP inválida. Intente de nuevo.")
        else:
            print(" Opción no válida. Ingrese 1, 2 o 3.")


def main():
    parser = argparse.ArgumentParser(description="Puesto de Vigilancia - Host x86_64")
    parser.add_argument("--borde-ip", type=str, default=None, help="IP del Dispositivo de Borde (si se omite, se pregunta por menú)")
    parser.add_argument("--stream-port", type=int, default=5000, help="Puerto UDP para video RTP")
    parser.add_argument("--admin-port", type=int, default=6000, help="Puerto TCP para administración")
    parser.add_argument("--event-port", type=int, default=6001, help="Puerto UDP para escucha de eventos")
    args = parser.parse_args()

    # Si no se especifica --borde-ip por línea de comandos, mostrar el menú inicial
    borde_ip = args.borde_ip if args.borde_ip else seleccionar_modo_ejecucion()

    log_shared = ReceiverLog("logs/receptor_vigilancia.log")

    ui = AutoRefreshingUI(
        ip_borde=borde_ip,
        stream_port=args.stream_port,
        admin_port=args.admin_port,
        event_port=args.event_port,
    )

    listener_eventos = EventListenerThread(port=args.event_port, ui=ui, log=log_shared)
    listener_eventos.start()

    receptor_video = VigilanceReceiverThread(port=args.stream_port, ui=ui, log_path="logs/receptor_vigilancia.log")
    receptor_video.start()

    time.sleep(0.5)

    while True:
        ui.render()
        try:
            opcion = input().strip()
        except (KeyboardInterrupt, EOFError):
            print("\nCerrando la aplicación...")
            break

        if opcion == "1":
            ok, resp = enviar_comando_tcp(borde_ip, args.admin_port, "LIST")
            if ok and resp.startswith("OK: USUARIOS:"):
                usuarios = resp.replace("OK: USUARIOS:", "").split(",")
                ui.update_event(ui.video_status, f"Consulta LIST -> Usuarios ({len(usuarios)}): {', '.join(usuarios)}")
            else:
                ui.update_event(ui.video_status, f"Consulta LIST -> {resp}")

        elif opcion == "2":
            sys.stdout.write(" Ingrese ID del usuario a REGISTRAR: ")
            sys.stdout.flush()
            user = input().strip()
            if user:
                ok, resp = enviar_comando_tcp(borde_ip, args.admin_port, f"REG:{user}")
                ui.update_event(ui.video_status, f"Comando REG:{user} -> {resp}")
            else:
                ui.update_event(ui.video_status, "Operación REG cancelada (ID vacío)")

        elif opcion == "3":
            sys.stdout.write(" Ingrese ID del usuario a ELIMINAR: ")
            sys.stdout.flush()
            user = input().strip()
            if user:
                ok, resp = enviar_comando_tcp(borde_ip, args.admin_port, f"DEL:{user}")
                ui.update_event(ui.video_status, f"Comando DEL:{user} -> {resp}")
            else:
                ui.update_event(ui.video_status, "Operación DEL cancelada (ID vacío)")

        elif opcion == "4":
            ok, resp = enviar_comando_tcp(borde_ip, args.admin_port, "PING")
            ui.update_event(ui.video_status, f"Prueba PING -> {resp}")

        elif opcion == "5":
            print("\nCerrando Puesto de Vigilancia...")
            break
        else:
            ui.update_event(ui.video_status, f"Opción inválida '{opcion}'. Seleccione un número entre 1 y 5.")

    receptor_video.stop()
    listener_eventos.stop()
    print("Aplicación del Puesto de Vigilancia finalizada.")


if __name__ == "__main__":
    main()