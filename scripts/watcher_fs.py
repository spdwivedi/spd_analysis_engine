"""
scripts/watcher_fs.py
=====================
Non-blocking file-system observer using watchdog for the SPD Analysis Engine.

Classes
-------
ProjectWatcher
    Monitors a target project directory recursively, filters out noise
    (temporary files, git metadata, internal logs), and emits clean file
    mutation events into an ``EventBundler``.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

if TYPE_CHECKING:
    from .event_bundler import EventBundler

logger = logging.getLogger(__name__)

_SYNC_SUPPRESSION_UNTIL: float = 0.0


def suppress_sync_events(duration_s: float = 5.0) -> None:
    """Set an atomic in-memory suppression window for engine sync file modifications."""
    global _SYNC_SUPPRESSION_UNTIL
    _SYNC_SUPPRESSION_UNTIL = max(_SYNC_SUPPRESSION_UNTIL, time.time() + duration_s)

#: Directories ignored by the file watcher
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
    ".mypy_cache",
    ".pytest_cache",
}

#: File extensions ignored by the file watcher
IGNORED_EXTENSIONS: set[str] = {
    ".tmp",
    ".swp",
    ".swo",
    ".pyc",
    ".pyo",
    ".bak",
    ".log",
    ".db",
    ".db-shm",
    ".db-wal",
    ".pid",
}

#: Exact filenames ignored by the file watcher
IGNORED_FILES: set[str] = {
    "session.db",
    "session.db-shm",
    "session.db-wal",
    "worker.log",
    "worker.pid",
    "worker.pid.stale",
    "worker.status.json",
    "project.meta",
    ".engine_sync.lock",
}


class _WatchdogEventHandler(FileSystemEventHandler):
    """
    Internal Watchdog event handler that filters ignored paths and sends
    clean file events to the EventBundler.
    """

    def __init__(self, target_path: Path, bundler: EventBundler) -> None:
        super().__init__()
        self.target_path = target_path.resolve()
        self.bundler = bundler

    def _is_ignored(self, path: Path) -> bool:
        """Check if *path* matches any ignore rule."""
        try:
            if (self.target_path / ".engine_sync.lock").exists():
                return True
        except Exception:
            pass
        parts = path.parts
        for part in parts:
            if part in IGNORED_DIRS or part.startswith(".sf"):
                return True
        name = path.name
        if name in IGNORED_FILES:
            return True
        if name == "CHANGELOG.md":
            if time.time() < _SYNC_SUPPRESSION_UNTIL:
                return True
        if name.startswith("catalog.json") or name.endswith(".__staging__") or name.endswith(".tmp"):
            return True
        if path.suffix.lower() in IGNORED_EXTENSIONS:
            return True
        return False

    def _get_rel_path(self, abs_path_str: str) -> str | None:
        """Convert system absolute path to workspace-relative path."""
        p = Path(abs_path_str).resolve()
        if self._is_ignored(p):
            return None
        try:
            rel = p.relative_to(self.target_path)
            return str(rel).replace("\\", "/")
        except ValueError:
            return None

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        rel = self._get_rel_path(event.src_path)
        if rel:
            logger.debug("Watchdog on_modified: %s", rel)
            self.bundler.add_event("modified", rel)

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        rel = self._get_rel_path(event.src_path)
        if rel:
            logger.debug("Watchdog on_created: %s", rel)
            self.bundler.add_event("created", rel)

    def on_deleted(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        rel = self._get_rel_path(event.src_path)
        if rel:
            logger.debug("Watchdog on_deleted: %s", rel)
            self.bundler.add_event("deleted", rel)

    def on_moved(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        src_rel = self._get_rel_path(event.src_path)
        dest_rel = self._get_rel_path(event.dest_path) if hasattr(event, "dest_path") else None
        if src_rel:
            self.bundler.add_event("deleted", src_rel)
        if dest_rel:
            self.bundler.add_event("created", dest_rel)


class ProjectWatcher:
    """
    Non-blocking project directory watcher.

    Wraps a ``watchdog.observers.Observer`` running in a background daemon
    thread, feeding debounced events into ``EventBundler``.

    Parameters
    ----------
    target_path : Path | str
        The root project directory being monitored.
    bundler : EventBundler
        Sliding-window event bundler receiving clean notifications.
    recursive : bool, optional
        Whether to monitor child subdirectories (default: True).
    """

    def __init__(
        self,
        target_path: Path | str,
        bundler: EventBundler,
        recursive: bool = True,
    ) -> None:
        self.target_path = Path(target_path).resolve()
        self.bundler = bundler
        self.recursive = recursive

        self._handler = _WatchdogEventHandler(self.target_path, self.bundler)
        self._observer: Observer | None = None

    def start(self) -> None:
        """Start the background watchdog observer thread."""
        if self._observer is not None and self._observer.is_alive():
            logger.debug("ProjectWatcher observer already running on %s", self.target_path)
            return

        if not self.target_path.exists():
            raise FileNotFoundError(f"Target directory does not exist: {self.target_path}")

        self._observer = Observer()
        self._observer.daemon = True
        self._observer.schedule(self._handler, str(self.target_path), recursive=self.recursive)
        self._observer.start()
        logger.info("ProjectWatcher started observing %s (recursive=%s)", self.target_path, self.recursive)

    def stop(self, timeout: float = 3.0) -> None:
        """Stop and join the observer thread."""
        if self._observer is not None:
            logger.info("Stopping ProjectWatcher on %s", self.target_path)
            self._observer.stop()
            try:
                self._observer.join(timeout=timeout)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Error joining observer thread: %s", exc)
            self._observer = None

    def is_alive(self) -> bool:
        """Return True if the observer thread is active."""
        return self._observer is not None and self._observer.is_alive()
