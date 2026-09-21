#!/usr/bin/env python3
"""medir.py -- sondas de medicion sobre pipeline.py (NO lo modifica).
Debe correr en el mismo directorio que pipeline.py.
Variables: DEVICE (def. /dev/video4), SEG (def. 20 s), SLOW (def. 0: segundos
que duerme on_frame, para simular una aplicacion lenta), WARM (def. 2 s).
Mide: fps real de camara y de codificado (cuenta buffers con pad probes),
intervalo de keyframes, tasa codificada, tiempo del callback del appsink,
ocupacion de cada queue, y vuelca el grafo .dot si GST_DEBUG_DUMP_DOT_DIR
esta definido."""
import os
import time

import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst, GLib  # noqa: E402

import pipeline as P  # noqa: E402

DEVICE = os.environ.get("DEVICE", "/dev/video4")
SEG = float(os.environ.get("SEG", "20"))
SLOW = float(os.environ.get("SLOW", "0"))
WARM = float(os.environ.get("WARM", "2"))

t0 = [None]
cam_t, enc_t, key_t, key_i, cb_t = [], [], [], [], []
enc_bytes = [0]
app_n = [0]
occ = {}


def activo():
    return t0[0] is not None and (time.perf_counter() - t0[0]) >= WARM


# Se envuelve el callback del appsink ANTES de crear el pipeline, porque
# pipeline.py lo conecta (connect("new-sample", self._on_new_sample)) al
# construirse.
_orig = P.AccessControlPipeline._on_new_sample


def _timed(self, appsink):
    t = time.perf_counter()
    r = _orig(self, appsink)
    if activo():
        cb_t.append(time.perf_counter() - t)
    return r


P.AccessControlPipeline._on_new_sample = _timed


def on_frame(frame, ts_ns):
    if activo():
        app_n[0] += 1
    if SLOW:
        time.sleep(SLOW)


loop = GLib.MainLoop()


def on_error(msg):
    print("[medir] ON_ERROR:", msg)
    loop.quit()


a = P.AccessControlPipeline(
    device=DEVICE, evidence_dir="./evidencia_medicion",
    on_frame=on_frame, on_error=on_error,
)
E = a._elements


def p_cam(pad, info):
    if activo():
        cam_t.append(time.perf_counter())
    return Gst.PadProbeReturn.OK


def p_enc(pad, info):
    if activo():
        buf = info.get_buffer()
        enc_t.append(time.perf_counter())
        enc_bytes[0] += buf.get_size()
        if not buf.has_flags(Gst.BufferFlags.DELTA_UNIT):
            key_t.append(time.perf_counter())
            key_i.append(len(enc_t))
    return Gst.PadProbeReturn.OK


E["camera_caps_raw"].get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER, p_cam)
E["caps_encoded"].get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER, p_enc)

QS = ["q_capture", "q_encode", "q_record", "q_stream", "q_analysis"]


def muestrear():
    if activo():
        for n in QS:
            q = E[n]
            occ.setdefault(n, []).append(
                (q.get_property("current-level-time") / 1e6,
                 q.get_property("current-level-buffers")))
    return True


def volcar_dot():
    Gst.debug_bin_to_dot_file(
        a.pipeline, Gst.DebugGraphDetails.ALL, "pipeline-py-PLAYING")
    return False


def fin():
    loop.quit()
    return False


a.start()
t0[0] = time.perf_counter()
GLib.timeout_add(500, muestrear)
GLib.timeout_add_seconds(4, volcar_dot)
GLib.timeout_add_seconds(int(SEG + WARM), fin)
try:
    loop.run()
except KeyboardInterrupt:
    pass
a.stop()


def intervalos(ts):
    return [y - x for x, y in zip(ts, ts[1:])]


def resumen(nombre, ts):
    if len(ts) < 3:
        print(f"{nombre}: datos insuficientes ({len(ts)} buffers)")
        return
    d = intervalos(ts)
    dur = ts[-1] - ts[0]
    m = sum(d) / len(d)
    sd = (sum((x - m) ** 2 for x in d) / len(d)) ** 0.5
    print(f"{nombre}: {len(ts)} buffers en {dur:.2f} s -> "
          f"{(len(ts) - 1) / dur:.2f} fps | intervalo medio {m * 1000:.1f} ms, "
          f"desv {sd * 1000:.1f} ms, max {max(d) * 1000:.1f} ms")


print("== medir.py ==")
print(f"DEVICE={DEVICE} SEG={SEG:.0f}s WARM={WARM:.0f}s SLOW={SLOW}")
resumen("camara    (salida de camera_caps_raw)", cam_t)
resumen("codificado (salida de caps_encoded)   ", enc_t)
print(f"entregados a on_frame: {app_n[0]} (~{app_n[0] / SEG:.2f} fps; "
      "la rama de analisis descarta por diseno)")
if cam_t and enc_t:
    print(f"cuadros camara vs codificados: {len(cam_t)} vs {len(enc_t)} "
          f"(diferencia {len(cam_t) - len(enc_t)})")

if len(key_t) >= 2:
    kt = intervalos(key_t)
    ki = [y - x for x, y in zip(key_i, key_i[1:])]
    print(f"keyframes: {len(key_t)}; intervalo medio {sum(kt) / len(kt):.2f} s "
          f"({sum(ki) / len(ki):.1f} cuadros)")
else:
    print(f"keyframes: {len(key_t)} (ventana corta; usar SEG>=30)")

if len(enc_t) > 2:
    dur = enc_t[-1] - enc_t[0]
    bps = enc_bytes[0] * 8 / dur
    print(f"tasa codificada real: {bps / 1e6:.2f} Mbps = {bps / 8 / 1024:.0f} KB/s "
          f"= {bps / 8 * 3600 / 1e9:.2f} GB/h")

if cb_t:
    s = sorted(cb_t)
    print(f"callback appsink (_on_new_sample incl. on_frame): n={len(s)} "
          f"media {sum(s) / len(s) * 1000:.2f} ms, "
          f"p95 {s[max(int(len(s) * 0.95) - 1, 0)] * 1000:.2f} ms, "
          f"max {s[-1] * 1000:.2f} ms")

print("ocupacion de queues (media / max en ms; max en buffers):")
for n in QS:
    if n in occ and occ[n]:
        ms = [x[0] for x in occ[n]]
        bf = [x[1] for x in occ[n]]
        print(f"  {n:11s} {sum(ms) / len(ms):7.1f} / {max(ms):7.1f} ms   "
              f"max {max(bf)} buffers")
d = os.environ.get("GST_DEBUG_DUMP_DOT_DIR")
if d:
    print(f"grafo: {d}/pipeline-py-PLAYING.dot")
