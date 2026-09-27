"""
scripts/git/shadow_repo.py
==========================
ShadowGit: isolated Git micro-versioning for the SPD Analysis Engine (Phase 4).

Manages an isolated Git repository in ``current/<slug>/shadow_git/`` pointing to
the workspace ``target_dir`` via ``--git-dir`` and ``--work-tree``.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

try:
    from .utils import ensure_hardened_gitignore, is_salesforce_or_transient_file, _NO_WINDOW_FLAG
    from .credentials import GitCredentialManager
    from .shadow_push import push_to_remote_repo
    from .shadow_rollback import ShadowRollbackMixin
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.git.utils import ensure_hardened_gitignore, is_salesforce_or_transient_file, _NO_WINDOW_FLAG
        from spd_analysis_engine.scripts.git.credentials import GitCredentialManager
        from spd_analysis_engine.scripts.git.shadow_push import push_to_remote_repo
        from spd_analysis_engine.scripts.git.shadow_rollback import ShadowRollbackMixin
    except (ImportError, ModuleNotFoundError):
        from scripts.git.utils import ensure_hardened_gitignore, is_salesforce_or_transient_file, _NO_WINDOW_FLAG
        from scripts.git.credentials import GitCredentialManager
        from scripts.git.shadow_push import push_to_remote_repo
        from scripts.git.shadow_rollback import ShadowRollbackMixin

logger = logging.getLogger(__name__)


class ShadowGit(ShadowRollbackMixin):
    """
    Manages an isolated Git repository for passive session micro-versioning.

    Parameters
    ----------
    shadow_dir : Path | str
        Path to the isolated shadow git directory (e.g. ``current/<slug>/shadow_git``).
    target_dir : Path | str
        Path to the workspace root directory being monitored.
    """

    def __init__(self, shadow_dir: Path | str, target_dir: Path | str) -> None:
        self.shadow_dir = Path(shadow_dir).resolve()
        self.target_dir = Path(target_dir).resolve()
        self.git_cmd = shutil.which("git")
        self._lock = threading.Lock()
        self._initialized = False

    @property
    def is_available(self) -> bool:
        """Return True if git binary is present on the host system."""
        return self.git_cmd is not None

    def _run_git(self, *args: str) -> subprocess.CompletedProcess[str]:
        """Execute a git command targeting the shadow repository."""
        if not self.git_cmd:
            raise RuntimeError("Git executable not found in PATH")

        cmd = [
            self.git_cmd,
            f"--git-dir={self.shadow_dir}",
            f"--work-tree={self.target_dir}",
            *args,
        ]

        # Use clean environment without interfering GIT_* vars
        env = dict(os.environ)
        env.pop("GIT_DIR", None)
        env.pop("GIT_WORK_TREE", None)
        env.pop("GIT_INDEX_FILE", None)

        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            env=env,
            creationflags=_NO_WINDOW_FLAG,
            shell=False,
        )

    def init_repo(self) -> bool:
        """
        Initialize the shadow git repository and configure local identity.
        Returns True on success, False if git is unavailable.
        """
        if not self.is_available:
            logger.warning("Git is not installed; ShadowGit micro-versioning disabled.")
            return False

        with self._lock:
            try:
                ensure_hardened_gitignore(self.target_dir)
                self.shadow_dir.mkdir(parents=True, exist_ok=True)
                # Check if repository already initialized
                head_file = self.shadow_dir / "HEAD"
                if not head_file.exists():
                    res = self._run_git("init")
                    if res.returncode != 0:
                        logger.error("Failed to init shadow git: %s", res.stderr)
                        return False

                    # Configure identity inside shadow repository only
                    self._run_git("config", "user.name", "SPD Shadow Engine")
                    self._run_git("config", "user.email", "spd-shadow@local")
                    self._run_git("config", "core.autocrlf", "false")
                    self._run_git("config", "commit.gpgsign", "false")

                    logger.info("Initialized shadow git repo at %s", self.shadow_dir)

                # Set up shadow git info/exclude to ignore .sf and transient artifacts
                try:
                    exclude_file = self.shadow_dir / "info" / "exclude"
                    exclude_file.parent.mkdir(parents=True, exist_ok=True)
                    default_excludes = "\n.sf\n.sf/**\n.sfdx\n.sfdx/**\n*.__staging__\n*.tmp\ncatalog.json*\nsession.db*\nworker.log\nworker.pid*\nworker.status.json\nproject.meta\n"
                    if exclude_file.exists():
                        cur = exclude_file.read_text(encoding="utf-8", errors="replace")
                        if ".sf" not in cur:
                            exclude_file.write_text(cur + default_excludes, encoding="utf-8")
                    else:
                        exclude_file.write_text(default_excludes, encoding="utf-8")
                except Exception as exc:
                    logger.debug("Failed to write shadow exclude file: %s", exc)

                self._initialized = True
                return True
            except Exception as exc:
                logger.error("Error initializing shadow git: %s", exc)
                return False

    def commit_burst(
        self,
        event_id: int,
        first_file: str | None,
        summary: str,
        files_touched: list[str] | None = None,
    ) -> str | None:
        """
        Stage touched files and create an atomic micro-commit.

        Parameters
        ----------
        event_id : int
            Event identifier from ``session.db``.
        first_file : str | None
            The earliest modified file in the burst (entrypoint).
        summary : str
            Brief human-readable summary of the burst.
        files_touched : list[str] | None
            Relative paths of modified or created files.

        Returns
        -------
        str | None
            The full commit SHA-1 hash, or ``None`` on failure.
        """
        if not self.is_available:
            return None

        if not self._initialized and not self.init_repo():
            return None

        with self._lock:
            try:
                # Stage files
                if files_touched:
                    # Filter out .sf, .sfdx, and transient files
                    files_touched = [f for f in files_touched if not is_salesforce_or_transient_file(f)]
                    # Filter existing files vs deleted files
                    existing = []
                    deleted = []
                    for f in files_touched:
                        abs_p = self.target_dir / Path(f)
                        if abs_p.exists():
                            existing.append(f)
                        else:
                            deleted.append(f)

                    if existing:
                        self._run_git("add", "--", *existing)
                    if deleted:
                        self._run_git("rm", "--cached", "--ignore-unmatch", "--", *deleted)
                else:
                    self._run_git("add", "-A")

                entry_tag = f"[{first_file}] " if first_file else ""
                commit_msg = f"Burst #{event_id}: {entry_tag}{summary}"

                res = self._run_git("commit", "-m", commit_msg, "--allow-empty")
                if res.returncode != 0 and "nothing to commit" not in res.stdout:
                    logger.debug("Shadow commit notice: %s", res.stdout or res.stderr)

                # Get latest commit hash
                hash_res = self._run_git("rev-parse", "HEAD")
                if hash_res.returncode == 0:
                    commit_hash = hash_res.stdout.strip()
                    logger.info("Shadow Git micro-commit created: %s (Burst #%d)", commit_hash[:8], event_id)
                    return commit_hash
                return None
            except Exception as exc:
                logger.error("Failed to create shadow git commit: %s", exc)
                return None

    def get_commit_history(self, max_count: int = 50) -> list[dict[str, Any]]:
        """
        Retrieve chronological commit history from the shadow git repository.

        Returns
        -------
        list[dict]
            List of ``{"hash", "short_hash", "author", "date", "message"}`` dicts.
        """
        if not self.is_available or not self.shadow_dir.exists():
            return []

        with self._lock:
            try:
                res = self._run_git(
                    "log",
                    f"-n{max_count}",
                    "--format=%H|%an|%ad|%s",
                    "--date=iso-strict",
                )
                if res.returncode != 0:
                    return []

                commits = []
                for line in res.stdout.splitlines():
                    parts = line.strip().split("|", 3)
                    if len(parts) == 4:
                        commits.append({
                            "hash": parts[0],
                            "short_hash": parts[0][:8],
                            "author": parts[1],
                            "date": parts[2],
                            "message": parts[3],
                        })
                return commits
            except Exception as exc:
                logger.debug("Error fetching shadow git history: %s", exc)
                return []

    def get_commit_for_event(self, event_id: int) -> str | None:
        """Find the ShadowGit commit hash matching the given burst event ID."""
        if not self.is_available or not self.shadow_dir.exists():
            return None
        with self._lock:
            try:
                res = self._run_git("log", "--all", f"--grep=^Burst #{event_id}:", "-n1", "--format=%H")
                if res.returncode == 0 and res.stdout.strip():
                    return res.stdout.strip()
                return None
            except Exception as exc:
                logger.debug("Error finding commit for event %d: %s", event_id, exc)
                return None

    def get_delta_diff(
        self,
        file_path: str,
        commit_hash: str | None = None,
        event_id: int | None = None,
    ) -> str:
        """
        Compute the dedicated version delta diff for a file relative to its immediate previous commit.
        Runs `git diff <commit>~1 <commit> -- <file_path>` (or diff-tree for root commits).
        """
        if not self.is_available or not self.shadow_dir.exists():
            return ""

        norm_path = str(file_path).replace("\\", "/")

        with self._lock:
            try:
                ref = commit_hash
                if not ref and event_id is not None:
                    res = self._run_git("log", "--all", f"--grep=^Burst #{event_id}:", "-n1", "--format=%H")
                    if res.returncode == 0 and res.stdout.strip():
                        ref = res.stdout.strip()

                if not ref:
                    ref = "HEAD"

                parent_check = self._run_git("rev-parse", "--verify", f"{ref}^")
                if parent_check.returncode == 0:
                    diff_res = self._run_git("diff", f"{ref}~1", ref, "--", norm_path)
                else:
                    diff_res = self._run_git("diff-tree", "-p", "--root", ref, "--", norm_path)

                if diff_res.returncode == 0 and diff_res.stdout.strip():
                    return diff_res.stdout.strip() + "\n"
                return ""
            except Exception as exc:
                logger.debug("Error computing delta diff in ShadowGit: %s", exc)
                return ""

    def get_burst_delta_diff(
        self,
        event_id: int | None = None,
        commit_hash: str | None = None,
        files: list[str] | None = None,
    ) -> str:
        """
        Compute delta diff strictly between immediate previous commit and current commit
        (`git diff HEAD~1 HEAD -- <files>`) for an atomic burst.
        """
        if not self.is_available or not self.shadow_dir.exists():
            return ""

        with self._lock:
            try:
                ref = commit_hash
                if not ref and event_id is not None:
                    res = self._run_git("log", "--all", f"--grep=^Burst #{event_id}:", "-n1", "--format=%H")
                    if res.returncode == 0 and res.stdout.strip():
                        ref = res.stdout.strip()

                if not ref:
                    ref = "HEAD"

                parent_check = self._run_git("rev-parse", "--verify", f"{ref}^")
                file_args = [str(f).replace("\\", "/") for f in (files or []) if f]
                cmd_args = ["--", *file_args] if file_args else []

                if parent_check.returncode == 0:
                    diff_res = self._run_git("diff", f"{ref}~1", ref, *cmd_args)
                else:
                    diff_res = self._run_git("diff-tree", "-p", "--root", ref, *cmd_args)

                if diff_res.returncode == 0 and diff_res.stdout.strip():
                    return diff_res.stdout.strip() + "\n"
                return ""
            except Exception as exc:
                logger.debug("Error computing burst delta diff in ShadowGit: %s", exc)
                return ""

    @staticmethod
    def ensure_hardened_gitignore(target_path: Path | str) -> bool:
        """Expose hardened gitignore utility as a static method on ShadowGit."""
        return ensure_hardened_gitignore(target_path)

    @classmethod
    def _ensure_shadow_repo(cls, repo_path: Path | str) -> bool:
        """
        Ensure target workspace root has a hardened .gitignore and valid repo configuration.
        """
        target = Path(repo_path).resolve()
        return ensure_hardened_gitignore(target)

    @staticmethod
    def init_primary_repo(target_dir: Path | str) -> bool:
        """
        Initialize a primary ``.git`` repository in the target workspace if absent.
        Automatically verifies and creates a hardened .gitignore.
        """
        target = Path(target_dir).resolve()
        ensure_hardened_gitignore(target)
        if (target / ".git").exists():
            return False

        git_bin = shutil.which("git")
        if not git_bin:
            return False

        try:
            res = subprocess.run(
                [git_bin, "init"],
                cwd=str(target),
                capture_output=True,
                text=True,
                check=False,
                creationflags=_NO_WINDOW_FLAG,
                shell=False,
            )
            if res.returncode == 0:
                logger.info("Initialized primary git repo in %s", target)
                return True
        except Exception as exc:
            logger.warning("Could not initialize primary git repo in %s: %s", target, exc)

        return False

    @staticmethod
    def push_to_remote(
        repo_path: Path | str,
        remote_url: str | None = None,
        commit_message: str | None = None,
        changelog_content: str | None = None,
        branch: str = "main",
        remote_name: str = "origin",
        engine_root: Path | str | None = None,
    ) -> dict:
        """
        Stage, commit, update CHANGELOG.md, and push primary workspace changes to a remote Git
        repository. Delegates to :func:`scripts.git.shadow_push.push_to_remote_repo`.
        """
        return push_to_remote_repo(
            repo_path=repo_path,
            remote_url=remote_url,
            commit_message=commit_message,
            changelog_content=changelog_content,
            branch=branch,
            remote_name=remote_name,
            engine_root=engine_root,
        )

