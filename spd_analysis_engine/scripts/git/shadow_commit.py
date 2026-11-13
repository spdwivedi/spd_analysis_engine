"""
scripts/git/shadow_commit.py
============================
Authoritative burst micro-commit pipeline, diff-tree patch extractor,
and durable recovery engine for ShadowGit.

Key Guarantees:
- Avoids Windows command line limits via git add --pathspec-from-file=- --pathspec-file-nul.
- Strictly executes: git add -> git diff --cached --quiet -> git commit -> git diff-tree.
- Never uses --allow-empty.
- Employs one recursive diff-tree operation per burst instead of per-file subprocesses.
- Sets GIT_TERMINAL_PROMPT=0 and enforces explicit timeouts on all Git calls.
- Enforces size policy with truncation.
- Git failure never loses already-persisted SessionDB events.
- Persists enough state to retry unfinished Git work across crashes/restarts.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.changeset import (
    ChangeSet,
    DEFAULT_MAX_PATCH_BYTES,
    DEFAULT_MAX_TEXT_BYTES,
    truncate_patch_content,
)
from ..core.faults import check_fault
from .utils import _NO_WINDOW_FLAG, is_salesforce_or_transient_file

logger = logging.getLogger(__name__)

DEFAULT_GIT_TIMEOUT_S: float = 30.0
DEFAULT_DIFF_TIMEOUT_S: float = 15.0


class GitState:
    """Enumeration of lifecycle states for burst Git operations."""
    PENDING = "pending"
    RUNNING = "running"
    COMMITTED = "committed"
    SKIPPED_NO_CHANGE = "skipped_no_change"
    SKIPPED_OVERLOAD = "skipped_overload"
    FAILED = "failed"


@dataclass
class GitBurstResult:
    """Outcome of committing a ChangeSet to Shadow Git."""
    state: str
    commit_hash: str | None = None
    patch: str | None = None
    is_truncated: bool = False
    patch_size: int = 0
    error: str | None = None

    @property
    def is_success(self) -> bool:
        return self.state in (GitState.COMMITTED, GitState.SKIPPED_NO_CHANGE)


def get_shadow_git_env() -> dict[str, str]:
    """Return environment variables safe for automated Shadow Git operations."""
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)
    env.pop("GIT_INDEX_FILE", None)
    return env


def configure_shadow_repository(
    git_cmd: str,
    shadow_dir: Path,
    target_dir: Path,
    timeout: float = 10.0,
) -> None:
    """Configure Shadow Git with hardened settings for automated micro-versioning."""
    configs = [
        ("gc.auto", "0"), ("core.longpaths", "true"), ("core.quotepath", "false"),
        ("core.autocrlf", "false"), ("core.fsmonitor", "false"), ("commit.gpgsign", "false"),
        ("advice.addIgnoredFile", "false"), ("user.name", "SPD Shadow Engine"), ("user.email", "spd-shadow@local"),
    ]
    env = get_shadow_git_env()
    for key, val in configs:
        try:
            subprocess.run(
                [git_cmd, f"--git-dir={shadow_dir}", f"--work-tree={target_dir}", "config", key, val],
                capture_output=True,
                timeout=timeout,
                env=env,
                creationflags=_NO_WINDOW_FLAG,
                check=False,
            )
        except Exception as exc:
            logger.debug("Failed setting git config %s: %s", key, exc)


def filter_gitignored_paths(
    git_cmd: str,
    shadow_dir: Path,
    target_dir: Path,
    paths: list[str],
    timeout: float = DEFAULT_GIT_TIMEOUT_S,
) -> list[str]:
    """
    Filter out paths that are ignored according to Git's native .gitignore rules.
    Runs 'git check-ignore -z --stdin' targeting the shadow git repository.
    """
    if not paths:
        return []
    payload = b"\0".join(p.replace("\\", "/").encode("utf-8") for p in paths) + b"\0"
    cmd = [git_cmd, f"--git-dir={shadow_dir}", f"--work-tree={target_dir}", "check-ignore", "-z", "--stdin"]
    try:
        res = subprocess.run(
            cmd,
            input=payload,
            capture_output=True,
            timeout=timeout,
            env=get_shadow_git_env(),
            creationflags=_NO_WINDOW_FLAG,
            check=False,
        )
        if res.returncode in (0, 1):
            raw_ignored = res.stdout.split(b"\0")
            ignored_set = {p.decode("utf-8", errors="replace") for p in raw_ignored if p}
            if ignored_set:
                paths = [p for p in paths if p.replace("\\", "/") not in ignored_set]
    except Exception as exc:
        logger.debug("Failed running git check-ignore: %s", exc)
    return [p for p in paths if not (p.lower().endswith((".pem", ".key", ".pfx")) or Path(p).name.lower().startswith((".env", "id_rsa")))]


def stage_paths_nul(
    git_cmd: str,
    shadow_dir: Path,
    target_dir: Path,
    paths: list[str],
    timeout: float = DEFAULT_GIT_TIMEOUT_S,
) -> subprocess.CompletedProcess[bytes]:
    """
    Stage paths using --pathspec-from-file=- and --pathspec-file-nul via stdin.
    Completely avoids Windows command-line argument length overflow for 1,000+ files.
    Filters out transient Salesforce files and Git-ignored paths.
    """
    filtered = [p for p in paths if not is_salesforce_or_transient_file(p)]
    if not filtered:
        # Nothing to stage
        return subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")

    if "." in filtered:
        # Root workspace staging: git add . natively respects .gitignore
        cmd = [git_cmd, f"--git-dir={shadow_dir}", f"--work-tree={target_dir}", "add", "."]
        return subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            env=get_shadow_git_env(),
            creationflags=_NO_WINDOW_FLAG,
            check=False,
        )

    filtered = filter_gitignored_paths(git_cmd, shadow_dir, target_dir, filtered, timeout=timeout)
    if not filtered:
        return subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")

    payload = b"\0".join(p.replace("\\", "/").encode("utf-8") for p in filtered) + b"\0"
    cmd = [
        git_cmd, f"--git-dir={shadow_dir}", f"--work-tree={target_dir}",
        "add", "--pathspec-from-file=-", "--pathspec-file-nul",
    ]
    res = subprocess.run(
        cmd,
        input=payload,
        capture_output=True,
        timeout=timeout,
        env=get_shadow_git_env(),
        creationflags=_NO_WINDOW_FLAG,
        check=False,
    )
    if res.returncode != 0 and b"ignored by one of your .gitignore files" in res.stderr:
        err_str = res.stderr.decode("utf-8", errors="replace")
        offending = set()
        capture = False
        for line in err_str.splitlines():
            line_s = line.strip()
            if "ignored by one of your .gitignore files" in line_s:
                capture = True
                continue
            if line_s.startswith("hint:") or not line_s:
                capture = False
                continue
            if capture:
                offending.add(line_s.replace("\\", "/"))
        if offending:
            remaining = [p for p in filtered if p.replace("\\", "/") not in offending]
            if not remaining:
                return subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")
            rem_payload = b"\0".join(p.replace("\\", "/").encode("utf-8") for p in remaining) + b"\0"
            return subprocess.run(
                cmd,
                input=rem_payload,
                capture_output=True,
                timeout=timeout,
                env=get_shadow_git_env(),
                creationflags=_NO_WINDOW_FLAG,
                check=False,
            )
    return res


def recover_stale_index_lock(shadow_dir: Path | str, max_age_s: float = 60.0) -> bool:
    """Recover stale index.lock in shadow_dir if older than max_age_s and no active git process holds it."""
    lock_path = Path(shadow_dir) / "index.lock"
    if not lock_path.exists():
        return False
    try:
        age = time.time() - lock_path.stat().st_mtime
        if age < max_age_s:
            return False
        is_git_running = False
        try:
            import psutil
            shadow_str = str(shadow_dir).lower()
            for p in psutil.process_iter(["name", "cmdline"]):
                try:
                    if "git" in (p.info.get("name") or "").lower():
                        cmdline = p.info.get("cmdline") or []
                        if any(shadow_str in str(arg).lower() for arg in cmdline):
                            is_git_running = True
                            break
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except ImportError:
            pass
        if is_git_running:
            logger.warning("Active git process detected for %s; preserving index.lock", shadow_dir)
            return False
        lock_path.unlink(missing_ok=True)
        logger.warning("Recovered stale index.lock (age=%.1fs) in %s", age, shadow_dir)
        return True
    except Exception as exc:
        logger.debug("Error checking or recovering stale index.lock in %s: %s", shadow_dir, exc)
        return False


def execute_burst_commit(
    git_cmd: str,
    shadow_dir: Path,
    target_dir: Path,
    changeset: ChangeSet,
    summary: str = "",
    first_file: str | None = None,
    max_patch_bytes: int = DEFAULT_MAX_PATCH_BYTES,
    timeout: float = DEFAULT_GIT_TIMEOUT_S,
) -> GitBurstResult:
    """
    Execute the authoritative 4-step Git workflow for an effective burst:
    1. git add (--pathspec-from-file)
    2. git diff --cached --quiet
    3. git commit (NO --allow-empty)
    4. git diff-tree (one recursive diff-tree operation)
    """
    recover_stale_index_lock(shadow_dir)
    if not changeset.is_effective:
        return GitBurstResult(state=GitState.SKIPPED_NO_CHANGE)

    # 1. Staging
    stage_targets = changeset.effective_files or changeset.files
    try:
        res_add = stage_paths_nul(git_cmd, shadow_dir, target_dir, stage_targets, timeout=timeout)
        if res_add.returncode != 0:
            err = res_add.stderr.decode("utf-8", errors="replace")
            logger.error("Git add failed for burst %s: %s", changeset.burst_uid, err)
            return GitBurstResult(state=GitState.FAILED, error=err)
    except subprocess.TimeoutExpired:
        logger.error("Git add timed out after %.1fs for burst %s", timeout, changeset.burst_uid)
        return GitBurstResult(state=GitState.FAILED, error=f"Git add timed out after {timeout}s")
    except Exception as exc:
        return GitBurstResult(state=GitState.FAILED, error=str(exc))

    # 2. Check staged changes (git diff --cached --quiet)
    diff_cmd = [
        git_cmd, f"--git-dir={shadow_dir}", f"--work-tree={target_dir}", "diff", "--cached", "--quiet",
    ]
    try:
        res_diff = subprocess.run(
            diff_cmd,
            capture_output=True,
            timeout=DEFAULT_DIFF_TIMEOUT_S,
            env=get_shadow_git_env(),
            creationflags=_NO_WINDOW_FLAG,
            check=False,
        )
        if res_diff.returncode == 0:
            # Staged changes are identical to HEAD -> No-op burst!
            logger.info("No staged changes for burst %s; skipping commit.", changeset.burst_uid)
            return GitBurstResult(state=GitState.SKIPPED_NO_CHANGE)
        elif res_diff.returncode != 1:
            err = res_diff.stderr.decode("utf-8", errors="replace")
            logger.warning("Git diff --cached error: %s", err)
            return GitBurstResult(state=GitState.FAILED, error=err)
    except subprocess.TimeoutExpired:
        return GitBurstResult(state=GitState.FAILED, error=f"Git diff --cached timed out after {DEFAULT_DIFF_TIMEOUT_S}s")
    except Exception as exc:
        return GitBurstResult(state=GitState.FAILED, error=str(exc))

    # 3. Commit (strictly NO --allow-empty)
    check_fault("before_git_commit")
    entry_tag = f"[{first_file}] " if first_file else ""
    commit_msg = f"Burst #{changeset.event_id or changeset.sequence}: {entry_tag}{summary or 'Code changes'}"
    commit_cmd = [
        git_cmd, f"--git-dir={shadow_dir}", f"--work-tree={target_dir}", "commit", "-m", commit_msg,
    ]
    try:
        check_fault("during_git_commit")
        res_commit = subprocess.run(
            commit_cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=get_shadow_git_env(),
            creationflags=_NO_WINDOW_FLAG,
            check=False,
        )
        if res_commit.returncode != 0:
            err = res_commit.stderr or res_commit.stdout
            logger.error("Git commit failed for burst %s: %s", changeset.burst_uid, err)
            return GitBurstResult(state=GitState.FAILED, error=err)
    except subprocess.TimeoutExpired:
        return GitBurstResult(state=GitState.FAILED, error=f"Git commit timed out after {timeout}s")
    except Exception as exc:
        return GitBurstResult(state=GitState.FAILED, error=str(exc))

    # Retrieve commit hash
    try:
        rev_cmd = [git_cmd, f"--git-dir={shadow_dir}", f"--work-tree={target_dir}", "rev-parse", "HEAD"]
        rev_res = subprocess.run(
            rev_cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5.0,
            env=get_shadow_git_env(),
            creationflags=_NO_WINDOW_FLAG,
            check=False,
        )
        commit_hash = rev_res.stdout.strip() if rev_res.returncode == 0 else None
    except Exception:
        commit_hash = None

    if commit_hash:
        check_fault("after_git_commit")

    # 4. Recursive diff-tree operation to obtain the entire burst patch
    patch_content: str | None = None
    is_truncated = False
    patch_size = 0
    if commit_hash:
        try:
            tree_cmd = [
                git_cmd, f"--git-dir={shadow_dir}", f"--work-tree={target_dir}",
                "diff-tree", "-p", "-r", "--root", "--no-commit-id", commit_hash,
            ]
            tree_res = subprocess.run(
                tree_cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env=get_shadow_git_env(),
                creationflags=_NO_WINDOW_FLAG,
                check=False,
            )
            if tree_res.returncode == 0:
                raw_patch = tree_res.stdout
                patch_content, is_truncated, patch_size = truncate_patch_content(
                    raw_patch, max_patch_bytes=max_patch_bytes
                )
        except Exception as exc:
            logger.debug("Failed to extract diff-tree patch for %s: %s", commit_hash, exc)

    return GitBurstResult(
        state=GitState.COMMITTED,
        commit_hash=commit_hash,
        patch=patch_content,
        is_truncated=is_truncated,
        patch_size=patch_size,
    )


# ---------------------------------------------------------------------------
# Durable Recovery Helpers
# ---------------------------------------------------------------------------

def record_git_op_state(
    conn: Any,
    event_id: int,
    burst_uid: str,
    status: str,
    files: list[str],
    effective_files: list[str],
) -> int | None:
    """Record initial git operation into git_ops table for crash recovery."""
    if conn is None:
        return None
    if hasattr(conn, "record_git_op_state"):
        return conn.record_git_op_state(event_id, burst_uid, status, files, effective_files)
    try:
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO git_ops (event_id, burst_uid, status, files_json, effective_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (event_id, burst_uid, status, json.dumps(files), json.dumps(effective_files), now, now),
        )
        op_id = int(cur.lastrowid)
        conn.commit()
        return op_id
    except Exception as exc:
        logger.debug("Error recording git_op state: %s", exc)
        return None


def update_git_op_state(
    conn: Any,
    op_id: int | None,
    status: str,
    commit_hash: str | None = None,
    error_message: str | None = None,
) -> None:
    """Update git operation status in git_ops table."""
    if conn is None or op_id is None:
        return
    if hasattr(conn, "update_git_op_state"):
        conn.update_git_op_state(op_id, status, commit_hash, error_message)
        return
    try:
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        cur = conn.cursor()
        cur.execute(
            "UPDATE git_ops SET status = ?, commit_hash = ?, error_message = ?, updated_at = ? WHERE id = ?",
            (status, commit_hash, error_message, now, op_id),
        )
        conn.commit()
    except Exception as exc:
        logger.debug("Error updating git_op state: %s", exc)


def recover_unfinished_git_ops(
    conn: Any,
    shadow_git: Any,
) -> list[dict[str, Any]]:
    """
    Discover and retry any pending or running Git operations from previous crashes.
    """
    if conn is None or shadow_git is None:
        return []
    recovered: list[dict[str, Any]] = []
    try:
        sql = "SELECT id, event_id, burst_uid, status, files_json, effective_json, attempts FROM git_ops WHERE status IN ('pending', 'running') ORDER BY id ASC"
        if hasattr(conn, "_execute"):
            rows = conn._execute(sql).fetchall()
        else:
            cur = conn.cursor()
            cur.execute(sql)
            rows = cur.fetchall()
        for row in rows:
            op_id, event_id, burst_uid, status, files_json, eff_json, attempts = row
            files = json.loads(files_json)
            eff_files = json.loads(eff_json)

            cs = ChangeSet(burst_uid=burst_uid, event_id=event_id, files=files, effective_files=eff_files)
            result = shadow_git.commit_changeset(cs, summary="Crash recovery replay")
            update_git_op_state(conn, op_id, result.state, result.commit_hash, result.error)
            recovered.append({
                "op_id": op_id, "event_id": event_id, "burst_uid": burst_uid,
                "state": result.state, "commit_hash": result.commit_hash,
            })
    except Exception as exc:
        logger.error("Error during Git crash recovery: %s", exc)
    return recovered
