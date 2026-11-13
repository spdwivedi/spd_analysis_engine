"""
scripts/controller/recovery.py
==============================
Startup recovery, orphan reconciliation, and dangling worker cleanup.
"""
from __future__ import annotations

import logging
from typing import Any

from .lifecycle import ProjectState, transition_state
from .process import is_process_alive
from .registry import EngineRegistry, ProjectRecord

logger = logging.getLogger(__name__)


def reconcile_orphans(registry: EngineRegistry) -> list[str]:
    """
    Scan all projects in non-terminal states (STARTING, INDEXING, ACTIVE, STOPPING).
    If their registered worker process is no longer running, transition project
    to ORPHANED and mark the worker stopped.
    Returns list of recovered project IDs.
    """
    orphaned_ids: list[str] = []
    projects = registry.list_projects()

    for project in projects:
        state = project.state
        if state not in (
            ProjectState.STARTING.value,
            ProjectState.INDEXING.value,
            ProjectState.ACTIVE.value,
            ProjectState.STOPPING.value,
        ):
            continue

        active_worker = registry.get_active_worker(project.project_id)
        is_alive = False
        if active_worker is not None and active_worker.pid is not None:
            is_alive = is_process_alive(
                active_worker.pid,
                expected_create_time=active_worker.process_create_time,
            )

        if not is_alive:
            logger.warning(
                "Project '%s' is in state %s but worker process (PID %s) is dead. Marking ORPHANED.",
                project.slug,
                state,
                active_worker.pid if active_worker else "None",
            )
            try:
                registry.transition_project_state(project.project_id, ProjectState.ORPHANED.value)
                if active_worker is not None:
                    registry.update_worker_state(
                        worker_id=active_worker.worker_id,
                        state=ProjectState.STOPPED.value,
                        end_reason="orphaned_on_recovery",
                        exit_code=-1,
                    )
                orphaned_ids.append(project.project_id)
            except Exception as exc:
                logger.error("Failed to mark project '%s' as ORPHANED: %s", project.slug, exc)

    return orphaned_ids
