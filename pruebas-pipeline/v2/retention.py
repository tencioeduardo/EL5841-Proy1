#!/usr/bin/env python3
"""
retention.py -- Politica de retencion de evidencia, independiente de
GStreamer. Este modulo NO importa Gst ni nada relacionado; se puede
importar y probar por separado (ver el bloque de auto-prueba al final).
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


def get_free_space_bytes(directory: str) -> int:
    return shutil.disk_usage(directory).free

def get_free_space_percent(directory: str) -> float:
    usage = shutil.disk_usage(directory)
    if usage.total == 0:
        return 0.0
    return (usage.free / usage.total) * 100.0

def is_event_segment(segment_path: str) -> bool:
    return os.path.exists(segment_path + EVENT_MARKER_SUFFIX)

def mark_as_event(segment_path: str) -> str:
    marker_path = segment_path + EVENT_MARKER_SUFFIX
    Path(marker_path).touch()
    return marker_path

def unmark_event(segment_path: str) -> bool:
    marker_path = segment_path + EVENT_MARKER_SUFFIX
    if os.path.exists(marker_path):
        os.remove(marker_path)
        return True
    return False

def list_segments_by_age(
    directory: str, pattern: str = DEFAULT_SEGMENT_PATTERN
) -> List[str]:
    paths = [
        p for p in glob.glob(os.path.join(directory, pattern))
        if os.path.isfile(p)
    ]
    paths.sort(key=lambda p: os.path.getmtime(p))
    return paths

def list_deletable_segments(
    directory: str, pattern: str = DEFAULT_SEGMENT_PATTERN, exclude_paths: Optional[List[str]] = None
) -> List[str]:
    exclude = set(exclude_paths) if exclude_paths else set()
    return [
        p for p in list_segments_by_age(directory, pattern)
        if not is_event_segment(p) and p not in exclude
    ]


class RetentionLog:
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


class RetentionPolicy:
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

    def check_once(self, exclude_paths: Optional[List[str]] = None) -> List[str]:
        """Ejecuta UNA pasada, protegiendo explícitamente los segmentos
        incluidos en `exclude_paths` (p. ej., el archivo actualmente
        en escritura)."""
        deleted: List[str] = []
        free_pct = get_free_space_percent(self.evidence_dir)

        if free_pct >= self.low_free_percent:
            return deleted

        self.log.log_info(
            f"espacio libre {free_pct:.1f}% por debajo del umbral "
            f"{self.low_free_percent:.1f}% en {self.evidence_dir}; "
            f"iniciando limpieza hasta alcanzar {self.target_free_percent:.1f}%"
        )

        for path in list_deletable_segments(self.evidence_dir, self.pattern, exclude_paths):
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
                "no quedan segmentos elegibles para borrar (todos protegidos o "
                "marcados como evento) y el espacio libre sigue bajo el objetivo"
            )

        return deleted

    def _run_loop(self, get_current_segment_cb=None) -> None:
        self.log.log_info(
            f"hilo de retencion iniciado: intervalo={self.check_interval_seconds}s "
            f"umbral_bajo={self.low_free_percent}% objetivo={self.target_free_percent}%"
        )
        while not self._stop_event.is_set():
            try:
                excluded = [get_current_segment_cb()] if get_current_segment_cb and get_current_segment_cb() else None
                self.check_once(exclude_paths=excluded)
            except Exception as exc:
                self.log.log_error(f"fallo inesperado en check_once: {exc}")
            self._stop_event.wait(self.check_interval_seconds)

    def start(self, get_current_segment_cb=None) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop, args=(get_current_segment_cb,), name="retention-thread", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: Optional[float] = 5.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout)


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory(prefix="retention_test_") as tmp:
        print(f"[auto-test] directorio temporal: {tmp}")

        segment_paths = []
        for i in range(5):
            path = os.path.join(tmp, f"acceso_{i:02d}.mp4")
            with open(path, "wb") as fh:
                fh.write(b"\0" * (1024 * 1024))
            past = time.time() - (5 - i) * 3600
            os.utime(path, (past, past))
            segment_paths.append(path)

        # 1. Marcar el más antiguo como evento
        mark_as_event(segment_paths[0])
        print(f"[auto-test] marcado como evento: {segment_paths[0]}")

        # 2. Proteger el segmento más reciente asumiendo que está "en uso"
        protected_segment = segment_paths[-1]
        print(f"[auto-test] protegiendo segmento en uso: {protected_segment}")

        log_path = os.path.join(tmp, "bitacora_retencion.log")
        policy = RetentionPolicy(
            evidence_dir=tmp,
            log_path=log_path,
            low_free_percent=100.0,
            target_free_percent=100.1,
        )
        
        deleted = policy.check_once(exclude_paths=[protected_segment])

        print(f"[auto-test] segmentos borrados: {deleted}")
        assert segment_paths[0] not in deleted, "el segmento marcado como evento NUNCA debe borrarse"
        assert protected_segment not in deleted, "el segmento excluido NUNCA debe borrarse"
        
        for p in deleted:
            assert not os.path.exists(p), f"{p} deberia haberse borrado"

        print("[auto-test] OK: politica de retencion respetó el evento y el segmento excluido")

# ---------------------------------------------------------------------------
# Cambios respecto a la version anterior
# ---------------------------------------------------------------------------
# - Bug Fix de Segmento Abierto: `check_once` ahora acepta `exclude_paths`. 
#   Se integró el parámetro a `list_deletable_segments` para ignorar rutas
#   específicas (como `current_segment_path` del pipeline) durante la
#   evaluación de borrado.
# - Retrocompatibilidad: El parámetro es opcional (`None` por defecto).
# - Auto-Test Actualizado: Se añadió un quinto segmento que funge como el
#   archivo en uso protegido. El test valida exitosamente que este archivo no
#   se borra al invocar `check_once(exclude_paths=[...])`.