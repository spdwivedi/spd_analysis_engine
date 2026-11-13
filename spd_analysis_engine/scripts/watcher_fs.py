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
    from .core.burst_assembler import BurstAssembler
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
    ".pyd",
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


from .core.fs_event import EventCoalescer, FsEvent, is_editor_temp_file
from .storage.ignore_policy import IgnorePolicy
from .storage.manifest import FileManifest

class _WatchdogEventHandler(FileSystemEventHandler):
    """
    Internal Watchdog event handler that filters ignored paths using IgnorePolicy
    and coalesces raw mutations through EventCoalescer into clean FsEvents.
    """

    def __init__(
        self,
        target_path: Path,
        bundler: BurstAssembler | EventBundler | Any,
        ignore_policy: IgnorePolicy | None = None,
        manifest: FileManifest | None = None,
    ) -> None:
        super().__init__()
        self.target_path = target_path.resolve()
        self.bundler = bundler
        self.ignore_policy = ignore_policy if ignore_policy is not None else IgnorePolicy(self.target_path)
        self.manifest = manifest if manifest is not None else FileManifest(self.target_path, self.ignore_policy)
        self.coalescer = EventCoalescer(
            manifest=self.manifest,
            emit_callback=self._emit_coalesced_event,
            coalesce_window_s=0.05,
        )
        self._paused: bool = False

    def _emit_coalesced_event(self, event: FsEvent) -> None:
        """Receive coalesced FsEvent and forward to EventBundler."""
        logger.debug("Coalesced FsEvent: %s %s", event.kind, event.rel_path)
        self.bundler.add_event(event.kind, event.rel_path, timestamp=event.timestamp)

    def _is_sync_locked(self) -> bool:
        """Check if engine sync lock is present or watcher is paused."""
        return self._paused or (self.target_path / ".engine_sync.lock").exists()

    def _is_ignored(self, path: Path | str) -> bool:
        """Check if a path should be ignored (including during sync lock)."""
        if self._is_sync_locked():
            return True
        try:
            p = Path(path).resolve()
            rel = str(p.relative_to(self.target_path)).replace("\\", "/")
            return not self.ignore_policy.should_watch(rel)
        except ValueError:
            return True

    def _get_rel_path(self, abs_path_str: str) -> str | None:
        """Convert system absolute path to workspace-relative path."""
        if self._is_sync_locked():
            return None
        p = Path(abs_path_str).resolve()
        try:
            rel = p.relative_to(self.target_path)
            norm = str(rel).replace("\\", "/")
            if not self.ignore_policy.should_watch(norm):
                return None
            return norm
        except ValueError:
            return None

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        rel = self._get_rel_path(event.src_path)
        if rel:
            logger.debug("Watchdog on_modified: %s", rel)
            self.coalescer.feed("modified", rel, is_directory=False)

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        rel = self._get_rel_path(event.src_path)
        if rel:
            logger.debug("Watchdog on_created: %s", rel)
            self.coalescer.feed("created", rel, is_directory=False)

    def on_deleted(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        rel = self._get_rel_path(event.src_path)
        if rel:
            logger.debug("Watchdog on_deleted: %s", rel)
            self.coalescer.feed("deleted", rel, is_directory=False)

    def on_moved(self, event: FileSystemEvent) -> None:
        src_p = Path(event.src_path).resolve()
        dest_p = Path(event.dest_path).resolve() if hasattr(event, "dest_path") else None
        try:
            src_rel = str(src_p.relative_to(self.target_path)).replace("\\", "/")
        except ValueError:
            src_rel = None
        try:
            dest_rel = str(dest_p.relative_to(self.target_path)).replace("\\", "/") if dest_p else None
        except ValueError:
            dest_rel = None

        if event.is_directory:
            if src_rel and dest_rel:
                if not self.ignore_policy.should_watch(dest_rel):
                    return
                immediate_events = self.coalescer.feed(
                    "renamed",
                    dest_rel,
                    is_directory=True,
                    old_rel_path=src_rel,
                )
                for ev in immediate_events:
                    self._emit_coalesced_event(ev)
            return

        if dest_rel and self.ignore_policy.should_watch(dest_rel):
            self.coalescer.feed(
                "renamed",
                dest_rel,
                is_directory=False,
                old_rel_path=src_rel,
            )
        elif src_rel and self.ignore_policy.should_watch(src_rel):
            self.coalescer.feed("deleted", src_rel, is_directory=False)


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
        bundler: BurstAssembler | EventBundler | Any,
        recursive: bool = True,
        ignore_policy: IgnorePolicy | None = None,
        manifest: FileManifest | None = None,
    ) -> None:
        self.target_path = Path(target_path).resolve()
        self.bundler = bundler
        self.recursive = recursive
        self.ignore_policy = ignore_policy if ignore_policy is not None else IgnorePolicy(self.target_path)
        self.manifest = manifest if manifest is not None else FileManifest(self.target_path, self.ignore_policy)

        self._handler = _WatchdogEventHandler(
            self.target_path,
            self.bundler,
            ignore_policy=self.ignore_policy,
            manifest=self.manifest,
        )
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
        """Stop and join the observer thread, flushing any remaining coalesced events."""
        if self._observer is not None:
            logger.info("Stopping ProjectWatcher on %s", self.target_path)
            self._observer.stop()
            try:
                self._observer.join(timeout=timeout)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Error joining observer thread: %s", exc)
            self._observer = None

        if hasattr(self, "_handler") and hasattr(self._handler, "coalescer"):
            for ev in self._handler.coalescer.flush():
                self._handler._emit_coalesced_event(ev)

    def is_alive(self) -> bool:
        """Return True if the observer thread is active."""
        return self._observer is not None and self._observer.is_alive()

    def pause(self) -> None:
        """Pause event capture on watchdog handler."""
        if hasattr(self, "_handler"):
            self._handler._paused = True
            logger.info("ProjectWatcher paused for %s", self.target_path)

    def resume(self) -> None:
        """Resume event capture on watchdog handler."""
        if hasattr(self, "_handler"):
            self._handler._paused = False
            logger.info("ProjectWatcher resumed for %s", self.target_path)

    @property
    def is_paused(self) -> bool:
        """Return True if event capture is currently paused."""
        return bool(hasattr(self, "_handler") and getattr(self._handler, "_paused", False))

