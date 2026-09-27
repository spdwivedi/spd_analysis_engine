"""scripts/project_worker.py - Isolated worker process coordinator."""
from __future__ import annotations

import argparse, logging, signal, sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ENGINE_ROOT_DEFAULT = _HERE.parent
sys.path.insert(0, str(_ENGINE_ROOT_DEFAULT.parent))

try:
    from .storage_rotator import _slug
    from .core.worker_lifecycle import (
        WorkerState, worker_state, wire_worker, _clean_shutdown, _unhandled_exception_handler,
    )
    from .core.worker_bundle import _run_heartbeat
except (ImportError, ModuleNotFoundError):
    from spd_analysis_engine.scripts.storage_rotator import _slug
    from spd_analysis_engine.scripts.core.worker_lifecycle import (
        WorkerState, worker_state, wire_worker, _clean_shutdown, _unhandled_exception_handler,
    )
    from spd_analysis_engine.scripts.core.worker_bundle import _run_heartbeat

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
    return p


def _setup_logging(log_path: Path) -> None:
    """Redirect stdout and stderr to worker.log."""
    log_file = open(log_path, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = log_file
    sys.excepthook = _unhandled_exception_handler
    for h in logging.root.handlers[:]:
        logging.root.removeHandler(h)
    file_handler = logging.StreamHandler(log_file)
    file_handler.setFormatter(logging.Formatter("%(asctime)s [WORKER] %(levelname)-8s %(message)s", datefmt="%H:%M:%S"))
    logging.root.addHandler(file_handler)
    logging.root.setLevel(logging.INFO)


def main(argv: list[str] | None = None) -> None:
    """Bootstrap and run the project worker."""
    args = _build_arg_parser().parse_args(argv)
    engine_root = Path(args.engine_root).resolve()
    log_path = engine_root / "current" / _slug(args.name) / "worker.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    _setup_logging(log_path)
    wire_worker(args, state=worker_state)
    try:
        _run_heartbeat(interval=args.heartbeat_interval, state=worker_state)
    except KeyboardInterrupt:
        _clean_shutdown(signal.SIGINT, state=worker_state)


if __name__ == "__main__":
    main()
