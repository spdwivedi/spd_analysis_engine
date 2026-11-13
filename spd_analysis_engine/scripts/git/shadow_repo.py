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

from .utils import ensure_hardened_gitignore, is_salesforce_or_transient_file, _NO_WINDOW_FLAG
from .credentials import GitCredentialManager
from .shadow_push import push_to_remote_repo
from .shadow_rollback import ShadowRollbackMixin
from .shadow_commit import (
    get_shadow_git_env,
    configure_shadow_repository,
    execute_burst_commit,
    recover_unfinished_git_ops,
    recover_stale_index_lock,
    GitState,
    GitBurstResult,
)
from ..core.changeset import ChangeSet, DEFAULT_MAX_PATCH_BYTES

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
        self._lock = threading.RLock()
        self._initialized = False

    @property
    def is_available(self) -> bool:
        """Return True if git binary is present on the host system."""
        return self.git_cmd is not None

    def _run_git(self, *args: str, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
        """Execute a git command targeting the shadow repository with explicit timeout."""
        if not self.git_cmd:
            raise RuntimeError("Git executable not found in PATH")

        cmd = [
            self.git_cmd,
            f"--git-dir={self.shadow_dir}",
            f"--work-tree={self.target_dir}",
            *args,
        ]

        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout,
            env=get_shadow_git_env(),
            creationflags=_NO_WINDOW_FLAG,
            shell=False,
        )

    def init_repo(self) -> bool:
        """
        Initialize the shadow git repository and configure local identity and hardened parameters.
        Returns True on success, False if git is unavailable.
        """
        if not self.is_available:
            logger.warning("Git is not installed; ShadowGit micro-versioning disabled.")
            return False

        with self._lock:
            try:
                ensure_hardened_gitignore(self.target_dir)
                self.shadow_dir.mkdir(parents=True, exist_ok=True)
                head_file = self.shadow_dir / "HEAD"
                if not head_file.exists():
                    res = self._run_git("init")
                    if res.returncode != 0:
                        logger.error("Failed to init shadow git: %s", res.stderr)
                        return False

                    # Configure identity and engine settings
                    configure_shadow_repository(self.git_cmd, self.shadow_dir, self.target_dir)
                    logger.info("Initialized shadow git repo at %s", self.shadow_dir)
                else:
                    configure_shadow_repository(self.git_cmd, self.shadow_dir, self.target_dir)

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

    def commit_changeset(
        self,
        changeset: ChangeSet,
        summary: str = "",
        first_file: str | None = None,
        max_patch_bytes: int = DEFAULT_MAX_PATCH_BYTES,
        timeout: float = 30.0,
    ) -> GitBurstResult:
        """
        Stage and commit a ChangeSet to Shadow Git following the authoritative workflow:
        git add -> git diff --cached --quiet -> git commit (no --allow-empty) -> git diff-tree.
        """
        if not self.is_available:
            return GitBurstResult(state=GitState.FAILED, error="Git not available")

        if not self._initialized and not self.init_repo():
            return GitBurstResult(state=GitState.FAILED, error="Shadow repo init failed")

        with self._lock:
            self.recover_stale_index_lock()
            return execute_burst_commit(
                git_cmd=self.git_cmd,
                shadow_dir=self.shadow_dir,
                target_dir=self.target_dir,
                changeset=changeset,
                summary=summary,
                first_file=first_file,
                max_patch_bytes=max_patch_bytes,
                timeout=timeout,
            )

    def commit_burst(
        self,
        event_id: int,
        first_file: str | None,
        summary: str,
        files_touched: list[str] | None = None,
    ) -> str | None:
        """
        Compatibility method: wraps files_touched into a ChangeSet and executes burst commit.
        """
        files = list(files_touched) if files_touched is not None else ["."]
        cs = ChangeSet(
            burst_uid=f"burst_{event_id}",
            event_id=event_id,
            files=files,
            effective_files=files,
        )
        result = self.commit_changeset(cs, summary=summary, first_file=first_file)
        return result.commit_hash

    def recover_pending_ops(self, conn: Any) -> list[dict[str, Any]]:
        """Recover and retry unfinished Git operations from a previous crash."""
        if not self.is_available:
            return []
        with self._lock:
            return recover_unfinished_git_ops(conn, self)

    def recover_stale_index_lock(self, max_age_s: float = 60.0) -> bool:
        """Recover stale index.lock in shadow_dir if older than max_age_s and no active git process holds it."""
        if not self.shadow_dir.exists():
            return False
        return recover_stale_index_lock(self.shadow_dir, max_age_s=max_age_s)

    def run_maintenance(self, keep_refs: list[str] | None = None) -> dict[str, Any]:
        """
        Run scheduled maintenance on the shadow repository:
        1. Recover stale index.lock.
        2. Repack loose objects into packfile (`git repack -ad -q`).
        3. Prune loose objects older than 30 days (`git prune --expire=30.days`).
        Milestone and rollback refs are protected by native reachability in refs/.
        """
        if not self.is_available or not self.shadow_dir.exists():
            return {"success": False, "error": "Shadow repository unavailable"}

        with self._lock:
            lock_recovered = self.recover_stale_index_lock()
            repack_ok = False
            prune_ok = False
            error = None

            try:
                if keep_refs:
                    for ref_spec in keep_refs:
                        try:
                            parts = ref_spec.split(":", 1)
                            ref_name = f"refs/keep/{parts[0]}"
                            commit_hash = parts[-1]
                            self._run_git("update-ref", ref_name, commit_hash, timeout=10.0)
                        except Exception as ref_exc:
                            logger.debug("Failed pinning keep ref %s: %s", ref_spec, ref_exc)

                res_repack = self._run_git("repack", "-ad", "-q", timeout=60.0)
                repack_ok = res_repack.returncode == 0
                if not repack_ok:
                    error = res_repack.stderr or res_repack.stdout

                res_prune = self._run_git("prune", "--expire=30.days", timeout=60.0)
                prune_ok = res_prune.returncode == 0
                if not prune_ok and not error:
                    error = res_prune.stderr or res_prune.stdout
            except Exception as exc:
                logger.error("Error during shadow git maintenance: %s", exc)
                error = str(exc)

            return {
                "success": repack_ok and prune_ok,
                "stale_lock_recovered": lock_recovered,
                "repack_success": repack_ok,
                "prune_success": prune_ok,
                "error": error,
            }

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

