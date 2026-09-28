#!/usr/bin/env bash
# host-test.sh -- pipelines de validacion con gst-launch-1.0, equivalentes
# a lo que arma pipeline.py, para probar en el host ANTES de tocar Python.
#
# Uso: ./host-test.sh {a|b|c-loopback|c-remoto|c-recv|d}
#
#   a           captura + previsualizacion minima (valida la camara)
#   b           grabacion segmentada a disco
#   c-loopback  EMISOR RTP apuntando a 127.0.0.1 (una sola maquina)
#   c-remoto    EMISOR RTP apuntando al puesto de vigilancia (dos maquinas)
#   c-recv      RECEPTOR RTP (sirve para c-loopback y para c-remoto)
#   d           pipeline completo: tee + queues + 3 ramas (analisis con fakesink)
#
# Cada comando esta pensado para copiarse y correrse por separado (varios
# son interactivos o de larga duracion), no para encadenarse en un solo
# script no interactivo.
set -euo pipefail

DEVICE="${DEVICE:-/dev/video0}"
WIDTH="${WIDTH:-640}"
HEIGHT="${HEIGHT:-480}"
FPS="${FPS:-30}"
EVIDENCIA_DIR="${EVIDENCIA_DIR:-./evidencia}"
# IMPORTANTE: cambiar por la IP real del puesto de vigilancia (otra
# computadora en la red local). 192.168.1.50 es solo un valor de ejemplo.
PUESTO_IP="${PUESTO_IP:-192.168.1.50}"
PORT="${PORT:-5000}"

mkdir -p "$EVIDENCIA_DIR"

cmd_a() {
    # (a) Valida que la camara USB entrega video: captura cruda +
    # previsualizacion minima. Se fuerza YUY2 crudo nativo de la camara
    # (confirmado con gst-device-monitor-1.0) en WIDTH/HEIGHT/FPS en vez
    # de dejar que v4l2src negocie libremente y usar decodebin de
    # intermediario: la camara REDRAGON, sin forzar nada, prioriza MJPEG a
    # 1280x720@30 (ver A1-caps.txt), que luego habria que decodificar y
    # reducir. Si la camara no ofrece YUY2 exactamente en esos valores,
    # este comando falla con "not-negotiated" en vez de aceptar en
    # silencio el camino MJPEG mas caro.
    gst-launch-1.0 -v \
        v4l2src device="$DEVICE" \
        ! video/x-raw,format=YUY2,width="$WIDTH",height="$HEIGHT",framerate="$FPS"/1 \
        ! videoconvert \
        ! autovideosink
}

cmd_b() {
    # (b) Valida la grabacion segmentada a disco con la MISMA estructura
    # (formato NV12 comun, encoder host x264enc, framing avc) que usara
    # el pipeline final en pipeline.py. Captura: YUY2 crudo nativo forzado
    # directo en WIDTH/HEIGHT/FPS (ver cmd_a); sin videoscale porque ya no
    # hace falta reducir resolucion -- la camara entrega YUY2 justo en el
    # tamano de trabajo.
    gst-launch-1.0 -e \
        v4l2src device="$DEVICE" \
        ! video/x-raw,format=YUY2,width="$WIDTH",height="$HEIGHT",framerate="$FPS"/1 \
        ! videoconvert \
        ! video/x-raw,format=NV12,width="$WIDTH",height="$HEIGHT",framerate="$FPS"/1 \
        ! queue \
        ! x264enc tune=zerolatency speed-preset=ultrafast bitrate=2000 key-int-max=$((FPS * 2)) \
        ! h264parse \
        ! video/x-h264,stream-format=avc,alignment=au \
        ! splitmuxsink location="$EVIDENCIA_DIR/segmento_%05d.mp4" max-size-time=300000000000
}

cmd_c_send_loopback() {
    # (c) [EMISOR - LOOPBACK] host=127.0.0.1: valida que el pipeline de
    # red esta bien formado, TODO en una sola maquina. NO valida la red
    # real, jitter, ni config-interval con un receptor que se conecta
    # tarde -- para eso usar c-remoto (ver guia-pruebas.md).
    # Captura: YUY2 crudo nativo forzado directo (ver cmd_a); sin
    # videoscale porque ya no hace falta reducir resolucion.
    gst-launch-1.0 -v \
        v4l2src device="$DEVICE" \
        ! video/x-raw,format=YUY2,width="$WIDTH",height="$HEIGHT",framerate="$FPS"/1 \
        ! videoconvert \
        ! video/x-raw,format=NV12,width="$WIDTH",height="$HEIGHT",framerate="$FPS"/1 \
        ! queue \
        ! x264enc tune=zerolatency speed-preset=ultrafast bitrate=1500 key-int-max=$((FPS * 2)) \
        ! h264parse \
        ! video/x-h264,stream-format=byte-stream,alignment=au \
        ! rtph264pay config-interval=1 pt=96 \
        ! udpsink host=127.0.0.1 port="$PORT"
}

cmd_c_send_remoto() {
    # (c) [EMISOR - DOS MAQUINAS] host=<IP del puesto de vigilancia>: es
    # la UNICA configuracion que valida el caso de uso real del proyecto
    # (la vigilancia esta en otra computadora de la red local).
    # Captura: YUY2 crudo nativo forzado directo (ver cmd_a); sin
    # videoscale porque ya no hace falta reducir resolucion.
    gst-launch-1.0 -v \
        v4l2src device="$DEVICE" \
        ! video/x-raw,format=YUY2,width="$WIDTH",height="$HEIGHT",framerate="$FPS"/1 \
        ! videoconvert \
        ! video/x-raw,format=NV12,width="$WIDTH",height="$HEIGHT",framerate="$FPS"/1 \
        ! queue \
        ! x264enc tune=zerolatency speed-preset=ultrafast bitrate=1500 key-int-max=$((FPS * 2)) \
        ! h264parse \
        ! video/x-h264,stream-format=byte-stream,alignment=au \
        ! rtph264pay config-interval=1 pt=96 \
        ! udpsink host="$PUESTO_IP" port="$PORT"
}

cmd_c_recv() {
    # (c) [RECEPTOR] El mismo comando sirve para el caso loopback (otra
    # terminal, misma maquina) y para el caso de dos maquinas (correrlo en
    # la computadora del puesto de vigilancia). Lo unico que cambia entre
    # los dos casos es donde apunta el EMISOR, no el receptor.
    gst-launch-1.0 -v \
        udpsrc port="$PORT" \
            caps="application/x-rtp,media=video,encoding-name=H264,payload=96" \
        ! rtpjitterbuffer \
        ! rtph264depay \
        ! avdec_h264 \
        ! videoconvert \
        ! autovideosink
}

cmd_d() {
    # (d) Pipeline completo: tee + queues + tres ramas simultaneas
    # (grabacion, streaming, sink de analisis simulado con fakesink). Es
    # el equivalente en gst-launch de lo que arma pipeline.py, salvo que
    # aqui la rama de "analisis" termina en fakesink en vez de en appsink
    # (fakesink no requiere Python al otro lado).
    #
    # El H.264 se fuerza a "avc,alignment=au" UNA sola vez, con un solo
    # h264parse + un solo capsfilter, antes de tee1 -- NO un h264parse por
    # rama. mp4mux (dentro de splitmuxsink) solo acepta avc; rtph264pay
    # acepta avc y byte-stream, asi que avc sirve para las dos ramas.
    # (Una version anterior de este comando reconvertia el formato con un
    # h264parse propio en cada rama de tee1; en pruebas eso dejaba la rama
    # de grabacion bloqueada sin ningun error en el bus -- streaming
    # seguia funcionando, pero splitmuxsink nunca llegaba a abrir un
    # archivo. Se quito esa reconversion redundante.)
    #
    # Captura: YUY2 crudo nativo forzado directo en vez de decodebin (ver
    # cmd_a); sin videoscale porque ya no hace falta reducir resolucion.
    gst-launch-1.0 -e \
        v4l2src device="$DEVICE" \
        ! video/x-raw,format=YUY2,width="$WIDTH",height="$HEIGHT",framerate="$FPS"/1 \
        ! videoconvert \
        ! video/x-raw,format=NV12,width="$WIDTH",height="$HEIGHT",framerate="$FPS"/1 \
        ! tee name=t0 \
        t0. ! queue \
            ! x264enc tune=zerolatency speed-preset=ultrafast bitrate=2000 key-int-max=$((FPS * 2)) \
            ! h264parse \
            ! video/x-h264,stream-format=avc,alignment=au \
            ! tee name=t1 \
        t1. ! queue \
            ! splitmuxsink location="$EVIDENCIA_DIR/segmento_%05d.mp4" max-size-time=300000000000 \
        t1. ! queue leaky=downstream max-size-time=1000000000 \
            ! rtph264pay config-interval=1 pt=96 \
            ! udpsink host="$PUESTO_IP" port="$PORT" \
        t0. ! queue leaky=downstream max-size-buffers=2 \
            ! videoscale \
            ! video/x-raw,width=320,height=240 \
            ! videoconvert \
            ! video/x-raw,format=BGR \
            ! fakesink sync=false
}

case "${1:-}" in
    a) cmd_a ;;
    b) cmd_b ;;
    c-loopback) cmd_c_send_loopback ;;
    c-remoto) cmd_c_send_remoto ;;
    c-recv) cmd_c_recv ;;
    d) cmd_d ;;
    *)
        echo "Uso: $0 {a|b|c-loopback|c-remoto|c-recv|d}" >&2
        echo "Variables opcionales: DEVICE WIDTH HEIGHT FPS EVIDENCIA_DIR PUESTO_IP PORT" >&2
        exit 1
        ;;
esac

# -----------------------------------------------------------------------
# Cambios respecto a la version anterior
# -----------------------------------------------------------------------
# - En los cinco comandos (a, b, c-loopback, c-remoto, d) se reemplazo
#   `! decodebin` por un capsfilter explicito que fuerza
#   `video/x-raw,format=YUY2,width="$WIDTH",height="$HEIGHT",
#   framerate="$FPS"/1` justo despues de v4l2src.
# - En b, c-loopback, c-remoto y d se elimino el `videoscale` que seguia a
#   `videoconvert`: ya no hace falta escalar, porque el capsfilter de
#   captura pide directamente WIDTH x HEIGHT (antes solo se llegaba a esa
#   resolucion tras decodificar MJPEG a 1280x720 y reducir).
# - Se actualizaron los comentarios que describian a decodebin como el
#   elemento que "absorbe" YUY2/MJPEG, explicando el nuevo camino y por
#   que se prefiere forzar el caps exacto en vez de dejar que v4l2src
#   negocie libremente.
# - Sin cambios en: las variables de entorno (DEVICE, WIDTH, HEIGHT, FPS,
#   EVIDENCIA_DIR, PUESTO_IP, PORT), la estructura de tee0/tee1 y sus
#   ramas, ni el resto de cmd_d posterior a la captura.