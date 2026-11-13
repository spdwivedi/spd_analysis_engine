"""
scripts/controller package
==========================
Authoritative lifecycle, process coordination, and state management layer
for the SPD Analysis Engine.
"""
from __future__ import annotations

from .controller import LifecycleController
from .lifecycle import (
    ProjectState,
    InvalidStateTransitionError,
    transition_state,
    is_valid_transition,
    VALID_TRANSITIONS,
)
from .locks import (
    EngineLock,
    ProjectOperationLock,
    FileLock,
    OperationLockConflictError,
)
from .operations import track_operation
from .process import is_process_alive, get_process_create_time, stop_process_cooperatively
from .recovery import reconcile_orphans
from .control_channel import (
    WorkerControlServer,
    send_worker_command,
    PROTOCOL_VERSION,
    VALID_COMMANDS,
)
from .reconciler import (
    Reconciler,
    ReconcilerDaemon,
)
from .registry import (
    EngineRegistry,
    ProjectRecord,
    WorkerRecord,
    OperationRecord,
    SCHEMA_VERSION,
)
from .jobs import (
    JobRecord,
    AiAnalysisRecord,
    ControllerFlag,
    init_controller_tables,
    create_job,
    update_job_state,
    get_job,
    record_ai_analysis,
    get_ai_analysis,
    set_controller_flag,
    get_controller_flag,
)

__all__ = [
    "LifecycleController",
    "EngineRegistry",
    "ProjectRecord",
    "WorkerRecord",
    "OperationRecord",
    "SCHEMA_VERSION",
    "ProjectState",
    "InvalidStateTransitionError",
    "transition_state",
    "is_valid_transition",
    "VALID_TRANSITIONS",
    "EngineLock",
    "ProjectOperationLock",
    "FileLock",
    "OperationLockConflictError",
    "track_operation",
    "is_process_alive",
    "get_process_create_time",
    "stop_process_cooperatively",
    "reconcile_orphans",
    "WorkerControlServer",
    "send_worker_command",
    "PROTOCOL_VERSION",
    "VALID_COMMANDS",
    "Reconciler",
    "ReconcilerDaemon",
    "JobRecord",
    "AiAnalysisRecord",
    "ControllerFlag",
    "init_controller_tables",
    "create_job",
    "update_job_state",
    "get_job",
    "record_ai_analysis",
    "get_ai_analysis",
    "set_controller_flag",
    "get_controller_flag",
]
