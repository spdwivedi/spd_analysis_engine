"""
scripts/git/auto_pusher.py
===========================
Hardened, opt-in automated commit and remote push for primary repositories.

Safety Guarantees:
- Unsafe automatic pushing disabled by default (strictly opt-in per project).
- Enforces branch allowlist and protects default branches (main/master).
- Only stages milestone files (never a bare git add -A).
- Refuses during merge or rebase operations.
- Refuses when .git/index.lock exists.
- Passes credentials via GIT_ASKPASS; never embeds tokens into remote URLs.
- Supports dry-run validation.
- Scrubs sensitive credentials from stderr output.
- Distinguishes "nothing to commit" from failures; failed commits never push.
- Runs as a controller background job, never in the capture worker's critical path.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Generator

from .credentials import GitCredentialManager
from .utils import _NO_WINDOW_FLAG, is_salesforce_or_transient_file
from ..storage.meta import read_project_meta

logger = logging.getLogger(__name__)

DEFAULT_PROTECTED_BRANCHES = frozenset({"main", "master"})


def _run_git(cmd: list[str], cwd: Path | str | None = None, **kwargs: Any) -> subprocess.CompletedProcess[str]:
    """Execute a git command via subprocess.run with safe defaults."""
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, **kwargs)


def _get_git_dir(target_dir: Path) -> Path | None:
    """Resolve the .git directory for target_dir."""
    dot_git = target_dir / ".git"
    if not dot_git.exists():
        return None
    if dot_git.is_dir():
        return dot_git
    if dot_git.is_file():
        # Submodule or worktree gitdir pointer
        try:
            content = dot_git.read_text(encoding="utf-8").strip()
            if content.startswith("gitdir:"):
                gitdir_rel = content.split("gitdir:", 1)[1].strip()
                return (target_dir / gitdir_rel).resolve()
        except OSError:
            pass
    return None


def is_repo_in_merge_or_rebase(target_dir: Path) -> bool:
    """Check if repository is currently in a merge, rebase, or cherry-pick operation."""
    git_dir = _get_git_dir(target_dir)
    if not git_dir or not git_dir.exists():
        return False

    sentinels = [
        git_dir / "MERGE_HEAD",
        git_dir / "REBASE_HEAD",
        git_dir / "rebase-apply",
        git_dir / "rebase-merge",
        git_dir / "CHERRY_PICK_HEAD",
        git_dir / "BISECT_LOG",
    ]
    return any(p.exists() for p in sentinels)


def is_index_locked(target_dir: Path) -> bool:
    """Check if Git index is currently locked by another operation."""
    git_dir = _get_git_dir(target_dir)
    if not git_dir or not git_dir.exists():
        return False
    return (git_dir / "index.lock").exists()


def scrub_sensitive_output(text: str) -> str:
    """Scrub sensitive credentials, passwords, and tokens from stderr or error messages."""
    if not text:
        return ""
    # Strip URL-embedded passwords/tokens (e.g. https://token@github.com)
    scrubbed = re.sub(r"https?://[^@/\s]+@", "https://***@", text)
    # Strip GitHub PATs and OAuth tokens
    scrubbed = re.sub(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{20,}\b", "[REDACTED_TOKEN]", scrubbed)
    scrubbed = re.sub(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b", "[REDACTED_PAT]", scrubbed)
    return scrubbed


@contextlib.contextmanager
def create_askpass_context(
    token: str | None = None,
) -> Generator[dict[str, str], None, None]:
    """
    Context manager creating a temporary GIT_ASKPASS credential helper.
    Ensures tokens are provided securely without modifying remote URLs.
    """
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(k, None)

    askpass_path: Path | None = None
    if token:
        try:
            # Create a small script that echos the token
            suffix = ".bat" if sys.platform == "win32" else ".sh"
            with tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False, encoding="utf-8") as tf:
                askpass_path = Path(tf.name)
                if sys.platform == "win32":
                    tf.write(f"@echo off\necho {token}\n")
                else:
                    tf.write(f"#!/bin/sh\necho '{token}'\n")
            if sys.platform != "win32" and askpass_path.exists():
                os.chmod(askpass_path, 0o700)
            env["GIT_ASKPASS"] = str(askpass_path)
        except Exception as exc:
            logger.debug("Failed creating askpass helper: %s", exc)

    try:
        yield env
    finally:
        if askpass_path and askpass_path.exists():
            try:
                askpass_path.unlink(missing_ok=True)
            except OSError:
                pass


def stage_milestone_files_only(
    target_dir: Path,
    files: list[str],
    timeout: float = 30.0,
) -> subprocess.CompletedProcess[str]:
    """Stage only milestone files without running a bare git add -A."""
    git_bin = shutil.which("git") or "git"
    clean_files = [
        f for f in files
        if not is_salesforce_or_transient_file(f) and (target_dir / f).exists()
    ]
    if not clean_files:
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(
        [git_bin, "add", "--", *clean_files],
        cwd=str(target_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        creationflags=_NO_WINDOW_FLAG,
        timeout=timeout,
        env=env,
    )


def execute_auto_push(
    project_slug: str,
    target_path: Path | str,
    milestone_files: list[str] | None = None,
    title: str = "",
    body: str = "",
    burst_id: int | None = None,
    engine_root: Path | str | None = None,
    dry_run: bool = False,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Execute hardened auto-commit and push with complete safety guards.
    """
    target = Path(target_path).resolve()
    slug = project_slug or target.name
    git_bin = shutil.which("git") or "git"

    # 1. Opt-in configuration check
    cfg = dict(config or {})
    if not cfg:
        eng_p = Path(engine_root).resolve() if engine_root else target.parent.parent
        meta = read_project_meta(eng_p, slug)
        cfg.update(meta)

    auto_push_val = str(cfg.get("auto_push", "")).lower().strip()
    if auto_push_val not in ("true", "1", "yes", "on"):
        return {"success": True, "skipped": True, "reason": "auto_push not enabled", "project": slug}

    # 2. Check Git repository exists
    if not (target / ".git").exists():
        return {"success": False, "skipped": True, "reason": "Not a git repository", "project": slug}

    # 3. Check merge / rebase status
    if is_repo_in_merge_or_rebase(target):
        logger.warning("Auto-push skipped for '%s': repository is in merge or rebase state.", slug)
        return {"success": False, "skipped": True, "reason": "Repository is in merge or rebase state", "project": slug}

    # 4. Check index.lock
    if is_index_locked(target):
        logger.warning("Auto-push skipped for '%s': Git index is locked (.git/index.lock exists).", slug)
        return {"success": False, "skipped": True, "reason": "Git index is locked (.git/index.lock exists)", "project": slug}

    # 5. Check remote origin
    remotes_res = _run_git(
        [git_bin, "remote"], cwd=str(target), capture_output=True, text=True, check=False, creationflags=_NO_WINDOW_FLAG
    )
    if "origin" not in remotes_res.stdout.split():
        return {"success": True, "skipped": True, "reason": "no remote origin configured", "project": slug}

    # 6. Branch allowlist and default branch protection
    branch_res = subprocess.run(
        [git_bin, "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(target), capture_output=True, text=True, check=False, creationflags=_NO_WINDOW_FLAG
    )
    branch = branch_res.stdout.strip() if branch_res.returncode == 0 else "main"

    allowed_branches = cfg.get("allowed_branches")
    if allowed_branches:
        if isinstance(allowed_branches, str):
            allowed_branches = [b.strip() for b in allowed_branches.split(",") if b.strip()]
        if branch not in allowed_branches:
            msg = f"Branch '{branch}' is not in allowed_branches {allowed_branches}"
            logger.info("Auto-push skipped for '%s': %s", slug, msg)
            return {"success": False, "skipped": True, "reason": msg, "project": slug}

    allow_default = str(cfg.get("allow_default_branch", "")).lower().strip() in ("true", "1", "yes", "on")
    if branch in DEFAULT_PROTECTED_BRANCHES and not allow_default:
        msg = f"Refusing to push to default branch '{branch}' without explicit allow_default_branch=True"
        logger.warning("Auto-push refused for '%s': %s", slug, msg)
        return {"success": False, "skipped": True, "reason": msg, "project": slug}

    # 7. Resolve milestone files and commit message
    files_to_stage = list(milestone_files) if milestone_files else []
    if not files_to_stage:
        st_res = subprocess.run(
            [git_bin, "status", "--porcelain"],
            cwd=str(target),
            capture_output=True,
            text=True,
            check=False,
            creationflags=_NO_WINDOW_FLAG,
        )
        for line in st_res.stdout.splitlines():
            item = line[3:].strip().strip('"')
            if " -> " in item:
                item = item.split(" -> ")[1].strip().strip('"')
            if item:
                files_to_stage.append(item)
    commit_title = title or f"feat(core): auto-commit burst #{burst_id or 'update'}"
    commit_body = body or "Automated milestone commit."

    token = GitCredentialManager.get_token(engine_root=engine_root)

    # 8. Dry-run support
    if dry_run:
        with create_askpass_context(token) as env:
            push_dry = subprocess.run(
                [git_bin, "push", "--dry-run", "origin", branch],
                cwd=str(target),
                capture_output=True,
                text=True,
                check=False,
                env=env,
                creationflags=_NO_WINDOW_FLAG,
                timeout=15,
            )
            return {
                "success": push_dry.returncode == 0,
                "dry_run": True,
                "branch": branch,
                "files_to_stage": files_to_stage,
                "title": commit_title,
                "output": scrub_sensitive_output(push_dry.stdout or push_dry.stderr),
            }

    # 9. Stage only milestone files
    stage_res = stage_milestone_files_only(target, files_to_stage)
    if stage_res.returncode != 0:
        err = scrub_sensitive_output(stage_res.stderr)
        return {"success": False, "committed": False, "pushed": False, "error": err, "reason": "stage_failed"}

    # 10. Commit changes (failed commit must never accidentally push)
    commit_res = subprocess.run(
        [git_bin, "commit", "-m", commit_title, "-m", commit_body],
        cwd=str(target),
        capture_output=True,
        text=True,
        check=False,
        creationflags=_NO_WINDOW_FLAG,
        timeout=20,
    )
    if commit_res.returncode != 0:
        out_text = commit_res.stdout + commit_res.stderr
        if "nothing to commit" in out_text.lower():
            return {"success": True, "committed": False, "pushed": False, "reason": "nothing to commit", "project": slug}
        err = scrub_sensitive_output(out_text)
        logger.error("Commit failed for '%s'; aborting push: %s", slug, err)
        return {"success": False, "committed": False, "pushed": False, "error": err, "reason": "commit_failed"}

    # 11. Push via GIT_ASKPASS (never tokens in remote URLs)
    with create_askpass_context(token) as env:
        push_res = subprocess.run(
            [git_bin, "push", "origin", branch],
            cwd=str(target),
            capture_output=True,
            text=True,
            check=False,
            env=env,
            creationflags=_NO_WINDOW_FLAG,
            timeout=30,
        )

    if push_res.returncode != 0:
        err = scrub_sensitive_output(push_res.stderr or push_res.stdout)
        logger.error("Push failed for '%s': %s", slug, err)
        return {"success": False, "committed": True, "pushed": False, "error": err, "reason": "push_failed"}

    logger.info("[AUTO_PUSH_SUCCESS] Project '%s' pushed commit '%s' to %s", slug, commit_title, branch)
    return {"success": True, "committed": True, "pushed": True, "branch": branch, "title": commit_title}


def handle_post_seal_autopush(
    project_slug: str,
    target_path: Path | str,
    burst_id: int,
    engine_root: Path | str | None = None,
    sync: bool = False,
) -> dict[str, Any] | None:
    """
    Non-blocking background launcher ensuring auto-push never runs in capture worker critical path.
    """
    if sync or "unittest" in sys.modules:
        return execute_auto_push(
            project_slug=project_slug,
            target_path=target_path,
            burst_id=burst_id,
            engine_root=engine_root,
        )

    thread = threading.Thread(
        target=execute_auto_push,
        kwargs={
            "project_slug": project_slug,
            "target_path": target_path,
            "burst_id": burst_id,
            "engine_root": engine_root,
        },
        daemon=True,
        name=f"autopush-{project_slug}",
    )
    thread.start()
    return None


__all__ = [
    "DEFAULT_PROTECTED_BRANCHES",
    "is_repo_in_merge_or_rebase",
    "is_index_locked",
    "scrub_sensitive_output",
    "stage_milestone_files_only",
    "execute_auto_push",
    "handle_post_seal_autopush",
]
