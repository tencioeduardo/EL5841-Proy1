#!/usr/bin/env python3
"""correr-largo.py -- corrida continua del pipeline (sin retencion ni app)
para F1-F3. Termina con SIGINT/SIGTERM o si el bus reporta error (exit 1)."""
import os
import signal
import sys

import gi
gi.require_version("Gst", "1.0")
from gi.repository import GLib  # noqa: E402

import pipeline as P  # noqa: E402

codigo = [0]
loop = GLib.MainLoop()


def on_error(msg):
    print("[largo] ON_ERROR:", msg, file=sys.stderr, flush=True)
    codigo[0] = 1
    loop.quit()


a = P.AccessControlPipeline(
    device=os.environ.get("DEVICE", "/dev/video4"),
    evidence_dir="./evidencia_larga", segment_seconds=300,
    on_frame=lambda frame, ts: None, on_error=on_error)


def parar():
    print("[largo] senal recibida, deteniendo", flush=True)
    loop.quit()
    return False


GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, parar)
GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, parar)
a.start()
loop.run()
a.stop()
sys.exit(codigo[0])
