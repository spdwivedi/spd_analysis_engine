"""
scripts/ai/identity.py
======================
AI Analysis identity, canonical cache key computation, status constants,
and progressive level definitions for the SPD Analysis Engine.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from typing import Any


# -----------------------------------------------------------------------------
# Status Constants
# -----------------------------------------------------------------------------

class AnalysisStatus:
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SUPERSEDED = "superseded"

    ALL = {QUEUED, RUNNING, DONE, FAILED, SUPERSEDED}


# -----------------------------------------------------------------------------
# Progressive Level Constants
# -----------------------------------------------------------------------------

class ProgressiveLevel:
    L1 = "L1"  # Quick: Executive summary and high-level intent
    L2 = "L2"  # Detailed: Per-file and functional mechanics
    L3 = "L3"  # Architecture: Structural invariants, coupling, boundaries
    L4 = "L4"  # Risk/Impact: Regression vectors, edge cases, rollback

    ALL = (L1, L2, L3, L4)

    DESCRIPTIONS = {
        L1: "L1 Quick",
        L2: "L2 Detailed",
        L3: "L3 Architecture",
        L4: "L4 Risk/Impact",
    }


EXPAND_INSTRUCTION = "expand, don't restate"


# -----------------------------------------------------------------------------
# Hash & Cache Key Computation
# -----------------------------------------------------------------------------

def compute_content_hash(content: str | bytes | dict[str, Any] | list[Any] | None) -> str:
    """Compute deterministic SHA-256 hex digest of arbitrary content."""
    if content is None:
        return hashlib.sha256(b"").hexdigest()
    if isinstance(content, bytes):
        return hashlib.sha256(content).hexdigest()
    if isinstance(content, (dict, list)):
        serialized = json.dumps(content, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return hashlib.sha256(str(content).strip().encode("utf-8")).hexdigest()


def compute_analysis_cache_key(
    target_kind: str,
    target_id: str,
    profile: str,
    input_hash: str,
    model: str,
    provider: str,
    prompt_version: str,
    sanitizer_version: str,
    context_hash: str,
) -> str:
    """
    Compute canonical unique SHA-256 cache key from the 9 mandatory identity components:
    1. target_kind
    2. target_id
    3. profile
    4. input_hash
    5. model
    6. provider
    7. prompt_version
    8. sanitizer_version
    9. context_hash
    """
    components = {
        "target_kind": str(target_kind).strip(),
        "target_id": str(target_id).strip(),
        "profile": str(profile).strip(),
        "input_hash": str(input_hash).strip(),
        "model": str(model).strip(),
        "provider": str(provider).strip(),
        "prompt_version": str(prompt_version).strip(),
        "sanitizer_version": str(sanitizer_version).strip(),
        "context_hash": str(context_hash).strip(),
    }
    canonical_repr = json.dumps(components, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_repr.encode("utf-8")).hexdigest()


PROMPT_VERSION = "v1.0"
DEFAULT_SANITIZER_VERSION = "v2.0"


@dataclass(frozen=True)
class AnalysisIdentity:
    """Canonical value object encapsulating the 9 identity components of an AI analysis."""

    target_kind: str
    target_id: str
    profile: str
    input_hash: str
    model: str
    provider: str
    prompt_version: str = PROMPT_VERSION
    sanitizer_version: str = DEFAULT_SANITIZER_VERSION
    context_hash: str = ""

    @property
    def cache_key(self) -> str:
        """Return canonical deterministic cache key."""
        return compute_analysis_cache_key(
            target_kind=self.target_kind,
            target_id=self.target_id,
            profile=self.profile,
            input_hash=self.input_hash,
            model=self.model,
            provider=self.provider,
            prompt_version=self.prompt_version,
            sanitizer_version=self.sanitizer_version,
            context_hash=self.context_hash,
        )

    def to_dict(self) -> dict[str, str]:
        """Convert identity fields to dict."""
        d = asdict(self)
        d["cache_key"] = self.cache_key
        return d
