"""
scripts/git/shadow_rollback.py
==============================
Rollback and physical workspace restoration routines for ShadowGit.
"""
from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any

try:
    from .utils import _NO_WINDOW_FLAG
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.git.utils import _NO_WINDOW_FLAG
    except (ImportError, ModuleNotFoundError):
        from scripts.git.utils import _NO_WINDOW_FLAG

logger = logging.getLogger(__name__)


class ShadowRollbackMixin:
    """Mixin containing rollback and physical workspace restoration routines for ShadowGit."""

    def rollback_burst_commit(
        self,
        event_id: int,
        commit_hash: str | None = None,
    ) -> bool:
        """
        Undo the shadow git commit for a specific burst event.
        If HEAD corresponds to this commit, resets to HEAD~1.
        If it has subsequent commits, applies a git revert or reset.
        """
        if not getattr(self, "is_available", False) or not self.shadow_dir.exists():
            return False

        with self._lock:
            try:
                commit = commit_hash or self.get_commit_for_event(event_id)
                if not commit:
                    logger.debug("No ShadowGit commit found for event %d", event_id)
                    return False

                # Check current HEAD
                head_res = self._run_git("rev-parse", "HEAD")
                current_head = head_res.stdout.strip() if head_res.returncode == 0 else ""

                # Check if commit has a parent
                has_parent_res = self._run_git("rev-parse", "--verify", f"{commit}~1")
                has_parent = (has_parent_res.returncode == 0)

                if current_head.startswith(commit):
                    if has_parent:
                        res = self._run_git("reset", "--hard", "HEAD~1")
                    else:
                        res = self._run_git("update-ref", "-d", "HEAD")
                    if res.returncode != 0:
                        return False
                    verify_head = self._run_git("rev-parse", "HEAD")
                    new_head = verify_head.stdout.strip() if verify_head.returncode == 0 else ""
                    return (new_head != current_head) or (not has_parent and not new_head)
                else:
                    if has_parent:
                        res = self._run_git("revert", "--no-edit", commit)
                        if res.returncode != 0:
                            self._run_git("revert", "--abort")
                            res = self._run_git("reset", "--hard", f"{commit}~1")
                        return res.returncode == 0
                    else:
                        res = self._run_git("reset", "--hard", "HEAD~1")
                        return res.returncode == 0
            except Exception as exc:
                logger.error("Failed to rollback shadow git commit for event %d: %s", event_id, exc)
                return False

    def restore_workspace_to_commit(
        self,
        commit_hash: str,
        target_dir: Path | str | None = None,
    ) -> bool:
        """
        Physically restore workspace files to a specific commit state.
        1. Force check out all files from the commit into the target workspace:
           `git checkout -f <commit_hash> -- .`
        2. Query untracked/added files added after this commit and remove them:
           `git diff --name-only --diff-filter=A <commit_hash> HEAD`
        3. Reset shadow git ref to this commit.
        """
        if not getattr(self, "is_available", False) or not self.shadow_dir.exists():
            logger.warning("ShadowGit repository not available at %s", self.shadow_dir)
            return False

        work_tree = Path(target_dir).resolve() if target_dir else self.target_dir
        if not work_tree.exists():
            logger.warning("Target directory does not exist: %s", work_tree)
            return False

        with self._lock:
            try:
                cmd_env = dict(os.environ)
                cmd_env.pop("GIT_DIR", None)
                cmd_env.pop("GIT_WORK_TREE", None)
                cmd_env.pop("GIT_INDEX_FILE", None)

                # 1. Force checkout all files from commit_hash into work_tree
                checkout_cmd = [
                    self.git_cmd,
                    f"--git-dir={self.shadow_dir}",
                    f"--work-tree={work_tree}",
                    "checkout",
                    "-f",
                    commit_hash,
                    "--",
                    ".",
                ]
                res = subprocess.run(
                    checkout_cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                    env=cmd_env,
                    creationflags=_NO_WINDOW_FLAG,
                    shell=False,
                )
                if res.returncode != 0:
                    logger.warning(
                        "ShadowGit restore checkout failed for %s: %s",
                        commit_hash[:8],
                        res.stderr or res.stdout,
                    )
                    return False

                # 2. Query files added after this commit up to HEAD
                diff_cmd = [
                    self.git_cmd,
                    f"--git-dir={self.shadow_dir}",
                    f"--work-tree={work_tree}",
                    "diff",
                    "--name-only",
                    "--diff-filter=A",
                    commit_hash,
                    "HEAD",
                ]
                diff_res = subprocess.run(
                    diff_cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                    env=cmd_env,
                    creationflags=_NO_WINDOW_FLAG,
                    shell=False,
                )
                if diff_res.returncode == 0 and diff_res.stdout.strip():
                    for rel_file in diff_res.stdout.strip().splitlines():
                        rel_file = rel_file.strip()
                        if not rel_file:
                            continue
                        f_path = work_tree / rel_file
                        if f_path.is_file() or f_path.is_symlink():
                            try:
                                f_path.unlink()
                                logger.info("Removed added file post-commit %s: %s", commit_hash[:8], rel_file)
                            except Exception as e:
                                logger.warning("Could not remove added file %s: %s", f_path, e)
                        parent = f_path.parent
                        while parent != work_tree and parent.is_relative_to(work_tree):
                            try:
                                if parent.exists() and not any(parent.iterdir()):
                                    parent.rmdir()
                                    parent = parent.parent
                                else:
                                    break
                            except Exception:
                                break

                # 3. Reset shadow git ref to this commit so HEAD matches
                reset_cmd = [
                    self.git_cmd,
                    f"--git-dir={self.shadow_dir}",
                    f"--work-tree={work_tree}",
                    "reset",
                    "--hard",
                    commit_hash,
                ]
                subprocess.run(
                    reset_cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                    env=cmd_env,
                    creationflags=_NO_WINDOW_FLAG,
                    shell=False,
                )

                logger.info("Successfully restored workspace %s to commit %s", work_tree, commit_hash[:8])
                return True
            except Exception as exc:
                logger.error("Exception during restore_workspace_to_commit: %s", exc)
                return False
