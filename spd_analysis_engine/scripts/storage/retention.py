"""
scripts/storage/retention.py
============================
Artifact retention policy, storage telemetry, and scheduled compaction
for the SPD Analysis Engine.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .utils import _slug
from .schema_migration import execute_incremental_vacuum

try:
    from spd_analysis_engine.scripts.trash_manager import _force_rmtree
except (ImportError, ModuleNotFoundError):
    from .utils import _remove_tree_force as _force_rmtree

logger = logging.getLogger(__name__)


def _dir_tree_size(dir_path: Path) -> int:
    """Return total bytes occupied by directory tree."""
    if not dir_path.exists():
        return 0
    total = 0
    try:
        for p in dir_path.rglob("*"):
            if p.is_file():
                try:
                    total += p.stat().st_size
                except Exception:
                    pass
    except Exception:
        pass
    return total


def get_project_artifact_sizes(engine_root: Path | str, name: str) -> dict[str, int]:
    """Calculate disk bytes for all project artifacts across tiers."""
    slug = _slug(name)
    root = Path(engine_root).resolve()
    curr = root / "current" / slug
    last = root / "last_run" / slug
    hist = root / "history" / slug

    sizes = {
        "session_db": 0,
        "wal": 0,
        "shadow_git": 0,
        "baseline": 0,
        "patches": 0,
        "logs": 0,
        "history": 0,
        "total": 0,
    }

    for base in (curr, last):
        if not base.exists():
            continue
        db_p = base / "session.db"
        if db_p.exists():
            sizes["session_db"] += db_p.stat().st_size
        wal_p = base / "session.db-wal"
        if wal_p.exists():
            sizes["wal"] += wal_p.stat().st_size
        sizes["shadow_git"] += _dir_tree_size(base / "shadow_git")
        sizes["baseline"] += _dir_tree_size(base / "baseline")
        sizes["patches"] += _dir_tree_size(base / "patches")
        for log_f in base.glob("worker.log*"):
            try:
                sizes["logs"] += log_f.stat().st_size
            except Exception:
                pass

    if hist.exists():
        sizes["history"] += _dir_tree_size(hist)

    sizes["total"] = sum(v for k, v in sizes.items() if k != "total")
    return sizes


def apply_artifact_retention_policy(
    engine_root: Path | str,
    name: str,
    max_history_runs: int = 10,
    max_trash_days: int = 30,
    keep_refs: list[str] | None = None,
) -> dict[str, Any]:
    """
    Enforce artifact retention policy:
    1. Prune oldest history/run_* archives beyond max_history_runs.
    2. Run shadow_git maintenance (repack + prune loose objects with anchor protection).
    3. Trigger SQLite incremental vacuum if session.db exists.
    """
    slug = _slug(name)
    root = Path(engine_root).resolve()
    actions_taken: list[str] = []
    freed_bytes = 0

    # 1. Prune excess history runs
    hist_dir = root / "history" / slug
    if hist_dir.exists():
        runs = sorted(
            [d for d in hist_dir.iterdir() if d.is_dir() and d.name.startswith("run_")],
            key=lambda p: p.stat().st_mtime,
        )
        if len(runs) > max_history_runs:
            excess = runs[:-max_history_runs]
            for old_run in excess:
                run_sz = _dir_tree_size(old_run)
                try:
                    _force_rmtree(old_run)
                    freed_bytes += run_sz
                    actions_taken.append(f"Pruned historical run archive: {old_run.name} ({run_sz} bytes)")
                except Exception as exc:
                    logger.debug("Failed to prune old run %s: %s", old_run, exc)

    # 2. Shadow Git maintenance
    curr_shadow = root / "current" / slug / "shadow_git"
    if curr_shadow.exists():
        try:
            from spd_analysis_engine.scripts.git.shadow_repo import ShadowGit
            sg = ShadowGit(shadow_dir=curr_shadow, target_dir=root / "current" / slug)
            res = sg.run_maintenance(keep_refs=keep_refs)
            if res.get("repacked"):
                actions_taken.append("Executed Shadow Git loose object repack and prune")
        except Exception as exc:
            logger.debug("Shadow Git maintenance skipped or failed: %s", exc)

    # 3. Incremental vacuum on session.db
    curr_db = root / "current" / slug / "session.db"
    freelist_remaining = 0
    if curr_db.exists():
        try:
            import sqlite3
            conn = sqlite3.connect(str(curr_db), timeout=5.0)
            freelist_remaining = execute_incremental_vacuum(conn, pages=200)
            conn.close()
            actions_taken.append(f"Executed incremental vacuum on session.db (freelist remaining: {freelist_remaining})")
        except Exception as exc:
            logger.debug("Incremental vacuum skipped: %s", exc)

    return {
        "project": name,
        "actions_taken": actions_taken,
        "freed_bytes": freed_bytes,
        "freelist_remaining": freelist_remaining,
    }
