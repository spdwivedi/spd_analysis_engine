"""
scripts/storage/project_storage.py
==================================
Durable project-oriented storage model manager.

Target model:
data/
  projects/
    <slug>/
      session.db
      shadow_git/
      logs/
      project.config.json

Legacy tiers (current/, last_run/, history/) remain intact as compatibility sources/views.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .utils import _slug, _utcnow_iso

logger = logging.getLogger(__name__)


def get_durable_root(engine_root: Path | str) -> Path:
    """Return the base path to the durable data/projects directory."""
    return Path(engine_root).resolve() / "data" / "projects"


def get_project_dir(engine_root: Path | str, project_name: str) -> Path:
    """Return the absolute path to data/projects/<slug>/."""
    slug = _slug(project_name)
    return get_durable_root(engine_root) / slug


def ensure_project_layout(engine_root: Path | str, project_name: str) -> Path:
    """
    Ensure the directory structure for data/projects/<slug>/ exists,
    including logs/ sub-directory.
    """
    proj_dir = get_project_dir(engine_root, project_name)
    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / "logs").mkdir(parents=True, exist_ok=True)
    return proj_dir


def resolve_project_db(engine_root: Path | str, project_name: str) -> Path | None:
    """
    Resolve session.db location with durable storage taking precedence,
    falling back to legacy compatibility tiers (current, last_run, history).
    """
    slug = _slug(project_name)
    engine_p = Path(engine_root).resolve()

    # 1. Primary: Durable model
    durable_db = get_project_dir(engine_p, slug) / "session.db"
    if durable_db.is_file():
        return durable_db

    # 2. Compatibility: current/
    curr_db = engine_p / "current" / slug / "session.db"
    if curr_db.is_file():
        return curr_db

    # 3. Compatibility: last_run/
    last_db = engine_p / "last_run" / slug / "session.db"
    if last_db.is_file():
        return last_db

    # 4. Compatibility: history/
    hist_dir = engine_p / "history" / slug
    if hist_dir.is_dir():
        runs = sorted(d for d in hist_dir.iterdir() if d.is_dir() and d.name.startswith("run_"))
        for run_dir in reversed(runs):
            run_db = run_dir / "session.db"
            if run_db.is_file():
                return run_db

    return None


def resolve_project_git(engine_root: Path | str, project_name: str) -> Path | None:
    """
    Resolve shadow_git directory path across durable and legacy tiers.
    """
    slug = _slug(project_name)
    engine_p = Path(engine_root).resolve()

    # 1. Primary: Durable model
    durable_git = get_project_dir(engine_p, slug) / "shadow_git"
    if durable_git.is_dir():
        return durable_git

    # 2. Compatibility: current/
    curr_git = engine_p / "current" / slug / "shadow_git"
    if curr_git.is_dir():
        return curr_git

    # 3. Compatibility: last_run/
    last_git = engine_p / "last_run" / slug / "shadow_git"
    if last_git.is_dir():
        return last_git

    return None


def read_project_config(engine_root: Path | str, project_name: str) -> dict[str, Any]:
    """
    Read project.config.json from durable storage, or synthesize from legacy project.meta.
    Checks data/projects/<slug>/, then current/<slug>/, then legacy project.meta.
    """
    slug = _slug(project_name)
    engine_p = Path(engine_root).resolve()

    # 1. Primary: data/projects/<slug>/project.config.json
    cfg_file = get_project_dir(engine_p, slug) / "project.config.json"
    if cfg_file.is_file():
        try:
            return json.loads(cfg_file.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Could not read project.config.json for %s: %s", slug, exc)

    # 2. Compatibility: current/<slug>/project.config.json
    curr_cfg = engine_p / "current" / slug / "project.config.json"
    if curr_cfg.is_file():
        try:
            return json.loads(curr_cfg.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Could not read current project.config.json for %s: %s", slug, exc)

    # 3. Fallback to legacy project.meta synthesis
    from .meta import read_project_meta
    legacy_meta = read_project_meta(engine_p, slug)
    cloud_enabled = bool(legacy_meta.get("cloud_ai_enabled") or legacy_meta.get("cloud_consent"))
    try:
        db_val = float(legacy_meta.get("debounce", legacy_meta.get("debounce_window", 3.5)))
    except (ValueError, TypeError):
        db_val = 3.5
    return {
        "slug": slug,
        "display_name": legacy_meta.get("project_name") or slug,
        "target_path": legacy_meta.get("target_path", ""),
        "created_at": legacy_meta.get("init_time") or _utcnow_iso(),
        "ide_profile": legacy_meta.get("ide_profile", "antigravity"),
        "remote_url": legacy_meta.get("remote_url", ""),
        "remote_branch": legacy_meta.get("remote_branch", "main"),
        "cloud_ai_enabled": cloud_enabled,
        "cloud_consent": cloud_enabled,
        "debounce": db_val,
        "debounce_s": db_val,
        "debounce_window": db_val,
        "legacy_imported": True,
    }


def write_project_config(engine_root: Path | str, project_name: str, config: dict[str, Any]) -> Path:
    """
    Write project.config.json atomically to data/projects/<slug>/.
    Also mirrors to current/<slug>/project.config.json if current/<slug> exists.
    """
    slug = _slug(project_name)
    proj_dir = ensure_project_layout(engine_root, slug)
    cfg_file = proj_dir / "project.config.json"
    tmp_file = proj_dir / "project.config.json.tmp"

    tmp_file.write_text(json.dumps(config, indent=2), encoding="utf-8")
    tmp_file.replace(cfg_file)

    curr_dir = Path(engine_root).resolve() / "current" / slug
    if curr_dir.is_dir():
        try:
            curr_cfg = curr_dir / "project.config.json"
            curr_tmp = curr_dir / "project.config.json.tmp"
            curr_tmp.write_text(json.dumps(config, indent=2), encoding="utf-8")
            curr_tmp.replace(curr_cfg)
        except Exception:
            pass

    return cfg_file


def list_all_project_slugs(engine_root: Path | str) -> list[str]:
    """
    List all known project slugs across durable storage and legacy tiers.
    """
    engine_p = Path(engine_root).resolve()
    slugs: set[str] = set()

    # Durable projects
    durable_root = get_durable_root(engine_p)
    if durable_root.is_dir():
        slugs.update(d.name for d in durable_root.iterdir() if d.is_dir() and not d.name.startswith("."))

    # Legacy tiers
    for tier in ("current", "last_run", "history"):
        tier_p = engine_p / tier
        if tier_p.is_dir():
            slugs.update(d.name for d in tier_p.iterdir() if d.is_dir() and not d.name.startswith("."))

    return sorted(slugs)
