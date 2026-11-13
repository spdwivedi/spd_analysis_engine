"""
scripts/core/time_utils.py
==========================
Authoritative time utilities for the SPD Analysis Engine.

Invariants:
- Exactly one utc_now_iso() utility.
- Store UTC everywhere.
- Accept legacy timestamps with offsets (e.g. +05:30, -04:00, Z, +00:00).
- Only format IST/local time at the UI boundary.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Indian Standard Time (IST) offset: UTC+05:30
IST_TIMEZONE = timezone(timedelta(hours=5, minutes=30))


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string (no microseconds, ending in Z)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso_timestamp(ts: Any) -> datetime | None:
    """
    Parse an ISO-8601 timestamp string into a timezone-aware UTC datetime.

    Accepts:
    - Standard UTC strings ending in 'Z' (e.g. '2026-09-30T02:00:00Z')
    - Legacy offset strings (e.g. '2026-09-30T07:30:00+05:30', '...-04:00')
    - Space-separated or ISO formatted strings
    - datetime objects (converted to UTC)
    - Returns None if input cannot be parsed.
    """
    if ts is None:
        return None

    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)

    ts_str = str(ts).strip()
    if not ts_str:
        return None

    # Replace trailing 'Z' or 'z' with '+00:00' for universal fromisoformat compatibility
    clean_str = ts_str
    if clean_str.endswith("Z") or clean_str.endswith("z"):
        clean_str = clean_str[:-1] + "+00:00"

    try:
        dt = datetime.fromisoformat(clean_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        pass

    # Fallback parsing for legacy formats without timezone offset
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y%m%d_%H%M%S",
    ):
        try:
            dt = datetime.strptime(ts_str[:19], fmt)
            return dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            pass

    return None


def calculate_heartbeat_age_s(ts_input: Any, now: datetime | None = None) -> float | None:
    """
    Calculate the elapsed time in seconds from ts_input to now in UTC.
    Returns None if ts_input cannot be parsed.
    Clamps negative age (clock skew) to 0.0.
    """
    dt = parse_iso_timestamp(ts_input)
    if dt is None:
        return None

    if now is not None:
        ref_now = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
    else:
        ref_now = datetime.now(timezone.utc)

    age = (ref_now - dt).total_seconds()
    return round(max(0.0, age), 2)


def format_ui_local_iso(ts_input: Any, tz: timezone = IST_TIMEZONE) -> str:
    """Format a UTC timestamp for UI display at the UI boundary in local time (default IST)."""
    dt = parse_iso_timestamp(ts_input)
    if dt is None:
        return str(ts_input or "")
    local_dt = dt.astimezone(tz)
    return local_dt.strftime("%Y-%m-%dT%H:%M:%S+05:30")
