"""
scripts/core/worker_helpers.py
==============================
Process inspection and status file helpers for Supervisor.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .time_utils import utc_now_iso, calculate_heartbeat_age_s


@dataclass
class StopResult:
    """Outcome of worker stop escalation pipeline."""
    exit_code: int | None = None
    escalated: bool = False
    clean: bool = False
    method: str = "unknown"

    def __int__(self) -> int:
        return self.exit_code or 0

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, int): return (self.exit_code or 0) == other
        if other is None: return self.exit_code is None
        return super().__eq__(other)

from .worker_entry import _PSUTIL_AVAILABLE

from .worker_status import (
    safe_write_status_json,
    read_status_file,
    prune_phantom_sessions,
    format_worker_status_row,
)

from ..storage_rotator import _slug, read_project_meta
from .project_config import DEFAULT_DEBOUNCE_S

if _PSUTIL_AVAILABLE:
    import psutil

logger = logging.getLogger(__name__)


def _tokenize_cmdline(raw_cmd: list[str]) -> list[str]:
    """Split a command line list or joined string into clean individual arguments."""
    if not raw_cmd:
        return []
    if len(raw_cmd) == 1 and (" " in raw_cmd[0] or "\t" in raw_cmd[0]):
        import shlex
        try:
            return shlex.split(raw_cmd[0], posix=(sys.platform != "win32"))
        except Exception:
            return raw_cmd[0].split()
    return list(raw_cmd)


def _extract_flag_value(tokens: list[str], flag: str) -> str | None:
    """Extract argument value for a given flag (e.g. --name, --path, --worker-id)."""
    flag_lower = flag.lower()
    for i, token in enumerate(tokens):
        t_clean = token.strip('\'"')
        t_lower = t_clean.lower()
        if t_lower == flag_lower:
            if i + 1 < len(tokens):
                return tokens[i + 1].strip('\'"')
        elif t_lower.startswith(flag_lower + "="):
            return t_clean[len(flag) + 1:].strip('\'"')
    return None


def validate_worker_process(
    pid: int,
    name: str = "",
    expected_worker_id: str | None = None,
    expected_create_time: float | None = None,
    expected_project_root: str | Path | None = None,
    expected_executable: str | None = None,
    expected_engine_root: str | Path | None = None,
    tolerance_s: float = 3.0,
) -> bool | None:
    """
    Validate a worker process against exact identity and OS-level attributes.
    """
    if not _PSUTIL_AVAILABLE:
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    try:
        if not psutil.pid_exists(pid):
            return False
        p = psutil.Process(pid)
        if not p.is_running() or p.status() == psutil.STATUS_ZOMBIE:
            return False

        # 1. PID create time check within tolerance
        if expected_create_time is not None:
            actual_create = p.create_time()
            if abs(actual_create - expected_create_time) > tolerance_s:
                logger.warning(
                    "PID %s create time mismatch (recorded: %.2f, actual: %.2f) — treating as stale",
                    pid, expected_create_time, actual_create,
                )
                return False

        # 2. Executable check
        if expected_executable:
            try:
                p_exe, exp_exe = Path(p.exe()).resolve(), Path(expected_executable).resolve()
                p_s = str(p_exe).lower() if sys.platform == "win32" else str(p_exe)
                e_s = str(exp_exe).lower() if sys.platform == "win32" else str(exp_exe)
                if p_s != e_s and not (sys.platform == "win32" and p_exe.stem.lower() in ("python", "pythonw") and exp_exe.stem.lower() in ("python", "pythonw")):
                    logger.warning("PID %s executable mismatch (expected: %s, actual: %s) — treating as stale", pid, expected_executable, p.exe())
                    return False
            except (psutil.AccessDenied, OSError): pass
        else:
            try:
                if "python" not in Path(p.exe()).stem.lower(): return False
            except (psutil.AccessDenied, OSError): pass

        # Parse command line tokens
        try: cmdline = p.cmdline()
        except (psutil.AccessDenied, OSError): cmdline = []
        tokens = _tokenize_cmdline(cmdline)

        # 3. Exact Worker ID check
        if expected_worker_id:
            actual_worker_id = _extract_flag_value(tokens, "--worker-id")
            if actual_worker_id != expected_worker_id:
                logger.warning("PID %s worker_id mismatch (expected: %s, actual: %s) — treating as stale", pid, expected_worker_id, actual_worker_id)
                return False

        # 4. Canonical project root check
        if expected_project_root:
            actual_path = _extract_flag_value(tokens, "--path")
            if actual_path:
                norm_actual, norm_expected = str(Path(actual_path).resolve()), str(Path(expected_project_root).resolve())
                if sys.platform == "win32":
                    norm_actual, norm_expected = norm_actual.lower(), norm_expected.lower()
                if norm_actual != norm_expected:
                    logger.warning("PID %s project root mismatch (expected: %s, actual: %s) — treating as stale", pid, norm_expected, norm_actual)
                    return False

        # 5. Engine root check
        if expected_engine_root:
            actual_eng = _extract_flag_value(tokens, "--engine-root")
            if actual_eng:
                norm_eng_act = str(Path(actual_eng).resolve())
                norm_eng_exp = str(Path(expected_engine_root).resolve())
                if sys.platform == "win32":
                    norm_eng_act, norm_eng_exp = norm_eng_act.lower(), norm_eng_exp.lower()
                if norm_eng_act != norm_eng_exp:
                    logger.warning("PID %s engine root mismatch (expected: %s, actual: %s) — treating as stale", pid, norm_eng_exp, norm_eng_act)
                    return False

        # 6. Legacy/Name check if no worker_id supplied
        if name and not expected_worker_id:
            actual_name = _extract_flag_value(tokens, "--name")
            if actual_name and _slug(actual_name) != _slug(name):
                return False

        # Verify it is indeed running project_worker
        if not any("project_worker" in t for t in tokens):
            logger.warning(
                "PID %s claimed by '%s' is not project_worker (cmdline: %s) — treating as stale",
                pid, name or expected_worker_id, " ".join(tokens[:6]),
            )
            return False

        return True

    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return False
    except psutil.AccessDenied:
        logger.debug("AccessDenied checking PID %s for '%s' — assuming valid", pid, name or expected_worker_id)
        return None


def verify_worker_pid(
    pid: int,
    name: str = "",
    expected_create_time: float | None = None,
    expected_worker_id: str | None = None,
    expected_project_root: str | Path | None = None,
    expected_executable: str | None = None,
    expected_engine_root: str | Path | None = None,
    tolerance_s: float = 3.0,
) -> bool | None:
    """Confirm a PID belongs to a live project_worker.py process with exact identity validation."""
    return validate_worker_process(
        pid=pid,
        name=name,
        expected_worker_id=expected_worker_id,
        expected_create_time=expected_create_time,
        expected_project_root=expected_project_root,
        expected_executable=expected_executable,
        expected_engine_root=expected_engine_root,
        tolerance_s=tolerance_s,
    )


def mark_stale_pid(pid_file: Path, slug: str) -> None:
    """
    Rename worker.pid -> worker.pid.stale for inspection safely across platforms.
    Safely removes any existing .stale file before renaming or appends a deterministic suffix
    (worker.pid.stale.1, etc.) to prevent [WinError 183] file collision on Windows.
    Never overwrites a valid current worker.pid and never crashes worker discovery.
    """
    if not pid_file.is_file():
        return
    stale = pid_file.with_name("worker.pid.stale")
    try:
        # Safely remove existing .stale file before renaming
        if stale.exists():
            try:
                stale.unlink()
            except OSError:
                # If existing .stale is locked or unremovable, find first unused deterministic suffix
                idx = 1
                while True:
                    candidate = pid_file.with_name(f"worker.pid.stale.{idx}")
                    if not candidate.exists():
                        stale = candidate
                        break
                    idx += 1

        try:
            os.replace(pid_file, stale)
            logger.info("Stale PID file preserved as %s for slug '%s'", stale.name, slug)
        except OSError:
            # Fallback: append deterministic suffix if collision occurred during replace
            idx = 1
            while idx < 100:
                candidate = pid_file.with_name(f"worker.pid.stale.{idx}")
                if not candidate.exists():
                    try:
                        os.replace(pid_file, candidate)
                        logger.info("Stale PID file preserved as %s for slug '%s'", candidate.name, slug)
                        return
                    except OSError:
                        pass
                idx += 1
    except Exception as exc:
        logger.warning("Could not rename stale PID file %s: %s", pid_file, exc)


def wait_for_worker_pid(
    pid_file: Path,
    proc: Any,
    deadline_s: float = 3.0,
    expected_worker_id: str | None = None,
) -> int:
    """Poll for worker.pid until deadline; return discovered or fallback PID."""
    import time
    real_pid = proc.pid if proc is not None else 0
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        if pid_file.exists():
            try:
                pid_data = json.loads(pid_file.read_text(encoding="utf-8"))
                found_id = pid_data.get("worker_id")
                if expected_worker_id is None or found_id == expected_worker_id:
                    return int(pid_data["pid"])
            except (json.JSONDecodeError, KeyError, ValueError, OSError):
                pass
        if proc is not None and proc.poll() is not None:
            break
        time.sleep(0.05)
    return real_pid


def stop_worker_process(
    entry: Any,
    pid: int,
    name: str,
    stop_timeout_s: float = 20.0,
    proj_dir: Path | None = None,
    send_stop_fn: Any = None,
    send_stop_pid_fn: Any = None,
    wait_for_pid_fn: Any = None,
) -> StopResult:
    """
    Execute the safe worker stop escalation pipeline:
      1. worker control STOP
         ↓ wait up to 20s
      2. stop.cmd fallback
         ↓ wait fallback window
      3. terminate
         ↓ wait terminate window
      4. kill
    """
    def _is_alive() -> bool:
        if _PSUTIL_AVAILABLE and pid:
            try: return psutil.pid_exists(pid) and psutil.Process(pid).is_running()
            except (psutil.NoSuchProcess, Exception): return False
        if getattr(entry, "is_alive", None) is not None:
            try: return bool(entry.is_alive())
            except Exception: pass
        if getattr(entry, "proc", None) is not None: return entry.proc.poll() is None
        if pid:
            try:
                os.kill(pid, 0)
                return True
            except (OSError, ProcessLookupError): return False
        return False

    if not _is_alive():
        return StopResult(exit_code=getattr(entry, "exit_code", 0), escalated=False, clean=True, method="already_dead")

    resolved_proj_dir = proj_dir
    if resolved_proj_dir is None and getattr(entry, "log_path", None) is not None:
        resolved_proj_dir = Path(entry.log_path).parent

    # Stage 1: Graceful control via worker control STOP
    if resolved_proj_dir is not None and resolved_proj_dir.exists():
        ctrl_file = resolved_proj_dir / "worker.control"
        try:
            tmp_ctrl = ctrl_file.with_suffix(".tmp")
            tmp_ctrl.write_text(json.dumps({"command": "STOP", "ts": utc_now_iso()}), encoding="utf-8")
            tmp_ctrl.replace(ctrl_file)
        except Exception as exc:
            logger.debug("Could not write worker.control for %s: %s", name, exc)

    if sys.platform != "win32":
        try:
            if send_stop_fn and getattr(entry, "proc", None) is not None:
                send_stop_fn(entry.proc)
            elif send_stop_pid_fn and pid:
                send_stop_pid_fn(pid)
        except Exception:
            pass

    def _wait_exit(timeout_s: float) -> bool:
        deadline = time.monotonic() + max(0.05, timeout_s)
        while time.monotonic() < deadline:
            if not _is_alive():
                return True
            time.sleep(0.05)
        return not _is_alive()

    # Stage 1: Graceful control wait (up to 20s)
    if _wait_exit(stop_timeout_s):
        return StopResult(exit_code=getattr(entry, "exit_code", 0), escalated=False, clean=True, method="graceful_control")

    # Stage 2: Escalation to stop.cmd fallback
    logger.warning("Worker '%s' (PID %s) unresponsive after %.1fs — escalating to stop.cmd", name, pid, stop_timeout_s)
    if resolved_proj_dir is not None and resolved_proj_dir.exists():
        try:
            cmd_file = resolved_proj_dir / "stop.cmd"
            tmp_cmd = cmd_file.with_suffix(".tmp")
            tmp_cmd.write_text(f"STOP\n{utc_now_iso()}\n", encoding="utf-8")
            tmp_cmd.replace(cmd_file)
        except Exception as exc:
            logger.debug("Could not write stop.cmd: %s", exc)

    if _wait_exit(min(max(0.1, stop_timeout_s * 0.15), 2.0)):
        return StopResult(exit_code=getattr(entry, "exit_code", 0), escalated=True, clean=False, method="stop_cmd")

    # Stage 3: Escalation to terminate
    logger.warning("Worker '%s' (PID %s) unresponsive after stop.cmd — escalating to terminate", name, pid)
    if _is_alive():
        if _PSUTIL_AVAILABLE and pid:
            try: psutil.Process(pid).terminate()
            except Exception: pass
        elif getattr(entry, "proc", None) is not None:
            try: entry.proc.terminate()
            except Exception: pass
        elif send_stop_pid_fn and pid:
            try: send_stop_pid_fn(pid)
            except Exception: pass

    if _wait_exit(min(max(0.1, stop_timeout_s * 0.15), 2.0)):
        return StopResult(exit_code=getattr(entry, "exit_code", -15), escalated=True, clean=False, method="terminate")

    # Stage 4: Escalation to kill
    logger.warning("Worker '%s' (PID %s) unresponsive after terminate — force-killing", name, pid)
    if _is_alive():
        if _PSUTIL_AVAILABLE and pid:
            try:
                p = psutil.Process(pid)
                p.kill()
                p.wait(timeout=3.0)
            except Exception: pass
        elif getattr(entry, "proc", None) is not None:
            try:
                entry.proc.kill()
                entry.proc.wait(timeout=3.0)
            except Exception: pass
        elif pid:
            try:
                from .spawner import kill_pid
                kill_pid(pid)
            except Exception: pass

    return StopResult(exit_code=-9, escalated=True, clean=False, method="kill")


def discover_active_workers(engine_root: Path, registry: dict[str, Any], worker_entry_cls: Any) -> None:
    """Scan current/ for live worker.pid files and update registry."""
    current_dir = engine_root / "current"
    if not current_dir.exists():
        return

    for proj_dir in sorted(current_dir.iterdir()):
        if not proj_dir.is_dir():
            continue

        slug = _slug(proj_dir.name)
        pid_file = proj_dir / "worker.pid"

        if not pid_file.exists():
            continue

        worker_id = create_time = canonical_target = canonical_project_root = engine_root_val = None
        try:
            raw = pid_file.read_text(encoding="utf-8").strip()
            if raw.startswith("{"):
                data: dict = json.loads(raw)
                pid = int(data["pid"])
                project_name = str(data.get("display_name") or data.get("project", slug))
                target_path = str(data.get("root_path") or data.get("target_path", ""))
                started_at_iso = str(data.get("started_at", ""))
                project_id = str(data.get("project_id", f"proj_{slug}"))
                slug_val = str(data.get("slug", slug))
                if slug_val:
                    slug = _slug(slug_val)
                worker_id = data.get("worker_id")
                create_time = data.get("process_create_time")
                canonical_target = data.get("canonical_target")
                canonical_project_root = data.get("canonical_project_root") or data.get("root_path")
                engine_root_val = data.get("engine_root")
            else:
                lines = raw.splitlines()
                pid = int(lines[0].strip())
                started_at_iso = lines[1].strip() if len(lines) > 1 else ""
                project_name = slug
                target_path = ""
                project_id = f"proj_{slug}"
        except Exception as exc:
            logger.warning("Cannot parse worker.pid in '%s': %s", proj_dir, exc)
            continue

        existing = registry.get(slug)
        if existing is not None:
            if existing.pid == pid and getattr(existing, "worker_id", None) == worker_id and existing.is_alive():
                continue
            logger.debug("Evicting stale registry entry for '%s' (was PID %s, disk says PID %s)", slug, existing.pid, pid)
            registry.pop(slug, None)

        alive = verify_worker_pid(
            pid,
            slug,
            expected_create_time=create_time,
            expected_worker_id=worker_id,
            expected_project_root=canonical_project_root,
            expected_engine_root=engine_root_val or engine_root,
        )
        if alive is False:
            logger.info("Stale PID file for '%s' (PID %s is dead or wrong process)", slug, pid)
            mark_stale_pid(pid_file, slug)
            continue

        entry = worker_entry_cls(
            name=project_name,
            target_path=target_path,
            pid=pid,
            log_path=proj_dir / "worker.log",
            started_at_iso=started_at_iso,
            project_id=project_id,
            slug=slug,
            display_name=project_name,
            worker_id=worker_id,
            process_create_time=create_time,
            canonical_target=canonical_target,
            engine_root=engine_root_val,
            canonical_project_root=canonical_project_root,
        )

        status_data = read_status_file(proj_dir)
        if status_data.get("session_id") is not None:
            entry.session_id = int(status_data["session_id"])

        registry[slug] = entry
        logger.info("Reconciled worker '%s' (PID %s, worker_id=%s, started %s)", slug, pid, worker_id, started_at_iso or "unknown")
