"""
scripts/git/milestone_tracker.py
=================================
Relational milestone tracking micro-module.

Provides three pure-function helpers that read/write to the project's
session.db without touching any ai_data/cache files:

  get_unpushed_bursts()        – events with pushed_in_milestone_id IS NULL
  record_git_milestone()       – insert milestone + mark events as pushed
  get_project_milestone_history() – recent milestone rows for UI display
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Idempotently ensure column and table migrations exist on legacy databases."""
    try:
        conn.execute("ALTER TABLE events ADD COLUMN pushed_in_milestone_id INTEGER NULL")
    except sqlite3.OperationalError:
        pass
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS git_milestones (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id      INTEGER NOT NULL,
        commit_sha      TEXT    NOT NULL,
        commit_title    TEXT    NOT NULL,
        commit_body     TEXT,
        changelog_entry TEXT,
        burst_ids       TEXT    NOT NULL,
        remote_url      TEXT    NOT NULL,
        branch          TEXT    NOT NULL,
        pushed_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(session_id) REFERENCES sessions(id)
    );
    CREATE INDEX IF NOT EXISTS idx_milestones_session ON git_milestones(session_id);
    """)


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None, timeout=8.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _ensure_schema(conn)
    return conn


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    db_uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(db_uri, uri=True, check_same_thread=False, timeout=8.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON;")
    conn.execute("PRAGMA wal_autocheckpoint = 0;")
    return conn


def _latest_session_id(conn: sqlite3.Connection) -> int | None:
    cur = conn.execute("SELECT id FROM sessions ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    return int(row["id"]) if row else None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_unpushed_bursts(
    db_path: Path | str,
    session_id: int | None = None,
) -> list[dict[str, Any]]:
    """Return all burst events that have not yet been attributed to a git milestone."""
    path = Path(db_path)
    if not path.exists():
        return []
    try:
        conn = _connect_ro(path)
        if session_id is None:
            cur_s = conn.execute(
                "SELECT session_id FROM events "
                "WHERE (event_type = 'EDIT' OR event_type IS NULL OR event_type = '') "
                "  AND (pushed_in_milestone_id IS NULL) "
                "ORDER BY id DESC LIMIT 1"
            )
            row_s = cur_s.fetchone()
            session_id = int(row_s["session_id"]) if row_s else _latest_session_id(conn)
        if session_id is None:
            conn.close()
            return []
        cur = conn.execute(
            "SELECT * FROM events "
            "WHERE session_id = ? "
            "  AND (event_type = 'EDIT' OR event_type IS NULL OR event_type = '') "
            "  AND (pushed_in_milestone_id IS NULL) "
            "ORDER BY id ASC",
            (session_id,),
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as exc:
        logger.warning("get_unpushed_bursts failed for %s: %s", db_path, exc)
        return []


def record_git_milestone(
    db_path: Path | str,
    session_id: int,
    commit_sha: str,
    title: str,
    body: str,
    changelog: str,
    burst_ids: list[int],
    remote_url: str,
    branch: str,
) -> int:
    """Persist a completed push as git_milestones row and mark events as pushed."""
    path = Path(db_path)
    burst_ids_json = json.dumps(burst_ids)
    try:
        conn = _connect(path)
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "INSERT INTO git_milestones "
            "(session_id, commit_sha, commit_title, commit_body, changelog_entry, "
            "burst_ids, remote_url, branch) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, commit_sha, title, body, changelog,
             burst_ids_json, remote_url, branch),
        )
        milestone_id = cur.lastrowid
        if burst_ids:
            placeholders = ",".join("?" for _ in burst_ids)
            conn.execute(
                f"UPDATE events SET pushed_in_milestone_id = ? "
                f"WHERE id IN ({placeholders})",
                (milestone_id, *burst_ids),
            )
        conn.execute("COMMIT")
        conn.close()
        logger.info(
            "Recorded milestone #%s (sha=%s, %d events) for session %s",
            milestone_id, commit_sha[:8], len(burst_ids), session_id,
        )
        return int(milestone_id)  # type: ignore[arg-type]
    except Exception as exc:
        logger.error("record_git_milestone failed for %s: %s", db_path, exc)
        return -1


def get_project_milestone_history(
    db_path: Path | str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """
    Return the most recent ``git_milestones`` rows for dashboard display.

    Returns
    -------
    list of dicts ordered newest-first.
    """
    path = Path(db_path)
    if not path.exists():
        return []
    try:
        conn = _connect_ro(path)
        cur = conn.execute(
            "SELECT * FROM git_milestones ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as exc:
        logger.warning("get_project_milestone_history failed for %s: %s", db_path, exc)
        return []


__all__ = [
    "get_unpushed_bursts",
    "record_git_milestone",
    "get_project_milestone_history",
]
