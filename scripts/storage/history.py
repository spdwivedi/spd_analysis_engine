"""
scripts/storage/history.py
==========================
Project history consolidation for the storage package.
"""
from __future__ import annotations

import logging
import shutil
import sqlite3
from pathlib import Path

try:
    from .utils import _slug
    from .db import SessionDB
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.storage.utils import _slug
        from spd_analysis_engine.scripts.storage.db import SessionDB
    except (ImportError, ModuleNotFoundError):
        from scripts.storage.utils import _slug
        from scripts.storage.db import SessionDB

logger = logging.getLogger(__name__)


def consolidate_project_history(engine_root: Path | str, project_name: str) -> Path | None:
    """
    Consolidate all historical session.db files and patch files for a project across
    history/<slug>/run_*, last_run/<slug>, and current/<slug> into a single cumulative
    database. Idempotent and crash-resilient.

    Returns the path to the canonical session.db.
    """
    slug = _slug(project_name)
    engine_p = Path(engine_root).resolve()

    current_dir = engine_p / "current" / slug
    last_run_dir = engine_p / "last_run" / slug
    hist_dir = engine_p / "history" / slug

    run_dirs: list[Path] = []
    if hist_dir.exists():
        for d in sorted(hist_dir.iterdir()):
            if d.is_dir() and d.name.startswith("run_"):
                if (d / "session.db").exists():
                    run_dirs.append(d)

    if last_run_dir.exists() and (last_run_dir / "session.db").exists():
        run_dirs.append(last_run_dir)

    if current_dir.exists() and (current_dir / "session.db").exists():
        run_dirs.append(current_dir)

    if not run_dirs:
        return None

    target_dir = current_dir if current_dir.exists() else last_run_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    target_db_path = target_dir / "session.db"
    target_patches_dir = target_dir / "patches"
    target_patches_dir.mkdir(parents=True, exist_ok=True)

    target_db = SessionDB(target_db_path)
    target_conn = target_db.conn
    assert target_conn is not None

    target_cur = target_conn.cursor()
    target_cur.execute("SELECT id, project_name, start_time FROM sessions")
    existing_sessions = {(r[1], r[2]): r[0] for r in target_cur.fetchall()}

    target_cur.execute("SELECT id, timestamp, event_type, first_file_touched, summary FROM events")
    existing_events = {(r[1], r[2], r[3], r[4]): r[0] for r in target_cur.fetchall()}

    for src_dir in run_dirs:
        src_db_path = src_dir / "session.db"
        if src_db_path.resolve() == target_db_path.resolve():
            continue

        try:
            src_conn = sqlite3.connect(f"file:{src_db_path}?mode=ro", uri=True)
            src_conn.row_factory = sqlite3.Row
            src_cur = src_conn.cursor()

            src_cur.execute("SELECT id, project_name, target_path, start_time, end_time, status FROM sessions ORDER BY id ASC")
            src_sessions = src_cur.fetchall()
            session_map: dict[int, int] = {}
            for s in src_sessions:
                s_key = (s["project_name"], s["start_time"])
                if s_key in existing_sessions:
                    session_map[s["id"]] = existing_sessions[s_key]
                else:
                    target_cur.execute(
                        "INSERT INTO sessions (project_name, target_path, start_time, end_time, status) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (s["project_name"], s["target_path"], s["start_time"], s["end_time"], s["status"]),
                    )
                    new_sid = target_cur.lastrowid
                    existing_sessions[s_key] = new_sid
                    session_map[s["id"]] = new_sid

            src_cur.execute("PRAGMA table_info(events)")
            cols = [c["name"] for c in src_cur.fetchall()]
            has_ai = "ai_summary" in cols
            has_exec = "executor" in cols

            select_cols = ["id", "session_id", "event_type", "timestamp", "first_file_touched", "summary"]
            if has_ai:
                select_cols.append("ai_summary")
            if has_exec:
                select_cols.append("executor")

            src_cur.execute(f"SELECT {', '.join(select_cols)} FROM events ORDER BY id ASC")
            src_events = src_cur.fetchall()

            for ev in src_events:
                ev_key = (ev["timestamp"], ev["event_type"], ev["first_file_touched"], ev["summary"])
                if ev_key in existing_events:
                    continue

                mapped_sid = session_map.get(ev["session_id"])
                if mapped_sid is None:
                    mapped_sid = list(existing_sessions.values())[-1] if existing_sessions else 1

                ai_summary = ev["ai_summary"] if has_ai else None
                executor = ev["executor"] if has_exec else None
                target_cur.execute(
                    "INSERT INTO events (session_id, event_type, timestamp, first_file_touched, summary, ai_summary, executor) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (mapped_sid, ev["event_type"], ev["timestamp"], ev["first_file_touched"], ev["summary"], ai_summary, executor),
                )
                new_eid = target_cur.lastrowid
                existing_events[ev_key] = new_eid

                src_cur.execute("SELECT file_path, diff_content FROM patches WHERE event_id = ?", (ev["id"],))
                for p in src_cur.fetchall():
                    target_cur.execute(
                        "INSERT INTO patches (event_id, file_path, diff_content) VALUES (?, ?, ?)",
                        (new_eid, p["file_path"], p["diff_content"]),
                    )

                src_patch_file = src_dir / "patches" / f"event_{ev['id']}.patch"
                if src_patch_file.exists():
                    target_patch_file = target_patches_dir / f"event_{new_eid}.patch"
                    try:
                        shutil.copy2(src_patch_file, target_patch_file)
                    except Exception:
                        pass

            target_conn.commit()
            src_conn.close()
        except Exception as exc:
            logger.warning("Error consolidating %s: %s", src_db_path, exc)

    target_db.checkpoint()
    target_db.close()
    return target_db_path
