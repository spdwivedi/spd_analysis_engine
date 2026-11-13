"""
scripts/controller/registry.py
==============================
Authoritative SQLite metadata database (engine.db) managing versioned schema
for projects, workers, operations, and engine_meta.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from spd_analysis_engine.scripts.core.time_utils import utc_now_iso
from .lifecycle import ProjectState, transition_state

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1"


@dataclass
class ProjectRecord:
    project_id: str
    slug: str
    display_name: str
    root_path: str
    state: str
    config_json: str | None
    created_at: str
    updated_at: str


@dataclass
class WorkerRecord:
    worker_id: str
    project_id: str
    pid: int | None
    process_create_time: float | None
    canonical_target: str | None
    engine_root: str | None
    state: str
    started_at: str
    last_heartbeat: str | None
    exit_code: int | None
    end_reason: str | None


@dataclass
class OperationRecord:
    operation_id: str
    project_id: str
    worker_id: str | None
    kind: str
    state: str
    requested_at: str
    started_at: str | None
    finished_at: str | None
    error: str | None


class EngineRegistry:
    """Thread-safe SQLite metadata registry for engine.db with versioned schema."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=10.0,
            check_same_thread=False,
            isolation_level=None,  # Autocommit mode, explicit transactions
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _init_db(self) -> None:
        """Create tables if they do not exist and verify schema version."""
        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    conn.executescript("""
                        CREATE TABLE IF NOT EXISTS engine_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                        CREATE TABLE IF NOT EXISTS projects (
                            project_id TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
                            root_path TEXT NOT NULL, state TEXT NOT NULL, config_json TEXT,
                            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                        );
                        CREATE TABLE IF NOT EXISTS workers (
                            worker_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, pid INTEGER,
                            process_create_time REAL, canonical_target TEXT, engine_root TEXT,
                            state TEXT NOT NULL, started_at TEXT NOT NULL, last_heartbeat TEXT,
                            exit_code INTEGER, end_reason TEXT,
                            FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE CASCADE
                        );
                        CREATE TABLE IF NOT EXISTS operations (
                            operation_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, worker_id TEXT,
                            kind TEXT NOT NULL, state TEXT NOT NULL, requested_at TEXT NOT NULL,
                            started_at TEXT, finished_at TEXT, error TEXT,
                            FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE CASCADE
                        );
                        CREATE INDEX IF NOT EXISTS idx_workers_project_id ON workers(project_id);
                        CREATE INDEX IF NOT EXISTS idx_workers_state ON workers(state);
                        CREATE INDEX IF NOT EXISTS idx_operations_project_id ON operations(project_id);
                        CREATE INDEX IF NOT EXISTS idx_operations_state ON operations(state);
                    """)
                    from .jobs import init_controller_tables
                    init_controller_tables(conn)
                    row = conn.execute("SELECT value FROM engine_meta WHERE key = 'schema_version'").fetchone()
                    if row is None:
                        now = utc_now_iso()
                        conn.execute("INSERT INTO engine_meta (key, value) VALUES ('schema_version', ?)", (SCHEMA_VERSION,))
                        conn.execute("INSERT INTO engine_meta (key, value) VALUES ('created_at', ?)", (now,))
            finally:
                conn.close()

    # -------------------------------------------------------------------------
    # Metadata
    # -------------------------------------------------------------------------

    def get_meta(self, key: str) -> str | None:
        with self._lock:
            conn = self._get_connection()
            try:
                row = conn.execute("SELECT value FROM engine_meta WHERE key = ?", (key,)).fetchone()
                return row["value"] if row else None
            finally:
                conn.close()

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    conn.execute(
                        "INSERT INTO engine_meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (key, str(value)),
                    )
            finally:
                conn.close()

    # -------------------------------------------------------------------------
    # Projects
    # -------------------------------------------------------------------------

    def upsert_project(
        self,
        project_id: str,
        slug: str,
        display_name: str,
        root_path: str,
        state: str = ProjectState.STOPPED.value,
        config_json: str | None = None,
    ) -> ProjectRecord:
        with self._lock:
            now = utc_now_iso()
            conn = self._get_connection()
            try:
                with conn:
                    existing = conn.execute("SELECT project_id FROM projects WHERE slug = ?", (slug,)).fetchone()
                    target_pid = existing["project_id"] if existing else project_id
                    conn.execute(
                        """
                        INSERT INTO projects (project_id, slug, display_name, root_path, state, config_json, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(project_id) DO UPDATE SET
                            slug = excluded.slug,
                            display_name = excluded.display_name,
                            root_path = excluded.root_path,
                            config_json = COALESCE(excluded.config_json, projects.config_json),
                            updated_at = excluded.updated_at;
                        """,
                        (target_pid, slug, display_name, str(root_path), state, config_json, now, now),
                    )
                return self.get_project(target_pid)  # type: ignore
            finally:
                conn.close()

    def get_project(self, project_id: str) -> ProjectRecord | None:
        with self._lock:
            conn = self._get_connection()
            try:
                row = conn.execute("SELECT * FROM projects WHERE project_id = ?", (project_id,)).fetchone()
                if not row:
                    return None
                return ProjectRecord(**dict(row))
            finally:
                conn.close()

    def get_project_by_slug(self, slug: str) -> ProjectRecord | None:
        with self._lock:
            conn = self._get_connection()
            try:
                row = conn.execute("SELECT * FROM projects WHERE slug = ?", (slug,)).fetchone()
                if not row:
                    return None
                return ProjectRecord(**dict(row))
            finally:
                conn.close()

    def get_project_by_path(self, root_path: str) -> ProjectRecord | None:
        norm_path = str(Path(root_path).resolve())
        with self._lock:
            conn = self._get_connection()
            try:
                # Direct match or path comparison
                for row in conn.execute("SELECT * FROM projects").fetchall():
                    if str(Path(row["root_path"]).resolve()) == norm_path:
                        return ProjectRecord(**dict(row))
                return None
            finally:
                conn.close()

    def list_projects(self) -> list[ProjectRecord]:
        with self._lock:
            conn = self._get_connection()
            try:
                rows = conn.execute("SELECT * FROM projects ORDER BY created_at ASC").fetchall()
                return [ProjectRecord(**dict(r)) for r in rows]
            finally:
                conn.close()

    def transition_project_state(self, project_id: str, target_state: str) -> ProjectRecord:
        """
        Authoritative transition: validates transition via transition_state()
        and updates projects table.
        """
        with self._lock:
            conn = self._get_connection()
            try:
                row = conn.execute("SELECT state FROM projects WHERE project_id = ?", (project_id,)).fetchone()
                if not row:
                    raise KeyError(f"Project '{project_id}' not found in registry")
                current_state = row["state"]
                # Must go through lifecycle validator
                new_state = transition_state(current_state, target_state, project_id=project_id)

                now = utc_now_iso()
                with conn:
                    conn.execute(
                        "UPDATE projects SET state = ?, updated_at = ? WHERE project_id = ?",
                        (new_state, now, project_id),
                    )
                return self.get_project(project_id)  # type: ignore
            finally:
                conn.close()

    def delete_project(self, project_id: str) -> bool:
        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    cursor = conn.execute("DELETE FROM projects WHERE project_id = ?", (project_id,))
                    return cursor.rowcount > 0
            finally:
                conn.close()

    # -------------------------------------------------------------------------
    # Workers
    # -------------------------------------------------------------------------

    def record_worker(
        self,
        worker_id: str,
        project_id: str,
        pid: int | None = None,
        process_create_time: float | None = None,
        canonical_target: str | None = None,
        engine_root: str | None = None,
        state: str = ProjectState.STARTING.value,
        started_at: str | None = None,
    ) -> WorkerRecord:
        with self._lock:
            now = started_at or utc_now_iso()
            conn = self._get_connection()
            try:
                with conn:
                    conn.execute(
                        """
                        INSERT INTO workers (
                            worker_id, project_id, pid, process_create_time,
                            canonical_target, engine_root, state, started_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (worker_id, project_id, pid, process_create_time, canonical_target, engine_root, state, now),
                    )
                return self.get_worker(worker_id)  # type: ignore
            finally:
                conn.close()

    def get_worker(self, worker_id: str) -> WorkerRecord | None:
        with self._lock:
            conn = self._get_connection()
            try:
                row = conn.execute("SELECT * FROM workers WHERE worker_id = ?", (worker_id,)).fetchone()
                if not row:
                    return None
                return WorkerRecord(**dict(row))
            finally:
                conn.close()

    def get_active_worker(self, project_id: str) -> WorkerRecord | None:
        """Return the most recent active/indexing/starting worker for project."""
        with self._lock:
            conn = self._get_connection()
            try:
                row = conn.execute(
                    """
                    SELECT * FROM workers
                    WHERE project_id = ? AND state IN ('STARTING', 'INDEXING', 'ACTIVE', 'UNRESPONSIVE', 'STOPPING')
                    ORDER BY started_at DESC LIMIT 1;
                    """,
                    (project_id,),
                ).fetchone()
                if not row:
                    return None
                return WorkerRecord(**dict(row))
            finally:
                conn.close()

    def get_workers_for_project(self, project_id: str) -> list[WorkerRecord]:
        with self._lock:
            conn = self._get_connection()
            try:
                rows = conn.execute(
                    "SELECT * FROM workers WHERE project_id = ? ORDER BY started_at DESC",
                    (project_id,),
                ).fetchall()
                return [WorkerRecord(**dict(r)) for r in rows]
            finally:
                conn.close()

    def update_worker_state(
        self,
        worker_id: str,
        state: str,
        last_heartbeat: str | None = None,
        exit_code: int | None = None,
        end_reason: str | None = None,
    ) -> WorkerRecord:
        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    conn.execute(
                        """
                        UPDATE workers SET
                            state = ?,
                            last_heartbeat = COALESCE(?, last_heartbeat),
                            exit_code = COALESCE(?, exit_code),
                            end_reason = COALESCE(?, end_reason)
                        WHERE worker_id = ?;
                        """,
                        (state, last_heartbeat, exit_code, end_reason, worker_id),
                    )
                return self.get_worker(worker_id)  # type: ignore
            finally:
                conn.close()

    def update_worker_heartbeat(self, worker_id: str, last_heartbeat: str) -> WorkerRecord:
        worker = self.get_worker(worker_id)
        current_state = worker.state if worker else ProjectState.ACTIVE.value
        return self.update_worker_state(worker_id, state=current_state, last_heartbeat=last_heartbeat)

    # -------------------------------------------------------------------------
    # Operations
    # -------------------------------------------------------------------------

    def create_operation(
        self,
        operation_id: str,
        project_id: str,
        kind: str,
        worker_id: str | None = None,
        state: str = "pending",
    ) -> OperationRecord:
        with self._lock:
            now = utc_now_iso()
            conn = self._get_connection()
            try:
                with conn:
                    conn.execute(
                        """
                        INSERT INTO operations (
                            operation_id, project_id, worker_id, kind, state, requested_at
                        ) VALUES (?, ?, ?, ?, ?, ?);
                        """,
                        (operation_id, project_id, worker_id, kind, state, now),
                    )
                return self.get_operation(operation_id)  # type: ignore
            finally:
                conn.close()

    def get_operation(self, operation_id: str) -> OperationRecord | None:
        with self._lock:
            conn = self._get_connection()
            try:
                row = conn.execute("SELECT * FROM operations WHERE operation_id = ?", (operation_id,)).fetchone()
                if not row:
                    return None
                return OperationRecord(**dict(row))
            finally:
                conn.close()

    def update_operation_state(
        self,
        operation_id: str,
        state: str | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
        error: str | None = None,
        worker_id: str | None = None,
    ) -> OperationRecord:
        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    conn.execute(
                        """
                        UPDATE operations SET
                            state = COALESCE(?, state),
                            started_at = COALESCE(?, started_at),
                            finished_at = COALESCE(?, finished_at),
                            error = COALESCE(?, error),
                            worker_id = COALESCE(?, worker_id)
                        WHERE operation_id = ?;
                        """,
                        (state, started_at, finished_at, error, worker_id, operation_id),
                    )
                return self.get_operation(operation_id)  # type: ignore
            finally:
                conn.close()

    def list_operations_for_project(self, project_id: str) -> list[OperationRecord]:
        with self._lock:
            conn = self._get_connection()
            try:
                rows = conn.execute(
                    "SELECT * FROM operations WHERE project_id = ? ORDER BY requested_at DESC",
                    (project_id,),
                ).fetchall()
                return [OperationRecord(**dict(r)) for r in rows]
            finally:
                conn.close()
