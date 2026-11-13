"""
scripts/controller/process.py
=============================
Process lifecycle, identity verification, and cooperative stop management.
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

try:
    import psutil
    _PSUTIL_AVAILABLE = True
except ImportError:
    psutil = None  # type: ignore
    _PSUTIL_AVAILABLE = False

from spd_analysis_engine.scripts.core.spawner import (
    spawn_detached_worker,
    send_stop_pid,
    kill_pid,
    wait_for_pid_exit,
    _STOP_TIMEOUT_S,
)

logger = logging.getLogger(__name__)


def is_process_alive(pid: int | None, expected_create_time: float | None = None) -> bool:
    """
    Verify if a process with pid is running.
    If expected_create_time is provided and psutil is available, ensures the process
    is not a recycled PID by checking process create_time.
    """
    if pid is None or pid <= 0:
        return False

    if _PSUTIL_AVAILABLE:
        try:
            p = psutil.Process(pid)
            if not p.is_running() or p.status() == psutil.STATUS_ZOMBIE:
                return False
            if expected_create_time is not None:
                # Allow minor float precision tolerance (0.5s)
                if abs(p.create_time() - expected_create_time) > 0.5:
                    logger.warning("PID %d is active but create_time mismatch (recycled PID)", pid)
                    return False
            return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False
        except Exception:
            return False

    # Standard library fallback
    if sys.platform.startswith("win"):
        try:
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            exit_code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            ctypes.windll.kernel32.CloseHandle(handle)
            return exit_code.value == STILL_ACTIVE
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False


def get_process_create_time(pid: int | None) -> float | None:
    """Return process create time in epoch seconds if psutil is available."""
    if pid is None or not _PSUTIL_AVAILABLE:
        return None
    try:
        p = psutil.Process(pid)
        return float(p.create_time())
    except Exception:
        return None


def stop_process_cooperatively(
    pid: int | None,
    timeout_s: float = _STOP_TIMEOUT_S,
    stop_cmd_path: Path | None = None,
    expected_create_time: float | None = None,
) -> tuple[bool, str]:
    """
    Stop worker process through cooperative escalation:
    1. Sentinel stop request (stop.cmd) / POSIX SIGTERM (never TerminateProcess on Windows)
    2. Wait up to timeout_s for clean exit
    3. Escalation to terminate / kill if timeout expires
    Returns (clean_stop: bool, end_reason: str).
    """
    if pid is None or not is_process_alive(pid, expected_create_time):
        return True, "already_stopped"

    # Step 1: Cooperative stop request via file sentinel
    if stop_cmd_path is not None:
        try:
            stop_cmd_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_cmd = stop_cmd_path.with_suffix(".tmp")
            tmp_cmd.write_text("STOP\n", encoding="utf-8")
            tmp_cmd.replace(stop_cmd_path)
        except Exception as exc:
            logger.debug("Failed writing stop sentinel to %s: %s", stop_cmd_path, exc)

    # On POSIX only: send SIGTERM (on Windows, send_stop_pid invokes TerminateProcess, which is not cooperative)
    if sys.platform != "win32":
        try:
            send_stop_pid(pid)
        except Exception as exc:
            logger.warning("Failed to send stop signal to PID %d: %s", pid, exc)

    # Step 2: Wait for clean exit
    clean = wait_for_pid_exit(pid, timeout=timeout_s)
    if clean:
        logger.info("PID %d exited cleanly within %.1fs", pid, timeout_s)
        return True, "clean_stop"

    # Step 3 & 4: Escalation to terminate / kill after timeout
    logger.warning("PID %d failed to stop within %.1fs; escalating to forced kill", pid, timeout_s)
    if is_process_alive(pid, expected_create_time):
        try:
            kill_pid(pid)
        except Exception as exc:
            logger.error("Failed to kill PID %d: %s", pid, exc)

    wait_for_pid_exit(pid, timeout=3.0)
    return False, "forced_kill"
