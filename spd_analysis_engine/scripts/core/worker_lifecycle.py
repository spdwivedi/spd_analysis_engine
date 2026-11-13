"""
scripts/core/worker_lifecycle.py
================================
Process lifecycle, atomic PID/lock sentinel file handlers, and signal traps.
"""
from __future__ import annotations

import atexit, json, logging, os, signal, sys, threading, time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .faults import check_fault
from spd_analysis_engine.scripts.core.time_utils import utc_now_iso, parse_iso_timestamp

# Legacy alias: worker_bundle and status file helpers still call _utcnow_iso
_utcnow_iso = utc_now_iso

logger = logging.getLogger(__name__)

from ..storage import (
    SessionDB, StorageRotator, _slug, update_project_meta, read_project_meta
)
from ..diff_calc import DiffCalculator
from .burst_assembler import BurstAssembler
from .change_processor import ChangeProcessor
from ..watcher_fs import ProjectWatcher
from ..git_shadow import ShadowGit
from ..watcher_proc import ProcessInspector
from ..shell_interceptor import ExecutionTracker
from .worker_helpers import safe_write_status_json
from .project_config import ProjectConfig, DEFAULT_DEBOUNCE_S, DEFAULT_MAX_BURST_S


@dataclass
class WorkerState:
    """State container for a running project worker."""

    session_db: Any = None; session_id: int | None = None
    pid_file: Path | None = None; lock_file: Path | None = None; status_file: Path | None = None
    start_time: float = 0.0; shutdown_requested: bool = False
    project_name: str = "<unknown>"; target_path_str: str = ""; engine_root_str: str = ""
    diff_calc: Any = None; event_bundler: Any = None; project_watcher: Any = None
    burst_assembler: Any = None; change_processor: Any = None
    patches_dir: Path | None = None; shadow_git: Any = None
    proc_inspector: Any = None; exec_tracker: Any = None
    powers: dict[str, Any] = field(default_factory=dict); config: Any = None
    stop_event: Any = field(default_factory=threading.Event); worker_id: str = ""
    process_create_time: float = 0.0; canonical_target: str = ""; canonical_project_root: str = ""
    _in_clean_shutdown: bool = False; _testing: bool = False


# Global singleton instance used by worker signal handlers
worker_state = WorkerState()


# ---------------------------------------------------------------------------
# PID file & Session lock
# ---------------------------------------------------------------------------

def _write_pid_file(pid_path: Path, state: WorkerState = worker_state) -> None:
    """Write an atomic JSON PID file containing metadata for Supervisor reconciliation."""
    slug = _slug(state.project_name)
    display_name = str(state.project_name).strip() or slug
    root_path = state.canonical_project_root or (str(Path(state.target_path_str).resolve()) if state.target_path_str else "")
    payload = {
        "worker_id": state.worker_id, "pid": os.getpid(), "process_create_time": state.process_create_time or time.time(),
        "canonical_target": state.canonical_target or root_path, "engine_root": state.engine_root_str,
        "canonical_project_root": root_path, "project_id": f"proj_{slug}", "slug": slug, "project_slug": slug,
        "display_name": display_name, "root_path": root_path, "state": "ACTIVE", "project": display_name,
        "target_path": state.target_path_str, "started_at": _utcnow_iso(), "powers": state.powers,
    }
    tmp = pid_path.with_name("worker.pid.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(pid_path)
    logger.info("PID file written: %s (worker_id=%s)", pid_path, state.worker_id)
    check_fault("after_pid_write")


def _remove_pid_file(state: WorkerState = worker_state) -> None:
    """Remove the PID file on clean exit."""
    if state.pid_file and state.pid_file.exists():
        try: state.pid_file.unlink()
        except OSError as exc: logger.warning("Could not remove PID file: %s", exc)


def _write_lock_file(lock_path: Path, state: WorkerState = worker_state) -> None:
    """Write sentinel session.lock containing active PID and start ISO timestamp."""
    payload = {"pid": os.getpid(), "start_time": utc_now_iso(), "project": state.project_name, "target_path": state.target_path_str}
    tmp = lock_path.with_suffix(".lock.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(lock_path)
    logger.info("Session lock sentinel written: %s", lock_path)


def _remove_lock_file(state: WorkerState = worker_state) -> None:
    """Remove sentinel lock file on clean exit."""
    if state.lock_file and state.lock_file.exists():
        try: state.lock_file.unlink()
        except OSError as exc: logger.warning("Could not remove session lock: %s", exc)


def _write_status_file(state: WorkerState = worker_state) -> None:
    """Atomically update worker.status.json with the current heartbeat data."""
    if state.status_file is None:
        return
    bundler = state.burst_assembler or state.event_bundler
    debounce_info = bundler.get_debounce_status() if bundler else {"active": False}
    recent_actions = bundler.get_recent_actions(40) if bundler else []
    slug = _slug(state.project_name)
    display_name = str(state.project_name).strip() or slug
    root_path = state.canonical_project_root or (str(Path(state.target_path_str).resolve()) if state.target_path_str else "")
    payload = {
        "worker_id": state.worker_id, "pid": os.getpid(), "process_create_time": state.process_create_time or time.time(),
        "canonical_target": state.canonical_target or root_path, "canonical_project_root": root_path,
        "project_id": f"proj_{slug}", "slug": slug, "project_slug": slug, "display_name": display_name,
        "root_path": root_path, "state": "ACTIVE", "project": display_name, "target_path": state.target_path_str,
        "session_id": state.session_id, "uptime_s": round(time.monotonic() - state.start_time, 2) if state.start_time else 0.0,
        "ts": _utcnow_iso(), "powers": state.powers, "debounce": debounce_info, "recent_actions": recent_actions,
        "queue_depth": getattr(bundler, "queue_depth", 0) if bundler else 0,
        "drop_counter": getattr(bundler, "drop_counter", 0) if bundler else 0,
        "needs_reconcile": getattr(bundler, "needs_reconcile", False) if bundler else False,
        "scan_duration_s": getattr(state.proc_inspector, "last_scan_duration_s", 0.0) if state.proc_inspector else 0.0,
    }
    safe_write_status_json(state.status_file, json.dumps(payload, indent=2))


def _remove_status_file(state: WorkerState = worker_state) -> None:
    """Remove the status file on clean exit."""
    if state.status_file and state.status_file.exists():
        try: state.status_file.unlink()
        except OSError as exc: logger.warning("Could not remove status file: %s", exc)


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
    """Gracefully terminate worker from the normal execution path."""
    if frame is not None and signum is not None and isinstance(signum, int):
        logger.warning("Signal handler directly invoked _clean_shutdown; setting stop flags only.")
        state.shutdown_requested = True
        state.stop_event.set()
        return

    if getattr(state, "_in_clean_shutdown", False):
        return
    check_fault("during_shutdown")
    state._in_clean_shutdown = True
    state.shutdown_requested = True
    state.stop_event.set()

    sig_name = signal.Signals(signum).name if (signum and isinstance(signum, int)) else "NORMAL"
    logger.info("Shutdown requested via %s — flushing state…", sig_name)
    _emit_json("shutdown", {"reason": sig_name}, state=state)

    for comp, name in [(state.project_watcher, "project watcher"), (state.proc_inspector, "ProcessInspector"), (state.exec_tracker, "ExecutionTracker")]:
        if comp is not None:
            try:
                comp.stop()
            except Exception as exc:
                logger.debug("Error stopping %s: %s", name, exc)
    state.project_watcher = state.proc_inspector = state.exec_tracker = None

    if state.burst_assembler is not None:
        try:
            state.burst_assembler.stop(drain=True)
        except Exception as exc:
            logger.debug("Error stopping burst assembler: %s", exc)
        state.burst_assembler = None

    if state.change_processor is not None:
        try:
            state.change_processor.stop(drain=True)
        except Exception as exc:
            logger.debug("Error stopping change processor: %s", exc)
        state.change_processor = None

    if state.event_bundler is not None:
        try:
            if hasattr(state.event_bundler, "stop"):
                state.event_bundler.stop()
        except Exception as exc:
            logger.debug("Error stopping event bundler: %s", exc)
        state.event_bundler = None

    if state.session_db is not None and state.session_id is not None:
        try:
            cur = state.session_db.conn.cursor()
            cur.execute("SELECT COUNT(*) FROM events WHERE session_id = ?", (state.session_id,))
            c_row = cur.fetchone()
            if (c_row[0] if c_row else 0) == 0:
                cur.execute("DELETE FROM sessions WHERE id = ?", (state.session_id,))
                state.session_db.conn.commit()
            else:
                state.session_db.close_session(state.session_id, status="STOPPED")
        except Exception as exc:  # noqa: BLE001
            logger.error("Error closing/pruning session row: %s", exc)

    if state.session_db is not None:
        try:
            state.session_db.close()
        except Exception as exc: logger.error("Error closing SessionDB: %s", exc)
        state.session_db = None

    _remove_lock_file(state)
    _remove_status_file(state)
    _remove_pid_file(state)

    try:
        update_project_meta(state.engine_root_str, state.project_name, clean_exit="true", status="stopped")
    except Exception as exc:
        logger.debug("Could not update project.meta clean_exit: %s", exc)

    logger.info("Worker exiting cleanly.")
    if not getattr(state, "_testing", False):
        sys.exit(0)


def _crash_handler(state: WorkerState = worker_state) -> None:
    """atexit fallback for unexpected termination."""
    if getattr(state, "_in_clean_shutdown", False):
        return

    logger.warning("Unexpected exit — marking session as INTERRUPTED")

    if state.session_db is not None:
        try:
            if state.session_id is not None:
                cur = state.session_db.conn.cursor()
                cur.execute("SELECT COUNT(*) FROM events WHERE session_id = ?", (state.session_id,))
                c_row = cur.fetchone()
                if (c_row[0] if c_row else 0) == 0:
                    cur.execute("DELETE FROM sessions WHERE id = ?", (state.session_id,))
                    state.session_db.conn.commit()
                else:
                    state.session_db.close_session(state.session_id, status="INTERRUPTED")
            state.session_db.close()
        except Exception:  # noqa: BLE001
            pass

    _remove_status_file(state)
    _remove_pid_file(state)


def _install_signal_handlers(state: WorkerState = worker_state) -> None:
    """Register OS signal handlers that ONLY set the shutdown flag/event."""
    def _sig_handler(sig: int, frame: Any) -> None:
        state.shutdown_requested = True
        state.stop_event.set()

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

def _sync_bundler(state: WorkerState, cfg: Any) -> None:
    bundler = state.burst_assembler or state.event_bundler
    if bundler is not None:
        bundler.quiet_period = cfg.debounce_s
        bundler.configured_quiet_period = cfg.debounce_s
        bundler.max_burst_s = cfg.max_burst_s


def wire_worker(args: Any, state: WorkerState = worker_state) -> None:
    """Wire and initialize all worker sub-components."""
    check_fault("during_starting")
    from .worker_bundle import _handle_event_bundle

    import uuid
    state.project_name = args.name
    state.start_time = time.monotonic()
    engine_root = Path(args.engine_root).resolve()
    target_path = Path(args.path).resolve()
    state.target_path_str = str(target_path)
    state.engine_root_str = str(engine_root)
    raw_wid = getattr(args, "worker_id", None)
    state.worker_id = str(raw_wid).strip() if raw_wid else str(uuid.uuid4())
    state.canonical_target = str(target_path)
    state.canonical_project_root = str(target_path)
    try:
        import psutil
        state.process_create_time = psutil.Process(os.getpid()).create_time()
    except Exception:
        state.process_create_time = time.time()

    persisted = read_project_meta(engine_root, args.name)
    cli_dict = {
        k: getattr(args, a, None) for k, a in [
            ("debounce_s", "debounce"), ("track_reads", "track_reads"), ("track_exec", "track_exec"),
            ("shadow_git", "shadow_git"), ("git_init_primary", "git_init_primary"),
            ("ide_profile", "ide_profile"), ("ide_custom_marker", "ide_custom_marker"),
        ]
    }
    state.config = ProjectConfig.resolve(cli_overrides=cli_dict, persisted_config=persisted)
    state.powers = dict(state.config.powers)
    if getattr(args, "ide_custom_marker", None):
        state.powers["ide_custom_marker"] = args.ide_custom_marker

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
    _write_status_file(state)

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
            state.exec_tracker.trigger_fast_poll(state.config.debounce_s + 1.0)

    has_files = any(p for p in target_path.iterdir() if p.name not in (".git", ".sf", ".sfdx", ".spd")) if target_path.exists() else False
    is_new = getattr(args, "is_new_project", False)
    from .worker_bundle import on_burst_assembler_action
    state.burst_assembler = BurstAssembler(
        quiet_period=state.config.debounce_s,
        max_burst_s=state.config.max_burst_s,
        on_activity=_on_fs_activity,
        on_action=on_burst_assembler_action,
        is_initial_ingestion=bool(is_new and has_files),
        config=state.config,
    )
    state.event_bundler = state.burst_assembler

    state.config.add_listener(lambda cfg: _sync_bundler(state, cfg))
    state.project_watcher = ProjectWatcher(target_path=target_path, bundler=state.burst_assembler)

    state.change_processor = ChangeProcessor(
        root_path=target_path,
        downstream_queue=state.burst_assembler.downstream_queue,
        manifest=state.project_watcher.manifest,
        ignore_policy=state.project_watcher.ignore_policy,
        on_bundle_processed=lambda b: _handle_event_bundle(b, state=state),
    )

    state.burst_assembler.start()
    state.change_processor.start()
    state.project_watcher.start()

    if args.shadow_git:
        shadow_repo_dir = proj_dir / "shadow_git"
        state.shadow_git = ShadowGit(shadow_dir=shadow_repo_dir, target_dir=target_path)
        if state.shadow_git.init_repo() and state.session_db and state.session_db.conn:
            state.shadow_git.recover_pending_ops(state.session_db.conn)

    if is_new and has_files:
        check_fault("during_indexing")
        from .worker_bundle import trigger_initial_ingestion
        trigger_initial_ingestion(state=state)

    if args.track_reads:
        def _on_file_read(read_info: dict[str, Any]) -> None:
            rel_path = read_info.get("file_path", "")
            proc_name = read_info.get("process_name", "unknown")
            pid = read_info.get("pid", 0)
            source = read_info.get("source", "other")
            if source not in ("open_handle", "cmd_ref", "other"):
                source = "other"
            details = f"Observed ({source}) by {proc_name} (PID {pid})"
            bundler = state.burst_assembler or state.event_bundler
            if bundler and rel_path:
                bundler.record_action("READ", rel_path, details=details)
                _write_status_file(state=state)
            if state.session_db is not None and state.session_id is not None:
                try:
                    state.session_db.record_event(
                        session_id=state.session_id,
                        event_type="READ",
                        first_file=rel_path,
                        summary=details,
                        executor=source,
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
            bundler = state.burst_assembler or state.event_bundler
            if bundler and cmd_str:
                bundler.record_action("EXEC", cmd_str, details=f"Exit code: {exit_code}")
                _write_status_file(state=state)
            logger.info("[EXEC_CAPTURED] %s (exit=%s)", cmd_str, exit_code)

        state.exec_tracker = ExecutionTracker(
            target_dir=target_path,
            db=state.session_db,
            session_id=state.session_id,
            idle_interval=1.2,
            fast_interval=0.18,
            is_active_callback=lambda: bool(
                (state.burst_assembler or state.event_bundler)
                and (state.burst_assembler or state.event_bundler).get_debounce_status().get("active")
            ),
            on_command=_on_command_executed,
            on_read=_on_file_read if args.track_reads else None,
            ide_profile=args.ide_profile,
            custom_marker=args.ide_custom_marker,
        )
        state.exec_tracker.start()

    _emit_json(
        "startup",
        {"target_path": str(target_path), "session_id": state.session_id, "db_path": str(db_path), "powers": state.powers},
        state=state,
    )
    _write_status_file(state=state)
