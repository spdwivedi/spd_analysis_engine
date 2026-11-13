"""scripts/project_worker.py - Isolated worker process coordinator."""
from __future__ import annotations

import argparse, json, logging, signal, sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_ENGINE_ROOT_DEFAULT = _HERE.parent
if str(_ENGINE_ROOT_DEFAULT) not in sys.path:
    sys.path.insert(0, str(_ENGINE_ROOT_DEFAULT))
if str(_ENGINE_ROOT_DEFAULT.parent) not in sys.path:
    sys.path.insert(0, str(_ENGINE_ROOT_DEFAULT.parent))

try:
    from spd_analysis_engine.scripts.storage_rotator import _slug
    from spd_analysis_engine.scripts.core.worker_lifecycle import (
        WorkerState,
        worker_state,
        wire_worker,
        _clean_shutdown,
        _unhandled_exception_handler,
    )
    from spd_analysis_engine.scripts.core.worker_bundle import _run_heartbeat
except (ImportError, ModuleNotFoundError):
    from scripts.storage_rotator import _slug
    from scripts.core.worker_lifecycle import (
        WorkerState,
        worker_state,
        wire_worker,
        _clean_shutdown,
        _unhandled_exception_handler,
    )
    from scripts.core.worker_bundle import _run_heartbeat

logger = logging.getLogger(__name__)


def _build_arg_parser() -> argparse.ArgumentParser:
    """Build command-line argument parser for project worker."""
    p = argparse.ArgumentParser(prog="project_worker", description="SPD Analysis Engine worker")
    p.add_argument("--name", required=True, metavar="PROJECT_NAME", help="Project name")
    p.add_argument("--path", required=True, metavar="TARGET_PATH", help="Monitored directory")
    p.add_argument("--scaffold", action="store_true", default=False)
    p.add_argument("--engine-root", default=str(_ENGINE_ROOT_DEFAULT))
    p.add_argument("--heartbeat-interval", type=float, default=2.0)
    p.add_argument("--debounce", type=float, default=3.5)
    p.add_argument("--track-reads", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--track-exec", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--shadow-git", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--git-init-primary", action="store_true", default=False)
    p.add_argument("--ide-profile", default="antigravity", choices=["antigravity", "cursor", "windsurf", "claude_code", "custom"])
    p.add_argument("--ide-custom-marker", default=None)
    p.add_argument("--is-new-project", action="store_true", default=False)
    p.add_argument("--worker-id", default=None, help="Explicit UUID for worker identity")
    return p


class WorkerJsonFormatter(logging.Formatter):
    """Structured JSON Lines formatter for worker telemetry logs."""

    def __init__(self, project_id: str = "", worker_id: str = "") -> None:
        super().__init__()
        self.project_id = project_id
        self.worker_id = worker_id

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()
        entry: dict[str, Any] = {
            "timestamp": ts,
            "project_id": getattr(record, "project_id", self.project_id or ""),
            "worker_id": getattr(record, "worker_id", self.worker_id or ""),
            "operation_id": getattr(record, "operation_id", None),
            "event_id": getattr(record, "event_id", None),
            "component": getattr(record, "component", record.name),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def _setup_logging(log_path: Path, project_id: str = "", worker_id: str = "") -> None:
    """Configure structured JSON Lines rotating log handler for worker.log (5 MB x 3)."""
    import logging.handlers
    log_path.parent.mkdir(parents=True, exist_ok=True)
    for h in logging.root.handlers[:]:
        logging.root.removeHandler(h)
    rotating_handler = logging.handlers.RotatingFileHandler(
        str(log_path),
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    rotating_handler.setFormatter(WorkerJsonFormatter(project_id=project_id, worker_id=worker_id))
    logging.root.addHandler(rotating_handler)
    logging.root.setLevel(logging.INFO)
    sys.excepthook = _unhandled_exception_handler


def main(argv: list[str] | None = None) -> None:
    """Bootstrap and run the project worker."""
    import os, uuid
    args = _build_arg_parser().parse_args(argv)
    engine_root = Path(args.engine_root).resolve()
    slug_name = _slug(args.name)
    current_proj_dir = engine_root / "current" / slug_name
    current_proj_dir.mkdir(parents=True, exist_ok=True)
    log_path = current_proj_dir / "worker.log"
    _setup_logging(log_path, project_id=slug_name, worker_id=str(getattr(args, "worker_id", "") or ""))
    wire_worker(args, state=worker_state)

    control_server = None
    try:
        from .controller.control_channel import WorkerControlServer
        secret = os.environ.get("SPD_WORKER_SECRET")
        secret_file = current_proj_dir / ".worker_secret"
        if not secret and secret_file.exists():
            try:
                secret = secret_file.read_text(encoding="utf-8").strip()
            except Exception:
                pass
        if not secret:
            secret = uuid.uuid4().hex
            try:
                secret_file.write_text(secret, encoding="utf-8")
            except Exception:
                pass
        elif not secret_file.exists():
            try:
                secret_file.write_text(secret, encoding="utf-8")
            except Exception:
                pass

        def _handle_control_command(cmd: str, data: dict[str, Any]) -> tuple[bool, str, str | None]:
            curr_state = "STOPPING" if worker_state.shutdown_requested else "ACTIVE"
            if cmd == "PING":
                return True, curr_state, None
            if cmd == "STOP":
                worker_state.shutdown_requested = True
                worker_state.stop_event.set()
                return True, "STOPPING", None
            if cmd == "PAUSE":
                if worker_state.project_watcher and hasattr(worker_state.project_watcher, "pause"):
                    worker_state.project_watcher.pause()
                if hasattr(worker_state, "change_processor") and worker_state.change_processor and hasattr(worker_state.change_processor, "pause"):
                    worker_state.change_processor.pause()
                return True, "PAUSED", None
            if cmd == "RESUME":
                if worker_state.project_watcher and hasattr(worker_state.project_watcher, "resume"):
                    worker_state.project_watcher.resume()
                if hasattr(worker_state, "change_processor") and worker_state.change_processor and hasattr(worker_state.change_processor, "resume"):
                    worker_state.change_processor.resume()
                return True, "ACTIVE", None
            if cmd == "SEAL":
                if worker_state.event_bundler:
                    if hasattr(worker_state.event_bundler, "flush_now"):
                        worker_state.event_bundler.flush_now()
                    elif hasattr(worker_state.event_bundler, "flush_burst_now"):
                        worker_state.event_bundler.flush_burst_now()
                    elif hasattr(worker_state.event_bundler, "flush_pending"):
                        worker_state.event_bundler.flush_pending()
                return True, curr_state, None
            if cmd in ("RELOAD_CONFIG", "SUPPRESS"):
                return True, curr_state, None
            return True, curr_state, None

        control_server = WorkerControlServer(
            secret=secret,
            project_dir=current_proj_dir,
            project_id=f"proj_{slug_name}",
            worker_id=worker_state.worker_id,
            command_handler=_handle_control_command,
            get_state_fn=lambda: "STOPPING" if worker_state.shutdown_requested else "ACTIVE",
        )
        control_server.start()
    except Exception as cs_exc:
        logger.warning("Could not start loopback control channel: %s", cs_exc)

    try:
        _run_heartbeat(interval=args.heartbeat_interval, state=worker_state)
    except KeyboardInterrupt:
        worker_state.shutdown_requested = True
        worker_state.stop_event.set()
    finally:
        if control_server is not None:
            try:
                control_server.stop()
            except Exception:
                pass
        _clean_shutdown(state=worker_state)


if __name__ == "__main__":
    main()
