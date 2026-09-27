"""
scripts/trash/constants.py
==========================
Scope definitions, timestamps, and constants for project trash management.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

_IST = timezone(timedelta(hours=5, minutes=30))

SCOPE_SELECTIVE_BURSTS = "selective_bursts"
SCOPE_METADATA_ONLY = "metadata_only"
SCOPE_LOCAL_SESSION = "local_session"
SCOPE_SESSION_AND_SHADOW_GIT = "session_and_shadow_git"
SCOPE_COMPLETE_ERASE = "complete_erase"

VALID_SCOPES = {
    SCOPE_SELECTIVE_BURSTS,
    SCOPE_METADATA_ONLY,
    SCOPE_LOCAL_SESSION,
    SCOPE_SESSION_AND_SHADOW_GIT,
    SCOPE_COMPLETE_ERASE,
}


def _now_ist_iso() -> str:
    return datetime.now(_IST).strftime("%Y-%m-%dT%H:%M:%S+05:30")


def _timestamp_prefix() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
