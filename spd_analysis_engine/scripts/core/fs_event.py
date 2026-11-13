"""
scripts/core/fs_event.py
========================
Normalized file-system events and editor atomic-save coalescer.

Features:
- Normalized FsEvent(kind, rel_path, timestamp) representation.
- Coalesces editor atomic-save sequences (temp file creation + rename / delete + create).
- Expands directory moves into file-level rename events using manifest knowledge.
- Thread-safe buffering with configurable debounce window.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from ..storage.manifest import FileManifest

logger = logging.getLogger(__name__)

EDITOR_TEMP_EXTENSIONS: tuple[str, ...] = (
    ".tmp",
    ".swp",
    ".swo",
    ".bak",
    "~",
)


@dataclass(frozen=True)
class FsEvent:
    """Normalized file system mutation event."""
    kind: str       # 'created', 'modified', 'deleted', 'renamed'
    rel_path: str
    timestamp: float
    old_rel_path: str | None = None


def is_editor_temp_file(rel_path: str) -> bool:
    """Detect whether a file path represents an editor swap or temporary write target."""
    name = Path(rel_path).name.lower()
    if name.endswith(EDITOR_TEMP_EXTENSIONS) or ".tmp." in name:
        return True
    if name.startswith((".#", "#", "4913", ".tmp")):
        return True
    return False


class EventCoalescer:
    """
    Coalesces raw file system events into clean FsEvents.
    Eliminates noise from editor atomic saves (e.g. write-to-temp-then-rename,
    or rapid delete-then-recreate sequences).
    """

    def __init__(
        self,
        manifest: FileManifest | None = None,
        emit_callback: Callable[[FsEvent], None] | None = None,
        coalesce_window_s: float = 0.05,
    ) -> None:
        self.manifest = manifest
        self.emit_callback = emit_callback
        self.coalesce_window_s = max(0.005, float(coalesce_window_s))

        self._lock = threading.Lock()
        self._buffered_events: dict[str, FsEvent] = {}
        self._pending_deletes: dict[str, float] = {}
        self._pending_temp_files: dict[str, float] = {}
        self._timer: threading.Timer | None = None

    def _arm_timer_locked(self) -> None:
        if self.emit_callback is None:
            return
        if self._timer is not None:
            self._timer.cancel()
        self._timer = threading.Timer(self.coalesce_window_s, self._on_timer)
        self._timer.daemon = True
        self._timer.start()

    def _on_timer(self) -> None:
        flushed = self.flush()
        if self.emit_callback:
            for ev in flushed:
                try:
                    self.emit_callback(ev)
                except Exception as exc:
                    logger.debug("Error in emit_callback: %s", exc)

    def feed(
        self,
        kind: str,
        rel_path: str,
        timestamp: float | None = None,
        is_directory: bool = False,
        old_rel_path: str | None = None,
    ) -> list[FsEvent]:
        """
        Process a raw file system event.
        Returns any immediately produced events (e.g. from directory moves),
        while buffering candidates for atomic coalescing.
        """
        ts = time.time() if timestamp is None else float(timestamp)
        norm_rel = rel_path.replace("\\", "/").strip("/")
        norm_old = old_rel_path.replace("\\", "/").strip("/") if old_rel_path else None

        with self._lock:
            # 1. Directory moves expanded using manifest
            if is_directory and kind in ("renamed", "moved"):
                immediate = self._handle_directory_move(norm_old or norm_rel, norm_rel, ts)
                return immediate

            # 2. Ignore directory create/modify/delete events
            if is_directory:
                return []

            # 3. Editor temporary file handling
            if is_editor_temp_file(norm_rel):
                if kind in ("created", "modified"):
                    self._pending_temp_files[norm_rel] = ts
                elif kind == "deleted":
                    self._pending_temp_files.pop(norm_rel, None)
                return []

            # 4. Handle Rename / Move of files
            if kind in ("renamed", "moved"):
                if norm_old and is_editor_temp_file(norm_old):
                    # Atomic save: temp file renamed to target file!
                    self._pending_temp_files.pop(norm_old, None)
                    self._pending_deletes.pop(norm_rel, None)
                    was_existing = bool(self.manifest and norm_rel in self.manifest)
                    target_kind = "modified" if was_existing else "created"
                    self._buffered_events[norm_rel] = FsEvent(
                        kind=target_kind,
                        rel_path=norm_rel,
                        timestamp=ts,
                    )
                    self._arm_timer_locked()
                    return []

                # Regular file rename
                self._buffered_events[norm_rel] = FsEvent(
                    kind="renamed",
                    rel_path=norm_rel,
                    timestamp=ts,
                    old_rel_path=norm_old,
                )
                self._arm_timer_locked()
                return []

            # 5. Handle File Deletions
            if kind == "deleted":
                self._pending_deletes[norm_rel] = ts
                self._arm_timer_locked()
                return []

            # 6. Handle File Creations and Modifications
            if kind in ("created", "modified"):
                # If path was in pending_deletes, delete + create sequence -> coalesced to modified
                if norm_rel in self._pending_deletes:
                    del self._pending_deletes[norm_rel]
                    self._buffered_events[norm_rel] = FsEvent(
                        kind="modified",
                        rel_path=norm_rel,
                        timestamp=ts,
                    )
                elif norm_rel in self._buffered_events:
                    prev = self._buffered_events[norm_rel]
                    # Retain 'created' if subsequent write occurs rapidly
                    if prev.kind == "created" and kind == "modified":
                        self._buffered_events[norm_rel] = FsEvent(
                            kind="created",
                            rel_path=norm_rel,
                            timestamp=ts,
                        )
                    else:
                        self._buffered_events[norm_rel] = FsEvent(
                            kind=kind,
                            rel_path=norm_rel,
                            timestamp=ts,
                        )
                else:
                    self._buffered_events[norm_rel] = FsEvent(
                        kind=kind,
                        rel_path=norm_rel,
                        timestamp=ts,
                    )

                self._arm_timer_locked()
                return []

        return []

    def _handle_directory_move(
        self,
        old_dir: str,
        new_dir: str,
        timestamp: float,
    ) -> list[FsEvent]:
        """Convert a directory rename into file-level renames via manifest lookup."""
        events: list[FsEvent] = []
        old_prefix = old_dir if old_dir.endswith("/") else f"{old_dir}/"

        if self.manifest:
            matching_entries = self.manifest.find_by_prefix(old_prefix)
            for entry in matching_entries:
                sub_path = entry.relative_path[len(old_prefix):]
                new_file = f"{new_dir}/{sub_path}" if new_dir else sub_path
                events.append(
                    FsEvent(
                        kind="renamed",
                        rel_path=new_file,
                        timestamp=timestamp,
                        old_rel_path=entry.relative_path,
                    )
                )
                self.manifest.record_rename(entry.relative_path, new_file)

        return events

    def flush(self) -> list[FsEvent]:
        """Flush and return all buffered and coalesced file events."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

            # Flush unresolved deletes
            for del_path, del_ts in self._pending_deletes.items():
                self._buffered_events[del_path] = FsEvent(
                    kind="deleted",
                    rel_path=del_path,
                    timestamp=del_ts,
                )
            self._pending_deletes.clear()

            events = list(self._buffered_events.values())
            self._buffered_events.clear()

            # Sort deterministically by timestamp
            events.sort(key=lambda e: e.timestamp)
            return events
