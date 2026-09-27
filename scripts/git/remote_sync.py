"""
scripts/git/remote_sync.py
==========================
Module-level convenience aliases for ShadowGit remote operations.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

try:
    from .shadow_repo import ShadowGit
    from .shadow_push import push_to_remote_repo
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.git.shadow_repo import ShadowGit
        from spd_analysis_engine.scripts.git.shadow_push import push_to_remote_repo
    except (ImportError, ModuleNotFoundError):
        from scripts.git.shadow_repo import ShadowGit
        from scripts.git.shadow_push import push_to_remote_repo


def push_to_remote(
    repo_path: Path | str,
    remote_url: str | None = None,
    commit_message: str | None = None,
    changelog_content: str | None = None,
    branch: str = "main",
    remote_name: str = "origin",
    engine_root: Path | str | None = None,
) -> dict[str, Any]:
    """Top-level convenience alias for ShadowGit.push_to_remote with watcher sync lock suppression."""
    target = Path(repo_path).resolve()
    lock_file = target / ".engine_sync.lock"
    try:
        try:
            from ..watcher_fs import suppress_sync_events
            suppress_sync_events(6.0)
        except Exception:
            pass

        try:
            lock_file.write_text(str(time.time()), encoding="utf-8")
        except Exception:
            pass

        return ShadowGit.push_to_remote(
            repo_path,
            remote_url=remote_url,
            commit_message=commit_message,
            changelog_content=changelog_content,
            branch=branch,
            remote_name=remote_name,
            engine_root=engine_root,
        )
    finally:
        try:
            if lock_file.exists():
                lock_file.unlink()
        except Exception:
            pass


init_primary_repo = ShadowGit.init_primary_repo
_ensure_shadow_repo = ShadowGit._ensure_shadow_repo

__all__ = [
    "push_to_remote",
    "push_to_remote_repo",
    "init_primary_repo",
    "_ensure_shadow_repo",
]

