"""
scripts/storage/rollback.py
===========================
Burst rollback functionality for the storage package.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

try:
    from .utils import _slug
    from .meta import read_project_meta
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.storage.utils import _slug
        from spd_analysis_engine.scripts.storage.meta import read_project_meta
    except (ImportError, ModuleNotFoundError):
        from scripts.storage.utils import _slug
        from scripts.storage.meta import read_project_meta

logger = logging.getLogger(__name__)


def rollback_burst(
    engine_root_or_project_name: Path | str,
    project_name_or_target_event_id: str | int,
    target_event_id_or_target_dir: int | Path | str | None = None,
    target_dir: Path | str | None = None,
) -> dict[str, Any]:
    """
    Rollback and discard an atomic prompt burst:
    1. Set file watcher suppression lock (.engine_sync.lock) in target workspace.
    2. Retrieve event details from session.db.
    3. Physically restore workspace files to the commit prior to this burst via ShadowGit.restore_workspace_to_commit().
    4. Move the event row(s), patch row(s), and .patch file(s) into trash/ under scope 'selective_bursts' via TrashManager.
    5. Clean up any associated events with id >= target_event_id in session.db.
    6. Cooldown 1.5s and release .engine_sync.lock in finally block.
    """
    # Disambiguate arguments
    if isinstance(project_name_or_target_event_id, int):
        project_name = str(engine_root_or_project_name)
        event_id = project_name_or_target_event_id
        target_dir_param = target_event_id_or_target_dir
        engine_root = None
    else:
        engine_root = engine_root_or_project_name
        project_name = str(project_name_or_target_event_id)
        event_id = int(target_event_id_or_target_dir) if target_event_id_or_target_dir is not None else 0
        target_dir_param = target_dir

    slug = _slug(project_name)

    if engine_root:
        engine_p = Path(engine_root).resolve()
    else:
        # Default engine root discovery: cwd or parent of scripts
        cwd = Path.cwd().resolve()
        if (cwd / "current").exists() or (cwd / "spd_analysis_engine").exists():
            engine_p = cwd / "spd_analysis_engine" if (cwd / "spd_analysis_engine").exists() else cwd
        else:
            engine_p = Path(__file__).resolve().parent.parent.parent

    # Find project directory across current and last_run
    proj_dir: Path | None = None
    for tier in ("current", "last_run"):
        cand = engine_p / tier / slug
        if cand.exists() and (cand / "session.db").exists():
            proj_dir = cand
            break

    if proj_dir is None:
        return {"success": False, "message": f"Project '{slug}' not found or session.db missing."}

    meta = read_project_meta(engine_p, slug)
    target_path_str = str(target_dir_param) if target_dir_param else meta.get("target_path", "")
    target_workspace = Path(target_path_str).resolve() if target_path_str else None

    # 1. Set file watcher suppression lock in workspace
    lock_file: Path | None = None
    if target_workspace and target_workspace.exists():
        try:
            lock_file = target_workspace / ".engine_sync.lock"
            lock_file.write_text("ROLLBACK\n", encoding="utf-8")
            logger.debug("Set watcher suppression lock at %s", lock_file)
        except Exception as exc:
            logger.debug("Could not write .engine_sync.lock: %s", exc)

    db_path = proj_dir / "session.db"
    rolled_back_git = False
    trash_res: dict[str, Any] = {}

    try:
        conn = sqlite3.connect(db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute("SELECT * FROM events WHERE id = ?", (event_id,))
        ev_row = cur.fetchone()
        if not ev_row:
            conn.close()
            return {"success": False, "message": f"Burst event #{event_id} not found in session.db."}

        ev_dict = dict(ev_row)
        session_id = ev_dict.get("session_id")

        # 2. Shadow Git physical rollback
        shadow_dir = proj_dir / "shadow_git"
        burst_commit = ev_dict.get("commit_hash")

        if shadow_dir.exists() and target_workspace and target_workspace.exists():
            try:
                try:
                    from spd_analysis_engine.scripts.git_shadow import ShadowGit
                except (ImportError, ModuleNotFoundError):
                    from scripts.git_shadow import ShadowGit

                sg = ShadowGit(shadow_dir=shadow_dir, target_dir=target_workspace)
                if not burst_commit:
                    burst_commit = sg.get_commit_for_event(event_id)

                if not burst_commit:
                    cur.execute(
                        "SELECT summary FROM events WHERE event_type = 'GIT' AND summary LIKE ? ORDER BY id DESC LIMIT 1",
                        (f"%Burst #{event_id}%",),
                    )
                    git_ev_row = cur.fetchone()
                    if git_ev_row and git_ev_row["summary"]:
                        m = re.search(r"\b([a-f0-9]{7,40})\b", git_ev_row["summary"])
                        if m:
                            burst_commit = m.group(1)

                if burst_commit:
                    # Check if commit has a parent
                    parent_check = sg._run_git("rev-parse", "--verify", f"{burst_commit}~1")
                    if parent_check.returncode == 0 and parent_check.stdout.strip():
                        parent_commit = parent_check.stdout.strip()
                        rolled_back_git = sg.restore_workspace_to_commit(parent_commit, target_dir=target_workspace)
                    else:
                        # Root commit: remove files added in this commit and reset HEAD
                        ls_res = sg._run_git("ls-tree", "-r", "--name-only", burst_commit)
                        if ls_res.returncode == 0 and ls_res.stdout.strip():
                            for rel_file in ls_res.stdout.strip().splitlines():
                                rel_file = rel_file.strip()
                                if not rel_file:
                                    continue
                                f_p = target_workspace / rel_file
                                if f_p.is_file() or f_p.is_symlink():
                                    try:
                                        f_p.unlink()
                                    except Exception:
                                        pass
                        sg._run_git("update-ref", "-d", "HEAD")
                        rolled_back_git = True
                else:
                    rolled_back_git = sg.rollback_burst_commit(event_id)
            except Exception as exc:
                logger.warning("ShadowGit restore failed for event #%d: %s", event_id, exc)

        # 3. Collect all burst event IDs to rollback (id >= event_id in same session)
        if session_id:
            cur.execute("SELECT id FROM events WHERE session_id = ? AND id >= ?", (session_id, event_id))
        else:
            cur.execute("SELECT id FROM events WHERE id >= ?", (event_id,))
        subsequent_ids = [r["id"] for r in cur.fetchall()]
        burst_ids_to_trash = list(subsequent_ids)
        if event_id not in burst_ids_to_trash:
            burst_ids_to_trash.append(event_id)

        # Check for associated GIT micro-commit events
        for bid in list(burst_ids_to_trash):
            cur.execute(
                "SELECT id FROM events WHERE event_type = 'GIT' AND summary LIKE ?",
                (f"%Burst #{bid}%",),
            )
            for gr in cur.fetchall():
                gid = gr["id"]
                if gid not in burst_ids_to_trash:
                    burst_ids_to_trash.append(gid)

        conn.close()

        # 4. Use TrashManager to move events + patches into trash/ under selective_bursts
        try:
            try:
                from spd_analysis_engine.scripts.trash_manager import TrashManager, SCOPE_SELECTIVE_BURSTS
            except (ImportError, ModuleNotFoundError):
                from scripts.trash_manager import TrashManager, SCOPE_SELECTIVE_BURSTS
            tm = TrashManager(engine_p)
            trash_res = tm.move_to_trash(slug, SCOPE_SELECTIVE_BURSTS, burst_ids=burst_ids_to_trash)
        except Exception as exc:
            logger.error("TrashManager error during rollback of event #%d: %s", event_id, exc)
            trash_res = {"success": False, "error": str(exc)}

        logger.info("Burst #%d rolled back successfully for project %s (git=%s)", event_id, slug, rolled_back_git)

        # 5. Clean up SQLite, remove any remaining .patch file, and prune empty inactive sessions
        try:
            conn = sqlite3.connect(db_path, timeout=10.0)
            cur = conn.cursor()
            placeholders = ",".join("?" for _ in burst_ids_to_trash)
            cur.execute(f"DELETE FROM patches WHERE event_id IN ({placeholders})", burst_ids_to_trash)
            cur.execute(f"DELETE FROM events WHERE id IN ({placeholders})", burst_ids_to_trash)
            conn.commit()

            if session_id:
                cur.execute(
                    "SELECT COUNT(*) FROM events WHERE session_id = ? AND (event_type = 'EDIT' OR event_type IS NULL OR event_type = '')",
                    (session_id,),
                )
                count_row = cur.fetchone()
                rem_bursts = count_row[0] if count_row else 0

                cur.execute("SELECT status FROM sessions WHERE id = ?", (session_id,))
                s_row = cur.fetchone()
                is_active = (s_row and s_row[0] == "ACTIVE")
                if rem_bursts == 0 and not is_active:
                    cur.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
                    conn.commit()
                    logger.info("Pruned empty session #%d after rollback", session_id)
                    for c_cand in (
                        engine_p / "ai_data" / "cache" / slug / f"session_{session_id}.json",
                        engine_p / "ai_data" / "cache" / f"session_{session_id}.json",
                    ):
                        if c_cand.exists():
                            try:
                                c_cand.unlink(missing_ok=True)
                            except Exception:
                                pass
            conn.close()
        except Exception as exc:
            logger.warning("Error finalizing rollback DB state for event #%d: %s", event_id, exc)

        # Clean up local patch files and AI cache files
        for eid in burst_ids_to_trash:
            for patches_tier in ("current", "last_run"):
                pf = engine_p / patches_tier / slug / "patches" / f"event_{eid}.patch"
                if pf.exists():
                    try:
                        pf.unlink(missing_ok=True)
                    except Exception:
                        pass

        # Prune AI cache files for rolled-back events
        cache_base = engine_p / "ai_data" / "cache"
        slug_cache = cache_base / slug
        prune_dirs = [cache_base, slug_cache, slug_cache / "files", slug_cache / "overviews"]
        if session_id:
            prune_dirs.append(slug_cache / f"session_{session_id}")
            prune_dirs.append(slug_cache / f"session_{session_id}" / "files")

        for eid in burst_ids_to_trash:
            patterns = [
                f"event_{eid}.json",
                f"file_analysis_*_{eid}_*.json",
                f"{eid}_*.json",
                f"burst_overview_*_{eid}_*.json",
                f"overview_{eid}_*.json",
                f"burst_{eid}_*.json",
            ]
            for pd in prune_dirs:
                if pd.exists():
                    for pat in patterns:
                        for cf in pd.glob(pat):
                            try:
                                cf.unlink(missing_ok=True)
                            except Exception:
                                pass

        # 6. Quiet cooldown before releasing lock
        time.sleep(1.5)

    finally:
        # Release watcher suppression lock
        if lock_file and lock_file.exists():
            try:
                lock_file.unlink(missing_ok=True)
                logger.debug("Released watcher suppression lock at %s", lock_file)
            except Exception:
                pass

    return {
        "success": True,
        "event_id": event_id,
        "project": slug,
        "rolled_back_git": rolled_back_git,
        "trash_res": trash_res,
        "message": f"Burst #{event_id} successfully rolled back and moved to Trash.",
    }
