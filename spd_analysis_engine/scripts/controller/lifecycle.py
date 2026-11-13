"""
scripts/controller/lifecycle.py
==============================
Authoritative state machine and lifecycle transition engine.

States:
- STOPPED: Worker process not running
- STARTING: Spawning process, allocating identity and locks
- INDEXING: Initial file scan / baseline capture
- ACTIVE: Normal monitoring and telemetry processing
- STOPPING: Graceful shutdown in progress
- INTERRUPTED: Emergency shutdown or timeout escalation
- FAILED: Worker failed during startup
- UNRESPONSIVE: Heartbeat timed out or communication lost
- ORPHANED: Process dead or lock severed unexpectedly

Invariants:
- Every transition must go through transition_state().
- Illegal transitions must fail explicitly with InvalidStateTransitionError.
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class ProjectState(str, Enum):
    """Authoritative project lifecycle states."""

    STOPPED = "STOPPED"
    STARTING = "STARTING"
    INDEXING = "INDEXING"
    ACTIVE = "ACTIVE"
    STOPPING = "STOPPING"
    INTERRUPTED = "INTERRUPTED"
    FAILED = "FAILED"
    UNRESPONSIVE = "UNRESPONSIVE"
    ORPHANED = "ORPHANED"


class InvalidStateTransitionError(Exception):
    """Raised when an illegal or undefined state transition is attempted."""

    def __init__(self, current_state: str, target_state: str, project_id: str | None = None):
        msg = f"Illegal state transition: {current_state} -> {target_state}"
        if project_id:
            msg = f"Project '{project_id}': {msg}"
        super().__init__(msg)
        self.current_state = current_state
        self.target_state = target_state
        self.project_id = project_id


# Explicit transition map defining all allowed transitions:
# STOPPED -> STARTING
# STARTING -> INDEXING
# INDEXING -> ACTIVE
# ACTIVE -> STOPPING
# STOPPING -> STOPPED
# STARTING/INDEXING/ACTIVE -> INTERRUPTED
# STARTING -> FAILED
# ACTIVE -> UNRESPONSIVE
# any -> ORPHANED
# INTERRUPTED/FAILED -> STARTING
VALID_TRANSITIONS: dict[str, set[str]] = {
    ProjectState.STOPPED.value: {
        ProjectState.STARTING.value,
        ProjectState.ORPHANED.value,
    },
    ProjectState.STARTING.value: {
        ProjectState.INDEXING.value,
        ProjectState.INTERRUPTED.value,
        ProjectState.FAILED.value,
        ProjectState.ORPHANED.value,
    },
    ProjectState.INDEXING.value: {
        ProjectState.ACTIVE.value,
        ProjectState.INTERRUPTED.value,
        ProjectState.ORPHANED.value,
    },
    ProjectState.ACTIVE.value: {
        ProjectState.STOPPING.value,
        ProjectState.INTERRUPTED.value,
        ProjectState.UNRESPONSIVE.value,
        ProjectState.ORPHANED.value,
    },
    ProjectState.STOPPING.value: {
        ProjectState.STOPPED.value,
        ProjectState.INTERRUPTED.value,
        ProjectState.ORPHANED.value,
    },
    ProjectState.INTERRUPTED.value: {
        ProjectState.STARTING.value,
        ProjectState.STOPPED.value,
        ProjectState.ORPHANED.value,
    },
    ProjectState.FAILED.value: {
        ProjectState.STARTING.value,
        ProjectState.STOPPED.value,
        ProjectState.ORPHANED.value,
    },
    ProjectState.UNRESPONSIVE.value: {
        ProjectState.ACTIVE.value,
        ProjectState.STOPPING.value,
        ProjectState.INTERRUPTED.value,
        ProjectState.ORPHANED.value,
    },
    ProjectState.ORPHANED.value: {
        ProjectState.STARTING.value,
        ProjectState.STOPPED.value,
        ProjectState.ORPHANED.value,
    },
}


def is_valid_transition(current_state: str, target_state: str) -> bool:
    """Check if transitioning from current_state to target_state is allowed."""
    current = str(current_state).strip().upper()
    target = str(target_state).strip().upper()

    # Rule: any -> ORPHANED is always allowed
    if target == ProjectState.ORPHANED.value:
        return True

    allowed = VALID_TRANSITIONS.get(current, set())
    return target in allowed


def transition_state(
    current_state: str,
    target_state: str,
    project_id: str | None = None,
) -> str:
    """
    Authoritative state transition validator.
    Every state change in the engine must pass through this single function.
    Raises InvalidStateTransitionError if the requested transition is illegal.
    """
    current = str(current_state).strip().upper()
    target = str(target_state).strip().upper()

    if not is_valid_transition(current, target):
        logger.error(
            "State machine rejected transition %s -> %s for project %s",
            current,
            target,
            project_id or "<unspecified>",
        )
        raise InvalidStateTransitionError(
            current_state=current,
            target_state=target,
            project_id=project_id,
        )

    logger.debug(
        "Transitioned %s: %s -> %s",
        project_id or "project",
        current,
        target,
    )
    return target
