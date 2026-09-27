"""
scripts/storage_rotator.py
==========================
Backward-compatible facade for the modularized storage management layer.
"""
from __future__ import annotations

try:
    from .storage import (
        SessionDB, StorageRotator, _slug, _utcnow_iso,
        read_project_meta, update_project_meta, consolidate_project_history,
        rollback_burst, purge_test_artifacts,
    )
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.storage import (
            SessionDB, StorageRotator, _slug, _utcnow_iso,
            read_project_meta, update_project_meta, consolidate_project_history,
            rollback_burst, purge_test_artifacts,
        )
    except (ImportError, ModuleNotFoundError):
        from scripts.storage import (
            SessionDB, StorageRotator, _slug, _utcnow_iso,
            read_project_meta, update_project_meta, consolidate_project_history,
            rollback_burst, purge_test_artifacts,
        )

__all__ = [
    "SessionDB", "StorageRotator", "_slug", "_utcnow_iso",
    "read_project_meta", "update_project_meta", "consolidate_project_history",
    "rollback_burst", "purge_test_artifacts",
]
