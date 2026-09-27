"""
scripts/server/db_queries.py
============================
Database query helper functions for the SPD Analysis Engine web server.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

try:
    import psutil
    _PSUTIL_AVAILABLE = True
except ImportError:
    _PSUTIL_AVAILABLE = False

try:
    from ..shell_interceptor import is_ignored_command, scrub_tokens
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.shell_interceptor import is_ignored_command, scrub_tokens
    except (ImportError, ModuleNotFoundError):
        try:
            from scripts.shell_interceptor import is_ignored_command, scrub_tokens
        except (ImportError, ModuleNotFoundError):
            def is_ignored_command(cmd: str) -> bool:
                return False

            def scrub_tokens(text: str) -> str:
                return text

logger = logging.getLogger(__name__)


def _count_events_in_db(db_path: Path) -> int:
    """Count prompt burst events (EDIT events) recorded in the database."""
    if not db_path.exists():
        return 0
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10.0)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM events WHERE event_type = 'EDIT' OR event_type IS NULL OR event_type = ''")
        row = cur.fetchone()
        count = row[0] if row else 0
        conn.close()
        return count
    except Exception:
        return 0


def _read_sessions_from_db(db_path: Path) -> list[dict[str, Any]]:
    """Read all sessions from session.db with burst counts, chronological order, and active state."""
    if not db_path.exists():
        return []
    try:
        # Determine if the project is actually currently running
        is_worker_running = False
        proj_dir = db_path.parent
        if proj_dir.parent.name == "current":
            pid_file = proj_dir / "worker.pid"
            if pid_file.exists():
                try:
                    pid_data = json.loads(pid_file.read_text(encoding="utf-8"))
                    pid = int(pid_data.get("pid", 0))
                except Exception:
                    try:
                        pid = int(pid_file.read_text(encoding="utf-8").strip())
                    except Exception:
                        pid = 0

                if pid > 0:
                    if _PSUTIL_AVAILABLE:
                        try:
                            p = psutil.Process(pid)
                            is_worker_running = p.is_running() and p.status() != psutil.STATUS_ZOMBIE
                        except Exception:
                            is_worker_running = False
                    else:
                        try:
                            os.kill(pid, 0)
                            is_worker_running = True
                        except Exception:
                            is_worker_running = False

        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10.0)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            "SELECT DISTINCT id, project_name, target_path, start_time, end_time, status "
            "FROM sessions ORDER BY id ASC"
        )
        s_rows = cur.fetchall()

        cur.execute(
            "SELECT session_id, COUNT(*) as c FROM events "
            "WHERE (event_type = 'EDIT' OR event_type IS NULL OR event_type = '') "
            "GROUP BY session_id"
        )
        burst_counts = {r["session_id"]: r["c"] for r in cur.fetchall()}

        cur.execute("SELECT session_id, COUNT(*) as c FROM events GROUP BY session_id")
        total_event_counts = {r["session_id"]: r["c"] for r in cur.fetchall()}
        conn.close()

        # Find the single highest session ID that could be active
        max_active_sid = None
        if is_worker_running:
            active_sids = [r["id"] for r in s_rows if r["status"] == "ACTIVE"]
            if active_sids:
                max_active_sid = max(active_sids)
            elif s_rows:
                max_active_sid = max(r["id"] for r in s_rows)

        sessions: list[dict[str, Any]] = []
        for r in s_rows:
            sid = r["id"]
            # Only the single active running session gets is_active=True
            is_active = (sid == max_active_sid) if is_worker_running else False
            b_count = burst_counts.get(sid, 0)
            t_count = total_event_counts.get(sid, 0)

            # Prune/skip zero-event, zero-burst phantom sessions if not currently active
            if b_count == 0 and t_count == 0 and not is_active:
                continue

            status_str = "ACTIVE" if is_active else (r["status"] if r["status"] != "ACTIVE" else "STOPPED")
            sessions.append({
                "id": sid,
                "session_id": sid,
                "project_name": r["project_name"],
                "target_path": r["target_path"],
                "start_time": r["start_time"],
                "end_time": r["end_time"],
                "status": status_str,
                "is_active": is_active,
                "burst_count": b_count,
                "bursts_count": b_count,
                "label": f"Session {sid} ({'Active' if is_active else str(b_count) + ' bursts'})",
            })

        # Fallback if sessions table was empty but events exist
        if not sessions and burst_counts:
            for sid, b_count in sorted(burst_counts.items(), key=lambda x: x[0]):
                sessions.append({
                    "id": sid,
                    "session_id": sid,
                    "project_name": proj_dir.name,
                    "target_path": "",
                    "start_time": None,
                    "end_time": None,
                    "status": "STOPPED",
                    "is_active": False,
                    "burst_count": b_count,
                    "bursts_count": b_count,
                    "label": f"Session {sid} ({b_count} bursts)",
                })

        return sessions
    except Exception as exc:
        logger.debug("Error reading sessions from %s: %s", db_path, exc)
        return []


def _read_events_from_db(db_path: Path) -> list[dict[str, Any]]:
    """Read all events and their linked patches from session.db with clean sequential burst indices."""
    if not db_path.exists():
        return []

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10.0)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("PRAGMA table_info(events)")
    cols = [r["name"] for r in cur.fetchall()]
    has_ai_summary = "ai_summary" in cols
    has_executor = "executor" in cols
    has_commit_hash = "commit_hash" in cols
    has_is_rolled_back = "is_rolled_back" in cols

    select_cols = ["id", "session_id", "event_type", "timestamp", "first_file_touched", "summary"]
    if has_ai_summary:
        select_cols.append("ai_summary")
    if has_executor:
        select_cols.append("executor")
    if has_commit_hash:
        select_cols.append("commit_hash")
    if has_is_rolled_back:
        select_cols.append("is_rolled_back")
    cur.execute(f"SELECT {', '.join(select_cols)} FROM events ORDER BY id ASC")
    event_rows = cur.fetchall()

    events: list[dict[str, Any]] = []
    burst_by_session: dict[int, int] = {}

    for er in event_rows:
        raw_first_file = er["first_file_touched"] or ""
        raw_summary = er["summary"] or ""

        # 1. Filter out internal IDE EXEC noise rows
        if er["event_type"] == "EXEC":
            if is_ignored_command(raw_first_file) or is_ignored_command(raw_summary):
                continue

        eid = er["id"]
        sid = er["session_id"]
        first_file_scrubbed = scrub_tokens(raw_first_file) if raw_first_file else None
        summary_scrubbed = scrub_tokens(raw_summary) if raw_summary else None
        executor_val = er["executor"] if has_executor else None
        commit_hash_val = er["commit_hash"] if has_commit_hash else None
        is_rolled_back_val = int(er["is_rolled_back"]) if (has_is_rolled_back and er["is_rolled_back"] is not None) else 0

        cur.execute(
            "SELECT id, file_path, diff_content FROM patches WHERE event_id = ? ORDER BY id ASC",
            (eid,),
        )
        patch_rows = cur.fetchall()

        patches = []
        total_adds = 0
        total_dels = 0
        for pr in patch_rows:
            diff_text = pr["diff_content"] or ""
            adds = 0
            dels = 0
            for line in diff_text.splitlines():
                if line.startswith("+") and not line.startswith("+++"):
                    adds += 1
                elif line.startswith("-") and not line.startswith("---"):
                    dels += 1
            total_adds += adds
            total_dels += dels
            patches.append({
                "id": pr["id"],
                "file_path": pr["file_path"],
                "diff_content": pr["diff_content"],
                "additions": adds,
                "deletions": dels,
                "lines_added": adds,
                "lines_deleted": dels,
            })

        # 2. Sequential burst numbering per session & execution tagging
        is_burst = (er["event_type"] == "EDIT") or (len(patches) > 0)
        if is_burst:
            burst_by_session[sid] = burst_by_session.get(sid, 0) + 1
            burst_num = burst_by_session[sid]
            associated_burst_id = burst_num
            timing_relation = "burst"
            session_burst_label = f"Session {sid} • Burst #{burst_num}"
        else:
            burst_num = None
            last_burst = burst_by_session.get(sid)
            if last_burst:
                associated_burst_id = last_burst
                timing_relation = "after_burst"
            else:
                associated_burst_id = None
                timing_relation = "before_burst"
            session_burst_label = f"Session {sid} • {er['event_type']}"

        ai_summary_parsed = None
        if has_ai_summary and er["ai_summary"]:
            try:
                ai_summary_parsed = json.loads(er["ai_summary"])
            except Exception:
                ai_summary_parsed = {
                    "intent": str(er["ai_summary"]),
                    "summary": [],
                    "functionality_gained": "",
                }

        events.append(
            {
                "id": eid,
                "session_id": sid,
                "burst_num": burst_num,
                "associated_burst_id": associated_burst_id,
                "timing_relation": timing_relation,
                "session_burst_label": session_burst_label,
                "event_type": er["event_type"],
                "timestamp": er["timestamp"],
                "first_file_touched": first_file_scrubbed,
                "summary": summary_scrubbed,
                "executor": executor_val,
                "commit_hash": commit_hash_val,
                "is_rolled_back": is_rolled_back_val,
                "ai_summary": ai_summary_parsed,
                "patches_count": len(patches),
                "patches": patches,
                "additions": total_adds,
                "deletions": total_dels,
            }
        )

    conn.close()
    return events


def resolve_burst_event(
    db_path: Path,
    session_id: int | str | None,
    burst_id_or_number: int | str | None = None,
    *,
    event_id: int | str | None = None,
    burst_number: int | str | None = None,
) -> tuple[int | None, int | None, int | None]:
    """
    Resolve (session_id, burst_id_or_number, event_id, burst_number) to:
    (exact_event_id, sequential_burst_number, resolved_session_id).
    
    Ensures that patch queries strictly query `WHERE event_id = ?` using the
    exact database primary key, and cache keys can use the sequential burst_number.
    """
    if not db_path.exists():
        eid = int(event_id) if event_id is not None and str(event_id).isdigit() else None
        bnum = int(burst_number) if burst_number is not None and str(burst_number).isdigit() else (
            int(burst_id_or_number) if burst_id_or_number is not None and str(burst_id_or_number).isdigit() else None
        )
        sid = int(session_id) if session_id is not None and str(session_id).isdigit() else None
        return eid, bnum, sid

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10.0)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        resolved_sid: int | None = int(session_id) if session_id is not None and str(session_id).isdigit() else None
        explicit_eid: int | None = int(event_id) if event_id is not None and str(event_id).isdigit() else None
        explicit_bnum: int | None = int(burst_number) if burst_number is not None and str(burst_number).isdigit() else None

        # If session_id not provided but event_id or burst_id_or_number provided, inspect events table
        if resolved_sid is None:
            cand_id = explicit_eid or (int(burst_id_or_number) if burst_id_or_number is not None and str(burst_id_or_number).isdigit() else None)
            if cand_id is not None:
                cur.execute("SELECT session_id FROM events WHERE id = ?", (cand_id,))
                row = cur.fetchone()
                if row and row["session_id"] is not None:
                    resolved_sid = row["session_id"]

        if resolved_sid is None:
            cur.execute("SELECT MAX(id) as max_id FROM sessions")
            row = cur.fetchone()
            if row and row["max_id"] is not None:
                resolved_sid = row["max_id"]
            else:
                resolved_sid = 1

        # Query all EDIT / patch-bearing events for this session in order
        cur.execute(
            "SELECT id FROM events "
            "WHERE session_id = ? AND (event_type = 'EDIT' OR event_type IS NULL OR event_type = '') "
            "ORDER BY id ASC",
            (resolved_sid,),
        )
        edit_ids = [r["id"] for r in cur.fetchall()]
        conn.close()

        if not edit_ids:
            # Fallback if no EDIT events in that session
            val = explicit_eid or (int(burst_id_or_number) if burst_id_or_number is not None and str(burst_id_or_number).isdigit() else None)
            return val, explicit_bnum or val, resolved_sid

        # 1. Explicit event_id takes precedence for exact DB row
        if explicit_eid is not None:
            if explicit_eid in edit_ids:
                return explicit_eid, edit_ids.index(explicit_eid) + 1, resolved_sid
            return explicit_eid, explicit_bnum, resolved_sid

        # 2. Explicit burst_number takes precedence for 1-based sequential mapping
        if explicit_bnum is not None:
            if 1 <= explicit_bnum <= len(edit_ids):
                return edit_ids[explicit_bnum - 1], explicit_bnum, resolved_sid
            return edit_ids[-1], explicit_bnum, resolved_sid

        # 3. Disambiguate burst_id_or_number
        if burst_id_or_number is not None and str(burst_id_or_number).isdigit():
            val = int(burst_id_or_number)
            # If val is a 1-based burst index within range
            if 1 <= val <= len(edit_ids):
                return edit_ids[val - 1], val, resolved_sid
            # If val matches an actual event_id in edit_ids (e.g. event 9)
            if val in edit_ids:
                return val, edit_ids.index(val) + 1, resolved_sid
            # Out of bounds fallback: if val matches any event in DB
            return val, None, resolved_sid

        # Default fallback to the latest burst in session
        return edit_ids[-1], len(edit_ids), resolved_sid

    except Exception as exc:
        logger.debug("Error in resolve_burst_event: %s", exc)
        eid = int(event_id) if event_id is not None and str(event_id).isdigit() else None
        val = int(burst_id_or_number) if burst_id_or_number is not None and str(burst_id_or_number).isdigit() else None
        sid = int(session_id) if session_id is not None and str(session_id).isdigit() else 1
        return eid or val, val, sid

