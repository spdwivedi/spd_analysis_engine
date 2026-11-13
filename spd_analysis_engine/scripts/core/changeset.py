"""
scripts/core/changeset.py
=========================
ChangeSet: Authoritative domain object produced after burst sealing.

Encapsulates point-in-time filesystem mutations in an effective burst:
- burst_uid: unique identifier of the burst
- event_id / sequence: monotonically increasing burst identity
- files: all workspace-relative files touched in this burst window
- effective_files: subset of files with genuine content changes (before != after)
- renames: detected (src_path, dst_path) file renames
- before_fingerprints: snapshot of file metadata/hashes before the burst
- after_fingerprints: snapshot of file metadata/hashes after the burst
- git_state: tracking state (pending, running, committed, skipped_no_change, skipped_overload, failed)
- git_commit: SHA-1 hash if committed
- git_error: error message if failed
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..storage.fingerprint import FileFingerprint

DEFAULT_MAX_TEXT_BYTES: int = 1_048_576    # 1 MB
DEFAULT_MAX_PATCH_BYTES: int = 2_097_152   # 2 MB


@dataclass
class ChangeSet:
    """Central domain object capturing changes from a sealed burst."""
    burst_uid: str
    event_id: int | None = None
    sequence: int = 0
    files: list[str] = field(default_factory=list)
    effective_files: list[str] = field(default_factory=list)
    renames: list[tuple[str, str]] = field(default_factory=list)
    before_fingerprints: dict[str, Any] = field(default_factory=dict)
    after_fingerprints: dict[str, Any] = field(default_factory=dict)
    git_state: str = "pending"  # pending, running, committed, skipped_no_change, skipped_overload, failed
    git_commit: str | None = None
    git_error: str | None = None
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_effective(self) -> bool:
        """Return True if any file has effective content changes or was renamed."""
        return bool(self.effective_files or self.renames)

    @property
    def is_empty(self) -> bool:
        """Return True if no files were touched in this burst."""
        return not bool(self.files)

    def to_dict(self) -> dict[str, Any]:
        """Convert ChangeSet to a JSON-serializable dictionary."""
        return {
            "burst_uid": self.burst_uid,
            "event_id": self.event_id,
            "sequence": self.sequence,
            "files": list(self.files),
            "effective_files": list(self.effective_files),
            "renames": [list(r) for r in self.renames],
            "before_fingerprints": {
                k: (v.__dict__ if hasattr(v, "__dict__") else v)
                for k, v in self.before_fingerprints.items()
            },
            "after_fingerprints": {
                k: (v.__dict__ if hasattr(v, "__dict__") else v)
                for k, v in self.after_fingerprints.items()
            },
            "git_state": self.git_state,
            "git_commit": self.git_commit,
            "git_error": self.git_error,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChangeSet:
        """Reconstruct ChangeSet from a dictionary."""
        before_fps: dict[str, Any] = {}
        for k, v in data.get("before_fingerprints", {}).items():
            if v is not None and isinstance(v, dict):
                before_fps[k] = FileFingerprint(**v)
            else:
                before_fps[k] = v

        after_fps: dict[str, Any] = {}
        for k, v in data.get("after_fingerprints", {}).items():
            if v is not None and isinstance(v, dict):
                after_fps[k] = FileFingerprint(**v)
            else:
                after_fps[k] = v

        renames = [tuple(r) for r in data.get("renames", [])]

        return cls(
            burst_uid=data.get("burst_uid", ""),
            event_id=data.get("event_id"),
            sequence=data.get("sequence", 0),
            files=data.get("files", []),
            effective_files=data.get("effective_files", []),
            renames=renames,
            before_fingerprints=before_fps,
            after_fingerprints=after_fps,
            git_state=data.get("git_state", "pending"),
            git_commit=data.get("git_commit"),
            git_error=data.get("git_error"),
            created_at=data.get("created_at", time.time()),
            metadata=data.get("metadata", {}),
        )


def check_file_size_policy(
    abs_path: Path,
    max_text_bytes: int = DEFAULT_MAX_TEXT_BYTES,
) -> tuple[bool, int, str | None]:
    """
    Check if a file exceeds max_text_bytes without loading the whole file into RAM.
    Returns (exceeds_limit, size_bytes, sha256_hash).
    """
    try:
        size = abs_path.stat().st_size
        if size > max_text_bytes:
            hasher = hashlib.sha256()
            with open(abs_path, "rb") as f:
                while chunk := f.read(65536):
                    hasher.update(chunk)
            return True, size, hasher.hexdigest()
        return False, size, None
    except OSError:
        return False, 0, None


def truncate_patch_content(
    patch_text: str,
    max_patch_bytes: int = DEFAULT_MAX_PATCH_BYTES,
) -> tuple[str, bool, int]:
    """
    Truncate patch text if it exceeds max_patch_bytes.
    Returns (result_text, is_truncated, original_byte_size).
    """
    encoded = patch_text.encode("utf-8", errors="replace")
    orig_size = len(encoded)
    if orig_size <= max_patch_bytes:
        return patch_text, False, orig_size
    truncated_bytes = encoded[:max_patch_bytes]
    truncated_str = (
        truncated_bytes.decode("utf-8", errors="ignore")
        + "\n\n# --- [PATCH TRUNCATED: Exceeded max_patch_bytes limit] ---\n"
    )
    return truncated_str, True, orig_size
