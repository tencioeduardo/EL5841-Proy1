# Guia de pruebas en el host

Sistema de control de acceso -- Raspberry Pi 4 / Yocto / GStreamer / Python
Camara USB (UVC), `/dev/videoX`, la misma que se usara en la Pi.

Esta guia asume el entorno ya verificado (ver `reporte-entorno.txt`: GStreamer
1.28.2, 274 plugins, `v4l2src` disponible, `x264enc` disponible, PyGObject
instalado, 2 fuentes de video detectadas). Si algo de eso falta, resuelvalo
antes de seguir.

Todos los comandos de esta guia estan tomados literalmente de
`host-test.sh`. Corralos desde el mismo directorio donde esta ese script.

---

## Paso 0 -- Identificar la camara correcta

Como el reporte de entorno detecto **2 fuentes de video**, primero hay que
confirmar cual `/dev/videoX` es la camara USB real (muchas veces un mismo
sensor UVC expone dos nodos: uno de captura y uno de metadata).

```bash
gst-device-monitor-1.0 Video/Source
```

**Que debe ver:** una entrada por cada fuente, con `device.path` apuntando a
algo como `/dev/video0`, `/dev/video2`, etc., y una lista de `caps` (formatos
que ofrece). Anote el `/dev/videoX` cuyos caps incluyan resoluciones de video
normales (640x480, 1280x720, ...) -- ese es el que va en `DEVICE=`.

**Como interpretar:**
- Si aparecen dos entradas con el mismo `card`, es normal en camaras UVC
  compuestas; use la que declare formatos `video/x-raw` o `image/jpeg` con
  varias resoluciones.
- Si no aparece ninguna fuente, la camara no esta conectada o el usuario no
  tiene permisos sobre `/dev/video*` (grupo `video`).

---

## Paso 1 -- Validar que la camara entrega video (comando a)

```bash
DEVICE=/dev/video0 ./host-test.sh a
```

**Que debe ver:** una ventana con el video en vivo de la camara, y en la
consola mensajes de GStreamer indicando el estado `PLAYING` y, con `-v`, las
caps negociadas en el pad de `v4l2src` forzadas por el comando:
`video/x-raw, format=(string)YUY2, width=(int)640, height=(int)480,
framerate=(fraction)30/1` (o los valores de `WIDTH`/`HEIGHT`/`FPS` que haya
puesto). El comando ya no deja que la camara negocie MJPEG libremente (ver
la nota al final del Paso 5): si ve `image/jpeg` en la salida, algo esta
mal (revise que no haya editado el `capsfilter` del comando).

**Errores probables:**
- `Device '/dev/video0' is not a capture device`: el numero de dispositivo
  no corresponde a la camara; repita el Paso 0.
- `Internal data stream error` justo despues de arrancar: normalmente un
  problema de permisos o la camara ya esta abierta por otro proceso
  (cierre cualquier app de videoconferencia u otra terminal con esta prueba
  corriendo).
- `Erroneous pipeline: ... not-negotiated` (o el pipeline arranca y muere
  de inmediato sin ventana): la camara no ofrece `YUY2` exactamente en el
  `WIDTH`/`HEIGHT`/`FPS` pedido. Confirme con `gst-device-monitor-1.0`
  (Paso 0) cuales son los modos YUY2 reales que anuncia la camara (la
  REDRAGON, por ejemplo, solo llega a YUY2 640x480@30 o 1280x720@10) y
  ajuste las variables de entorno para que coincidan.
- Ventana negra sin datos: la resolucion/formato que `autovideosink` recibio
  no es el que la camara realmente soporta; revise las caps con
  `gst-device-monitor-1.0` (Paso 0) y con `gst-discoverer-1.0` (Paso 5).

Cierre la ventana o `Ctrl+C` para terminar.

---

## Paso 2 -- Validar la grabacion segmentada (comando b)

```bash
DEVICE=/dev/video0 EVIDENCIA_DIR=./evidencia ./host-test.sh b
```

Dejelo correr unos 20-30 segundos y luego `Ctrl+C` (el pipeline usa `-e`,
asi que al recibir la senal cierra el ultimo segmento correctamente en vez
de dejarlo corrupto).

**Que debe ver:** en la consola, mensajes de `x264enc` negociando, y al
terminar, uno o mas archivos `segmento_00000.mp4`, `segmento_00001.mp4`, ...
dentro de `./evidencia/`. Con `max-size-time` en 300 000 000 000 ns (5 min),
en una prueba corta probablemente solo vera `segmento_00000.mp4`.

**Verificacion:**

```bash
gst-discoverer-1.0 ./evidencia/segmento_00000.mp4
```

Debe reportar un stream de video H.264, con la resolucion y framerate
configurados (`640x480@30` por defecto), duracion mayor a cero y sin
errores de "seekable: no" ni advertencias de indice corrupto (eso pasaria
si el proceso se mato con `kill -9` en vez de `Ctrl+C`).

**Errores probables:**
- `no element "x264enc"`: falta el paquete de plugins `ugly`/`bad` segun la
  distribucion del host; instalelo o revise con
  `gst-inspect-1.0 x264enc`.
- El archivo `.mp4` no reproduce o `gst-discoverer-1.0` marca duracion 0:
  el proceso se interrumpio sin EOS limpio; repita con `Ctrl+C` (una sola
  vez, esperando a que el proceso termine solo) en vez de forzar el cierre.

---

## Paso 3 -- Validar el streaming RTP/UDP (comando c)

Esta rama tiene DOS configuraciones posibles. Use la que corresponda segun
en que etapa este:

### 3.1 LOOPBACK (una sola maquina)

Util para validar rapido que el pipeline de red esta bien formado, sin
necesitar una segunda computadora a mano.

**Terminal 1 (receptor):**
```bash
./host-test.sh c-recv
```

**Terminal 2 (emisor, apuntando a 127.0.0.1):**
```bash
DEVICE=/dev/video0 ./host-test.sh c-loopback
```

**Que debe ver:** una ventana de video en la Terminal 1 con el video de la
camara, con la latencia tipica de `rtpjitterbuffer` (unos cientos de
milisegundos es normal en loopback).

**Importante -- que NO valida el loopback:** el loopback nunca toca una
tarjeta de red real, asi que no dice nada sobre jitter de una red
inalambrica o cableada real, ni sobre que pasa cuando el receptor se
conecta DESPUES de que el emisor ya esta transmitiendo (ahi es donde
importa `config-interval=1` en `rtph264pay`, que reenvia SPS/PPS cada
segundo para que un receptor tardio pueda decodificar sin reiniciar el
emisor). Por eso el laboratorio recomienda probar tambien con dos maquinas
en cuanto haya una segunda disponible.

### 3.2 DOS MAQUINAS (validacion real)

Es la UNICA configuracion que valida el caso de uso real del proyecto (el
puesto de vigilancia es otra computadora en la red local).

**En la maquina del puesto de vigilancia:**
```bash
./host-test.sh c-recv
```

**En la laptop con la camara, editando `PUESTO_IP` con la IP real:**
```bash
DEVICE=/dev/video0 PUESTO_IP=192.168.1.50 ./host-test.sh c-remoto
```

**Que debe ver:** lo mismo que en loopback, pero ahora si esta viajando por
la red real. Pruebe tambien arrancar el receptor 5-10 segundos DESPUES del
emisor, para confirmar que `config-interval=1` deja que el receptor
enganche el video sin reiniciar el emisor -- eso es lo que el loopback no
puede probar.

**Errores probables (ambas configuraciones):**
- Ventana negra en el receptor, sin errores en consola: casi siempre un
  firewall bloqueando el puerto UDP 5000 en la maquina receptora, o una IP
  equivocada en `PUESTO_IP`.
- `Could not receive any UDP packets`: revise que las dos maquinas esten en
  la misma red/subred y que el puerto coincida en emisor y receptor.

---

## Paso 4 -- Pipeline completo con las tres ramas (comando d)

```bash
DEVICE=/dev/video0 PUESTO_IP=192.168.1.50 EVIDENCIA_DIR=./evidencia ./host-test.sh d
```

Este es el equivalente en `gst-launch-1.0` del grafo completo que arma
`pipeline.py`: una camara, un solo `tee` para separar analisis de
codificacion, un encoder unico, un segundo `tee` para separar grabacion de
streaming, y la rama de analisis terminando en `fakesink` (en vez de
`appsink`, porque aqui no hay Python del otro lado).

**Que debe ver:** simultaneamente, nuevos archivos apareciendo en
`./evidencia/`, y si tiene un receptor RTP corriendo (Paso 3), video en
vivo ahi tambien. La consola no debe mostrar mensajes de `ERROR`; algunos
`WARNING` de negociacion al arrancar son normales.

Dejelo correr 20-30 segundos y presione `Ctrl+C`.

**Como interpretar fallas aqui:** si el comando (b) y el comando (c) por
separado funcionaron bien pero el (d) falla, el problema casi siempre esta
en la contencion del `tee` (una rama bloqueando a las demas por falta de
`queue`, o un `queue` sin `leaky` en la rama de analisis) -- revise que las
`queue` de la rama de analisis tengan `leaky=downstream`.

**Sintoma especifico ya visto y corregido:** streaming funcionando con
normalidad pero `splitmuxsink` sin escribir ningun archivo, sin `ERROR` ni
`WARNING` en el bus, incluso despues de un cierre ordenado con EOS. Este
comando ya trae el fix (un solo `h264parse` + `capsfilter(avc)` antes de
`tee1`, en vez de uno por rama); ver `notas-portabilidad.md`, seccion 8,
para el detalle completo. Si algun dia se reintroduce un `h264parse` por
rama y vuelve a aparecer este sintoma, es la primera pista a seguir.

---

## Paso 5 -- Verificacion final con las herramientas de diagnostico

```bash
gst-discoverer-1.0 ./evidencia/segmento_00000.mp4
gst-device-monitor-1.0 Video/Source
```

- `gst-discoverer-1.0` debe reportar el stream H.264 con la resolucion,
  framerate y duracion esperados, sin advertencias de "not seekable" ni de
  indice faltante.
- `gst-device-monitor-1.0` debe seguir listando la camara y sus caps
  reales; comparelas con las que forzo el pipeline (`NV12`,
  `640x480@30` por defecto) para confirmar que `video/x-raw,format=YUY2,
  width=640,height=480,framerate=30/1` sigue apareciendo entre los modos
  que la camara anuncia -- eso es lo que el capsfilter de captura le pide
  directamente a `v4l2src`, y de ahi solo falta un `videoconvert` (YUY2 ->
  NV12) para llegar al formato comun del resto del pipeline.

**Nota -- por que se fuerza YUY2 directo en vez de dejar que la camara
negocie MJPEG:** sin forzar nada, esta camara prioriza MJPEG a
1280x720@30 (ver `A1-caps.txt`), que despues habria que decodificar
(`jpegdec`) y reducir (`videoscale`) hasta 640x480@30 -- exactamente la
resolucion que la camara ya ofrece de forma nativa en crudo (YUY2). Forzar
YUY2 640x480@30 directo evita decodificar y volver a escalar: menos CPU
(relevante porque el reconocimiento facial correra en paralelo en la Pi),
menos elementos en el grafo, mismo resultado final. El costo es que USB
transfiere YUY2 sin comprimir en vez de MJPEG comprimido, pero a
640x480@30 son ~18 MB/s, holgado para USB 2.0.

---

## Lista de verificacion antes de pasar a la Raspberry Pi

- [ ] Paso 0: se identifico el `/dev/videoX` correcto de la camara USB.
- [ ] Paso 1 (comando a): la camara entrega video visible en `autovideosink`.
- [ ] Paso 2 (comando b): se generan segmentos `.mp4` validos, confirmados
      con `gst-discoverer-1.0`.
- [ ] Paso 3.1 (comando c-loopback): streaming RTP valida en una sola
      maquina.
- [ ] Paso 3.2 (comando c-remoto): streaming RTP validado con una SEGUNDA
      maquina real, incluyendo el caso de receptor que se conecta tarde.
- [ ] Paso 4 (comando d): el pipeline completo con tres ramas corre sin
      `ERROR` en el bus y produce grabacion + streaming simultaneos.
- [ ] `python3 pipeline.py` (perfil `host`) corre 15 s, no reporta errores
      de bus, y deja 5 archivos `.png` en `./prueba_appsink/`.
- [ ] `python3 retention.py` corre su auto-prueba sin `AssertionError` y
      escribe la bitacora esperada.
- [ ] Se anoto la version real de GStreamer del host (`gst-inspect-1.0
      --version`) para compararla contra la que reporte
      `gst-inspect-1.0 --version` en la Raspberry Pi una vez arrancada la
      imagen Yocto -- **nunca asuma que son la misma** (ver
      `notas-portabilidad.md`).

---

## Cambios respecto a la version anterior

- Paso 1: el ejemplo de caps esperadas ya no incluye la alternativa
  `image/jpeg` (MJPEG); ahora el comando fuerza YUY2 y ese es el unico
  resultado esperado. Se agrego un error probable nuevo:
  `not-negotiated` cuando la camara no ofrece YUY2 en el
  `WIDTH`/`HEIGHT`/`FPS` pedido.
- Paso 5: se quito la mencion a `decodebin` absorbiendo la diferencia
  entre lo que la camara entrega y lo que el pipeline espera, y se
  reemplazo por una verificacion directa de que YUY2 640x480@30 sigue
  entre los modos que anuncia la camara. Se agrego una nota explicando
  por que se prefiere forzar YUY2 directo en vez de dejar que la camara
  negocie MJPEG.
- Sin cambios en el resto de la guia: la estructura general, el Paso 0,
  los pasos 2, 3 y 4, y la lista de verificacion final quedan igual.