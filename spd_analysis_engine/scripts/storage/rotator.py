"""
scripts/storage/rotator.py
==========================
StorageRotator: three-tier directory lifecycle manager for the SPD Analysis Engine.
"""
from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from .utils import _slug, _utcnow_iso, _remove_tree_force
from .meta import read_project_meta
from .history import consolidate_project_history
from .db import SessionDB
from .retention import (
    get_project_artifact_sizes,
    apply_artifact_retention_policy,
)

try:
    from spd_analysis_engine.scripts.trash_manager import _force_rmtree, _safe_move_tree
except (ImportError, ModuleNotFoundError):
    _force_rmtree = _remove_tree_force
    def _safe_move_tree(s: Any, d: Any, **kwargs: Any) -> bool:
        if Path(d).exists():
            _force_rmtree(d)
        shutil.move(str(s), str(d))
        return True

logger = logging.getLogger(__name__)


def rotate_project_to_history(name: str, engine_root: Path | str) -> bool:
    """Rotate last_run to history/run_<timestamp> and move current to last_run."""
    slug = _slug(name)
    root = Path(engine_root).resolve()
    curr_dir = root / "current" / slug
    last_run_dir = root / "last_run" / slug
    hist_dir = root / "history" / slug

    if not curr_dir.exists():
        return False

    curr_db = curr_dir / "session.db"
    if curr_db.exists():
        try:
            sdb = SessionDB(curr_db)
            sdb.checkpoint()
            sdb.close()
        except Exception as exc:
            logger.debug("Checkpoint failed during rotation: %s", exc)

    if last_run_dir.exists():
        hist_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        archive_dest = hist_dir / f"run_{ts}"
        logger.info("Moving last_run/%s -> history/%s/run_%s", slug, slug, ts)
        _safe_move_tree(last_run_dir, archive_dest)

    logger.info("Moving current/%s -> last_run/%s", slug, slug)
    _safe_move_tree(curr_dir, last_run_dir)
    logger.info("Rotation complete for '%s'", name)
    return True


class StorageRotator:
    """Manages the three-tier directory lifecycle (current, last_run, history)."""

    def __init__(self, engine_root: Path | str) -> None:
        self.root = Path(engine_root).resolve()
        self._current = self.root / "current"
        self._last_run = self.root / "last_run"
        self._history = self.root / "history"
        for d in (self._current, self._last_run, self._history):
            d.mkdir(parents=True, exist_ok=True)

    def _project_dir(self, name: str, tier: str) -> Path:
        return getattr(self, f"_{tier.replace('-', '_')}") / _slug(name)

    def _ensure_subdirs(self, project_dir: Path) -> None:
        for sub in ("patches", "baseline"):
            (project_dir / sub).mkdir(parents=True, exist_ok=True)

    def init_project(self, name: str, target_path: str) -> Path:
        """Initialise current/<slug>/ working tree for a new session."""
        slug = _slug(name)
        proj_dir = self._project_dir(name, "current")
        proj_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_subdirs(proj_dir)
        last_run_dir = self._project_dir(name, "last_run")

        consolidate_project_history(self.root, name)

        curr_db = proj_dir / "session.db"
        if not curr_db.exists() and (last_run_dir / "session.db").exists():
            try:
                shutil.copy2(last_run_dir / "session.db", curr_db)
            except Exception as exc:
                logger.warning("Failed to copy historical session.db: %s", exc)

        if (last_run_dir / "patches").exists():
            for p_file in (last_run_dir / "patches").glob("event_*.patch"):
                dest_p = proj_dir / "patches" / p_file.name
                if not dest_p.exists():
                    try:
                        shutil.copy2(p_file, dest_p)
                    except Exception:
                        pass

        if (last_run_dir / "shadow_git").exists() and not (proj_dir / "shadow_git").exists():
            try:
                shutil.copytree(last_run_dir / "shadow_git", proj_dir / "shadow_git")
            except Exception as exc:
                logger.debug("Could not copy shadow_git: %s", exc)

        existing_meta = read_project_meta(self.root, name)
        existing_meta.update({
            "project_name": name,
            "target_path": target_path,
            "init_time": _utcnow_iso(),
            "clean_exit": "false",
            "status": "active",
        })

        if not existing_meta.get("remote_url") or existing_meta.get("remote_url") in ("", "Not Linked", "none"):
            try:
                from spd_analysis_engine.scripts.git_shadow import discover_native_git_remote
                disc_url, disc_branch = discover_native_git_remote(target_path)
                if disc_url:
                    existing_meta["remote_url"] = disc_url
                    if not existing_meta.get("remote_branch"):
                        existing_meta["remote_branch"] = disc_branch
            except Exception:
                pass

        meta_file = proj_dir / "project.meta"
        lines = [f"{k}={v}" for k, v in existing_meta.items()]
        meta_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return proj_dir

    def archive_project(self, name: str) -> None:
        """Archive a completed project session into last_run and history."""
        slug = _slug(name)
        pid_file = self._current / slug / "worker.pid"
        if pid_file.exists():
            try:
                import json
                from spd_analysis_engine.scripts.controller.process import is_process_alive
                content = pid_file.read_text(encoding="utf-8").strip()
                pdata = json.loads(content) if content.startswith("{") else {"pid": int(content.splitlines()[0].strip())}
                disk_pid, ctime = int(pdata.get("pid", 0)), pdata.get("process_create_time")
                if disk_pid > 0 and is_process_alive(disk_pid, ctime):
                    logger.warning("Refusing to archive '%s': active worker PID %s is running", name, disk_pid)
                    return
            except Exception:
                pass
        rotate_project_to_history(name, self.root)

    def list_all(self) -> dict[str, dict[str, Any]]:
        """Return a nested summary of all known projects across all tiers."""
        slugs: set[str] = set()
        for tier_dir in (self._current, self._last_run, self._history):
            if tier_dir.exists():
                slugs.update(d.name for d in tier_dir.iterdir() if d.is_dir())

        result: dict[str, dict[str, Any]] = {}
        for slug in sorted(slugs):
            history_runs: list[str] = []
            history_slug_dir = self._history / slug
            if history_slug_dir.exists():
                history_runs = sorted(
                    d.name for d in history_slug_dir.iterdir() if d.is_dir() and d.name.startswith("run_")
                )
            meta = read_project_meta(self.root, slug)
            result[slug] = {
                "current": (self._current / slug).exists(),
                "last_run": (self._last_run / slug).exists(),
                "history_runs": history_runs,
                "target_path": meta.get("target_path", ""),
            }
        return result

    def current_path(self, name: str) -> Path:
        return self._project_dir(name, "current")

    def last_run_path(self, name: str) -> Path:
        return self._project_dir(name, "last_run")

    def get_artifact_usage(self, name: str) -> dict[str, int]:
        return get_project_artifact_sizes(self.root, name)

    def apply_retention_policy(self, name: str, **kwargs: Any) -> dict[str, Any]:
        return apply_artifact_retention_policy(self.root, name, **kwargs)
