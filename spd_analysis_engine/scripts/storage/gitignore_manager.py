"""
scripts/storage/gitignore_manager.py
=====================================
Workspace .gitignore reader, updater, silent watchdog suppression,
and git unstaging management.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

def suppress_file_path(path: Any, duration_s: float = 5.0) -> None:
    try:
        from spd_analysis_engine.scripts.event_bundler import suppress_file_path as _sfp
        _sfp(path, duration_s=duration_s)
    except Exception:
        pass

from ..git.utils import _NO_WINDOW_FLAG

logger = logging.getLogger(__name__)

DEFAULT_GITIGNORE_CONTENT = """# Environments & Secrets
.env
*.env
*.key
*.pem
*.cert

# Dependencies & Build Artifacts
node_modules/
__pycache__/
*.pyc
*.pyo
dist/
build/
.venv/
.venv*/

# Binary & Documents
*.pdf
pdf/

# SPD Analysis Engine & Runtime
.spd/
session.db*
worker.log
worker.pid*
worker.status.json
*.patch
*.tmp
"""


def read_project_gitignore(target_path: Path | str) -> str:
    """
    Safely read the workspace .gitignore content.
    Returns the default hardened template if file does not exist or is empty.
    """
    target = Path(target_path).resolve()
    gi_path = target / ".gitignore"
    if not gi_path.is_file():
        return DEFAULT_GITIGNORE_CONTENT

    try:
        content = gi_path.read_text(encoding="utf-8", errors="replace")
        return content if content.strip() else DEFAULT_GITIGNORE_CONTENT
    except OSError as exc:
        logger.warning("Failed to read %s: %s", gi_path, exc)
        return DEFAULT_GITIGNORE_CONTENT


def write_project_gitignore(
    target_path: Path | str,
    content: str,
    project_slug: str | None = None,
) -> bool:
    """
    Write updated .gitignore rules with silent watcher bypass and git index refresh.

    1. Sets a temporary runtime suppression window (2.0s) to prevent watchdog bounce.
    2. Writes content to .gitignore.
    3. Calls git reset to unstage any files newly ignored.
    """
    target = Path(target_path).resolve()
    if not target.exists():
        logger.error("Cannot write .gitignore: target path does not exist (%s)", target)
        return False

    gi_path = target / ".gitignore"
    gi_str = str(gi_path)

    try:
        # 1. Silent watchdog bypass for 2 seconds
        suppress_file_path(gi_str, duration_s=2.0)
        suppress_file_path(".gitignore", duration_s=2.0)
        try:
            from ..core import worker_lifecycle
            if hasattr(worker_lifecycle, "SUPPRESSED_FS_PATHS"):
                worker_lifecycle.SUPPRESSED_FS_PATHS[gi_str] = time.monotonic() + 2.0
                worker_lifecycle.SUPPRESSED_FS_PATHS[".gitignore"] = time.monotonic() + 2.0
        except Exception:
            pass

        # 2. Persist updated content
        gi_path.write_text(content, encoding="utf-8")
        logger.info("Updated .gitignore in %s for project %s", target, project_slug or "<workspace>")

        # Re-apply suppression post-write
        suppress_file_path(gi_str, duration_s=2.0)
        suppress_file_path(".gitignore", duration_s=2.0)

        # 3. Refresh git index to unstage newly ignored files
        git_bin = shutil.which("git") or "git"
        if (target / ".git").exists():
            subprocess.run(
                [git_bin, "reset"],
                cwd=str(target),
                capture_output=True,
                text=True,
                check=False,
                creationflags=_NO_WINDOW_FLAG,
                timeout=10,
            )

        return True
    except Exception as exc:
        logger.exception("Failed to write .gitignore in %s: %s", target, exc)
        return False


__all__ = ["read_project_gitignore", "write_project_gitignore", "DEFAULT_GITIGNORE_CONTENT"]
