"""
scripts/core/worker_interlock.py
================================
Centralized WorkerInterlock context manager for locking project operations.
Pauses/terminates worker processes and releases SQLite connections before
Rollback, Project Deletion, or Storage Tier rotation.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

try:
    import psutil
    _PSUTIL = True
except ImportError:
    _PSUTIL = False

from ..storage_rotator import _slug, read_project_meta

from .process_sweeper import sweep_project_processes

logger = logging.getLogger(__name__)


def _is_proc_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if _PSUTIL:
        try:
            p = psutil.Process(pid)
            return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _terminate_proc(pid: int) -> None:
    if not _is_proc_alive(pid):
        return
    if _PSUTIL:
        try:
            p = psutil.Process(pid)
            procs = p.children(recursive=True) + [p]
            for proc in procs:
                try:
                    proc.terminate()
                except Exception:
                    pass
            _, alive = psutil.wait_procs(procs, timeout=3.0)
            for proc in alive:
                try:
                    proc.kill()
                except Exception:
                    pass
        except Exception:
            pass
    else:
        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(0.5)
            if _is_proc_alive(pid):
                os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
        except Exception:
            pass


@contextmanager
def interlock_project_operation(
    slug: str,
    engine_root: Path | str,
    restart_worker_after: bool = False,
    supervisor: Any | None = None,
) -> Generator[None, None, None]:
    """
    Context manager to pause/terminate worker process for slug, release file/DB locks,
    execute callers critical operation, and optionally re-spawn worker afterward.
    """
    clean_slug = _slug(slug)
    root = Path(engine_root).resolve()
    was_active = False
    active_pid: int | None = None

    if supervisor is not None and hasattr(supervisor, "_registry"):
        for k in list(supervisor._registry.keys()):
            if k == clean_slug or _slug(k) == clean_slug:
                ent = supervisor._registry[k]
                if ent and hasattr(ent, "pid") and ent.pid:
                    if _is_proc_alive(int(ent.pid)):
                        was_active = True
                        active_pid = int(ent.pid)
                supervisor._registry.pop(k, None)

    if not was_active:
        for tier in ("current", "last_run"):
            pid_file = root / tier / clean_slug / "worker.pid"
            if pid_file.exists():
                try:
                    data = pid_file.read_text(encoding="utf-8").strip()
                    cand_pid = int(json.loads(data).get("pid", 0)) if data.startswith("{") else int(data)
                    if cand_pid > 0 and _is_proc_alive(cand_pid):
                        was_active = True
                        active_pid = cand_pid
                        break
                except Exception:
                    pass

    meta = read_project_meta(root, clean_slug)
    if active_pid is not None:
        _terminate_proc(active_pid)
        time.sleep(0.3)

    swept = sweep_project_processes(clean_slug, meta.get("target_path"))
    if swept:
        was_active = True
        time.sleep(0.3)

    try:
        yield
    finally:
        if restart_worker_after and was_active:
            if supervisor is not None and hasattr(supervisor, "start_project"):
                target_path = meta.get("target_path")
                if target_path and Path(target_path).exists():
                    powers = {}
                    if meta.get("powers"):
                        try:
                            p = json.loads(meta["powers"]) if isinstance(meta["powers"], str) else meta["powers"]
                            if isinstance(p, dict):
                                powers = p
                        except Exception:
                            pass
                    try:
                        supervisor.start_project(
                            name=clean_slug,
                            path=target_path,
                            scaffold=False,
                            track_reads=powers.get("track_reads", True),
                            track_exec=powers.get("track_exec", True),
                            shadow_git=powers.get("shadow_git", True),
                        )
                    except Exception as exc:
                        logger.warning("Interlock failed to restart worker %s: %s", clean_slug, exc)
        else:
            for tier in ("current", "last_run"):
                for fname in ("worker.pid", "session.lock"):
                    f = root / tier / clean_slug / fname
                    if f.exists():
                        try:
                            f.unlink(missing_ok=True)
                        except Exception:
                            pass
