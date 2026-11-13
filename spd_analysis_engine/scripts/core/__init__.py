"""
scripts/core package
====================
Core engine supervisor, process spawner, reaper, and worker execution modules.
"""
from __future__ import annotations

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
from .worker_interlock import interlock_project_operation
from .process_sweeper import sweep_project_processes
from .project_config import (
    ProjectConfig,
    DEFAULT_DEBOUNCE_S,
    DEFAULT_MAX_BURST_S,
    MIN_DEBOUNCE_S,
    MAX_DEBOUNCE_S,
)
from .time_utils import (
    utc_now_iso,
    parse_iso_timestamp,
    calculate_heartbeat_age_s,
    format_ui_local_iso,
    IST_TIMEZONE,
)
from .fs_event import FsEvent, EventCoalescer, is_editor_temp_file
from .burst_assembler import BurstAssembler
from .change_processor import ChangeProcessor

__all__ = [
    "Supervisor",
    "_WorkerEntry",
    "ReaperDaemon",
    "interlock_project_operation",
    "sweep_project_processes",
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
    "ProjectConfig",
    "DEFAULT_DEBOUNCE_S",
    "DEFAULT_MAX_BURST_S",
    "MIN_DEBOUNCE_S",
    "MAX_DEBOUNCE_S",
    "utc_now_iso",
    "parse_iso_timestamp",
    "calculate_heartbeat_age_s",
    "format_ui_local_iso",
    "IST_TIMEZONE",
    "FsEvent",
    "EventCoalescer",
    "is_editor_temp_file",
    "BurstAssembler",
    "ChangeProcessor",
]
