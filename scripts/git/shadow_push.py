"""
scripts/git/shadow_push.py
==========================
Standalone push_to_remote_repo() function extracted from ShadowGit.push_to_remote().
Stages, commits, updates CHANGELOG.md, and pushes primary workspace changes to a
remote Git repository.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

try:
    from .utils import _NO_WINDOW_FLAG
    from .credentials import GitCredentialManager
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.git.utils import _NO_WINDOW_FLAG
        from spd_analysis_engine.scripts.git.credentials import GitCredentialManager
    except (ImportError, ModuleNotFoundError):
        from scripts.git.utils import _NO_WINDOW_FLAG
        from scripts.git.credentials import GitCredentialManager

logger = logging.getLogger(__name__)


def push_to_remote_repo(
    repo_path: Path | str,
    remote_url: str | None = None,
    commit_message: str | None = None,
    changelog_content: str | None = None,
    branch: str = "main",
    remote_name: str = "origin",
    engine_root: Path | str | None = None,
) -> dict[str, Any]:
    """
    Stage, commit, update CHANGELOG.md, and push primary workspace changes to a
    remote Git repository.

    Parameters
    ----------
    repo_path : Path | str
        Target workspace root directory containing the primary ``.git`` folder.
    remote_url : str | None
        Remote repository URL (e.g. ``https://github.com/owner/repo.git``) or remote name.
    commit_message : str | None
        Structured semantic commit message (e.g. Conventional Commit format).
    changelog_content : str | None
        Markdown content block to record in ``CHANGELOG.md``.
    branch : str
        Target branch name (default: ``"main"``).
    remote_name : str
        Remote identifier if remote_url is omitted (default: ``"origin"``).
    engine_root : Path | str | None
        Root of spd_analysis_engine for credential resolution.

    Returns
    -------
    dict[str, Any]
        ``{"success": bool, "message": str, "remote": str, "branch": str, "commit": str | None}``
    """
    target = Path(repo_path).resolve()
    git_bin = shutil.which("git")
    if not git_bin:
        return {
            "success": False,
            "message": "Git executable not found in PATH",
            "remote": remote_name,
            "branch": branch,
            "commit": None,
        }

    if not (target / ".git").exists():
        return {
            "success": False,
            "message": f"Target workspace '{target}' is not a Git repository (.git not found)",
            "remote": remote_name,
            "branch": branch,
            "commit": None,
        }

    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = "echo"
    env.setdefault("GIT_AUTHOR_NAME", "SPD Engine")
    env.setdefault("GIT_AUTHOR_EMAIL", "spd-engine@local")
    env.setdefault("GIT_COMMITTER_NAME", "SPD Engine")
    env.setdefault("GIT_COMMITTER_EMAIL", "spd-engine@local")

    def _run_in_repo(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [git_bin, *args],
            cwd=str(target),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            check=False,
            creationflags=_NO_WINDOW_FLAG,
            shell=False,
        )

    try:
        # 0. Root commit guard: check if target repo has an existing commit or is an unborn branch
        root_check = _run_in_repo("rev-parse", "--verify", "HEAD")
        if root_check.returncode != 0:
            logger.info("Unborn branch / empty repository detected in %s; creating initial root commit", target)
            _run_in_repo("add", "-A")
            _run_in_repo("commit", "-m", "feat(core): initial project commit", "--allow-empty")
            if branch:
                _run_in_repo("branch", "-M", branch)

        # 1. Update CHANGELOG.md if content is provided
        if changelog_content and changelog_content.strip():
            lock_file = target / ".engine_sync.lock"
            try:
                lock_file.write_text(str(time.time()), encoding="utf-8")
                from spd_analysis_engine.scripts.watcher_fs import suppress_sync_events
                suppress_sync_events(6.0)
            except Exception:
                try:
                    from scripts.watcher_fs import suppress_sync_events
                    suppress_sync_events(6.0)
                except Exception:
                    pass
            try:
                from spd_analysis_engine.scripts.event_bundler import suppress_file_path
                suppress_file_path("CHANGELOG.md", 4.0)
                suppress_file_path(str(target / "CHANGELOG.md"), 4.0)
            except Exception:
                pass
            changelog_path = target / "CHANGELOG.md"
            cleaned_entry = changelog_content.strip()
            if changelog_path.exists():
                existing_text = changelog_path.read_text(encoding="utf-8")
                if existing_text.startswith("# Changelog"):
                    rest = existing_text[len("# Changelog"):].lstrip()
                    new_text = f"# Changelog\n\n{cleaned_entry}\n\n{rest}"
                else:
                    new_text = f"# Changelog\n\n{cleaned_entry}\n\n{existing_text}"
            else:
                new_text = f"# Changelog\n\n{cleaned_entry}\n\n"
            changelog_path.write_text(new_text, encoding="utf-8")
            try:
                from spd_analysis_engine.scripts.event_bundler import suppress_file_path
                suppress_file_path("CHANGELOG.md", 4.0)
                suppress_file_path(str(target / "CHANGELOG.md"), 4.0)
            except Exception:
                pass
            try:
                if lock_file.exists():
                    lock_file.unlink()
            except Exception:
                pass
            logger.info("Updated CHANGELOG.md in %s", target)

        # 2. Detect active branch
        branch_res = _run_in_repo("branch", "--show-current")
        active_branch = branch_res.stdout.strip() if branch_res.returncode == 0 else ""
        if not active_branch or active_branch == "HEAD":
            active_branch = branch

        # 3. Stage changes
        _run_in_repo("add", "-A")

        # 4. Commit if working tree is dirty
        status_res = _run_in_repo("status", "--porcelain")
        if status_res.stdout.strip():
            from datetime import datetime, timezone
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            msg = commit_message.strip() if commit_message else f"SPD Session Sync: {ts}"
            commit_res = _run_in_repo("commit", "-m", msg, "--allow-empty")
            logger.info("Committed workspace changes: %s", commit_res.stdout.strip())

        # 5. Get current HEAD commit hash
        rev_res = _run_in_repo("rev-parse", "HEAD")
        commit_hash = rev_res.stdout.strip() if rev_res.returncode == 0 else None

        # 6. Resolve target push destination & credentials
        target_push = ""
        effective_remote_label = remote_name

        if remote_url and (
            remote_url.startswith("http://")
            or remote_url.startswith("https://")
            or Path(remote_url).exists()
            or remote_url.endswith(".git")
        ):
            target_push = GitCredentialManager.get_authenticated_url(remote_url, engine_root=engine_root)
            effective_remote_label = remote_url
        else:
            lookup_name = remote_name or remote_url or "origin"
            remotes_res = _run_in_repo("remote")
            configured_remotes = [r.strip() for r in remotes_res.stdout.splitlines() if r.strip()]
            if lookup_name not in configured_remotes:
                return {
                    "success": False,
                    "message": (
                        f"No remote named '{lookup_name}' configured in '{target}'. "
                        f"Configured remotes: {configured_remotes or 'none'}"
                    ),
                    "remote": lookup_name,
                    "branch": active_branch,
                    "commit": commit_hash,
                }
            effective_remote_label = lookup_name
            get_url_res = _run_in_repo("remote", "get-url", lookup_name)
            configured_url = get_url_res.stdout.strip()
            if configured_url.startswith("http://") or configured_url.startswith("https://"):
                target_push = GitCredentialManager.get_authenticated_url(configured_url, engine_root=engine_root)
            else:
                target_push = lookup_name

        # 7. Push to remote
        push_res = _run_in_repo("push", "-u", target_push, active_branch)
        clean_stdout = GitCredentialManager.scrub_text(push_res.stdout)
        clean_stderr = GitCredentialManager.scrub_text(push_res.stderr)

        if push_res.returncode == 0:
            masked_remote = GitCredentialManager.scrub_text(effective_remote_label)
            msg = f"Successfully pushed {active_branch} to {masked_remote}"
            logger.info(msg)
            return {
                "success": True,
                "message": msg,
                "remote": masked_remote,
                "branch": active_branch,
                "commit": commit_hash,
            }
        else:
            err_msg = clean_stderr.strip() or clean_stdout.strip() or "Unknown git push error"
            logger.warning("Git push failed: %s", err_msg)
            return {
                "success": False,
                "message": f"Git push failed: {err_msg}",
                "remote": GitCredentialManager.scrub_text(effective_remote_label),
                "branch": active_branch,
                "commit": commit_hash,
            }
    except Exception as exc:
        logger.exception("Exception during push_to_remote_repo")
        return {
            "success": False,
            "message": f"Error executing git push: {GitCredentialManager.scrub_text(str(exc))}",
            "remote": GitCredentialManager.scrub_text(remote_name),
            "branch": branch,
            "commit": None,
        }
