"""
scripts/trash/project_trash.py
==============================
Handles project-level soft-deletion scopes:
- SCOPE_METADATA_ONLY
- SCOPE_LOCAL_SESSION
- SCOPE_SESSION_AND_SHADOW_GIT
- SCOPE_COMPLETE_ERASE
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Callable

from .constants import (
    SCOPE_METADATA_ONLY,
    SCOPE_LOCAL_SESSION,
    SCOPE_SESSION_AND_SHADOW_GIT,
    SCOPE_COMPLETE_ERASE,
    _now_ist_iso,
    _timestamp_prefix,
)
from .fs_ops import _safe_move_tree, _safe_rmtree, _remove_readonly_recursive
from .remote_gh import delete_remote_github_repo
from ..storage_rotator import read_project_meta

from ..core.process_sweeper import sweep_project_processes

logger = logging.getLogger(__name__)


def delete_metadata_only(
    engine_root: Path,
    trash_dir: Path,
    slug: str,
    locate_dirs_fn: Callable[[str], list[tuple[str, Path]]],
) -> dict[str, Any]:
    dirs = locate_dirs_fn(slug)
    if not dirs:
        raise FileNotFoundError(f"Project '{slug}' not found.")

    trash_id = f"{_timestamp_prefix()}_{slug}_metadata"
    entry_dir = trash_dir / trash_id
    entry_dir.mkdir(parents=True, exist_ok=True)

    meta = read_project_meta(engine_root, slug)
    (entry_dir / "project.meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    cleared_files = 0
    for tier, p_dir in dirs:
        meta_file = p_dir / "project.meta"
        if meta_file.exists():
            try:
                meta_file.unlink()
                cleared_files += 1
            except Exception:
                pass
        status_file = p_dir / "worker.status.json"
        if status_file.exists():
            try:
                status_file.unlink()
                cleared_files += 1
            except Exception:
                pass

    manifest = {
        "trash_id": trash_id,
        "project_name": slug,
        "scope": SCOPE_METADATA_ONLY,
        "timestamp": _now_ist_iso(),
        "source_tier": dirs[0][0],
        "original_path": str(dirs[0][1]),
        "details": {
            "metadata_snapshot": meta,
            "cleared_files_count": cleared_files,
        },
    }
    (entry_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    return {
        "success": True,
        "trash_id": trash_id,
        "scope": SCOPE_METADATA_ONLY,
        "message": f"Cleared project metadata for '{slug}' (moved to trash {trash_id}).",
    }


def delete_local_session(
    engine_root: Path,
    trash_dir: Path,
    slug: str,
    locate_dirs_fn: Callable[[str], list[tuple[str, Path]]],
    stop_worker_fn: Callable[[str], None],
    session_id: int | None = None,
) -> dict[str, Any]:
    stop_worker_fn(slug)
    dirs = locate_dirs_fn(slug)
    if not dirs:
        raise FileNotFoundError(f"Project '{slug}' not found.")

    tier, p_dir = dirs[0]

    if session_id is not None:
        db_path = p_dir / "session.db"
        if not db_path.exists():
            for t, d in dirs:
                if (d / "session.db").exists():
                    tier, p_dir = t, d
                    db_path = d / "session.db"
                    break

        if db_path.exists():
            trash_id = f"{_timestamp_prefix()}_{slug}_session_{session_id}"
            entry_dir = trash_dir / trash_id
            entry_dir.mkdir(parents=True, exist_ok=True)
            trash_patches_dir = entry_dir / "patches"
            trash_patches_dir.mkdir(parents=True, exist_ok=True)

            conn = sqlite3.connect(db_path, timeout=10.0)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            cur.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
            session_row = cur.fetchone()
            session_data = dict(session_row) if session_row else {}

            cur.execute("SELECT * FROM events WHERE session_id = ?", (session_id,))
            events_rows = [dict(r) for r in cur.fetchall()]
            event_ids = [ev["id"] for ev in events_rows]

            patches_rows = []
            if event_ids:
                ph = ",".join("?" for _ in event_ids)
                cur.execute(f"SELECT * FROM patches WHERE event_id IN ({ph})", event_ids)
                patches_rows = [dict(r) for r in cur.fetchall()]

            src_patches_dir = p_dir / "patches"
            backed_up_patches = 0
            for eid in event_ids:
                pf = src_patches_dir / f"event_{eid}.patch"
                if pf.exists():
                    shutil.copy2(pf, trash_patches_dir / f"event_{eid}.patch")
                    backed_up_patches += 1

            backup_file = entry_dir / "session_backup.json"
            backup_file.write_text(json.dumps({
                "session": session_data,
                "events": events_rows,
                "patches": patches_rows,
            }, indent=2), encoding="utf-8")

            if event_ids:
                ph = ",".join("?" for _ in event_ids)
                cur.execute(f"DELETE FROM patches WHERE event_id IN ({ph})", event_ids)
                cur.execute(f"DELETE FROM events WHERE id IN ({ph})", event_ids)
            cur.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            conn.commit()
            conn.close()

            for eid in event_ids:
                pf = src_patches_dir / f"event_{eid}.patch"
                if pf.exists():
                    try:
                        _remove_readonly_recursive(pf)
                        pf.unlink()
                    except Exception:
                        pass

            manifest = {
                "trash_id": trash_id,
                "project_name": slug,
                "scope": SCOPE_LOCAL_SESSION,
                "session_id": session_id,
                "timestamp": _now_ist_iso(),
                "source_tier": tier,
                "original_path": str(p_dir),
                "details": {
                    "session_id": session_id,
                    "events_count": len(events_rows),
                    "patches_count": len(patches_rows),
                    "patch_files_backed_up": backed_up_patches,
                },
            }
            (entry_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

            return {
                "success": True,
                "trash_id": trash_id,
                "scope": SCOPE_LOCAL_SESSION,
                "session_id": session_id,
                "deleted_events": len(events_rows),
                "message": f"Moved Session #{session_id} for '{slug}' to trash ({trash_id}).",
            }

    trash_id = f"{_timestamp_prefix()}_{slug}_session"
    entry_dir = trash_dir / trash_id
    entry_dir.mkdir(parents=True, exist_ok=True)

    session_files = ["session.db", "worker.log", "worker.status.json", "baseline"]
    moved_items = []

    for item_name in session_files:
        src = p_dir / item_name
        if src.exists():
            _remove_readonly_recursive(src)
            dst = entry_dir / item_name
            shutil.move(str(src), str(dst))
            moved_items.append(item_name)

    src_patches = p_dir / "patches"
    if src_patches.exists():
        _remove_readonly_recursive(src_patches)
        dst_patches = entry_dir / "patches"
        shutil.move(str(src_patches), str(dst_patches))
        moved_items.append("patches")

    cur_p = engine_root / "current" / slug
    if cur_p.exists():
        sweep_project_processes(slug)
        for extra in list(cur_p.iterdir()):
            try:
                _remove_readonly_recursive(extra)
                extra_dst = entry_dir / extra.name
                if not extra_dst.exists():
                    shutil.move(str(extra), str(extra_dst))
                    moved_items.append(extra.name)
            except Exception:
                pass
        _safe_rmtree(cur_p)

    manifest = {
        "trash_id": trash_id,
        "project_name": slug,
        "scope": SCOPE_LOCAL_SESSION,
        "timestamp": _now_ist_iso(),
        "source_tier": tier,
        "original_path": str(p_dir),
        "details": {
            "moved_items": moved_items,
        },
    }
    (entry_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    return {
        "success": True,
        "trash_id": trash_id,
        "scope": SCOPE_LOCAL_SESSION,
        "moved_items": moved_items,
        "message": f"Moved local monitoring session for '{slug}' to trash ({trash_id}).",
    }


def delete_session_and_shadow_git(
    trash_dir: Path,
    slug: str,
    locate_dirs_fn: Callable[[str], list[tuple[str, Path]]],
    stop_worker_fn: Callable[[str], None],
) -> dict[str, Any]:
    stop_worker_fn(slug)
    dirs = locate_dirs_fn(slug)
    if not dirs:
        raise FileNotFoundError(f"Project '{slug}' not found.")

    tier, p_dir = dirs[0]
    trash_id = f"{_timestamp_prefix()}_{slug}_full_session"
    entry_dir = trash_dir / trash_id
    entry_dir.mkdir(parents=True, exist_ok=True)

    items_to_move = ["session.db", "worker.log", "worker.status.json", "baseline", "patches", "shadow_git"]
    moved_items = []

    for item_name in items_to_move:
        src = p_dir / item_name
        if src.exists():
            _remove_readonly_recursive(src)
            dst = entry_dir / item_name
            shutil.move(str(src), str(dst))
            moved_items.append(item_name)

    cur_p = trash_dir.parent / "current" / slug
    if cur_p.exists():
        sweep_project_processes(slug)
        for extra in list(cur_p.iterdir()):
            try:
                _remove_readonly_recursive(extra)
                extra_dst = entry_dir / extra.name
                if not extra_dst.exists():
                    shutil.move(str(extra), str(extra_dst))
                    moved_items.append(extra.name)
            except Exception:
                pass
        _safe_rmtree(cur_p)

    manifest = {
        "trash_id": trash_id,
        "project_name": slug,
        "scope": SCOPE_SESSION_AND_SHADOW_GIT,
        "timestamp": _now_ist_iso(),
        "source_tier": tier,
        "original_path": str(p_dir),
        "details": {
            "moved_items": moved_items,
        },
    }
    (entry_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    return {
        "success": True,
        "trash_id": trash_id,
        "scope": SCOPE_SESSION_AND_SHADOW_GIT,
        "moved_items": moved_items,
        "message": f"Moved local session and Shadow Git for '{slug}' to trash ({trash_id}).",
    }


def delete_complete_erase(
    engine_root: Path,
    trash_dir: Path,
    slug: str,
    locate_dirs_fn: Callable[[str], list[tuple[str, Path]]],
    stop_worker_fn: Callable[[str], None],
    delete_github_repo: bool = False,
) -> dict[str, Any]:
    stop_worker_fn(slug)
    dirs = locate_dirs_fn(slug)
    if not dirs:
        raise FileNotFoundError(f"Project '{slug}' not found.")

    trash_id = f"{_timestamp_prefix()}_{slug}_complete"
    entry_dir = trash_dir / trash_id
    entry_dir.mkdir(parents=True, exist_ok=True)

    meta = read_project_meta(engine_root, slug)
    remote_url = meta.get("remote_url", "")
    github_deleted = False
    github_msg = ""

    if delete_github_repo and remote_url:
        gh_res = delete_remote_github_repo(engine_root, remote_url)
        github_deleted = gh_res.get("deleted", False)
        github_msg = gh_res.get("message", "")

    moved_tiers = []
    sweep_project_processes(slug)
    for tier, p_dir in dirs:
        dst_tier_dir = entry_dir / tier
        dst_tier_dir.mkdir(parents=True, exist_ok=True)
        target_dst = dst_tier_dir / slug
        _safe_move_tree(p_dir, target_dst)
        moved_tiers.append(tier)

    cur_p = engine_root / "current" / slug
    if cur_p.exists():
        _safe_rmtree(cur_p)

    manifest = {
        "trash_id": trash_id,
        "project_name": slug,
        "scope": SCOPE_COMPLETE_ERASE,
        "timestamp": _now_ist_iso(),
        "source_tier": ",".join(moved_tiers),
        "original_path": str(dirs[0][1]),
        "details": {
            "moved_tiers": moved_tiers,
            "remote_url": remote_url,
            "delete_github_repo_requested": delete_github_repo,
            "github_repo_deleted": github_deleted,
            "github_deletion_message": github_msg,
        },
    }
    (entry_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    return {
        "success": True,
        "trash_id": trash_id,
        "scope": SCOPE_COMPLETE_ERASE,
        "moved_tiers": moved_tiers,
        "github_repo_deleted": github_deleted,
        "message": f"Project '{slug}' completely erased from local engine tiers ({trash_id})."
        + (f" GitHub deletion: {github_msg}" if delete_github_repo else ""),
    }
