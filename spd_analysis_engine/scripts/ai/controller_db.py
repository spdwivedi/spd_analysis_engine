"""
scripts/ai/controller_db.py
===========================
Controller-owned database manager for durable `jobs` and `ai_analyses` in engine.db.
Supports atomic leasing, heartbeats, resume of killed jobs, and error tracking.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from collections.abc import Generator

from .identity import AnalysisStatus

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class EngineDB:
    """Controller SQLite database manager for engine.db."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        try:
            yield conn
        finally:
            conn.close()


    def _init_schema(self) -> None:
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    project_slug TEXT NOT NULL,
                    target_kind TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    level TEXT NOT NULL DEFAULT 'L1',
                    profile TEXT NOT NULL DEFAULT 'default',
                    analysis_id TEXT,
                    cache_key TEXT,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    max_retries INTEGER NOT NULL DEFAULT 3,
                    error_class TEXT,
                    error_message TEXT,
                    lease_owner TEXT,
                    lease_expires_at REAL,
                    last_heartbeat REAL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );
            """)
            existing_jobs = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
            for col, spec in (
                ("id", "TEXT"), ("project_slug", "TEXT"), ("target_kind", "TEXT"), ("target_id", "TEXT"),
                ("level", "TEXT DEFAULT 'L1'"), ("profile", "TEXT DEFAULT 'default'"), ("analysis_id", "TEXT"),
                ("cache_key", "TEXT"), ("status", "TEXT DEFAULT 'queued'"), ("retry_count", "INTEGER DEFAULT 0"),
                ("max_retries", "INTEGER DEFAULT 3"), ("error_class", "TEXT"), ("error_message", "TEXT"),
                ("lease_owner", "TEXT"), ("lease_expires_at", "REAL"), ("last_heartbeat", "REAL"),
                ("updated_at", "TEXT"), ("completed_at", "TEXT"),
            ):
                if col not in existing_jobs:
                    conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {spec};")

            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_project ON jobs(project_slug);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_target ON jobs(target_kind, target_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_lease ON jobs(status, lease_expires_at);")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS ai_analyses (
                    id TEXT PRIMARY KEY,
                    cache_key TEXT UNIQUE NOT NULL,
                    project_slug TEXT NOT NULL,
                    target_kind TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    level TEXT NOT NULL,
                    input_hash TEXT NOT NULL,
                    model TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    sanitizer_version TEXT NOT NULL,
                    context_hash TEXT NOT NULL,
                    parent_analysis_id TEXT,
                    status TEXT NOT NULL,
                    result_json TEXT,
                    prompt_tokens INTEGER DEFAULT 0,
                    completion_tokens INTEGER DEFAULT 0,
                    total_tokens INTEGER DEFAULT 0,
                    latency_ms REAL DEFAULT 0.0,
                    error_class TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );
            """)
            existing_ai = {row[1] for row in conn.execute("PRAGMA table_info(ai_analyses)").fetchall()}
            for col, spec in (
                ("id", "TEXT"), ("cache_key", "TEXT"), ("project_slug", "TEXT"), ("target_kind", "TEXT"),
                ("target_id", "TEXT"), ("profile", "TEXT"), ("level", "TEXT"), ("input_hash", "TEXT"),
                ("model", "TEXT"), ("provider", "TEXT"), ("prompt_version", "TEXT"), ("sanitizer_version", "TEXT"),
                ("context_hash", "TEXT"), ("parent_analysis_id", "TEXT"), ("status", "TEXT"),
                ("result_json", "TEXT"), ("prompt_tokens", "INTEGER DEFAULT 0"),
                ("completion_tokens", "INTEGER DEFAULT 0"), ("total_tokens", "INTEGER DEFAULT 0"),
                ("latency_ms", "REAL DEFAULT 0.0"), ("error_class", "TEXT"), ("error_message", "TEXT"),
                ("completed_at", "TEXT"),
            ):
                if col not in existing_ai:
                    conn.execute(f"ALTER TABLE ai_analyses ADD COLUMN {col} {spec};")

            conn.execute("CREATE INDEX IF NOT EXISTS idx_analyses_cache_key ON ai_analyses(cache_key);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_analyses_target ON ai_analyses(target_kind, target_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_analyses_project ON ai_analyses(project_slug);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_analyses_level ON ai_analyses(level);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_analyses_status ON ai_analyses(status);")
            conn.execute("COMMIT;")

    # -------------------------------------------------------------------------
    # Job Queue & Leasing Operations
    # -------------------------------------------------------------------------

    def enqueue_job(
        self,
        project_slug: str,
        target_kind: str,
        target_id: str,
        payload: dict[str, Any],
        level: str = "L1",
        profile: str = "default",
        cache_key: str | None = None,
        max_retries: int = 3,
        job_id: str | None = None,
    ) -> str:
        """Enqueue an analysis job in engine.db with 'queued' status."""
        jid = job_id or f"job_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        now = _utcnow_iso()
        payload_str = json.dumps(payload, ensure_ascii=False)

        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute(
                """
                INSERT INTO jobs (
                    id, project_slug, target_kind, target_id, level, profile,
                    analysis_id, cache_key, payload_json, status, retry_count,
                    max_retries, error_class, error_message, lease_owner,
                    lease_expires_at, last_heartbeat, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, 0, ?, NULL, NULL, NULL, NULL, NULL, ?, ?)
                """,
                (jid, project_slug, target_kind, target_id, level, profile, cache_key, payload_str, AnalysisStatus.QUEUED, max_retries, now, now),
            )
            conn.execute("COMMIT;")
        return jid

    def claim_job(self, worker_id: str, lease_duration_s: float = 30.0) -> dict[str, Any] | None:
        """Atomically claim eligible job (queued or expired running). Resumes killed jobs."""
        now_ts = time.time()
        lease_expires = now_ts + lease_duration_s
        now_iso = _utcnow_iso()

        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            cur = conn.execute(
                """
                SELECT id FROM jobs
                WHERE status = 'queued'
                   OR (status = 'running' AND lease_expires_at IS NOT NULL AND lease_expires_at < ?)
                ORDER BY CASE WHEN status = 'running' THEN 0 ELSE 1 END, created_at ASC
                LIMIT 1
                """,
                (now_ts,),
            )
            row = cur.fetchone()
            if not row:
                conn.execute("COMMIT;")
                return None

            candidate_id = row["id"]
            conn.execute(
                """
                UPDATE jobs
                SET status = 'running', lease_owner = ?, lease_expires_at = ?, last_heartbeat = ?, updated_at = ?
                WHERE id = ?
                """,
                (worker_id, lease_expires, now_ts, now_iso, candidate_id),
            )
            conn.execute("COMMIT;")

            cur = conn.execute("SELECT * FROM jobs WHERE id = ?", (candidate_id,))
            job_row = cur.fetchone()
            return dict(job_row) if job_row else None

    def heartbeat_job(self, job_id: str, worker_id: str, extend_s: float = 30.0) -> bool:
        """Update heartbeat and extend lease for actively running job."""
        now_ts = time.time()
        now_iso = _utcnow_iso()
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            cur = conn.execute(
                "UPDATE jobs SET last_heartbeat = ?, lease_expires_at = ?, updated_at = ? WHERE id = ? AND lease_owner = ? AND status = 'running'",
                (now_ts, now_ts + extend_s, now_iso, job_id, worker_id),
            )
            affected = cur.rowcount > 0
            conn.execute("COMMIT;")
            return affected

    def complete_job(self, job_id: str, analysis_id: str, completed_at: str | None = None) -> bool:
        """Mark job done and link created analysis."""
        done_time = completed_at or _utcnow_iso()
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            cur = conn.execute(
                "UPDATE jobs SET status = 'done', analysis_id = ?, completed_at = ?, lease_owner = NULL, lease_expires_at = NULL, updated_at = ? WHERE id = ?",
                (analysis_id, done_time, done_time, job_id),
            )
            affected = cur.rowcount > 0
            conn.execute("COMMIT;")
            return affected

    def fail_job(self, job_id: str, error_class: str, error_message: str, retry_count: int) -> bool:
        """Mark job failed."""
        now_iso = _utcnow_iso()
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            cur = conn.execute(
                "UPDATE jobs SET status = 'failed', error_class = ?, error_message = ?, retry_count = ?, completed_at = ?, lease_owner = NULL, lease_expires_at = NULL, updated_at = ? WHERE id = ?",
                (error_class, error_message, retry_count, now_iso, now_iso, job_id),
            )
            affected = cur.rowcount > 0
            conn.execute("COMMIT;")
            return affected

    def requeue_job(
        self,
        job_id: str,
        error_class: str,
        error_message: str,
        retry_count: int,
        payload_json: str | None = None,
    ) -> bool:
        """Re-queue job for an automatic retry."""
        now_iso = _utcnow_iso()
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            if payload_json is not None:
                cur = conn.execute(
                    "UPDATE jobs SET status = 'queued', error_class = ?, error_message = ?, retry_count = ?, payload_json = ?, lease_owner = NULL, lease_expires_at = NULL, updated_at = ? WHERE id = ?",
                    (error_class, error_message, retry_count, payload_json, now_iso, job_id),
                )
            else:
                cur = conn.execute(
                    "UPDATE jobs SET status = 'queued', error_class = ?, error_message = ?, retry_count = ?, lease_owner = NULL, lease_expires_at = NULL, updated_at = ? WHERE id = ?",
                    (error_class, error_message, retry_count, now_iso, job_id),
                )
            affected = cur.rowcount > 0
            conn.execute("COMMIT;")
            return affected

    def retry_job(self, job_id: str) -> bool:
        """Manual Retry action: resets failed job to queued with 0 retry_count."""
        now_iso = _utcnow_iso()
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            cur = conn.execute(
                "UPDATE jobs SET status = 'queued', retry_count = 0, error_class = NULL, error_message = NULL, lease_owner = NULL, lease_expires_at = NULL, completed_at = NULL, updated_at = ? WHERE id = ?",
                (now_iso, job_id),
            )
            affected = cur.rowcount > 0
            conn.execute("COMMIT;")
            return affected

    def supersede_target_jobs(self, target_kind: str, target_id: str, except_job_id: str | None = None) -> int:
        """Mark uncompleted jobs for a target as superseded."""
        now_iso = _utcnow_iso()
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            q = "UPDATE jobs SET status = 'superseded', updated_at = ? WHERE target_kind = ? AND target_id = ? AND status IN ('queued', 'running')"
            params: list[Any] = [now_iso, target_kind, target_id]
            if except_job_id:
                q += " AND id != ?"
                params.append(except_job_id)
            cur = conn.execute(q, params)
            count = cur.rowcount
            conn.execute("COMMIT;")
            return count

    def reclaim_expired_leases(self) -> int:
        """Reclaim expired running job leases back to queued (for killed controller recovery)."""
        now_ts = time.time()
        now_iso = _utcnow_iso()
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            cur = conn.execute(
                "UPDATE jobs SET status = 'queued', lease_owner = NULL, lease_expires_at = NULL, updated_at = ? WHERE status = 'running' AND lease_expires_at IS NOT NULL AND lease_expires_at < ?",
                (now_iso, now_ts),
            )
            count = cur.rowcount
            conn.execute("COMMIT;")
            return count

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._get_connection() as conn:
            cur = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    # -------------------------------------------------------------------------
    # AI Analysis Durable Storage
    # -------------------------------------------------------------------------

    def record_analysis(self, data: dict[str, Any]) -> str:
        """
        Record or update an AI analysis in `ai_analyses`.
        RULE: Never store an AI failure as a successful result.
        """
        aid = data.get("id") or f"ana_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        status = data.get("status", AnalysisStatus.DONE)
        is_failure = (status == AnalysisStatus.FAILED)

        # Enforce rule: failures never store a result_json
        result_json = None if is_failure else (
            json.dumps(data["result_json"]) if isinstance(data.get("result_json"), (dict, list)) else data.get("result_json")
        )

        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute(
                """
                INSERT INTO ai_analyses (
                    id, cache_key, project_slug, target_kind, target_id,
                    profile, level, input_hash, model, provider, prompt_version,
                    sanitizer_version, context_hash, parent_analysis_id, status,
                    result_json, prompt_tokens, completion_tokens, total_tokens,
                    latency_ms, error_class, error_message, created_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    status = excluded.status,
                    result_json = excluded.result_json,
                    prompt_tokens = excluded.prompt_tokens,
                    completion_tokens = excluded.completion_tokens,
                    total_tokens = excluded.total_tokens,
                    latency_ms = excluded.latency_ms,
                    error_class = excluded.error_class,
                    error_message = excluded.error_message,
                    completed_at = excluded.completed_at
                """,
                (
                    aid, data["cache_key"], data.get("project_slug", ""), data["target_kind"], data["target_id"],
                    data.get("profile", "default"), data.get("level", "L1"), data.get("input_hash", ""),
                    data.get("model", ""), data.get("provider", ""), data.get("prompt_version", "v1"),
                    data.get("sanitizer_version", "v1"), data.get("context_hash", ""), data.get("parent_analysis_id"),
                    status, result_json, int(data.get("prompt_tokens", 0)), int(data.get("completion_tokens", 0)),
                    int(data.get("total_tokens", 0)), float(data.get("latency_ms", 0.0)), data.get("error_class"),
                    data.get("error_message"), data.get("created_at") or _utcnow_iso(),
                    data.get("completed_at") or (_utcnow_iso() if status in (AnalysisStatus.DONE, AnalysisStatus.FAILED) else None),
                ),
            )
            conn.execute("COMMIT;")
        return aid

    def get_analysis_by_cache_key(self, cache_key: str) -> dict[str, Any] | None:
        """Fetch cached analysis by canonical 9-component cache key."""
        with self._get_connection() as conn:
            cur = conn.execute("SELECT * FROM ai_analyses WHERE cache_key = ?", (cache_key,))
            row = cur.fetchone()
            if not row:
                return None
            res = dict(row)
            if res.get("result_json"):
                try:
                    res["result"] = json.loads(res["result_json"])
                except Exception:
                    res["result"] = res["result_json"]
            return res

    def get_analysis(self, analysis_id: str) -> dict[str, Any] | None:
        with self._get_connection() as conn:
            cur = conn.execute("SELECT * FROM ai_analyses WHERE id = ?", (analysis_id,))
            row = cur.fetchone()
            if not row:
                return None
            res = dict(row)
            if res.get("result_json"):
                try:
                    res["result"] = json.loads(res["result_json"])
                except Exception:
                    res["result"] = res["result_json"]
            return res

    def get_analyses_for_target(self, target_kind: str, target_id: str, level: str | None = None) -> list[dict[str, Any]]:
        """Return all analyses for a target, optionally filtered by level."""
        with self._get_connection() as conn:
            q = "SELECT * FROM ai_analyses WHERE target_kind = ? AND target_id = ?"
            params: list[Any] = [target_kind, target_id]
            if level:
                q += " AND level = ?"
                params.append(level)
            q += " ORDER BY created_at ASC"
            cur = conn.execute(q, params)
            results = []
            for r in cur.fetchall():
                item = dict(r)
                if item.get("result_json"):
                    try:
                        item["result"] = json.loads(item["result_json"])
                    except Exception:
                        item["result"] = item["result_json"]
                results.append(item)
            return results
