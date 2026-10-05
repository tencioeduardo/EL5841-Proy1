#!/usr/bin/env bash
# Crea build/, agrega capas y aplica la configuración. Correr DENTRO del contenedor.
# Se puede correr varias veces sin duplicar nada.
cd /workdir || exit 1
source poky/oe-init-build-env build >/dev/null || exit 1

# Todas en una llamada (un solo parseo). El orden importa: dependencias primero.
bitbake-layers add-layer \
  ../meta-openembedded/meta-oe \
  ../meta-openembedded/meta-python \
  ../meta-openembedded/meta-multimedia \
  ../meta-raspberrypi \
  ../meta-control-acceso \
  || { echo "!! Falló add-layer (lee el error: suele indicar una capa faltante)"; exit 1; }

if ! grep -q "control-acceso: inicio" conf/local.conf; then
  source ../scripts/hilos.sh
  sed -e "s/@BB@/$BB/" -e "s/@PM@/$PM/" ../conf/local.conf.append >> conf/local.conf
  echo "Configuración agregada a conf/local.conf (BB_NUMBER_THREADS=$BB, PARALLEL_MAKE=-j $PM)"
else
  echo "local.conf ya tenía la configuración; no se tocó"
fi
bitbake-layers show-layers
