"""
scripts/controller/controller.py
================================
Authoritative Lifecycle Controller for the SPD Analysis Engine.

Enforces:
- Strict per-project operation locking (OS-level file lock + in-process lock).
- Authoritative state machine transitions.
- Idempotent start and stop semantics.
- Audit trail via operations table in engine.db.
- Complete orphan detection and recovery.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from spd_analysis_engine.scripts.core.time_utils import utc_now_iso
from spd_analysis_engine.scripts.storage_rotator import _slug
from .lifecycle import ProjectState, InvalidStateTransitionError, transition_state
from .locks import EngineLock, ProjectOperationLock, OperationLockConflictError
from .operations import track_operation
from .process import is_process_alive, get_process_create_time, stop_process_cooperatively
from .recovery import reconcile_orphans
from .reconciler import Reconciler, ReconcilerDaemon
from .registry import EngineRegistry, ProjectRecord, WorkerRecord, OperationRecord

logger = logging.getLogger(__name__)


class LifecycleController:
    """Authoritative lifecycle and process supervisor controller."""

    def __init__(
        self,
        engine_root: Path | str,
        db_path: Path | str | None = None,
        worker_spawner: Callable[[ProjectRecord, str], tuple[int, float]] | None = None,
    ) -> None:
        self.engine_root = Path(engine_root).resolve()


        if db_path is not None:
            self.db_path = Path(db_path).resolve()
        else:
            data_db = self.engine_root / "data" / "engine.db"
            root_db = self.engine_root / "engine.db"
            self.db_path = root_db if (root_db.exists() and not data_db.exists()) else data_db

        self.registry = EngineRegistry(self.db_path)
        self.engine_lock = EngineLock(self.engine_root)
        self._worker_spawner = worker_spawner
        self.reconciler = Reconciler(self)
        self._reconciler_daemon: ReconcilerDaemon | None = None

    # -------------------------------------------------------------------------
    # Project Identity Resolution
    # -------------------------------------------------------------------------

    def resolve_project(self, project_id_or_slug: str) -> ProjectRecord | None:
        """Resolve a project by exact project_id, slug, or normalized display name."""
        target = str(project_id_or_slug).strip()
        proj = self.registry.get_project(target)
        if proj is not None:
            return proj
        proj = self.registry.get_project_by_slug(target)
        if proj is not None:
            return proj
        # Fallback by slugifying target
        return self.registry.get_project_by_slug(_slug(target))

    def register_project(
        self,
        display_name: str,
        root_path: str,
        config: dict[str, Any] | None = None,
        project_id: str | None = None,
        slug: str | None = None,
    ) -> ProjectRecord:
        """Register a new project in engine.db or return existing."""
        path_str = str(Path(root_path).resolve())
        p_slug = slug or _slug(display_name)
        with self.engine_lock:
            existing = self.registry.get_project_by_slug(p_slug)
            if existing is not None:
                return existing
            existing = self.registry.get_project_by_path(path_str)
            if existing is not None:
                return existing

            p_id = project_id or f"proj_{uuid.uuid4().hex[:12]}"
            cfg_json = json.dumps(config) if config else None

            return self.registry.upsert_project(
                project_id=p_id,
                slug=p_slug,
                display_name=display_name,
                root_path=path_str,
                state=ProjectState.STOPPED.value,
                config_json=cfg_json,
            )

    # -------------------------------------------------------------------------
    # Core Authoritative Operations
    # -------------------------------------------------------------------------

    def start_project(
        self,
        project_id_or_slug: str,
        path: str | None = None,
        config: dict[str, Any] | None = None,
        lock_timeout_s: float = 10.0,
        _holding_lock: bool = False,
    ) -> ProjectRecord:
        """
        Authoritatively start a project worker.

        Guarantees:
        - Mutual exclusion via ProjectOperationLock.
        - Idempotent start: if already ACTIVE, returns existing project immediately.
        - 50 concurrent calls => exactly 1 worker spawned.
        - Operation lock contention returns 409-equivalent OperationLockConflictError.
        """
        # Resolve or auto-register project
        proj = self.resolve_project(project_id_or_slug)
        if proj is None:
            if path is not None:
                proj = self.register_project(
                    display_name=project_id_or_slug,
                    root_path=path,
                    config=config,
                )
            else:
                raise KeyError(f"Project '{project_id_or_slug}' not found")

        # 1. Acquire per-project operation lock
        op_lock = ProjectOperationLock(self.engine_root, proj.project_id, timeout_s=lock_timeout_s)
        if not _holding_lock:
            op_lock.acquire()

        try:
            with track_operation(self.registry, proj.project_id, "start") as op:
                # 2. Check fresh state under lock
                fresh = self.registry.get_project(proj.project_id)
                if fresh is None:
                    raise KeyError(f"Project '{proj.project_id}' disappeared")

                # IDEMPOTENT START: If already active and worker is healthy, return immediately
                if fresh.state == ProjectState.ACTIVE.value:
                    active_worker = self.registry.get_active_worker(fresh.project_id)
                    is_alive = True
                    if self._worker_spawner is None and active_worker is not None:
                        is_alive = is_process_alive(active_worker.pid, active_worker.process_create_time)

                    if active_worker is not None and is_alive:
                        logger.info("Project '%s' is already ACTIVE; idempotent start.", fresh.slug)
                        self.registry.update_operation_state(op.operation_id, worker_id=active_worker.worker_id)
                        return fresh

                    # If worker process is dead, transition to ORPHANED before starting
                    fresh = self.registry.transition_project_state(fresh.project_id, ProjectState.ORPHANED.value)

                # Verify that transition to STARTING is legal
                # Allowed from STOPPED, INTERRUPTED, FAILED, ORPHANED
                if fresh.state not in (
                    ProjectState.STOPPED.value,
                    ProjectState.INTERRUPTED.value,
                    ProjectState.FAILED.value,
                    ProjectState.ORPHANED.value,
                ):
                    # Trigger transition validation to throw explicit InvalidStateTransitionError
                    transition_state(fresh.state, ProjectState.STARTING.value, project_id=fresh.project_id)

                # 3. Execute Start Operation: Transition -> STARTING
                fresh = self.registry.transition_project_state(fresh.project_id, ProjectState.STARTING.value)
                worker_id = str(uuid.uuid4())
                self.registry.update_operation_state(op.operation_id, state="running", worker_id=worker_id)

                # Spawn worker process
                pid: int | None = None
                create_time: float | None = None
                if self._worker_spawner is not None:
                    pid, create_time = self._worker_spawner(fresh, worker_id)
                else:
                    # Default spawner fallback
                    try:
                        from spd_analysis_engine.scripts.core.spawner import spawn_detached_worker
                        entry = spawn_detached_worker(
                            project_name=fresh.slug,
                            target_path=fresh.root_path,
                            engine_root=self.engine_root,
                            worker_id=worker_id,
                        )
                        if entry is not None:
                            pid = entry.pid
                            create_time = get_process_create_time(pid)
                    except Exception as spawn_exc:
                        logger.error("Failed to spawn detached worker: %s", spawn_exc)
                        self.registry.transition_project_state(fresh.project_id, ProjectState.FAILED.value)
                        raise

                # Record worker
                self.registry.record_worker(
                    worker_id=worker_id,
                    project_id=fresh.project_id,
                    pid=pid,
                    process_create_time=create_time,
                    canonical_target=fresh.root_path,
                    engine_root=str(self.engine_root),
                    state=ProjectState.STARTING.value,
                )

                # Transition STARTING -> INDEXING -> ACTIVE
                fresh = self.registry.transition_project_state(fresh.project_id, ProjectState.INDEXING.value)
                fresh = self.registry.transition_project_state(fresh.project_id, ProjectState.ACTIVE.value)

                # Update worker state to ACTIVE
                self.registry.update_worker_state(worker_id, state=ProjectState.ACTIVE.value)
                return fresh

        finally:
            op_lock.release()

    def stop_project(
        self,
        project_id_or_slug: str,
        timeout_s: float = 20.0,
        lock_timeout_s: float = 10.0,
        _holding_lock: bool = False,
    ) -> ProjectRecord:
        """
        Authoritatively stop a project worker.

        Guarantees:
        - Mutual exclusion via ProjectOperationLock.
        - Idempotent stop: if already STOPPED, returns cleanly without error.
        - Transitions: ACTIVE -> STOPPING -> STOPPED (or INTERRUPTED if forced kill).
        """
        proj = self.resolve_project(project_id_or_slug)
        if proj is None:
            raise KeyError(f"Project '{project_id_or_slug}' not found")

        # 1. Acquire per-project operation lock
        op_lock = ProjectOperationLock(self.engine_root, proj.project_id, timeout_s=lock_timeout_s)
        if not _holding_lock:
            op_lock.acquire()

        try:
            with track_operation(self.registry, proj.project_id, "stop") as op:
                # 2. Check fresh state under lock
                fresh = self.registry.get_project(proj.project_id)
                if fresh is None:
                    raise KeyError(f"Project '{proj.project_id}' disappeared")

                # IDEMPOTENT STOP: If already STOPPED, return immediately
                if fresh.state == ProjectState.STOPPED.value:
                    logger.info("Project '%s' is already STOPPED; idempotent stop.", fresh.slug)
                    return fresh

                active_worker = self.registry.get_active_worker(fresh.project_id)
                if active_worker is not None:
                    self.registry.update_operation_state(op.operation_id, worker_id=active_worker.worker_id)

                # Transition -> STOPPING
                fresh = self.registry.transition_project_state(fresh.project_id, ProjectState.STOPPING.value)

                # Stop worker process: attempt loopback control channel first
                clean_stop = True
                end_reason = "stopped"
                if active_worker is not None and active_worker.pid is not None:
                    if self._worker_spawner is None:
                        proj_dir = self.engine_root / "current" / fresh.slug
                        control_stopped = False
                        if (proj_dir / "worker.control.json").exists():
                            try:
                                from .control_channel import send_worker_command
                                resp = send_worker_command(
                                    project_dir=proj_dir,
                                    command="STOP",
                                    project_id=fresh.project_id,
                                    worker_id=active_worker.worker_id,
                                    deadline_ms=int(timeout_s * 1000),
                                    timeout_s=min(3.0, timeout_s),
                                )
                                if resp.get("ok"):
                                    # Wait for worker process to exit
                                    start_wait = time.time()
                                    while time.time() - start_wait < timeout_s:
                                        if not is_process_alive(active_worker.pid, active_worker.process_create_time):
                                            control_stopped = True
                                            break
                                        time.sleep(0.1)
                            except Exception as cmd_err:
                                logger.debug("Control channel stop attempt failed: %s", cmd_err)

                        if not control_stopped and is_process_alive(active_worker.pid, active_worker.process_create_time):
                            stop_cmd = self.engine_root / "locks" / f"stop_{fresh.slug}.cmd"
                            clean_stop, end_reason = stop_process_cooperatively(
                                pid=active_worker.pid,
                                timeout_s=timeout_s,
                                stop_cmd_path=stop_cmd,
                                expected_create_time=active_worker.process_create_time,
                            )
                        else:
                            clean_stop = True
                            end_reason = "clean_stop"
                    else:
                        clean_stop = True
                        end_reason = "clean_stop"

                # Transition to STOPPED or INTERRUPTED
                final_state = ProjectState.STOPPED.value if clean_stop else ProjectState.INTERRUPTED.value
                fresh = self.registry.transition_project_state(fresh.project_id, final_state)

                if active_worker is not None:
                    self.registry.update_worker_state(
                        worker_id=active_worker.worker_id,
                        state=final_state,
                        exit_code=0 if clean_stop else -9,
                        end_reason=end_reason,
                    )

                return fresh

        finally:
            if not _holding_lock:
                op_lock.release()

    def resume_project(self, project_id_or_slug: str, lock_timeout_s: float = 10.0) -> ProjectRecord:
        """Resume an interrupted, failed, or stopped project."""
        proj = self.resolve_project(project_id_or_slug)
        if proj is None:
            raise KeyError(f"Project '{project_id_or_slug}' not found")

        op_lock = ProjectOperationLock(self.engine_root, proj.project_id, timeout_s=lock_timeout_s)
        op_lock.acquire()
        try:
            with track_operation(self.registry, proj.project_id, "resume"):
                return self.start_project(proj.project_id, lock_timeout_s=lock_timeout_s, _holding_lock=True)
        finally:
            op_lock.release()

    def rollback_project(self, project_id_or_slug: str, lock_timeout_s: float = 10.0) -> ProjectRecord:
        """Execute a rollback operation under the operation lock."""
        proj = self.resolve_project(project_id_or_slug)
        if proj is None:
            raise KeyError(f"Project '{project_id_or_slug}' not found")

        op_lock = ProjectOperationLock(self.engine_root, proj.project_id, timeout_s=lock_timeout_s)
        op_lock.acquire()
        try:
            with track_operation(self.registry, proj.project_id, "rollback"):
                # Rollback operation placeholder recorded in operations table
                fresh = self.registry.get_project(proj.project_id)
                return fresh  # type: ignore
        finally:
            op_lock.release()

    def delete_project(self, project_id_or_slug: str, lock_timeout_s: float = 10.0) -> bool:
        """Delete a project from the registry under the operation lock."""
        proj = self.resolve_project(project_id_or_slug)
        if proj is None:
            return False

        op_lock = ProjectOperationLock(self.engine_root, proj.project_id, timeout_s=lock_timeout_s)
        op_lock.acquire()
        try:
            with track_operation(self.registry, proj.project_id, "delete"):
                if proj.state != ProjectState.STOPPED.value:
                    try:
                        self.stop_project(proj.project_id, timeout_s=5.0, lock_timeout_s=lock_timeout_s, _holding_lock=True)
                    except Exception:
                        pass
                return self.registry.delete_project(proj.project_id)
        finally:
            op_lock.release()

    def recover_orphans(self) -> list[str]:
        """Perform orphan recovery against live system processes."""
        with self.engine_lock:
            return reconcile_orphans(self.registry)

    def reconcile(self) -> dict[str, list[str]]:
        """Run an immediate reconciliation pass via authoritative Reconciler."""
        return self.reconciler.reconcile_once()

    def start_reconciler_daemon(self, interval_s: float = 5.0) -> None:
        """Start periodic reconciler daemon (~every 5s)."""
        if self._reconciler_daemon is None or not self._reconciler_daemon.is_running():
            self._reconciler_daemon = ReconcilerDaemon(self, interval_s=interval_s)
            self._reconciler_daemon.start()

    def stop_reconciler_daemon(self, timeout_s: float = 5.0) -> None:
        """Stop periodic reconciler daemon."""
        if self._reconciler_daemon is not None:
            self._reconciler_daemon.stop(timeout_s=timeout_s)
            self._reconciler_daemon = None
