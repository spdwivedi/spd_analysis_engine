"""
scripts/ai/rate_limiter.py
==========================
Thread-safe rate limiter with rolling 60-second window and cooldown queue.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Callable


class RateLimitManager:
    """
    Thread-safe RPM/TPM rate limiter with rolling 60-second window and cooldown queue.
    """

    def __init__(self, max_rpm: int = 15) -> None:
        self.max_rpm = max_rpm
        self._lock = threading.Lock()
        self._request_timestamps: deque[float] = deque()
        self._cooling_down = False
        self._cooldown_expiry = 0.0

    def reset(self) -> None:
        """Reset rate limiter state (clears cooldown and timestamp history)."""
        with self._lock:
            self._request_timestamps.clear()
            self._cooling_down = False
            self._cooldown_expiry = 0.0

    def record_request(self) -> None:
        """Record an outbound API request timestamp."""
        now = time.monotonic()
        with self._lock:
            self._clean_window(now)
            self._request_timestamps.append(now)

    def _clean_window(self, now: float) -> None:
        """Remove request timestamps older than 60 seconds."""
        cutoff = now - 60.0
        while self._request_timestamps and self._request_timestamps[0] < cutoff:
            self._request_timestamps.popleft()

    def get_status(self) -> dict[str, Any]:
        """Return live rate-limit status including cooling_down and cooldown_remaining_s."""
        now = time.monotonic()
        with self._lock:
            self._clean_window(now)
            current_count = len(self._request_timestamps)
            is_cooling = False
            remaining_s = 0.0

            if self._cooling_down and now < self._cooldown_expiry:
                is_cooling = True
                remaining_s = max(0.0, self._cooldown_expiry - now)
            elif current_count >= self.max_rpm:
                is_cooling = True
                oldest = self._request_timestamps[0]
                remaining_s = max(1.0, 60.0 - (now - oldest))
            else:
                self._cooling_down = False

            return {
                "cooling_down": is_cooling,
                "cooldown_remaining_s": round(remaining_s, 1),
                "current_rpm": current_count,
                "max_rpm": self.max_rpm,
            }

    def trigger_cooldown(self, duration_s: float = 60.0) -> None:
        """Explicitly enforce a cooldown (e.g. after receiving a 429 response)."""
        now = time.monotonic()
        with self._lock:
            self._cooling_down = True
            self._cooldown_expiry = max(self._cooldown_expiry, now + duration_s)

    def wait_if_needed(self, progress_callback: Callable[[float], None] | None = None, max_wait_s: float = 60.0) -> float:
        """
        Check rate limit and sleep if cooling down.
        Invokes progress_callback(remaining_s) periodically during wait.
        Returns total seconds waited.
        """
        total_waited = 0.0
        max_steps = 120
        step_count = 0
        while step_count < max_steps:
            step_count += 1
            status = self.get_status()
            if not status["cooling_down"]:
                break
            remaining = status["cooldown_remaining_s"]
            if remaining <= 0 or total_waited >= max_wait_s:
                break
            if progress_callback:
                try:
                    progress_callback(remaining)
                except Exception:
                    pass
            sleep_chunk = min(1.0, remaining, max(0.0, max_wait_s - total_waited))
            if sleep_chunk <= 0:
                break
            time.sleep(sleep_chunk)
            total_waited += sleep_chunk

        self.record_request()
        return total_waited
