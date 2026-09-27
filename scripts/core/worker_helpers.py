"""
scripts/core/worker_helpers.py
==============================
Process inspection and status file helpers for Supervisor.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

try:
    from .worker_entry import _PSUTIL_AVAILABLE
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.core.worker_entry import _PSUTIL_AVAILABLE
    except (ImportError, ModuleNotFoundError):
        from scripts.core.worker_entry import _PSUTIL_AVAILABLE

if _PSUTIL_AVAILABLE:
    import psutil

logger = logging.getLogger(__name__)


def verify_worker_pid(pid: int, name: str) -> bool | None:
    """Confirm a PID belongs to a live project_worker.py process."""
    if not _PSUTIL_AVAILABLE:
        return None

    try:
        p = psutil.Process(pid)
        if not p.is_running() or p.status() == psutil.STATUS_ZOMBIE:
            return False
        cmdline = p.cmdline()
        if any("project_worker" in arg for arg in cmdline):
            return True
        logger.warning(
            "PID %s claimed by '%s' is not project_worker (cmdline: %s) — treating as stale",
            pid,
            name,
            " ".join(cmdline[:6]),
        )
        return False
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        logger.debug(
            "AccessDenied checking PID %s for '%s' — assuming valid", pid, name
        )
        return None


def mark_stale_pid(pid_file: Path, slug: str) -> None:
    """Rename worker.pid -> worker.pid.stale for inspection."""
    stale = pid_file.with_name("worker.pid.stale")
    try:
        pid_file.rename(stale)
        logger.info("Stale PID file preserved as worker.pid.stale for slug '%s'", slug)
    except OSError as exc:
        logger.warning("Could not rename stale PID file %s: %s", pid_file, exc)


def read_status_file(proj_dir: Path) -> dict[str, Any]:
    """Read and parse worker.status.json; return {} on any error."""
    path = proj_dir / "worker.status.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.debug("Could not read status file %s: %s", path, exc)
        return {}


def prune_phantom_sessions(db_path: Path, slug: str) -> None:
    """Prune zero-event / zero-burst phantom sessions before archiving."""
    if not db_path.exists():
        return
    try:
        conn = sqlite3.connect(db_path, timeout=10.0)
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM sessions WHERE id NOT IN (SELECT DISTINCT session_id FROM events WHERE session_id IS NOT NULL)"
        )
        conn.commit()
        conn.close()
        logger.debug("Pruned empty phantom sessions for '%s' prior to archive", slug)
    except Exception as exc:
        logger.debug("Error pruning empty sessions for '%s': %s", slug, exc)


def wait_for_worker_pid(pid_file: Path, proc: Any, deadline_s: float = 3.0) -> int:
    """Poll for worker.pid until deadline; return discovered or fallback PID."""
    import time
    real_pid = proc.pid if proc is not None else 0
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        if pid_file.exists():
            try:
                pid_data = json.loads(pid_file.read_text(encoding="utf-8"))
                return int(pid_data["pid"])
            except (json.JSONDecodeError, KeyError, ValueError, OSError):
                pass
        if proc is not None and proc.poll() is not None:
            break
        time.sleep(0.1)
    return real_pid


def stop_worker_process(entry: Any, pid: int, name: str, stop_timeout_s: float = 10.0, send_stop_fn: Any = None, send_stop_pid_fn: Any = None, wait_for_pid_fn: Any = None) -> int | None:
    """Signal worker to terminate gracefully via psutil or signals."""
    import subprocess
    exit_code: int | None = None
    if _PSUTIL_AVAILABLE and pid:
        try:
            p = psutil.Process(pid)
            if p.is_running():
                p.terminate()
                try:
                    p.wait(timeout=stop_timeout_s)
                except psutil.TimeoutExpired:
                    logger.warning(
                        "Worker '%s' (PID %s) unresponsive after %.1fs — force-killing",
                        name,
                        pid,
                        stop_timeout_s,
                    )
                    p.kill()
                    p.wait(timeout=3.0)
        except psutil.NoSuchProcess:
            pass
        except Exception as exc:
            logger.warning("Error stopping process %s: %s", pid, exc)
    elif getattr(entry, "proc", None) is not None:
        if send_stop_fn:
            send_stop_fn(entry.proc)
        try:
            entry.proc.wait(timeout=stop_timeout_s)
        except subprocess.TimeoutExpired:
            entry.proc.kill()
            entry.proc.wait()
        exit_code = entry.proc.returncode
    elif pid:
        if send_stop_pid_fn:
            send_stop_pid_fn(pid)
        if wait_for_pid_fn:
            wait_for_pid_fn(pid, stop_timeout_s)
    return exit_code


def discover_active_workers(engine_root: Path, registry: dict[str, Any], worker_entry_cls: Any) -> None:
    """Scan current/ for live worker.pid files and update registry."""
    current_dir = engine_root / "current"
    if not current_dir.exists():
        return

    for proj_dir in sorted(current_dir.iterdir()):
        if not proj_dir.is_dir():
            continue

        slug = proj_dir.name
        pid_file = proj_dir / "worker.pid"

        if not pid_file.exists():
            continue

        try:
            data: dict = json.loads(pid_file.read_text(encoding="utf-8"))
            pid = int(data["pid"])
            project_name = str(data.get("project", slug))
            target_path = str(data.get("target_path", ""))
            started_at_iso = str(data.get("started_at", ""))
        except Exception as exc:
            logger.warning("Cannot parse worker.pid in '%s': %s", proj_dir, exc)
            continue

        existing = registry.get(project_name)
        if existing is not None:
            if existing.pid == pid and existing.is_alive():
                continue
            logger.debug(
                "Evicting stale registry entry for '%s' (was PID %s, disk says PID %s)",
                project_name,
                existing.pid,
                pid,
            )
            del registry[project_name]

        alive = verify_worker_pid(pid, project_name)
        if alive is False:
            logger.info(
                "Stale PID file for '%s' (PID %s is dead or wrong process)",
                project_name,
                pid,
            )
            mark_stale_pid(pid_file, slug)
            continue

        entry = worker_entry_cls(
            name=project_name,
            target_path=target_path,
            pid=pid,
            log_path=proj_dir / "worker.log",
            started_at_iso=started_at_iso,
        )

        status_data = read_status_file(proj_dir)
        if status_data.get("session_id") is not None:
            entry.session_id = int(status_data["session_id"])

        registry[project_name] = entry
        logger.info(
            "Reconciled worker '%s' (PID %s, started %s)",
            project_name,
            pid,
            started_at_iso or "unknown",
        )


