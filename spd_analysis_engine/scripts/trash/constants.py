"""
scripts/trash/constants.py
==========================
Scope definitions, timestamps, and constants for project trash management.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

# Legacy alias — kept for backward compatibility with fs_ops, burst_trash, etc.
_IST = timezone(timedelta(hours=5, minutes=30))

try:
    from spd_analysis_engine.scripts.core.time_utils import utc_now_iso as _now_ist_iso
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.core.time_utils import utc_now_iso as _now_ist_iso
    except (ImportError, ModuleNotFoundError):
        def _now_ist_iso() -> str:  # type: ignore[misc]
            return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

SCOPE_SELECTIVE_BURSTS = "selective_bursts"
SCOPE_METADATA_ONLY = "metadata_only"
SCOPE_LOCAL_SESSION = "local_session"
SCOPE_SESSION_AND_SHADOW_GIT = "session_and_shadow_git"
SCOPE_COMPLETE_ERASE = "complete_erase"
SCOPE_ROLLBACK_BACKUP = "rollback_backup"

VALID_SCOPES = {
    SCOPE_SELECTIVE_BURSTS,
    SCOPE_METADATA_ONLY,
    SCOPE_LOCAL_SESSION,
    SCOPE_SESSION_AND_SHADOW_GIT,
    SCOPE_COMPLETE_ERASE,
    SCOPE_ROLLBACK_BACKUP,
}


def _timestamp_prefix() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
