#!/usr/bin/env python3
"""
app_borde.py -- Aplicación principal del Dispositivo de Borde (Raspberry Pi / Host).

Integra:
1. Pipeline GStreamer (captura, streaming UDP, evidencia con retención).
2. Reconocimiento QR en tiempo real con cv2.QRCodeDetector sobre callback appsink.
3. Persistencia de usuarios en disco mediante JSON (config/usuarios_autorizados.json).
4. Actuación silenciosa en Borde (soporta LED integrado Sysfs, GPIO físico o simulación).
5. Emisor UDP de eventos de acceso hacia el Puesto de Vigilancia (Puerto 6001).
6. Servidor TCP de administración sincronizado (REG:, DEL:, LIST, PING en Puerto 6000).
"""

import os
import sys
import time
import json
import socket
import threading
from typing import Set, Optional

import cv2
import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib  # noqa: E402

from pipeline import AccessControlPipeline  # noqa: E402
from retention import RetentionPolicy  # noqa: E402

# Configuración y puertos por defecto
DEFAULT_ADMIN_PORT = 6000
DEFAULT_EVENT_PORT = 6001
DEFAULT_ACTUATION_GPIO_PIN = 18
DEFAULT_PULSE_DURATION_SEC = 3.0
DEFAULT_QR_COOLDOWN_SEC = 5.0
USERS_FILE_PATH = "config/usuarios_autorizados.json"

try:
    import RPi.GPIO as GPIO
    HAS_GPIO = True
except (ImportError, RuntimeError):
    HAS_GPIO = False


class EventNotifier:
    """Envía notificaciones de eventos UDP hacia el Puesto de Vigilancia."""

    def __init__(self, host: str, port: int = DEFAULT_EVENT_PORT):
        self.host = host
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send_event(self, message: str):
        try:
            self.sock.sendto(message.encode("utf-8"), (self.host, self.port))
        except Exception as e:
            print(f"[event_notifier_error] No se pudo enviar evento UDP: {e}", file=sys.stderr)


class AccessController:
    """Maneja el control de acceso, la persistencia JSON y la actuación física/simulada."""

    def __init__(
        self,
        notifier: Optional[EventNotifier] = None,
        pulse_duration: float = DEFAULT_PULSE_DURATION_SEC,
        gpio_pin: int = DEFAULT_ACTUATION_GPIO_PIN,
        perfil: str = "host",
        db_path: str = USERS_FILE_PATH,
    ):
        self.notifier = notifier
        self.pulse_duration = pulse_duration
        self.gpio_pin = gpio_pin
        self.perfil = perfil
        self.db_path = db_path
        self.users_lock = threading.Lock()

        # Garantizar existencia del directorio de configuración
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

        self.authorized_users: Set[str] = set()
        self._load_users_from_disk()

        self.last_scanned_code: Optional[str] = None
        self.last_scan_time: float = 0.0
        self.actuation_lock = threading.Lock()

        self.use_led_sysfs = False
        self.led_path: Optional[str] = None
        self._init_actuator()

    def _load_users_from_disk(self):
        """Carga los usuarios autorizados desde el archivo JSON o crea la lista por defecto."""
        default_users = {"USER_001", "USER_002", "ADMIN_KEY", "INVITADO_01"}
        if os.path.exists(self.db_path):
            try:
                with open(self.db_path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                    self.authorized_users = set(data.get("usuarios", default_users))
            except Exception as e:
                print(f"[db_error] Error leyendo {self.db_path}: {e}. Cargando valores por defecto.", file=sys.stderr)
                self.authorized_users = default_users
        else:
            self.authorized_users = default_users
            self._save_users_to_disk()

    def _save_users_to_disk(self):
        """Persiste el conjunto de usuarios en la carpeta config/."""
        try:
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            with open(self.db_path, "w", encoding="utf-8") as fh:
                json.dump({"usuarios": sorted(list(self.authorized_users))}, fh, indent=4)
        except Exception as e:
            print(f"[db_error] Error al guardar {self.db_path}: {e}", file=sys.stderr)

    def _init_actuator(self):
        """Configura el actuador según el entorno (LED integrado Sysfs, GPIO o Simulación)."""
        if self.perfil == "produccion":
            # Probar si existe LED integrado por Sysfs
            for possible_path in ["/sys/class/leds/ACT", "/sys/class/leds/led0"]:
                if os.path.exists(possible_path):
                    self.led_path = possible_path
                    self.use_led_sysfs = True
                    break

            if self.use_led_sysfs and self.led_path:
                try:
                    os.system(f"echo none > {self.led_path}/trigger 2>/dev/null")
                except Exception:
                    pass
            elif HAS_GPIO:
                try:
                    GPIO.setmode(GPIO.BCM)
                    GPIO.setup(self.gpio_pin, GPIO.OUT, initial=GPIO.LOW)
                except Exception as e:
                    print(f"[actuador] Error inicializando GPIO {self.gpio_pin}: {e}", file=sys.stderr)

    def register_user(self, username: str) -> bool:
        with self.users_lock:
            if username in self.authorized_users:
                return False
            self.authorized_users.add(username)
            self._save_users_to_disk()
            return True

    def delete_user(self, username: str) -> bool:
        with self.users_lock:
            if username not in self.authorized_users:
                return False
            self.authorized_users.remove(username)
            self._save_users_to_disk()
            return True

    def list_users(self) -> str:
        with self.users_lock:
            return ",".join(sorted(self.authorized_users))

    def is_authorized(self, username: str) -> bool:
        with self.users_lock:
            return username in self.authorized_users

    def actuate(self, username: str):
        """Ejecuta el pulso de apertura sin bloquear el hilo principal de video."""
        def _pulse():
            with self.actuation_lock:
                if self.notifier:
                    self.notifier.send_event(f"ACCESO CONCEDIDO: '{username}'")

                if self.perfil == "produccion":
                    if self.use_led_sysfs and self.led_path:
                        try:
                            bright_file = os.path.join(self.led_path, "brightness")
                            with open(bright_file, "w") as f:
                                f.write("1")
                            time.sleep(self.pulse_duration)
                            with open(bright_file, "w") as f:
                                f.write("0")
                        except Exception as e:
                            print(f"[actuador] Error en LED Sysfs: {e}", file=sys.stderr)
                    elif HAS_GPIO:
                        try:
                            GPIO.output(self.gpio_pin, GPIO.HIGH)
                            time.sleep(self.pulse_duration)
                            GPIO.output(self.gpio_pin, GPIO.LOW)
                        except Exception as e:
                            print(f"[actuador] Error en pulso GPIO: {e}", file=sys.stderr)
                    else:
                        time.sleep(self.pulse_duration)
                else:
                    time.sleep(self.pulse_duration)

        threading.Thread(target=_pulse, daemon=True).start()

    def process_qr_code(self, qr_data: str):
        now = time.monotonic()
        if qr_data == self.last_scanned_code and (now - self.last_scan_time) < DEFAULT_QR_COOLDOWN_SEC:
            return

        self.last_scanned_code = qr_data
        self.last_scan_time = now

        if self.is_authorized(qr_data):
            self.actuate(qr_data)
        else:
            if self.notifier:
                self.notifier.send_event(f"ACCESO DENEGADO: '{qr_data}' (No Autorizado)")

    def cleanup(self):
        if self.perfil == "produccion" and HAS_GPIO and not self.use_led_sysfs:
            try:
                GPIO.cleanup()
            except Exception:
                pass


class AdminServerThread(threading.Thread):
    """Servidor TCP para recepción de comandos remotos de administración."""

    def __init__(self, controller: AccessController, host: str = "0.0.0.0", port: int = DEFAULT_ADMIN_PORT):
        super().__init__(name="AdminServerThread", daemon=True)
        self.controller = controller
        self.host = host
        self.port = port
        self.server_socket = None
        self.running = False

    def run(self):
        self.running = True
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            self.server_socket.bind((self.host, self.port))
            self.server_socket.listen(5)

            while self.running:
                self.server_socket.settimeout(1.0)
                try:
                    client_sock, addr = self.server_socket.accept()
                except socket.timeout:
                    continue

                self._handle_client(client_sock)

        except Exception as e:
            if self.running:
                print(f"[servidor_admin] Error en servidor TCP: {e}", file=sys.stderr)
        finally:
            self.stop()

    def _handle_client(self, client_sock: socket.socket):
        try:
            client_sock.settimeout(3.0)
            data = client_sock.recv(1024)
            if not data:
                client_sock.close()
                return

            comando = data.decode("utf-8").strip()
            respuesta = self._procesar_comando(comando)
            client_sock.sendall(respuesta.encode("utf-8"))
        except Exception as e:
            err_resp = f"ERR: Excepción procesando comando: {e}"
            try:
                client_sock.sendall(err_resp.encode("utf-8"))
            except Exception:
                pass
        finally:
            client_sock.close()

    def _procesar_comando(self, cmd: str) -> str:
        if cmd == "PING":
            return "OK: PONG"
        elif cmd == "LIST":
            return f"OK: USUARIOS:{self.controller.list_users()}"
        elif cmd.startswith("REG:"):
            usuario = cmd[4:].strip()
            if not usuario:
                return "ERR: Nombre de usuario vacío"
            if self.controller.register_user(usuario):
                return f"OK: Usuario '{usuario}' registrado"
            return f"ERR: El usuario '{usuario}' ya existe"
        elif cmd.startswith("DEL:"):
            usuario = cmd[4:].strip()
            if not usuario:
                return "ERR: Nombre de usuario vacío"
            if self.controller.delete_user(usuario):
                return f"OK: Usuario '{usuario}' eliminado"
            return f"ERR: El usuario '{usuario}' no fue encontrado"
        else:
            return f"ERR: Comando desconocido '{cmd}'"

    def stop(self):
        self.running = False
        if self.server_socket:
            try:
                self.server_socket.close()
            except Exception:
                pass


def main():
    stream_host = os.environ.get("STREAM_HOST")
    if not stream_host:
        print("ERROR: Falta la variable STREAM_HOST. Ejemplo:\n  STREAM_HOST=192.168.0.10 python3 app_borde.py", file=sys.stderr)
        return 1

    stream_port = int(os.environ.get("STREAM_PORT", "5000"))
    admin_port = int(os.environ.get("ADMIN_PORT", str(DEFAULT_ADMIN_PORT)))
    event_port = int(os.environ.get("EVENT_PORT", str(DEFAULT_EVENT_PORT)))
    device = os.environ.get("DEVICE", "/dev/video0")
    perfil = os.environ.get("PERFIL", "host")
    evidencia = os.environ.get("EVIDENCIA", "./evidencia")

    notifier = EventNotifier(host=stream_host, port=event_port)
    controller = AccessController(notifier=notifier, perfil=perfil)
    qr_detector = cv2.QRCodeDetector()

    servidor_admin = AdminServerThread(controller=controller, port=admin_port)
    servidor_admin.start()

    def al_cuadro(frame_bgr, timestamp_ns):
        data, _, _ = qr_detector.detectAndDecode(frame_bgr)
        if data:
            controller.process_qr_code(data)

    def al_fallar(mensaje):
        loop.quit()

    def al_estar_listo():
        notifier.send_event("Nodo de Borde en PLAYING. Transmitiendo video...")

    pipeline = AccessControlPipeline(
        device=device,
        encoder_profile=perfil,
        evidence_dir=evidencia,
        stream_host=stream_host,
        stream_port=stream_port,
        on_frame=al_cuadro,
        on_error=al_fallar,
        on_ready=al_estar_listo,
    )

    politica_retencion = RetentionPolicy(evidence_dir=evidencia)
    politica_retencion.start(get_current_segment_cb=lambda: pipeline.current_segment_path)

    loop = GLib.MainLoop()
    pipeline.start()

    try:
        loop.run()
    except KeyboardInterrupt:
        pass
    finally:
        pipeline.stop()
        politica_retencion.stop()
        servidor_admin.stop()
        controller.cleanup()

    return 0


if __name__ == "__main__":
    sys.exit(main())