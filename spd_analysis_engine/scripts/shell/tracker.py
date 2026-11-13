"""
scripts/shell/tracker.py
========================
Background process execution auditing and terminal tracking daemon.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import psutil

try:
    from .classifier import (
        is_ignored_command,
        detect_executor,
        SHELL_HOST_NAMES,
        IGNORED_COMMANDS,
    )
    from .cmd_parser import (
        scrub_tokens,
        _utcnow_iso,
        _is_path_inside_target,
        _detect_file_reads_from_cmd,
    )
    from ..ide_profiles import is_process_whitelisted
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.shell.classifier import (
            is_ignored_command,
            detect_executor,
            SHELL_HOST_NAMES,
            IGNORED_COMMANDS,
        )
        from spd_analysis_engine.scripts.shell.cmd_parser import (
            scrub_tokens,
            _utcnow_iso,
            _is_path_inside_target,
            _detect_file_reads_from_cmd,
        )
        from spd_analysis_engine.scripts.ide_profiles import is_process_whitelisted
    except (ImportError, ModuleNotFoundError):
        from spd_analysis_engine.scripts.shell.classifier import (
            is_ignored_command,
            detect_executor,
            SHELL_HOST_NAMES,
            IGNORED_COMMANDS,
        )
        from spd_analysis_engine.scripts.shell.cmd_parser import (
            scrub_tokens,
            _utcnow_iso,
            _is_path_inside_target,
            _detect_file_reads_from_cmd,
        )
        try:
            from spd_analysis_engine.scripts.ide_profiles import is_process_whitelisted
        except (ImportError, ModuleNotFoundError):
            def is_process_whitelisted(proc: Any, profile_name: str = "antigravity", custom_marker: str | None = None) -> bool:
                return True

if TYPE_CHECKING:
    from spd_analysis_engine.scripts.storage_rotator import SessionDB

logger = logging.getLogger(__name__)


class ExecutionTracker:
    """
    Audits and records terminal executions within a workspace directory.

    Parameters
    ----------
    target_path : Path | str
        Root workspace path being monitored.
    session_db : SessionDB | None
        Open SessionDB instance for inserting EXEC event rows.
    session_id : int | None
        Active session identifier.
    poll_interval : float
        Interval for background shell process scanning (default: 1.2s).
    """

    def __init__(
        self,
        target_path: Path | str | None = None,
        session_db: SessionDB | None = None,
        session_id: int | None = None,
        poll_interval: float = 1.2,
        idle_interval: float | None = None,
        fast_interval: float = 0.18,
        is_active_callback: Callable[[], bool] | None = None,
        target_dir: Path | str | None = None,
        db: SessionDB | None = None,
        on_command: Callable[[dict[str, Any]], None] | None = None,
        on_read: Callable[[dict[str, Any]], None] | None = None,
        ide_profile: str = "antigravity",
        custom_marker: str | None = None,
    ) -> None:
        real_target = target_path if target_path is not None else target_dir
        if real_target is None:
            real_target = Path.cwd()
        self.target_path = Path(real_target).resolve()
        self.session_db = session_db if session_db is not None else db
        self.session_id = session_id

        self.idle_interval = idle_interval if idle_interval is not None else poll_interval
        self.poll_interval = self.idle_interval
        self.fast_interval = fast_interval
        self.is_active_callback = is_active_callback
        self._fast_poll_until: float = 0.0

        self.on_command = on_command
        self.on_read = on_read
        self.ide_profile = ide_profile
        self.custom_marker = custom_marker

        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        # TTL-based seen set mapping pid -> timestamp to avoid clearing entire set
        self.seen_ttl_s: float = 300.0
        self._seen_pids: dict[int, float] = {}
        # Cache (pid, create_time) -> metadata to avoid repeated cwd() / cmdline() calls
        self._proc_cache: dict[tuple[int, float], dict[str, Any]] = {}
        self._recent_executions: list[dict[str, Any]] = []

        # Scan performance and adaptive interval tracking
        self.last_scan_duration_s: float = 0.0
        self.scan_count: int = 0
        self.total_scan_duration_s: float = 0.0
        self.last_scan_time: float = 0.0
        self._current_interval: float = self.idle_interval

    def get_scan_stats(self) -> dict[str, Any]:
        """Return scan duration, frequency and adaptive interval metrics."""
        with self._lock:
            avg_dur = (self.total_scan_duration_s / self.scan_count) if self.scan_count > 0 else 0.0
            return {
                "scan_count": self.scan_count,
                "last_scan_duration_s": round(self.last_scan_duration_s, 5),
                "total_scan_duration_s": round(self.total_scan_duration_s, 5),
                "avg_scan_duration_s": round(avg_dur, 5),
                "current_interval": round(self._current_interval, 3),
                "is_fast_polling": self.is_fast_polling,
            }

    def trigger_fast_poll(self, duration_s: float = 3.5) -> None:
        """Temporarily accelerate polling to fast_interval (150ms-200ms) for duration_s."""
        expiry = time.monotonic() + duration_s
        with self._lock:
            if expiry > self._fast_poll_until:
                self._fast_poll_until = expiry

    @property
    def is_fast_polling(self) -> bool:
        """Return True if fast polling (150ms-200ms) is active."""
        if time.monotonic() < self._fast_poll_until:
            return True
        if self.is_active_callback:
            try:
                return bool(self.is_active_callback())
            except Exception:
                return False
        return False

    def set_session(self, session_db: SessionDB, session_id: int) -> None:
        """Bind or update active SessionDB reference."""
        with self._lock:
            self.session_db = session_db
            self.session_id = session_id

    def _detect_file_reads_from_cmd(self, cmd_str: str) -> list[str]:
        """Parse command line strings for referenced workspace files and emit READ events."""
        return _detect_file_reads_from_cmd(cmd_str, self.target_path)

    def _is_path_inside_target(self, path_str: str) -> bool:
        """Check if *path_str* is inside self.target_path."""
        return _is_path_inside_target(path_str, self.target_path)

    def record_execution(
        self,
        command: str,
        duration_s: float = 0.0,
        exit_code: int = 0,
        cwd: str | None = None,
        executor: str | None = None,
    ) -> int | None:
        """
        Record a command execution directly into SessionDB and the recent buffer.
        """
        raw_cmd = str(command).strip()
        if not raw_cmd or is_ignored_command(raw_cmd):
            return None

        clean_cmd = scrub_tokens(raw_cmd)
        if not executor:
            executor = detect_executor(clean_cmd)

        event_id = None
        summary = f"[{executor}] cmd: '{clean_cmd}' | exit={exit_code} | duration={duration_s:.2f}s"
        ts = _utcnow_iso()

        with self._lock:
            if self.session_db is not None and self.session_id is not None:
                try:
                    event_id = self.session_db.record_event(
                        session_id=self.session_id,
                        event_type="EXEC",
                        first_file=clean_cmd,
                        summary=summary,
                        executor=executor,
                    )
                except Exception as exc:
                    logger.error("Failed to insert EXEC event in SessionDB: %s", exc)

            record = {
                "id": event_id,
                "event_type": "EXEC",
                "command": clean_cmd,
                "duration_s": round(duration_s, 2),
                "exit_code": exit_code,
                "cwd": cwd or str(self.target_path),
                "timestamp": ts,
                "summary": summary,
                "executor": executor,
            }
            self._recent_executions.append(record)
            logger.info("Recorded EXEC event: %s", summary)

        if self.on_command:
            try:
                self.on_command(record)
            except Exception as exc:
                logger.debug("Error in on_command callback: %s", exc)

        # Detect referenced files in workspace and record READ events with observation source 'cmd_ref'
        referenced_files = self._detect_file_reads_from_cmd(clean_cmd)
        for ref_f in referenced_files:
            try:
                read_record = {
                    "event_type": "READ",
                    "file_path": ref_f,
                    "source": "cmd_ref",
                    "process_name": "cmd_reference",
                    "pid": os.getpid(),
                    "timestamp": ts,
                    "summary": f"[cmd_ref] File referenced in command: {clean_cmd}",
                }
                if self.on_read:
                    self.on_read(read_record)
                elif self.session_db is not None and self.session_id is not None:
                    self.session_db.record_event(
                        session_id=self.session_id,
                        event_type="READ",
                        first_file=ref_f,
                        summary=f"[cmd_ref] Inspected by command: {clean_cmd}",
                        executor="cmd_ref",
                    )
            except Exception as read_exc:
                logger.debug("Failed recording command-derived READ event: %s", read_exc)

        return event_id

    def _scan_processes_once(self) -> bool:
        """Inspect running child processes of shells located inside target_path."""
        t_start = time.monotonic()
        my_pid = os.getpid()
        now = time.monotonic()
        found_activity = False

        try:
            # Prune expired seen PIDs (TTL-based)
            cutoff = now - self.seen_ttl_s
            with self._lock:
                expired = [p for p, ts in self._seen_pids.items() if ts < cutoff]
                for p in expired:
                    del self._seen_pids[p]

            # Light prune of proc cache if large
            if len(self._proc_cache) > 1000:
                self._proc_cache = {k: v for k, v in list(self._proc_cache.items())[-500:]}

            for proc in psutil.process_iter(["pid", "name", "create_time"]):
                if self._stop_event.is_set():
                    break

                try:
                    pid = proc.info.get("pid")
                    create_time = proc.info.get("create_time", 0.0)
                    if not pid or pid == my_pid:
                        continue

                    with self._lock:
                        if pid in self._seen_pids and (now - self._seen_pids[pid] < self.seen_ttl_s):
                            continue

                    name = proc.info.get("name") or ""
                    if name.lower() in IGNORED_COMMANDS:
                        continue

                    proc_key = (pid, create_time)
                    cached = self._proc_cache.get(proc_key)

                    if cached is not None:
                        if not cached.get("is_candidate"):
                            continue
                        cwd = cached.get("cwd")
                        cmdline = cached.get("cmdline") or [name]
                    else:
                        # Unseen process instance: inspect and cache to avoid repeated cwd()/cmdline() calls
                        try:
                            exe = proc.exe()
                            if exe and is_ignored_command(exe):
                                self._proc_cache[proc_key] = {"is_candidate": False}
                                continue
                        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                            pass

                        if not is_process_whitelisted(proc, self.ide_profile, self.custom_marker):
                            self._proc_cache[proc_key] = {"is_candidate": False}
                            continue

                        try:
                            cwd = proc.cwd()
                        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                            cwd = None

                        if not cwd or not self._is_path_inside_target(cwd):
                            self._proc_cache[proc_key] = {"is_candidate": False}
                            continue

                        try:
                            cmdline = proc.cmdline() or [name]
                        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                            cmdline = [name]

                        self._proc_cache[proc_key] = {
                            "is_candidate": True,
                            "cwd": cwd,
                            "cmdline": cmdline,
                        }

                    cmd_str = scrub_tokens(" ".join(cmdline))
                    if "project_worker.py" in cmd_str or "web_server.py" in cmd_str or is_ignored_command(cmd_str):
                        continue

                    with self._lock:
                        self._seen_pids[pid] = now

                    executor = detect_executor(cmd_str, proc, ide_profile=self.ide_profile)

                    t0 = time.monotonic()
                    exit_code = 0
                    try:
                        proc.wait(timeout=0.2)
                        exit_code = proc.returncode if proc.returncode is not None else 0
                    except (psutil.TimeoutExpired, psutil.NoSuchProcess):
                        pass

                    duration = time.monotonic() - t0
                    self.record_execution(
                        command=cmd_str,
                        duration_s=duration,
                        exit_code=exit_code,
                        cwd=cwd,
                        executor=executor,
                    )
                    found_activity = True

                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
                    continue
                except Exception as exc:
                    logger.debug("Error inspecting process execution: %s", exc)
                    continue

            return found_activity
        finally:
            dur = time.monotonic() - t_start
            with self._lock:
                self.last_scan_duration_s = dur
                self.scan_count += 1
                self.total_scan_duration_s += dur
                self.last_scan_time = time.monotonic()

    def _run_loop(self) -> None:
        logger.info(
            "ExecutionTracker started observing %s (idle=%.2fs, fast=%.2fs)",
            self.target_path,
            self.idle_interval,
            self.fast_interval,
        )
        self._current_interval = self.idle_interval
        consecutive_idle = 0

        while not self._stop_event.is_set():
            found_activity = self._scan_processes_once()

            if not self.is_fast_polling:
                self._current_interval = self.idle_interval
                consecutive_idle = 0
            else:
                if found_activity:
                    self._current_interval = self.fast_interval
                    consecutive_idle = 0
                else:
                    consecutive_idle += 1
                    # Adaptively scale up so we do NOT scan at 0.18s indefinitely
                    backoff = self.fast_interval * (1.3 ** min(consecutive_idle, 8))
                    self._current_interval = min(self.idle_interval, max(self.fast_interval, backoff))

            self._stop_event.wait(self._current_interval)

    def start(self) -> None:
        """Start the background process execution scanner."""
        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Stop and join the tracker thread."""
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            self._thread = None
        logger.info("ExecutionTracker stopped on %s", self.target_path)

    def get_recent_executions(self, clear: bool = False) -> list[dict[str, Any]]:
        """Return buffered execution records."""
        with self._lock:
            records = list(self._recent_executions)
            if clear:
                self._recent_executions.clear()
            return records
