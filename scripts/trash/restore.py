"""
scripts/trash/restore.py
========================
Restoration procedures for soft-deleted project items from trash back to origin tiers.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import stat
from pathlib import Path
from typing import Any

try:
    from .constants import (
        SCOPE_SELECTIVE_BURSTS,
        SCOPE_METADATA_ONLY,
        SCOPE_LOCAL_SESSION,
        SCOPE_SESSION_AND_SHADOW_GIT,
        SCOPE_COMPLETE_ERASE,
    )
    from .fs_ops import _force_rmtree
    from ..storage_rotator import update_project_meta
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.trash.constants import (
            SCOPE_SELECTIVE_BURSTS,
            SCOPE_METADATA_ONLY,
            SCOPE_LOCAL_SESSION,
            SCOPE_SESSION_AND_SHADOW_GIT,
            SCOPE_COMPLETE_ERASE,
        )
        from spd_analysis_engine.scripts.trash.fs_ops import _force_rmtree
        from spd_analysis_engine.scripts.storage_rotator import update_project_meta
    except (ImportError, ModuleNotFoundError):
        from scripts.trash.constants import (
            SCOPE_SELECTIVE_BURSTS,
            SCOPE_METADATA_ONLY,
            SCOPE_LOCAL_SESSION,
            SCOPE_SESSION_AND_SHADOW_GIT,
            SCOPE_COMPLETE_ERASE,
        )
        from scripts.trash.fs_ops import _force_rmtree
        from scripts.storage_rotator import update_project_meta

logger = logging.getLogger(__name__)


def restore_from_trash(engine_root: Path, trash_dir: Path, trash_id: str) -> dict[str, Any]:
    """Restore a soft-deleted item from trash back to its origin tier."""
    target_dir = trash_dir / trash_id
    if not target_dir.exists() or not target_dir.is_dir():
        raise FileNotFoundError(f"Trash entry '{trash_id}' not found.")

    mf_path = target_dir / "manifest.json"
    manifest = {}
    if mf_path.exists():
        manifest = json.loads(mf_path.read_text(encoding="utf-8"))

    scope = manifest.get("scope", "")
    slug = manifest.get("project_name", "")
    if not slug:
        slug = trash_id.split("_", 2)[-1] if "_" in trash_id else trash_id

    # Destination project directory
    source_tier = manifest.get("source_tier", "current")
    original_path = manifest.get("original_path")
    if original_path:
        dest_dir = Path(original_path)
    else:
        dest_dir = engine_root / source_tier / slug
    dest_dir.mkdir(parents=True, exist_ok=True)

    if scope == SCOPE_SELECTIVE_BURSTS:
        # Restore events and patches into session.db
        db_path = dest_dir / "session.db"
        if not db_path.exists():
            curr_db = engine_root / "current" / slug / "session.db"
            if curr_db.exists():
                db_path = curr_db

        backup_file = target_dir / "events_backup.json"
        if backup_file.exists():
            data = json.loads(backup_file.read_text(encoding="utf-8"))
            events = data.get("events", [])
            patches = data.get("patches", [])

            if db_path.exists():
                conn = sqlite3.connect(db_path, timeout=10.0)
                cur = conn.cursor()
                cur.execute("PRAGMA table_info(events)")
                available_cols = {r[1] for r in cur.fetchall()}
                for ev in events:
                    cols_to_insert = [c for c in ["id", "session_id", "event_type", "timestamp", "first_file_touched", "summary", "ai_summary", "executor", "burst_num", "commit_hash", "is_rolled_back"] if c in available_cols and c in ev]
                    vals = [ev.get(c) for c in cols_to_insert]
                    placeholders = ",".join("?" for _ in cols_to_insert)
                    cur.execute(
                        f"INSERT OR REPLACE INTO events ({','.join(cols_to_insert)}) VALUES ({placeholders})",
                        vals,
                    )
                for p in patches:
                    cur.execute(
                        "INSERT OR REPLACE INTO patches (event_id, file_path, diff_content) VALUES (?, ?, ?)",
                        (p["event_id"], p["file_path"], p.get("diff_content", "")),
                    )
                conn.commit()
                conn.close()

        # Restore patch files
        trash_patches = target_dir / "patches"
        dest_patches = dest_dir / "patches"
        dest_patches.mkdir(parents=True, exist_ok=True)
        if trash_patches.exists():
            for pf in trash_patches.iterdir():
                if pf.is_file():
                    shutil.copy2(pf, dest_patches / pf.name)

    elif scope == SCOPE_METADATA_ONLY:
        meta_json = target_dir / "project.meta.json"
        if meta_json.exists():
            meta_dict = json.loads(meta_json.read_text(encoding="utf-8"))
            update_project_meta(engine_root, slug, **meta_dict)

    elif scope in (SCOPE_LOCAL_SESSION, SCOPE_SESSION_AND_SHADOW_GIT):
        for item in target_dir.iterdir():
            if item.name == "manifest.json":
                continue
            dst = dest_dir / item.name
            if dst.exists():
                if dst.is_dir():
                    _force_rmtree(dst)
                else:
                    try:
                        os.chmod(dst, stat.S_IWRITE | stat.S_IWUSR)
                        dst.unlink()
                    except Exception:
                        pass
            shutil.move(str(item), str(dst))

    elif scope == SCOPE_COMPLETE_ERASE:
        # Restore each tier directory
        for tier in ("current", "last_run", "history"):
            src_tier = target_dir / tier / slug
            if src_tier.exists():
                dst_tier = engine_root / tier / slug
                dst_tier.parent.mkdir(parents=True, exist_ok=True)
                if dst_tier.exists():
                    _force_rmtree(dst_tier)
                shutil.move(str(src_tier), str(dst_tier))

    # Cleanup trash folder after successful restoration
    _force_rmtree(target_dir)

    return {
        "success": True,
        "trash_id": trash_id,
        "project": slug,
        "scope": scope,
        "message": f"Successfully restored '{slug}' ({scope}) from trash.",
    }
