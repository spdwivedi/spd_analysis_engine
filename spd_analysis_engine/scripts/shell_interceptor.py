"""
scripts/shell_interceptor.py
============================
Backward-compatible facade for the modularized shell interception layer.
All symbols are re-exported from scripts/shell/ sub-modules.
"""

from __future__ import annotations

from .shell import (
    ExecutionTracker,
    detect_executor,
    is_ignored_command,
    scrub_tokens,
    SHELL_HOST_NAMES,
    IGNORED_COMMANDS,
    IGNORED_PATH_PATTERNS,
    IGNORED_EXEC_PATTERNS,
)

__all__ = [
    "ExecutionTracker",
    "detect_executor",
    "is_ignored_command",
    "scrub_tokens",
    "SHELL_HOST_NAMES",
    "IGNORED_COMMANDS",
    "IGNORED_PATH_PATTERNS",
    "IGNORED_EXEC_PATTERNS",
]
