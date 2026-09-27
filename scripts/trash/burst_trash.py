"""
scripts/trash/burst_trash.py
============================
Selective burst deletion logic for TrashManager.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Callable

try:
    from .constants import (
        SCOPE_SELECTIVE_BURSTS,
        _now_ist_iso,
        _timestamp_prefix,
    )
    from .fs_ops import _force_rmtree, _remove_readonly_recursive
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.trash.constants import (
            SCOPE_SELECTIVE_BURSTS,
            _now_ist_iso,
            _timestamp_prefix,
        )
        from spd_analysis_engine.scripts.trash.fs_ops import _force_rmtree, _remove_readonly_recursive
    except (ImportError, ModuleNotFoundError):
        from scripts.trash.constants import (
            SCOPE_SELECTIVE_BURSTS,
            _now_ist_iso,
            _timestamp_prefix,
        )
        from scripts.trash.fs_ops import _force_rmtree, _remove_readonly_recursive

logger = logging.getLogger(__name__)


def delete_selective_bursts(
    engine_root: Path,
    trash_dir: Path,
    slug: str,
    burst_ids: list[int],
    locate_dirs_fn: Callable[[str], list[tuple[str, Path]]],
) -> dict[str, Any]:
    """Soft-delete specific bursts into the trash directory."""
    if not burst_ids:
        raise ValueError("Parameter 'burst_ids' cannot be empty for selective bursts deletion.")

    # Find target db
    dirs = locate_dirs_fn(slug)
    if not dirs:
        raise FileNotFoundError(f"Project '{slug}' not found.")

    tier, p_dir = dirs[0]
    db_path = p_dir / "session.db"
    if not db_path.exists():
        for t, d in dirs:
            if (d / "session.db").exists():
                tier, p_dir = t, d
                db_path = d / "session.db"
                break

    if not db_path.exists():
        raise FileNotFoundError(f"session.db not found for project '{slug}'.")

    trash_id = f"{_timestamp_prefix()}_{slug}_bursts"
    entry_dir = trash_dir / trash_id
    entry_dir.mkdir(parents=True, exist_ok=True)
    trash_patches_dir = entry_dir / "patches"
    trash_patches_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("PRAGMA table_info(events)")
    cols = {r["name"] for r in cur.fetchall()}
    has_burst_num = "burst_num" in cols

    placeholders = ",".join("?" for _ in burst_ids)
    if has_burst_num:
        cur.execute(
            f"SELECT * FROM events WHERE id IN ({placeholders}) OR burst_num IN ({placeholders})",
            list(burst_ids) + list(burst_ids),
        )
    else:
        cur.execute(f"SELECT * FROM events WHERE id IN ({placeholders})", burst_ids)

    events_rows = [dict(r) for r in cur.fetchall()]

    # Also find any associated GIT events referencing these bursts
    resolved_event_ids = {r["id"] for r in events_rows}
    for ev in list(events_rows):
        eid = ev["id"]
        bnum = ev.get("burst_num")
        patterns = [f"%Burst #{eid}%"]
        if bnum is not None:
            patterns.append(f"%Burst #{bnum}%")
        for pat in patterns:
            cur.execute(
                "SELECT * FROM events WHERE event_type = 'GIT' AND summary LIKE ?",
                (pat,),
            )
            for git_r in cur.fetchall():
                git_dict = dict(git_r)
                if git_dict["id"] not in resolved_event_ids:
                    events_rows.append(git_dict)
                    resolved_event_ids.add(git_dict["id"])

    all_event_ids = list(resolved_event_ids)
    if not events_rows or not all_event_ids:
        conn.close()
        _force_rmtree(entry_dir)
        return {"success": False, "message": "None of the specified burst IDs exist in session.db."}

    patch_placeholders = ",".join("?" for _ in all_event_ids)
    cur.execute(f"SELECT * FROM patches WHERE event_id IN ({patch_placeholders})", all_event_ids)
    patches_rows = [dict(r) for r in cur.fetchall()]

    # Backup patch files
    src_patches_dir = p_dir / "patches"
    backed_up_patches = 0
    for eid in all_event_ids:
        pf = src_patches_dir / f"event_{eid}.patch"
        if pf.exists():
            shutil.copy2(pf, trash_patches_dir / f"event_{eid}.patch")
            backed_up_patches += 1

    # Save events JSON backup
    backup_file = entry_dir / "events_backup.json"
    backup_file.write_text(json.dumps({
        "events": events_rows,
        "patches": patches_rows,
    }, indent=2), encoding="utf-8")

    # Delete from SQLite
    cur.execute(f"DELETE FROM patches WHERE event_id IN ({patch_placeholders})", all_event_ids)
    cur.execute(f"DELETE FROM events WHERE id IN ({patch_placeholders})", all_event_ids)
    conn.commit()
    conn.close()

    # Remove local patch files
    for eid in all_event_ids:
        pf = src_patches_dir / f"event_{eid}.patch"
        if pf.exists():
            try:
                _remove_readonly_recursive(pf)
                pf.unlink(missing_ok=True)
            except Exception:
                pass

    # Remove corresponding AI cache files
    cache_base = engine_root / "ai_data" / "cache"
    slug_cache = cache_base / slug
    dirs_to_prune = [cache_base, slug_cache, slug_cache / "files", slug_cache / "overviews"]
    for ev in events_rows:
        sess_id = ev.get("session_id")
        if sess_id:
            dirs_to_prune.append(slug_cache / f"session_{sess_id}")
            dirs_to_prune.append(slug_cache / f"session_{sess_id}" / "files")
        eid = ev["id"]
        bnum = ev.get("burst_num")
        check_ids = [eid]
        if bnum is not None and bnum not in check_ids:
            check_ids.append(bnum)
        patterns = []
        for cid in check_ids:
            patterns.extend([
                f"event_{cid}.json",
                f"file_analysis_*_{cid}_*.json",
                f"{cid}_*.json",
                f"burst_overview_*_{cid}_*.json",
                f"overview_{cid}_*.json",
                f"burst_{cid}_*.json",
            ])
        for d in dirs_to_prune:
            if d.exists():
                for pat in patterns:
                    for cf in d.glob(pat):
                        try:
                            _remove_readonly_recursive(cf)
                            cf.unlink(missing_ok=True)
                        except Exception:
                            pass

    non_git_burst_ids = [ev["id"] for ev in events_rows if ev.get("event_type") != "GIT"]
    reported_burst_ids = non_git_burst_ids if non_git_burst_ids else all_event_ids
    manifest = {
        "trash_id": trash_id,
        "project_name": slug,
        "scope": SCOPE_SELECTIVE_BURSTS,
        "timestamp": _now_ist_iso(),
        "source_tier": tier,
        "original_path": str(p_dir),
        "details": {
            "burst_ids": reported_burst_ids,
            "events_count": len(events_rows),
            "patches_count": len(patches_rows),
            "patch_files_backed_up": backed_up_patches,
        },
        "metadata": {
            "burst_ids": reported_burst_ids,
        },
    }
    (entry_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    count = len(reported_burst_ids)
    return {
        "success": True,
        "trash_id": trash_id,
        "scope": SCOPE_SELECTIVE_BURSTS,
        "deleted_bursts": count,
        "deleted_bursts_count": count,
        "message": f"Moved {count} burst(s) to trash ({trash_id}).",
    }
