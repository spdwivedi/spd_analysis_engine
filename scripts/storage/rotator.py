"""
scripts/storage/rotator.py
==========================
StorageRotator class: three-tier directory lifecycle manager for the SPD Analysis Engine.
"""
from __future__ import annotations

import logging
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from .utils import _slug, _utcnow_iso, _remove_tree_force
    from .meta import read_project_meta
    from .history import consolidate_project_history
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.storage.utils import _slug, _utcnow_iso, _remove_tree_force
        from spd_analysis_engine.scripts.storage.meta import read_project_meta
        from spd_analysis_engine.scripts.storage.history import consolidate_project_history
    except (ImportError, ModuleNotFoundError):
        from scripts.storage.utils import _slug, _utcnow_iso, _remove_tree_force
        from scripts.storage.meta import read_project_meta
        from scripts.storage.history import consolidate_project_history

# _force_rmtree and safe move inline imports from trash_manager:
try:
    from scripts.trash_manager import _force_rmtree, _safe_move_tree, _safe_rmtree
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.trash_manager import _force_rmtree, _safe_move_tree, _safe_rmtree
    except (ImportError, ModuleNotFoundError):
        try:
            from scripts.trash.fs_ops import _force_rmtree, _safe_move_tree, _safe_rmtree
        except (ImportError, ModuleNotFoundError):
            _force_rmtree = _remove_tree_force
            _safe_rmtree = _remove_tree_force
            def _safe_move_tree(s, d, **kwargs):
                if Path(d).exists():
                    _force_rmtree(d)
                shutil.move(str(s), str(d))
                return True

logger = logging.getLogger(__name__)


class StorageRotator:
    """
    Manages the three-tier directory lifecycle for all projects.

    Tier layout (all relative to ``engine_root``)
    -----------------------------------------------
    ``current/<slug>/``
        Live working tree while the worker is running.  Contains:

        * ``session.db``   – the live SQLite database
        * ``patches/``     – staged patch files written by the worker
        * ``baseline/``    – snapshot of monitored files at session start
        * ``worker.pid``   – PID file written by project_worker.py

    ``last_run/<slug>/``
        Snapshot of the most recently *completed* session.  Replaced each
        time the same project finishes a new session.

    ``history/<slug>/run_<YYYYMMDD_HHMMSS>/``
        Permanent archive.  The previous ``last_run`` snapshot is moved here
        before ``current`` becomes the new ``last_run``.

    Parameters
    ----------
    engine_root : Path | str
        Root directory of the SPD Analysis Engine installation
        (the ``spd_analysis_engine/`` folder).

    Thread / process safety
    -----------------------
    Each worker process owns exactly one project slug.  The orchestrator
    serialises ``archive_project`` calls so no two processes race on the
    same slug directory.
    """

    def __init__(self, engine_root: Path | str) -> None:
        self.root = Path(engine_root).resolve()
        self._current = self.root / "current"
        self._last_run = self.root / "last_run"
        self._history = self.root / "history"

        # Ensure top-level tier directories exist
        for d in (self._current, self._last_run, self._history):
            d.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _project_dir(self, name: str, tier: str) -> Path:
        """Return the path for *name* under *tier* (``current``/``last_run``/etc.)."""
        return getattr(self, f"_{tier.replace('-', '_')}") / _slug(name)

    def _ensure_subdirs(self, project_dir: Path) -> None:
        """Create the expected sub-folders inside a project directory."""
        for sub in ("patches", "baseline"):
            (project_dir / sub).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def init_project(self, name: str, target_path: str) -> Path:
        """
        Initialise the ``current/<slug>/`` working tree for a new session.

        Retains historical session.db, patches, and git metadata from prior runs
        so that project history is cumulative across restarts and resumes.

        Parameters
        ----------
        name        : Project name (converted to a slug internally).
        target_path : The directory being monitored.

        Returns
        -------
        Path – Absolute path to ``current/<slug>/``.
        """
        slug = _slug(name)
        proj_dir = self._project_dir(name, "current")
        proj_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_subdirs(proj_dir)

        last_run_dir = self._project_dir(name, "last_run")

        # 1. Consolidate any historical runs if present
        consolidate_project_history(self.root, name)

        # 2. Retain cumulative session.db if not already in current/
        curr_db = proj_dir / "session.db"
        if not curr_db.exists() and (last_run_dir / "session.db").exists():
            try:
                shutil.copy2(last_run_dir / "session.db", curr_db)
                logger.info("Restored historical session.db into %s", proj_dir)
            except Exception as exc:
                logger.warning("Failed to copy historical session.db: %s", exc)

        # 3. Retain patch files
        if (last_run_dir / "patches").exists():
            for p_file in (last_run_dir / "patches").glob("event_*.patch"):
                dest_p = proj_dir / "patches" / p_file.name
                if not dest_p.exists():
                    try:
                        shutil.copy2(p_file, dest_p)
                    except Exception:
                        pass

        # 4. Retain shadow_git repository if present
        if (last_run_dir / "shadow_git").exists() and not (proj_dir / "shadow_git").exists():
            try:
                shutil.copytree(last_run_dir / "shadow_git", proj_dir / "shadow_git")
            except Exception as exc:
                logger.debug("Could not copy shadow_git: %s", exc)

        # 5. Retain and update project.meta (preserving remote_url, etc.)
        existing_meta = read_project_meta(self.root, name)
        existing_meta["project_name"] = name
        existing_meta["target_path"] = target_path
        existing_meta["init_time"] = _utcnow_iso()
        existing_meta["clean_exit"] = "false"
        existing_meta["status"] = "active"

        # Check native git auto-discovery
        if not existing_meta.get("remote_url") or existing_meta.get("remote_url") in ("", "Not Linked", "none"):
            try:
                try:
                    from spd_analysis_engine.scripts.git_shadow import discover_native_git_remote
                except (ImportError, ModuleNotFoundError):
                    from scripts.git_shadow import discover_native_git_remote
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

        logger.info("Initialised current dir for '%s' at %s (cumulative history preserved)", name, proj_dir)
        return proj_dir

    def archive_project(self, name: str) -> None:
        """
        Archive a completed project session.

        Steps
        -----
        1. If ``last_run/<slug>`` exists → move it to
           ``history/<slug>/run_<YYYYMMDD_HHMMSS>/``.
        2. Move ``current/<slug>`` → ``last_run/<slug>``.

        This is intentionally idempotent: if ``current/<slug>`` does not
        exist (e.g. worker was never started) the call is a no-op.

        Parameters
        ----------
        name : Project name (same slug rules as :meth:`init_project`).
        """
        slug = _slug(name)
        current_dir = self._current / slug
        last_run_dir = self._last_run / slug
        history_project_dir = self._history / slug

        if not current_dir.exists():
            logger.warning(
                "archive_project('%s'): current dir %s does not exist; skipping",
                name, current_dir,
            )
            return

        # Step 1 – rotate the old last_run into history
        if last_run_dir.exists():
            history_project_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            archive_dest = history_project_dir / f"run_{ts}"
            logger.info(
                "Moving last_run/%s → history/%s/run_%s", slug, slug, ts
            )
            _safe_move_tree(last_run_dir, archive_dest)

        # Step 2 – move current into last_run
        logger.info("Moving current/%s → last_run/%s", slug, slug)
        _safe_move_tree(current_dir, last_run_dir)
        logger.info("Archive complete for '%s'", name)

    def list_all(self) -> dict[str, dict[str, Any]]:
        """
        Return a nested summary of all known projects across all tiers.

        Returns
        -------
        dict
            ``{ project_slug: { "current": bool, "last_run": bool,
                                "history_runs": [str, ...] } }``
        """
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
                    d.name
                    for d in history_slug_dir.iterdir()
                    if d.is_dir() and d.name.startswith("run_")
                )
            result[slug] = {
                "current": (self._current / slug).exists(),
                "last_run": (self._last_run / slug).exists(),
                "history_runs": history_runs,
            }
        return result

    def current_path(self, name: str) -> Path:
        """Return the ``current/<slug>/`` path for *name* (may not exist)."""
        return self._project_dir(name, "current")

    def last_run_path(self, name: str) -> Path:
        """Return the ``last_run/<slug>/`` path for *name* (may not exist)."""
        return self._project_dir(name, "last_run")
