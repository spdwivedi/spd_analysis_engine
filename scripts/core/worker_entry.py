"""
scripts/core/worker_entry.py
============================
Runtime process record and liveness tracker for single project workers.
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

try:
    import psutil
    _PSUTIL_AVAILABLE = True
except ImportError:
    _PSUTIL_AVAILABLE = False
    logging.getLogger(__name__).warning(
        "psutil not installed — memory/CPU stats and PID verification "
        "will be limited.  Run: pip install psutil"
    )

logger = logging.getLogger(__name__)


class _WorkerEntry:
    """
    Runtime record for a single project worker.

    Supports two creation modes:

    **Spawned** (``proc`` is not ``None``)
        This Supervisor launched the worker in the current process via
        ``subprocess.Popen``.  All ``Popen`` methods are available.

    **Reconciled** (``proc`` is ``None``)
        The worker was discovered from a ``worker.pid`` file on disk.
        Liveness is checked via ``psutil`` or ``os.kill(pid, 0)``.
        Signals are sent via ``os.kill(pid, …)``.

    Parameters
    ----------
    name          : Project name as supplied by the caller.
    target_path   : Absolute path being monitored.
    pid           : OS process ID of the worker.
    proc          : ``Popen`` handle (spawned mode only).
    log_path      : Path to ``worker.log`` for this project.
    started_at_iso: UTC timestamp string from ``worker.pid``.
    """

    def __init__(
        self,
        name: str,
        target_path: str,
        pid: int,
        *,
        proc: subprocess.Popen | None = None,
        log_path: Path | None = None,
        started_at_iso: str | None = None,
    ) -> None:
        self.name = name
        self.target_path = target_path
        self.pid = pid
        self.proc = proc
        self.log_path = log_path
        self.started_at_iso = started_at_iso
        self._mono_start = time.monotonic()
        self.session_id: int | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def reconciled(self) -> bool:
        """``True`` if this entry was discovered from disk (not spawned here)."""
        return self.proc is None

    @property
    def exit_code(self) -> int | None:
        """Exit code — available only for spawned workers after they exit."""
        return self.proc.returncode if self.proc is not None else None

    # ------------------------------------------------------------------
    # Methods
    # ------------------------------------------------------------------

    def is_alive(self) -> bool:
        """Return ``True`` if the worker OS process is still running."""
        if self.proc is not None:
            return self.proc.poll() is None

        # Reconciled path — prefer psutil, fall back to os.kill signal-0
        if _PSUTIL_AVAILABLE:
            try:
                p = psutil.Process(self.pid)
                return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
            except psutil.NoSuchProcess:
                return False

        try:
            os.kill(self.pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    def uptime_s(self) -> float:
        """
        Best-effort uptime in seconds.

        Uses ``psutil.Process.create_time()`` (wall-clock accurate) when
        psutil is available; otherwise falls back to a monotonic timer
        started when this entry was created.
        """
        if _PSUTIL_AVAILABLE:
            try:
                return time.time() - psutil.Process(self.pid).create_time()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return time.monotonic() - self._mono_start
