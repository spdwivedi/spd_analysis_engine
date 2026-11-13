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
from .project_trash import (
    delete_metadata_only,
    delete_local_session,
    delete_session_and_shadow_git,
    delete_complete_erase,
)
from .remote_gh import delete_remote_github_repo
from .restore import restore_from_trash
from .burst_trash import delete_selective_bursts
from .purge import list_trash_entries, purge_trash_entry, purge_all_trash
from ..storage_rotator import _slug, read_project_meta

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

            try:
                from ..core.process_sweeper import sweep_project_processes
                sweep_project_processes(slug)
            except Exception:
                pass

            if target_pid and target_pid > 0 and target_pid != os.getpid():
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
        return delete_metadata_only(self.engine_root, self.trash_dir, slug, self._locate_project_dirs)

    def _delete_local_session(self, slug: str, session_id: int | None = None) -> dict[str, Any]:
        return delete_local_session(
            self.engine_root,
            self.trash_dir,
            slug,
            self._locate_project_dirs,
            self._stop_active_worker_if_running,
            session_id=session_id,
        )

    def _delete_session_and_shadow_git(self, slug: str) -> dict[str, Any]:
        return delete_session_and_shadow_git(
            self.trash_dir,
            slug,
            self._locate_project_dirs,
            self._stop_active_worker_if_running,
        )

    def _delete_complete_erase(self, slug: str, delete_github_repo: bool = False) -> dict[str, Any]:
        return delete_complete_erase(
            self.engine_root,
            self.trash_dir,
            slug,
            self._locate_project_dirs,
            self._stop_active_worker_if_running,
            delete_github_repo=delete_github_repo,
        )

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
