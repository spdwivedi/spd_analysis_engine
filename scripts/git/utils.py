"""
scripts/git/utils.py
====================
Utility constants and helper functions for the git micro-versioning layer.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_NO_WINDOW_FLAG = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
IGNORED_PATH_PARTS = {".sf", ".sfdx", ".git", "node_modules", "__pycache__"}

HARDENED_GITIGNORE_TEMPLATE = """# Environment & Secrets
.env
.env.*
*.key
*.pem
*.token
*id_rsa*
credentials.json
git_config.json

# Salesforce & IDE Noise
.sf/
.sfdx/
*catalog.json*
*.__staging__
*.tmp
.vscode/
.idea/

# Python
__pycache__/
*.py[cod]
*$py.class
.pytest_cache/
.venv/
env/
*.egg-info/

# Node & Web
node_modules/
.npm/
dist/
.next/
.cache/

# SPD Engine Files
.spd/
session.db*
*.patch
worker.log
*.meta
"""


try:
    from scripts.event_bundler import suppress_file_path
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.event_bundler import suppress_file_path
    except (ImportError, ModuleNotFoundError):
        def suppress_file_path(path: Any, duration_s: float = 5.0) -> None:
            pass


def ensure_hardened_gitignore(target_path: Path | str) -> bool:
    """
    Check if .gitignore exists in the target workspace root. If missing, automatically
    create it with comprehensive protection for environment secrets, IDE noise, and engine files.
    """
    target = Path(target_path).resolve()
    gi_path = target / ".gitignore"
    if not gi_path.exists():
        try:
            target.mkdir(parents=True, exist_ok=True)
            suppress_file_path(str(gi_path), duration_s=5.0)
            gi_path.write_text(HARDENED_GITIGNORE_TEMPLATE, encoding="utf-8")
            suppress_file_path(str(gi_path), duration_s=5.0)
            logger.info("Automatically generated hardened .gitignore in %s", target)
            return True
        except Exception as exc:
            logger.warning("Could not create hardened .gitignore in %s: %s", target, exc)
            return False
    return False


def scrub_tokens(text: str, token: str | None = None) -> str:
    """Sanitize passwords, personal access tokens, and API credentials from strings/URLs."""
    if not text:
        return ""
    # 1. URL basic auth: https://user:pass@host -> https://***:***@host
    s = re.sub(r'(https?://)([^:\s@]+):([^@\s]+)@', r'\1***:***@', text)
    # 2. URL single token auth: https://token@host -> https://***@host
    s = re.sub(r'(https?://)([^@/:\s]+)@', r'\1***@', s)
    # 3. GitHub personal access tokens: ghp_...
    s = re.sub(r'ghp_[A-Za-z0-9_]+', 'ghp_***', s)
    # 4. GitHub Fine-grained PATs: github_pat_...
    s = re.sub(r'github_pat_[A-Za-z0-9_]+', 'github_pat_***', s)
    # 5. Google / Gemini API keys: AIza...
    s = re.sub(r'AIza[A-Za-z0-9_\-]+', 'AIza***', s)
    # 6. Explicit token if provided
    if token and token in s:
        s = s.replace(token, '***')
    return s


def discover_native_git_remote(target_path: Path | str) -> tuple[str, str]:
    """
    Auto-discover remote origin URL and active branch from target workspace's native .git.
    Returns (remote_url, branch_name).
    """
    target = Path(target_path).resolve()
    git_dir = target / ".git"
    if not git_dir.exists():
        return "", ""

    git_bin = shutil.which("git")
    if not git_bin:
        return "", ""

    remote_url = ""
    branch = ""

    try:
        res = subprocess.run(
            [git_bin, "config", "--get", "remote.origin.url"],
            cwd=str(target),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3,
            creationflags=_NO_WINDOW_FLAG,
            shell=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            remote_url = scrub_tokens(res.stdout.strip())
    except Exception:
        pass

    try:
        res_branch = subprocess.run(
            [git_bin, "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(target),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3,
            creationflags=_NO_WINDOW_FLAG,
            shell=False,
        )
        if res_branch.returncode == 0 and res_branch.stdout.strip():
            branch = res_branch.stdout.strip()
    except Exception:
        pass

    return remote_url, branch or "main"


def is_salesforce_or_transient_file(path_str: str) -> bool:
    """Check if a file belongs to .sf, .sfdx, staging, or transient directories."""
    p = Path(path_str)
    for part in p.parts:
        if part in IGNORED_PATH_PARTS or part.startswith(".sf"):
            return True
    name = p.name
    if name.startswith("catalog.json") or name.endswith(".__staging__") or name.endswith(".tmp"):
        return True
    return False
