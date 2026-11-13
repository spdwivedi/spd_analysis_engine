"""
scripts/core/process_sweeper.py
===============================
OS-level process sweeper for inspecting and terminating rogue or duplicate
worker daemons before lifecycle events.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

try:
    import psutil
    _PSUTIL = True
except ImportError:
    _PSUTIL = False

from ..storage_rotator import _slug
from .worker_helpers import _tokenize_cmdline, _extract_flag_value

logger = logging.getLogger(__name__)


def sweep_project_processes(
    slug: str,
    target_path: str | None = None,
    force_kill: bool = True,
    worker_id: str | None = None,
    engine_root: Path | str | None = None,
) -> list[int]:
    """
    Compatibility wrapper: sweeps rogue or orphaned worker processes.
    Authoritative lifecycle management is delegated to LifecycleController.
    Never sweeps a live registered worker unless explicitly targeted by worker_id.
    """
    if not _PSUTIL:
        return []

    clean_slug = _slug(slug)
    norm_target = str(Path(target_path).resolve()).lower() if target_path else None
    current_pid = os.getpid()
    matched_procs: list[psutil.Process] = []
    matched_pids: list[int] = []

    # Check active worker PID from current/<slug>/worker.pid to protect it
    active_disk_pid: int | None = None
    if engine_root and not worker_id:
        p_file = Path(engine_root) / "current" / clean_slug / "worker.pid"
        if p_file.exists():
            try:
                import json
                raw = p_file.read_text(encoding="utf-8").strip()
                if raw.startswith("{"):
                    active_disk_pid = int(json.loads(raw).get("pid", 0))
                else:
                    active_disk_pid = int(raw.splitlines()[0].strip())
            except Exception:
                pass

    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            pid = proc.info.get("pid")
            if not pid or pid == current_pid:
                continue

            pname = (proc.info.get("name") or "").lower()
            if "python" not in pname:
                continue

            raw_cmd = proc.info.get("cmdline") or []
            if not raw_cmd:
                continue

            tokens = _tokenize_cmdline(raw_cmd)
            if not any("project_worker" in str(arg).lower() for arg in tokens):
                continue

            is_match = False

            # 1. Match by worker_id if provided
            if worker_id:
                val_worker_id = _extract_flag_value(tokens, "--worker-id")
                if val_worker_id and val_worker_id == worker_id:
                    is_match = True

            # 2. Match by exact canonical slug (never substring)
            if not is_match and clean_slug:
                val_name = _extract_flag_value(tokens, "--name")
                if val_name and _slug(val_name) == clean_slug:
                    is_match = True

            # 3. Match by exact canonical target workspace path (never substring or prefix)
            if not is_match and norm_target:
                val_path = _extract_flag_value(tokens, "--path")
                if val_path:
                    try:
                        cand_target = str(Path(val_path).resolve()).lower()
                        if cand_target == norm_target:
                            is_match = True
                    except Exception:
                        pass

            if is_match:
                if active_disk_pid is not None and pid == active_disk_pid and not worker_id:
                    logger.debug("Skipping active worker PID %s during sweep", pid)
                    continue
                matched_procs.append(proc)
                matched_pids.append(pid)

        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        except Exception as exc:
            logger.debug("Error inspecting process %s: %s", getattr(proc, "pid", "unknown"), exc)

    if not matched_procs:
        return []

    logger.info("Sweeping %d rogue worker process(es) for project '%s': %s", len(matched_pids), clean_slug, matched_pids)

    for proc in matched_procs:
        try:
            proc.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    gone, alive = psutil.wait_procs(matched_procs, timeout=1.5)

    if force_kill and alive:
        for proc in alive:
            try:
                proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

    return matched_pids
