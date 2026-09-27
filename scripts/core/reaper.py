"""
scripts/core/reaper.py
======================
Background reaper daemon loop and dead worker cleanup routines.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

#: How long the reaper daemon waits between liveness checks.
_REAPER_INTERVAL_S: float = 5.0


class ReaperDaemon:
    """
    Daemon thread: detect and clean up workers that have died without
    going through ``stop_project``.

    Runs every ``interval`` seconds. For each dead worker:
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
        self._thread = threading.Thread(
            target=self._run,
            name="spd-reaper",
            daemon=True,
        )

    def start(self) -> None:
        """Start the background reaper thread."""
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Signal the reaper thread to terminate and wait for join."""
        self._stop_event.set()
        self._thread.join(timeout=timeout)

    def _run(self) -> None:
        """Reaper execution loop."""
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=self._interval)

            dead: list[str] = []
            for name, entry in list(self._registry.items()):
                if not entry.is_alive():
                    logger.warning(
                        "Worker '%s' (PID %s) exited unexpectedly "
                        "(exit_code=%s) — archiving",
                        name,
                        entry.pid,
                        entry.exit_code if entry.exit_code is not None else "unknown",
                    )
                    dead.append(name)

            for name in dead:
                try:
                    self._rotator.archive_project(name)
                except Exception as exc:  # noqa: BLE001
                    logger.error("Error archiving dead project '%s': %s", name, exc)
                self._registry.pop(name, None)
