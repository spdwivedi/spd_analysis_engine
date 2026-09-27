"""
scripts/trash/remote_gh.py
==========================
Remote GitHub repository deletion handler using stored credentials.
"""

from __future__ import annotations

import logging
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

try:
    from ..git_shadow import GitCredentialManager
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.git_shadow import GitCredentialManager
    except (ImportError, ModuleNotFoundError):
        from scripts.git_shadow import GitCredentialManager

logger = logging.getLogger(__name__)


def delete_remote_github_repo(engine_root: Path, remote_url: str) -> dict[str, Any]:
    """Call GitHub REST API to delete remote repository using stored credentials."""
    cfg = GitCredentialManager.load_config(engine_root)
    token = cfg.get("github_token", "")
    if not token:
        return {"deleted": False, "message": "GitHub personal access token not found in config."}

    # Parse owner and repo from URL (e.g. https://github.com/owner/repo.git or git@github.com:owner/repo.git)
    clean_url = remote_url.strip()
    m = re.search(r"github\.com[/:]([\w\.-]+)/([\w\.-]+?)(?:\.git)?$", clean_url)
    if not m:
        return {"deleted": False, "message": f"Could not parse owner/repo from remote URL: {remote_url}"}

    owner, repo = m.group(1), m.group(2)
    api_url = f"https://api.github.com/repos/{owner}/{repo}"

    req = urllib.request.Request(
        api_url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "SPD-Analysis-Engine/1.0",
        },
        method="DELETE",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status in (204, 200):
                return {"deleted": True, "message": f"Successfully deleted remote repo {owner}/{repo} on GitHub."}
            return {"deleted": False, "message": f"Unexpected status HTTP {resp.status} from GitHub API."}
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
        return {"deleted": False, "message": f"GitHub API error HTTP {exc.code}: {err_body or exc.reason}"}
    except Exception as exc:
        return {"deleted": False, "message": f"Failed to contact GitHub API: {exc}"}
