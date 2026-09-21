# Notas de portabilidad: host -> Raspberry Pi 4

Sistema de control de acceso -- Yocto Project / GStreamer / Python
PyGObject.

## 1. Regla de diseno

El pipeline de `pipeline.py` esta pensado para que, al pasar del host
(laptop x86_64) a la Raspberry Pi 4, **solo cambie el elemento
codificador**. Todo lo demas -- captura (`v4l2src` forzando YUY2 crudo
nativo), `videoconvert`, formato comun `NV12`, `tee`s, `queue`s,
segmentacion, streaming RTP y appsink -- es identico en ambos entornos.
Esto se logra forzando `NV12` como formato de intercambio entre la etapa
de captura y la etapa de codificacion: es un formato que tanto `x264enc`
(software) como `v4l2h264enc` (hardware V4L2 de la Pi) aceptan sin
conversion adicional.

La etapa de captura misma tampoco necesita `decodebin` ni `videoscale`:
la camara USB entrega YUY2 crudo nativo justo en la resolucion/framerate
de trabajo (640x480@30 por defecto), asi que un `capsfilter` fuerza ese
caps directo sobre `v4l2src` y un solo `videoconvert` (YUY2 -> NV12) basta
para llegar al formato comun. Como esto depende solo de los caps que la
camara anuncia -- identicos en host y Pi, ver seccion 2 -- no introduce
ninguna diferencia nueva entre entornos.

## 2. Tabla comparativa del pipeline completo

| Etapa | Host (x86_64) | Raspberry Pi 4 | Cambia? |
|---|---|---|---|
| Captura | `v4l2src device=/dev/videoX` | `v4l2src device=/dev/videoX` | No |
| Camara | USB UVC | USB UVC (misma clase de dispositivo) | No |
| Formato crudo forzado en captura | `capsfilter` fuerza `video/x-raw,format=YUY2,640x480@30` directo sobre `v4l2src` | igual | No |
| Conversion | `videoconvert` (YUY2 -> NV12, sin escalado) | `videoconvert` (igual) | No |
| Formato comun forzado | `video/x-raw,format=NV12,...` | `video/x-raw,format=NV12,...` | No |
| Codificador H.264 | `x264enc` (software, GPL, permitido en desarrollo) | `v4l2h264enc` (hardware del SoC) | **Si** |
| Parseo / framing | `h264parse` (avc para mux, byte-stream para RTP) | `h264parse` (igual) | No |
| Segmentacion / grabacion | `splitmuxsink` | `splitmuxsink` | No |
| Streaming | `rtph264pay ! udpsink` | `rtph264pay ! udpsink` | No |
| Entrega a analisis | `appsink` (BGR, resolucion reducida) | `appsink` (igual) | No |
| Decodificacion (si hiciera falta revisar evidencia) | `avdec_h264` | `v4l2h264dec` (hardware) | Si, pero **no se usa en este pipeline** (solo se graba y transmite, no se decodifica en el propio equipo) |

Si en algun momento se necesita reproducir o reprocesar evidencia grabada
*en la propia Pi*, ahi si entraria `v4l2h264dec`; en este proyecto esa
etapa no aparece porque el pipeline solo produce video, no lo consume.

## 3. Encoder: parametros equivalentes

| Parametro | `x264enc` (host) | `v4l2h264enc` (Pi) |
|---|---|---|
| Baja latencia | `tune=zerolatency` | No existe una propiedad equivalente directa; se logra con el tamano de cola del driver V4L2 (por defecto ya es bajo) y con `h264_i_frame_period` moderado |
| Perfil de velocidad | `speed-preset=ultrafast` | No aplica (es hardware fijo) |
| Bitrate | `bitrate=2000` (kbps) | `extra-controls="controls,video_bitrate=2000000,..."` (bps, va dentro de una `GstStructure` de controles V4L2) |
| Intervalo de keyframe | `key-int-max=60` | `h264_i_frame_period=60` (dentro de `extra-controls`) |
| Perfil/nivel H.264 | negociado por caps | `h264_profile`, `h264_level` (dentro de `extra-controls`) |

**Advertencia importante:** los nombres exactos de los controles V4L2 que
acepta `extra-controls` (`video_bitrate`, `h264_i_frame_period`,
`h264_profile`, `h264_level`, sus valores enteros para perfil/nivel, etc.)
dependen del driver del SoC y de la version del kernel/GStreamer de la
imagen Yocto. **Se verifican en el equipo destino**, no se asumen:

```bash
gst-inspect-1.0 v4l2h264enc
v4l2-ctl --list-ctrls -d /dev/video11   # el nodo de "m2m" suele variar
```

Ademas, a diferencia de `x264enc`, `v4l2h264enc` no siempre anuncia
`level`/`profile` en sus caps de salida; si el muxer o el pay lo exigen
explicito, se agrega un `capsfilter` despues del encoder, por ejemplo
`video/x-h264,level=(string)4`, ajustado a lo que reporte
`gst-inspect-1.0` en la Pi.

## 4. Decoder (si hiciera falta en el futuro)

| Tarea | Host | Pi 4 |
|---|---|---|
| Decodificar H.264 | `avdec_h264` (software, FFmpeg/libav) | `v4l2h264dec` (hardware) |

Util para una futura herramienta de revision de evidencia que reproduzca
los `.mp4` grabados; no forma parte del pipeline de captura de este
proyecto.

**Nota sobre Raspberry Pi 5:** este proyecto apunta a Pi 4, que si tiene
`v4l2h264enc`/`v4l2h264dec` por hardware. La Pi 5 NO tiene codificador
H.264 por hardware (solo decodificador limitado); un pipeline con
`v4l2h264enc` copiado de un tutorial viejo falla en Pi 5 con
`no element "v4l2h264enc"`. No aplica a este proyecto, pero es el primer
punto a revisar si en el futuro se porta a Pi 5.

## 5. Dependencias de Yocto

Con `meta-raspberrypi` como capa de BSP sobre una imagen minima/base:

```bitbake
IMAGE_INSTALL:append = " gstreamer1.0 \
                         gstreamer1.0-plugins-base \
                         gstreamer1.0-plugins-good \
                         gstreamer1.0-plugins-bad \
                         gstreamer1.0-tools \
                         python3-pygobject"
```

- **Camara USB (UVC):** basta con `gstreamer1.0-plugins-good`, que ya trae
  `v4l2src`. **No hace falta** `gstreamer1.0-libcamera`: esa capa es para
  la camara CSI nativa de la Pi (acceso por `libcamerasrc`), y este
  proyecto usa explicitamente una camara USB UVC, la misma en host y en
  Pi. Si en el futuro el proyecto migrara a la camara CSI, ahi si entraria
  `gstreamer1.0-libcamera` y el elemento `libcamerasrc` en vez de
  `v4l2src`.
- **Aceleracion por hardware (`v4l2h264enc`):** la aporta la capa del BSP
  (`meta-raspberrypi`), no `openembedded-core`. No hay que agregar un
  paquete de plugins aparte para esto; lo que hay que confirmar es que la
  imagen incluya el driver V4L2 M2M del SoC habilitado (parte del kernel
  de `meta-raspberrypi`) y que `gst-inspect-1.0` lo vea en el destino.
- **Herramientas de diagnostico** (`gst-inspect-1.0`,
  `gst-device-monitor-1.0`, `gst-discoverer-1.0`, `gst-launch-1.0`): vienen
  en `gstreamer1.0-tools`. Se agregan siempre a la imagen de desarrollo y
  son de las primeras cosas a quitar de la imagen de produccion final.
- **PyGObject:** `python3-pygobject` (y sus dependencias GI estandar de
  `openembedded-core`).
- **OpenCV:** no forma parte de este pipeline de GStreamer, pero si la
  aplicacion completa la necesita en la imagen, se agrega por separado
  (por ejemplo via `meta-openembedded/meta-oe` u otra capa que provea la
  receta de OpenCV para Python), fuera del alcance de estos cinco
  archivos.

**No se asume la version de GStreamer de la imagen Yocto.** Se confirma en
el destino, ya arrancada la imagen:

```bash
gst-inspect-1.0 --version
gst-inspect-1.0 | grep -iE "v4l2|x264"
```

## 6. Lista de verificacion (checklist) al arrancar la imagen en la Pi

1. `gst-inspect-1.0 --version`: anotar la serie real (puede no coincidir
   ni con el host de desarrollo ni con la rama `master` de
   `openembedded-core`; son numeros independientes).
2. `gst-inspect-1.0 v4l2src`, `gst-inspect-1.0 v4l2h264enc`,
   `gst-inspect-1.0 h264parse`, `gst-inspect-1.0 splitmuxsink`,
   `gst-inspect-1.0 rtph264pay`: confirmar que los cinco existen antes de
   correr `pipeline.py` con `encoder_profile="produccion"`.
3. `gst-device-monitor-1.0 Video/Source` con la camara USB conectada:
   confirmar el nodo `/dev/videoX` real en la Pi (puede no ser el mismo
   numero que en el host) y los caps que ofrece.
4. `v4l2-ctl --list-ctrls -d /dev/videoN` sobre el nodo M2M del
   codificador: confirmar los nombres de control que va a usar
   `extra-controls` en `ENCODER_PROFILES["produccion"]`.
5. Correr `host-test.sh b` (grabacion) y `host-test.sh c-remoto`
   (streaming hacia el puesto de vigilancia) directamente en la Pi,
   reemplazando `x264enc` por `v4l2h264enc` con sus parametros, antes de
   correr `pipeline.py` completo.
6. Confirmar con `gst-discoverer-1.0` que los `.mp4` generados en la Pi
   tienen perfil, resolucion y bitrate razonables (el hardware a veces
   fuerza un perfil/nivel distinto al pedido).
7. Medir la latencia de streaming **en la Pi**, no en la laptop: el costo
   de codificacion por software en el host no es representativo del
   hardware de la Pi (y viceversa).

## 7. Licencias: por que `x264enc` no va en produccion

`x264enc` es **GPL**. Es la opcion correcta para el host de desarrollo (se
usa solo localmente, sin distribuirse), pero es una decision de licencia
incompatible con un producto embebido de firmware cerrado, ademas de
consumir CPU que la Pi necesita para OpenCV y el resto de la aplicacion.
Por eso `ENCODER_PROFILES["produccion"]` en `pipeline.py` usa
`v4l2h264enc` (hardware del SoC, sin el problema de licencia ni el costo
de CPU) y el codigo nunca selecciona `x264enc` para ese perfil.

Alternativas si en algun momento no hubiera hardware disponible:

| Situacion | Opcion |
|---|---|
| El SoC tiene codificador por hardware (caso de la Pi 4) | `v4l2h264enc` -- la opcion usada aqui |
| Solo software, producto cerrado | `openh264enc` (licencia mas permisiva; igual revisar patentes de H.264) |
| Producto abierto o uso interno | `x264enc` sin problema |

Yocto permite hacer que la construccion **falle** si se cuela una licencia
incompatible en la imagen de produccion, en vez de generar en silencio una
imagen no distribuible:

```bitbake
INCOMPATIBLE_LICENSE = "GPL-3.0-only LGPL-3.0-only"
```

## 8. Hallazgo de pruebas: un solo `h264parse` antes de `tee1`, no uno por rama

Version inicial de este diseno: un `h264parse` general antes de `tee1`, y
CADA rama de `tee1` con su propio `h264parse` + `capsfilter` para pedir el
"framing" que cada consumidor necesitaba (`avc` para `splitmuxsink`,
`byte-stream` para `rtph264pay`). Al probar en el host (ver
`guia-pruebas.md`, Paso 4) esto dio dos sintomas, en dos intentos:

1. **Sin ningun `h264parse`/`capsfilter` entre el `h264parse` general y
   `tee1`:** error `not-negotiated (-4)`. Esperable: `tee1` reparte el
   MISMO flujo a las dos ramas, y no puede satisfacer simultaneamente un
   `capsfilter` de `avc` en una rama y uno de `byte-stream` en la otra sin
   que algo convierta el formato primero.
2. **Con un `h264parse` propio por rama** (para hacer esa conversion):
   sin ningun `ERROR` ni `WARNING` en el bus, el pipeline llegaba a
   `PLAYING` y la rama de streaming funcionaba con normalidad, pero
   `splitmuxsink` nunca abria ningun archivo -- ni siquiera uno parcial --
   tras 30+ segundos y un cierre ordenado con EOS (`-e` + `Ctrl+C`).

**Causa raiz mas probable:** `mp4mux` (el muxer que usa `splitmuxsink`
internamente) **solo acepta H.264 en formato `avc,alignment=au`**; nunca
acepta `byte-stream`. `rtph264pay`, en cambio, acepta `avc` igual que
`byte-stream` desde hace varias versiones de `gst-plugins-good` (mucho
antes de la 1.28). Como `avc` ya satisface a los dos consumidores, no hace
falta reconvertir por rama; el segundo `h264parse` de la rama de grabacion
era una reconversion redundante (avc que ya venia siendo avc), y algo en
esa reconversion -- probablemente relacionado con como se propaga la
marca de "es un keyframe" al reconstruir el bitstream, o un `queue` sin
`leaky` bloqueado a la espera de que `splitmuxsink` complete su cambio de
estado interno -- dejaba a `splitmuxsink` sin recibir nunca un buffer
utilizable, en silencio (sin `ERROR` visible porque el bloqueo ocurre
dentro del hilo propio de esa rama, sin afectar a la rama de streaming).
No se confirmo la causa exacta a nivel de codigo de GStreamer; para
confirmarla haria falta correr con
`GST_DEBUG=h264parse:5,splitmuxsink:6,queue:4` y revisar el log en
detalle, o insertar un elemento `identity silent=false` justo antes de
`splitmuxsink` para ver si realmente llegan buffers.

**Fix aplicado (en `pipeline.py` y en `host-test.sh cmd_d`):** se fuerza
`video/x-h264,stream-format=avc,alignment=au` **una sola vez**, con un
solo `h264parse` + un solo `capsfilter`, ANTES de `tee1`. Las dos ramas de
`tee1` cuelgan directo de ahi (con su propia `queue` para el hilo, nada
mas): `splitmuxsink` recibe exactamente el formato que exige, y
`rtph264pay` recibe `avc`, que tambien acepta. Se elimino por completo el
segundo `h264parse` (y su `capsfilter`) de cada rama. Esto ademas es mas
barato en CPU (un parseo en vez de dos) y mas simple de leer.

**Pendiente de confirmar en la Pi 4:** este hallazgo se probo solo en el
host con `x264enc`. El codificador de la Pi (`v4l2h264enc`) puede negociar
el formato de salida de forma distinta (algunos codificadores V4L2 M2M
entregan `avc` de forma nativa sin pasar por `h264parse` para construirlo,
otros no) -- al portar, repetir el Paso 4 de `guia-pruebas.md` completo en
la Pi antes de asumir que el mismo fix aplica sin cambios.

## 9. Escritura en microSD y por que hace falta `retention.py`

Una microSD tiene un numero de ciclos de escritura limitado y, a
diferencia de un disco de una laptop, no es facil de sustituir en campo.
La grabacion continua de este proyecto escribe evidencia sin parar, asi
que sin una politica de retencion la tarjeta se llena (o se desgasta)
inevitablemente. Por eso:

- La segmentacion (`splitmuxsink`, dentro del pipeline) es solo el
  mecanismo: produce archivos pequenos y con nombres predecibles en vez de
  un archivo unico gigante que un corte de energia podria corromper por
  completo.
- **La decision de que conservar y que borrar vive fuera del pipeline**,
  en `retention.py`, que corre en un hilo de la aplicacion Python, revisa
  el espacio libre cada 60 s y borra los segmentos mas antiguos (nunca los
  marcados como evento) cuando el espacio libre cae del 15 % hasta
  recuperar el 25 %.
- Esta separacion importa especialmente en la Pi: `splitmuxsink` con
  `max-files` habria sido un atajo tentador, pero es solo un primitivo de
  rotacion ciega (por cantidad de archivos, no por espacio real ni por
  eventos); no distingue un segmento con una identificacion positiva de
  uno rutinario. `retention.py` si hace esa distincion, y es ademas
  testeable sin arrancar GStreamer ni tocar hardware, lo cual es valioso
  en un dispositivo con almacenamiento limitado como la Pi.

## 10. Cambios respecto a la version anterior

- Seccion 1 (Regla de diseno): se quito `decodebin` y `videoscale` de la
  lista de elementos identicos entre host y Pi, y se agrego un parrafo
  explicando que la etapa de captura ahora fuerza YUY2 crudo directo
  sobre `v4l2src` con un `capsfilter`, sin decodificar ni escalar.
- Seccion 2 (tabla comparativa): la fila "Decodificacion de formato de
  camara" (que mencionaba `decodebin`) se reemplazo por "Formato crudo
  forzado en captura" (`capsfilter` con YUY2 640x480@30 directo), y la
  fila "Conversion / escalado" (`videoconvert ! videoscale`) se redujo a
  "Conversion" (`videoconvert` solo, sin escalado, porque ya no hace
  falta reducir resolucion).
- Sin cambios en el resto de las secciones (3 a 9): parametros de
  encoder, decoder, dependencias de Yocto, checklist de arranque en la
  Pi, licencias, el hallazgo de `h264parse` unico antes de `tee1`, y la
  politica de retencion en microSD.