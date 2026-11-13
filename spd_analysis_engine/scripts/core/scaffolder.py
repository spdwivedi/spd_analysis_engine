"""
scripts/core/scaffolder.py
===========================
Smart project workspace scaffolder, baseline gitignore generator,
and GitHub remote handshake initialization.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..git.credentials import GitCredentialManager
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


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Execute a git command within cwd with no-window flag on Windows."""
    git_bin = shutil.which("git") or "git"
    return subprocess.run(
        [git_bin, *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        creationflags=_NO_WINDOW_FLAG,
        timeout=30,
    )


def scaffold_new_project(
    project_name: str,
    target_path: str | Path,
    create_remote: bool = False,
    pat: str | None = None,
    engine_root: Path | str | None = None,
) -> dict[str, Any]:
    """
    Scaffold a workspace directory, write default .gitignore, init Git, and optionally create GitHub remote.
    """
    target = Path(target_path).resolve()
    slug = project_name.strip()
    remote_url: str | None = None

    try:
        # 1. Create target workspace directory if missing
        target.mkdir(parents=True, exist_ok=True)

        # 2. Write hardened default .gitignore if not already present
        gitignore_file = target / ".gitignore"
        if not gitignore_file.exists():
            gitignore_file.write_text(DEFAULT_GITIGNORE_CONTENT, encoding="utf-8")
            logger.info("Created baseline .gitignore in %s", target)

        # 3. Initialize Git repository with main as default branch
        init_res = _run_git(["init", "-b", "main"], target)
        if init_res.returncode != 0:
            # Fallback for older git versions
            _run_git(["init"], target)
            _run_git(["checkout", "-B", "main"], target)

        # 4. Optional Remote Handshake
        if create_remote and pat:
            res_repo = GitCredentialManager.create_remote_repo(
                repo_name=slug,
                private=True,
                token=pat,
                engine_root=engine_root,
            )
            if res_repo.get("success"):
                clone_url = str(res_repo.get("clone_url") or "")
                remote_url = clone_url
                # Configure remote origin
                remotes_res = _run_git(["remote"], target)
                if "origin" in remotes_res.stdout.split():
                    _run_git(["remote", "set-url", "origin", clone_url], target)
                else:
                    _run_git(["remote", "add", "origin", clone_url], target)

                # Create seed commit
                _run_git(["add", ".gitignore"], target)
                commit_msg = "chore(scaffold): initialize repository baseline and ignore rules"
                _run_git(["commit", "-m", commit_msg], target)

                # Push seed commit
                auth_url = GitCredentialManager.get_authenticated_url(clone_url, token=pat, engine_root=engine_root)
                push_res = _run_git(["push", "-u", auth_url, "main"], target)
                if push_res.returncode != 0:
                    logger.warning("Remote push of scaffold commit had non-zero exit: %s", push_res.stderr)
            else:
                logger.warning("Could not auto-create remote repository: %s", res_repo.get("message"))

        return {
            "success": True,
            "created": True,
            "path": str(target),
            "project_name": slug,
            "remote_url": remote_url,
        }
    except Exception as exc:
        logger.exception("Failed to scaffold project '%s' at %s", slug, target)
        return {
            "success": False,
            "created": False,
            "error": str(exc),
            "path": str(target),
            "project_name": slug,
        }


__all__ = ["scaffold_new_project", "DEFAULT_GITIGNORE_CONTENT"]
