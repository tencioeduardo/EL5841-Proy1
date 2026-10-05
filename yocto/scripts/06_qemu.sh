#!/usr/bin/env bash
# Build y prueba en QEMU (qemuarm64), sin hardware. Correr DENTRO del contenedor.
# OJO: la Pi compila para Cortex-A72 y QEMU para ARMv8 genérico, así que casi nada del
# sstate se reutiliza: es OTRO build completo. Úsalo cuando no tengas la Pi disponible.
# Uso: ./06_qemu.sh [build|run] [imagen]     Salir de QEMU: Ctrl+A y luego X
MODO=${1:-build}; IMG=${2:-control-acceso-image}
cd /workdir || exit 1
source poky/oe-init-build-env build-qemu >/dev/null || exit 1

if ! grep -q "control-acceso: inicio" conf/local.conf; then
  cp ../build/conf/bblayers.conf conf/bblayers.conf
  source ../scripts/hilos.sh
  sed -e "s/@BB@/$BB/" -e "s/@PM@/$PM/" ../conf/local.conf.append >> conf/local.conf
  cat ../conf/qemu.conf.append >> conf/local.conf
fi

case "$MODO" in
  build) time bitbake -k "$IMG" ;;
  run)   runqemu qemuarm64 "$IMG" nographic slirp ;;
esac
