"""
scripts/storage_rotator.py
==========================
Backward-compatible facade for the modularized storage management layer.
"""
from __future__ import annotations

from spd_analysis_engine.scripts.storage import (
    SessionDB,
    StorageRotator,
    rotate_project_to_history,
    _slug,
    _utcnow_iso,
    resolve_project_identity,
    read_project_meta,
    update_project_meta,
    synthesize_project_meta,
    resolve_project_context,
    consolidate_project_history,
    rollback_burst,
    purge_test_artifacts,
)

__all__ = [
    "SessionDB", "StorageRotator", "rotate_project_to_history", "_slug", "_utcnow_iso",
    "resolve_project_identity",
    "read_project_meta", "update_project_meta", "synthesize_project_meta", "resolve_project_context",
    "consolidate_project_history", "rollback_burst", "purge_test_artifacts",
]
