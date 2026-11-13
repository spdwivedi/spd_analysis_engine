"""
scripts/storage/history.py
==========================
Project history consolidation for the storage package.
"""
from __future__ import annotations

import logging
import shutil
import sqlite3
import time
from collections import Counter
from pathlib import Path
from typing import Any

from .utils import _slug
from .db import SessionDB

logger = logging.getLogger(__name__)


def _open_ro_connection(db_path: Path) -> sqlite3.Connection:
    """Open SQLite connection in strictly read-only mode with 30s busy timeout."""
    db_uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    last_exc = None
    for attempt in range(3):
        try:
            conn = sqlite3.connect(db_uri, uri=True, timeout=30.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout = 30000;")
            conn.execute("PRAGMA query_only = ON;")
            conn.execute("PRAGMA wal_autocheckpoint = 0;")
            return conn
        except sqlite3.OperationalError as exc:
            last_exc = exc
            time.sleep(0.05 * (2 ** attempt))
    if last_exc:
        raise last_exc
    conn = sqlite3.connect(db_uri, uri=True, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON;")
    conn.execute("PRAGMA wal_autocheckpoint = 0;")
    return conn


def _retry_cursor_execute(cur: sqlite3.Cursor, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
    """Execute statement with retry on busy/locked."""
    for attempt in range(3):
        try:
            return cur.execute(sql, params)
        except sqlite3.OperationalError:
            if attempt == 2:
                raise
            time.sleep(0.05 * (2 ** attempt))
    return cur


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
    try:
        target_conn = target_db.conn
        assert target_conn is not None

        target_cur = target_conn.cursor()
        target_cur.execute("SELECT id, project_name, start_time FROM sessions")
        existing_sessions = {(r[1], r[2]): r[0] for r in target_cur.fetchall()}

        target_cur.execute("SELECT timestamp, event_type, first_file_touched, summary FROM events")
        target_counts = Counter((r[0], r[1], r[2], r[3]) for r in target_cur.fetchall())
        inserted_counts: Counter[tuple[str, str, str | None, str | None]] = Counter()

        for src_dir in run_dirs:
            src_db_path = src_dir / "session.db"
            if src_db_path.resolve() == target_db_path.resolve():
                continue

            src_conn = None
            try:
                src_conn = _open_ro_connection(src_db_path)
                src_cur = src_conn.cursor()

                _retry_cursor_execute(src_cur, "SELECT id, project_name, target_path, start_time, end_time, status FROM sessions ORDER BY id ASC")
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
                    if inserted_counts[ev_key] < target_counts[ev_key]:
                        inserted_counts[ev_key] += 1
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
                    inserted_counts[ev_key] += 1

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
            except Exception as exc:
                logger.warning("Error consolidating %s: %s", src_db_path, exc)
            finally:
                if src_conn is not None:
                    try:
                        src_conn.close()
                    except Exception:
                        pass

        target_db.checkpoint()
    finally:
        try:
            target_db.close()
        except Exception:
            pass
    return target_db_path
