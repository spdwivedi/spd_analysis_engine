"""
scripts/trash_manager.py
========================
Backward-compatible facade for the modularized trash management layer.
All symbols are re-exported from scripts/trash/ sub-modules.
"""

from __future__ import annotations

try:
    from .trash import (
        TrashManager,
        _force_rmtree,
        _safe_rmtree,
        _safe_move_tree,
        _strip_readonly_recursive,
        _remove_readonly_recursive,
        _parse_orphan_trash_dir,
        SCOPE_SELECTIVE_BURSTS,
        SCOPE_METADATA_ONLY,
        SCOPE_LOCAL_SESSION,
        SCOPE_SESSION_AND_SHADOW_GIT,
        SCOPE_COMPLETE_ERASE,
        VALID_SCOPES,
    )
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.trash import (
            TrashManager,
            _force_rmtree,
            _safe_rmtree,
            _safe_move_tree,
            _strip_readonly_recursive,
            _remove_readonly_recursive,
            _parse_orphan_trash_dir,
            SCOPE_SELECTIVE_BURSTS,
            SCOPE_METADATA_ONLY,
            SCOPE_LOCAL_SESSION,
            SCOPE_SESSION_AND_SHADOW_GIT,
            SCOPE_COMPLETE_ERASE,
            VALID_SCOPES,
        )
    except (ImportError, ModuleNotFoundError):
        from scripts.trash import (
            TrashManager,
            _force_rmtree,
            _safe_rmtree,
            _safe_move_tree,
            _strip_readonly_recursive,
            _remove_readonly_recursive,
            _parse_orphan_trash_dir,
            SCOPE_SELECTIVE_BURSTS,
            SCOPE_METADATA_ONLY,
            SCOPE_LOCAL_SESSION,
            SCOPE_SESSION_AND_SHADOW_GIT,
            SCOPE_COMPLETE_ERASE,
            VALID_SCOPES,
        )

__all__ = [
    "TrashManager",
    "_force_rmtree",
    "_safe_rmtree",
    "_safe_move_tree",
    "_strip_readonly_recursive",
    "_remove_readonly_recursive",
    "_parse_orphan_trash_dir",
    "SCOPE_SELECTIVE_BURSTS",
    "SCOPE_METADATA_ONLY",
    "SCOPE_LOCAL_SESSION",
    "SCOPE_SESSION_AND_SHADOW_GIT",
    "SCOPE_COMPLETE_ERASE",
    "VALID_SCOPES",
]
