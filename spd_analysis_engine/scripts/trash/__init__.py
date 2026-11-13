"""
scripts/trash/__init__.py
=========================
Modularized trash management package for the SPD Analysis Engine.
"""

from .constants import (
    SCOPE_SELECTIVE_BURSTS,
    SCOPE_METADATA_ONLY,
    SCOPE_LOCAL_SESSION,
    SCOPE_SESSION_AND_SHADOW_GIT,
    SCOPE_COMPLETE_ERASE,
    VALID_SCOPES,
)
from .fs_ops import (
    _force_rmtree,
    _safe_rmtree,
    _safe_move_tree,
    _strip_readonly_recursive,
    _parse_orphan_trash_dir,
    _remove_readonly_recursive,
)
from .manager import TrashManager
from .burst_trash import delete_selective_bursts
from .purge import list_trash_entries, purge_trash_entry, purge_all_trash

__all__ = [
    "TrashManager",
    "_force_rmtree",
    "_safe_rmtree",
    "_safe_move_tree",
    "_strip_readonly_recursive",
    "_parse_orphan_trash_dir",
    "_remove_readonly_recursive",
    "delete_selective_bursts",
    "list_trash_entries",
    "purge_trash_entry",
    "purge_all_trash",
    "SCOPE_SELECTIVE_BURSTS",
    "SCOPE_METADATA_ONLY",
    "SCOPE_LOCAL_SESSION",
    "SCOPE_SESSION_AND_SHADOW_GIT",
    "SCOPE_COMPLETE_ERASE",
    "VALID_SCOPES",
]
