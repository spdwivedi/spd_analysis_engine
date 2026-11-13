"""
scripts/storage/rollback.py
===========================
Non-destructive rollback, dry-run planning, pre-rollback snapshotting,
and undoable restoration for the storage package.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from .utils import _slug
from .meta import read_project_meta
def check_fault(name: str) -> None:
    from ..core.faults import check_fault as _cf
    _cf(name)

logger = logging.getLogger(__name__)


def _resolve_project_paths(
    engine_root: Path | str | None,
    project_name: str,
    target_dir: Path | str | None = None,
) -> tuple[Path, str, Path | None, Path | None]:
    """Resolve engine root, project slug, project dir, and workspace path."""
    slug = _slug(project_name)
    if engine_root:
        engine_p = Path(engine_root).resolve()
    else:
        cwd = Path.cwd().resolve()
        if (cwd / "current").exists() or (cwd / "spd_analysis_engine").exists():
            engine_p = cwd / "spd_analysis_engine" if (cwd / "spd_analysis_engine").exists() else cwd
        else:
            engine_p = Path(__file__).resolve().parent.parent.parent

    proj_dir: Path | None = None
    for tier in ("current", "last_run", "projects"):
        cand = engine_p / tier / slug
        if cand.exists() and (cand / "session.db").exists():
            proj_dir = cand
            break

    meta = read_project_meta(engine_p, slug)
    target_path_str = str(target_dir) if target_dir else meta.get("target_path", "")
    target_workspace = Path(target_path_str).resolve() if target_path_str else None
    return engine_p, slug, proj_dir, target_workspace


def execute_non_destructive_rollback(
    engine_root: Path | str | None,
    project_name: str,
    target_event_id: int,
    target_dir: Path | str | None = None,
    dry_run: bool = False,
    confirmed: bool = True,
) -> dict[str, Any]:
    """
    Authoritative non-destructive rollback sequence:
    PAUSE -> seal pending burst -> pre-rollback snapshot commit ->
    compute dry-run file plan -> user confirmation -> restore required files ->
    mark affected historical events as rolled_back/superseded -> write ROLLBACK event -> resume.
    """
    engine_p, slug, proj_dir, target_workspace = _resolve_project_paths(engine_root, project_name, target_dir)
    if proj_dir is None:
        return {"success": False, "error": f"Project '{slug}' not found or session.db missing."}

    has_workspace = bool(target_workspace and target_workspace.exists())

    # 1. PAUSE & set suppression lock
    lock_file = (target_workspace / ".engine_sync.lock") if has_workspace else None
    if lock_file:
        try:
            lock_file.write_text("ROLLBACK\n", encoding="utf-8")
        except Exception as exc:
            logger.debug("Could not write .engine_sync.lock: %s", exc)

    try:
        from ..controller.control_channel import send_control_command
        cmd_res = send_control_command(engine_p, slug, "PAUSE", timeout_s=1.0)
        logger.debug("Rollback PAUSE sent for %s: %s", slug, cmd_res.get("ok"))
    except Exception as exc:
        logger.debug("Could not send PAUSE to worker %s: %s", slug, exc)

    conn: sqlite3.Connection | None = None
    try:
        # 2. Seal pending burst
        try:
            from ..controller.control_channel import send_control_command
            cmd_res = send_control_command(engine_p, slug, "SEAL", timeout_s=1.0)
            logger.debug("Rollback SEAL sent for %s: %s", slug, cmd_res.get("ok"))
            time.sleep(0.1)
        except Exception as exc:
            logger.debug("Could not send SEAL to worker %s: %s", slug, exc)

        # 3. Pre-rollback snapshot commit
        shadow_dir = proj_dir / "shadow_git"
        from ..git.shadow_repo import ShadowGit
        sg = ShadowGit(shadow_dir=shadow_dir, target_dir=target_workspace if has_workspace else proj_dir)
        has_shadow_git = bool(has_workspace and sg.is_available and shadow_dir.exists())

        snapshot_commit = None
        if has_shadow_git:
            snapshot_commit = sg.commit_snapshot(f"Pre-rollback snapshot before rolling back to event #{target_event_id}")
            if not snapshot_commit:
                rev_res = sg._run_git("rev-parse", "HEAD")
                snapshot_commit = rev_res.stdout.strip() if rev_res.returncode == 0 else "HEAD"

        # 4. Compute dry-run file plan & identify target commit
        db_path = proj_dir / "session.db"
        conn = sqlite3.connect(db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute("SELECT id, session_id, commit_hash, event_type, summary, first_file_touched FROM events WHERE id = ?", (target_event_id,))
        ev_row = cur.fetchone()
        if not ev_row:
            return {"success": False, "error": f"Target event #{target_event_id} not found in session.db."}

        session_id = ev_row["session_id"]
        target_commit = ev_row["commit_hash"]
        if has_shadow_git and not target_commit:
            target_commit = sg.get_commit_for_event(target_event_id)
        if not target_commit:
            try:
                cur.execute("SELECT commit_hash FROM git_ops WHERE event_id = ? AND commit_hash IS NOT NULL", (target_event_id,))
                g_row = cur.fetchone()
                if g_row:
                    target_commit = g_row[0]
            except Exception:
                pass

        if has_shadow_git and not target_commit:
            return {"success": False, "error": f"Could not resolve Git commit for target event #{target_event_id}."}

        # Collect affected historical events (events created AFTER target_event_id)
        cur.execute(
            "SELECT id FROM events WHERE session_id = ? AND id > ? AND (rollback_state IS NULL OR rollback_state != 'rolled_back')",
            (session_id, target_event_id),
        )
        affected_ids = [r["id"] for r in cur.fetchall()]

        # Compute file plan via ShadowGit
        if has_shadow_git and target_commit:
            raw_plan = sg.compute_rollback_plan(target_commit, snapshot_commit)
        else:
            raw_plan = {"files_to_restore": [], "files_to_remove": []}
        plan = {
            "target_event_id": target_event_id,
            "target_commit": target_commit or "",
            "snapshot_commit": snapshot_commit or "",
            "files_to_restore": raw_plan.get("files_to_restore", []),
            "files_to_remove": raw_plan.get("files_to_remove", []),
            "affected_event_ids": affected_ids,
        }

        # 5. User confirmation check
        if dry_run or not confirmed:
            return {
                "success": True,
                "dry_run": True,
                "confirmed": False,
                "plan": plan,
                "message": "Dry-run file plan computed; awaiting confirmation.",
            }

        # 6. Restore required files (no bare git clean -fd)
        if has_shadow_git and target_commit:
            check_fault("during_rollback")
            restored = sg.restore_files_non_destructive(raw_plan, target_commit)
            if not restored:
                return {"success": False, "error": "Failed to restore files to target commit."}

        # 7. Mark affected historical events as rolled_back / superseded
        rollback_id = f"rb_{uuid.uuid4().hex[:8]}"
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        if affected_ids:
            ph = ",".join("?" for _ in affected_ids)
            cur.execute(
                f"UPDATE events SET rollback_state = 'rolled_back', is_rolled_back = 1, superseded_by = ? WHERE id IN ({ph})",
                [rollback_id, *affected_ids],
            )

        # 8. Write ROLLBACK event and record rollbacks row
        safe_tgt_commit = target_commit or "00000000"
        safe_snp_commit = snapshot_commit or "00000000"
        cur.execute(
            "INSERT INTO rollbacks (rollback_id, target_event_id, target_commit, snapshot_commit, status, affected_events, file_plan_json, created_at) "
            "VALUES (?, ?, ?, ?, 'COMPLETED', ?, ?, ?)",
            (rollback_id, target_event_id, safe_tgt_commit, safe_snp_commit, json.dumps(affected_ids), json.dumps(plan), now_iso),
        )

        first_file = ev_row["first_file_touched"] or (plan["files_to_restore"][0] if plan["files_to_restore"] else "workspace")
        summary_text = f"Rolled back to Event #{target_event_id}" + (f" (commit {target_commit[:7]})" if target_commit else "")
        cur.execute(
            "INSERT INTO events (session_id, event_type, timestamp, first_file_touched, summary, commit_hash, rollback_state) "
            "VALUES (?, 'ROLLBACK', ?, ?, ?, ?, 'active')",
            (session_id, now_iso, first_file, summary_text, target_commit or None),
        )
        conn.commit()

        return {
            "success": True,
            "rollback_id": rollback_id,
            "target_event_id": target_event_id,
            "event_id": target_event_id,
            "project": slug,
            "target_commit": target_commit,
            "snapshot_commit": snapshot_commit,
            "restored_files": plan["files_to_restore"],
            "removed_files": plan["files_to_remove"],
            "affected_event_ids": affected_ids,
            "message": f"Successfully rolled back to Event #{target_event_id} (non-destructive).",
        }
    except Exception as exc:
        logger.exception("Error during execute_non_destructive_rollback for %s", slug)
        return {"success": False, "error": str(exc)}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        if lock_file and lock_file.exists():
            try:
                lock_file.unlink(missing_ok=True)
            except Exception:
                pass
        try:
            from ..controller.control_channel import send_control_command
            cmd_res = send_control_command(engine_p, slug, "RESUME", timeout_s=1.0)
            logger.debug("Rollback RESUME sent for %s: %s", slug, cmd_res.get("ok"))
        except Exception as exc:
            logger.debug("Could not send RESUME to worker %s: %s", slug, exc)


def undo_rollback(
    engine_root: Path | str | None,
    project_name: str,
    rollback_id: str | None = None,
    target_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Undo a completed rollback, restoring the workspace to its pre-rollback snapshot commit."""
    engine_p, slug, proj_dir, target_workspace = _resolve_project_paths(engine_root, project_name, target_dir)
    if proj_dir is None or target_workspace is None or not target_workspace.exists():
        return {"success": False, "error": f"Project '{slug}' not found or workspace missing."}

    lock_file = target_workspace / ".engine_sync.lock"
    try:
        lock_file.write_text("ROLLBACK_UNDO\n", encoding="utf-8")
    except Exception:
        pass

    try:
        from ..controller.control_channel import send_control_command
        cmd_res = send_control_command(engine_p, slug, "PAUSE", timeout_s=1.0)
        logger.debug("Undo rollback PAUSE sent for %s: %s", slug, cmd_res.get("ok"))
    except Exception as exc:
        logger.debug("Could not send PAUSE to worker %s: %s", slug, exc)

    conn: sqlite3.Connection | None = None
    try:
        db_path = proj_dir / "session.db"
        conn = sqlite3.connect(db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        if rollback_id:
            cur.execute("SELECT * FROM rollbacks WHERE rollback_id = ? AND status = 'COMPLETED'", (rollback_id,))
        else:
            cur.execute("SELECT * FROM rollbacks WHERE status = 'COMPLETED' ORDER BY id DESC LIMIT 1")
        rb_row = cur.fetchone()
        if not rb_row:
            return {"success": False, "error": "No completed rollback found to undo."}

        rb_dict = dict(rb_row)
        snapshot_commit = rb_dict["snapshot_commit"]
        affected_ids = json.loads(rb_dict.get("affected_events") or "[]")
        rb_rec_id = rb_dict["id"]
        actual_rb_id = rb_dict["rollback_id"]

        shadow_dir = proj_dir / "shadow_git"
        from ..git.shadow_repo import ShadowGit
        sg = ShadowGit(shadow_dir=shadow_dir, target_dir=target_workspace)

        plan = sg.compute_rollback_plan(target_commit=snapshot_commit)
        restored = sg.restore_files_non_destructive(plan, snapshot_commit)
        if not restored:
            return {"success": False, "error": f"Failed to restore workspace to snapshot {snapshot_commit[:7]}"}

        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        # Reactivate affected events
        if affected_ids:
            ph = ",".join("?" for _ in affected_ids)
            cur.execute(
                f"UPDATE events SET rollback_state = 'active', is_rolled_back = 0, superseded_by = NULL WHERE id IN ({ph})",
                affected_ids,
            )

        cur.execute("UPDATE rollbacks SET status = 'UNDONE', undone_at = ? WHERE id = ?", (now_iso, rb_rec_id))

        cur.execute("SELECT id FROM sessions ORDER BY id DESC LIMIT 1")
        s_row = cur.fetchone()
        session_id = s_row[0] if s_row else 1
        cur.execute(
            "INSERT INTO events (session_id, event_type, timestamp, summary, commit_hash, rollback_state) "
            "VALUES (?, 'ROLLBACK', ?, ?, ?, 'active')",
            (session_id, now_iso, f"Undo rollback #{actual_rb_id}: restored to snapshot {snapshot_commit[:7]}", snapshot_commit),
        )
        conn.commit()

        return {
            "success": True,
            "rollback_id": actual_rb_id,
            "restored_to_snapshot": snapshot_commit,
            "reactivated_events": affected_ids,
            "message": f"Successfully undid rollback #{actual_rb_id}; restored to snapshot {snapshot_commit[:7]}.",
        }
    except Exception as exc:
        logger.exception("Error during undo_rollback for %s", slug)
        return {"success": False, "error": str(exc)}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        if lock_file and lock_file.exists():
            try:
                lock_file.unlink(missing_ok=True)
            except Exception:
                pass
        try:
            from ..controller.control_channel import send_control_command
            cmd_res = send_control_command(engine_p, slug, "RESUME", timeout_s=1.0)
            logger.debug("Undo rollback RESUME sent for %s: %s", slug, cmd_res.get("ok"))
        except Exception as exc:
            logger.debug("Could not send RESUME to worker %s: %s", slug, exc)


def rollback_burst(
    engine_root_or_project_name: Path | str,
    project_name_or_target_event_id: str | int,
    target_event_id_or_target_dir: int | Path | str | None = None,
    target_dir: Path | str | None = None,
    non_destructive: bool = False,
    dry_run: bool = False,
    confirmed: bool = True,
) -> dict[str, Any]:
    """Rollback helper supporting both non-destructive mode and legacy trash migration."""
    # Disambiguate arguments
    if isinstance(project_name_or_target_event_id, int):
        pname = str(engine_root_or_project_name)
        eid = project_name_or_target_event_id
        tdir = target_event_id_or_target_dir
        eroot = None
    else:
        eroot = engine_root_or_project_name
        pname = str(project_name_or_target_event_id)
        eid = int(target_event_id_or_target_dir) if target_event_id_or_target_dir is not None else 0
        tdir = target_dir

    if non_destructive:
        return execute_non_destructive_rollback(
            engine_root=eroot,
            project_name=pname,
            target_event_id=eid,
            target_dir=tdir,
            dry_run=dry_run,
            confirmed=confirmed,
        )

    # Legacy trash migration behavior
    engine_p, slug, proj_dir, target_workspace = _resolve_project_paths(eroot, pname, tdir)
    if proj_dir is None:
        return {"success": False, "message": f"Project '{slug}' not found or session.db missing."}

    lock_file = target_workspace / ".engine_sync.lock" if target_workspace and target_workspace.exists() else None
    if lock_file:
        try:
            lock_file.write_text("ROLLBACK\n", encoding="utf-8")
        except Exception:
            pass

    db_path = proj_dir / "session.db"
    rolled_back_git = False
    trash_res: dict[str, Any] = {}
    conn: sqlite3.Connection | None = None

    try:
        conn = sqlite3.connect(db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM events WHERE id = ?", (eid,))
        ev_row = cur.fetchone()
        if not ev_row:
            return {"success": False, "message": f"Burst event #{eid} not found in session.db."}

        ev_dict = dict(ev_row)
        session_id = ev_dict.get("session_id")
        shadow_dir = proj_dir / "shadow_git"
        burst_commit = ev_dict.get("commit_hash")

        if shadow_dir.exists() and target_workspace and target_workspace.exists():
            try:
                from ..git_shadow import ShadowGit
                sg = ShadowGit(shadow_dir=shadow_dir, target_dir=target_workspace)
                if not burst_commit:
                    burst_commit = sg.get_commit_for_event(eid)
                if burst_commit:
                    parent_check = sg._run_git("rev-parse", "--verify", f"{burst_commit}~1")
                    if parent_check.returncode == 0 and parent_check.stdout.strip():
                        rolled_back_git = sg.restore_workspace_to_commit(
                            parent_check.stdout.strip(), target_dir=target_workspace, project_slug=slug, engine_root=engine_p
                        )
                    else:
                        ls_res = sg._run_git("ls-tree", "-r", "--name-only", burst_commit)
                        if ls_res.returncode == 0 and ls_res.stdout.strip():
                            for rel_file in ls_res.stdout.strip().splitlines():
                                f_p = target_workspace / rel_file.strip()
                                if f_p.is_file() or f_p.is_symlink():
                                    f_p.unlink(missing_ok=True)
                        sg._run_git("update-ref", "-d", "HEAD")
                        rolled_back_git = True
                else:
                    rolled_back_git = sg.rollback_burst_commit(eid)
            except Exception as exc:
                logger.warning("ShadowGit restore failed for event #%d: %s", eid, exc)

        cur.execute("SELECT id FROM events WHERE session_id = ? AND id >= ?", (session_id, eid) if session_id else (eid,))
        burst_ids_to_trash = [r["id"] for r in cur.fetchall()]
        if eid not in burst_ids_to_trash:
            burst_ids_to_trash.append(eid)

        conn.close()
        conn = None

        try:
            from ..trash_manager import TrashManager, SCOPE_SELECTIVE_BURSTS
            tm = TrashManager(engine_p)
            trash_res = tm.move_to_trash(slug, SCOPE_SELECTIVE_BURSTS, burst_ids=burst_ids_to_trash)
        except Exception as exc:
            trash_res = {"success": False, "error": str(exc)}

        conn = sqlite3.connect(db_path, timeout=10.0)
        cur = conn.cursor()
        placeholders = ",".join("?" for _ in burst_ids_to_trash)
        cur.execute(f"DELETE FROM patches WHERE event_id IN ({placeholders})", burst_ids_to_trash)
        cur.execute(f"DELETE FROM events WHERE id IN ({placeholders})", burst_ids_to_trash)
        conn.commit()

        time.sleep(1.5)
        return {
            "success": True,
            "event_id": eid,
            "project": slug,
            "rolled_back_git": rolled_back_git,
            "trash_res": trash_res,
            "message": f"Burst #{eid} successfully rolled back and moved to Trash.",
        }
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        if lock_file and lock_file.exists():
            try:
                lock_file.unlink(missing_ok=True)
            except Exception:
                pass
