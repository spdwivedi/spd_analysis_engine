"""
scripts/storage/fingerprint.py
==============================
Fingerprint-based effective change detection.

Compares file fingerprints (existence, size, content hash, binary status)
rather than relying on diff-string emptiness.

Correctly identifies:
- Empty file creation
- Deletion
- Binary mutations
- Textual modifications
While ignoring timestamp-only rewrites when content is identical.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .manifest import FileManifest, FileManifestEntry

EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
CHUNK_SIZE = 65536
PROBE_SIZE = 8192


@dataclass(frozen=True)
class FileFingerprint:
    """Immutable point-in-time snapshot of file identity and content."""
    relative_path: str
    exists: bool
    size: int
    sha256: str | None
    is_binary: bool = False
    mtime_ns: int = 0

    @classmethod
    def from_manifest_entry(cls, entry: FileManifestEntry) -> FileFingerprint:
        return cls(
            relative_path=entry.relative_path,
            exists=entry.exists,
            size=entry.size,
            sha256=entry.sha256,
            is_binary=entry.is_binary,
            mtime_ns=entry.mtime_ns,
        )


@dataclass(frozen=True)
class ChangeResult:
    """Outcome of comparing before and after fingerprints for a single file."""
    relative_path: str
    is_effective: bool
    change_type: str  # 'created', 'deleted', 'modified', 'unchanged'
    is_binary: bool = False
    is_empty: bool = False
    before: FileFingerprint | None = None
    after: FileFingerprint | None = None


def compute_fingerprint(
    abs_path: Path,
    rel_path: str,
) -> FileFingerprint:
    """Compute FileFingerprint directly from disk."""
    norm_rel = rel_path.replace("\\", "/").strip("/")
    if not abs_path.is_file():
        return FileFingerprint(
            relative_path=norm_rel,
            exists=False,
            size=0,
            sha256=None,
            is_binary=False,
            mtime_ns=0,
        )

    try:
        st = abs_path.stat()
    except OSError:
        return FileFingerprint(
            relative_path=norm_rel,
            exists=False,
            size=0,
            sha256=None,
            is_binary=False,
            mtime_ns=0,
        )

    size = st.st_size
    mtime_ns = st.st_mtime_ns

    if size == 0:
        return FileFingerprint(
            relative_path=norm_rel,
            exists=True,
            size=0,
            sha256=EMPTY_SHA256,
            is_binary=False,
            mtime_ns=mtime_ns,
        )

    hasher = hashlib.sha256()
    is_binary = False
    try:
        with open(abs_path, "rb") as f:
            probe = f.read(PROBE_SIZE)
            if b"\x00" in probe:
                is_binary = True
            hasher.update(probe)
            while chunk := f.read(CHUNK_SIZE):
                hasher.update(chunk)
        sha256_hex = hasher.hexdigest()
    except OSError:
        return FileFingerprint(
            relative_path=norm_rel,
            exists=False,
            size=0,
            sha256=None,
            is_binary=False,
            mtime_ns=0,
        )

    return FileFingerprint(
        relative_path=norm_rel,
        exists=True,
        size=size,
        sha256=sha256_hex,
        is_binary=is_binary,
        mtime_ns=mtime_ns,
    )


def compare_fingerprints(
    before: FileFingerprint | None,
    after: FileFingerprint | None,
    rel_path: str | None = None,
) -> ChangeResult:
    """
    Determine if an effective change occurred between before and after states.
    Ignores timestamp-only rewrites when content is unchanged.
    """
    path = rel_path or (after.relative_path if after else (before.relative_path if before else ""))
    b_exists = before is not None and before.exists
    a_exists = after is not None and after.exists

    # Neither exists
    if not b_exists and not a_exists:
        return ChangeResult(
            relative_path=path,
            is_effective=False,
            change_type="unchanged",
            before=before,
            after=after,
        )

    # Creation
    if not b_exists and a_exists:
        assert after is not None
        return ChangeResult(
            relative_path=path,
            is_effective=True,
            change_type="created",
            is_binary=after.is_binary,
            is_empty=(after.size == 0),
            before=before,
            after=after,
        )

    # Deletion
    if b_exists and not a_exists:
        assert before is not None
        return ChangeResult(
            relative_path=path,
            is_effective=True,
            change_type="deleted",
            is_binary=before.is_binary,
            is_empty=(before.size == 0),
            before=before,
            after=after,
        )

    # Both exist: compare content
    assert before is not None and after is not None
    if before.sha256 != after.sha256 or before.size != after.size:
        return ChangeResult(
            relative_path=path,
            is_effective=True,
            change_type="modified",
            is_binary=(before.is_binary or after.is_binary),
            is_empty=(after.size == 0),
            before=before,
            after=after,
        )

    # Content unchanged (even if mtime_ns differed)
    return ChangeResult(
        relative_path=path,
        is_effective=False,
        change_type="unchanged",
        is_binary=after.is_binary,
        is_empty=(after.size == 0),
        before=before,
        after=after,
    )
