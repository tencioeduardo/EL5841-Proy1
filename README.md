# Sistema operativo a la medida para un sistema de control de acceso con el marco de trabajo de Yocto Project y GStreamer


**Institución:** Instituto Tecnológico de Costa Rica
**Curso:** Taller de Sistemas Embebidos 
**Profesor**  Dr. Ing. Johan Carvajal Godínez  
**Integrantes:**  
* [Nombre del Estudiante 1]
* José Eduardo Tencio Solano

## 1. Visión General del Proyecto

Este proyecto consiste en un sistema de control de acceso embebido distribuido entre un Dispositivo de Borde (Raspberry Pi 4 corriendo una imagen de Linux a la medida sintetizada con Yocto Project) y un Puesto de Vigilancia (Host Linux x86_64).

El nodo de Borde captura video mediante una cámara USB/V4L2, realiza el análisis visual de códigos QR en tiempo real para conceder o denegar acceso (activando un actuador/LED), graba evidencia continua en segmentos MP4 con gestión automática de almacenamiento y transmite el flujo de video en vivo por red hacia el Puesto de Vigilancia.

## 2. Arquitectura de Red y Protocolos

La comunicación entre el Borde y el Puesto de Vigilancia opera mediante tres flujos independientes:

* **Streaming de Video UDP/RTP (Puerto 5000):** Transmisión unidireccional H.264 desde el Borde hacia el Puesto de Vigilancia.
* **Administración TCP (Puerto 6000):** Canal bidireccional cliente/servidor para la gestión remota de usuarios (`REG:`, `DEL:`, `LIST`, `PING`).
* **Notificación de Eventos UDP (Puerto 6001):** Emisión instantánea de eventos desde el Borde hacia el Host al detectar lecturas de códigos QR o cambios en la transmisión.

## 3. Requisitos del Sistema y Dependencias

### Requisitos en el Host (Desarrollo y Puesto de Vigilancia)
* Linux Ubuntu 22.04 LTS / 24.04 LTS / 26.04 LTS.
* Python 3.10+.
* Librerías de Python y Sistema:
  * `python3-gi` / `PyGObject`
  * `gstreamer-1.0` y plugins (`gst-plugins-base`, `gst-plugins-good`, `gst-plugins-bad`, `gst-plugins-ugly`, `gstreamer-libav`)
  * `python3-opencv` (`cv2`)

### Requisitos en el Dispositivo de Borde (Raspberry Pi 4)
* Imagen Linux producida con Yocto Project (ver Tutorial de Yocto).
* Soporte para codificación H.264 por hardware vía V4L2 (`v4l2h264enc`).
* Cámara compatible con V4L2 (`/dev/video0`).

## 4. Estructura de Módulos del Código Fuente

* `app_borde.py`: Aplicación principal del nodo Borde. Integra el pipeline de GStreamer, la detección de QR vía `appsink`, el control del actuador (Sysfs LED / GPIO), la persistencia JSON de usuarios y el servidor de administración TCP.
* `puesto_vigilancia.py`: Aplicación del Puesto de Vigilancia. Receptor de video RTP con conmutación dinámica a Standby ("SIN SEÑAL"), servidor de eventos UDP y consola interactiva de administración.
* `pipeline.py`: Encargado del armado modular de la tubería multimedia GStreamer mediante `Gst.ElementFactory`.
* `retention.py`: Módulo independiente que monitorea el espacio en disco y elimina segmentos de video viejos cuando el almacenamiento libre cae por debajo del 15%, protegiendo archivos en grabación activa y segmentos marcados con `.event`.

## 5. Guía de Ejecución y Uso

### A. Ejecución en Modo Loopback (Prueba Local en Host)
En una primera terminal, inicie el Dispositivo de Borde apuntando a `localhost`:
```bash
STREAM_HOST=127.0.0.1 PERFIL=host python3 app_borde.py
```
En una segunda terminal, inicie el Puesto de Vigilancia y seleccione la Opción 1 (Modo Loopback):

```Bash
python3 puesto_vigilancia.py
```
### B. Ejecución en Modo Red (Raspberry Pi + Host)
En la Raspberry Pi (Borde):

```Bash
STREAM_HOST= PERFIL=produccion python3 app_borde.py
```
En la computadora Host (Puesto de Vigilancia) y seleccione la Opcion 2 o 3:

```Bash
python3 puesto_vigilancia.py 
```
### C. Generación de Códigos QR para Pruebas
El sistema requiere códigos QR de Texto Plano Estático (evitar creadores de QR dinámicos con URLs). Puede generarlos en terminal mediante:

```Bash
# Instalar utilidad
sudo apt install qrencode


# Generar e imprimir QR en terminal
qrencode -t ansiutf8 "USER_001"
qrencode -t ansiutf8 "ADMIN_KEY"
```
### 6. Documentación del Proyecto
**Ingeniería de Requisitos:** Especificación formal de requisitos funcionales y no funcionales del sistema (docs/REQUISITOS.md).

**Tutorial de Yocto Project:** Guía paso a paso para la construcción e instalación de la imagen de Linux a la medida (docs/YOCTO_TUTORIAL.md).

**Bitácora de Trabajo:** Registro cronológico de actividades individuales desarrolladas (docs/BITACORA.md).

### 7. Declaración de Uso de Inteligencia Artificial
De acuerdo con la normativa del curso, se declara el uso de herramientas de Inteligencia Artificial Generativa durante el desarrollo del proyecto:

**Modelos Empleados:** Gemini (Google) / Claude (Anthropic).

#### Alcance de la Asistencia:

## 7. Declaración de Uso de Inteligencia Artificial

De acuerdo con la normativa del curso, se declara el uso de herramientas de Inteligencia Artificial Generativa durante el desarrollo del proyecto:

* **Modelos Empleados:** Gemini (Google) / Claude (Anthropic).
* **Alcance de la Asistencia:**
  * Apoyo en la arquitectura del grafo de GStreamer (`pipeline.py`) y manejo de callbacks en `appsink`.
  * Diseño del protocolo de comunicación socket TCP/UDP multihilo hilos-seguros.
  * Estructuración del módulo de retención de evidencia (`retention.py`).
  * Desarrollo del nodo de Borde (`app_borde.py`): Integración del reconocimiento de códigos QR en tiempo real con OpenCV, control de enfriamiento (*cooldown*) para lecturas repetidas, persistencia de usuarios en archivos JSON y abstracción del actuador de acceso.
  * Desarrollo del Puesto de Vigilancia (`puesto_vigilancia.py`): Lógica de conmutación automática de video (RTP / Standby "SIN SEÑAL"), interfaz de consola dinámica con auto-refresco ante eventos en tiempo real y menú inicial interactivo para selección de entorno de red (Loopback vs. Raspberry Pi).
