"""
scripts/event_bundler.py
========================
Thread-safe sliding-window event aggregator for the SPD Analysis Engine.

Classes
-------
EventBundler
    Coalesces rapid streams of file system I/O notifications into atomic
    event bundles using a configurable sliding debounce window (default 3.5s).
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))


def _now_ist_iso() -> str:
    """Return current IST timestamp in ISO-8601 format."""
    return datetime.now(_IST).strftime("%Y-%m-%dT%H:%M:%S+05:30")


_utcnow_iso = _now_ist_iso

_SUPPRESSED_PATHS_LOCK = threading.Lock()
_SUPPRESSED_PATHS: dict[str, float] = {}


def suppress_file_path(file_path: str, duration_s: float = 4.0) -> None:
    """Suppress watchdog events for a specific file path or filename for duration_s seconds."""
    norm = str(file_path).replace("\\", "/").lower().strip("/")
    base = Path(file_path).name.lower()
    expiry = time.monotonic() + duration_s
    with _SUPPRESSED_PATHS_LOCK:
        _SUPPRESSED_PATHS[norm] = expiry
        if base:
            _SUPPRESSED_PATHS[base] = expiry


def is_path_suppressed(file_path: str) -> bool:
    """Check if file_path is currently under temporary suppression window."""
    norm = str(file_path).replace("\\", "/").lower().strip("/")
    base = Path(file_path).name.lower()
    now = time.monotonic()
    with _SUPPRESSED_PATHS_LOCK:
        expired = [k for k, exp in _SUPPRESSED_PATHS.items() if exp < now]
        for k in expired:
            del _SUPPRESSED_PATHS[k]
        return (_SUPPRESSED_PATHS.get(norm, 0.0) > now) or (_SUPPRESSED_PATHS.get(base, 0.0) > now)


class EventBundler:
    """
    Sliding-window event aggregator with a quiet-period debounce.

    Whenever an event arrives, the 3.5-second countdown is reset.
    When 3.5 seconds elapse with no new file system activity, the bundle
    is flushed and dispatched to the registered callback.

    Parameters
    ----------
    callback : Callable[[dict[str, Any]], None]
        Function to invoke with the flushed bundle payload.
    quiet_period : float, optional
        Inactivity threshold in seconds before flushing (default: 3.5).
    """

    def __init__(
        self,
        callback: Callable[[dict[str, Any]], None] | None = None,
        quiet_period: float = 3.5,
        on_activity: Callable[[], None] | None = None,
    ) -> None:
        self.callback = callback or (lambda b: None)
        self.quiet_period = quiet_period
        self.on_activity = on_activity

        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._pending_events: dict[str, dict[str, Any]] = {}  # file_path -> event_dict
        self._first_event_mono: float | None = None
        self._first_event_iso: str | None = None
        self._stopped = False
        self._recent_actions: deque[dict[str, Any]] = deque(maxlen=50)

    def suppress_path(self, file_path: str, duration_s: float = 4.0) -> None:
        """Temporarily suppress events on file_path (e.g. self-triggering CHANGELOG.md)."""
        suppress_file_path(file_path, duration_s)

    def record_action(
        self,
        action_type: str,
        file_path: str,
        details: str = "",
        timestamp: float | None = None,
    ) -> dict[str, Any]:
        # Filter out internal IDE background tasks and git credential helpers
        combined_text = f"{file_path} {details}".lower()
        for pattern in (
            "shellintegration.ps1",
            "jsonservermain",
            "eslintserver",
            "typescript-language-features",
            "apex-language-server",
            "git-credential-",
        ):
            if pattern in combined_text:
                return {}

        ts_str = datetime.now(_IST).strftime("%I:%M:%S %p IST")
        action = {
            "timestamp": ts_str,
            "iso": _utcnow_iso(),
            "action_type": action_type.upper(),
            "file_path": file_path,
            "details": details,
        }
        with self._lock:
            self._recent_actions.append(action)
        return action

    def get_recent_actions(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return rolling recent actions in chronological order."""
        with self._lock:
            actions = list(self._recent_actions)
        return actions[-limit:] if limit > 0 else actions

    @property
    def is_pending(self) -> bool:
        """Return True if events are queued awaiting quiet period expiry."""
        with self._lock:
            return bool(self._pending_events)

    def get_debounce_status(self) -> dict[str, Any]:
        """
        Return live debounce countdown status for UI progress meters.
        """
        with self._lock:
            if not self._pending_events or self._timer is None or self._stopped:
                return {
                    "active": False,
                    "remaining_s": 0.0,
                    "elapsed_s": 0.0,
                    "files_count": 0,
                    "first_file": None,
                    "quiet_period": self.quiet_period,
                }
            now = time.monotonic()
            last_event = max((e["last_seen_mono"] for e in self._pending_events.values()), default=now)
            elapsed = now - last_event
            remaining = max(0.0, self.quiet_period - elapsed)
            first_file = next(iter(self._pending_events.keys()), None)
            return {
                "active": True,
                "remaining_s": round(remaining, 2),
                "elapsed_s": round(elapsed, 2),
                "files_count": len(self._pending_events),
                "first_file": first_file,
                "quiet_period": self.quiet_period,
            }

    def add_event(
        self,
        event_type: str,
        file_path: str,
        timestamp: float | None = None,
    ) -> None:
        """
        Record a file system event and reset the sliding debounce window.

        Parameters
        ----------
        event_type : str
            Type of file operation (e.g. "modified", "created", "deleted").
        file_path : str
            Workspace-relative or absolute path to the file.
        timestamp : float | None
            Event occurrence timestamp (monotonic). Defaults to ``time.monotonic()``.
        """
        if is_path_suppressed(file_path):
            logger.debug("Suppressing event on '%s' (self-trigger suppression active)", file_path)
            return

        now_mono = time.monotonic() if timestamp is None else timestamp
        now_iso = _utcnow_iso()

        if self.on_activity:
            try:
                self.on_activity()
            except Exception:
                pass

        with self._lock:
            if self._stopped:
                logger.warning("EventBundler is stopped; dropping event for %s", file_path)
                return

            if self._first_event_mono is None:
                self._first_event_mono = now_mono
                self._first_event_iso = now_iso

            # Track or update file in pending events
            if file_path not in self._pending_events:
                self._pending_events[file_path] = {
                    "file_path": file_path,
                    "event_type": event_type,
                    "first_seen_mono": now_mono,
                    "first_seen_iso": now_iso,
                    "last_seen_mono": now_mono,
                    "edit_count": 1,
                }
            else:
                entry = self._pending_events[file_path]
                entry["last_seen_mono"] = now_mono
                entry["edit_count"] += 1
                # If it was created and later modified, keep original creation intent
                if entry["event_type"] != "created":
                    entry["event_type"] = event_type

            # Reset sliding debounce timer
            if self._timer is not None:
                self._timer.cancel()

            self._timer = threading.Timer(self.quiet_period, self._on_timer_fired)
            self._timer.daemon = True
            self._timer.start()

            self._recent_actions.append({
                "timestamp": datetime.now(_IST).strftime("%I:%M:%S %p IST"),
                "iso": now_iso,
                "action_type": "WRITE",
                "file_path": file_path,
                "details": f"File {event_type}",
            })

            logger.debug(
                "Added event %s for '%s' (pending files: %d, timer reset to %.1fs)",
                event_type, file_path, len(self._pending_events), self.quiet_period,
            )

    def _on_timer_fired(self) -> None:
        """Invoked when the quiet period elapses with zero new I/O events."""
        bundle = self._extract_bundle()
        if bundle is not None:
            logger.info(
                "Quiet period (%.1fs) elapsed — flushing bundle with %d file(s) (entrypoint: %s)",
                self.quiet_period, len(bundle["files_touched"]), bundle["first_file"],
            )
            self._dispatch_bundle(bundle)

    def _extract_bundle(self) -> dict[str, Any] | None:
        """
        Thread-safely harvest and clear all pending events.
        Returns the constructed bundle dict or None if empty.
        """
        with self._lock:
            if not self._pending_events:
                return None

            now_mono = time.monotonic()
            first_mono = self._first_event_mono or now_mono
            duration = max(0.0, now_mono - first_mono)

            # Sort pending events by first_seen_mono to identify entrypoint file
            sorted_entries = sorted(
                self._pending_events.values(),
                key=lambda x: x["first_seen_mono"],
            )
            first_file = sorted_entries[0]["file_path"] if sorted_entries else None
            files_touched = [entry["file_path"] for entry in sorted_entries]

            bundle: dict[str, Any] = {
                "event_type": "EDIT",
                "files_touched": files_touched,
                "first_file": first_file,
                "duration_s": round(duration, 2),
                "events": list(sorted_entries),
                "started_at": self._first_event_iso,
                "flushed_at": _utcnow_iso(),
            }

            # Reset internal state
            self._pending_events.clear()
            self._first_event_mono = None
            self._first_event_iso = None
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

            self._recent_actions.append({
                "timestamp": datetime.now(_IST).strftime("%I:%M:%S %p IST"),
                "iso": _utcnow_iso(),
                "action_type": "BURST",
                "file_path": first_file or "workspace",
                "details": f"Bundled {len(files_touched)} files",
            })

            return bundle

    def _dispatch_bundle(self, bundle: dict[str, Any]) -> None:
        """Execute the user callback outside of the lock."""
        try:
            self.callback(bundle)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error in EventBundler callback: %s", exc)

    def flush_burst_now(self) -> dict[str, Any] | None:
        """
        Immediately seal and flush any unbundled file modifications in the debounce
        queue into a completed burst, resetting the timer to idle (Lap button).
        Returns the emitted bundle or None if no pending events.
        """
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

        bundle = self._extract_bundle()
        if bundle is not None:
            logger.info("Manual burst sealed (Lap button) — emitting bundle with %d file(s)", len(bundle["files_touched"]))
            self._dispatch_bundle(bundle)
            return bundle
        return None

    def flush(self) -> None:
        """
        Immediately flush any pending events regardless of timer.
        Useful during worker shutdown or explicit synchronisation points.
        """
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

        bundle = self._extract_bundle()
        if bundle is not None:
            logger.info("Explicit flush of pending bundle with %d file(s)", len(bundle["files_touched"]))
            self._dispatch_bundle(bundle)

    def stop(self) -> None:
        """
        Stop the bundler, cancelling any active timers and flushing pending events.
        """
        with self._lock:
            self._stopped = True
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

        bundle = self._extract_bundle()
        if bundle is not None:
            self._dispatch_bundle(bundle)
