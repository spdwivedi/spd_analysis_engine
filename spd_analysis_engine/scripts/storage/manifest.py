"""
scripts/storage/manifest.py
===========================
File manifest engine tracking file metadata, content hashes, and binary status.

Key invariant:
- Hash only when size or mtime_ns indicates a potential change.
- Fast binary detection (checks initial bytes for null bytes).
- Empty files handled with deterministic empty sha256 hash.
- Supports prefix matching for resolving directory moves to file renames.
- Optional SQLite persistence.
"""
from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .ignore_policy import IgnorePolicy

logger = logging.getLogger(__name__)

EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
CHUNK_SIZE = 65536
PROBE_SIZE = 8192


@dataclass
class FileManifestEntry:
    """Canonical representation of an individual file in the manifest."""
    relative_path: str
    exists: bool = True
    size: int = 0
    mtime_ns: int = 0
    sha256: str | None = None
    is_binary: bool = False
    mode: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FileManifestEntry:
        return cls(
            relative_path=data["relative_path"],
            exists=bool(data.get("exists", True)),
            size=int(data.get("size", 0)),
            mtime_ns=int(data.get("mtime_ns", 0)),
            sha256=data.get("sha256"),
            is_binary=bool(data.get("is_binary", False)),
            mode=int(data.get("mode", 0)),
        )


class FileManifest:
    """
    Project file manifest managing state and fingerprinting across all tracked files.
    """

    def __init__(
        self,
        root_path: Path | str,
        ignore_policy: IgnorePolicy | None = None,
    ) -> None:
        self.root_path = Path(root_path).resolve()
        self.ignore_policy = ignore_policy or IgnorePolicy(self.root_path)
        self._entries: dict[str, FileManifestEntry] = {}

    def __len__(self) -> int:
        return sum(1 for e in self._entries.values() if e.exists)

    def __contains__(self, rel_path: str) -> bool:
        norm = IgnorePolicy.normalize_rel_path(rel_path)
        entry = self._entries.get(norm)
        return entry is not None and entry.exists

    def __getitem__(self, rel_path: str) -> FileManifestEntry:
        norm = IgnorePolicy.normalize_rel_path(rel_path)
        return self._entries[norm]

    def get(self, rel_path: str) -> FileManifestEntry | None:
        norm = IgnorePolicy.normalize_rel_path(rel_path)
        return self._entries.get(norm)

    def all_entries(self) -> list[FileManifestEntry]:
        """Return all tracked entries."""
        return list(self._entries.values())

    def active_entries(self) -> list[FileManifestEntry]:
        """Return only existing entries."""
        return [e for e in self._entries.values() if e.exists]

    # -------------------------------------------------------------------------
    # Core Fingerprinting & Ingestion
    # -------------------------------------------------------------------------

    def record_file(self, rel_path: str | Path) -> FileManifestEntry:
        """
        Record or update file metadata in the manifest.
        Hashes file contents ONLY when size or mtime_ns indicates a change.
        """
        norm_path = IgnorePolicy.normalize_rel_path(rel_path)
        abs_path = self.root_path / norm_path

        if not abs_path.is_file():
            return self.record_deletion(norm_path)

        try:
            st = abs_path.stat()
        except OSError:
            return self.record_deletion(norm_path)

        current_size = st.st_size
        current_mtime_ns = st.st_mtime_ns
        current_mode = st.st_mode

        prev = self._entries.get(norm_path)
        # Hash only when size/mtime indicates potential change
        if (
            prev is not None
            and prev.exists
            and prev.size == current_size
            and prev.mtime_ns == current_mtime_ns
            and prev.sha256 is not None
        ):
            return prev

        # Compute hash and detect binary format
        if current_size == 0:
            sha256_hex = EMPTY_SHA256
            is_bin = False
        else:
            sha256_hex, is_bin = self._hash_file(abs_path)

        entry = FileManifestEntry(
            relative_path=norm_path,
            exists=True,
            size=current_size,
            mtime_ns=current_mtime_ns,
            sha256=sha256_hex,
            is_binary=is_bin,
            mode=current_mode,
        )
        self._entries[norm_path] = entry
        return entry

    def record_deletion(self, rel_path: str | Path) -> FileManifestEntry:
        """Mark a file as deleted in the manifest."""
        norm_path = IgnorePolicy.normalize_rel_path(rel_path)
        entry = FileManifestEntry(
            relative_path=norm_path,
            exists=False,
            size=0,
            mtime_ns=0,
            sha256=None,
            is_binary=False,
            mode=0,
        )
        self._entries[norm_path] = entry
        return entry

    def record_rename(
        self,
        old_rel_path: str | Path,
        new_rel_path: str | Path,
    ) -> FileManifestEntry:
        """Mark old path as deleted and index new path."""
        self.record_deletion(old_rel_path)
        return self.record_file(new_rel_path)

    def find_by_prefix(self, prefix: str) -> list[FileManifestEntry]:
        """
        Find active manifest entries starting with prefix (e.g. 'old_dir/').
        Essential for mapping directory moves to file-level renames.
        """
        norm_prefix = IgnorePolicy.normalize_rel_path(prefix)
        if not norm_prefix.endswith("/"):
            norm_prefix += "/"

        matches: list[FileManifestEntry] = []
        for p, entry in self._entries.items():
            if entry.exists and (p.startswith(norm_prefix) or f"{p}/".startswith(norm_prefix)):
                matches.append(entry)
        return matches

    # -------------------------------------------------------------------------
    # Full Project Scan
    # -------------------------------------------------------------------------

    def scan_all(self) -> int:
        """
        Recursively scan project directory, respecting ignore policy,
        updating existing entries and detecting deleted files.
        """
        if not self.root_path.exists():
            return 0

        found_paths: set[str] = set()

        for dirpath, dirnames, filenames in os.walk(str(self.root_path)):
            rel_dir = os.path.relpath(dirpath, str(self.root_path))
            norm_rel_dir = "" if rel_dir == "." else IgnorePolicy.normalize_rel_path(rel_dir)

            # Prune directory recursion early using should_watch
            dirnames[:] = [
                d for d in dirnames
                if self.ignore_policy.should_watch(
                    f"{norm_rel_dir}/{d}" if norm_rel_dir else d
                )
            ]

            for fname in filenames:
                rel_f = f"{norm_rel_dir}/{fname}" if norm_rel_dir else fname
                norm_rel_f = IgnorePolicy.normalize_rel_path(rel_f)

                if not self.ignore_policy.should_index(norm_rel_f):
                    continue

                found_paths.add(norm_rel_f)
                self.record_file(norm_rel_f)

        # Mark removed files as deleted
        for known_path, entry in list(self._entries.items()):
            if entry.exists and known_path not in found_paths:
                self.record_deletion(known_path)

        return len(found_paths)

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _hash_file(abs_path: Path) -> tuple[str, bool]:
        """Compute SHA-256 hex digest and detect binary content."""
        hasher = hashlib.sha256()
        is_binary = False

        try:
            with open(abs_path, "rb") as f:
                first_chunk = f.read(PROBE_SIZE)
                if b"\x00" in first_chunk:
                    is_binary = True
                hasher.update(first_chunk)
                while chunk := f.read(CHUNK_SIZE):
                    hasher.update(chunk)
            return hasher.hexdigest(), is_binary
        except OSError:
            return "", False

    # -------------------------------------------------------------------------
    # SQLite Persistence
    # -------------------------------------------------------------------------

    def save_to_db(self, conn: sqlite3.Connection) -> None:
        """Persist current manifest state into SQLite table 'file_manifest'."""
        conn.execute("""
            CREATE TABLE IF NOT EXISTS file_manifest (
                relative_path TEXT PRIMARY KEY,
                "exists"      INTEGER NOT NULL,
                size          INTEGER NOT NULL,
                mtime_ns      INTEGER NOT NULL,
                sha256        TEXT,
                is_binary     INTEGER NOT NULL,
                mode          INTEGER NOT NULL
            );
        """)
        conn.execute("BEGIN IMMEDIATE;")
        try:
            for entry in self._entries.values():
                conn.execute(
                    """
                    INSERT INTO file_manifest (relative_path, "exists", size, mtime_ns, sha256, is_binary, mode)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(relative_path) DO UPDATE SET
                        "exists" = excluded."exists",
                        size = excluded.size,
                        mtime_ns = excluded.mtime_ns,
                        sha256 = excluded.sha256,
                        is_binary = excluded.is_binary,
                        mode = excluded.mode;
                    """,
                    (
                        entry.relative_path,
                        1 if entry.exists else 0,
                        entry.size,
                        entry.mtime_ns,
                        entry.sha256,
                        1 if entry.is_binary else 0,
                        entry.mode,
                    ),
                )
            conn.execute("COMMIT;")
        except Exception:
            conn.execute("ROLLBACK;")
            raise

    def load_from_db(self, conn: sqlite3.Connection) -> None:
        """Load manifest state from SQLite table 'file_manifest'."""
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='file_manifest';")
        if not cur.fetchone():
            return
        cur.execute('SELECT relative_path, "exists", size, mtime_ns, sha256, is_binary, mode FROM file_manifest;')
        for row in cur.fetchall():
            entry = FileManifestEntry(
                relative_path=row[0],
                exists=bool(row[1]),
                size=row[2],
                mtime_ns=row[3],
                sha256=row[4],
                is_binary=bool(row[5]),
                mode=row[6],
            )
            self._entries[entry.relative_path] = entry


# Backward compatibility and lowercase alias
file_manifest = FileManifest
