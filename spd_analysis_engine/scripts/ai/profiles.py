"""
scripts/ai/profiles.py
======================
Provider execution profiles for the SPD Analysis Engine.
Supports NORMAL, ECO, GPU_AI, and OFFLINE execution profiles.

Controls:
- Concurrency (provider pool limits)
- Timeout
- Context size (diff lines & characters)
- Ollama num_ctx
- Ollama num_gpu
- Ollama keep_alive
- Polling intensity

Guarantees:
Execution profile changes NEVER alter capture durability (SQLite WAL, git commits).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


class ExecutionProfile:
    NORMAL = "NORMAL"
    ECO = "ECO"
    GPU_AI = "GPU_AI"
    OFFLINE = "OFFLINE"

    ALL = (NORMAL, ECO, GPU_AI, OFFLINE)


@dataclass(frozen=True)
class ProfileConfig:
    """Immutable execution profile parameters."""

    name: str
    gemini_concurrency: int
    ollama_concurrency: int
    heuristic_concurrency: int
    timeout_s: float
    max_diff_lines: int
    max_context_bytes: int
    ollama_num_ctx: int
    ollama_num_gpu: int
    ollama_keep_alive: str
    polling_interval_s: float
    allow_cloud_ai: bool

    def get_ollama_options(self) -> dict[str, Any]:
        """Return Ollama-compatible inference options."""
        return {
            "num_ctx": self.ollama_num_ctx,
            "num_gpu": self.ollama_num_gpu,
            "temperature": 0.2,
        }

    def get_pool_capacities(self) -> dict[str, int]:
        """Return provider pool capacities for this profile."""
        return {
            "gemini": self.gemini_concurrency,
            "ollama": self.ollama_concurrency,
            "heuristic": self.heuristic_concurrency,
        }


# -----------------------------------------------------------------------------
# Canonical Profile Definitions
# -----------------------------------------------------------------------------

PROFILES: dict[str, ProfileConfig] = {
    ExecutionProfile.NORMAL: ProfileConfig(
        name=ExecutionProfile.NORMAL,
        gemini_concurrency=3,
        ollama_concurrency=1,
        heuristic_concurrency=4,
        timeout_s=30.0,
        max_diff_lines=250,
        max_context_bytes=32768,
        ollama_num_ctx=4096,
        ollama_num_gpu=0,  # balanced default
        ollama_keep_alive="5m",
        polling_interval_s=2.0,
        allow_cloud_ai=True,
    ),
    ExecutionProfile.ECO: ProfileConfig(
        name=ExecutionProfile.ECO,
        gemini_concurrency=1,
        ollama_concurrency=1,
        heuristic_concurrency=1,
        timeout_s=15.0,
        max_diff_lines=100,
        max_context_bytes=8192,
        ollama_num_ctx=2048,
        ollama_num_gpu=0,  # CPU only, save battery/heat
        ollama_keep_alive="1m",
        polling_interval_s=4.0,  # lower polling frequency
        allow_cloud_ai=True,
    ),
    ExecutionProfile.GPU_AI: ProfileConfig(
        name=ExecutionProfile.GPU_AI,
        gemini_concurrency=3,
        ollama_concurrency=2,
        heuristic_concurrency=4,
        timeout_s=45.0,
        max_diff_lines=500,
        max_context_bytes=65536,
        ollama_num_ctx=8192,
        ollama_num_gpu=-1,  # full GPU layer offloading
        ollama_keep_alive="30m",
        polling_interval_s=1.0,  # high responsive polling
        allow_cloud_ai=True,
    ),
    ExecutionProfile.OFFLINE: ProfileConfig(
        name=ExecutionProfile.OFFLINE,
        gemini_concurrency=0,  # cloud calls forbidden
        ollama_concurrency=1,
        heuristic_concurrency=4,
        timeout_s=25.0,
        max_diff_lines=200,
        max_context_bytes=16384,
        ollama_num_ctx=4096,
        ollama_num_gpu=0,
        ollama_keep_alive="5m",
        polling_interval_s=2.0,
        allow_cloud_ai=False,  # strictly offline
    ),
}


def get_profile_config(profile_name: str | None = None) -> ProfileConfig:
    """Resolve ProfileConfig by name, falling back to NORMAL if unspecified."""
    if not profile_name:
        return PROFILES[ExecutionProfile.NORMAL]
    clean_name = str(profile_name).strip().upper()
    return PROFILES.get(clean_name, PROFILES[ExecutionProfile.NORMAL])
