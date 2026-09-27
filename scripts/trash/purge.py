"""
scripts/trash/purge.py
======================
Trash bin listing, entry purging, and bulk emptying routines.
"""

from __future__ import annotations

import json
import logging
import os
import stat
import time
import urllib.parse
from pathlib import Path
from typing import Any

try:
    from .fs_ops import _force_rmtree, _parse_orphan_trash_dir
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.trash.fs_ops import _force_rmtree, _parse_orphan_trash_dir
    except (ImportError, ModuleNotFoundError):
        from scripts.trash.fs_ops import _force_rmtree, _parse_orphan_trash_dir

logger = logging.getLogger(__name__)


def list_trash_entries(trash_dir: Path) -> list[dict[str, Any]]:
    """Return metadata for all soft-deleted items currently in the trash bin."""
    items: list[dict[str, Any]] = []
    if not trash_dir.exists():
        return items

    for sub in sorted(trash_dir.iterdir()):
        if not sub.is_dir():
            continue
        mf = sub / "manifest.json"
        folder_size = 0
        try:
            folder_size = sum(f.stat().st_size for f in sub.rglob("*") if f.is_file())
        except Exception:
            pass

        data = None
        if mf.exists():
            try:
                data = json.loads(mf.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Corrupted trash manifest in %s: %s", sub, exc)
                data = None

        if data and isinstance(data, dict) and data.get("scope") and data.get("project_name"):
            data["trash_id"] = sub.name
            data["folder_size_bytes"] = folder_size
            data["metadata"] = data.get("details", {})
            items.append(data)
        else:
            ts, proj_name, scope = _parse_orphan_trash_dir(sub.name)
            items.append({
                "trash_id": sub.name,
                "project_name": proj_name,
                "scope": scope if scope else "manual_archive",
                "timestamp": ts,
                "folder_size_bytes": folder_size,
                "details": {},
                "metadata": {},
                "source_tier": "unknown",
                "original_path": None,
            })

    items.sort(key=lambda x: str(x.get("timestamp", x.get("trash_id", ""))), reverse=True)
    return items


def purge_trash_entry(trash_dir: Path, trash_id: str) -> dict[str, Any]:
    """Permanently delete a specific trash entry."""
    raw_id = str(trash_id).strip()
    unquoted_id = urllib.parse.unquote(raw_id)
    quoted_id = urllib.parse.quote(raw_id)

    candidates = [
        trash_dir / raw_id,
        trash_dir / unquoted_id,
        trash_dir / quoted_id,
    ]

    target_dir = None
    for cand in candidates:
        if cand.exists():
            target_dir = cand
            break

    if not target_dir:
        raise FileNotFoundError(f"Trash entry '{trash_id}' not found.")

    resolved_id = target_dir.name

    # Ensure all Git objects and read-only files are unlocked & purged
    _force_rmtree(target_dir)

    if target_dir.exists():
        import gc
        gc.collect()
        time.sleep(0.1)
        _force_rmtree(target_dir)

    if target_dir.exists():
        raise PermissionError(f"Failed to completely remove '{target_dir}'. Path remains locked.")

    return {
        "success": True,
        "trash_id": resolved_id,
        "message": f"Permanently purged '{resolved_id}' from disk.",
    }


def purge_all_trash(trash_dir: Path) -> dict[str, Any]:
    """Permanently empty the entire trash directory."""
    purged_count = 0
    if trash_dir.exists():
        for item in list(trash_dir.iterdir()):
            if item.is_dir():
                _force_rmtree(item)
                purged_count += 1
            elif item.is_file():
                try:
                    os.chmod(item, stat.S_IWRITE | stat.S_IWUSR)
                    item.unlink()
                except Exception:
                    pass
                purged_count += 1
    return {
        "success": True,
        "purged_count": purged_count,
        "message": f"Emptied trash bin ({purged_count} item(s) permanently erased).",
    }
