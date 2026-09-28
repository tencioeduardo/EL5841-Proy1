#!/usr/bin/env python3
"""
retention.py -- Politica de retencion de evidencia, independiente de
GStreamer. Este modulo NO importa Gst ni nada relacionado; se puede
importar y probar por separado (ver el bloque de auto-prueba al final).

Regla de negocio (fuera del pipeline, por diseno: el pipeline solo escribe
segmentos con nombres y timestamps predecibles; este modulo decide que se
conserva):

  - Se retienen todos los segmentos por defecto.
  - Un hilo revisa cada 60 s (configurable) el espacio libre de la
    particion de evidencia.
  - Si el espacio libre baja del 15 % (configurable), se borran los
    segmentos MAS ANTIGUOS hasta recuperar el 25 % de espacio libre
    (configurable).
  - Cada borrado queda en bitacora (archivo de texto plano + stdout) con
    timestamp, nombre de archivo y motivo.
  - Los segmentos marcados como "evento" NUNCA se borran automaticamente.

Convencion de "segmento evento": junto a cada segmento de video
"<nombre>.mp4" puede existir un archivo lateral "<nombre>.mp4.event"
(vacio, solo su existencia importa). Se elige un archivo lateral -- en vez
de renombrar o mover el .mp4 -- porque el segmento "vigente" puede seguir
abierto por splitmuxsink en el momento en que la aplicacion detecta el
evento; tocar un archivo lateral es seguro incluso mientras el .mp4 sigue
escribiendose. pipeline.py expone `current_segment_path` con la ruta que
la aplicacion debe pasar a mark_as_event() cuando el reconocimiento facial
da positivo.
"""

import glob
import os
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

EVENT_MARKER_SUFFIX = ".event"
DEFAULT_SEGMENT_PATTERN = "*.mp4"
DEFAULT_CHECK_INTERVAL_SECONDS = 60
DEFAULT_LOW_FREE_PERCENT = 15.0
DEFAULT_TARGET_FREE_PERCENT = 25.0


# ---------------------------------------------------------------------------
# Funciones de espacio en disco
# ---------------------------------------------------------------------------

def get_free_space_bytes(directory: str) -> int:
    """Bytes libres en la particion que contiene `directory`."""
    return shutil.disk_usage(directory).free


def get_free_space_percent(directory: str) -> float:
    """Porcentaje de espacio libre (0-100) en la particion que contiene
    `directory`, respecto del tamano TOTAL de la particion (no solo de lo
    usado por evidencia), que es lo que realmente puede tirar el sistema
    abajo si llega a 0."""
    usage = shutil.disk_usage(directory)
    if usage.total == 0:
        return 0.0
    return (usage.free / usage.total) * 100.0


# ---------------------------------------------------------------------------
# Segmentos: listado, antiguedad, marca de evento
# ---------------------------------------------------------------------------

def is_event_segment(segment_path: str) -> bool:
    """True si el segmento tiene su marcador lateral '<segmento>.event'."""
    return os.path.exists(segment_path + EVENT_MARKER_SUFFIX)


def mark_as_event(segment_path: str) -> str:
    """Marca un segmento como evento (creando el archivo lateral). A partir
    de este momento, check_once() nunca lo eliminara automaticamente; solo
    se libera por rotacion manual (borrar el .mp4 y su .event a mano, o con
    una herramienta de mantenimiento aparte)."""
    marker_path = segment_path + EVENT_MARKER_SUFFIX
    Path(marker_path).touch()
    return marker_path


def unmark_event(segment_path: str) -> bool:
    """Quita la marca de evento (rotacion manual). Devuelve True si habia
    marca y se quito."""
    marker_path = segment_path + EVENT_MARKER_SUFFIX
    if os.path.exists(marker_path):
        os.remove(marker_path)
        return True
    return False


def list_segments_by_age(
    directory: str, pattern: str = DEFAULT_SEGMENT_PATTERN
) -> List[str]:
    """Lista TODOS los segmentos que hacen match con `pattern` en
    `directory`, ordenados del mas antiguo al mas reciente segun mtime.
    Incluye tanto los marcados como evento como los que no."""
    paths = [
        p for p in glob.glob(os.path.join(directory, pattern))
        if os.path.isfile(p)
    ]
    paths.sort(key=lambda p: os.path.getmtime(p))
    return paths


def list_deletable_segments(
    directory: str, pattern: str = DEFAULT_SEGMENT_PATTERN
) -> List[str]:
    """Igual que list_segments_by_age, pero excluye los marcados como
    evento: son los unicos candidatos legitimos a borrado automatico."""
    return [
        p for p in list_segments_by_age(directory, pattern)
        if not is_event_segment(p)
    ]


# ---------------------------------------------------------------------------
# Bitacora
# ---------------------------------------------------------------------------

class RetentionLog:
    """Bitacora en texto plano + stdout. Deliberadamente simple (sin el
    modulo logging de la biblioteca estandar) para que sea trivial de leer
    a mano en campo y de probar en el auto-test."""

    def __init__(self, log_path: Optional[str] = None):
        self.log_path = log_path
        if self.log_path:
            os.makedirs(os.path.dirname(self.log_path) or ".", exist_ok=True)

    def _write(self, line: str) -> None:
        print(line)
        if self.log_path:
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def log_deletion(self, filename: str, reason: str, freed_bytes: int = 0) -> None:
        stamp = datetime.now().isoformat(timespec="seconds")
        self._write(
            f"{stamp}\tBORRADO\t{filename}\tmotivo={reason}\t"
            f"liberado_bytes={freed_bytes}"
        )

    def log_info(self, message: str) -> None:
        stamp = datetime.now().isoformat(timespec="seconds")
        self._write(f"{stamp}\tINFO\t{message}")

    def log_error(self, message: str) -> None:
        stamp = datetime.now().isoformat(timespec="seconds")
        self._write(f"{stamp}\tERROR\t{message}")


# ---------------------------------------------------------------------------
# Politica de retencion
# ---------------------------------------------------------------------------

class RetentionPolicy:
    """Encapsula la politica de retencion. `check_once()` es la unidad
    testeable sin hilos ni GStreamer; `start()`/`stop()` la corren en un
    hilo de fondo con el intervalo configurado."""

    def __init__(
        self,
        evidence_dir: str,
        log_path: Optional[str] = None,
        pattern: str = DEFAULT_SEGMENT_PATTERN,
        low_free_percent: float = DEFAULT_LOW_FREE_PERCENT,
        target_free_percent: float = DEFAULT_TARGET_FREE_PERCENT,
        check_interval_seconds: float = DEFAULT_CHECK_INTERVAL_SECONDS,
    ):
        if target_free_percent <= low_free_percent:
            raise ValueError(
                "target_free_percent debe ser mayor que low_free_percent"
            )
        self.evidence_dir = evidence_dir
        self.pattern = pattern
        self.low_free_percent = low_free_percent
        self.target_free_percent = target_free_percent
        self.check_interval_seconds = check_interval_seconds
        self.log = RetentionLog(log_path)

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def check_once(self) -> List[str]:
        """Ejecuta UNA pasada: si el espacio libre esta bajo el umbral,
        borra segmentos no marcados como evento (del mas antiguo al mas
        reciente) hasta recuperar el porcentaje objetivo. Devuelve la
        lista de rutas borradas. No requiere hilos: es la funcion a probar
        directamente en tests unitarios."""
        deleted: List[str] = []
        free_pct = get_free_space_percent(self.evidence_dir)

        if free_pct >= self.low_free_percent:
            return deleted

        self.log.log_info(
            f"espacio libre {free_pct:.1f}% por debajo del umbral "
            f"{self.low_free_percent:.1f}% en {self.evidence_dir}; "
            f"iniciando limpieza hasta alcanzar {self.target_free_percent:.1f}%"
        )

        for path in list_deletable_segments(self.evidence_dir, self.pattern):
            free_pct = get_free_space_percent(self.evidence_dir)
            if free_pct >= self.target_free_percent:
                break
            try:
                size = os.path.getsize(path)
                os.remove(path)
            except OSError as exc:
                self.log.log_error(f"no se pudo borrar {path}: {exc}")
                continue
            deleted.append(path)
            self.log.log_deletion(
                path, reason="espacio_libre_bajo_umbral", freed_bytes=size
            )

        free_pct = get_free_space_percent(self.evidence_dir)
        if free_pct < self.target_free_percent and not deleted:
            self.log.log_error(
                "no quedan segmentos elegibles para borrar (todos marcados "
                "como evento) y el espacio libre sigue bajo el objetivo"
            )

        return deleted

    def _run_loop(self) -> None:
        self.log.log_info(
            f"hilo de retencion iniciado: intervalo={self.check_interval_seconds}s "
            f"umbral_bajo={self.low_free_percent}% objetivo={self.target_free_percent}%"
        )
        while not self._stop_event.is_set():
            try:
                self.check_once()
            except Exception as exc:  # el hilo de fondo nunca debe morir
                self.log.log_error(f"fallo inesperado en check_once: {exc}")
            self._stop_event.wait(self.check_interval_seconds)

    def start(self) -> None:
        """Arranca el bucle de verificacion en un hilo daemon."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop, name="retention-thread", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: Optional[float] = 5.0) -> None:
        """Detiene el hilo de forma ordenada (usado por tests y por el
        apagado limpio de la aplicacion)."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout)


# ---------------------------------------------------------------------------
# Auto-prueba: crea un directorio temporal con segmentos falsos de distinto
# tamano y antiguedad, uno marcado como evento, y corre check_once() con
# umbrales forzados para demostrar que borra lo esperado y respeta el
# segmento evento. No arranca GStreamer en ningun momento.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory(prefix="retention_test_") as tmp:
        print(f"[auto-test] directorio temporal: {tmp}")

        # Crea 4 segmentos de 1 MiB cada uno, con mtimes escalonados para
        # que el orden de antiguedad sea predecible.
        segment_paths = []
        for i in range(4):
            path = os.path.join(tmp, f"acceso_{i:02d}.mp4")
            with open(path, "wb") as fh:
                fh.write(b"\0" * (1024 * 1024))
            # Escalona el mtime: el segmento 0 es el mas antiguo.
            past = time.time() - (4 - i) * 3600
            os.utime(path, (past, past))
            segment_paths.append(path)

        # Marca el segmento mas antiguo (el primer candidato a borrarse)
        # como evento, para demostrar que la politica lo respeta.
        mark_as_event(segment_paths[0])
        print(f"[auto-test] marcado como evento: {segment_paths[0]}")

        print(
            "[auto-test] segmentos por antiguedad:",
            list_segments_by_age(tmp),
        )
        print(
            "[auto-test] segmentos borrables (sin el evento):",
            list_deletable_segments(tmp),
        )

        log_path = os.path.join(tmp, "bitacora_retencion.log")
        # Umbrales forzados a 100 %/100 % para que check_once() SIEMPRE
        # dispare el borrado, sin depender del espacio real del disco del
        # entorno donde se corra esta prueba.
        policy = RetentionPolicy(
            evidence_dir=tmp,
            log_path=log_path,
            low_free_percent=100.0,
            target_free_percent=100.1,
        )
        deleted = policy.check_once()

        print(f"[auto-test] segmentos borrados: {deleted}")
        assert segment_paths[0] not in deleted, (
            "el segmento marcado como evento NUNCA debe borrarse"
        )
        assert os.path.exists(segment_paths[0]), (
            "el segmento evento debe seguir en disco"
        )
        for p in deleted:
            assert not os.path.exists(p), f"{p} deberia haberse borrado"

        print("[auto-test] bitacora escrita en:", log_path)
        with open(log_path, encoding="utf-8") as fh:
            print(fh.read())

        print("[auto-test] OK: politica de retencion se comporto como se esperaba")
