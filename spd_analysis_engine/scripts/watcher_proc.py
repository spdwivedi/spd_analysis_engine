"""
scripts/watcher_proc.py
=======================
Low-overhead process handle inspector using psutil for the SPD Analysis Engine.

Tracks files actively opened/read by developer IDEs and runtimes (Cursor, Code,
Antigravity, Windsurf) within the monitored workspace path to record what
code files the AI analyzed prior to generating code edits.

Features
--------
1. Low-overhead background thread running adaptive handle scans (idle around 2s, min 0.5s).
2. Deep inspection on newly seen processes; cached bypass for unchanged processes.
3. Restricts open_files() exclusively to allowlisted IDE/application processes on a slower cadence.
4. Non-blocking bounded telemetry queue ensuring capture durability and zero writer blocking.
5. TTL-based handle deduplication to prevent unbounded growth and event flooding.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import psutil

try:
    from spd_analysis_engine.scripts.ide_profiles import is_process_whitelisted
except (ImportError, ModuleNotFoundError):
    try:
        from scripts.ide_profiles import is_process_whitelisted
    except (ImportError, ModuleNotFoundError):
        def is_process_whitelisted(proc: Any, profile_name: str = "antigravity", custom_marker: str | None = None) -> bool:
            return True

logger = logging.getLogger(__name__)

#: Target developer processes to inspect for active file handles
TARGET_PROCESS_NAMES: set[str] = {
    "cursor.exe", "cursor",
    "code.exe", "code",
    "antigravity.exe", "antigravity",
    "python.exe", "python", "python3.exe", "python3", "py.exe",
    "node.exe", "node",
    "windsurf.exe", "windsurf",
    "devenv.exe",
}

#: Whitelisted IDE/application process names that are allowed open_files() calls
IDE_APP_NAMES: set[str] = {
    "cursor.exe", "cursor",
    "code.exe", "code",
    "antigravity.exe", "antigravity",
    "windsurf.exe", "windsurf",
    "devenv.exe",
}

#: Directories ignored during process handle scanning
IGNORED_DIRS: set[str] = {
    ".git", ".sf", ".sfdx", "node_modules", "__pycache__",
    ".venv", "venv", ".idea", ".vscode", "patches", "baseline",
    "shadow_git", ".mypy_cache", ".pytest_cache",
}

#: File extensions ignored during handle inspection
IGNORED_EXTENSIONS: set[str] = {
    ".tmp", ".swp", ".swo", ".pyc", ".pyo", ".bak", ".log",
    ".db", ".db-shm", ".db-wal", ".pid",
}

#: Substring patterns of internal IDE tasks and extensions to filter out from handle inspection
IGNORED_HANDLE_PATTERNS: tuple[str, ...] = (
    "shellintegration.ps1", "jsonservermain", "eslintserver",
    "eslintserver.js", "typescript-language-features", "apex-language-server",
    "git-credential-", "credential-manager", "git-credential-manager",
    "appdata/local/programs/microsoft vs code", "resources/app/extensions",
    "resources/app/out/vs", ".vscode/extensions", ".cursor/extensions",
    ".windsurf/extensions",
)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ProcessInspector:
    """
    Monitors active file read handles of IDE processes within a target directory.

    Avoids hot polling via adaptive intervals, caches unchanged processes,
    and runs expensive open_files() only on allowlisted IDE processes at a slower cadence.
    """

    def __init__(
        self,
        target_path: Path | str,
        on_file_read: Callable[[dict[str, Any]], None] | None = None,
        poll_interval: float = 2.0,
        scan_interval: float | None = None,
        ide_profile: str = "antigravity",
        custom_marker: str | None = None,
        min_interval: float = 0.5,
        idle_interval: float = 2.0,
        open_files_interval: float = 4.0,
        max_queue_size: int = 1000,
    ) -> None:
        self.target_path = Path(target_path).resolve()
        self.on_file_read = on_file_read
        self.min_interval = float(min_interval)
        self.idle_interval = float(idle_interval)
        self.open_files_interval = float(open_files_interval)
        self.poll_interval = scan_interval if scan_interval is not None else poll_interval
        self.ide_profile = ide_profile
        self.custom_marker = custom_marker
        self.max_queue_size = int(max_queue_size)

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        # Telemetry bounded queue & decoupling to never block the event writer
        self.telemetry_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=self.max_queue_size)
        self.drop_count: int = 0
        self._last_event_latency_s: float = 0.0
        self._total_event_latency_s: float = 0.0
        self._dispatched_event_count: int = 0

        # TTL-based seen set mapping (pid, rel_path) -> timestamp
        self.seen_ttl_s: float = 120.0
        self._seen_handles: dict[tuple[int, str], float] = {}

        # Caches to avoid repeated cmdline() and open_files() calls on unchanged processes
        self._proc_cache: dict[tuple[int, float], dict[str, Any]] = {}
        self._proc_last_cpu: dict[tuple[int, float], float] = {}
        self._recent_reads: list[dict[str, Any]] = []

        # Scan metrics & adaptive interval
        self.last_scan_duration_s: float = 0.0
        self.scan_count: int = 0
        self.total_scan_duration_s: float = 0.0
        self.last_scan_time: float = 0.0
        self._last_open_files_ts: float = 0.0
        self._consecutive_idle: int = 0
        self._current_interval: float = max(self.min_interval, self.poll_interval)

    def _is_ide_process(self, name_lower: str) -> bool:
        """Check if executable name matches an allowlisted IDE or application."""
        if name_lower in IDE_APP_NAMES:
            return True
        if self.ide_profile == "claude_code" and name_lower in ("claude.exe", "claude", "node.exe", "node"):
            return True
        if self.custom_marker and self.custom_marker.lower() in name_lower:
            return True
        return False

    def adapt_interval(self, scan_duration: float, found_activity: bool) -> float:
        """Calculate next adaptive interval based on scan duration and activity."""
        duty_interval = scan_duration * 4.0
        if found_activity:
            self._consecutive_idle = 0
            target = max(self.min_interval, duty_interval)
        else:
            self._consecutive_idle += 1
            backoff = self._current_interval * (1.2 ** min(self._consecutive_idle, 4))
            target = min(self.idle_interval * 2.0, max(self.idle_interval, backoff, duty_interval))
        self._current_interval = max(self.min_interval, target)
        return self._current_interval

    def get_scan_stats(self) -> dict[str, Any]:
        """Return scan duration, frequency, queue size, and adaptive interval metrics."""
        with self._lock:
            avg_dur = (self.total_scan_duration_s / self.scan_count) if self.scan_count > 0 else 0.0
            avg_lat = (self._total_event_latency_s / self._dispatched_event_count) if self._dispatched_event_count > 0 else 0.0
            return {
                "scan_count": self.scan_count,
                "last_scan_duration_s": round(self.last_scan_duration_s, 5),
                "total_scan_duration_s": round(self.total_scan_duration_s, 5),
                "avg_scan_duration_s": round(avg_dur, 5),
                "current_interval": round(self._current_interval, 3),
                "queue_size": self.telemetry_queue.qsize(),
                "drop_count": self.drop_count,
                "last_event_latency_s": round(self._last_event_latency_s, 6),
                "avg_event_latency_s": round(avg_lat, 6),
            }

    def _is_ignored(self, path: Path) -> bool:
        """Check if *path* matches any ignore rule."""
        for part in path.parts:
            if part in IGNORED_DIRS or part.startswith(".sf"):
                return True
        name = path.name
        if name.startswith("catalog.json") or name.endswith(".__staging__") or name.endswith(".tmp"):
            return True
        if path.suffix.lower() in IGNORED_EXTENSIONS:
            return True
        if name in ("session.db", "worker.log", "worker.pid", "project.meta"):
            return True
        path_str = str(path).lower().replace("\\", "/")
        for pattern in IGNORED_HANDLE_PATTERNS:
            if pattern in path_str:
                return True
        return False

    def _get_rel_path(self, file_path_str: str) -> str | None:
        """Return relative POSIX path if within target_path and not ignored."""
        try:
            p = Path(file_path_str).resolve()
            if self._is_ignored(p):
                return None
            rel = p.relative_to(self.target_path)
            return str(rel).replace("\\", "/")
        except (ValueError, OSError):
            return None

    def _inspect_once(self, check_open_files: bool = True) -> bool:
        """Scan active processes and inspect their open file handles."""
        t_start = time.monotonic()
        my_pid = os.getpid()
        now = time.monotonic()
        found_handles = False
        batch_reads: list[dict[str, Any]] = []

        try:
            # Prune expired seen handles (TTL-based)
            cutoff = now - self.seen_ttl_s
            with self._lock:
                expired = [k for k, ts in self._seen_handles.items() if ts < cutoff]
                for k in expired:
                    del self._seen_handles[k]

            # Prune proc caches if large
            if len(self._proc_cache) > 500:
                self._proc_cache = {k: v for k, v in list(self._proc_cache.items())[-250:]}
            if len(self._proc_last_cpu) > 500:
                self._proc_last_cpu = {k: v for k, v in list(self._proc_last_cpu.items())[-250:]}

            for proc in psutil.process_iter(["pid", "name", "create_time"]):
                if self._stop_event.is_set():
                    break

                try:
                    name = proc.info.get("name") or ""
                    pid = proc.info.get("pid")
                    create_time = proc.info.get("create_time", 0.0)
                    if not pid or pid == my_pid:
                        continue

                    name_lower = name.lower()
                    if name_lower not in TARGET_PROCESS_NAMES:
                        continue

                    proc_key = (pid, create_time)
                    cached = self._proc_cache.get(proc_key)

                    if cached is not None:
                        # Repeated process: avoid repeated expensive calls
                        if not cached.get("is_candidate"):
                            continue
                    else:
                        # Newly seen process: deep inspection once
                        is_ide = self._is_ide_process(name_lower)
                        if not is_process_whitelisted(proc, self.ide_profile, self.custom_marker):
                            self._proc_cache[proc_key] = {"is_candidate": False, "is_ide": is_ide}
                            continue

                        is_candidate = True
                        try:
                            cmdline = proc.cmdline()
                            cmd_str = " ".join(cmdline).lower()
                            if any(p in cmd_str for p in IGNORED_HANDLE_PATTERNS):
                                is_candidate = False
                        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                            pass

                        try:
                            exe_path = proc.exe().lower().replace("\\", "/")
                            if any(p in exe_path for p in IGNORED_HANDLE_PATTERNS):
                                is_candidate = False
                        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                            pass

                        self._proc_cache[proc_key] = {
                            "is_candidate": is_candidate,
                            "is_ide": is_ide,
                        }
                        cached = self._proc_cache[proc_key]
                        if not is_candidate:
                            continue

                    # Check process CPU usage to avoid unnecessary calls on idle processes
                    try:
                        cpu_times = proc.cpu_times()
                        total_cpu = cpu_times.user + cpu_times.system
                    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                        total_cpu = None

                    if total_cpu is not None and proc_key in self._proc_last_cpu:
                        last_cpu = self._proc_last_cpu[proc_key]
                        if abs(total_cpu - last_cpu) < 0.0001:
                            # Process has been idle since last check — skip!
                            continue

                    if total_cpu is not None:
                        self._proc_last_cpu[proc_key] = total_cpu

                    # open_files() ONLY for allowlisted IDE/application processes AND when cadence allows
                    if not cached.get("is_ide"):
                        continue
                    if not check_open_files:
                        continue

                    try:
                        open_files = proc.open_files()
                    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                        open_files = None

                    if not open_files:
                        continue

                    for f in open_files:
                        rel_path = self._get_rel_path(f.path)
                        if not rel_path:
                            continue

                        handle_key = (pid, rel_path)
                        with self._lock:
                            if handle_key in self._seen_handles and (now - self._seen_handles[handle_key] < self.seen_ttl_s):
                                continue

                            self._seen_handles[handle_key] = now

                            read_record = {
                                "event_type": "READ",
                                "file_path": rel_path,
                                "source": "open_handle",
                                "process_name": name,
                                "pid": pid,
                                "timestamp": _utcnow_iso(),
                                "summary": f"[open_handle] File observed via handle open: {name} (PID {pid})",
                            }
                            self._recent_reads.append(read_record)
                            found_handles = True
                            batch_reads.append(read_record)

                            logger.debug("Detected file read by %s (PID %s): %s", name, pid, rel_path)

                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
                    continue
                except Exception as exc:
                    logger.debug("Unexpected error during process inspect: %s", exc)
                    continue

            # Non-blocking telemetry delivery outside the lock to prevent blocking
            for record in batch_reads:
                try:
                    self.telemetry_queue.put_nowait(record)
                except queue.Full:
                    with self._lock:
                        self.drop_count += 1
                    logger.debug("Telemetry queue full; dropped record for %s", record.get("file_path"))

                if self.on_file_read is not None:
                    try:
                        t_cb = time.monotonic()
                        self.on_file_read(record)
                        lat = time.monotonic() - t_cb
                        with self._lock:
                            self._last_event_latency_s = lat
                            self._total_event_latency_s += lat
                            self._dispatched_event_count += 1
                    except Exception as cb_exc:
                        logger.debug("Error in on_file_read callback (isolated): %s", cb_exc)

            return found_handles
        finally:
            dur = time.monotonic() - t_start
            with self._lock:
                self.last_scan_duration_s = dur
                self.scan_count += 1
                self.total_scan_duration_s += dur
                self.last_scan_time = time.monotonic()

    def _run_loop(self) -> None:
        """Background scanning loop with adaptive intervals and duration tracking."""
        logger.info(
            "ProcessInspector started observing %s (idle=%.1fs, min=%.1fs, open_files=%.1fs)",
            self.target_path, self.idle_interval, self.min_interval, self.open_files_interval,
        )
        self._current_interval = max(self.min_interval, self.poll_interval)
        self._last_open_files_ts = 0.0

        while not self._stop_event.is_set():
            now = time.monotonic()
            due_open_files = (self.open_files_interval <= 0) or ((now - self._last_open_files_ts) >= self.open_files_interval)
            try:
                found_handles = self._inspect_once(check_open_files=due_open_files)
                if due_open_files:
                    self._last_open_files_ts = time.monotonic()
            except Exception as loop_exc:
                logger.debug("Error in inspector loop cycle (isolated): %s", loop_exc)
                found_handles = False

            self.adapt_interval(self.last_scan_duration_s, found_handles)
            self._stop_event.wait(self._current_interval)

    def start(self) -> None:
        """Start the background inspector thread."""
        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Stop and join the inspector thread."""
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            self._thread = None
        logger.info("ProcessInspector stopped on %s", self.target_path)

    def get_recent_reads(self, clear: bool = False) -> list[dict[str, Any]]:
        """Return buffered read records since last check."""
        with self._lock:
            records = list(self._recent_reads)
            if clear:
                self._recent_reads.clear()
            return records
