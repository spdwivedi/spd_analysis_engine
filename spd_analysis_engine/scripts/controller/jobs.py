"""
scripts/controller/jobs.py
==========================
Controller-owned data management for engine.db:
- jobs: background tasks, indexing jobs, repair tasks
- ai_analyses: cached and historical AI syntheses and audit trails
- controller flags: system-wide runtime feature flags and engine toggles
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import asdict, dataclass
from typing import Any

from spd_analysis_engine.scripts.core.time_utils import utc_now_iso

logger = logging.getLogger(__name__)


@dataclass
class JobRecord:
    job_id: str
    project_id: str
    kind: str
    state: str  # 'PENDING', 'RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED'
    payload_json: str | None = None
    result_json: str | None = None
    created_at: str = ""
    finished_at: str | None = None
    error: str | None = None


@dataclass
class AiAnalysisRecord:
    analysis_id: str
    project_id: str
    target_type: str  # 'burst', 'event', 'session', 'milestone'
    target_id: str
    prompt: str
    response: str | None = None
    model: str | None = None
    state: str = "COMPLETED"  # 'PENDING', 'COMPLETED', 'FAILED'
    created_at: str = ""
    finished_at: str | None = None
    error: str | None = None


@dataclass
class ControllerFlag:
    flag_name: str
    enabled: bool = False
    description: str | None = None
    updated_at: str = ""


def init_controller_tables(conn: sqlite3.Connection) -> None:
    """Initialize controller-owned jobs, ai_analyses, and controller_flags tables."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
            job_id       TEXT PRIMARY KEY,
            project_id   TEXT NOT NULL,
            kind         TEXT NOT NULL,
            state        TEXT NOT NULL,
            payload_json TEXT,
            result_json  TEXT,
            created_at   TEXT NOT NULL,
            finished_at  TEXT,
            error        TEXT,
            FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS ai_analyses (
            analysis_id  TEXT PRIMARY KEY,
            project_id   TEXT NOT NULL,
            target_type  TEXT NOT NULL,
            target_id    TEXT NOT NULL,
            prompt       TEXT NOT NULL,
            response     TEXT,
            model        TEXT,
            state        TEXT NOT NULL,
            created_at   TEXT NOT NULL,
            finished_at  TEXT,
            error        TEXT,
            FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS controller_flags (
            flag_name    TEXT PRIMARY KEY,
            enabled      INTEGER NOT NULL DEFAULT 0,
            description  TEXT,
            updated_at   TEXT NOT NULL
        );
    """)
    existing_jobs = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    for col, spec in (
        ("job_id", "TEXT"), ("project_id", "TEXT"), ("kind", "TEXT"), ("state", "TEXT"),
        ("payload_json", "TEXT"), ("result_json", "TEXT"), ("finished_at", "TEXT"), ("error", "TEXT"),
    ):
        if col not in existing_jobs:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {spec};")

    existing_ai = {row[1] for row in conn.execute("PRAGMA table_info(ai_analyses)").fetchall()}
    for col, spec in (
        ("analysis_id", "TEXT"), ("project_id", "TEXT"), ("target_type", "TEXT"), ("prompt", "TEXT"),
        ("response", "TEXT"), ("model", "TEXT"), ("state", "TEXT"), ("finished_at", "TEXT"), ("error", "TEXT"),
    ):
        if col not in existing_ai:
            conn.execute(f"ALTER TABLE ai_analyses ADD COLUMN {col} {spec};")



# ---------------------------------------------------------------------------
# Jobs API
# ---------------------------------------------------------------------------

def create_job(
    conn: sqlite3.Connection,
    job_id: str,
    project_id: str,
    kind: str,
    payload: dict[str, Any] | None = None,
) -> JobRecord:
    now = utc_now_iso()
    payload_str = json.dumps(payload) if payload is not None else None
    conn.execute(
        """
        INSERT INTO jobs (job_id, project_id, kind, state, payload_json, created_at)
        VALUES (?, ?, ?, 'PENDING', ?, ?);
        """,
        (job_id, project_id, kind, payload_str, now),
    )
    return JobRecord(
        job_id=job_id,
        project_id=project_id,
        kind=kind,
        state="PENDING",
        payload_json=payload_str,
        created_at=now,
    )


def update_job_state(
    conn: sqlite3.Connection,
    job_id: str,
    state: str,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    now = utc_now_iso() if state in ("COMPLETED", "FAILED", "CANCELLED") else None
    res_str = json.dumps(result) if result is not None else None
    conn.execute(
        """
        UPDATE jobs SET
            state = ?,
            result_json = COALESCE(?, result_json),
            finished_at = COALESCE(?, finished_at),
            error = COALESCE(?, error)
        WHERE job_id = ?;
        """,
        (state, res_str, now, error, job_id),
    )


def get_job(conn: sqlite3.Connection, job_id: str) -> JobRecord | None:
    cur = conn.cursor()
    cols = {row[1] for row in cur.execute("PRAGMA table_info(jobs)").fetchall()}
    if "job_id" in cols and "id" in cols:
        cur.execute("SELECT * FROM jobs WHERE job_id = ? OR id = ?;", (job_id, job_id))
    elif "job_id" in cols:
        cur.execute("SELECT * FROM jobs WHERE job_id = ?;", (job_id,))
    else:
        cur.execute("SELECT * FROM jobs WHERE id = ?;", (job_id,))
    row = cur.fetchone()
    if not row:
        return None
    d = dict(row)
    fields = {"job_id", "project_id", "kind", "state", "payload_json", "result_json", "created_at", "finished_at", "error"}
    filtered = {k: v for k, v in d.items() if k in fields}
    filtered.setdefault("job_id", d.get("id", job_id))
    filtered.setdefault("project_id", d.get("project_slug", ""))
    filtered.setdefault("kind", d.get("target_kind", "AI_ANALYSIS"))
    filtered.setdefault("state", d.get("status", "PENDING").upper())
    return JobRecord(**filtered)


# ---------------------------------------------------------------------------
# AI Analyses API
# ---------------------------------------------------------------------------

def record_ai_analysis(
    conn: sqlite3.Connection,
    analysis_id: str,
    project_id: str,
    target_type: str,
    target_id: str,
    prompt: str,
    response: str | None = None,
    model: str | None = None,
    state: str = "COMPLETED",
    error: str | None = None,
) -> AiAnalysisRecord:
    now = utc_now_iso()
    fin = now if state in ("COMPLETED", "FAILED") else None
    conn.execute(
        """
        INSERT INTO ai_analyses (analysis_id, project_id, target_type, target_id, prompt, response, model, state, created_at, finished_at, error)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (analysis_id, project_id, target_type, target_id, prompt, response, model, state, now, fin, error),
    )
    return AiAnalysisRecord(
        analysis_id=analysis_id,
        project_id=project_id,
        target_type=target_type,
        target_id=target_id,
        prompt=prompt,
        response=response,
        model=model,
        state=state,
        created_at=now,
        finished_at=fin,
        error=error,
    )


def get_ai_analysis(conn: sqlite3.Connection, analysis_id: str) -> AiAnalysisRecord | None:
    cur = conn.cursor()
    cols = {row[1] for row in cur.execute("PRAGMA table_info(ai_analyses)").fetchall()}
    if "analysis_id" in cols and "id" in cols:
        cur.execute("SELECT * FROM ai_analyses WHERE analysis_id = ? OR id = ?;", (analysis_id, analysis_id))
    elif "analysis_id" in cols:
        cur.execute("SELECT * FROM ai_analyses WHERE analysis_id = ?;", (analysis_id,))
    else:
        cur.execute("SELECT * FROM ai_analyses WHERE id = ?;", (analysis_id,))
    row = cur.fetchone()
    if not row:
        return None
    d = dict(row)
    fields = {"analysis_id", "project_id", "target_type", "target_id", "prompt", "response", "model", "state", "created_at", "finished_at", "error"}
    filtered = {k: v for k, v in d.items() if k in fields}
    filtered.setdefault("analysis_id", d.get("id", analysis_id))
    filtered.setdefault("project_id", d.get("project_slug", ""))
    filtered.setdefault("target_type", d.get("target_kind", "burst"))
    filtered.setdefault("target_id", d.get("target_id", ""))
    filtered.setdefault("prompt", "")
    return AiAnalysisRecord(**filtered)



# ---------------------------------------------------------------------------
# Controller Flags API
# ---------------------------------------------------------------------------

def set_controller_flag(
    conn: sqlite3.Connection,
    flag_name: str,
    enabled: bool,
    description: str | None = None,
) -> None:
    now = utc_now_iso()
    conn.execute(
        """
        INSERT INTO controller_flags (flag_name, enabled, description, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(flag_name) DO UPDATE SET
            enabled = excluded.enabled,
            description = COALESCE(excluded.description, controller_flags.description),
            updated_at = excluded.updated_at;
        """,
        (flag_name, 1 if enabled else 0, description, now),
    )


def get_controller_flag(conn: sqlite3.Connection, flag_name: str) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT enabled FROM controller_flags WHERE flag_name = ?;", (flag_name,))
    row = cur.fetchone()
    return bool(row[0]) if row else False


def enqueue_auto_push_job(
    conn: sqlite3.Connection,
    project_id: str,
    target_path: str,
    milestone_files: list[str],
    burst_id: int | None = None,
) -> JobRecord:
    """Enqueue an asynchronous auto-push job in engine.db for background controller execution."""
    import uuid
    payload = {
        "target_path": target_path,
        "milestone_files": milestone_files,
        "burst_id": burst_id,
    }
    job_id = f"job_push_{uuid.uuid4().hex[:8]}"
    return create_job(conn, job_id, project_id, kind="AUTO_PUSH", payload=payload)

