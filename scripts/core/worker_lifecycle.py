"""
scripts/core/worker_lifecycle.py
================================
Process lifecycle, atomic PID/lock sentinel file handlers, and signal traps.
"""
from __future__ import annotations

import atexit
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from ..storage_rotator import SessionDB, StorageRotator, _slug, update_project_meta
    from ..diff_calc import DiffCalculator
    from ..event_bundler import EventBundler
    from ..watcher_fs import ProjectWatcher
    from ..git_shadow import ShadowGit
    from ..watcher_proc import ProcessInspector
    from ..shell_interceptor import ExecutionTracker
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.storage_rotator import SessionDB, StorageRotator, _slug, update_project_meta
        from spd_analysis_engine.scripts.diff_calc import DiffCalculator
        from spd_analysis_engine.scripts.event_bundler import EventBundler
        from spd_analysis_engine.scripts.watcher_fs import ProjectWatcher
        from spd_analysis_engine.scripts.git_shadow import ShadowGit
        from spd_analysis_engine.scripts.watcher_proc import ProcessInspector
        from spd_analysis_engine.scripts.shell_interceptor import ExecutionTracker
    except (ImportError, ModuleNotFoundError):
        from scripts.storage_rotator import SessionDB, StorageRotator, _slug, update_project_meta
        from scripts.diff_calc import DiffCalculator
        from scripts.event_bundler import EventBundler
        from scripts.watcher_fs import ProjectWatcher
        from scripts.git_shadow import ShadowGit
        from scripts.watcher_proc import ProcessInspector
        from scripts.shell_interceptor import ExecutionTracker

logger = logging.getLogger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))


def _now_ist_iso() -> str:
    return datetime.now(_IST).strftime("%Y-%m-%dT%H:%M:%S+05:30")


_utcnow_iso = _now_ist_iso


@dataclass
class WorkerState:
    """State container for a running project worker."""

    session_db: Any = None
    session_id: int | None = None
    pid_file: Path | None = None
    lock_file: Path | None = None
    status_file: Path | None = None
    start_time: float = 0.0
    shutdown_requested: bool = False
    project_name: str = "<unknown>"
    target_path_str: str = ""
    engine_root_str: str = ""
    diff_calc: Any = None
    event_bundler: Any = None
    project_watcher: Any = None
    patches_dir: Path | None = None
    shadow_git: Any = None
    proc_inspector: Any = None
    exec_tracker: Any = None
    powers: dict[str, Any] = field(default_factory=dict)


# Global singleton instance used by worker signal handlers
worker_state = WorkerState()


# ---------------------------------------------------------------------------
# PID file & Session lock
# ---------------------------------------------------------------------------

def _write_pid_file(pid_path: Path, state: WorkerState = worker_state) -> None:
    """Write an atomic JSON PID file containing metadata for Supervisor reconciliation."""
    payload = {
        "pid":         os.getpid(),
        "project":     state.project_name,
        "target_path": state.target_path_str,
        "engine_root": state.engine_root_str,
        "started_at":  _utcnow_iso(),
        "powers":      state.powers,
    }
    tmp = pid_path.with_name("worker.pid.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(pid_path)
    logger.info("PID file written: %s", pid_path)


def _remove_pid_file(state: WorkerState = worker_state) -> None:
    """Remove the PID file on clean exit."""
    if state.pid_file and state.pid_file.exists():
        try:
            state.pid_file.unlink()
            logger.debug("PID file removed: %s", state.pid_file)
        except OSError as exc:
            logger.warning("Could not remove PID file: %s", exc)


def _write_lock_file(lock_path: Path, state: WorkerState = worker_state) -> None:
    """Write sentinel session.lock containing active PID and start ISO timestamp."""
    payload = {
        "pid": os.getpid(),
        "start_time": _now_ist_iso(),
        "project": state.project_name,
        "target_path": state.target_path_str,
    }
    tmp = lock_path.with_suffix(".lock.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(lock_path)
    logger.info("Session lock sentinel written: %s", lock_path)


def _remove_lock_file(state: WorkerState = worker_state) -> None:
    """Remove sentinel lock file on clean exit."""
    if state.lock_file and state.lock_file.exists():
        try:
            state.lock_file.unlink()
            logger.debug("Session lock removed: %s", state.lock_file)
        except OSError as exc:
            logger.warning("Could not remove session lock: %s", exc)


# ---------------------------------------------------------------------------
# Status file
# ---------------------------------------------------------------------------

def _write_status_file(state: WorkerState = worker_state) -> None:
    """Atomically update worker.status.json with the current heartbeat data."""
    if state.status_file is None:
        return

    debounce_info = state.event_bundler.get_debounce_status() if state.event_bundler else {"active": False}
    recent_actions = state.event_bundler.get_recent_actions(40) if state.event_bundler else []

    payload = {
        "pid":            os.getpid(),
        "project":        state.project_name,
        "target_path":    state.target_path_str,
        "session_id":     state.session_id,
        "uptime_s":       round(time.monotonic() - state.start_time, 2) if state.start_time else 0.0,
        "ts":             _utcnow_iso(),
        "powers":         state.powers,
        "debounce":       debounce_info,
        "recent_actions": recent_actions,
    }
    tmp = state.status_file.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(state.status_file)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not write status file: %s", exc)


def _remove_status_file(state: WorkerState = worker_state) -> None:
    """Remove the status file on clean exit."""
    if state.status_file and state.status_file.exists():
        try:
            state.status_file.unlink()
            logger.debug("Status file removed: %s", state.status_file)
        except OSError as exc:
            logger.warning("Could not remove status file: %s", exc)


# ---------------------------------------------------------------------------
# JSON heartbeat emitter
# ---------------------------------------------------------------------------

def _emit_json(event: str, extra: dict | None = None, state: WorkerState = worker_state) -> None:
    """Write a single JSON line to stdout (redirected to worker.log)."""
    payload: dict = {
        "event":    event,
        "pid":      os.getpid(),
        "project":  state.project_name,
        "uptime_s": round(time.monotonic() - state.start_time, 2) if state.start_time else 0.0,
        "ts":       _utcnow_iso(),
    }
    if extra:
        payload.update(extra)
    line = json.dumps(payload)
    try:
        print(line, flush=True)
    except (OSError, BrokenPipeError):
        try:
            print(line, file=sys.stderr, flush=True)
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Shutdown / signal handling
# ---------------------------------------------------------------------------

def _clean_shutdown(signum: int | None = None, frame: object = None, state: WorkerState = worker_state) -> None:
    """Gracefully terminate worker, flushing watcher, bundler, and DB."""
    if state.shutdown_requested:
        return
    state.shutdown_requested = True

    sig_name = signal.Signals(signum).name if signum else "ATEXIT"
    logger.info("Shutdown requested via %s — flushing state…", sig_name)
    _emit_json("shutdown", {"reason": sig_name}, state=state)

    if state.project_watcher is not None:
        try:
            state.project_watcher.stop()
        except Exception as exc:
            logger.debug("Error stopping project watcher: %s", exc)
        state.project_watcher = None

    if state.proc_inspector is not None:
        try:
            state.proc_inspector.stop()
        except Exception as exc:
            logger.debug("Error stopping ProcessInspector: %s", exc)
        state.proc_inspector = None

    if state.exec_tracker is not None:
        try:
            state.exec_tracker.stop()
        except Exception as exc:
            logger.debug("Error stopping ExecutionTracker: %s", exc)
        state.exec_tracker = None

    if state.event_bundler is not None:
        try:
            state.event_bundler.flush()
            state.event_bundler.stop()
        except Exception as exc:
            logger.debug("Error stopping event bundler: %s", exc)
        state.event_bundler = None

    if state.session_db is not None and state.session_id is not None:
        try:
            cur = state.session_db.conn.cursor()
            cur.execute("SELECT COUNT(*) FROM events WHERE session_id = ?", (state.session_id,))
            c_row = cur.fetchone()
            total_events = c_row[0] if c_row else 0
            if total_events == 0:
                cur.execute("DELETE FROM sessions WHERE id = ?", (state.session_id,))
                state.session_db.conn.commit()
                logger.info("Pruned empty zero-event session #%d on worker shutdown", state.session_id)
            else:
                state.session_db.close_session(state.session_id, status="STOPPED")
        except Exception as exc:  # noqa: BLE001
            logger.error("Error closing/pruning session row: %s", exc)

    if state.session_db is not None:
        try:
            state.session_db.close()
        except Exception as exc:  # noqa: BLE001
            logger.error("Error closing SessionDB: %s", exc)
        state.session_db = None

    _remove_lock_file(state)
    _remove_status_file(state)
    _remove_pid_file(state)

    try:
        update_project_meta(state.engine_root_str, state.project_name, clean_exit="true", status="stopped")
    except Exception as exc:
        logger.debug("Could not update project.meta clean_exit: %s", exc)

    logger.info("Worker exiting cleanly.")
    sys.exit(0)


def _crash_handler(state: WorkerState = worker_state) -> None:
    """atexit fallback for unexpected termination."""
    if state.shutdown_requested:
        return

    logger.warning("Unexpected exit — marking session as CRASHED")

    if state.session_db is not None and state.session_id is not None:
        try:
            cur = state.session_db.conn.cursor()
            cur.execute("SELECT COUNT(*) FROM events WHERE session_id = ?", (state.session_id,))
            c_row = cur.fetchone()
            total_events = c_row[0] if c_row else 0
            if total_events == 0:
                cur.execute("DELETE FROM sessions WHERE id = ?", (state.session_id,))
                state.session_db.conn.commit()
            else:
                state.session_db.close_session(state.session_id, status="CRASHED")
        except Exception:  # noqa: BLE001
            pass

    if state.session_db is not None:
        try:
            state.session_db.close()
        except Exception:  # noqa: BLE001
            pass

    _remove_status_file(state)
    _remove_pid_file(state)


def _install_signal_handlers(state: WorkerState = worker_state) -> None:
    """Register OS signal handlers for graceful shutdown."""
    def _sig_handler(sig, frame):
        _clean_shutdown(sig, frame, state=state)

    signal.signal(signal.SIGINT, _sig_handler)

    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _sig_handler)

    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _sig_handler)  # type: ignore[attr-defined]


def _unhandled_exception_handler(exc_type, exc_value, exc_traceback) -> None:
    """Log any uncaught exception before process termination."""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    logger.critical("UNHANDLED EXCEPTION in worker:", exc_info=(exc_type, exc_value, exc_traceback))


# ---------------------------------------------------------------------------
# Component wiring
# ---------------------------------------------------------------------------

def wire_worker(args: Any, state: WorkerState = worker_state) -> None:
    """Wire and initialize all worker sub-components."""
    from .worker_bundle import _handle_event_bundle

    state.project_name = args.name
    state.start_time = time.monotonic()
    state.powers = {
        "track_edits": True,
        "track_reads": bool(args.track_reads),
        "track_exec": bool(args.track_exec),
        "shadow_git": bool(args.shadow_git),
        "ide_profile": args.ide_profile,
        "ide_custom_marker": args.ide_custom_marker or "",
    }

    engine_root = Path(args.engine_root).resolve()
    target_path = Path(args.path).resolve()
    state.target_path_str = str(target_path)
    state.engine_root_str = str(engine_root)

    logger.info("Worker starting | project=%s | target=%s | engine_root=%s", args.name, target_path, engine_root)

    if not target_path.exists():
        if args.scaffold:
            logger.info("Scaffolding target directory: %s", target_path)
            target_path.mkdir(parents=True, exist_ok=True)
        else:
            logger.error("Target path does not exist: %s", target_path)
            sys.exit(1)

    if args.git_init_primary and ShadowGit.init_primary_repo(target_path):
        logger.info("Primary git repo initialized in %s", target_path)

    rotator = StorageRotator(engine_root)
    proj_dir = rotator.init_project(args.name, str(target_path))
    db_path = proj_dir / "session.db"

    state.session_db = SessionDB(db_path)
    state.session_id = state.session_db.create_session(args.name, str(target_path))
    logger.info("Session created | id=%s | db=%s", state.session_id, db_path)

    state.pid_file = proj_dir / "worker.pid"
    _write_pid_file(state.pid_file, state=state)

    state.lock_file = proj_dir / "session.lock"
    _write_lock_file(state.lock_file, state=state)

    state.status_file = proj_dir / "worker.status.json"

    _install_signal_handlers(state=state)
    atexit.register(lambda: _crash_handler(state=state))

    state.patches_dir = proj_dir / "patches"
    state.patches_dir.mkdir(parents=True, exist_ok=True)

    baseline_dir = proj_dir / "baseline"
    baseline_dir.mkdir(parents=True, exist_ok=True)

    state.diff_calc = DiffCalculator(baseline_dir=baseline_dir, target_dir=target_path)
    state.diff_calc.capture_baseline(target_path)

    def _on_fs_activity() -> None:
        if state.exec_tracker is not None:
            state.exec_tracker.trigger_fast_poll(args.debounce + 1.0)

    state.event_bundler = EventBundler(
        callback=lambda b: _handle_event_bundle(b, state=state),
        quiet_period=args.debounce,
        on_activity=_on_fs_activity,
    )
    state.project_watcher = ProjectWatcher(target_path=target_path, bundler=state.event_bundler)
    state.project_watcher.start()

    if args.shadow_git:
        shadow_repo_dir = proj_dir / "shadow_git"
        state.shadow_git = ShadowGit(shadow_dir=shadow_repo_dir, target_dir=target_path)
        state.shadow_git.init_repo()

    if args.track_reads:
        def _on_file_read(read_info: dict[str, Any]) -> None:
            rel_path = read_info.get("file_path", "")
            proc_name = read_info.get("process_name", "unknown")
            pid = read_info.get("pid", 0)
            if state.event_bundler and rel_path:
                state.event_bundler.record_action("READ", rel_path, details=f"Inspected by {proc_name} (PID {pid})")
                _write_status_file(state=state)
            if state.session_db is not None and state.session_id is not None:
                try:
                    state.session_db.record_event(
                        session_id=state.session_id,
                        event_type="READ",
                        first_file=rel_path,
                        summary=f"File inspected by {proc_name} (PID {pid})",
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Failed to record READ event: %s", exc)

        state.proc_inspector = ProcessInspector(
            target_path=target_path,
            on_file_read=_on_file_read,
            ide_profile=args.ide_profile,
            custom_marker=args.ide_custom_marker,
        )
        state.proc_inspector.start()

    if args.track_exec:
        def _on_command_executed(cmd_info: dict[str, Any]) -> None:
            cmd_str = cmd_info.get("command", "")
            exit_code = cmd_info.get("exit_code", 0)
            if state.event_bundler and cmd_str:
                state.event_bundler.record_action("EXEC", cmd_str, details=f"Exit code: {exit_code}")
                _write_status_file(state=state)
            logger.info("[EXEC_CAPTURED] %s (exit=%s)", cmd_str, exit_code)

        state.exec_tracker = ExecutionTracker(
            target_dir=target_path,
            db=state.session_db,
            session_id=state.session_id,
            idle_interval=1.2,
            fast_interval=0.18,
            is_active_callback=lambda: bool(state.event_bundler and state.event_bundler.get_debounce_status().get("active")),
            on_command=_on_command_executed,
            on_read=_on_file_read if args.track_reads else None,
            ide_profile=args.ide_profile,
            custom_marker=args.ide_custom_marker,
        )
        state.exec_tracker.start()

    _emit_json(
        "startup",
        {
            "target_path": str(target_path),
            "session_id":  state.session_id,
            "db_path":     str(db_path),
            "powers":      state.powers,
        },
        state=state,
    )
    _write_status_file(state=state)
