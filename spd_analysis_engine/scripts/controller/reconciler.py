"""
scripts/controller/reconciler.py
================================
Authoritative lifecycle reconciler daemon and state reconciliation engine.

Reconciler rules:
1. gone -> INTERRUPTED (with startup grace period protection)
2. valid unknown worker -> ADOPT
3. unknown unverifiable worker -> ORPHANED
4. stale heartbeat -> UNRESPONSIVE (never automatically kill!)

Invariants:
- A stale heartbeat must never automatically mean 'kill'.
- The reconciler must NEVER archive a project solely because a previous PID died.
- A worker started within its configured startup grace period must not be declared dead.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from spd_analysis_engine.scripts.core.time_utils import (
    utc_now_iso,
    parse_iso_timestamp,
    calculate_heartbeat_age_s,
)
from spd_analysis_engine.scripts.storage_rotator import _slug
from .lifecycle import ProjectState, is_valid_transition, transition_state
from .process import is_process_alive, get_process_create_time
from .registry import EngineRegistry, ProjectRecord, WorkerRecord

logger = logging.getLogger(__name__)

DEFAULT_RECONCILE_INTERVAL_S = 5.0
DEFAULT_STARTUP_GRACE_S = 10.0
DEFAULT_STALE_HEARTBEAT_S = 15.0


class Reconciler:
    """Core reconciliation logic comparing desired database state with process reality."""

    def __init__(
        self,
        controller: Any,
        startup_grace_s: float = DEFAULT_STARTUP_GRACE_S,
        stale_heartbeat_s: float = DEFAULT_STALE_HEARTBEAT_S,
    ) -> None:
        self.controller = controller
        self.registry: EngineRegistry = controller.registry
        self.engine_root: Path = Path(controller.engine_root).resolve()
        self.startup_grace_s = float(startup_grace_s)
        self.stale_heartbeat_s = float(stale_heartbeat_s)
        self.last_reconcile_ts: float | None = None

    def reconcile_once(self) -> dict[str, list[str]]:
        """
        Execute a single reconciliation pass across all projects and workers.
        Returns a dict summarizing actions taken:
        {'interrupted': [...], 'adopted': [...], 'orphaned': [...], 'unresponsive': [...]}
        """
        self.last_reconcile_ts = time.time()
        results: dict[str, list[str]] = {
            "interrupted": [],
            "adopted": [],
            "orphaned": [],
            "unresponsive": [],
        }

        with self.controller.engine_lock:
            self._reconcile_on_disk_workers(results)
            self._reconcile_known_projects(results)

        return results

    def _reconcile_known_projects(self, results: dict[str, list[str]]) -> None:
        """Check all registered projects in engine.db against live processes."""
        projects = self.registry.list_projects()
        now = time.time()

        for project in projects:
            state = project.state
            active_worker = self.registry.get_active_worker(project.project_id)

            # Only inspect projects in non-terminal states
            if state not in (
                ProjectState.STARTING.value,
                ProjectState.INDEXING.value,
                ProjectState.ACTIVE.value,
                ProjectState.STOPPING.value,
                ProjectState.UNRESPONSIVE.value,
            ):
                continue

            # -----------------------------------------------------------------
            # Startup Grace Period Invariant:
            # A worker started within startup_grace_s must not be declared dead.
            # -----------------------------------------------------------------
            if active_worker is not None and active_worker.started_at:
                dt_started = parse_iso_timestamp(active_worker.started_at)
                if dt_started is not None:
                    age = now - dt_started.timestamp()
                    if age < self.startup_grace_s:
                        logger.debug(
                            "Project '%s' worker %s within startup grace period (%.1fs < %.1fs)",
                            project.slug,
                            active_worker.worker_id,
                            age,
                            self.startup_grace_s,
                        )
                        continue

            # Check if active worker process is alive
            is_alive = False
            if active_worker is not None and active_worker.pid is not None:
                is_alive = is_process_alive(active_worker.pid, active_worker.process_create_time)

            if is_alive and active_worker is not None:
                # -------------------------------------------------------------
                # Rule: stale heartbeat -> UNRESPONSIVE (NEVER kill!)
                # -------------------------------------------------------------
                hb_age = self._check_heartbeat_age(project.slug, active_worker)
                if hb_age is not None and hb_age > self.stale_heartbeat_s:
                    if state == ProjectState.ACTIVE.value:
                        logger.warning(
                            "Project '%s' heartbeat stale (%.1fs > %.1fs). Marking UNRESPONSIVE (no kill).",
                            project.slug,
                            hb_age,
                            self.stale_heartbeat_s,
                        )
                        self.registry.transition_project_state(project.project_id, ProjectState.UNRESPONSIVE.value)
                        self.registry.update_worker_state(active_worker.worker_id, state=ProjectState.UNRESPONSIVE.value)
                        results["unresponsive"].append(project.project_id)
                elif state == ProjectState.UNRESPONSIVE.value and (hb_age is None or hb_age <= self.stale_heartbeat_s):
                    logger.info("Project '%s' heartbeat recovered. Transitioning to ACTIVE.", project.slug)
                    self.registry.transition_project_state(project.project_id, ProjectState.ACTIVE.value)
                    self.registry.update_worker_state(active_worker.worker_id, state=ProjectState.ACTIVE.value)
            else:
                # -------------------------------------------------------------
                # Rule: gone -> INTERRUPTED
                # INVARIANT: NEVER archive project solely because previous PID died!
                # -------------------------------------------------------------
                logger.warning(
                    "Project '%s' worker process is gone (PID %s). Marking INTERRUPTED.",
                    project.slug,
                    active_worker.pid if active_worker else "None",
                )
                try:
                    self.registry.transition_project_state(project.project_id, ProjectState.INTERRUPTED.value)
                    if active_worker is not None:
                        self.registry.update_worker_state(
                            active_worker.worker_id,
                            state=ProjectState.INTERRUPTED.value,
                            end_reason="process_exited_unexpectedly",
                            exit_code=-1,
                        )
                    results["interrupted"].append(project.project_id)
                except Exception as exc:
                    logger.error("Failed to mark project '%s' as INTERRUPTED: %s", project.slug, exc)

    def _check_heartbeat_age(self, slug: str, worker: WorkerRecord) -> float | None:
        """Check heartbeat age from workers table or current/<slug>/worker.status.json."""
        if worker.last_heartbeat:
            age = calculate_heartbeat_age_s(worker.last_heartbeat)
            if age is not None:
                return age

        status_file = self.engine_root / "current" / slug / "worker.status.json"
        if status_file.exists():
            try:
                data = json.loads(status_file.read_text(encoding="utf-8"))
                ts = data.get("heartbeat_at") or data.get("last_heartbeat") or data.get("timestamp")
                if ts:
                    return calculate_heartbeat_age_s(ts)
            except Exception:
                pass
        return None

    def _reconcile_on_disk_workers(self, results: dict[str, list[str]]) -> None:
        """
        Inspect filesystem current/ directories for unknown or unverified workers.
        Rules:
        - valid unknown worker -> ADOPT
        - unknown unverifiable worker -> ORPHANED
        """
        current_tier = self.engine_root / "current"
        if not current_tier.exists():
            return

        for project_dir in current_tier.iterdir():
            if not project_dir.is_dir():
                continue

            slug = project_dir.name
            pid_file = project_dir / "worker.pid"
            if not pid_file.exists():
                continue

            # Attempt to parse worker identity from on-disk sentinel
            disk_data = self._read_pid_file(pid_file)
            if disk_data is None:
                # Corrupted / unreadable PID file => unknown unverifiable worker -> ORPHANED
                logger.warning("Unverifiable worker metadata in %s. Marking ORPHANED.", project_dir)
                proj = self.controller.resolve_project(slug)
                if proj is not None and proj.state != ProjectState.ORPHANED.value:
                    try:
                        self.registry.transition_project_state(proj.project_id, ProjectState.ORPHANED.value)
                        results["orphaned"].append(proj.project_id)
                    except Exception:
                        pass
                continue

            disk_pid = disk_data.get("pid")
            disk_worker_id = disk_data.get("worker_id") or f"w_adopted_{slug}"
            disk_create_time = disk_data.get("process_create_time")
            target_path = disk_data.get("target_path") or disk_data.get("root_path") or ""

            # Verify if process is alive
            alive = is_process_alive(disk_pid, disk_create_time)
            if not alive:
                continue

            # Process is alive! Check if it's already recorded in engine.db
            proj = self.controller.resolve_project(slug)
            if proj is None:
                # Register project
                proj = self.controller.register_project(
                    display_name=disk_data.get("display_name") or slug,
                    root_path=target_path or str(project_dir),
                    slug=slug,
                )

            active_worker = self.registry.get_active_worker(proj.project_id)
            if active_worker is None or active_worker.pid != disk_pid or active_worker.worker_id != disk_worker_id:
                # -------------------------------------------------------------
                # Rule: valid unknown worker -> ADOPT!
                # -------------------------------------------------------------
                logger.info(
                    "Adopting valid running worker PID %s (worker_id=%s) for project '%s'",
                    disk_pid,
                    disk_worker_id,
                    slug,
                )
                self.registry.record_worker(
                    worker_id=disk_worker_id,
                    project_id=proj.project_id,
                    pid=disk_pid,
                    process_create_time=disk_create_time,
                    canonical_target=target_path,
                    engine_root=str(self.engine_root),
                    state=ProjectState.ACTIVE.value,
                    started_at=disk_data.get("started_at") or utc_now_iso(),
                )

                # Ensure project state is ACTIVE
                if proj.state != ProjectState.ACTIVE.value:
                    self._force_project_active(proj.project_id)

                results["adopted"].append(proj.project_id)

    def _force_project_active(self, project_id: str) -> None:
        """Safely transition project through legal sequence to ACTIVE."""
        proj = self.registry.get_project(project_id)
        if proj is None or proj.state == ProjectState.ACTIVE.value:
            return

        state = proj.state
        if state in (ProjectState.STOPPED.value, ProjectState.INTERRUPTED.value, ProjectState.FAILED.value, ProjectState.ORPHANED.value):
            self.registry.transition_project_state(project_id, ProjectState.STARTING.value)
            state = ProjectState.STARTING.value
        if state == ProjectState.STARTING.value:
            self.registry.transition_project_state(project_id, ProjectState.INDEXING.value)
            state = ProjectState.INDEXING.value
        if state == ProjectState.INDEXING.value:
            self.registry.transition_project_state(project_id, ProjectState.ACTIVE.value)

    def _read_pid_file(self, pid_file: Path) -> dict[str, Any] | None:
        try:
            content = pid_file.read_text(encoding="utf-8").strip()
            if not content:
                return None
            if content.startswith("{"):
                return json.loads(content)
            # Legacy plain PID format
            lines = content.splitlines()
            pid = int(lines[0].strip())
            return {"pid": pid, "worker_id": None}
        except Exception:
            return None


class ReconcilerDaemon:
    """Background daemon thread executing periodic reconciliation passes (~every 5s)."""

    def __init__(
        self,
        controller: Any,
        interval_s: float = DEFAULT_RECONCILE_INTERVAL_S,
        startup_grace_s: float = DEFAULT_STARTUP_GRACE_S,
        stale_heartbeat_s: float = DEFAULT_STALE_HEARTBEAT_S,
    ) -> None:
        self.reconciler = Reconciler(
            controller=controller,
            startup_grace_s=startup_grace_s,
            stale_heartbeat_s=stale_heartbeat_s,
        )
        self.interval_s = float(interval_s)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop_event.is_set()

    def start(self) -> None:
        with self._lock:
            if self.is_running():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="spd-reconciler-daemon",
                daemon=True,
            )
            self._thread.start()
            logger.info("Lifecycle Reconciler daemon started (interval=%.1fs)", self.interval_s)

    def stop(self, timeout_s: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout_s)
            self._thread = None
            logger.info("Lifecycle Reconciler daemon stopped")

    def reconcile_now(self) -> dict[str, list[str]]:
        """Run an immediate reconciliation pass."""
        return self.reconciler.reconcile_once()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.reconciler.reconcile_once()
            except Exception as exc:
                logger.error("Error during reconciliation pass: %s", exc)
            self._stop_event.wait(timeout=self.interval_s)
