"""
scripts/storage/ignore_policy.py
=================================
Unified ignore policy layer for SPD Analysis Engine.

Evaluation layers:
1. Built-in ignored directories/files (cheap string checks in hot paths).
2. Project-level .gitignore.
3. Optional .spdignore.
4. Sensitive-file blocklist (.env*, *.pem, *.key, id_rsa*, credentials*, *.pfx).

Sensitive files are metadata-only by default (watched and indexed in manifest,
but never diffed, staged to git, or sent to AI).
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Built-in directories ignored anywhere in path (vendor, metadata, caches)
BUILTIN_DIR_NAMES: set[str] = {
    ".git",
    ".spd",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".idea",
    ".vscode",
    ".pytest_cache",
    ".mypy_cache",
    ".sf",
    ".sfdx",
}

# Root-level only directories (must NOT match nested user folders like src/patches)
ROOT_ONLY_DIRS: tuple[str, ...] = ("patches/", "baseline/")

# Built-in exact files (engine internal storage & daemon coordination)
BUILTIN_EXACT_FILES: set[str] = {
    "session.db",
    "session.db-wal",
    "session.db-shm",
    "worker.log",
    "worker.pid",
    "worker.pid.stale",
    "worker.status.json",
    "project.meta",
    ".engine_sync.lock",
    "engine.db",
    "engine.db-wal",
    "engine.db-shm",
    "engine.lock",
}

# Fast extension blocklist for editor temporary/swap/cache files
EDITOR_TEMP_EXTENSIONS: tuple[str, ...] = (
    ".tmp",
    ".swp",
    ".swo",
    ".pyc",
    ".pyo",
    ".pyd",
    ".bak",
    "~",
)

# Sensitive default patterns: metadata-only by default
DEFAULT_SENSITIVE_PATTERNS: tuple[str, ...] = (
    ".env*",
    "*.pem",
    "*.key",
    "id_rsa*",
    "credentials*",
    "*.pfx",
    "*.pkcs12",
    "*.p12",
    "*id_ed25519*",
    "*.kdbx",
)


def _compile_glob_to_regex(pattern: str) -> tuple[bool, re.Pattern]:
    """Compile a gitignore-style glob pattern into (is_negation, re.Pattern)."""
    is_negation = False
    pat = pattern.strip()
    if pat.startswith("!"):
        is_negation = True
        pat = pat[1:].strip()

    is_dir_only = pat.endswith("/")
    if is_dir_only:
        pat = pat[:-1]

    is_rooted = pat.startswith("/") or ("/" in pat)
    if pat.startswith("/"):
        pat = pat[1:]

    sub_parts: list[str] = []
    i = 0
    n = len(pat)
    while i < n:
        if pat[i:i + 2] == "**":
            sub_parts.append("(?:.+/)?")
            i += 2
            if i < n and pat[i] == "/":
                i += 1
        elif pat[i] == "*":
            sub_parts.append("[^/]*")
            i += 1
        elif pat[i] == "?":
            sub_parts.append("[^/]")
            i += 1
        else:
            sub_parts.append(re.escape(pat[i]))
            i += 1

    body = "".join(sub_parts)
    if is_rooted:
        rx_str = rf"^{body}(?:/.*)?$" if is_dir_only else rf"^{body}$"
    else:
        rx_str = rf"(?:^|.*/){body}(?:/.*)?$" if is_dir_only else rf"(?:^|.*/){body}$"

    return is_negation, re.compile(rx_str, re.IGNORECASE)


def _parse_ignore_content(content: str) -> list[tuple[bool, re.Pattern]]:
    """Parse .gitignore or .spdignore file content into compiled rule tuples."""
    rules: list[tuple[bool, re.Pattern]] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            rules.append(_compile_glob_to_regex(line))
        except Exception as exc:
            logger.debug("Failed to compile pattern '%s': %s", line, exc)
    return rules


class IgnorePolicy:
    """
    Authoritative ignore policy enforcing multi-layer filtering:
    1. Built-in system/engine ignore rules
    2. Project .gitignore
    3. Optional .spdignore
    4. Sensitive file blocklist
    """

    def __init__(
        self,
        root_path: Path | str | None = None,
        gitignore_content: str | None = None,
        spdignore_content: str | None = None,
        extra_sensitive_patterns: list[str] | None = None,
    ) -> None:
        self.root_path = Path(root_path).resolve() if root_path else None
        self._custom_gi_content = gitignore_content
        self._custom_spd_content = spdignore_content
        self._extra_sensitive = tuple(extra_sensitive_patterns or ())

        self._gitignore_rules: list[tuple[bool, re.Pattern]] = []
        self._spdignore_rules: list[tuple[bool, re.Pattern]] = []
        self._sensitive_rules: list[tuple[bool, re.Pattern]] = []

        self._cache_gi: dict[str, bool] = {}
        self._cache_spd: dict[str, bool] = {}

        self.reload()

    def reload(self) -> None:
        """Reload .gitignore and .spdignore from disk or custom content."""
        self._cache_gi.clear()
        self._cache_spd.clear()

        # 1. Compile sensitive patterns
        all_sens = DEFAULT_SENSITIVE_PATTERNS + self._extra_sensitive
        self._sensitive_rules = [_compile_glob_to_regex(pat) for pat in all_sens]

        # 2. Load .gitignore
        gi_text = self._custom_gi_content
        if gi_text is None and self.root_path:
            gi_file = self.root_path / ".gitignore"
            if gi_file.is_file():
                try:
                    gi_text = gi_file.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    gi_text = ""
        self._gitignore_rules = _parse_ignore_content(gi_text or "")

        # 3. Load .spdignore
        spd_text = self._custom_spd_content
        if spd_text is None and self.root_path:
            spd_file = self.root_path / ".spdignore"
            if spd_file.is_file():
                try:
                    spd_text = spd_file.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    spd_text = ""
        self._spdignore_rules = _parse_ignore_content(spd_text or "")

    # -------------------------------------------------------------------------
    # Path Normalization & Cheap Hot-Path Checks
    # -------------------------------------------------------------------------

    @staticmethod
    def normalize_rel_path(path: str | Path) -> str:
        """Convert arbitrary system path to normalized posix-style relative path."""
        p_str = str(path).replace("\\", "/").strip()
        if p_str.startswith("./"):
            p_str = p_str[2:]
        return p_str.strip("/")

    def is_builtin_ignored(self, rel_path: str) -> bool:
        """
        Fast string-prefix and exact-match checks for engine/system noise.
        Avoids naive substring checks (e.g. nested 'src/patches' is allowed).
        """
        p = self.normalize_rel_path(rel_path)
        if not p or p == ".":
            return True

        # Root-level only directories
        for root_dir in ROOT_ONLY_DIRS:
            if p.startswith(root_dir) or p == root_dir[:-1]:
                return True

        parts = p.split("/")
        name = parts[-1]

        # Exact filenames
        if name in BUILTIN_EXACT_FILES:
            return True

        # Editor temporary / swap files
        if name.endswith(EDITOR_TEMP_EXTENSIONS) or name.startswith((".#", "#", "4913")):
            return True

        # Directory matches anywhere in path (e.g. __pycache__, node_modules, .git)
        for part in parts:
            if part in BUILTIN_DIR_NAMES or part.startswith(".sf"):
                return True

        return False

    def is_sensitive(self, rel_path: str) -> bool:
        """Check if file matches sensitive patterns (.env*, *.key, id_rsa*, etc.)."""
        p = self.normalize_rel_path(rel_path)
        name = p.split("/")[-1]
        for _, rx in self._sensitive_rules:
            if rx.match(p) or rx.match(name):
                return True
        return False

    def is_spdignored(self, rel_path: str) -> bool:
        """Check if file matches explicit project .spdignore rules."""
        if not self._spdignore_rules:
            return False
        p = self.normalize_rel_path(rel_path)
        if p in self._cache_spd:
            return self._cache_spd[p]

        matched = False
        for is_neg, rx in self._spdignore_rules:
            if rx.match(p):
                matched = not is_neg
        self._cache_spd[p] = matched
        return matched

    def is_gitignored(self, rel_path: str) -> bool:
        """Check if file matches project .gitignore rules."""
        if not self._gitignore_rules:
            return False
        p = self.normalize_rel_path(rel_path)
        if p in self._cache_gi:
            return self._cache_gi[p]

        matched = False
        for is_neg, rx in self._gitignore_rules:
            if rx.match(p):
                matched = not is_neg
        self._cache_gi[p] = matched
        return matched

    # -------------------------------------------------------------------------
    # Authoritative Public Evaluation Methods
    # -------------------------------------------------------------------------

    def should_watch(self, rel_path: str | Path) -> bool:
        """Return True if the file system observer should monitor this path."""
        p = self.normalize_rel_path(rel_path)
        if self.is_builtin_ignored(p) or self.is_spdignored(p):
            return False
        if self.is_sensitive(p):
            return True  # Sensitive files are metadata-only: watched for changes
        return not self.is_gitignored(p)

    def should_index(self, rel_path: str | Path) -> bool:
        """Return True if path should be tracked in file_manifest."""
        p = self.normalize_rel_path(rel_path)
        if self.is_builtin_ignored(p) or self.is_spdignored(p):
            return False
        if self.is_sensitive(p):
            return True  # Sensitive files are metadata-only: indexed in manifest
        return not self.is_gitignored(p)

    def should_diff(self, rel_path: str | Path) -> bool:
        """Return True if diff content should be calculated and stored."""
        p = self.normalize_rel_path(rel_path)
        if self.is_builtin_ignored(p) or self.is_spdignored(p):
            return False
        if self.is_sensitive(p):
            return False  # Sensitive files are strictly metadata-only (NEVER diffed)
        return not self.is_gitignored(p)

    def should_stage(self, rel_path: str | Path) -> bool:
        """Return True if file should be staged into shadow git tracking."""
        p = self.normalize_rel_path(rel_path)
        if self.is_builtin_ignored(p) or self.is_spdignored(p):
            return False
        if self.is_sensitive(p):
            return False  # Sensitive files are strictly metadata-only (NEVER staged)
        return not self.is_gitignored(p)

    def should_send_to_ai(self, rel_path: str | Path) -> bool:
        """Return True if file content/patches may be sent to AI models."""
        p = self.normalize_rel_path(rel_path)
        if self.is_builtin_ignored(p) or self.is_spdignored(p):
            return False
        if self.is_sensitive(p):
            return False  # Sensitive files are strictly metadata-only (NEVER sent to AI)
        return not self.is_gitignored(p)
