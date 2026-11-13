"""
scripts/controller/operations.py
================================
Operation tracking and auditing for authoritative lifecycle mutations.

Supported operations:
- start
- stop
- resume
- rollback
- delete
"""
from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager
from typing import Generator

from spd_analysis_engine.scripts.core.time_utils import utc_now_iso
from .registry import EngineRegistry, OperationRecord

logger = logging.getLogger(__name__)

VALID_OPERATION_KINDS = {"start", "stop", "resume", "rollback", "delete"}


class OperationExecutionError(Exception):
    """Raised when an operation encounters an internal execution error."""


@contextmanager
def track_operation(
    registry: EngineRegistry,
    project_id: str,
    kind: str,
    worker_id: str | None = None,
) -> Generator[OperationRecord, None, None]:
    """
    Context manager recording the lifecycle of an operation in engine.db.
    Transitions: pending -> running -> completed | failed.
    """
    norm_kind = str(kind).strip().lower()
    if norm_kind not in VALID_OPERATION_KINDS:
        raise ValueError(f"Unknown operation kind '{kind}'. Must be one of {VALID_OPERATION_KINDS}")

    op_id = f"op_{uuid.uuid4().hex[:12]}"
    op = registry.create_operation(
        operation_id=op_id,
        project_id=project_id,
        worker_id=worker_id,
        kind=norm_kind,
        state="pending",
    )

    started_at = utc_now_iso()
    op = registry.update_operation_state(
        operation_id=op_id,
        state="running",
        started_at=started_at,
        worker_id=worker_id,
    )
    logger.info("Started operation %s (%s) for project %s", op_id, norm_kind, project_id)

    try:
        yield op
        finished_at = utc_now_iso()
        registry.update_operation_state(
            operation_id=op_id,
            state="completed",
            finished_at=finished_at,
        )
        logger.info("Completed operation %s (%s) for project %s", op_id, norm_kind, project_id)
    except Exception as exc:
        finished_at = utc_now_iso()
        err_msg = str(exc)
        registry.update_operation_state(
            operation_id=op_id,
            state="failed",
            finished_at=finished_at,
            error=err_msg,
        )
        logger.error(
            "Operation %s (%s) failed for project %s: %s",
            op_id,
            norm_kind,
            project_id,
            err_msg,
        )
        raise
