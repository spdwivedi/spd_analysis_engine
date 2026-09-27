"""
scripts/watcher_proc.py
=======================
Low-overhead process handle inspector using psutil for the SPD Analysis Engine.

Tracks files actively opened/read by developer IDEs and runtimes (Cursor, Code,
Antigravity, Python, Node) within the monitored workspace path to record what
code files the AI analyzed prior to generating code edits.

Features
--------
1. Low-overhead background thread running periodic (1.0s) handle scans.
2. Filters processes targeting developer IDEs and language runtimes.
3. Automatically excludes internal engine files, baselines, and VCS metadata.
4. Deduplicates repeated handle reads to avoid event flooding.
5. Emits structured READ event callbacks and maintains recent reads buffer.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import psutil

try:
    from scripts.ide_profiles import is_process_whitelisted
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.ide_profiles import is_process_whitelisted
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

#: Directories ignored during process handle scanning
IGNORED_DIRS: set[str] = {
    ".git",
    ".sf",
    ".sfdx",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".idea",
    ".vscode",
    "patches",
    "baseline",
    "shadow_git",
    ".mypy_cache",
    ".pytest_cache",
}

#: File extensions ignored during handle inspection
IGNORED_EXTENSIONS: set[str] = {
    ".tmp", ".swp", ".swo", ".pyc", ".pyo", ".bak", ".log",
    ".db", ".db-shm", ".db-wal", ".pid",
}

#: Substring patterns of internal IDE tasks and extensions to filter out from handle inspection
IGNORED_HANDLE_PATTERNS: tuple[str, ...] = (
    "shellintegration.ps1",
    "jsonservermain",
    "eslintserver",
    "eslintserver.js",
    "typescript-language-features",
    "apex-language-server",
    "git-credential-",
    "credential-manager",
    "git-credential-manager",
    "appdata/local/programs/microsoft vs code",
    "resources/app/extensions",
    "resources/app/out/vs",
    ".vscode/extensions",
    ".cursor/extensions",
    ".windsurf/extensions",
)



def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ProcessInspector:
    """
    Monitors active file read handles of IDE processes within a target directory.

    Parameters
    ----------
    target_path : Path | str
        Root workspace directory being monitored.
    on_file_read : Callable[[dict[str, Any]], None] | None
        Callback triggered whenever a new file read is detected.
    poll_interval : float
        Interval in seconds between process inspections (default: 1.0s).
    """

    def __init__(
        self,
        target_path: Path | str,
        on_file_read: Callable[[dict[str, Any]], None] | None = None,
        poll_interval: float = 1.0,
        scan_interval: float | None = None,
        ide_profile: str = "antigravity",
        custom_marker: str | None = None,
    ) -> None:
        self.target_path = Path(target_path).resolve()
        self.on_file_read = on_file_read
        self.poll_interval = scan_interval if scan_interval is not None else poll_interval
        self.ide_profile = ide_profile
        self.custom_marker = custom_marker

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        # Track (pid, rel_path) tuples seen recently to avoid duplicates
        self._seen_handles: set[tuple[int, str]] = set()
        self._recent_reads: list[dict[str, Any]] = []

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

    def _inspect_once(self) -> None:
        """Scan active processes and inspect their open file handles."""
        my_pid = os.getpid()

        for proc in psutil.process_iter(["pid", "name"]):
            if self._stop_event.is_set():
                break

            try:
                name = proc.info.get("name") or ""
                pid = proc.info.get("pid")
                if not pid or pid == my_pid:
                    continue

                if name.lower() not in TARGET_PROCESS_NAMES:
                    continue

                if not is_process_whitelisted(proc, self.ide_profile, self.custom_marker):
                    continue

                try:
                    cmdline = proc.cmdline()
                    cmd_str = " ".join(cmdline).lower()
                    if any(p in cmd_str for p in IGNORED_HANDLE_PATTERNS):
                        continue
                except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                    pass

                open_files = proc.open_files()
                if not open_files:
                    continue

                for f in open_files:
                    rel_path = self._get_rel_path(f.path)
                    if not rel_path:
                        continue

                    handle_key = (pid, rel_path)
                    with self._lock:
                        if handle_key in self._seen_handles:
                            continue

                        # Cap set size to avoid memory growth
                        if len(self._seen_handles) > 3000:
                            self._seen_handles.clear()

                        self._seen_handles.add(handle_key)

                        read_record = {
                            "event_type": "READ",
                            "file_path": rel_path,
                            "process_name": name,
                            "pid": pid,
                            "timestamp": _utcnow_iso(),
                        }
                        self._recent_reads.append(read_record)

                        logger.debug(
                            "Detected file read by %s (PID %s): %s",
                            name, pid, rel_path,
                        )

                        if self.on_file_read:
                            try:
                                self.on_file_read(read_record)
                            except Exception as cb_exc:
                                logger.debug("Error in on_file_read callback: %s", cb_exc)

            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
                continue
            except Exception as exc:
                logger.debug("Unexpected error during process inspect: %s", exc)
                continue

    def _run_loop(self) -> None:
        """Background scanning loop."""
        logger.info("ProcessInspector started observing %s (interval=%.1fs)", self.target_path, self.poll_interval)
        while not self._stop_event.is_set():
            self._inspect_once()
            self._stop_event.wait(self.poll_interval)

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
