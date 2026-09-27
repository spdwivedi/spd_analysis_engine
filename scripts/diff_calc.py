"""
scripts/diff_calc.py
====================
Unified diff calculator and baseline cache manager for the SPD Analysis Engine.

Classes
-------
DiffCalculator
    Maintains an in-memory and disk-backed baseline of tracked files in
    ``current/<project_slug>/baseline/``. Computes standard unified diffs
    using Python's ``difflib`` and detects entrypoint edit files.
"""

from __future__ import annotations

import difflib
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Directory names to ignore during scanning and baseline capture
DEFAULT_IGNORED_DIRS: set[str] = {
    ".git",
    ".sf",
    ".sfdx",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".idea",
    ".vscode",
    "patches",
    "baseline",
    ".mypy_cache",
    ".pytest_cache",
}

#: File patterns / names to ignore during baseline capture
DEFAULT_IGNORED_EXTENSIONS: set[str] = {
    ".tmp",
    ".swp",
    ".swo",
    ".pyc",
    ".pyo",
    ".bak",
    ".log",
    ".db",
    ".db-shm",
    ".db-wal",
    ".pid",
}

DEFAULT_IGNORED_FILES: set[str] = {
    "session.db",
    "session.db-shm",
    "session.db-wal",
    "worker.log",
    "worker.pid",
    "worker.pid.stale",
    "worker.status.json",
    "project.meta",
}


def is_binary_file(file_path: Path, block_size: int = 8192) -> bool:
    """
    Heuristically determine if a file is binary by looking for null bytes
    or decoding errors in the first *block_size* bytes.
    """
    try:
        with open(file_path, "rb") as f:
            chunk = f.read(block_size)
            if b"\x00" in chunk:
                return True
            try:
                chunk.decode("utf-8")
            except UnicodeDecodeError:
                return True
        return False
    except OSError:
        return True


class DiffCalculator:
    """
    Manages file baseline snapshots and computes unified diffs.

    Parameters
    ----------
    baseline_dir : Path | str
        Directory where snapshot files are stored (``current/<slug>/baseline/``).
    target_dir : Path | str
        Root directory of the workspace being monitored.
    """

    def __init__(
        self,
        baseline_dir: Path | str,
        target_dir: Path | str,
        ignored_dirs: set[str] | None = None,
        ignored_extensions: set[str] | None = None,
    ) -> None:
        self.baseline_dir = Path(baseline_dir).resolve()
        self.target_dir = Path(target_dir).resolve()
        self.ignored_dirs = ignored_dirs or DEFAULT_IGNORED_DIRS
        self.ignored_extensions = ignored_extensions or DEFAULT_IGNORED_EXTENSIONS

        # In-memory baseline cache: { "rel/path/to/file.py": "file content..." }
        self._cache: dict[str, str] = {}

        # Ensure baseline directory exists on disk
        self.baseline_dir.mkdir(parents=True, exist_ok=True)

    def is_ignored(self, path: Path | str) -> bool:
        """
        Check if a file or directory path should be ignored.
        """
        p = Path(path)
        parts = p.parts
        for part in parts:
            if part in self.ignored_dirs or part.startswith(".sf"):
                return True
        name = p.name
        if name in DEFAULT_IGNORED_FILES:
            return True
        if name.startswith("catalog.json") or name.endswith(".__staging__") or name.endswith(".tmp"):
            return True
        if p.suffix.lower() in self.ignored_extensions:
            return True
        return False

    def normalize_rel_path(self, path: Path | str) -> str:
        """
        Convert an absolute or relative path to a normalized relative path
        (POSIX-style forward slashes) against ``target_dir``.
        """
        p = Path(path)
        if p.is_absolute():
            try:
                rel = p.relative_to(self.target_dir)
            except ValueError:
                # Path is outside target_dir, return normalized str
                return str(p).replace("\\", "/")
        else:
            rel = p
        return str(rel).replace("\\", "/")

    def capture_baseline(self, target_dir: Path | str | None = None) -> dict[str, str]:
        """
        Take an initial snapshot of all text files in ``target_dir``.

        Skips binary files, directories matching ignore filters, and hidden tooling files.
        Populates both in-memory cache and disk files in ``baseline_dir``.

        Returns
        -------
        dict[str, str]
            Mapping of relative file path -> file content.
        """
        root = Path(target_dir).resolve() if target_dir else self.target_dir
        if not root.exists():
            logger.warning("Target directory does not exist for baseline capture: %s", root)
            return {}

        self._cache.clear()

        for dirpath, dirnames, filenames in os.walk(root):
            # Prune ignored directories in-place
            dirnames[:] = [d for d in dirnames if d not in self.ignored_dirs and not d.startswith(".sf")]

            for fname in filenames:
                file_path = Path(dirpath) / fname
                if self.is_ignored(file_path):
                    continue

                if is_binary_file(file_path):
                    logger.debug("Skipping binary file in baseline: %s", file_path)
                    continue

                rel_path = self.normalize_rel_path(file_path)
                try:
                    content = file_path.read_text(encoding="utf-8", errors="replace")
                    self._cache[rel_path] = content

                    # Persist to baseline_dir on disk
                    disk_target = self.baseline_dir / Path(rel_path)
                    disk_target.parent.mkdir(parents=True, exist_ok=True)
                    disk_target.write_text(content, encoding="utf-8")
                except OSError as exc:
                    logger.warning("Failed to read file for baseline %s: %s", file_path, exc)

        logger.info(
            "Captured baseline with %d text files in %s",
            len(self._cache), self.baseline_dir,
        )
        return dict(self._cache)

    def get_baseline(self, rel_path: str) -> str | None:
        """
        Retrieve the baseline content for a file.
        Checks in-memory cache first, then disk baseline file.
        Returns ``None`` if file was not in the baseline (e.g. newly created file).
        """
        norm = self.normalize_rel_path(rel_path)
        if norm in self._cache:
            return self._cache[norm]

        disk_path = self.baseline_dir / Path(norm)
        if disk_path.is_file():
            try:
                content = disk_path.read_text(encoding="utf-8", errors="replace")
                self._cache[norm] = content
                return content
            except OSError:
                return None
        return None

    def update_baseline(self, rel_path: str, new_content: str | None) -> None:
        """
        Update the baseline for a file after a burst is processed.

        If *new_content* is ``None``, the file has been deleted and is
        evicted from cache and disk.
        """
        norm = self.normalize_rel_path(rel_path)
        disk_path = self.baseline_dir / Path(norm)

        if new_content is None:
            self._cache.pop(norm, None)
            if disk_path.exists():
                try:
                    disk_path.unlink()
                except OSError as exc:
                    logger.debug("Could not remove baseline file %s: %s", disk_path, exc)
        else:
            self._cache[norm] = new_content
            try:
                disk_path.parent.mkdir(parents=True, exist_ok=True)
                disk_path.write_text(new_content, encoding="utf-8")
            except OSError as exc:
                logger.warning("Could not persist updated baseline file %s: %s", disk_path, exc)

    def compute_diff(
        self,
        file_path: str,
        old_content: str | None = None,
        new_content: str | None = None,
    ) -> str:
        """
        Compute standard unified diff string with file headers and line numbers.

        Parameters
        ----------
        file_path : str
            Relative or absolute file path.
        old_content : str | None
            Previous content (retrieved from baseline if ``None``).
        new_content : str | None
            Current content on disk (read from disk if ``None``).

        Returns
        -------
        str
            Unified diff formatted string, or empty string if no diff.
        """
        norm = self.normalize_rel_path(file_path)

        if old_content is None:
            old_content = self.get_baseline(norm) or ""

        if new_content is None:
            abs_path = self.target_dir / Path(norm)
            if abs_path.is_file():
                try:
                    if is_binary_file(abs_path):
                        return ""
                    new_content = abs_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    new_content = ""
            else:
                # File deleted
                new_content = ""

        if old_content == new_content:
            return ""

        old_lines = old_content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)

        from_file = f"a/{norm}" if old_content else "/dev/null"
        to_file = f"b/{norm}" if new_content else "/dev/null"

        diff_lines = list(
            difflib.unified_diff(
                old_lines,
                new_lines,
                fromfile=from_file,
                tofile=to_file,
                lineterm="",
            )
        )

        if not diff_lines:
            return ""

        return "\n".join(diff_lines) + "\n"

    @staticmethod
    def detect_first_file(changes_list: list[dict[str, Any]]) -> str | None:
        """
        Analyze timestamps of modified files within a burst to pinpoint
        the earliest touched file (the AI entrypoint file).

        Parameters
        ----------
        changes_list : list[dict]
            List of event dicts containing at least:
            ``{"file_path": ..., "timestamp": float}`` or
            ``{"file_path": ..., "ts": ...}``.

        Returns
        -------
        str | None
            The relative or absolute file_path of the earliest event.
        """
        if not changes_list:
            return None

        def _sort_key(item: dict[str, Any]) -> float:
            if "timestamp" in item and isinstance(item["timestamp"], (int, float)):
                return float(item["timestamp"])
            if "time" in item and isinstance(item["time"], (int, float)):
                return float(item["time"])
            return 0.0

        sorted_changes = sorted(changes_list, key=_sort_key)
        return str(sorted_changes[0].get("file_path", ""))
