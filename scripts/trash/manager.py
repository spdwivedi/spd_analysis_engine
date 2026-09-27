"""
scripts/trash/manager.py
========================
TrashManager implementation for soft-deletion, restoration, and purging.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import stat
import time
import urllib.parse
from pathlib import Path
from typing import Any

try:
    from .constants import (
        SCOPE_SELECTIVE_BURSTS,
        SCOPE_METADATA_ONLY,
        SCOPE_LOCAL_SESSION,
        SCOPE_SESSION_AND_SHADOW_GIT,
        SCOPE_COMPLETE_ERASE,
        VALID_SCOPES,
        _now_ist_iso,
        _timestamp_prefix,
    )
    from .fs_ops import (
        _force_rmtree,
        _safe_rmtree,
        _safe_move_tree,
        _parse_orphan_trash_dir,
        _remove_readonly_recursive,
    )
    from .remote_gh import delete_remote_github_repo
    from .restore import restore_from_trash
    from .burst_trash import delete_selective_bursts
    from .purge import list_trash_entries, purge_trash_entry, purge_all_trash
    from ..storage_rotator import _slug, read_project_meta
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.trash.constants import (
            SCOPE_SELECTIVE_BURSTS,
            SCOPE_METADATA_ONLY,
            SCOPE_LOCAL_SESSION,
            SCOPE_SESSION_AND_SHADOW_GIT,
            SCOPE_COMPLETE_ERASE,
            VALID_SCOPES,
            _now_ist_iso,
            _timestamp_prefix,
        )
        from spd_analysis_engine.scripts.trash.fs_ops import (
            _force_rmtree,
            _safe_rmtree,
            _safe_move_tree,
            _parse_orphan_trash_dir,
            _remove_readonly_recursive,
        )
        from spd_analysis_engine.scripts.trash.remote_gh import delete_remote_github_repo
        from spd_analysis_engine.scripts.trash.restore import restore_from_trash
        from spd_analysis_engine.scripts.trash.burst_trash import delete_selective_bursts
        from spd_analysis_engine.scripts.trash.purge import list_trash_entries, purge_trash_entry, purge_all_trash
        from spd_analysis_engine.scripts.storage_rotator import _slug, read_project_meta
    except (ImportError, ModuleNotFoundError):
        from scripts.trash.constants import (
            SCOPE_SELECTIVE_BURSTS,
            SCOPE_METADATA_ONLY,
            SCOPE_LOCAL_SESSION,
            SCOPE_SESSION_AND_SHADOW_GIT,
            SCOPE_COMPLETE_ERASE,
            VALID_SCOPES,
            _now_ist_iso,
            _timestamp_prefix,
        )
        from scripts.trash.fs_ops import (
            _force_rmtree,
            _safe_rmtree,
            _safe_move_tree,
            _parse_orphan_trash_dir,
            _remove_readonly_recursive,
        )
        from scripts.trash.remote_gh import delete_remote_github_repo
        from scripts.trash.restore import restore_from_trash
        from scripts.trash.burst_trash import delete_selective_bursts
        from scripts.trash.purge import list_trash_entries, purge_trash_entry, purge_all_trash
        from scripts.storage_rotator import _slug, read_project_meta

logger = logging.getLogger(__name__)


class TrashManager:
    """
    Manages soft deletion, trash inspection, restoration, and permanent purging.
    """

    def __init__(self, engine_root: Path | str | None = None) -> None:
        if engine_root is None:
            self.engine_root = Path(__file__).resolve().parent.parent.parent
        else:
            self.engine_root = Path(engine_root).resolve()

        self.trash_dir = self.engine_root / "trash"
        self.trash_dir.mkdir(parents=True, exist_ok=True)

    def _locate_project_dirs(self, slug: str) -> list[tuple[str, Path]]:
        """Find existing directories for a project across current, last_run, and history."""
        found: list[tuple[str, Path]] = []
        for tier in ("current", "last_run"):
            p = self.engine_root / tier / slug
            if p.exists() and p.is_dir():
                found.append((tier, p))

        hist_p = self.engine_root / "history" / slug
        if hist_p.exists() and hist_p.is_dir():
            found.append(("history", hist_p))
        return found

    def move_to_trash(
        self,
        project_name: str,
        scope: str,
        burst_ids: list[int] | None = None,
        delete_github_repo: bool = False,
        delete_remote: bool = False,
        session_id: int | None = None,
    ) -> dict[str, Any]:
        """
        Soft-delete project data according to the requested scope.
        """
        delete_remote_repo = bool(delete_github_repo or delete_remote)
        slug = _slug(project_name)
        if scope not in VALID_SCOPES:
            raise ValueError(f"Invalid deletion scope '{scope}'. Must be one of: {sorted(VALID_SCOPES)}")

        if scope == SCOPE_SELECTIVE_BURSTS:
            return self._delete_selective_bursts(slug, burst_ids or [])
        elif scope == SCOPE_METADATA_ONLY:
            return self._delete_metadata_only(slug)
        elif scope == SCOPE_LOCAL_SESSION:
            return self._delete_local_session(slug, session_id=session_id)
        elif scope == SCOPE_SESSION_AND_SHADOW_GIT:
            return self._delete_session_and_shadow_git(slug)
        elif scope == SCOPE_COMPLETE_ERASE:
            return self._delete_complete_erase(slug, delete_github_repo=delete_remote_repo)
        else:
            raise ValueError(f"Unhandled scope: {scope}")

    delete_project = move_to_trash

    def _stop_active_worker_if_running(self, slug: str) -> None:
        """Ensure active worker process is stopped and file locks released before directory operations."""
        try:
            current_dir = self.engine_root / "current" / slug
            pid_file = current_dir / "worker.pid"
            target_pid = None
            if pid_file.exists():
                try:
                    data = json.loads(pid_file.read_text(encoding="utf-8"))
                    target_pid = int(data.get("pid", 0))
                except Exception:
                    pass

            try:
                from ..core.supervisor import Supervisor
                sup = Supervisor(self.engine_root)
                sup.stop_project(slug)
            except Exception:
                pass

            if target_pid and target_pid > 0:
                try:
                    from ..core.spawner import kill_pid
                    kill_pid(target_pid)
                except Exception:
                    try:
                        import signal
                        os.kill(target_pid, signal.SIGTERM)
                    except Exception:
                        pass

            # 1.5-second grace window to allow OS file handles and SQLite WAL locks to flush and close
            time.sleep(1.5)
        except Exception as e:
            logger.debug("Error stopping active worker for %s: %s", slug, e)

    def _delete_selective_bursts(self, slug: str, burst_ids: list[int]) -> dict[str, Any]:
        return delete_selective_bursts(
            self.engine_root,
            self.trash_dir,
            slug,
            burst_ids,
            self._locate_project_dirs,
        )

    def _delete_metadata_only(self, slug: str) -> dict[str, Any]:
        dirs = self._locate_project_dirs(slug)
        if not dirs:
            raise FileNotFoundError(f"Project '{slug}' not found.")

        trash_id = f"{_timestamp_prefix()}_{slug}_metadata"
        entry_dir = self.trash_dir / trash_id
        entry_dir.mkdir(parents=True, exist_ok=True)

        meta = read_project_meta(self.engine_root, slug)
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

    def _delete_local_session(self, slug: str, session_id: int | None = None) -> dict[str, Any]:
        self._stop_active_worker_if_running(slug)
        dirs = self._locate_project_dirs(slug)
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
                entry_dir = self.trash_dir / trash_id
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

                # Backup patch files
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

                # Remove local patch files
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
        entry_dir = self.trash_dir / trash_id
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

        # Move patches/ directory
        src_patches = p_dir / "patches"
        if src_patches.exists():
            _remove_readonly_recursive(src_patches)
            dst_patches = entry_dir / "patches"
            shutil.move(str(src_patches), str(dst_patches))
            moved_items.append("patches")

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

    def _delete_session_and_shadow_git(self, slug: str) -> dict[str, Any]:
        self._stop_active_worker_if_running(slug)
        dirs = self._locate_project_dirs(slug)
        if not dirs:
            raise FileNotFoundError(f"Project '{slug}' not found.")

        tier, p_dir = dirs[0]
        trash_id = f"{_timestamp_prefix()}_{slug}_full_session"
        entry_dir = self.trash_dir / trash_id
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

    def _delete_complete_erase(self, slug: str, delete_github_repo: bool = False) -> dict[str, Any]:
        self._stop_active_worker_if_running(slug)
        dirs = self._locate_project_dirs(slug)
        if not dirs:
            raise FileNotFoundError(f"Project '{slug}' not found.")

        trash_id = f"{_timestamp_prefix()}_{slug}_complete"
        entry_dir = self.trash_dir / trash_id
        entry_dir.mkdir(parents=True, exist_ok=True)

        meta = read_project_meta(self.engine_root, slug)
        remote_url = meta.get("remote_url", "")
        github_deleted = False
        github_msg = ""

        # Remote GitHub deletion if requested
        if delete_github_repo and remote_url:
            gh_res = self._delete_remote_github_repo(remote_url)
            github_deleted = gh_res.get("deleted", False)
            github_msg = gh_res.get("message", "")

        moved_tiers = []
        for tier, p_dir in dirs:
            dst_tier_dir = entry_dir / tier
            dst_tier_dir.mkdir(parents=True, exist_ok=True)
            target_dst = dst_tier_dir / slug
            _safe_move_tree(p_dir, target_dst)
            moved_tiers.append(tier)

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

    def _delete_remote_github_repo(self, remote_url: str) -> dict[str, Any]:
        """Call GitHub REST API to delete remote repository using stored credentials."""
        return delete_remote_github_repo(self.engine_root, remote_url)

    def list_trash(self) -> list[dict[str, Any]]:
        """Return metadata for all soft-deleted items currently in the trash bin."""
        return list_trash_entries(self.trash_dir)

    def restore(self, trash_id: str) -> dict[str, Any]:
        """Restore a soft-deleted item from trash back to its origin tier."""
        return restore_from_trash(self.engine_root, self.trash_dir, trash_id)

    def purge(self, trash_id: str) -> dict[str, Any]:
        """Permanently delete a specific trash entry."""
        return purge_trash_entry(self.trash_dir, trash_id)

    def purge_all(self) -> dict[str, Any]:
        """Permanently empty the entire trash directory."""
        return purge_all_trash(self.trash_dir)
