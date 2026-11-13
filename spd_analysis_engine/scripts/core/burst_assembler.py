"""
scripts/core/burst_assembler.py
===============================
Condition-variable-based burst event assembler.

Replaces threading.Timer debouncing with a deterministic condition-variable loop:
- No threading.Timer objects for event debouncing.
- Due time is strictly recomputed after every notification:
    due = min(last_event + quiet_period, first_event + max_burst)
- add() and set_policy() notify the condition variable.
- Exactly one ordered bundle is extracted under lock and enqueued downstream outside the lock.
- Bounded queue (~20,000 events) prevents unbounded memory growth.
- Queue overflow flags needs_reconcile=True and increments drop_counter.
"""
from __future__ import annotations

import logging, queue, threading, time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .faults import check_fault
from .suppression import suppress_file_path, is_path_suppressed

logger = logging.getLogger(__name__)

DEFAULT_QUIET_PERIOD_S = 3.5
DEFAULT_MAX_BURST_S = 60.0
DEFAULT_MAX_QUEUE_SIZE = 20000


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class BurstAssembler:
    """Condition-variable event aggregator enforcing sliding-window debounce without Timers."""

    def __init__(
        self,
        quiet_period: float = DEFAULT_QUIET_PERIOD_S,
        max_burst_s: float = DEFAULT_MAX_BURST_S,
        max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE,
        downstream_queue: queue.Queue[dict[str, Any]] | None = None,
        on_activity: Callable[[], None] | None = None,
        callback: Callable[[dict[str, Any]], None] | None = None,
        on_action: Callable[[dict[str, Any]], None] | None = None,
        is_initial_ingestion: bool = False,
        config: Any = None,
    ) -> None:
        cfg_quiet = getattr(config, "debounce_s", quiet_period) if config is not None else quiet_period
        self._configured_quiet_period = max(0.005, float(cfg_quiet))
        self.is_initial_ingestion = is_initial_ingestion
        self._quiet_period = (
            min(DEFAULT_QUIET_PERIOD_S, self._configured_quiet_period)
            if is_initial_ingestion else self._configured_quiet_period
        )
        cfg_burst = getattr(config, "max_burst_s", max_burst_s) if config is not None else max_burst_s
        self._max_burst_s = max(self._quiet_period, float(cfg_burst))
        self.max_queue_size = max(10, int(max_queue_size))
        self.downstream_queue: queue.Queue[dict[str, Any]] = (
            downstream_queue if downstream_queue is not None else queue.Queue(maxsize=1000)
        )
        self.on_activity = on_activity
        self.on_action = on_action
        self.callback = callback
        self.config = config

        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._pending_events: dict[str, dict[str, Any]] = {}
        self._first_event_mono: float | None = None
        self._last_event_mono: float | None = None
        self._first_event_iso: str | None = None
        self._event_counter = 0

        self.needs_reconcile = False
        self.drop_counter = 0
        self._recent_actions: deque[dict[str, Any]] = deque(maxlen=50)
        self._touch_history: deque[tuple[float, str]] = deque()

        self._running = False
        self._stopped = False
        self._thread: threading.Thread | None = None

    @property
    def quiet_period(self) -> float:
        with self._cond:
            return self._quiet_period

    @quiet_period.setter
    def quiet_period(self, val: float) -> None:
        self.set_policy(quiet_period=val)

    @property
    def configured_quiet_period(self) -> float:
        with self._cond:
            return self._configured_quiet_period

    @configured_quiet_period.setter
    def configured_quiet_period(self, val: float) -> None:
        with self._cond:
            self._configured_quiet_period = float(val)
        self.set_policy(quiet_period=val)

    @property
    def max_burst_s(self) -> float:
        with self._cond:
            return self._max_burst_s

    @max_burst_s.setter
    def max_burst_s(self, val: float) -> None:
        self.set_policy(max_burst_s=val)

    @property
    def is_pending(self) -> bool:
        with self._cond:
            return bool(self._pending_events)

    @property
    def pending_count(self) -> int:
        with self._cond:
            return len(self._pending_events)

    @property
    def queue_depth(self) -> int:
        return self.pending_count

    def set_policy(self, quiet_period: float | None = None, max_burst_s: float | None = None) -> None:
        """Hot-reload debounce quiet period and max burst duration."""
        with self._cond:
            if quiet_period is not None:
                self._quiet_period = max(0.005, float(quiet_period))
                self._configured_quiet_period = self._quiet_period
            if max_burst_s is not None:
                self._max_burst_s = float(max_burst_s)
            if self._max_burst_s < self._quiet_period:
                self._max_burst_s = self._quiet_period
            self._cond.notify_all()

    def update_config(self, config: Any = None, quiet_period: float | None = None, max_burst_s: float | None = None) -> None:
        """Hot-reload debounce and burst configuration."""
        with self._cond:
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
            self._cond.notify_all()

    def add(self, file_path: str, event_type: str = "modified", timestamp: float | None = None) -> bool:
        """Add a file mutation event. Rejects overflow / stopped / suppressed."""
        if is_path_suppressed(file_path):
            logger.debug("Suppressing event on '%s' (suppression active)", file_path)
            return False

        if timestamp is None:
            now_mono = time.monotonic()
        elif float(timestamp) > 1_000_000_000.0:
            now_mono = max(0.0, time.monotonic() - max(0.0, time.time() - float(timestamp)))
        else:
            now_mono = float(timestamp)

        now_iso = _utcnow_iso()
        if self.on_activity:
            try:
                self.on_activity()
            except Exception:
                pass

        with self._cond:
            if self._stopped:
                return False
            if len(self._pending_events) >= self.max_queue_size and file_path not in self._pending_events:
                self.needs_reconcile = True
                self.drop_counter += 1
                logger.warning("BurstAssembler overflow (%d). Dropped: %s", len(self._pending_events), file_path)
                self._cond.notify_all()
                return False

            self._event_counter += 1
            if self._first_event_mono is None:
                self._first_event_mono, self._first_event_iso = now_mono, now_iso
            self._last_event_mono = now_mono
            self._prune_and_check_churn(now_mono)
            self._touch_history.append((now_mono, file_path))

            if file_path not in self._pending_events:
                self._pending_events[file_path] = {
                    "file_path": file_path, "event_type": event_type,
                    "first_seen_mono": now_mono, "first_seen_iso": now_iso,
                    "last_seen_mono": now_mono, "edit_count": 1, "seq": self._event_counter,
                }
            else:
                entry = self._pending_events[file_path]
                entry["last_seen_mono"] = now_mono
                entry["edit_count"] += 1
                entry["seq"] = self._event_counter
                if entry["event_type"] != "created":
                    entry["event_type"] = event_type

            action = {
                "timestamp": datetime.now(timezone.utc).strftime("%I:%M:%S %p UTC"),
                "iso": now_iso, "action_type": "WRITE", "file_path": file_path, "details": f"File {event_type}",
            }
            self._recent_actions.append(action)
            self._cond.notify_all()
            if self.on_action is not None:
                try:
                    self.on_action(action)
                except Exception:
                    pass
            return True

    def add_event(self, event_type: str, file_path: str, timestamp: float | None = None) -> bool:
        """Compatibility adapter for ProjectWatcher and callers using add_event."""
        return self.add(file_path=file_path, event_type=event_type, timestamp=timestamp)

    def suppress_path(self, file_path: str, duration_s: float = 4.0) -> None:
        """Temporarily suppress events on file_path."""
        suppress_file_path(file_path, duration_s)

    def record_action(self, action_type: str, file_path: str, details: str = "", timestamp: float | None = None) -> dict[str, Any]:
        """Record an action (READ, EXEC, BURST, WRITE) in recent actions circular buffer."""
        combined = f"{file_path} {details}".lower()
        if any(p in combined for p in ("shellintegration.ps1", "jsonservermain", "eslintserver", "typescript-language-features", "apex-language-server", "git-credential-")):
            return {}
        now_iso = _utcnow_iso()
        action = {
            "timestamp": datetime.now(timezone.utc).strftime("%I:%M:%S %p UTC"),
            "iso": now_iso, "action_type": action_type.upper(), "file_path": file_path, "details": details,
        }
        with self._cond:
            self._recent_actions.append(action)
        if self.on_action is not None:
            try:
                self.on_action(action)
            except Exception:
                pass
        return action

    def get_recent_actions(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return rolling recent actions in chronological order."""
        with self._cond:
            actions = list(self._recent_actions)
        return actions[-limit:] if limit > 0 else actions

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
        with self._cond:
            return self._prune_and_check_churn(time.monotonic())

    def get_debounce_status(self) -> dict[str, Any]:
        """Return live debounce countdown status for UI progress meters and worker.status.json."""
        with self._cond:
            now = time.monotonic()
            churn = self._prune_and_check_churn(now)
            if not self._pending_events or self._stopped:
                return {
                    "active": False, "remaining_s": 0.0, "elapsed_s": 0.0, "files_count": 0,
                    "first_file": None, "quiet_period": self._quiet_period,
                    "max_burst_s": self._max_burst_s, "ide_churn": churn,
                }
            last_event = self._last_event_mono if self._last_event_mono is not None else now
            first_event = self._first_event_mono if self._first_event_mono is not None else now
            elapsed = max(0.0, now - last_event)
            rem_quiet = max(0.0, self._quiet_period - elapsed)
            rem_burst = max(0.0, self._max_burst_s - max(0.0, now - first_event))
            remaining = min(rem_quiet, rem_burst)
            return {
                "active": True, "remaining_s": round(remaining, 2), "elapsed_s": round(elapsed, 2),
                "files_count": len(self._pending_events), "first_file": next(iter(self._pending_events.keys()), None),
                "quiet_period": self._quiet_period, "max_burst_s": self._max_burst_s, "ide_churn": churn,
            }

    def start(self) -> None:
        """Start the background assembler thread."""
        with self._cond:
            if self._running:
                return
            self._running, self._stopped = True, False
            self._thread = threading.Thread(target=self._assembler_loop, name="BurstAssemblerThread", daemon=True)
            self._thread.start()

    def _assembler_loop(self) -> None:
        while True:
            bundle: dict[str, Any] | None = None
            with self._cond:
                while self._running and not self._pending_events:
                    self._cond.wait()
                if not self._running:
                    break
                while self._running and self._pending_events:
                    now = time.monotonic()
                    last_event = self._last_event_mono or now
                    first_event = self._first_event_mono or now
                    due = min(last_event + self._quiet_period, first_event + self._max_burst_s)
                    rem = due - now
                    if rem <= 0.0005:
                        break
                    check_fault("during_pending_burst")
                    self._cond.wait(timeout=rem)
                if not self._running:
                    break
                bundle = self._extract_bundle_locked()
            if bundle is not None:
                self._enqueue_downstream(bundle)

    def _extract_bundle_locked(self) -> dict[str, Any] | None:
        """Extract all pending events into a single bundle. Must be called under lock."""
        if not self._pending_events:
            return None
        now_mono = time.monotonic()
        first_mono = self._first_event_mono or now_mono
        duration = max(0.0, now_mono - first_mono)
        sorted_entries = sorted(self._pending_events.values(), key=lambda x: x["seq"])
        first_file = sorted_entries[0]["file_path"] if sorted_entries else None
        files_touched = [entry["file_path"] for entry in sorted_entries]

        bundle: dict[str, Any] = {
            "event_type": "EDIT", "files_touched": files_touched, "first_file": first_file,
            "duration_s": round(duration, 2), "events": list(sorted_entries),
            "needs_reconcile": self.needs_reconcile,
            "started_at": self._first_event_iso or _utcnow_iso(), "flushed_at": _utcnow_iso(),
        }
        if self.is_initial_ingestion:
            bundle["is_initial"] = True
            self._quiet_period = self._configured_quiet_period
            self.is_initial_ingestion = False

        self._recent_actions.append({
            "timestamp": datetime.now(timezone.utc).strftime("%I:%M:%S %p UTC"),
            "iso": _utcnow_iso(), "action_type": "BURST",
            "file_path": first_file or "workspace", "details": f"Bundled {len(files_touched)} files",
        })
        self._pending_events.clear()
        self._first_event_mono, self._last_event_mono, self._first_event_iso = None, None, None
        self._touch_history.clear()
        self.needs_reconcile = False
        return bundle

    def _enqueue_downstream(self, bundle: dict[str, Any]) -> None:
        """Push bundle into downstream queue and notify optional callback."""
        try:
            self.downstream_queue.put(bundle, block=True, timeout=5.0)
        except queue.Full:
            logger.error("Downstream queue is full! Dropping bundle for %s", bundle.get("first_file"))
        if self.callback:
            try:
                self.callback(bundle)
            except Exception as exc:
                logger.exception("Error in BurstAssembler callback: %s", exc)

    def flush_now(self) -> dict[str, Any] | None:
        """Immediately extract and enqueue any pending events."""
        bundle: dict[str, Any] | None = None
        with self._cond:
            bundle = self._extract_bundle_locked()
            self._cond.notify_all()
        if bundle is not None:
            self._enqueue_downstream(bundle)
        return bundle

    def flush_burst_now(self) -> dict[str, Any] | None:
        """Immediately seal and flush any unbundled file modifications in the debounce queue."""
        return self.flush_now()

    def flush(self) -> dict[str, Any] | None:
        """Immediately extract and flush pending events."""
        return self.flush_now()

    def stop(self, drain: bool = True, flush_pending: bool | None = None, timeout: float = 5.0) -> None:
        """Stop the assembler thread, optionally draining any pending bundle."""
        if flush_pending is not None:
            drain = flush_pending
        with self._cond:
            self._running, self._stopped = False, True
            self._cond.notify_all()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            self._thread = None
        if drain:
            self.flush_now()
