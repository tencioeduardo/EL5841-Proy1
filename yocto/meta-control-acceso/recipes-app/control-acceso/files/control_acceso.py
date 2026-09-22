#!/usr/bin/env python3
"""Placeholder: confirma que el servicio arranca y reporta el hardware visible.
Ver salida con: journalctl -u control-acceso -f
Se reemplaza por la aplicación real (pipeline GStreamer + GPIO)."""
import os
import sys
import time

def dispositivos(prefijo):
    return sorted(d for d in os.listdir("/dev") if d.startswith(prefijo))

def main():
    print("control-acceso: iniciado", flush=True)
    try:
        with open("/proc/device-tree/model") as f:
            print("modelo:", f.read().strip("\0\n"), flush=True)
    except OSError:
        print("modelo: desconocido (¿QEMU?)", flush=True)
    while True:
        print("video:", dispositivos("video") or "ninguno",
              "| gpio:", dispositivos("gpiochip") or "ninguno", flush=True)
        time.sleep(30)

if __name__ == "__main__":
    sys.exit(main())
