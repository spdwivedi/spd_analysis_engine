"""
scripts/core package
====================
Core engine supervisor, process spawner, reaper, and worker execution modules.
"""
from __future__ import annotations

try:
    from .worker_entry import _WorkerEntry, _PSUTIL_AVAILABLE
    from .spawner import (
        spawn_detached_worker,
        build_worker_cmd,
        send_stop,
        send_stop_pid,
        kill_pid,
        wait_for_pid_exit,
        _STOP_TIMEOUT_S,
        _BOOT_TIMEOUT_S,
    )
    from .reaper import ReaperDaemon, _REAPER_INTERVAL_S
    from .supervisor import Supervisor
    from .worker_lifecycle import (
        WorkerState,
        worker_state,
        _write_pid_file,
        _remove_pid_file,
        _write_lock_file,
        _remove_lock_file,
        _write_status_file,
        _remove_status_file,
        _emit_json,
        _clean_shutdown,
        _crash_handler,
        _install_signal_handlers,
        _unhandled_exception_handler,
    )
    from .worker_bundle import _handle_event_bundle, _run_heartbeat
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.core.worker_entry import _WorkerEntry, _PSUTIL_AVAILABLE
        from spd_analysis_engine.scripts.core.spawner import (
            spawn_detached_worker,
            build_worker_cmd,
            send_stop,
            send_stop_pid,
            kill_pid,
            wait_for_pid_exit,
            _STOP_TIMEOUT_S,
            _BOOT_TIMEOUT_S,
        )
        from spd_analysis_engine.scripts.core.reaper import ReaperDaemon, _REAPER_INTERVAL_S
        from spd_analysis_engine.scripts.core.supervisor import Supervisor
        from spd_analysis_engine.scripts.core.worker_lifecycle import (
            WorkerState,
            worker_state,
            _write_pid_file,
            _remove_pid_file,
            _write_lock_file,
            _remove_lock_file,
            _write_status_file,
            _remove_status_file,
            _emit_json,
            _clean_shutdown,
            _crash_handler,
            _install_signal_handlers,
            _unhandled_exception_handler,
        )
        from spd_analysis_engine.scripts.core.worker_bundle import _handle_event_bundle, _run_heartbeat
    except (ImportError, ModuleNotFoundError):
        from scripts.core.worker_entry import _WorkerEntry, _PSUTIL_AVAILABLE
        from scripts.core.spawner import (
            spawn_detached_worker,
            build_worker_cmd,
            send_stop,
            send_stop_pid,
            kill_pid,
            wait_for_pid_exit,
            _STOP_TIMEOUT_S,
            _BOOT_TIMEOUT_S,
        )
        from scripts.core.reaper import ReaperDaemon, _REAPER_INTERVAL_S
        from scripts.core.supervisor import Supervisor
        from scripts.core.worker_lifecycle import (
            WorkerState,
            worker_state,
            _write_pid_file,
            _remove_pid_file,
            _write_lock_file,
            _remove_lock_file,
            _write_status_file,
            _remove_status_file,
            _emit_json,
            _clean_shutdown,
            _crash_handler,
            _install_signal_handlers,
            _unhandled_exception_handler,
        )
        from scripts.core.worker_bundle import _handle_event_bundle, _run_heartbeat

__all__ = [
    "Supervisor",
    "_WorkerEntry",
    "ReaperDaemon",
    "spawn_detached_worker",
    "build_worker_cmd",
    "send_stop",
    "send_stop_pid",
    "kill_pid",
    "wait_for_pid_exit",
    "WorkerState",
    "worker_state",
    "_write_pid_file",
    "_remove_pid_file",
    "_write_lock_file",
    "_remove_lock_file",
    "_write_status_file",
    "_remove_status_file",
    "_emit_json",
    "_clean_shutdown",
    "_crash_handler",
    "_install_signal_handlers",
    "_unhandled_exception_handler",
    "_handle_event_bundle",
    "_run_heartbeat",
    "_STOP_TIMEOUT_S",
    "_BOOT_TIMEOUT_S",
    "_REAPER_INTERVAL_S",
    "_PSUTIL_AVAILABLE",
]
