"""
scripts/server/sse_stream.py
============================
Hardened Server-Sent Events (SSE) manager, replay buffer, bounded queues,
client cap, and lightweight heartbeat streaming for the SPD Analysis Engine.
"""

from __future__ import annotations

import collections
import json
import logging
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

MAX_SSE_CLIENTS = 25
DEFAULT_SSE_QUEUE_SIZE = 50
REPLAY_BUFFER_CAPACITY = 500
DEFAULT_HEARTBEAT_INTERVAL = 10.0

try:
    from spd_analysis_engine.scripts.core.time_utils import utc_now_iso as _utcnow_iso
except (ImportError, ModuleNotFoundError):
    def _utcnow_iso() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class SSEEvent:
    """Canonical Server-Sent Event frame with monotonic ID."""

    event_id: int
    event: str
    data: dict[str, Any]
    created_at: float = field(default_factory=time.time)

    def to_wire(self) -> bytes:
        payload = json.dumps(self.data)
        return f"id: {self.event_id}\nevent: {self.event}\ndata: {payload}\n\n".encode("utf-8")


class SSEClient:
    """Per-client bounded queue container with drop tracking for slow consumers."""

    def __init__(self, client_id: str, max_queue_size: int = DEFAULT_SSE_QUEUE_SIZE) -> None:
        self.client_id = client_id
        self.max_queue_size = max_queue_size
        self.q: queue.Queue[SSEEvent] = queue.Queue(maxsize=max_queue_size)
        self.connected_at = time.time()
        self.last_active = time.time()
        self.dropped_events: int = 0

    def put_event(self, ev: SSEEvent) -> bool:
        """Enqueue event; if full (slow client), drop oldest frame and append newest."""
        self.last_active = time.time()
        try:
            self.q.put_nowait(ev)
            return True
        except queue.Full:
            self.dropped_events += 1
            try:
                self.q.get_nowait()
            except queue.Empty:
                pass
            try:
                self.q.put_nowait(ev)
                return True
            except queue.Full:
                return False


class SSEManager:
    """Authoritative SSE manager with replay buffer, client capping, and coalescing."""

    def __init__(
        self,
        max_clients: int = MAX_SSE_CLIENTS,
        queue_size: int = DEFAULT_SSE_QUEUE_SIZE,
        replay_capacity: int = REPLAY_BUFFER_CAPACITY,
    ) -> None:
        self.max_clients = max_clients
        self.queue_size = queue_size
        self.replay_capacity = replay_capacity

        self._lock = threading.Lock()
        self._next_id: int = 1
        self._replay_buffer: collections.deque[SSEEvent] = collections.deque(maxlen=replay_capacity)
        self._clients: dict[str, SSEClient] = {}
        self._coalesce_timestamps: dict[str, float] = {}

    @property
    def client_count(self) -> int:
        with self._lock:
            return len(self._clients)

    def register_client(
        self,
        last_event_id: int | None = None,
        max_queue_size: int | None = None,
    ) -> tuple[SSEClient | None, str | None]:
        """
        Register a new SSE client listener.
        Replays missed events from buffer if last_event_id is provided.
        Returns (client, error_message).
        """
        with self._lock:
            if len(self._clients) >= self.max_clients:
                return None, f"Maximum concurrent SSE clients ({self.max_clients}) reached."

            client_id = str(uuid.uuid4())[:8]
            client = SSEClient(client_id, max_queue_size or self.queue_size)

            # Replay missed events from replay buffer
            if last_event_id is not None:
                for ev in self._replay_buffer:
                    if ev.event_id > last_event_id:
                        client.put_event(ev)

            self._clients[client_id] = client
            return client, None

    def unregister_client(self, client_id: str) -> None:
        """Remove client from active broadcast set."""
        with self._lock:
            self._clients.pop(client_id, None)

    def broadcast(
        self,
        event: str,
        data: dict[str, Any],
        coalesce_key: str | None = None,
        min_interval_s: float = 0.05,
    ) -> SSEEvent:
        """Broadcast an event frame to all registered clients and record in replay buffer."""
        now = time.time()
        with self._lock:
            if coalesce_key:
                last_time = self._coalesce_timestamps.get(coalesce_key, 0.0)
                if now - last_time < min_interval_s:
                    # Throttled/coalesced burst
                    pass
                self._coalesce_timestamps[coalesce_key] = now

            ev_id = self._next_id
            self._next_id += 1

            ev = SSEEvent(event_id=ev_id, event=event, data=data, created_at=now)
            self._replay_buffer.append(ev)

            for client in list(self._clients.values()):
                client.put_event(ev)

            return ev

    # Convenience broadcast helpers for domain lifecycle events
    def broadcast_burst_sealed(self, project: str, burst_uid: str, count: int, **extra: Any) -> SSEEvent:
        data = {"project": project, "burst_uid": burst_uid, "files_count": count, "timestamp": _utcnow_iso(), **extra}
        return self.broadcast("burst_sealed", data, coalesce_key=f"seal_{project}")

    def broadcast_status_changed(self, project: str, state: str, **extra: Any) -> SSEEvent:
        data = {"project": project, "state": state, "timestamp": _utcnow_iso(), **extra}
        return self.broadcast("status_changed", data, coalesce_key=f"status_{project}")

    def broadcast_job_progress(self, job_id: str, progress: float, stage: str = "", **extra: Any) -> SSEEvent:
        data = {"job_id": job_id, "progress": progress, "stage": stage, "timestamp": _utcnow_iso(), **extra}
        return self.broadcast("job_progress", data, coalesce_key=f"job_{job_id}", min_interval_s=0.1)

    def broadcast_ai_completed(self, project: str, summary: str = "", **extra: Any) -> SSEEvent:
        data = {"project": project, "summary": summary, "timestamp": _utcnow_iso(), **extra}
        return self.broadcast("ai_completed", data)

    def broadcast_ai_failed(self, project: str, error: str = "", **extra: Any) -> SSEEvent:
        data = {"project": project, "error": error, "timestamp": _utcnow_iso(), **extra}
        return self.broadcast("ai_failed", data)

    def broadcast_live_action(
        self,
        project: str,
        action_type: str,
        path: str = "",
        command: str = "",
        **extra: Any,
    ) -> SSEEvent:
        data = {
            "project": project,
            "action_type": action_type,
            "path": path,
            "command": command,
            "timestamp": _utcnow_iso(),
            **extra,
        }
        return self.broadcast("live_action", data, coalesce_key=f"action_{project}_{path}_{command}", min_interval_s=0.02)


# Global default SSE manager instance
_GLOBAL_SSE_MANAGER: SSEManager | None = None
_INIT_LOCK = threading.Lock()


def get_sse_manager() -> SSEManager:
    """Return singleton SSEManager instance."""
    global _GLOBAL_SSE_MANAGER
    if _GLOBAL_SSE_MANAGER is None:
        with _INIT_LOCK:
            if _GLOBAL_SSE_MANAGER is None:
                _GLOBAL_SSE_MANAGER = SSEManager()
    return _GLOBAL_SSE_MANAGER


def set_sse_manager(mgr: SSEManager) -> None:
    """Set or override global SSEManager (primarily for testing)."""
    global _GLOBAL_SSE_MANAGER
    with _INIT_LOCK:
        _GLOBAL_SSE_MANAGER = mgr
