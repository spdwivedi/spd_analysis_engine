"""
scripts/event_bundler.py
========================
[LEGACY COMPONENT - FOR BACKWARD-COMPATIBILITY TESTS ONLY]
DO NOT USE IN PRODUCTION. The production worker uses BurstAssembler
(scripts/core/burst_assembler.py) which provides deterministic condition-variable
debouncing without threading.Timer objects.
"""

from __future__ import annotations

import logging, threading, time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

try:
    from .core.project_config import DEFAULT_DEBOUNCE_S, DEFAULT_MAX_BURST_S
except Exception:
    DEFAULT_DEBOUNCE_S, DEFAULT_MAX_BURST_S = 3.5, 60.0

from .core.suppression import suppress_file_path, is_path_suppressed  # noqa: F401

_IST = timezone(timedelta(hours=5, minutes=30))
_utcnow_iso = lambda: datetime.now(_IST).strftime("%Y-%m-%dT%H:%M:%S+05:30")
_now_ist_iso = _utcnow_iso


class EventBundler:
    """
    [LEGACY] Sliding-window event aggregator with quiet-period debounce and max-burst bound.
    Maintained solely for backward compatibility with legacy tests.
    Production workers must use BurstAssembler.
    """

    def __init__(
        self,
        callback: Callable[[dict[str, Any]], None] | None = None,
        quiet_period: float = DEFAULT_DEBOUNCE_S,
        on_activity: Callable[[], None] | None = None,
        is_initial_ingestion: bool = False,
        config: Any = None,
        max_burst_s: float = DEFAULT_MAX_BURST_S,
    ) -> None:
        self.callback = callback or (lambda b: None)
        self.config = config
        cfg_quiet = getattr(config, "debounce_s", quiet_period) if config is not None else quiet_period
        self._configured_quiet_period = float(cfg_quiet)
        self._max_burst_s = float(
            getattr(config, "max_burst_s", max_burst_s) if config is not None else max_burst_s
        )
        if self._max_burst_s < self._configured_quiet_period:
            self._max_burst_s = max(DEFAULT_MAX_BURST_S, self._configured_quiet_period)

        self.is_initial_ingestion = is_initial_ingestion
        self._quiet_period = (
            min(DEFAULT_DEBOUNCE_S, self._configured_quiet_period)
            if is_initial_ingestion
            else self._configured_quiet_period
        )
        self.on_activity = on_activity

        self._lock = threading.Lock()
        self._processing_lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._timer_generation: int = 0
        self._event_counter: int = 0
        self._extract_cutoff_seq: int | None = None
        self._pending_events: dict[str, dict[str, Any]] = {}
        self._first_event_mono: float | None = None
        self._first_event_iso: str | None = None
        self._stopped = False
        self._recent_actions: deque[dict[str, Any]] = deque(maxlen=50)
        self._touch_history: deque[tuple[float, str]] = deque()

    @property
    def quiet_period(self) -> float:
        return self._quiet_period

    @quiet_period.setter
    def quiet_period(self, val: float) -> None:
        with self._lock:
            self._quiet_period = self._configured_quiet_period = float(val)
            self._rearm_timer_locked()

    @property
    def configured_quiet_period(self) -> float:
        return self._configured_quiet_period

    @configured_quiet_period.setter
    def configured_quiet_period(self, val: float) -> None:
        with self._lock:
            self._configured_quiet_period = float(val)

    @property
    def max_burst_s(self) -> float:
        return self._max_burst_s

    @max_burst_s.setter
    def max_burst_s(self, val: float) -> None:
        with self._lock:
            self._max_burst_s = float(val)
            self._rearm_timer_locked()

    @property
    def queue_depth(self) -> int:
        with self._lock:
            return len(self._pending_events)

    @property
    def drop_counter(self) -> int:
        return 0

    @property
    def needs_reconcile(self) -> bool:
        return False

    def update_config(
        self,
        config: Any = None,
        quiet_period: float | None = None,
        max_burst_s: float | None = None,
    ) -> None:
        """Hot-reload debounce and burst configuration, re-arming any pending timer."""
        with self._lock:
            if config is not None:
                self.config = config
                if hasattr(config, "debounce_s"):
                    self._configured_quiet_period = float(config.debounce_s)
                    self._max_burst_s = float(getattr(config, "max_burst_s", DEFAULT_MAX_BURST_S))
            if quiet_period is not None:
                self._configured_quiet_period = float(quiet_period)
            if max_burst_s is not None:
                self._max_burst_s = float(max_burst_s)

            if not self.is_initial_ingestion:
                self._quiet_period = self._configured_quiet_period

            if self._max_burst_s < self._configured_quiet_period:
                self._max_burst_s = max(DEFAULT_MAX_BURST_S, self._configured_quiet_period)

            self._rearm_timer_locked()

    def _rearm_timer_locked(self) -> None:
        """Cancel and re-arm timer based on pending events and quiet period. Must be called under self._lock."""
        if self._stopped:
            return

        self._timer_generation += 1
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

        if not self._pending_events:
            return

        now_mono = time.monotonic()
        last_event_mono = max((e["last_seen_mono"] for e in self._pending_events.values()), default=now_mono)
        rem_quiet = max(0.0, self._quiet_period - (now_mono - last_event_mono))

        first_mono = self._first_event_mono or now_mono
        rem_burst = max(0.0, self._max_burst_s - (now_mono - first_mono))

        delay = min(rem_quiet, rem_burst)
        gen = self._timer_generation
        self._timer = threading.Timer(delay, self._on_timer_fired, args=[gen])
        self._timer.daemon = True
        self._timer.start()

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
        combined = f"{file_path} {details}".lower()
        ignored = (
            "shellintegration.ps1", "jsonservermain", "eslintserver",
            "typescript-language-features", "apex-language-server", "git-credential-",
        )
        if any(p in combined for p in ignored):
            return {}

        action = {
            "timestamp": datetime.now(_IST).strftime("%I:%M:%S %p IST"),
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

    def _prune_and_check_churn(self, now: float) -> bool:
        cutoff = now - 10.0
        while self._touch_history and self._touch_history[0][0] < cutoff:
            self._touch_history.popleft()
        if not self._pending_events:
            return False
        counts: dict[str, int] = {}
        for _, fp in self._touch_history:
            counts[fp] = counts.get(fp, 0) + 1
        return any(c > 8 for c in counts.values()) or len(self._touch_history) > 8

    def has_ide_churn(self) -> bool:
        """Return True if rapid repeated edits (>8 in 10s) are occurring without settling."""
        with self._lock:
            return self._prune_and_check_churn(time.monotonic())

    def get_debounce_status(self) -> dict[str, Any]:
        """Return live debounce countdown status for UI progress meters."""
        with self._lock:
            now = time.monotonic()
            churn = self._prune_and_check_churn(now)
            if not self._pending_events or self._timer is None or self._stopped:
                return {
                    "active": False, "remaining_s": 0.0, "elapsed_s": 0.0,
                    "files_count": 0, "first_file": None,
                    "quiet_period": self.quiet_period, "max_burst_s": self.max_burst_s, "ide_churn": churn,
                }
            last_event = max((e["last_seen_mono"] for e in self._pending_events.values()), default=now)
            elapsed = now - last_event
            remaining = max(0.0, self.quiet_period - elapsed)
            if self._first_event_mono is not None:
                remaining = min(remaining, max(0.0, self.max_burst_s - (now - self._first_event_mono)))
            return {
                "active": True,
                "remaining_s": round(remaining, 2),
                "elapsed_s": round(elapsed, 2),
                "files_count": len(self._pending_events),
                "first_file": next(iter(self._pending_events.keys()), None),
                "quiet_period": self.quiet_period,
                "max_burst_s": self.max_burst_s,
                "ide_churn": churn,
            }

    def add_event(
        self,
        event_type: str,
        file_path: str,
        timestamp: float | None = None,
    ) -> None:
        """Record a file system event and reset the sliding debounce window."""
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

            self._event_counter += 1
            current_seq = self._event_counter

            self._prune_and_check_churn(now_mono)
            self._touch_history.append((now_mono, file_path))

            if self._first_event_mono is None:
                self._first_event_mono = now_mono
                self._first_event_iso = now_iso

            if file_path not in self._pending_events:
                self._pending_events[file_path] = {
                    "file_path": file_path,
                    "event_type": event_type,
                    "first_seen_mono": now_mono,
                    "first_seen_iso": now_iso,
                    "last_seen_mono": now_mono,
                    "edit_count": 1,
                    "seq": current_seq,
                }
            else:
                entry = self._pending_events[file_path]
                entry["last_seen_mono"] = now_mono
                entry["edit_count"] += 1
                entry["seq"] = current_seq
                if entry["event_type"] != "created":
                    entry["event_type"] = event_type

            self._rearm_timer_locked()

            self._recent_actions.append({
                "timestamp": datetime.now(_IST).strftime("%I:%M:%S %p IST"),
                "iso": now_iso,
                "action_type": "WRITE",
                "file_path": file_path,
                "details": f"File {event_type}",
            })

    def _on_timer_fired(self, timer_gen: int | None = None) -> None:
        """Invoked when the quiet period elapses with zero new I/O events."""
        with self._processing_lock:
            with self._lock:
                if self._stopped or (timer_gen is not None and timer_gen != self._timer_generation):
                    return

                if not self._pending_events:
                    return

                now_mono = time.monotonic()
                last_event_mono = max((e["last_seen_mono"] for e in self._pending_events.values()), default=now_mono)
                quiet_elapsed = now_mono - last_event_mono
                first_event_mono = self._first_event_mono or now_mono
                burst_elapsed = now_mono - first_event_mono
                max_burst_reached = burst_elapsed >= self._max_burst_s

                # Verify quiet period is satisfied using latest event timestamp; if not, reschedule
                if timer_gen is not None and not max_burst_reached and quiet_elapsed < self._quiet_period:
                    self._rearm_timer_locked()
                    return

                self._extract_cutoff_seq = self._event_counter

            bundle = self._extract_bundle()
            if bundle is not None:
                logger.info(
                    "Quiet period (%.1fs) elapsed — flushing bundle with %d file(s)",
                    self._quiet_period, len(bundle["files_touched"]),
                )
                self._dispatch_bundle(bundle)

    def _extract_bundle(self, max_seq: int | None = None) -> dict[str, Any] | None:
        """Thread-safely harvest and clear pending events up to max_seq (or cutoff)."""
        with self._lock:
            if not self._pending_events:
                return None

            cutoff = max_seq if max_seq is not None else self._extract_cutoff_seq
            self._extract_cutoff_seq = None

            if cutoff is not None:
                eligible = {k: v for k, v in self._pending_events.items() if v.get("seq", 0) <= cutoff}
                if not eligible:
                    return None
            else:
                eligible = dict(self._pending_events)

            now_mono = time.monotonic()
            first_mono = min((e["first_seen_mono"] for e in eligible.values()), default=now_mono)
            duration = max(0.0, now_mono - first_mono)

            sorted_entries = sorted(eligible.values(), key=lambda x: x["first_seen_mono"])
            first_file = sorted_entries[0]["file_path"] if sorted_entries else None
            files_touched = [entry["file_path"] for entry in sorted_entries]

            bundle: dict[str, Any] = {
                "event_type": "EDIT",
                "files_touched": files_touched,
                "first_file": first_file,
                "duration_s": round(duration, 2),
                "events": list(sorted_entries),
                "started_at": sorted_entries[0].get("first_seen_iso", self._first_event_iso),
                "flushed_at": _utcnow_iso(),
            }

            for k in eligible:
                self._pending_events.pop(k, None)

            if self._pending_events:
                self._first_event_mono = min(
                    (e["first_seen_mono"] for e in self._pending_events.values()),
                    default=None,
                )
                self._first_event_iso = next(
                    (e.get("first_seen_iso") for e in self._pending_events.values() if e.get("first_seen_iso")),
                    None,
                )
            else:
                self._first_event_mono = None
                self._first_event_iso = None
                self._touch_history.clear()
                self._timer_generation += 1
                if self._timer is not None:
                    self._timer.cancel()
                    self._timer = None

            if self.is_initial_ingestion:
                self._quiet_period = self._configured_quiet_period
                self.is_initial_ingestion = False

            self._recent_actions.append({
                "timestamp": datetime.now(_IST).strftime("%I:%M:%S %p IST"),
                "iso": _utcnow_iso(),
                "action_type": "BURST",
                "file_path": first_file or "workspace",
                "details": f"Bundled {len(files_touched)} files",
            })

            return bundle

    def _dispatch_bundle(self, bundle: dict[str, Any]) -> None:
        """Execute the user callback. Must only be invoked while holding _processing_lock."""
        try:
            self.callback(bundle)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error in EventBundler callback: %s", exc)

    def flush_burst_now(self) -> dict[str, Any] | None:
        """Immediately seal and flush any unbundled file modifications in the debounce queue."""
        with self._processing_lock:
            with self._lock:
                self._timer_generation += 1
                if self._timer is not None:
                    self._timer.cancel()
                    self._timer = None

            bundle = self._extract_bundle()
            if bundle is not None:
                self._dispatch_bundle(bundle)
                return bundle
            return None

    def flush(self) -> None:
        """Immediately flush any pending events regardless of timer."""
        with self._processing_lock:
            with self._lock:
                self._timer_generation += 1
                if self._timer is not None:
                    self._timer.cancel()
                    self._timer = None

            bundle = self._extract_bundle()
            if bundle is not None:
                self._dispatch_bundle(bundle)

    def stop(self, flush_pending: bool = True) -> None:
        """Stop the bundler, cancelling any active timers and flushing pending events."""
        with self._processing_lock:
            with self._lock:
                self._stopped = True
                self._timer_generation += 1
                if self._timer is not None:
                    self._timer.cancel()
                    self._timer = None
                if not flush_pending:
                    self._pending_events.clear()
                    self._first_event_mono = None
                    self._first_event_iso = None
                    self._touch_history.clear()

            if flush_pending:
                bundle = self._extract_bundle()
                if bundle is not None:
                    self._dispatch_bundle(bundle)
