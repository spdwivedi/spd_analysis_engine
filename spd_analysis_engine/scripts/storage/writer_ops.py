"""
scripts/storage/writer_ops.py
=============================
Data models, metrics, and operation dispatcher for the ProjectWriter architecture.
"""
from __future__ import annotations

import concurrent.futures
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from .utils import _utcnow_iso


@dataclass
class WriteOperation:
    """A single logical write operation submitted to ProjectWriter."""
    op_type: str
    payload: dict[str, Any]
    future: concurrent.futures.Future | None = None
    is_critical: bool = True
    sequence: int = 0
    submitted_at: float = field(default_factory=time.perf_counter)


@dataclass
class WriterMetrics:
    """Observable metrics for ProjectWriter backpressure, queue depth, and throughput."""
    queue_depth: int
    max_queue_size: int
    total_submitted: int
    total_committed: int
    total_dropped: int
    total_batches: int
    avg_batch_size: float
    last_batch_duration_ms: float
    is_running: bool
    backpressure_active: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "queue_depth": self.queue_depth,
            "max_queue_size": self.max_queue_size,
            "total_submitted": self.total_submitted,
            "total_committed": self.total_committed,
            "total_dropped": self.total_dropped,
            "total_batches": self.total_batches,
            "avg_batch_size": round(self.avg_batch_size, 2),
            "last_batch_duration_ms": round(self.last_batch_duration_ms, 2),
            "is_running": self.is_running,
            "backpressure_active": self.backpressure_active,
        }


def execute_operation(conn: sqlite3.Connection, op: WriteOperation) -> Any:
    """Dispatch operation execution against the write connection."""
    p = op.payload
    t = op.op_type

    if t == "event":
        eid = p.get("event_id") or f"evt_{uuid.uuid4().hex[:16]}"
        seq = p.get("seq")
        sid = p["session_id"]
        if seq is None:
            cur = conn.cursor()
            cur.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM events WHERE session_id = ?;", (sid,))
            r = cur.fetchone()
            seq = int(r[0]) if r else 1

        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO events (
                session_id, event_type, timestamp, first_file_touched, summary,
                executor, commit_hash, is_rolled_back, burst_num, event_id, seq,
                burst_uid, effective_files
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                sid, p["event_type"], p.get("timestamp") or _utcnow_iso(),
                p.get("first_file"), p.get("summary"), p.get("executor"),
                p.get("commit_hash"), p.get("is_rolled_back", 0), p.get("burst_num"),
                eid, seq, p.get("burst_uid"), p.get("effective_files"),
            ),
        )
        return cur.lastrowid

    elif t == "patch":
        cur = conn.cursor()
        size = p.get("size") or (len(p["diff_content"]) if p.get("diff_content") else 0)
        cur.execute(
            """
            INSERT INTO patches (
                event_id, file_path, diff_content, adds, dels, kind,
                size, hash_before, hash_after, truncated
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                p["event_id"], p["file_path"], p.get("diff_content"),
                p.get("adds", 0), p.get("dels", 0), p.get("kind", "unified"),
                size, p.get("hash_before"), p.get("hash_after"), p.get("truncated", 0),
            ),
        )
        return cur.lastrowid

    elif t == "session_create":
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO sessions (
                project_name, target_path, start_time, status, end_reason,
                worker_id, config_json, actor
            ) VALUES (?, ?, ?, 'ACTIVE', ?, ?, ?, ?);
            """,
            (
                p["project_name"], p["target_path"], _utcnow_iso(),
                p.get("end_reason"), p.get("worker_id"), p.get("config_json"), p.get("actor"),
            ),
        )
        return cur.lastrowid

    elif t == "session_close":
        sid = p["session_id"]
        st = p.get("status", "STOPPED")
        er = p.get("end_reason")
        if er:
            conn.execute(
                "UPDATE sessions SET end_time = ?, status = ?, end_reason = ? WHERE id = ?;",
                (_utcnow_iso(), st, er, sid),
            )
        else:
            conn.execute(
                "UPDATE sessions SET end_time = ?, status = ? WHERE id = ?;",
                (_utcnow_iso(), st, sid),
            )
        return sid

    elif t == "manifest":
        conn.execute(
            """
            INSERT INTO manifest (file_path, file_hash, file_size, last_modified, tracked_status, session_id)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(file_path) DO UPDATE SET
                file_hash = excluded.file_hash,
                file_size = excluded.file_size,
                last_modified = excluded.last_modified,
                tracked_status = excluded.tracked_status,
                session_id = excluded.session_id;
            """,
            (
                p["file_path"], p["file_hash"], p["file_size"],
                p["last_modified"], p.get("tracked_status", "TRACKED"), p.get("session_id"),
            ),
        )
        return p["file_path"]

    elif t == "git_attribution":
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO git_attribution (
                session_id, commit_hash, author_name, author_email,
                committed_at, milestone_id, burst_ids
            ) VALUES (?, ?, ?, ?, ?, ?, ?);
            """,
            (
                p["session_id"], p["commit_hash"], p.get("author_name"),
                p.get("author_email"), p.get("committed_at") or _utcnow_iso(),
                p.get("milestone_id"), p.get("burst_ids"),
            ),
        )
        return cur.lastrowid

    elif t == "capture_meta":
        conn.execute(
            """
            INSERT INTO capture_metadata (key, value, updated_at, session_id)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at,
                session_id = excluded.session_id;
            """,
            (p["key"], p["value"], _utcnow_iso(), p.get("session_id")),
        )
        return p["key"]

    elif t == "sql":
        cur = conn.cursor()
        cur.execute(p["sql"], p.get("params", ()))
        return cur.lastrowid or cur.rowcount

    elif t == "custom":
        fn: Callable[[sqlite3.Connection], Any] = p["fn"]
        return fn(conn)

    raise ValueError(f"Unknown operation type: {t!r}")
