"""
scripts/core/spawner.py
=======================
Detached process spawning routines and process termination helpers for SPD workers.
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
    from .worker_entry import _PSUTIL_AVAILABLE
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.core.worker_entry import _PSUTIL_AVAILABLE
    except (ImportError, ModuleNotFoundError):
        try:
            from spd_analysis_engine.scripts.core.worker_entry import _PSUTIL_AVAILABLE
        except (ImportError, ModuleNotFoundError):
            try:
                import psutil
                _PSUTIL_AVAILABLE = True
            except ImportError:
                _PSUTIL_AVAILABLE = False

if _PSUTIL_AVAILABLE:
    import psutil

logger = logging.getLogger(__name__)

#: Seconds to wait for worker to exit gracefully before force-killing.
_STOP_TIMEOUT_S: float = 10.0

#: Seconds to wait for ``worker.pid`` to appear after spawning.
_BOOT_TIMEOUT_S: float = 8.0


import base64
import uuid


class SpawnResult(tuple):
    """Tuple subclass (proc, pid) preserving backward-compatibility while exposing launch details."""
    def __new__(
        cls,
        proc: subprocess.Popen | None,
        pid: int | None,
        strategy: str = "unknown",
        worker_id: str = "",
    ) -> SpawnResult:
        inst = super().__new__(cls, (proc, pid))
        inst.proc = proc
        inst.pid = pid
        inst.strategy = strategy
        inst.launch_strategy = strategy
        inst.worker_id = worker_id
        return inst


def find_worker_pid_by_worker_id(worker_id: str) -> int | None:
    """Find the OS PID of a running worker process with the specified worker_id."""
    if not _PSUTIL_AVAILABLE or not worker_id:
        return None
    from .worker_helpers import _tokenize_cmdline, _extract_flag_value

    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            pid = p.info.get("pid")
            if not pid:
                continue
            cmdline = p.info.get("cmdline") or []
            if not cmdline:
                continue
            tokens = _tokenize_cmdline(cmdline)
            if not any("project_worker" in str(t).lower() for t in tokens):
                continue
            val_id = _extract_flag_value(tokens, "--worker-id")
            if val_id == worker_id:
                return pid
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        except Exception:
            pass
    return None


def build_worker_cmd(
    py_exe: str,
    worker_script: Path,
    name: str,
    path: str | Path,
    engine_root: Path | str,
    scaffold: bool = True,
    debounce: float = 3.5,
    track_reads: bool = True,
    track_exec: bool = True,
    shadow_git: bool = True,
    git_init_primary: bool = False,
    ide_profile: str = "antigravity",
    ide_custom_marker: str | None = None,
    is_new_project: bool = False,
    worker_id: str | None = None,
) -> list[str]:
    """Build the argument list for invoking project_worker.py."""
    cmd = [
        py_exe,
        str(worker_script),
        "--name", name,
        "--path", str(Path(path).resolve()),
        "--engine-root", str(Path(engine_root).resolve()),
    ]
    if worker_id and str(worker_id).strip():
        cmd.extend(["--worker-id", str(worker_id).strip()])
    if scaffold:
        cmd.append("--scaffold")
    if is_new_project:
        cmd.append("--is-new-project")
    if debounce != 3.5:
        cmd.extend(["--debounce", str(debounce)])
    if not track_reads:
        cmd.append("--no-track-reads")
    if not track_exec:
        cmd.append("--no-track-exec")
    if not shadow_git:
        cmd.append("--no-shadow-git")
    if git_init_primary:
        cmd.append("--git-init-primary")
    if ide_profile:
        cmd.extend(["--ide-profile", ide_profile])
    if ide_custom_marker:
        cmd.extend(["--ide-custom-marker", ide_custom_marker])
    return cmd


def spawn_detached_worker(
    py_exe: str | None = None,
    worker_script: Path | None = None,
    name: str | None = None,
    path: str | Path | None = None,
    engine_root: Path | str | None = None,
    log_path: Path | None = None,
    scaffold: bool = True,
    debounce: float = 3.5,
    track_reads: bool = True,
    track_exec: bool = True,
    shadow_git: bool = True,
    git_init_primary: bool = False,
    ide_profile: str = "antigravity",
    ide_custom_marker: str | None = None,
    is_new_project: bool = False,
    worker_id: str | None = None,
    **kwargs: Any,
) -> SpawnResult:
    """
    Launch a detached worker subprocess across Windows and POSIX platforms.

    Returns
    -------
    SpawnResult(proc, spawned_pid)
        Unpacks cleanly as (proc, pid) while exposing .launch_strategy and .worker_id.
    """
    effective_name = str(name or kwargs.get("project_name") or "").strip()
    raw_path = path if path is not None else kwargs.get("target_path")
    effective_path = str(Path(raw_path).resolve()) if raw_path else ""
    effective_root = Path(engine_root).resolve() if engine_root else Path.cwd().resolve()
    effective_worker_id = str(worker_id).strip() if worker_id else str(uuid.uuid4())

    effective_py_exe = py_exe
    if not effective_py_exe:
        effective_py_exe = sys.executable
        if sys.platform == "win32":
            cand = Path(sys.executable).parent / "pythonw.exe"
            if cand.exists():
                effective_py_exe = str(cand)

    effective_script = worker_script
    if not effective_script:
        effective_script = Path(__file__).resolve().parent.parent / "project_worker.py"

    effective_log = log_path
    if not effective_log:
        from ..storage_rotator import _slug
        effective_log = effective_root / "current" / _slug(effective_name) / "worker.log"
    effective_log.parent.mkdir(parents=True, exist_ok=True)

    cmd = build_worker_cmd(
        py_exe=effective_py_exe,
        worker_script=effective_script,
        name=effective_name,
        path=effective_path,
        engine_root=effective_root,
        scaffold=scaffold,
        debounce=debounce,
        track_reads=track_reads,
        track_exec=track_exec,
        shadow_git=shadow_git,
        git_init_primary=git_init_primary,
        ide_profile=ide_profile,
        ide_custom_marker=ide_custom_marker,
        is_new_project=is_new_project,
        worker_id=effective_worker_id,
    )

    log_fd = open(effective_log, "a", encoding="utf-8", buffering=1)
    proc: subprocess.Popen | None = None
    spawned_pid: int | None = None
    launch_strategy: str = "unknown"

    worker_env = {
        k: v for k, v in os.environ.items()
        if not (k.endswith("_API_KEY") or "GEMINI" in k or "OPENAI" in k or "ANTHROPIC" in k)
    }

    try:
        if sys.platform == "win32":
            log_fd.close()

            # Strategy 1: Popen with DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP + CREATE_BREAKAWAY_FROM_JOB + CREATE_NO_WINDOW
            flags_breakaway = (
                subprocess.DETACHED_PROCESS
                | subprocess.CREATE_NEW_PROCESS_GROUP
                | 0x01000000  # CREATE_BREAKAWAY_FROM_JOB
                | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            )
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=flags_breakaway,
                    close_fds=True,
                    env=worker_env,
                )
                spawned_pid = proc.pid
                launch_strategy = "popen_detached_breakaway"
                logger.info("Worker launched using strategy '%s' (PID %s, worker_id=%s)", launch_strategy, spawned_pid, effective_worker_id)
            except (PermissionError, OSError) as exc:
                logger.debug("Popen breakaway failed (%s), attempting Popen detached without breakaway", exc)
                try:
                    flags_detached = (
                        subprocess.DETACHED_PROCESS
                        | subprocess.CREATE_NEW_PROCESS_GROUP
                        | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                    )
                    proc = subprocess.Popen(
                        cmd,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=flags_detached,
                        close_fds=True,
                        env=worker_env,
                    )
                    spawned_pid = proc.pid
                    launch_strategy = "popen_detached"
                    logger.info("Worker launched using strategy '%s' (PID %s, worker_id=%s)", launch_strategy, spawned_pid, effective_worker_id)
                except Exception as exc2:
                    logger.debug("Popen detached failed (%s), falling back to WMI", exc2)

            # Strategy 2: Last Fallback: WMI with encoded PowerShell
            if spawned_pid is None:
                cmd_line = subprocess.list2cmdline(cmd)
                b64_cmd = base64.b64encode(cmd_line.encode("utf-8")).decode("ascii")
                ps_script = (
                    f'$b64 = "{b64_cmd}"\n'
                    '$bytes = [System.Convert]::FromBase64String($b64)\n'
                    '$cmdLine = [System.Text.Encoding]::UTF8.GetString($bytes)\n'
                    '$res = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine = $cmdLine}\n'
                    'if ($res.ReturnValue -eq 0) { Write-Host $res.ProcessId }\n'
                    'else { exit $res.ReturnValue }\n'
                )
                ps_encoded = base64.b64encode(ps_script.encode("utf-16le")).decode("ascii")
                no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                try:
                    res = subprocess.run(
                        ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", ps_encoded],
                        capture_output=True,
                        text=True,
                        check=True,
                        timeout=8.0,
                        creationflags=no_window,
                        shell=False,
                    )
                    out = res.stdout.strip()
                    if out.isdigit():
                        spawned_pid = int(out)
                        launch_strategy = "wmi_encoded_powershell"
                        logger.info("Worker launched using strategy '%s' (PID %s, worker_id=%s)", launch_strategy, spawned_pid, effective_worker_id)
                    else:
                        dup_pid = find_worker_pid_by_worker_id(effective_worker_id)
                        if dup_pid:
                            spawned_pid = dup_pid
                            launch_strategy = "wmi_detected_duplicate"
                            logger.info("Worker detected after WMI launch using strategy '%s' (PID %s, worker_id=%s)", launch_strategy, spawned_pid, effective_worker_id)
                        else:
                            raise RuntimeError(f"WMI process creation returned: {res.stderr or out}")
                except subprocess.TimeoutExpired:
                    logger.warning("WMI launch timed out; checking for duplicate/delayed successful spawn for worker_id=%s", effective_worker_id)
                    dup_pid = find_worker_pid_by_worker_id(effective_worker_id)
                    if dup_pid:
                        spawned_pid = dup_pid
                        launch_strategy = "wmi_timeout_recovered"
                        logger.info("Worker successfully detected after timeout using strategy '%s' (PID %s, worker_id=%s)", launch_strategy, spawned_pid, effective_worker_id)
                    else:
                        raise
                except Exception as exc:
                    dup_pid = find_worker_pid_by_worker_id(effective_worker_id)
                    if dup_pid:
                        spawned_pid = dup_pid
                        launch_strategy = "wmi_error_recovered"
                        logger.info("Worker successfully detected after error using strategy '%s' (PID %s, worker_id=%s)", launch_strategy, spawned_pid, effective_worker_id)
                    else:
                        logger.error("All Windows detached spawn methods failed: %s", exc)
                        raise RuntimeError(f"Could not spawn worker process on Windows: {exc}") from exc
        else:
            proc = subprocess.Popen(
                cmd,
                stdout=log_fd,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,     # POSIX: new session, detached from TTY
                close_fds=True,
                env=worker_env,
            )
            log_fd.close()
            spawned_pid = proc.pid
            launch_strategy = "posix_session"
            logger.info("Worker launched using strategy '%s' (PID %s, worker_id=%s)", launch_strategy, spawned_pid, effective_worker_id)
    finally:
        try:
            log_fd.close()
        except Exception:
            pass

    return SpawnResult(proc, spawned_pid, strategy=launch_strategy, worker_id=effective_worker_id)


def send_stop(proc: subprocess.Popen) -> None:
    """
    Send a graceful termination signal to a ``Popen`` object.

    Windows: ``proc.terminate()`` calls ``TerminateProcess()`` directly.
    POSIX:   ``SIGTERM``.
    """
    proc.terminate()


def send_stop_pid(pid: int) -> None:
    """
    Send a graceful termination signal to a raw PID (reconciled workers).

    Windows: ``psutil.Process(pid).terminate()`` → ``TerminateProcess()``.
    POSIX:   ``SIGTERM``.
    """
    try:
        if _PSUTIL_AVAILABLE:
            psutil.Process(pid).terminate()
        else:
            if sys.platform != "win32":
                os.kill(pid, signal.SIGTERM)
            else:
                os.kill(pid, signal.SIGTERM)
    except (OSError, ProcessLookupError) as exc:
        logger.debug("Could not send stop signal to PID %s: %s", pid, exc)


def kill_pid(pid: int) -> None:
    """Force-kill a PID when graceful stop times out."""
    try:
        if _PSUTIL_AVAILABLE:
            psutil.Process(pid).kill()
        elif sys.platform == "win32":
            os.kill(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGKILL)  # type: ignore[attr-defined]
    except (OSError, ProcessLookupError) as exc:
        logger.debug("Could not kill PID %s: %s", pid, exc)


def wait_for_pid_exit(pid: int, timeout: float) -> bool:
    """
    Poll until *pid* dies or *timeout* seconds elapse.

    Returns ``True`` if the process exited within the timeout, ``False``
    if it is still alive.
    """
    if _PSUTIL_AVAILABLE:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = max(0.1, deadline - time.monotonic())
            try:
                psutil.Process(pid).wait(timeout=min(0.5, remaining))
                return True
            except psutil.NoSuchProcess:
                return True
            except psutil.TimeoutExpired:
                continue
        return not psutil.pid_exists(pid)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
            time.sleep(0.2)
        except (OSError, ProcessLookupError):
            return True
    return False
