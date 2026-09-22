# Bitácora — Imagen Yocto

Sistema de control de acceso con video · Raspberry Pi 4 · EL5841 Taller de Sistemas Embebidos

---

## Resumen del estado

| Aspecto | Estado |
|---|---|
| Imagen `control-acceso-image` construida | Completo |
| Arranque y servicio automático en la Pi 4 | Validado en hardware |
| Cámara USB, GPIO y alimentación | Validado en hardware |
| Captura con GStreamer | Validado, con mediciones de fps |
| Codec H.264 por hardware (`v4l2h264enc`) | Corrección aplicada, pendiente de validar |
| `python3-numpy` para el pipeline | Agregado, pendiente de validar |
| Versionado en git (rama `yocto/imagen-base`) | Completo |
| Partición de datos para evidencia | Preparada, pendiente de aplicar |
| Sincronización horaria (NTP) | Preparada, pendiente de aplicar |
| Integración de la aplicación real | Pendiente |
| Endurecimiento de la imagen | Pendiente |

---

## Configuración

| | |
|---|---|
| Distribución | Poky, Yocto Project **Scarthgap 5.0.20** |
| Máquina | `raspberrypi4-64` |
| Kernel | 6.6.63-v8 |
| Sistema de inicio | systemd |
| GStreamer | 1.22.12 |
| libgpiod | 2.1.3 |
| Imagen | 95 MB comprimida, 1,3 GB escrita (arranque 130 MB + raíz 1,1 GB) |
| Entorno de compilación | Docker `crops/poky:ubuntu-22.04` sobre Ubuntu 24.04 (12 hilos, 15 GB RAM, NVMe) |
| Hardware de prueba | CanaKit, Raspberry Pi 4 Model B Rev 1.5, 8 GB RAM, microSD Samsung EVO+ de 32 GB |

---

## Sesión 1 — Preparación del entorno

- Selección de Yocto Scarthgap 5.0 LTS y de la máquina `raspberrypi4-64`, con build dentro de Docker para tener un entorno soportado y reproducible.
- Creación de la capa propia `meta-control-acceso`:
  - receta de imagen `control-acceso-image`;
  - grupo de paquetes separado por área (base, video, csi, gpio, debug);
  - aplicación de prueba con servicio systemd (`Restart=on-failure`).
- Scripts numerados para verificar el equipo, clonar capas, abrir el contenedor, configurar, compilar y grabar la microSD.
- Revisión del diseño inicial y correcciones:
  - se retiró `VIDEO_CAMERA`, que activa el stack de cámara *legacy*, incompatible con libcamera;
  - se ajustó el paralelismo al equipo;
  - se corrigió el script de grabación para aceptar lectores `mmcblk` y proteger el disco del sistema.
- Definición de la cámara como USB, lo que evita incluir libcamera.

---

## Sesión 2 — Primer build y prueba en hardware (17 de setiembre)

### Construcción

- Descarga completa de fuentes con `bitbake --runall=fetch`, para poder compilar sin red.
- Primer build de `control-acceso-image`: **6823 tareas en ~44 minutos**.
- Revisión del manifiesto: faltaba el driver `uvcvideo` de la cámara USB. Se agregó `kernel-module-uvcvideo` y la recompilación tomó **segundos**: 6807 de 6823 tareas se reutilizaron desde la caché sstate.
- Imagen grabada en la microSD con `dd`; resultado con dos particiones (FAT32 de 130 MB y ext4 de 1,1 GB).

### Pruebas en la Pi

| Prueba | Resultado |
|---|---|
| Arranque (salida por HDMI0) | Correcto; login como `root` |
| Servicio `control-acceso` | `active (running)`, `enabled` |
| Modelo detectado por la app | Raspberry Pi 4 Model B Rev 1.5 |
| GPIO | `gpiochip0`, `gpiochip1` |
| Cámara USB | Detectada en caliente: `/dev/video0` (captura) y `/dev/video1` (metadatos) |
| Alimentación y temperatura | `vcgencmd get_throttled` = `0x0` |
| `jpegdec` | Disponible |
| `v4l2h264enc` | **No disponible** |

Observación: la primera vez no hubo video porque el monitor estaba en HDMI1. La Pi 4 solo da salida por HDMI0 durante el arranque.

### Modos de la cámara

| Formato | Resoluciones y tasas |
|---|---|
| MJPG | 1280x720 @ 30/25/20/15/10/5 · 800x600 @ 30 · 640x480 @ 30 · 320x240 @ 30 |
| YUYV | 1280x720 @ 10 · 800x600 @ 15 · 640x480 @ 30 · 320x240 @ 30 |

### Mediciones de rendimiento

150 cuadros, MJPG 1280x720 solicitando 30 fps:

| Pipeline | Tiempo real | fps efectivos | CPU (user) |
|---|---|---|---|
| `v4l2src ! image/jpeg ! fakesink` | 5,37 s | **~28** | 0,09 s |
| `v4l2src ! image/jpeg ! jpegdec ! videoconvert ! fakesink` | 7,83 s | **~19** | 2,11 s |

**Conclusión:** la cámara entrega casi 30 fps; la decodificación JPEG por software reduce la tasa en ~9 fps. Este dato respalda la decisión del pipeline de capturar en YUY2 640x480 y evitar `jpegdec`.

### Diagnóstico de `v4l2h264enc`

1. `libgstvideo4linux2.so` está instalado.
2. `gst-inspect-1.0 video4linux2` lista `v4l2src`, `v4l2sink`, `v4l2radio` y el *device provider*, pero **ningún** elemento de codificación ni decodificación.
3. `/dev/` solo contiene `video0` y `video1`; faltan `video10-12`, los nodos del codec.
4. `modprobe bcm2835-codec` responde `Module not found`: el módulo no está instalado en la imagen.

**Causa:** el plugin V4L2 registra los elementos de codec solo si encuentra un dispositivo *memory-to-memory* al iniciar. Sin el módulo `bcm2835-codec`, ese dispositivo no existe.

**Pendiente de aclarar:** la búsqueda del módulo se hizo en `drivers/media/`, pero en el kernel de la Raspberry Pi este driver vive en `drivers/staging/vc04_services/`. Es posible que el módulo se compilara y solo faltara instalarlo.

### Otras observaciones

- **BusyBox:** los comandos básicos vienen de BusyBox, no de GNU coreutils; algunas opciones no existen (`head -30` y `head -n 30` fallaron).
- **Hora:** la Pi arrancó con fecha de junio de 2025, porque no tiene reloj de tiempo real ni sincronización configurada.
- **Desconexión de la cámara:** con la cámara desconectada, GStreamer falla en ~0,1 s con `Cannot identify device '/dev/video0'`, de forma limpia. La aplicación debe manejar este caso; el servicio ya reinicia el proceso si muere.

---

## Sesión 3 — Correcciones y versionado (21–22 de setiembre)

### Cambios en la capa

| Cambio | Motivo |
|---|---|
| `kernel-module-bcm2835-codec` en el grupo de video | Instalar el driver del codec por hardware |
| `recipes-kernel/linux/linux-raspberrypi_%.bbappend` + `codec.cfg` | Activar el driver en la configuración del kernel, si no se compilaba |
| `python3-numpy` en el grupo base | El `appsink` del pipeline entrega cuadros como arrays de NumPy |

Verificación en seco (`bitbake -n`): dependencias resueltas; las tareas pasaron de 6823 a 7161 por `numpy` y sus dependencias.

**Limitación de la verificación:** los paquetes `kernel-module-*` se generan de forma dinámica al empaquetar el kernel, por lo que la prueba en seco los acepta aunque todavía no existan. La confirmación real es el manifiesto tras el build.

### Versionado

- Repositorio del proyecto: `EL5841-Proy1`, carpeta `yocto/`, rama `yocto/imagen-base`.
- Se versionan la capa, los scripts, la configuración, la documentación y `versiones_capas.txt` (commits exactos de las capas externas).
- **No** se versionan las capas externas, `build/`, `downloads/` ni `sstate-cache/` (decenas de GB); se regeneran con los scripts.
- El directorio de trabajo se unificó dentro del repositorio: las capas externas, el build y las cachés se movieron a `yocto/`, excluidos por `.gitignore`. Dentro del contenedor la ruta sigue siendo `/workdir`, por lo que el build no se ve afectado.

### Preparado, pendiente de aplicar

- **Partición de datos `/data`:** tabla de particiones propia (`control-acceso.wks`), copia de la de meta-raspberrypi más una tercera partición ext4 de 4 GB. Opcionalmente, la receta `expandir-datos` la expande hasta el final de la microSD en el primer arranque (~27 GB).
- **Directorio de evidencia:** `/data/evidencia`, creado al arrancar mediante `tmpfiles.d`; el servicio espera a que `/data` esté montada (`RequiresMountsFor`).
- **NTP:** configuración complementaria de `systemd-timesyncd` (`sistema-config`).

**Motivo de la partición:** la raíz deja ~500 MB libres. A la tasa de grabación medida en el pipeline (0,59 GB/h), eso equivale a menos de una hora de evidencia. Con toda la microSD de 32 GB serían unas 45 horas.

---

## Pendientes

1. Confirmar si el módulo del codec ya se compilaba; compilar y validar `v4l2h264enc` en la Pi.
2. Medir el pipeline completo con H.264 por hardware (YUY2 640x480 → NV12 → `v4l2h264enc`).
3. Aplicar y validar la partición de datos y NTP.
4. Probar SSH por Ethernet.
5. Integrar la aplicación real en la receta (`SRC_URI` al código del pipeline, `DEVICE=/dev/video0`).
6. Endurecer la imagen para la entrega: retirar `debug-tweaks` y el grupo `debug`, y reducir los plugins de GStreamer a los utilizados.

---

## Aprendizajes

- **Caché sstate:** cada tarea se identifica por un hash de su receta, fuentes y dependencias. Si no cambia, el resultado se reutiliza. Por eso el primer build tomó 44 minutos y una modificación del grupo de paquetes, segundos. Cambiar variables globales como `MACHINE` invalida casi todo.
- **`.bbappend`:** permite modificar recetas de otras capas sin editarlas. Así se ajusta el kernel de meta-raspberrypi desde la capa propia.
- **Verificar lo que se afirma:** el manifiesto de la imagen, `gst-inspect-1.0` en la Pi y las mediciones con `time` son la evidencia; una prueba en seco no garantiza que un paquete generado dinámicamente exista.
- **Diagnóstico por capas:** ante un elemento de GStreamer ausente se revisa en orden: el plugin, los elementos que registra, los nodos en `/dev` y el módulo del kernel.
