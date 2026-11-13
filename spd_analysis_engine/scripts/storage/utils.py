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
import importlib.util as _ilu
import pathlib as _pl
from pathlib import Path

logger = logging.getLogger(__name__)

def _load_time_utils():
    _candidates = [
        _pl.Path(__file__).resolve().parent.parent / "core" / "time_utils.py",
        _pl.Path(__file__).resolve().parent.parent.parent / "scripts" / "core" / "time_utils.py",
    ]
    for _p in _candidates:
        if _p.exists():
            _spec = _ilu.spec_from_file_location("scripts.core.time_utils", _p)
            _mod = _ilu.module_from_spec(_spec)
            import sys as _sys
            _sys.modules.setdefault("scripts.core.time_utils", _mod)
            _sys.modules.setdefault("spd_analysis_engine.scripts.core.time_utils", _mod)
            _spec.loader.exec_module(_mod)
            return _mod
    return None

_time_utils_mod = _load_time_utils()
if _time_utils_mod is not None:
    utc_now_iso = _time_utils_mod.utc_now_iso
    parse_iso_timestamp = _time_utils_mod.parse_iso_timestamp
else:
    from datetime import datetime, timezone as _tz
    def utc_now_iso() -> str:  # type: ignore[misc]
        return datetime.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    def parse_iso_timestamp(ts):  # type: ignore[misc]
        return None

# Backward-compatibility alias
_utcnow_iso = utc_now_iso


def _slug(name: str) -> str:
    """
    Convert a project name to a filesystem-safe canonical slug.

    Replaces spaces and invalid characters with underscores, preserves hyphens,
    and lower-cases the result to eliminate casing collisions (e.g. SPCL vs spcl).
    """
    s = str(name).strip()
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", s).lower()


def resolve_project_identity(
    name_or_slug: str,
    target_path: str | Path | None = None,
    project_meta: dict[str, Any] | None = None,
) -> dict[str, str]:
    """
    Produce canonical project identity: project_id, slug, display_name, root_path.
    """
    raw_name = str(name_or_slug).strip()
    slug = _slug(raw_name)
    meta = project_meta or {}

    display_name = str(meta.get("project_name") or raw_name or slug).strip()
    root_path = ""
    cand_path = target_path or meta.get("target_path")
    if cand_path:
        try:
            root_path = str(Path(cand_path).resolve())
        except Exception:
            root_path = str(cand_path).strip()

    project_id = str(meta.get("project_id") or f"proj_{slug}").strip()

    return {
        "project_id": project_id,
        "slug": slug,
        "display_name": display_name,
        "root_path": root_path,
    }


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
