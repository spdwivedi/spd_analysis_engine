"""
scripts/core/supervisor.py
==========================
Multi-worker process supervisor and cross-CLI-invocation process registry
for the SPD Analysis Engine.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import uuid

try:
    from .worker_entry import _WorkerEntry, _PSUTIL_AVAILABLE
    from .spawner import (spawn_detached_worker, send_stop, send_stop_pid, kill_pid, wait_for_pid_exit,
                          _STOP_TIMEOUT_S, _BOOT_TIMEOUT_S, find_worker_pid_by_worker_id)
    from .reaper import ReaperDaemon, _REAPER_INTERVAL_S
    from .worker_helpers import (verify_worker_pid, mark_stale_pid, read_status_file, prune_phantom_sessions,
                                 stop_worker_process, wait_for_worker_pid, discover_active_workers, format_worker_status_row)
    from ..storage_rotator import StorageRotator, SessionDB, _slug, update_project_meta, read_project_meta
    from .process_sweeper import sweep_project_processes
    from .project_config import DEFAULT_DEBOUNCE_S
    from .time_utils import utc_now_iso
except (ImportError, ModuleNotFoundError):
    from spd_analysis_engine.scripts.core.worker_entry import _WorkerEntry, _PSUTIL_AVAILABLE
    from spd_analysis_engine.scripts.core.spawner import (spawn_detached_worker, send_stop, send_stop_pid, kill_pid, wait_for_pid_exit,
                                      _STOP_TIMEOUT_S, _BOOT_TIMEOUT_S, find_worker_pid_by_worker_id)
    from spd_analysis_engine.scripts.core.reaper import ReaperDaemon, _REAPER_INTERVAL_S
    from spd_analysis_engine.scripts.core.worker_helpers import (verify_worker_pid, mark_stale_pid, read_status_file, prune_phantom_sessions,
                                             stop_worker_process, wait_for_worker_pid, discover_active_workers, format_worker_status_row)
    from spd_analysis_engine.scripts.storage_rotator import StorageRotator, SessionDB, _slug, update_project_meta, read_project_meta
    from spd_analysis_engine.scripts.core.time_utils import utc_now_iso
    try:
        from spd_analysis_engine.scripts.core.project_config import DEFAULT_DEBOUNCE_S
    except (ImportError, ModuleNotFoundError):
        DEFAULT_DEBOUNCE_S = 3.5
    try:
        from spd_analysis_engine.scripts.core.process_sweeper import sweep_project_processes
    except (ImportError, ModuleNotFoundError):
        def sweep_project_processes(s, t=None, f=True): return []

if _PSUTIL_AVAILABLE:
    import psutil

logger = logging.getLogger(__name__)

for _mod in ("scripts.core.supervisor", "spd_analysis_engine.scripts.core.supervisor"):
    sys.modules.setdefault(_mod, sys.modules.get(__name__))


class Supervisor:
    """Multi-worker process supervisor for the SPD Analysis Engine."""

    if not hasattr(sys, "_SPD_ENGINE_SUPERVISORS"):
        sys._SPD_ENGINE_SUPERVISORS = {}
    _instances: dict[Path, Supervisor] = sys._SPD_ENGINE_SUPERVISORS
    _lock = threading.RLock()

    @classmethod
    def _resolve_engine_root(cls, engine_root: Path | str | None) -> Path:
        return Path(engine_root).resolve() if engine_root is not None else Path(__file__).resolve().parent.parent.parent

    @classmethod
    def get_authoritative(
        cls, engine_root: Path | str | None = None, enable_reaper: bool = False, create: bool = True
    ) -> Supervisor | None:
        """Retrieve or construct the single authoritative Supervisor instance for engine_root."""
        resolved = cls._resolve_engine_root(engine_root)
        with cls._lock:
            sup = cls._instances.get(resolved)
            if sup is not None and (getattr(sup, "_is_shutdown", False) or not resolved.exists()):
                cls._instances.pop(resolved, None)
                sup = None
            if sup is not None:
                if enable_reaper and not sup.is_reaper_running():
                    sup.start_reaper()
                return sup
            return cls(resolved, enable_reaper=enable_reaper) if create else None

    @classmethod
    def reset_instances(cls) -> None:
        """Helper for test cleanup to reset the authoritative registry."""
        with cls._lock:
            for sup in list(cls._instances.values()):
                try: sup.shutdown()
                except Exception: pass
            cls._instances.clear()

    def __new__(
        cls, engine_root: Path | str | None = None, enable_reaper: bool = False, *args: Any, **kwargs: Any
    ) -> Supervisor:
        resolved = cls._resolve_engine_root(engine_root)
        with cls._lock:
            existing = cls._instances.get(resolved)
            if existing is not None and not getattr(existing, "_is_shutdown", False) and resolved.exists():
                return existing
            instance = super().__new__(cls)
            cls._instances[resolved] = instance
            instance._initialized = False
            return instance

    def __init__(
        self,
        engine_root: Path | str | None = None,
        enable_reaper: bool = False,
    ) -> None:
        resolved = self._resolve_engine_root(engine_root)
        with self._lock:
            if getattr(self, "_initialized", False):
                if enable_reaper and not self.is_reaper_running():
                    self.start_reaper()
                return
            self._initialized = True
            self._is_shutdown = False

        self._root = resolved
        self._rotator = StorageRotator(self._root)
        self._controller: Any = None

        # Registry strictly keyed by project slug
        self._registry: dict[str, _WorkerEntry] = {}
        self._worker_script = Path(__file__).resolve().parent.parent / "project_worker.py"

        # Background reaper daemon
        self._reaper = ReaperDaemon(self._registry, self._rotator, interval=_REAPER_INTERVAL_S)
        self._reaper_stop = self._reaper._stop_event

        if enable_reaper:
            self._reaper.start()
            self.controller.start_reconciler_daemon(interval_s=_REAPER_INTERVAL_S)

        # Reconcile with running workers on startup
        self._discover_active_workers()
        logger.info("Supervisor ready | root=%s | tracked_workers=%d", self._root, len(self._registry))

    @property
    def controller(self) -> Any:
        if getattr(self, "_controller", None) is None:
            from spd_analysis_engine.scripts.controller.controller import LifecycleController
            self._controller = LifecycleController(self._root)
            self._rotator.controller = self._controller
        return self._controller

    def is_reaper_running(self) -> bool:
        """Return True if background reconciler or reaper daemon thread is currently running."""
        ctrl = getattr(self, "_controller", None)
        ctrl_d = getattr(ctrl, "_reconciler_daemon", None) if ctrl is not None else None
        return (ctrl_d is not None and ctrl_d.is_running()) or self._reaper.is_running()

    def start_reaper(self, interval: float = _REAPER_INTERVAL_S) -> None:
        """Start the background reaper and reconciler daemon if not already running."""
        self._reaper._interval = interval
        self._reaper.start()
        self.controller.start_reconciler_daemon(interval_s=interval)

    def reap_dead_workers(self) -> list[str]:
        """Perform a single reconciliation pass over tracked workers."""
        return self.controller.reconcile().get("interrupted", [])

    _send_stop, _send_stop_pid = staticmethod(send_stop), staticmethod(send_stop_pid)
    _kill_pid, _wait_for_pid_exit = staticmethod(kill_pid), staticmethod(wait_for_pid_exit)
    _verify_worker_pid, _mark_stale_pid = staticmethod(verify_worker_pid), staticmethod(mark_stale_pid)
    _read_status_file = staticmethod(read_status_file)

    def _reaper_loop(self) -> None:
        """Alias to ReaperDaemon execution loop."""
        self._reaper._run()

    def _discover_active_workers(self) -> None:
        """Scan current/ for live worker.pid files and update _registry."""
        discover_active_workers(self._root, self._registry, _WorkerEntry)

    def start_project(
        self,
        name: str,
        path: str,
        scaffold: bool = True,
        debounce: float = 3.5,
        track_reads: bool = True,
        track_exec: bool = True,
        shadow_git: bool = True,
        git_init_primary: bool = False,
        ide_profile: str = "antigravity",
        ide_custom_marker: str | None = None,
    ) -> dict[str, Any]:
        """Spawn a detached worker subprocess for name."""
        norm_req_path = str(Path(path).resolve()).lower()
        on_disk = self._rotator.list_all()
        is_new_project = True
        for disk_slug, disk_info in on_disk.items():
            raw_target = str(disk_info.get("target_path", "")).strip()
            if not raw_target:
                raw_target = str(read_project_meta(self._root, disk_slug).get("target_path", "")).strip()
            disk_target = str(Path(raw_target).resolve()).lower() if raw_target else ""
            if disk_target and disk_target == norm_req_path:
                name = disk_slug
                is_new_project = False
                break
        slug = _slug(name)
        self._discover_active_workers()

        if slug in self._registry and self._registry[slug].is_alive():
            entry = self._registry[slug]
            raise ValueError(
                f"A worker for project '{slug}' is already running "
                f"(PID {entry.pid}).  "
                f"Run 'python start.py stop --project {slug}' first."
            )

        if not self._worker_script.exists():
            raise FileNotFoundError(
                f"Worker script not found: {self._worker_script}"
            )

        sweep_project_processes(slug, path)

        proj_dir = self._root / "current" / slug
        proj_dir.mkdir(parents=True, exist_ok=True)
        log_path = proj_dir / "worker.log"

        if debounce is None or debounce == DEFAULT_DEBOUNCE_S:
            saved_meta = read_project_meta(self._root, slug)
            saved_db = saved_meta.get("debounce_window") or saved_meta.get("debounce")
            if saved_db:
                try:
                    debounce = float(saved_db)
                except (ValueError, TypeError):
                    pass

        powers_dict = {"track_edits": True, "track_reads": track_reads, "track_exec": track_exec, "shadow_git": shadow_git}
        update_project_meta(
            self._root, slug, target_path=str(Path(path).resolve()), debounce_window=str(debounce),
            powers=powers_dict, ide_profile=ide_profile, ide_custom_marker=ide_custom_marker or "",
        )

        with open(log_path, "a", encoding="utf-8", buffering=1) as lf:
            now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            lf.write(f"\n{'=' * 72}\n  SPD Worker Session Start\n  Project : {name}\n  Target  : {path}\n  Started : {now_iso}\n{'=' * 72}\n\n")

        py_exe = sys.executable
        if sys.platform == "win32":
            cand = Path(sys.executable).parent / "pythonw.exe"
            if cand.exists():
                py_exe = str(cand)

        worker_id = str(uuid.uuid4())
        logger.info("Spawning detached worker | project=%s | worker_id=%s | log=%s", name, worker_id, log_path)

        spawn_res = spawn_detached_worker(
            py_exe=py_exe, worker_script=self._worker_script, name=name, path=path,
            engine_root=self._root, log_path=log_path, scaffold=scaffold, debounce=debounce,
            track_reads=track_reads, track_exec=track_exec, shadow_git=shadow_git,
            git_init_primary=git_init_primary, ide_profile=ide_profile,
            ide_custom_marker=ide_custom_marker, is_new_project=is_new_project,
            worker_id=worker_id,
        )
        proc, spawned_pid = spawn_res[0], spawn_res[1]

        pid_file = proj_dir / "worker.pid"
        real_pid = wait_for_worker_pid(pid_file, proc, deadline_s=_BOOT_TIMEOUT_S, expected_worker_id=worker_id) or spawned_pid or (proc.pid if proc is not None else 0)

        # Detect duplicate successful spawn after timeout
        if real_pid == 0 or not pid_file.exists():
            dup_pid = find_worker_pid_by_worker_id(worker_id)
            if dup_pid:
                real_pid = dup_pid
                logger.info("Recovered worker PID %s for worker_id %s after timeout", real_pid, worker_id)

        canonical_target_str = str(Path(path).resolve())
        proc_create_time: float | None = None
        if _PSUTIL_AVAILABLE and real_pid:
            try: proc_create_time = psutil.Process(real_pid).create_time()
            except Exception: pass

        entry = _WorkerEntry(
            name=name,
            target_path=path,
            pid=real_pid,
            proc=proc,
            log_path=log_path,
            worker_id=worker_id,
            process_create_time=proc_create_time,
            canonical_target=canonical_target_str,
            engine_root=str(self._root),
            canonical_project_root=canonical_target_str,
        )
        self._registry[slug] = entry

        logger.info("Worker started | project=%s | PID=%s | worker_id=%s", name, real_pid, worker_id)

        status_data = self._read_status_file(proj_dir)
        if status_data.get("session_id") is not None:
            entry.session_id = int(status_data["session_id"])

        return {
            "status": "started", "pid": real_pid, "project": name, "target_path": path,
            "log_path": str(log_path), "worker_id": worker_id, "process_create_time": proc_create_time,
            "canonical_target": canonical_target_str, "engine_root": str(self._root),
            "canonical_project_root": canonical_target_str,
            "launch_strategy": getattr(spawn_res, "launch_strategy", "unknown"),
        }

    def stop_project(self, name: str) -> dict[str, Any]:
        """Gracefully terminate worker for name and archive its session."""
        self._discover_active_workers()
        slug = _slug(name)

        entry = self._registry.get(slug)
        if entry is None:
            pid_file = self._root / "current" / slug / "worker.pid"
            if pid_file.exists():
                try:
                    d = json.loads(pid_file.read_text(encoding="utf-8"))
                    entry = _WorkerEntry(
                        name=d.get("display_name") or d.get("project") or name,
                        target_path=str(d.get("root_path") or d.get("target_path", "")),
                        pid=int(d["pid"]),
                        log_path=self._root / "current" / slug / "worker.log",
                        project_id=d.get("project_id"),
                        slug=slug,
                        display_name=d.get("display_name"),
                        root_path=d.get("root_path"),
                        worker_id=d.get("worker_id"),
                        process_create_time=d.get("process_create_time"),
                        canonical_target=d.get("canonical_target"),
                        engine_root=d.get("engine_root"),
                        canonical_project_root=d.get("canonical_project_root"),
                    )
                    self._registry[slug] = entry
                except Exception:
                    pass

        if entry is None:
            raise KeyError(
                f"No running worker found for project '{name}'.  "
                f"Run 'python start.py list' to see known projects."
            )

        pid = entry.pid
        exit_code: int | None = None
        stop_res = None

        if entry.is_alive():
            logger.info("Sending stop signal to worker '%s' (PID %s)", name, pid)
            stop_res = stop_worker_process(
                entry,
                pid,
                name,
                stop_timeout_s=_STOP_TIMEOUT_S,
                send_stop_fn=self._send_stop,
                send_stop_pid_fn=self._send_stop_pid,
                wait_for_pid_fn=self._wait_for_pid_exit,
                proj_dir=self._root / "current" / slug,
            )
            exit_code = stop_res.exit_code
        else:
            logger.warning("Worker '%s' (PID %s) already exited", name, pid)

        sweep_project_processes(slug, entry.target_path)
        # Ensure Windows file handles on SQLite WAL files (session.db, -wal, -shm) are released
        time.sleep(1.2)

        for fname in ("worker.pid", "worker.status.json"):
            fpath = self._root / "current" / slug / fname
            if fpath.exists():
                try:
                    fpath.unlink()
                except OSError:
                    pass

        escalated = bool(stop_res and stop_res.escalated)
        is_clean = bool(stop_res and stop_res.clean) if stop_res else True

        db_path = self._root / "current" / slug / "session.db"
        if db_path.exists():
            if escalated or not is_clean:
                try:
                    sdb = SessionDB(db_path)
                    row = sdb.conn.cursor().execute("SELECT id FROM sessions ORDER BY id DESC LIMIT 1").fetchone()
                    if row:
                        sdb.close_session(int(row["id"]), status="INTERRUPTED")
                    sdb.close()
                except Exception:
                    pass
            else:
                prune_phantom_sessions(db_path, slug)

        if escalated or not is_clean:
            update_project_meta(self._root, slug, clean_exit="false", status="interrupted")
        else:
            lock_f = self._root / "current" / slug / "session.lock"
            if lock_f.exists():
                try: lock_f.unlink()
                except OSError: pass
            update_project_meta(self._root, slug, clean_exit="true", status="stopped")

        if (self._root / "current" / slug).exists():
            try:
                self._rotator.archive_project(slug)
                logger.info("Session archived for project '%s'", slug)
            except Exception as exc:  # noqa: BLE001
                logger.error("StorageRotator.archive_project failed for '%s': %s", slug, exc)
                raise

        self._registry.pop(slug, None)

        return {
            "status": "interrupted" if (escalated or not is_clean) else "stopped",
            "project": name, "pid": pid, "exit_code": exit_code if exit_code is not None else 0,
            "escalated": escalated, "clean": is_clean,
            "method": stop_res.method if stop_res else "already_dead",
        }

    def get_status(self, name: str | None = None) -> list[dict[str, Any]]:
        """Return live status for one or all known workers."""
        self._discover_active_workers()

        if name is not None:
            slug = _slug(name)
            entry = self._registry.get(slug) or self._registry.get(name)
            if entry is None:
                return []
            targets = [entry]
        else:
            seen_slugs = set()
            targets = []
            for entry in self._registry.values():
                s = getattr(entry, "slug", _slug(entry.name))
                if s not in seen_slugs:
                    seen_slugs.add(s)
                    targets.append(entry)

        results: list[dict[str, Any]] = []

        for entry in targets:
            slug = _slug(entry.name)
            proj_dir = self._root / "current" / slug
            status_data = self._read_status_file(proj_dir)

            with entry._lock:
                if status_data.get("session_id") is not None:
                    entry.session_id = int(status_data["session_id"])

            verified = self._verify_worker_pid(
                entry.pid, entry.name, expected_create_time=getattr(entry, "process_create_time", None),
                expected_worker_id=getattr(entry, "worker_id", None),
                expected_project_root=getattr(entry, "canonical_project_root", None),
                expected_engine_root=getattr(entry, "engine_root", None) or self._root,
            )
            results.append(format_worker_status_row(entry, status_data, self._root, verified))

        return results

    def list_projects(self) -> dict[str, Any]:
        """Return a combined view of projects across all storage tiers."""
        self._discover_active_workers()
        running = {entry.name: entry.pid for entry in self._registry.values() if entry.is_alive()}
        return {"running": running, "on_disk": self._rotator.list_all()}

    def stop_all(self) -> list[dict[str, Any]]:
        """Stop every tracked worker."""
        self._discover_active_workers()
        results: list[dict[str, Any]] = []
        for slug in list(self._registry.keys()):
            try:
                results.append(self.stop_project(slug))
            except Exception as exc:  # noqa: BLE001
                logger.error("Error stopping project '%s': %s", slug, exc)
                results.append({"status": "error", "project": slug, "error": str(exc)})
        return results

    def shutdown(self) -> None:
        """Cleanly shut down Supervisor: stop all workers, stop reaper."""
        with self._lock:
            self._is_shutdown = True
            self._instances.pop(self._root, None)

        logger.info("Supervisor shutdown — stopping all workers")
        self.stop_all()
        try:
            for s in list(self._rotator.list_all().keys()):
                sweep_project_processes(s, force_kill=True)
        except Exception:
            pass
        if getattr(self, "_controller", None) is not None:
            self._controller.stop_reconciler_daemon(timeout_s=5.0)
        self._reaper.stop(timeout=5.0)
        logger.info("Supervisor shutdown complete")
