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
            from scripts.core.worker_entry import _PSUTIL_AVAILABLE
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


def build_worker_cmd(
    py_exe: str,
    worker_script: Path,
    name: str,
    path: str,
    engine_root: Path,
    scaffold: bool = True,
    debounce: float = 3.5,
    track_reads: bool = True,
    track_exec: bool = True,
    shadow_git: bool = True,
    git_init_primary: bool = False,
    ide_profile: str = "antigravity",
    ide_custom_marker: str | None = None,
) -> list[str]:
    """Build the argument list for invoking project_worker.py."""
    cmd = [
        py_exe,
        str(worker_script),
        "--name", name,
        "--path", str(Path(path).resolve()),
        "--engine-root", str(engine_root),
    ]
    if scaffold:
        cmd.append("--scaffold")
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
    py_exe: str,
    worker_script: Path,
    name: str,
    path: str,
    engine_root: Path,
    log_path: Path,
    scaffold: bool = True,
    debounce: float = 3.5,
    track_reads: bool = True,
    track_exec: bool = True,
    shadow_git: bool = True,
    git_init_primary: bool = False,
    ide_profile: str = "antigravity",
    ide_custom_marker: str | None = None,
) -> tuple[subprocess.Popen | None, int | None]:
    """
    Launch a detached worker subprocess across Windows and POSIX platforms.

    Returns
    -------
    tuple[subprocess.Popen | None, int | None]
        (proc, spawned_pid). On Windows proc is None and spawned_pid may be set;
        on POSIX proc is a Popen instance.
    """
    cmd = build_worker_cmd(
        py_exe=py_exe,
        worker_script=worker_script,
        name=name,
        path=path,
        engine_root=engine_root,
        scaffold=scaffold,
        debounce=debounce,
        track_reads=track_reads,
        track_exec=track_exec,
        shadow_git=shadow_git,
        git_init_primary=git_init_primary,
        ide_profile=ide_profile,
        ide_custom_marker=ide_custom_marker,
    )

    log_fd = open(log_path, "a", encoding="utf-8", buffering=1)
    proc = None
    spawned_pid: int | None = None

    try:
        if sys.platform == "win32":
            # Close log file handle in parent since worker redirects stdout/stderr internally
            log_fd.close()

            cmd_args = [
                f'"{py_exe}"',
                f'"{worker_script}"',
                "--name", f'"{name}"',
                "--path", f'"{Path(path).resolve()}"',
                "--engine-root", f'"{engine_root}"',
            ]
            if scaffold:
                cmd_args.append("--scaffold")
            if debounce != 3.5:
                cmd_args.extend(["--debounce", str(debounce)])
            if not track_reads:
                cmd_args.append("--no-track-reads")
            if not track_exec:
                cmd_args.append("--no-track-exec")
            if not shadow_git:
                cmd_args.append("--no-shadow-git")
            if git_init_primary:
                cmd_args.append("--git-init-primary")
            if ide_profile:
                cmd_args.extend(["--ide-profile", f'"{ide_profile}"'])
            if ide_custom_marker:
                cmd_args.extend(["--ide-custom-marker", f'"{ide_custom_marker}"'])
            cmd_line = " ".join(cmd_args)

            spawned_ok = False
            no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

            # 1. Primary approach: WMI Win32_Process.Create via PowerShell Invoke-CimMethod.
            try:
                ps_script = (
                    f"$res = Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
                    f"-Arguments @{{CommandLine = '{cmd_line}'}}; "
                    f"if ($res.ReturnValue -eq 0) {{ Write-Host $res.ProcessId }}"
                )
                res = subprocess.run(
                    ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=5.0,
                    creationflags=no_window,
                    shell=False,
                )
                out = res.stdout.strip()
                if out.isdigit():
                    spawned_ok = True
                    spawned_pid = int(out)
                    logger.info("Worker process spawned via WMI (PID %s)", spawned_pid)
            except Exception as exc:
                logger.debug("WMI spawn failed (%s), attempting powershell Start-Process fallback", exc)

            # 2. Fallback: powershell Start-Process
            if not spawned_ok:
                try:
                    arg_list = f'"{worker_script}" --name "{name}" --path "{Path(path).resolve()}" --engine-root "{engine_root}"'
                    if scaffold:
                        arg_list += " --scaffold"
                    if debounce != 3.5:
                        arg_list += f" --debounce {debounce}"
                    if not track_reads:
                        arg_list += " --no-track-reads"
                    if not track_exec:
                        arg_list += " --no-track-exec"
                    if not shadow_git:
                        arg_list += " --no-shadow-git"
                    if git_init_primary:
                        arg_list += " --git-init-primary"
                    ps_cmd = [
                        "powershell", "-NoProfile", "-NonInteractive", "-Command",
                        f"Start-Process '{py_exe}' -ArgumentList '{arg_list}' -WindowStyle Hidden"
                    ]
                    subprocess.run(ps_cmd, check=True, timeout=5.0, creationflags=no_window, shell=False)
                    spawned_ok = True
                except Exception as exc:
                    logger.debug("Start-Process failed (%s), attempting ShellExecuteW fallback", exc)

            # 3. Fallback: ShellExecuteW
            if not spawned_ok:
                try:
                    import ctypes
                    SW_HIDE = 0
                    params = f'"{worker_script}" --name "{name}" --path "{Path(path).resolve()}" --engine-root "{engine_root}"'
                    if scaffold:
                        params += " --scaffold"
                    if debounce != 3.5:
                        params += f" --debounce {debounce}"
                    if not track_reads:
                        params += " --no-track-reads"
                    if not track_exec:
                        params += " --no-track-exec"
                    if not shadow_git:
                        params += " --no-shadow-git"
                    if git_init_primary:
                        params += " --git-init-primary"
                    ret = ctypes.windll.shell32.ShellExecuteW(
                        None, "open", py_exe, params, str(engine_root), SW_HIDE
                    )
                    if ret > 32:
                        spawned_ok = True
                    else:
                        raise RuntimeError(f"ShellExecuteW returned code {ret}")
                except Exception as exc:
                    logger.error("All Windows detached spawn methods failed: %s", exc)
                    raise RuntimeError(f"Could not spawn worker process on Windows: {exc}") from exc

            proc = None
        else:
            proc = subprocess.Popen(
                cmd,
                stdout=log_fd,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,     # POSIX: new session, detached from TTY
                close_fds=True,
            )
            log_fd.close()
    finally:
        try:
            log_fd.close()
        except Exception:
            pass

    return proc, spawned_pid


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
