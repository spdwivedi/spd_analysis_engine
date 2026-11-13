"""
scripts/ai/provider_pool.py
===========================
Bounded provider concurrency pools for the SPD Analysis Engine.
Prevents provider resource exhaustion, local GPU thrashing, and API rate limits.
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Any, Generator

from .error_handling import TimeoutError

logger = logging.getLogger(__name__)


class BoundedProviderPool:
    """Manages bounded concurrency limits per AI provider."""

    DEFAULT_CAPACITIES = {
        "gemini": 3,
        "ollama": 1,
        "heuristic": 4,
    }

    def __init__(self, capacities: dict[str, int] | None = None) -> None:
        self._capacities = dict(self.DEFAULT_CAPACITIES)
        if capacities:
            self._capacities.update(capacities)

        self._semaphores: dict[str, threading.Semaphore] = {
            prov: threading.Semaphore(cap) for prov, cap in self._capacities.items()
        }
        self._active_counts: dict[str, int] = {prov: 0 for prov in self._capacities}
        self._lock = threading.Lock()

    def apply_profile(self, profile_config: Any) -> None:
        """Apply concurrency capacities from an ExecutionProfile."""
        capacities = profile_config.get_pool_capacities() if hasattr(profile_config, "get_pool_capacities") else profile_config
        with self._lock:
            for prov, cap in capacities.items():
                norm = self._normalize_provider(prov)
                self._capacities[norm] = cap
                self._semaphores[norm] = threading.Semaphore(cap)
                self._active_counts[norm] = 0

    def _normalize_provider(self, provider: str) -> str:
        prov = str(provider).strip().lower()
        if "gemini" in prov:
            return "gemini"
        if "ollama" in prov:
            return "ollama"
        return "heuristic"

    def get_capacity(self, provider: str) -> int:
        norm = self._normalize_provider(provider)
        return self._capacities.get(norm, 2)

    def get_active_count(self, provider: str) -> int:
        norm = self._normalize_provider(provider)
        with self._lock:
            return self._active_counts.get(norm, 0)

    @contextmanager
    def acquire(self, provider: str, timeout: float = 30.0) -> Generator[None, None, None]:
        """
        Acquire a slot in the provider's bounded pool.
        Raises TimeoutError if slot is not acquired within timeout.
        """
        norm = self._normalize_provider(provider)
        with self._lock:
            if norm not in self._semaphores:
                self._capacities[norm] = 2
                self._semaphores[norm] = threading.Semaphore(2)
                self._active_counts[norm] = 0
            sem = self._semaphores[norm]

        acquired = sem.acquire(timeout=timeout)
        if not acquired:
            raise TimeoutError(f"Timed out waiting for available slot in '{norm}' provider pool (max {self._capacities[norm]})")

        with self._lock:
            self._active_counts[norm] += 1

        try:
            yield
        finally:
            with self._lock:
                self._active_counts[norm] = max(0, self._active_counts[norm] - 1)
            sem.release()

    def get_pool_status(self) -> dict[str, Any]:
        """Return live status of all bounded pools."""
        with self._lock:
            return {
                prov: {
                    "capacity": self._capacities[prov],
                    "active": self._active_counts[prov],
                    "available": max(0, self._capacities[prov] - self._active_counts[prov]),
                }
                for prov in self._capacities
            }
