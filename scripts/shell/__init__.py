"""
scripts/shell/__init__.py
=========================
Modularized terminal command tracking and execution interception package.
"""

from .classifier import (
    SHELL_HOST_NAMES,
    IGNORED_COMMANDS,
    IGNORED_PATH_PATTERNS,
    IGNORED_EXEC_PATTERNS,
    is_ignored_command,
    detect_executor,
)
from .cmd_parser import scrub_tokens, _detect_file_reads_from_cmd, _is_path_inside_target
from .tracker import ExecutionTracker

__all__ = [
    "ExecutionTracker",
    "detect_executor",
    "is_ignored_command",
    "scrub_tokens",
    "SHELL_HOST_NAMES",
    "IGNORED_COMMANDS",
    "IGNORED_PATH_PATTERNS",
    "IGNORED_EXEC_PATTERNS",
    "_detect_file_reads_from_cmd",
    "_is_path_inside_target",
]
