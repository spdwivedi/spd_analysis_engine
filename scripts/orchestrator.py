"""scripts/orchestrator.py - Backward-compatible facade for scripts/core/."""
from __future__ import annotations

try:
    from .core import (
        Supervisor, _WorkerEntry, ReaperDaemon, spawn_detached_worker,
        send_stop, send_stop_pid, kill_pid, wait_for_pid_exit,
        _STOP_TIMEOUT_S, _BOOT_TIMEOUT_S, _REAPER_INTERVAL_S, _PSUTIL_AVAILABLE,
    )
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.core import (
            Supervisor, _WorkerEntry, ReaperDaemon, spawn_detached_worker,
            send_stop, send_stop_pid, kill_pid, wait_for_pid_exit,
            _STOP_TIMEOUT_S, _BOOT_TIMEOUT_S, _REAPER_INTERVAL_S, _PSUTIL_AVAILABLE,
        )
    except (ImportError, ModuleNotFoundError):
        from scripts.core import (
            Supervisor, _WorkerEntry, ReaperDaemon, spawn_detached_worker,
            send_stop, send_stop_pid, kill_pid, wait_for_pid_exit,
            _STOP_TIMEOUT_S, _BOOT_TIMEOUT_S, _REAPER_INTERVAL_S, _PSUTIL_AVAILABLE,
        )

__all__ = [
    "Supervisor", "_WorkerEntry", "ReaperDaemon", "spawn_detached_worker",
    "send_stop", "send_stop_pid", "kill_pid", "wait_for_pid_exit",
    "_STOP_TIMEOUT_S", "_BOOT_TIMEOUT_S", "_REAPER_INTERVAL_S", "_PSUTIL_AVAILABLE",
]
