#!/usr/bin/env bash
# prueba_hw.sh — Validación de hardware: control de acceso con video (Raspberry Pi 4)
#
# Uso:
#   ./prueba_hw.sh instalar            # dependencias (una vez, necesita internet)
#   ./prueba_hw.sh info                # modelo, SO, temperatura, alimentación
#   ./prueba_hw.sh camara              # detecta cámara y mide FPS
#   ./prueba_hw.sh captura             # graba 5 s a prueba.mkv
#   ./prueba_hw.sh stream IP_LAPTOP    # RTP/H.264 por UDP a la laptop (puerto 5000)
#   ./prueba_hw.sh tee IP_LAPTOP       # graba + transmite a la vez (como en la arquitectura)
#   ./prueba_hw.sh gpio [OUT] [IN]     # parpadea OUT (def. 17) y lee IN (def. 27), numeración BCM
#
# Receptor en la laptop:
#   gst-launch-1.0 udpsrc port=5000 caps="application/x-rtp,media=video,encoding-name=H264,payload=96" \
#     ! rtph264depay ! h264parse ! avdec_h264 ! videoconvert ! autovideosink sync=false

set -u
W=640; H=480; FPS=30; DUR=5; PORT=5000
CHIP=gpiochip0

sep(){ echo; echo "=== $* ==="; }

instalar(){
  sep "Instalando dependencias"
  sudo apt update
  for p in gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
           gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly gstreamer1.0-libcamera \
           libcamera-tools v4l-utils gpiod; do
    sudo apt install -y "$p" || echo "!! No se pudo instalar $p"
  done
}

info(){
  sep "Sistema"
  tr -d '\0' < /proc/device-tree/model; echo
  grep PRETTY_NAME /etc/os-release; uname -r
  vcgencmd measure_temp 2>/dev/null
  echo "throttled: $(vcgencmd get_throttled 2>/dev/null)  (0x0 = alimentación OK)"
  free -h | head -2; df -h / | tail -1
  gst-launch-1.0 --version | head -1
}

# Define SRC con la fuente de video. Se puede forzar: SRC="v4l2src device=/dev/video0" ./prueba_hw.sh ...
detectar_camara(){
  [ -n "${SRC:-}" ] && { echo "Fuente forzada: $SRC"; return; }
  if command -v cam >/dev/null && cam -l 2>/dev/null | grep -qE '^[0-9]+:'; then
    SRC="libcamerasrc"; echo "Cámara vía libcamera (CSI):"; cam -l | grep -E '^[0-9]+:'
    return
  fi
  for d in /dev/video*; do
    if v4l2-ctl -d "$d" --info 2>/dev/null | grep -q uvcvideo && \
       v4l2-ctl -d "$d" --list-formats 2>/dev/null | grep -q '\['; then
      SRC="v4l2src device=$d"; echo "Cámara USB en $d"
      v4l2-ctl -d "$d" --list-formats-ext | head -20
      return
    fi
  done
  echo "!! No se detectó cámara. Revisa conexión y 'v4l2-ctl --list-devices'."; exit 1
}

# Encoder H.264: hardware si existe, si no software
encoder(){
  if gst-inspect-1.0 v4l2h264enc >/dev/null 2>&1; then
    ENC="v4l2h264enc extra-controls=controls,repeat_sequence_header=1 ! video/x-h264,level=(string)4"
  else
    echo "(sin v4l2h264enc, usando x264enc por software)"
    ENC="x264enc tune=zerolatency speed-preset=ultrafast bitrate=1500"
  fi
}

CAPS="video/x-raw,width=$W,height=$H,framerate=$FPS/1"

camara(){
  sep "Cámara"; detectar_camara
  sep "Midiendo FPS 10 s (Ctrl+C para salir antes)"
  timeout -s INT 10 gst-launch-1.0 -e $SRC ! $CAPS ! videoconvert ! \
    fpsdisplaysink text-overlay=false video-sink=fakesink -v 2>&1 | grep --line-buffered -o 'current: [0-9.]*'
}

captura(){
  detectar_camara; encoder
  sep "Grabando $DUR s en prueba.mkv"
  timeout -s INT $DUR gst-launch-1.0 -e $SRC ! $CAPS ! videoconvert ! $ENC ! \
    h264parse ! matroskamux ! filesink location=prueba.mkv
  ls -lh prueba.mkv
}

stream(){
  [ -z "${1:-}" ] && { echo "Falta IP de la laptop"; exit 1; }
  detectar_camara; encoder
  sep "Transmitiendo a $1:$PORT (Ctrl+C para parar)"
  gst-launch-1.0 -e $SRC ! $CAPS ! videoconvert ! $ENC ! h264parse config-interval=-1 ! \
    rtph264pay pt=96 ! udpsink host="$1" port=$PORT
}

tee_prueba(){
  [ -z "${1:-}" ] && { echo "Falta IP de la laptop"; exit 1; }
  detectar_camara; encoder
  sep "Grabando a tee.mkv y transmitiendo a $1:$PORT (Ctrl+C para parar)"
  gst-launch-1.0 -e $SRC ! $CAPS ! videoconvert ! $ENC ! h264parse config-interval=-1 ! tee name=t \
    t. ! queue ! matroskamux ! filesink location=tee.mkv \
    t. ! queue ! rtph264pay pt=96 ! udpsink host="$1" port=$PORT
}

gpio(){
  OUT=${1:-17}; IN=${2:-27}
  sep "GPIO ($CHIP) salida BCM$OUT, entrada BCM$IN"
  if gpioget --version 2>&1 | grep -q 'v1\.'; then V=1; else V=2; fi
  echo "libgpiod v$V"
  set_pin(){ # $1 valor, $2 segundos
    if [ $V = 1 ]; then gpioset --mode=time --sec="$2" $CHIP "$OUT=$1"
    else timeout "$2" gpioset -c $CHIP "$OUT=$1"; fi
  }
  get_pin(){
    if [ $V = 1 ]; then gpioget -B pull-up $CHIP "$IN"
    else gpioget -c $CHIP -b pull-up "$IN"; fi
  }
  echo "Parpadeo x3 en BCM$OUT (LED con resistencia o módulo relé)"
  for i in 1 2 3; do set_pin 1 1; set_pin 0 1; done
  echo "Leyendo BCM$IN 10 veces (botón a GND con pull-up: suelto = 1 / active, presionado = 0 / inactive)"
  for i in $(seq 10); do echo "  $(get_pin)"; sleep 1; done
}

case "${1:-}" in
  instalar) instalar ;;
  info)     info ;;
  camara)   camara ;;
  captura)  captura ;;
  stream)   stream "${2:-}" ;;
  tee)      tee_prueba "${2:-}" ;;
  gpio)     gpio "${2:-}" "${3:-}" ;;
  *) sed -n '2,17p' "$0" ;;
esac
