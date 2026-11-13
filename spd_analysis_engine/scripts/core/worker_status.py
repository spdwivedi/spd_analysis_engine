"""
scripts/core/worker_status.py
=============================
Status file management, session pruning, and worker status formatting for Supervisor.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

from .time_utils import calculate_heartbeat_age_s

from .worker_entry import _PSUTIL_AVAILABLE

from ..storage_rotator import _slug, read_project_meta
from .project_config import DEFAULT_DEBOUNCE_S

if _PSUTIL_AVAILABLE:
    import psutil

logger = logging.getLogger(__name__)


def safe_write_status_json(
    target_path: Path | str,
    data_str: str,
    max_retries: int = 5,
    delay: float = 0.04,
) -> bool:
    """Safely write status JSON using atomic replacement with exponential backoff on Windows file locks."""
    target = Path(target_path).resolve()
    tmp_path = target.with_suffix(".json.tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(data_str)
    except OSError:
        return False
    for attempt in range(max_retries):
        try:
            os.replace(str(tmp_path), str(target))
            return True
        except (PermissionError, OSError):
            time.sleep(delay * (2 ** attempt))
    try:
        if tmp_path.exists():
            tmp_path.unlink()
    except OSError:
        pass
    return False


def read_status_file(proj_dir: Path, max_retries: int = 3, delay: float = 0.03) -> dict[str, Any]:
    """Read and parse worker.status.json with shared read access and retry on lock conflicts."""
    path = proj_dir / "worker.status.json"
    if not path.exists():
        return {}
    for attempt in range(max_retries):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (PermissionError, OSError):
            if attempt < max_retries - 1:
                time.sleep(delay * (2 ** attempt))
            else:
                return {}
        except Exception:
            return {}
    return {}


def prune_phantom_sessions(db_path: Path, slug: str) -> None:
    """Prune zero-event / zero-burst phantom sessions before archiving."""
    if not db_path.exists():
        return
    try:
        conn = sqlite3.connect(db_path, timeout=10.0)
        conn.cursor().execute(
            "DELETE FROM sessions WHERE id NOT IN (SELECT DISTINCT session_id FROM events WHERE session_id IS NOT NULL)"
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.debug("Error pruning empty sessions for '%s': %s", slug, exc)


def format_worker_status_row(
    entry: Any,
    status_data: dict[str, Any],
    root: Path,
    verified: bool | None,
) -> dict[str, Any]:
    """Build the normalized status dictionary for a worker entry."""
    last_hb_s: float | None = None
    if "ts" in status_data:
        last_hb_s = calculate_heartbeat_age_s(status_data["ts"])

    slug = getattr(entry, "slug", None) or _slug(entry.name)
    saved_meta = read_project_meta(root, slug)
    display_name = getattr(entry, "display_name", None) or saved_meta.get("project_name") or str(entry.name).strip() or slug
    root_path = getattr(entry, "root_path", None) or (str(Path(entry.target_path).resolve()) if entry.target_path else "") or saved_meta.get("target_path", "")
    project_id = getattr(entry, "project_id", None) or saved_meta.get("project_id") or f"proj_{slug}"

    db_val = saved_meta.get("debounce_window") or saved_meta.get("debounce")
    try:
        debounce_window = float(db_val) if db_val else DEFAULT_DEBOUNCE_S
    except (ValueError, TypeError):
        debounce_window = DEFAULT_DEBOUNCE_S
    hb_grace_threshold = max(debounce_window, 12.0)

    is_proc_alive = entry.is_alive() if verified is None else verified
    worker_alive = bool(is_proc_alive and (last_hb_s is None or last_hb_s <= hb_grace_threshold or verified is True))
    state = "ACTIVE" if worker_alive else "STOPPED"

    row: dict[str, Any] = {
        "project_id":             project_id,
        "slug":                   slug,
        "project_slug":           slug,
        "display_name":           display_name,
        "root_path":              root_path,
        "state":                  state,
        "project":                display_name,
        "pid":                    entry.pid,
        "target_path":            entry.target_path or root_path,
        "uptime_s":               round(entry.uptime_s(), 2),
        "alive":                  worker_alive,
        "status":                 state,
        "exit_code":              entry.exit_code,
        "session_id":             entry.session_id,
        "last_heartbeat_s":       last_hb_s,
        "reconciled":             entry.reconciled,
        "worker_id":              getattr(entry, "worker_id", None) or status_data.get("worker_id"),
        "process_create_time":    getattr(entry, "process_create_time", None) or status_data.get("process_create_time"),
        "canonical_target":       getattr(entry, "canonical_target", None) or status_data.get("canonical_target") or root_path,
        "canonical_project_root": getattr(entry, "canonical_project_root", None) or status_data.get("canonical_project_root") or root_path,
        "log_path":               str(entry.log_path) if entry.log_path else None,
        "memory_mb":              None,
        "cpu_percent":            None,
        "powers":                 status_data.get("powers", {}),
        "debounce":               status_data.get("debounce", {"active": False}),
        "recent_actions":         status_data.get("recent_actions", []),
    }

    if _PSUTIL_AVAILABLE and entry.is_alive():
        try:
            ps = psutil.Process(entry.pid)
            row["memory_mb"] = round(ps.memory_info().rss / 1_048_576, 2)
            row["cpu_percent"] = ps.cpu_percent(interval=0.1)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    return row
