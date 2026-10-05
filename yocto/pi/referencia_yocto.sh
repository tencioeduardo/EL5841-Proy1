#!/usr/bin/env bash
# referencia_yocto.sh — Recolecta la configuración de Raspberry Pi OS que funciona,
# para replicarla en la imagen Yocto (meta-raspberrypi / meta-control-acceso).
#
# Uso: ./referencia_yocto.sh      → genera referencia_pi4_FECHA.tar.gz
# Correrlo DESPUÉS de confirmar que cámara y GPIO funcionan (prueba_hw.sh).

set -u
OUT="referencia_pi4_$(date +%Y%m%d_%H%M)"
mkdir -p "$OUT"
BOOT=/boot/firmware; [ -d "$BOOT" ] || BOOT=/boot

run(){ # $1 archivo, resto = comando
  f="$OUT/$1"; shift
  { echo "\$ $*"; "$@" 2>&1; } > "$f" || true
}

echo "Recolectando en $OUT/ ..."

# --- Arranque (config.txt → RPI_EXTRA_CONFIG, ENABLE_UART, VIDEO_CAMERA) ---
cp "$BOOT/config.txt"  "$OUT/config.txt"  2>/dev/null
cp "$BOOT/cmdline.txt" "$OUT/cmdline.txt" 2>/dev/null
run boot_config_efectiva.txt vcgencmd get_config int
run boot_config_str.txt      vcgencmd get_config str
run overlays_cargados.txt    dtoverlay -l   # solo los cargados en caliente; los de config.txt están en config.txt
run eeprom_version.txt       sudo rpi-eeprom-update
run eeprom_config.txt        sudo rpi-eeprom-config
run firmware_version.txt     vcgencmd version

# --- Kernel (versión, módulos → KERNEL_MODULE_AUTOLOAD / fragmentos .cfg) ---
run kernel.txt               uname -a
run lsmod.txt                lsmod
run modules_load.txt         sh -c 'cat /etc/modules /etc/modules-load.d/* 2>/dev/null'
sudo modprobe configs 2>/dev/null   # crea /proc/config.gz si el kernel lo trae como módulo
[ -e /proc/config.gz ] && zcat /proc/config.gz > "$OUT/kernel_config.txt"
[ -e "/boot/config-$(uname -r)" ] && cp "/boot/config-$(uname -r)" "$OUT/kernel_config.txt"
run dmesg_hw.txt             sh -c 'sudo dmesg | grep -iE "camera|imx|ov5647|unicam|uvc|video|gpio|bcm2835|codec"'
run device_tree_modelo.txt   sh -c 'tr -d "\0" < /proc/device-tree/model'

# --- Video (qué necesita IMAGE_INSTALL: libcamera, plugins GStreamer) ---
run v4l2_dispositivos.txt    v4l2-ctl --list-devices
run libcamera_camaras.txt    cam -l
run libcamera_version.txt    sh -c 'dpkg -l | grep -iE "libcamera|rpicam"'
run gst_version.txt          gst-launch-1.0 --version
run gst_plugins_clave.txt    sh -c 'for e in libcamerasrc v4l2src v4l2h264enc x264enc h264parse rtph264pay matroskamux tee; do printf "%-14s " $e; gst-inspect-1.0 $e >/dev/null 2>&1 && echo OK || echo NO; done'
run gst_paquetes.txt         sh -c 'dpkg -l | grep gstreamer'

# --- GPIO (libgpiod: versión y chip; en Yocto se usa lo mismo) ---
run gpio_chips.txt           gpiodetect
run gpio_lineas.txt          gpioinfo
run gpiod_version.txt        gpioget --version

# --- Sistema en general ---
run os.txt                   cat /etc/os-release
run servicios_activos.txt    systemctl list-units --type=service --state=running --no-pager
run grupos_usuario.txt       id
run udev_video_gpio.txt      sh -c 'ls -l /dev/video* /dev/gpiochip* /dev/media* 2>/dev/null'

tar czf "$OUT.tar.gz" "$OUT" && echo "Listo: $OUT.tar.gz"
