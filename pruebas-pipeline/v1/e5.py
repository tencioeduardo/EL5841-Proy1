#!/usr/bin/env python3
"""e5.py -- prueba de disco lleno / retencion sobre un tmpfs pequeno.
Variables: RET (1 = corre retention.RetentionPolicy; def. 0),
T_LLENAR (s en que se llena el disco a proposito con posix_fallocate;
0 = no llenar; def. 60), T_FIN (s totales; def. 240).
Requiere /mnt/tmpevid montado (ver comandos)."""
import os
import shutil

import gi
gi.require_version("Gst", "1.0")
from gi.repository import GLib  # noqa: E402

import pipeline as P  # noqa: E402
import retention as R  # noqa: E402

D = "/mnt/tmpevid"
RET = os.environ.get("RET", "0") == "1"
T_LLENAR = int(os.environ.get("T_LLENAR", "60"))
T_FIN = int(os.environ.get("T_FIN", "240"))

loop = GLib.MainLoop()


def on_error(msg):
    print("[E5] ON_ERROR:", msg)
    loop.quit()


a = P.AccessControlPipeline(
    device=os.environ.get("DEVICE", "/dev/video4"), evidence_dir=D,
    segment_seconds=30, on_error=on_error)

pol = None
if RET:
    pol = R.RetentionPolicy(D, log_path="./E5-bitacora.log",
                            check_interval_seconds=10)
    pol.start()


def estado():
    u = shutil.disk_usage(D)
    segs = len([f for f in os.listdir(D) if f.endswith(".mp4")])
    print(f"[E5] libre {u.free / u.total * 100:5.1f}%  segmentos={segs}")
    return True


def llenar():
    free = shutil.disk_usage(D).free
    fd = os.open(D + "/relleno.bin", os.O_CREAT | os.O_WRONLY)
    os.posix_fallocate(fd, 0, max(free - 1024 * 1024, 1))
    os.close(fd)
    print("[E5] disco llenado a proposito (queda ~1 MiB libre)")
    return False


def fin():
    print("[E5] fin de la prueba")
    loop.quit()
    return False


a.start()
GLib.timeout_add_seconds(10, estado)
if T_LLENAR > 0:
    GLib.timeout_add_seconds(T_LLENAR, llenar)
GLib.timeout_add_seconds(T_FIN, fin)
try:
    loop.run()
except KeyboardInterrupt:
    pass
a.stop()
if pol:
    pol.stop()
print("[E5] RET =", RET, "| archivos finales:", sorted(os.listdir(D)))
