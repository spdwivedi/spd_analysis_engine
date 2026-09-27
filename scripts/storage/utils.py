"""
scripts/storage/utils.py
========================
Utility helpers for the storage package:
  - _slug(name)
  - _utcnow_iso()
  - _remove_tree_force(target_dir)
  - purge_test_artifacts(engine_root)
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import stat
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    """Return the current UTC time as an ISO-8601 string (no microseconds)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slug(name: str) -> str:
    """
    Convert a project name to a filesystem-safe slug.

    Replaces spaces and special characters with underscores and lower-cases
    the result so paths are predictable across platforms.

    Examples
    --------
    >>> _slug("My Cool Project!")
    'my_cool_project_'
    """
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", name).lower()


def _remove_tree_force(target_dir: Path) -> bool:
    """Robustly remove directory tree on Windows handling read-only git/db files."""

    def _on_exc(func, path, exc):
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except Exception:
            pass

    try:
        try:
            shutil.rmtree(target_dir, onexc=_on_exc)
        except TypeError:
            shutil.rmtree(target_dir, onerror=lambda f, p, e: _on_exc(f, p, e[1]))
        return not target_dir.exists()
    except Exception:
        return False


def purge_test_artifacts(engine_root: Path | str) -> list[str]:
    """
    Permanently delete phase5_e2e_test and lingering mock_* directories from
    current/, last_run/, history/, and root mock_workspace/.
    """
    engine_p = Path(engine_root).resolve()
    purged: list[str] = []

    # 1. Clean from tiers
    for tier in ("current", "last_run", "history"):
        tier_dir = engine_p / tier
        if not tier_dir.exists():
            continue
        for child in tier_dir.iterdir():
            if child.is_dir() and ("phase5_e2e_test" in child.name or child.name.startswith("mock_")):
                if _remove_tree_force(child):
                    purged.append(str(child))
                    logger.info("Purged test artifact: %s", child)

    # 2. Clean root mock_workspace
    mock_ws = engine_p / "mock_workspace"
    if mock_ws.exists():
        if _remove_tree_force(mock_ws):
            purged.append(str(mock_ws))
            logger.info("Purged mock_workspace directory: %s", mock_ws)

    return purged
