"""
scripts/core/reaper.py
======================
[DEPRECATED: Compatibility facade - authoritative lifecycle reconciliation is provided by Reconciler / ReconcilerDaemon in scripts/controller/reconciler.py]
Background reaper daemon loop and dead worker cleanup routines.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from ..storage.utils import _slug

from .worker_entry import _PSUTIL_AVAILABLE

if _PSUTIL_AVAILABLE:
    import psutil

logger = logging.getLogger(__name__)

#: How long the reaper daemon waits between liveness checks.
_REAPER_INTERVAL_S: float = 5.0


class ReaperDaemon:
    """
    Daemon thread: detect and clean up workers that have died without
    going through ``stop_project``.

    Runs every ``interval`` seconds. For each dead worker:
    * Checks if a replacement worker PID is active on disk before archiving.
    * Calls ``StorageRotator.archive_project()`` so the session is
      moved from ``current/`` to ``last_run/``.
    * Removes the entry from ``registry``.
    """

    def __init__(
        self,
        registry: dict[str, Any],
        rotator: Any,
        interval: float = _REAPER_INTERVAL_S,
    ) -> None:
        self._registry = registry
        self._rotator = rotator
        self._interval = interval
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def is_running(self) -> bool:
        """Return True if background reaper thread is active."""
        return self._thread is not None and self._thread.is_alive() and not self._stop_event.is_set()

    def start(self) -> None:
        """Start the background reaper thread if not already running."""
        with self._lock:
            if self.is_running():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="spd-reaper",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Signal the reaper thread to terminate and wait for join."""
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            self._thread = None

    def reap_dead_workers(self) -> list[str]:
        """
        Compatibility wrapper: delegates dead worker inspection to authoritative
        LifecycleController reconciler. NEVER archives a project solely because a previous PID died.
        """
        curr_root = getattr(self._rotator, "root", None)
        if curr_root:
            try:
                from spd_analysis_engine.scripts.controller.controller import LifecycleController
                ctrl = getattr(self._rotator, "controller", None)
                if ctrl is None:
                    ctrl = LifecycleController(curr_root)
                reconcile_res = ctrl.reconcile()
                interrupted = reconcile_res.get("interrupted", [])
                for name in list(self._registry.keys()):
                    entry = self._registry.get(name)
                    if entry and not entry.is_alive():
                        self._registry.pop(name, None)
                return interrupted
            except Exception as exc:
                logger.debug("Reconciliation delegation in ReaperDaemon encountered: %s", exc)

        dead: list[str] = []
        for name, entry in list(self._registry.items()):
            if not entry.is_alive():
                logger.warning(
                    "Worker '%s' (PID %s) exited unexpectedly — marked interrupted (never archived solely for PID exit)",
                    name,
                    entry.pid,
                )
                dead.append(name)
                self._registry.pop(name, None)
        return dead

    def _run(self) -> None:
        """Reaper execution loop."""
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=self._interval)
            if self._stop_event.is_set():
                break
            self.reap_dead_workers()
