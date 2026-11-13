"""
scripts/git/shadow_rollback.py
==============================
Rollback and physical workspace restoration routines for ShadowGit.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .utils import _NO_WINDOW_FLAG
from ..core.faults import check_fault

logger = logging.getLogger(__name__)


def _run_git_cmd(cmd: list[str], timeout: int = 8) -> subprocess.CompletedProcess[str]:
    """Execute git subprocess with safe.directory and timeout guard."""
    env = dict(os.environ)
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(k, None)
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=env,
        creationflags=_NO_WINDOW_FLAG,
        timeout=timeout,
    )


def rollback_to_burst(
    target_path: Path | str,
    shadow_git_dir: Path | str,
    burst_hash: str,
    project_slug: str,
    engine_root: Path | str | None = None,
    burst_id: int | None = None,
) -> dict[str, Any]:
    """
    Safely rollback workspace to burst_hash with pre-rollback trash preservation:
    1. Identify modified/untracked files and back them up to trash/<slug>/rollback_<timestamp>/.
    2. Revert files via checkout -f, clean -fd, and reset --hard with timeout=8.
    """
    target_p = Path(target_path).resolve()
    shadow_p = Path(shadow_git_dir).resolve()
    if not target_p.exists() or not shadow_p.exists():
        return {"success": False, "error": "Invalid workspace or shadow git directory"}

    eng_p = Path(engine_root).resolve() if engine_root else target_p.parent
    git_bin = shutil.which("git") or "git"
    git_prefix = [git_bin, "-c", "safe.directory=*", f"--git-dir={shadow_p}", f"--work-tree={target_p}"]

    try:
        # 1. Identify files modified or untracked between current workspace and burst_hash
        diff_res = _run_git_cmd(git_prefix + ["diff", "--name-only", burst_hash], timeout=8)
        status_res = _run_git_cmd(git_prefix + ["status", "--porcelain"], timeout=8)

        files_to_backup: set[str] = set()
        if diff_res.returncode == 0 and diff_res.stdout.strip():
            files_to_backup.update(l.strip() for l in diff_res.stdout.splitlines() if l.strip())
        if status_res.returncode == 0 and status_res.stdout.strip():
            for line in status_res.stdout.splitlines():
                if len(line) > 3:
                    files_to_backup.add(line[3:].strip().strip('"'))

        # Backup current versions into trash/<project_slug>/rollback_<timestamp>/
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = eng_p / "trash" / project_slug / f"rollback_{ts}"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backed_up: list[str] = []

        for rel in files_to_backup:
            src_f = target_p / rel
            if src_f.is_file():
                dst_f = backup_dir / "files" / rel
                dst_f.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_f, dst_f)
                if rel != "manifest.json":
                    dst_direct = backup_dir / rel
                    dst_direct.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_f, dst_direct)
                backed_up.append(rel)

        manifest = {
            "trash_id": f"{project_slug}/rollback_{ts}",
            "project_name": project_slug,
            "scope": "rollback_backup",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+05:30"),
            "target_path": str(target_p),
            "burst_id": burst_id,
            "burst_hash": burst_hash,
            "files": backed_up,
            "details": {"burst_id": burst_id, "burst_hash": burst_hash, "files_count": len(backed_up), "files": backed_up},
        }
        (backup_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        # 2. Resilient Revert with timeout=8
        check_fault("during_rollback")
        chk = _run_git_cmd(git_prefix + ["checkout", "-f", burst_hash, "--", "."], timeout=8)
        if chk.returncode != 0:
            return {"success": False, "error": chk.stderr or chk.stdout or "Checkout failed"}

        _run_git_cmd(git_prefix + ["clean", "-fd", "-e", ".engine_sync.lock"], timeout=8)
        _run_git_cmd(git_prefix + ["reset", "--hard", burst_hash], timeout=8)

        logger.info("Restored workspace %s to burst %s (backup: %s)", target_p, burst_hash[:8], backup_dir.name)
        return {"success": True, "rolled_back_to": burst_hash, "trash_backup": str(backup_dir)}
    except subprocess.TimeoutExpired:
        logger.error("Git operation timed out during rollback of %s", project_slug)
        return {"success": False, "error": "Git operation timed out"}
    except Exception as exc:
        logger.exception("Rollback exception for %s", project_slug)
        return {"success": False, "error": str(exc)}


class ShadowRollbackMixin:
    """Mixin containing rollback and physical workspace restoration routines for ShadowGit."""

    def rollback_burst_commit(self, event_id: int, commit_hash: str | None = None) -> bool:
        if not getattr(self, "is_available", False) or not self.shadow_dir.exists():
            return False
        with self._lock:
            try:
                commit = commit_hash or self.get_commit_for_event(event_id)
                if not commit:
                    return False
                head_res = self._run_git("rev-parse", "HEAD")
                cur_head = head_res.stdout.strip() if head_res.returncode == 0 else ""
                has_parent = (self._run_git("rev-parse", "--verify", f"{commit}~1").returncode == 0)

                if cur_head.startswith(commit):
                    res = self._run_git("reset", "--hard", "HEAD~1") if has_parent else self._run_git("update-ref", "-d", "HEAD")
                    return res.returncode == 0
                if has_parent:
                    res = self._run_git("revert", "--no-edit", commit)
                    if res.returncode != 0:
                        self._run_git("revert", "--abort")
                        res = self._run_git("reset", "--hard", f"{commit}~1")
                    return res.returncode == 0
                return self._run_git("reset", "--hard", "HEAD~1").returncode == 0
            except Exception as exc:
                logger.error("Failed rollback commit event %d: %s", event_id, exc)
                return False

    def restore_workspace_to_commit(
        self,
        commit_hash: str,
        target_dir: Path | str | None = None,
        project_slug: str | None = None,
        engine_root: Path | str | None = None,
    ) -> bool:
        if not getattr(self, "is_available", False) or not self.shadow_dir.exists():
            return False
        work_tree = Path(target_dir).resolve() if target_dir else self.target_dir
        with self._lock:
            res = rollback_to_burst(
                target_path=work_tree,
                shadow_git_dir=self.shadow_dir,
                burst_hash=commit_hash,
                project_slug=project_slug or work_tree.name,
                engine_root=engine_root,
            )
            return bool(res.get("success"))

    def commit_snapshot(self, message: str = "Pre-rollback snapshot") -> str | None:
        """Create a pre-rollback snapshot commit of the current workspace."""
        if not getattr(self, "is_available", False) or not self.shadow_dir.exists():
            return None
        with self._lock:
            return commit_snapshot(self.shadow_dir, self.target_dir, message)

    def compute_rollback_plan(self, target_commit: str, snapshot_commit: str | None = None) -> dict[str, Any]:
        """Compute dry-run file plan without altering the filesystem."""
        if not getattr(self, "is_available", False) or not self.shadow_dir.exists():
            return {"target_commit": target_commit, "files_to_restore": [], "files_to_remove": []}
        with self._lock:
            return compute_rollback_plan(self.shadow_dir, self.target_dir, target_commit, snapshot_commit)

    def restore_files_non_destructive(self, plan: dict[str, Any], target_commit: str) -> bool:
        """Restore required files and prune added files without bare git clean -fd."""
        if not getattr(self, "is_available", False) or not self.shadow_dir.exists():
            return False
        with self._lock:
            return restore_files_non_destructive(self.shadow_dir, self.target_dir, plan, target_commit)


def commit_snapshot(
    shadow_git_dir: Path | str,
    target_path: Path | str,
    message: str = "Pre-rollback snapshot",
) -> str | None:
    """Stage all current workspace files and create a pre-rollback snapshot commit."""
    target_p = Path(target_path).resolve()
    shadow_p = Path(shadow_git_dir).resolve()
    git_bin = shutil.which("git") or "git"
    git_prefix = [git_bin, "-c", "safe.directory=*", f"--git-dir={shadow_p}", f"--work-tree={target_p}"]
    try:
        _run_git_cmd(git_prefix + ["add", "-A"], timeout=15)
        _run_git_cmd(git_prefix + ["commit", "-m", message, "--allow-empty"], timeout=15)
        rev_res = _run_git_cmd(git_prefix + ["rev-parse", "HEAD"], timeout=5)
        if rev_res.returncode == 0 and rev_res.stdout.strip():
            return rev_res.stdout.strip()
    except Exception as exc:
        logger.error("Error creating pre-rollback snapshot: %s", exc)
    return None


def compute_rollback_plan(
    shadow_git_dir: Path | str,
    target_path: Path | str,
    target_commit: str,
    snapshot_commit: str | None = None,
) -> dict[str, Any]:
    """Compute dry-run file plan comparing target_commit to snapshot_commit or HEAD."""
    target_p = Path(target_path).resolve()
    shadow_p = Path(shadow_git_dir).resolve()
    git_bin = shutil.which("git") or "git"
    git_prefix = [git_bin, "-c", "safe.directory=*", f"--git-dir={shadow_p}", f"--work-tree={target_p}"]

    source_ref = snapshot_commit or "HEAD"
    diff_res = _run_git_cmd(git_prefix + ["diff", "--name-status", target_commit, source_ref], timeout=15)

    files_to_restore: list[str] = []
    files_to_remove: list[str] = []
    ignored_names = {".engine_sync.lock", ".git", ".spd", "session.db", "worker.pid", "worker.status.json", "worker.log"}

    if diff_res.returncode == 0 and diff_res.stdout.strip():
        for line in diff_res.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            status = parts[0]
            rel_file = parts[-1].strip().strip('"')
            if any(rel_file.startswith(ign) or rel_file == ign for ign in ignored_names):
                continue
            if status.startswith("M") or status.startswith("D"):
                files_to_restore.append(rel_file)
            elif status.startswith("A"):
                files_to_remove.append(rel_file)
            elif status.startswith("R") and len(parts) >= 3:
                files_to_restore.append(parts[1].strip().strip('"'))
                files_to_remove.append(parts[2].strip().strip('"'))

    return {
        "target_commit": target_commit,
        "snapshot_commit": snapshot_commit or source_ref,
        "files_to_restore": sorted(set(files_to_restore)),
        "files_to_remove": sorted(set(files_to_remove)),
    }


def restore_files_non_destructive(
    shadow_git_dir: Path | str,
    target_path: Path | str,
    plan: dict[str, Any],
    target_commit: str,
) -> bool:
    """Restore required files without using bare git clean -fd."""
    target_p = Path(target_path).resolve()
    shadow_p = Path(shadow_git_dir).resolve()
    git_bin = shutil.which("git") or "git"
    git_prefix = [git_bin, "-c", "safe.directory=*", f"--git-dir={shadow_p}", f"--work-tree={target_p}"]

    try:
        # 1. Remove only files that were added after target_commit
        for rel_file in plan.get("files_to_remove", []):
            f_path = target_p / rel_file
            if f_path.is_file() or f_path.is_symlink():
                f_path.unlink(missing_ok=True)
                parent = f_path.parent
                while parent != target_p and parent.exists():
                    try:
                        if not any(parent.iterdir()):
                            parent.rmdir()
                            parent = parent.parent
                        else:
                            break
                    except OSError:
                        break

        # 2. Restore files that were modified or deleted
        to_restore = plan.get("files_to_restore", [])
        if to_restore:
            payload = "\x00".join(to_restore) + "\x00"
            res_chk = subprocess.run(
                git_prefix + ["checkout", target_commit, "--pathspec-from-file=-", "--pathspec-file-nul"],
                input=payload.encode("utf-8"),
                capture_output=True,
                text=False,
                timeout=30,
            )
            if res_chk.returncode != 0:
                logger.warning("Pathspec-from-file checkout failed: %s; falling back to chunked checkout", res_chk.stderr.decode("utf-8", errors="replace"))
                batch_size = 25
                for i in range(0, len(to_restore), batch_size):
                    chunk = to_restore[i : i + batch_size]
                    res_batch = _run_git_cmd(git_prefix + ["checkout", target_commit, "--", *chunk], timeout=20)
                    if res_batch.returncode != 0:
                        logger.error("Checkout chunk failed during restore: %s", res_batch.stderr)
                        return False

        # 3. Update HEAD to target_commit
        res_reset = _run_git_cmd(git_prefix + ["reset", "--soft", target_commit], timeout=10)
        if res_reset.returncode != 0:
            logger.error("Soft reset failed during restore: %s", res_reset.stderr)
            return False
        return True
    except Exception as exc:
        logger.error("Error executing non-destructive restore: %s", exc)
        return False
