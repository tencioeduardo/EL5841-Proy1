SUMMARY = "Grupos de paquetes del sistema de control de acceso"
LICENSE = "MIT"

# Algunos paquetes (libcamera, raspi-utils) dependen de la máquina
PACKAGE_ARCH = "${MACHINE_ARCH}"
inherit packagegroup

# Separado por área: la imagen elige cuáles instalar
PACKAGES = "${PN}-base ${PN}-video ${PN}-csi ${PN}-gpio ${PN}-debug"

RDEPENDS:${PN}-base = "python3-core python3-pygobject python3-numpy bash sistema-config"

# ---- Video: lo ajusta el responsable de GStreamer según su pipeline ----
# Plugins usados en prueba_hw.sh y dónde viven:
#   base: videoconvert                 good: v4l2src, v4l2h264enc, rtph264pay, matroskamux, udpsink
#   bad : h264parse (videoparsersbad)  core: tee, queue, fakesink
# base y good completos por ahora; de bad solo el plugin necesario.
RDEPENDS:${PN}-video = " \
    gstreamer1.0 \
    gstreamer1.0-plugins-base-meta \
    gstreamer1.0-plugins-good-meta \
    gstreamer1.0-plugins-bad-videoparsersbad \
    gstreamer1.0-python \
    v4l-utils \
    kernel-module-uvcvideo \
    kernel-module-bcm2835-codec \
"

# Solo si CAMARA = "csi"
RDEPENDS:${PN}-csi = "libcamera libcamera-gst"

RDEPENDS:${PN}-gpio = "libgpiod libgpiod-tools python3-gpiod"

# raspi-utils trae vcgencmd y pinctrl; solo existe para máquinas Raspberry Pi (override :rpi)
RDEPENDS:${PN}-debug = "htop nano strace i2c-tools"
RDEPENDS:${PN}-debug:append:rpi = " raspi-utils"
