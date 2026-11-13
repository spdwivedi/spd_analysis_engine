"""
scripts/core/suppression.py
===========================
Atomic runtime file path suppression registry to prevent self-triggering
watchdog cascades from internal file updates (e.g., .gitignore, CHANGELOG.md).
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

_SUPPRESSED_PATHS_LOCK = threading.Lock()
_SUPPRESSED_PATHS: dict[str, float] = {}


def suppress_file_path(file_path: str, duration_s: float = 4.0) -> None:
    """Suppress watchdog events for a specific file path or filename for duration_s seconds."""
    norm = str(file_path).replace("\\", "/").lower().strip("/")
    base = Path(file_path).name.lower()
    expiry = time.monotonic() + duration_s
    with _SUPPRESSED_PATHS_LOCK:
        _SUPPRESSED_PATHS[norm] = expiry
        if base:
            _SUPPRESSED_PATHS[base] = expiry


def is_path_suppressed(file_path: str) -> bool:
    """Check if file_path is currently under temporary suppression window."""
    norm = str(file_path).replace("\\", "/").lower().strip("/")
    base = Path(file_path).name.lower()
    now = time.monotonic()
    with _SUPPRESSED_PATHS_LOCK:
        expired = [k for k, exp in _SUPPRESSED_PATHS.items() if exp < now]
        for k in expired:
            del _SUPPRESSED_PATHS[k]
        return (_SUPPRESSED_PATHS.get(norm, 0.0) > now) or (_SUPPRESSED_PATHS.get(base, 0.0) > now)
