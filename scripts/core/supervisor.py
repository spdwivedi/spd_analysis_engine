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

try:
    from .worker_entry import _WorkerEntry, _PSUTIL_AVAILABLE
    from .spawner import (
        spawn_detached_worker,
        send_stop,
        send_stop_pid,
        kill_pid,
        wait_for_pid_exit,
        _STOP_TIMEOUT_S,
        _BOOT_TIMEOUT_S,
    )
    from .reaper import ReaperDaemon, _REAPER_INTERVAL_S
    from .worker_helpers import (
        verify_worker_pid,
        mark_stale_pid,
        read_status_file,
        prune_phantom_sessions,
        stop_worker_process,
        wait_for_worker_pid,
        discover_active_workers,
    )
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.core.worker_entry import _WorkerEntry, _PSUTIL_AVAILABLE
        from spd_analysis_engine.scripts.core.spawner import (
            spawn_detached_worker,
            send_stop,
            send_stop_pid,
            kill_pid,
            wait_for_pid_exit,
            _STOP_TIMEOUT_S,
            _BOOT_TIMEOUT_S,
        )
        from spd_analysis_engine.scripts.core.reaper import ReaperDaemon, _REAPER_INTERVAL_S
        from spd_analysis_engine.scripts.core.worker_helpers import (
            verify_worker_pid,
            mark_stale_pid,
            read_status_file,
            prune_phantom_sessions,
            stop_worker_process,
            wait_for_worker_pid,
            discover_active_workers,
        )
    except (ImportError, ModuleNotFoundError):
        from scripts.core.worker_entry import _WorkerEntry, _PSUTIL_AVAILABLE
        from scripts.core.spawner import (
            spawn_detached_worker,
            send_stop,
            send_stop_pid,
            kill_pid,
            wait_for_pid_exit,
            _STOP_TIMEOUT_S,
            _BOOT_TIMEOUT_S,
        )
        from scripts.core.reaper import ReaperDaemon, _REAPER_INTERVAL_S
        from scripts.core.worker_helpers import (
            verify_worker_pid,
            mark_stale_pid,
            read_status_file,
            prune_phantom_sessions,
            stop_worker_process,
            wait_for_worker_pid,
            discover_active_workers,
        )

if _PSUTIL_AVAILABLE:
    import psutil

try:
    from ..storage_rotator import StorageRotator, _slug, update_project_meta, read_project_meta
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.storage_rotator import StorageRotator, _slug, update_project_meta, read_project_meta
    except (ImportError, ModuleNotFoundError):
        from scripts.storage_rotator import StorageRotator, _slug, update_project_meta, read_project_meta

logger = logging.getLogger(__name__)


class Supervisor:
    """
    Multi-worker process supervisor for the SPD Analysis Engine.

    State is persisted across CLI invocations via ``worker.pid`` files in
    ``current/<slug>/``.  Every public method calls
    ``_discover_active_workers()`` first so that separate ``python start.py``
    invocations share a consistent view of running workers.
    """

    def __init__(self, engine_root: Path | str | None = None) -> None:
        if engine_root is None:
            # scripts/core/supervisor.py -> parent = scripts -> parent.parent = spd_analysis_engine
            engine_root = Path(__file__).resolve().parent.parent.parent
        self._root = Path(engine_root).resolve()
        self._rotator = StorageRotator(self._root)

        # Registry keyed by project name (not slug)
        self._registry: dict[str, _WorkerEntry] = {}

        self._worker_script = Path(__file__).resolve().parent.parent / "project_worker.py"

        # Background reaper daemon
        self._reaper = ReaperDaemon(self._registry, self._rotator, interval=_REAPER_INTERVAL_S)
        self._reaper_stop = self._reaper._stop_event
        self._reaper.start()

        # Reconcile with running workers on startup
        self._discover_active_workers()

        logger.info(
            "Supervisor ready | root=%s | tracked_workers=%d",
            self._root,
            len(self._registry),
        )

    # ==================================================================
    # Signal and process helper aliases for backward compatibility
    # ==================================================================

    _send_stop = staticmethod(send_stop)
    _send_stop_pid = staticmethod(send_stop_pid)
    _kill_pid = staticmethod(kill_pid)
    _wait_for_pid_exit = staticmethod(wait_for_pid_exit)

    def _reaper_loop(self) -> None:
        """Alias to ReaperDaemon execution loop."""
        self._reaper._run()

    # ==================================================================
    # PID-file reconciliation
    # ==================================================================

    def _discover_active_workers(self) -> None:
        """Scan current/ for live worker.pid files and update _registry."""
        discover_active_workers(self._root, self._registry, _WorkerEntry)


    _verify_worker_pid = staticmethod(verify_worker_pid)
    _mark_stale_pid = staticmethod(mark_stale_pid)
    _read_status_file = staticmethod(read_status_file)


    # ==================================================================
    # Public API
    # ==================================================================

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
        self._discover_active_workers()

        if name in self._registry and self._registry[name].is_alive():
            entry = self._registry[name]
            raise ValueError(
                f"A worker for project '{name}' is already running "
                f"(PID {entry.pid}).  "
                f"Run 'python start.py stop --project {name}' first."
            )

        if not self._worker_script.exists():
            raise FileNotFoundError(
                f"Worker script not found: {self._worker_script}"
            )

        slug = _slug(name)
        proj_dir = self._root / "current" / slug
        proj_dir.mkdir(parents=True, exist_ok=True)
        log_path = proj_dir / "worker.log"

        if debounce is None or debounce == 3.5:
            saved_meta = read_project_meta(self._root, slug)
            saved_db = saved_meta.get("debounce_window") or saved_meta.get("debounce")
            if saved_db:
                try:
                    debounce = float(saved_db)
                except (ValueError, TypeError):
                    pass

        powers_dict = {
            "track_edits": True,
            "track_reads": track_reads,
            "track_exec": track_exec,
            "shadow_git": shadow_git,
        }

        update_project_meta(
            self._root,
            slug,
            target_path=str(Path(path).resolve()),
            debounce_window=str(debounce),
            powers=powers_dict,
            ide_profile=ide_profile,
            ide_custom_marker=ide_custom_marker or "",
        )

        with open(log_path, "a", encoding="utf-8", buffering=1) as lf:
            lf.write(
                f"\n{'=' * 72}\n"
                f"  SPD Worker Session Start\n"
                f"  Project : {name}\n"
                f"  Target  : {path}\n"
                f"  Started : {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
                f"{'=' * 72}\n\n"
            )

        py_exe = sys.executable
        if sys.platform == "win32":
            cand = Path(sys.executable).parent / "pythonw.exe"
            if cand.exists():
                py_exe = str(cand)

        logger.info("Spawning detached worker | project=%s | log=%s", name, log_path)

        proc, spawned_pid = spawn_detached_worker(
            py_exe=py_exe,
            worker_script=self._worker_script,
            name=name,
            path=path,
            engine_root=self._root,
            log_path=log_path,
            scaffold=scaffold,
            debounce=debounce,
            track_reads=track_reads,
            track_exec=track_exec,
            shadow_git=shadow_git,
            git_init_primary=git_init_primary,
            ide_profile=ide_profile,
            ide_custom_marker=ide_custom_marker,
        )

        pid_file = proj_dir / "worker.pid"
        real_pid = wait_for_worker_pid(pid_file, proc, deadline_s=3.0) or spawned_pid or (proc.pid if proc is not None else 0)


        if not pid_file.exists() and real_pid == 0:
            logger.warning(
                "worker.pid not found within 3.0s for '%s' — worker may have failed (log: %s)",
                name,
                log_path,
            )

        entry = _WorkerEntry(
            name=name,
            target_path=path,
            pid=real_pid,
            proc=proc,
            log_path=log_path,
        )
        self._registry[name] = entry

        logger.info("Worker started | project=%s | PID=%s", name, real_pid)

        status_data = self._read_status_file(proj_dir)
        if status_data.get("session_id") is not None:
            entry.session_id = int(status_data["session_id"])

        return {
            "status":      "started",
            "pid":         real_pid,
            "project":     name,
            "target_path": path,
            "log_path":    str(log_path),
        }

    def stop_project(self, name: str) -> dict[str, Any]:
        """Gracefully terminate worker for name and archive its session."""
        self._discover_active_workers()

        if name not in self._registry:
            slug = _slug(name)
            pid_file = self._root / "current" / slug / "worker.pid"
            if pid_file.exists():
                try:
                    data = json.loads(pid_file.read_text(encoding="utf-8"))
                    pid = int(data["pid"])
                    self._registry[name] = _WorkerEntry(
                        name=name,
                        target_path=str(data.get("target_path", "")),
                        pid=pid,
                        log_path=self._root / "current" / slug / "worker.log",
                    )
                except Exception:
                    pass

        if name not in self._registry:
            raise KeyError(
                f"No running worker found for project '{name}'.  "
                f"Run 'python start.py list' to see known projects."
            )

        entry = self._registry[name]
        slug = _slug(name)
        pid = entry.pid
        exit_code: int | None = None

        if entry.is_alive():
            logger.info("Sending stop signal to worker '%s' (PID %s)", name, pid)
            exit_code = stop_worker_process(
                entry,
                pid,
                name,
                stop_timeout_s=_STOP_TIMEOUT_S,
                send_stop_fn=self._send_stop,
                send_stop_pid_fn=self._send_stop_pid,
                wait_for_pid_fn=self._wait_for_pid_exit,
            )
        else:
            logger.warning("Worker '%s' (PID %s) already exited", name, pid)

        # Ensure Windows file handles on SQLite WAL files (session.db, -wal, -shm) are released
        time.sleep(1.2)

        pid_file = self._root / "current" / slug / "worker.pid"
        if pid_file.exists():
            try:
                pid_file.unlink()
                logger.debug("Removed leftover worker.pid for '%s'", name)
            except OSError as exc:
                logger.debug("Could not remove worker.pid: %s", exc)

        status_file = self._root / "current" / slug / "worker.status.json"
        if status_file.exists():
            try:
                status_file.unlink()
            except OSError:
                pass

        # Prune zero-event / zero-burst phantom sessions before archiving
        db_path = self._root / "current" / slug / "session.db"
        prune_phantom_sessions(db_path, slug)

        try:
            self._rotator.archive_project(name)
            logger.info("Session archived for project '%s'", name)
        except Exception as exc:  # noqa: BLE001
            logger.error("StorageRotator.archive_project failed for '%s': %s", name, exc)
            raise

        del self._registry[name]

        return {
            "status":    "stopped",
            "project":   name,
            "pid":       pid,
            "exit_code": exit_code or 0,
        }

    def get_status(self, name: str | None = None) -> list[dict[str, Any]]:
        """Return live status for one or all known workers."""
        self._discover_active_workers()

        if name is not None:
            if name not in self._registry:
                return []
            targets = [self._registry[name]]
        else:
            targets = list(self._registry.values())

        results: list[dict[str, Any]] = []

        for entry in targets:
            slug = _slug(entry.name)
            proj_dir = self._root / "current" / slug
            status_data = self._read_status_file(proj_dir)

            with entry._lock:
                if status_data.get("session_id") is not None:
                    entry.session_id = int(status_data["session_id"])

            last_hb_s: float | None = None
            if "ts" in status_data:
                try:
                    ts = datetime.strptime(status_data["ts"], "%Y-%m-%dT%H:%M:%SZ")
                    ts = ts.replace(tzinfo=timezone.utc)
                    last_hb_s = round(
                        (datetime.now(timezone.utc) - ts).total_seconds(), 2
                    )
                except Exception:
                    pass

            saved_meta = read_project_meta(self._root, slug)
            db_val = saved_meta.get("debounce_window") or saved_meta.get("debounce")
            try:
                debounce_window = float(db_val) if db_val else 3.5
            except (ValueError, TypeError):
                debounce_window = 3.5
            hb_grace_threshold = max(debounce_window, 12.0)

            is_proc_alive = entry.is_alive()
            verified = self._verify_worker_pid(entry.pid, entry.name)
            if verified is True:
                is_proc_alive = True
            elif verified is False:
                is_proc_alive = False

            worker_alive = bool(is_proc_alive and (last_hb_s is None or last_hb_s <= hb_grace_threshold or verified is True))

            row: dict[str, Any] = {
                "project":          entry.name,
                "pid":              entry.pid,
                "target_path":      entry.target_path,
                "uptime_s":         round(entry.uptime_s(), 2),
                "alive":            worker_alive,
                "status":           "ACTIVE" if worker_alive else "STOPPED",
                "exit_code":        entry.exit_code,
                "session_id":       entry.session_id,
                "last_heartbeat_s": last_hb_s,
                "reconciled":       entry.reconciled,
                "log_path":         str(entry.log_path) if entry.log_path else None,
                "memory_mb":        None,
                "cpu_percent":      None,
                "powers":           status_data.get("powers", {}),
                "debounce":         status_data.get("debounce", {"active": False}),
                "recent_actions":   status_data.get("recent_actions", []),
            }

            if _PSUTIL_AVAILABLE and entry.is_alive():
                try:
                    ps = psutil.Process(entry.pid)
                    row["memory_mb"] = round(ps.memory_info().rss / 1_048_576, 2)
                    row["cpu_percent"] = ps.cpu_percent(interval=0.1)
                except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
                    logger.debug("psutil error for PID %s: %s", entry.pid, exc)

            results.append(row)

        return results

    def list_projects(self) -> dict[str, Any]:
        """Return a combined view of projects across all storage tiers."""
        self._discover_active_workers()

        running = {
            entry.name: entry.pid
            for entry in self._registry.values()
            if entry.is_alive()
        }
        on_disk = self._rotator.list_all()
        return {"running": running, "on_disk": on_disk}

    def stop_all(self) -> list[dict[str, Any]]:
        """Stop every tracked worker."""
        self._discover_active_workers()
        names = list(self._registry.keys())
        results: list[dict[str, Any]] = []
        for name in names:
            try:
                results.append(self.stop_project(name))
            except Exception as exc:  # noqa: BLE001
                logger.error("Error stopping project '%s': %s", name, exc)
                results.append({"status": "error", "project": name, "error": str(exc)})
        return results

    def shutdown(self) -> None:
        """Cleanly shut down Supervisor: stop all workers, stop reaper."""
        logger.info("Supervisor shutdown — stopping all workers")
        self.stop_all()
        self._reaper.stop(timeout=5.0)
        logger.info("Supervisor shutdown complete")
