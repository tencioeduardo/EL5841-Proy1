# Imagen Yocto — Sistema de control de acceso con video

Imagen Linux embebida para Raspberry Pi 4, construida con Yocto Project, que ejecuta el sistema de control de acceso con captura y transmisión de video mediante GStreamer.

| | |
|---|---|
| Distribución | Poky, Yocto Project **Scarthgap 5.0.20 (LTS)** |
| Máquina | `raspberrypi4-64` (capa `meta-raspberrypi`) |
| Arquitectura | `aarch64`, optimizada para Cortex-A72 |
| Kernel | 6.6.63 |
| Sistema de inicio | systemd |
| GStreamer | 1.22.12 |
| Entorno de compilación | Contenedor Docker `crops/poky:ubuntu-22.04` |

---

## 1. Estructura

```
yocto/
├── meta-control-acceso/        Capa propia del proyecto
│   ├── conf/layer.conf
│   ├── recipes-core/images/            Receta de la imagen
│   ├── recipes-core/packagegroups/     Paquetes agrupados por área
│   ├── recipes-app/control-acceso/     Aplicación y servicio systemd
│   └── recipes-kernel/linux/           Ajustes a la configuración del kernel
├── conf/
│   ├── local.conf.append       Configuración del build agregada a local.conf
│   ├── qemu.conf.append        Configuración alternativa para QEMU
│   └── usado/                  Copia de local.conf y bblayers.conf usados en el build validado
├── scripts/                    Automatización del flujo de construcción
├── pi/                         Scripts de prueba para ejecutar en la Raspberry Pi
├── evidencia/                  Registros y capturas de las pruebas en hardware
└── versiones_capas.txt         Commits exactos de las capas externas
```

Las capas externas (`poky`, `meta-raspberrypi`, `meta-openembedded`), el directorio de build y las cachés **no se versionan**. Se regeneran con los scripts; el archivo `.gitignore` de esta carpeta las excluye.

---

## 2. Capa `meta-control-acceso`

Todas las modificaciones del proyecto se concentran en esta capa. Las capas de terceros no se editan; cuando es necesario modificar una receta ajena, se usa un archivo `.bbappend`.

| Receta | Función |
|---|---|
| `control-acceso-image.bb` | Define la imagen: arranque base, servidor SSH, grupos de paquetes y la aplicación. |
| `packagegroup-control-acceso.bb` | Agrupa las dependencias por área: `base` (Python), `video` (GStreamer, V4L2, drivers de cámara y codec), `csi` (libcamera, solo para cámara CSI), `gpio` (libgpiod) y `debug` (herramientas de diagnóstico). |
| `control-acceso_0.1.bb` | Instala la aplicación en `/usr/bin/control-acceso` y su servicio systemd, habilitado al arranque con `Restart=on-failure`. |
| `linux-raspberrypi_%.bbappend` | Agrega el fragmento `codec.cfg` a la configuración del kernel para compilar el driver del codec de video por hardware (`bcm2835-codec`). |

Variable de configuración propia, en `local.conf`:

| Variable | Valores | Efecto |
|---|---|---|
| `CAMARA` | `usb` (por defecto), `csi` | Con `csi` se incluye libcamera y su plugin de GStreamer. El proyecto usa cámara USB. |

---

## 3. Requisitos del equipo de compilación

| | Mínimo | Recomendado |
|---|---|---|
| Sistema operativo | Linux x86_64 | Ubuntu 22.04 o 24.04 |
| Disco libre | 60 GB | 100 GB |
| Memoria | 8 GB | 16 GB |
| Procesador | 4 hilos | 8 hilos o más |
| Software | Docker, git | — |

La compilación se realiza dentro de un contenedor para garantizar un entorno soportado por Scarthgap y reproducible entre equipos. El contenedor solo provee el entorno: la compilación hacia ARM la resuelve el toolchain cruzado que genera Yocto. El detalle está en `GUIA-DOCKER.md`.

No es compatible con macOS (sistema de archivos sin distinción de mayúsculas). En Windows debe usarse WSL2, trabajando dentro del sistema de archivos de Linux.

---

## 4. Construcción

### 4.1 Con scripts

Desde la carpeta `yocto/`:

| Paso | Dónde | Comando | Función |
|---|---|---|---|
| 0 | Anfitrión | `./scripts/00_verificar_host.sh` | Verifica disco, memoria y Docker |
| 1 | Anfitrión | `./scripts/01_clonar.sh` | Clona las capas externas (rama `scarthgap`) y registra sus commits |
| 2 | Anfitrión | `./scripts/02_contenedor.sh` | Abre el contenedor; la carpeta se monta en `/workdir` |
| 3 | Contenedor | `./scripts/03_configurar.sh` | Crea `build/`, registra las capas y aplica la configuración |
| 4 | Contenedor | `./scripts/04_build.sh verificar control-acceso-image` | Prueba en seco de dependencias |
| 5 | Contenedor | `./scripts/04_build.sh build control-acceso-image` | Compilación |
| 6 | Anfitrión | `./scripts/05_flashear.sh /dev/DISPOSITIVO control-acceso-image` | Graba la imagen en la microSD |

El paralelismo (`BB_NUMBER_THREADS`, `PARALLEL_MAKE`) se calcula automáticamente según los núcleos y la memoria del equipo (`scripts/hilos.sh`).

### 4.2 Comandos equivalentes

```bash
# Anfitrión
git clone -b scarthgap https://git.yoctoproject.org/poky
git clone -b scarthgap https://github.com/agherzan/meta-raspberrypi
git clone -b scarthgap https://git.openembedded.org/meta-openembedded
docker run --rm -it -v "$PWD":/workdir crops/poky:ubuntu-22.04 --workdir=/workdir

# Contenedor
source poky/oe-init-build-env build
bitbake-layers add-layer ../meta-openembedded/meta-oe ../meta-openembedded/meta-python \
  ../meta-openembedded/meta-multimedia ../meta-raspberrypi ../meta-control-acceso
cat ../conf/local.conf.append >> conf/local.conf   # reemplazar @BB@ y @PM@ por el paralelismo
bitbake control-acceso-image
```

### 4.3 Resultado

```
build/tmp/deploy/images/raspberrypi4-64/control-acceso-image-raspberrypi4-64.rootfs.wic.bz2
```

Imagen de disco con dos particiones: arranque (FAT32, 130 MB) y sistema de archivos raíz (ext4, 1,1 GB).

### 4.4 Tiempos de referencia

Medidos en un equipo de 12 hilos, 15 GB de RAM y almacenamiento NVMe:

| Operación | Tiempo |
|---|---|
| Primera compilación completa (6823 tareas) | ~44 min, más el tiempo de descarga |
| Recompilación tras modificar un grupo de paquetes | segundos |
| Recompilación tras modificar la configuración del kernel | ~20–30 min |

Las recompilaciones reutilizan la caché de estado compartido (`sstate-cache/`): cada tarea se identifica por un hash de su receta, fuentes y dependencias, y solo se ejecutan las tareas cuyo hash cambió.

### 4.5 Reproducibilidad

`versiones_capas.txt` registra el commit de cada capa externa usado en el build validado. Para reproducir exactamente ese build, cada capa debe posicionarse en su commit (`git -C <capa> checkout <commit>`).

---

## 5. Uso en la Raspberry Pi 4

1. Grabar la imagen en la microSD (paso 6).
2. Conectar el monitor al puerto **HDMI0** (el más cercano a la alimentación). Es el único con salida durante el arranque.
3. Encender. El acceso es con el usuario `root`, por consola o por SSH (dropbear).
4. La aplicación arranca automáticamente como servicio:

```bash
systemctl status control-acceso
journalctl -u control-acceso -b
```

La imagen actual incluye `debug-tweaks`: `root` no tiene contraseña. Esta configuración es exclusiva de desarrollo.

---

## 6. Estado de validación

### 6.1 Verificado en hardware (17 de setiembre de 2026)

Hardware: Raspberry Pi 4 Model B Rev 1.5, 8 GB RAM, microSD de 32 GB, cámara USB (UVC).

| Prueba | Resultado |
|---|---|
| Arranque y acceso por consola | Correcto |
| Servicio `control-acceso` | `active (running)`, habilitado al arranque |
| Detección de GPIO (libgpiod 2.1.3) | `gpiochip0`, `gpiochip1` |
| Cámara USB | Detectada en caliente como `/dev/video0` (captura) y `/dev/video1` (metadatos) |
| Alimentación y temperatura | `vcgencmd get_throttled` = `0x0` |
| Captura con GStreamer | Correcta |

Modos de la cámara:

| Formato | Resoluciones y tasas |
|---|---|
| MJPG | 1280x720 @ 30/25/20/15/10/5 · 800x600 @ 30 · 640x480 @ 30 · 320x240 @ 30 |
| YUYV | 1280x720 @ 10 · 800x600 @ 15 · 640x480 @ 30 · 320x240 @ 30 |

Rendimiento medido (150 cuadros, MJPG 1280x720 solicitando 30 fps):

| Pipeline | Tiempo | fps efectivos | CPU (user) |
|---|---|---|---|
| `v4l2src ! image/jpeg ! fakesink` | 5,37 s | ~28 | 0,09 s |
| `v4l2src ! image/jpeg ! jpegdec ! videoconvert ! fakesink` | 7,83 s | ~19 | 2,11 s |

La decodificación JPEG por software reduce la tasa en ~9 fps.

### 6.2 Problema identificado: codec por hardware ausente

El elemento `v4l2h264enc` no estaba disponible en la primera imagen. Diagnóstico en la Pi:

1. El plugin `libgstvideo4linux2.so` está instalado y registra `v4l2src`, pero ningún elemento de codificación ni decodificación.
2. No existen los nodos `/dev/video10-12` del codec.
3. `modprobe bcm2835-codec` falla: el módulo no está instalado en la imagen.

**Causa:** el plugin V4L2 de GStreamer registra los elementos de codec solo si encuentra un dispositivo *memory-to-memory* al iniciar. Sin el módulo `bcm2835-codec`, ese dispositivo no existe.

**Corrección:** el grupo de video incluye ahora `kernel-module-bcm2835-codec`. Se está verificando si el módulo ya se compilaba y solo faltaba instalarlo, o si además hay que activarlo en la configuración del kernel mediante el `.bbappend` de `recipes-kernel/`. **Pendiente de validación en hardware.**

---

## 7. Pendientes

| Tarea | Motivo |
|---|---|
| Validar el codec por hardware | Requisito para codificar H.264 en la Pi |
| Partición de datos para evidencia | El sistema de archivos raíz deja ~500 MB libres; a la tasa de grabación medida (0,59 GB/h) equivale a menos de una hora |
| Sincronización horaria (NTP) | La Pi no tiene reloj de tiempo real y arranca con una fecha incorrecta |
| Integrar la aplicación real | La receta instala actualmente una aplicación de prueba que reporta los dispositivos detectados |
| Endurecer la imagen | Retirar `debug-tweaks` y el grupo `debug`, y reducir los plugins de GStreamer a los utilizados |

---

## 8. Resolución de problemas

| Síntoma | Causa | Solución |
|---|---|---|
| `Nothing RPROVIDES 'xyz'` | Nombre de paquete incorrecto o capa faltante | Verificar el nombre con `oe-pkgdata-util list-pkgs` o en layers.openembedded.org |
| `bitbake: command not found` | Entorno no cargado | `source poky/oe-init-build-env build` |
| `cd: /workdir: No existe` | Script del contenedor ejecutado en el anfitrión | Abrir antes el contenedor con `02_contenedor.sh` |
| El build termina con `Killed` | Memoria insuficiente | Reducir `BB_NUMBER_THREADS` en `build/conf/local.conf` |
| `User namespaces are not usable by BitBake` | Restricción de AppArmor en Ubuntu 24.04 | Agregar `--security-opt apparmor=unconfined` en `02_contenedor.sh` |
| `permission denied` al usar Docker | Usuario fuera del grupo `docker` | `sudo usermod -aG docker $USER` y reiniciar la sesión |
| `Failed to fetch URL ... attempting MIRRORS` | Servidor de origen no disponible | Advertencia; BitBake usa un espejo |
| Sin video en el monitor | Puerto HDMI incorrecto | Usar HDMI0 |

---

## 9. Documentación relacionada

| Documento | Contenido |
|---|---|
| `BITACORA.md` | Registro de la sesión de pruebas en hardware y el diagnóstico del codec |
