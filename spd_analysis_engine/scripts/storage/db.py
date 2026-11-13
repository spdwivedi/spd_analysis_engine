"""SessionDB class: SQLite WAL-mode database manager for the SPD Analysis Engine."""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from collections.abc import Generator

from .schema_migration import CURRENT_SCHEMA_VERSION, apply_schema_migrations
from .utils import _utcnow_iso
def check_fault(name: str) -> None:
    from ..core.faults import check_fault as _cf
    _cf(name)

logger = logging.getLogger(__name__)


class SessionDB:
    """Manages a SQLite database in WAL mode for a single project session."""

    def __init__(self, db_path: Path | str) -> None:
        self._path = Path(db_path)
        self._lock = threading.RLock()
        self._savepoint_depth: int = 0
        self._local = threading.local()
        self._conn: sqlite3.Connection | None = None
        self._open()

    @property
    def conn(self) -> sqlite3.Connection | None:
        """Return the underlying SQLite connection."""
        return self._conn

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _open(self) -> None:
        """Open (or create) the SQLite database and apply versioned migrations."""
        logger.debug("Opening session DB at %s", self._path)
        self._conn = sqlite3.connect(
            str(self._path),
            check_same_thread=False,
            isolation_level=None,   # autocommit; we manage transactions manually
            timeout=30.0,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout = 30000;")
        self._conn.execute("PRAGMA journal_mode = WAL;")
        self._conn.execute("PRAGMA synchronous = NORMAL;")
        self._conn.execute("PRAGMA foreign_keys = ON;")
        apply_schema_migrations(self._conn)
        logger.info("SessionDB ready: %s", self._path)

    # ------------------------------------------------------------------
    # Transaction, execution, and read-only helpers
    # ------------------------------------------------------------------

    def open_ro_connection(self) -> sqlite3.Connection:
        """
        Open and return an independent read-only SQLite connection in WAL mode.
        Allows concurrent readers without competing for writer locks.
        """
        db_uri = self._path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(db_uri, uri=True, timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.execute("PRAGMA query_only = ON;")
        return conn

    def _get_ro_conn(self) -> sqlite3.Connection:
        """Get or create a thread-local read-only connection."""
        conn = getattr(self._local, "ro_conn", None)
        if conn is None:
            conn = self.open_ro_connection()
            self._local.ro_conn = conn
        return conn

    def _begin_immediate(self) -> None:
        """Begin immediate transaction with exponential backoff on busy."""
        assert self._conn is not None
        for attempt in range(5):
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                return
            except sqlite3.OperationalError:
                if attempt == 4:
                    raise
                time.sleep(0.02 * (2 ** attempt))

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Cursor, None, None]:
        """
        Thread-safe transaction context manager protected by an RLock.
        Commits on exit, rolls back on exception.
        Nested transactions within the same thread use savepoints.
        """
        with self._lock:
            assert self._conn is not None, "Database is closed"
            self._savepoint_depth += 1
            sp_name = f"sp_{self._savepoint_depth}"
            if self._savepoint_depth == 1:
                self._begin_immediate()
            else:
                self._conn.execute(f"SAVEPOINT {sp_name}")
            try:
                cur = self._conn.cursor()
                check_fault("inside_db_transaction")
                yield cur
                if self._savepoint_depth == 1:
                    self._conn.execute("COMMIT")
                else:
                    self._conn.execute(f"RELEASE SAVEPOINT {sp_name}")
            except Exception:
                if self._savepoint_depth == 1:
                    try:
                        self._conn.execute("ROLLBACK")
                    except Exception:
                        pass
                else:
                    try:
                        self._conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
                        self._conn.execute(f"RELEASE SAVEPOINT {sp_name}")
                    except Exception:
                        pass
                raise
            finally:
                self._savepoint_depth -= 1

    def _execute(
        self,
        sql: str,
        params: tuple[Any, ...] = (),
        *,
        commit: bool = True,
    ) -> sqlite3.Cursor:
        """Execute a statement protected by the shared RLock."""
        with self._lock:
            assert self._conn is not None, "Database is closed"
            if self._savepoint_depth > 0:
                cur = self._conn.cursor()
                cur.execute(sql, params)
                return cur

            if not commit:
                cur = self._conn.cursor()
                cur.execute(sql, params)
                return cur

            with self.transaction() as cur:
                cur.execute(sql, params)
                return cur

    # ------------------------------------------------------------------
    # Public API – sessions
    # ------------------------------------------------------------------

    def create_session(
        self,
        project_name: str,
        target_path: str,
        end_reason: str | None = None,
        worker_id: str | None = None,
        config_json: str | None = None,
        actor: str | None = None,
    ) -> int:
        """
        Insert a new ``ACTIVE`` session row and return its primary key.
        If the last session for this project has 0 events, reuse the session ID.
        """
        with self.transaction() as cur:
            cur.execute("SELECT id FROM sessions ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
            if row:
                latest_id = int(row["id"])
                cur.execute("SELECT COUNT(*) FROM events WHERE session_id = ?", (latest_id,))
                evt_count_row = cur.fetchone()
                if evt_count_row and evt_count_row[0] == 0:
                    cur.execute(
                        "UPDATE sessions SET project_name = ?, target_path = ?, start_time = ?, status = 'ACTIVE', "
                        "end_reason = ?, worker_id = ?, config_json = ?, actor = ? WHERE id = ?",
                        (project_name, target_path, _utcnow_iso(), end_reason, worker_id, config_json, actor, latest_id),
                    )
                    logger.info("Reusing empty session id=%s for project '%s'", latest_id, project_name)
                    return latest_id

            cur.execute(
                "INSERT INTO sessions (project_name, target_path, start_time, status, end_reason, worker_id, config_json, actor) "
                "VALUES (?, ?, ?, 'ACTIVE', ?, ?, ?, ?)",
                (project_name, target_path, _utcnow_iso(), end_reason, worker_id, config_json, actor),
            )
            sid = cur.lastrowid
            logger.info("Created session id=%s for project '%s'", sid, project_name)
            return sid  # type: ignore[return-value]

    def close_session(self, session_id: int, status: str = "STOPPED", end_reason: str | None = None) -> None:
        """Mark a session as finished by setting ``end_time``, ``status``, and optional ``end_reason``."""
        if end_reason:
            self._execute(
                "UPDATE sessions SET end_time = ?, status = ?, end_reason = ? WHERE id = ?",
                (_utcnow_iso(), status, end_reason, session_id),
            )
        else:
            self._execute(
                "UPDATE sessions SET end_time = ?, status = ? WHERE id = ?",
                (_utcnow_iso(), status, session_id),
            )
        logger.info("Closed session id=%s with status=%s", session_id, status)

    def get_session(self, session_id: int) -> sqlite3.Row | None:
        """Return the row for *session_id*, or ``None`` if not found."""
        if self._savepoint_depth > 0:
            with self._lock:
                assert self._conn is not None
                return self._conn.cursor().execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        try:
            return self._get_ro_conn().execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        except Exception:
            with self._lock:
                assert self._conn is not None
                return self._conn.cursor().execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()

    # ------------------------------------------------------------------
    # Public API – events
    # ------------------------------------------------------------------

    def record_event(
        self,
        session_id: int,
        event_type: str,
        first_file: str | None = None,
        summary: str | None = None,
        executor: str | None = None,
        commit_hash: str | None = None,
        is_rolled_back: int = 0,
        burst_num: int | None = None,
        event_id: str | None = None,
        seq: int | None = None,
        burst_uid: str | None = None,
        effective_files: str | None = None,
    ) -> int:
        """
        Insert a new event row and return its primary key.
        Deduplicates identical READ events for the same file in a session.
        Uses canonical event_id (generates stable evt_ identity if not provided).
        """
        valid_types = {"EDIT", "READ", "EXEC", "GIT"}
        if event_type not in valid_types:
            raise ValueError(f"event_type must be one of {valid_types}, got {event_type!r}")

        if not event_id:
            event_id = f"evt_{uuid.uuid4().hex[:16]}"

        with self.transaction() as cur:
            if event_type == "READ" and first_file:
                cur.execute(
                    "SELECT id FROM events WHERE session_id = ? AND event_type = 'READ' AND first_file_touched = ? LIMIT 1",
                    (session_id, first_file),
                )
                existing = cur.fetchone()
                if existing:
                    return int(existing["id"])

            if seq is None:
                cur.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM events WHERE session_id = ?", (session_id,))
                seq_row = cur.fetchone()
                seq = int(seq_row[0]) if seq_row else 1

            cur.execute(
                "INSERT INTO events (session_id, event_type, timestamp, first_file_touched, summary, executor, commit_hash, is_rolled_back, burst_num, event_id, seq, burst_uid, effective_files) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (session_id, event_type, _utcnow_iso(), first_file, summary, executor, commit_hash, is_rolled_back, burst_num, event_id, seq, burst_uid, effective_files),
            )
            eid = cur.lastrowid
            logger.debug("Recorded event id=%s event_id=%s type=%s file=%s executor=%s commit=%s", eid, event_id, event_type, first_file, executor, commit_hash)
            return eid  # type: ignore[return-value]

    def get_event_by_id(self, event_identifier: str | int) -> sqlite3.Row | None:
        """
        Resolve an event by its canonical event_id or legacy integer id.
        Burst numbers are display-only and must never resolve an event.
        """
        if isinstance(event_identifier, str) and not event_identifier.isdigit():
            q = "SELECT * FROM events WHERE event_id = ? LIMIT 1"
            params: tuple[Any, ...] = (event_identifier,)
        else:
            val_str = str(event_identifier)
            val_int = int(event_identifier)
            q = "SELECT * FROM events WHERE event_id = ? OR id = ? ORDER BY CASE WHEN event_id = ? THEN 0 ELSE 1 END LIMIT 1"
            params = (val_str, val_int, val_str)

        if self._savepoint_depth > 0:
            with self._lock:
                assert self._conn is not None
                return self._conn.cursor().execute(q, params).fetchone()
        try:
            return self._get_ro_conn().execute(q, params).fetchone()
        except Exception:
            with self._lock:
                assert self._conn is not None
                return self._conn.cursor().execute(q, params).fetchone()

    def get_events(self, session_id: int) -> list[sqlite3.Row]:
        """Return all events for the given session, ordered by timestamp."""
        q = "SELECT * FROM events WHERE session_id = ? ORDER BY timestamp"
        if self._savepoint_depth > 0:
            with self._lock:
                assert self._conn is not None
                return self._conn.cursor().execute(q, (session_id,)).fetchall()
        try:
            return self._get_ro_conn().execute(q, (session_id,)).fetchall()
        except Exception:
            with self._lock:
                assert self._conn is not None
                return self._conn.cursor().execute(q, (session_id,)).fetchall()

    def update_event_ai_summary(self, event_id: int, ai_summary: str) -> None:
        """Update the AI-generated intent and functional explanation for an event."""
        self._execute(
            "UPDATE events SET ai_summary = ? WHERE id = ?",
            (ai_summary, event_id),
        )
        logger.debug("Updated AI summary for event id=%s", event_id)

    # ------------------------------------------------------------------
    # Public API – patches
    # ------------------------------------------------------------------

    def record_patch(
        self,
        event_id: int,
        file_path: str,
        diff_content: str | None = None,
        adds: int = 0,
        dels: int = 0,
        kind: str = "unified",
        size: int = 0,
        hash_before: str | None = None,
        hash_after: str | None = None,
        truncated: int = 0,
    ) -> int:
        """Store a raw diff associated with an EDIT event."""
        if size == 0 and diff_content:
            size = len(diff_content)
        with self.transaction() as cur:
            cur.execute(
                "INSERT INTO patches (event_id, file_path, diff_content, adds, dels, kind, size, hash_before, hash_after, truncated) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (event_id, file_path, diff_content, adds, dels, kind, size, hash_before, hash_after, truncated),
            )
            pid = cur.lastrowid
            logger.debug("Recorded patch id=%s for event id=%s", pid, event_id)
            return pid  # type: ignore[return-value]

    def get_patches(self, event_id: int) -> list[sqlite3.Row]:
        """Return all patches associated with *event_id*."""
        q = "SELECT * FROM patches WHERE event_id = ? ORDER BY id"
        if self._savepoint_depth > 0:
            with self._lock:
                assert self._conn is not None
                return self._conn.cursor().execute(q, (event_id,)).fetchall()
        try:
            return self._get_ro_conn().execute(q, (event_id,)).fetchall()
        except Exception:
            with self._lock:
                assert self._conn is not None
                return self._conn.cursor().execute(q, (event_id,)).fetchall()

    def update_patch_diff(self, event_id: int, file_path: str, diff_content: str) -> None:
        """Update or set diff_content for a patch row identified by event_id and file_path."""
        self._execute(
            "UPDATE patches SET diff_content = ? WHERE event_id = ? AND file_path = ?",
            (diff_content, event_id, file_path),
        )
        logger.debug("Updated delta diff for event id=%s file=%s", event_id, file_path)

    def purge_ignored_events(self) -> int:
        """Purge internal IDE EXEC noise rows from the events table."""
        try:
            from spd_analysis_engine.scripts.shell_interceptor import is_ignored_command
        except Exception:
            return 0

        with self.transaction() as cur:
            cur.execute("SELECT id, first_file_touched, summary FROM events WHERE event_type = 'EXEC'")
            rows = cur.fetchall()
            to_delete = [
                r["id"] for r in rows
                if is_ignored_command(r["first_file_touched"] or "") or is_ignored_command(r["summary"] or "")
            ]
            if to_delete:
                q = f"DELETE FROM events WHERE id IN ({','.join('?' for _ in to_delete)})"
                cur.execute(q, tuple(to_delete))
                logger.info("Purged %d internal IDE EXEC noise rows from session.db", len(to_delete))
            return len(to_delete)

    def record_git_op_state(
        self, event_id: int, burst_uid: str, status: str, files: list[str], effective_files: list[str]
    ) -> int | None:
        """Record initial git operation into git_ops table for crash recovery."""
        try:
            now = _utcnow_iso()
            cur = self._execute(
                "INSERT INTO git_ops (event_id, burst_uid, status, files_json, effective_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (event_id, burst_uid, status, json.dumps(files), json.dumps(effective_files), now, now),
            )
            return int(cur.lastrowid)
        except Exception as exc:
            logger.debug("Error recording git_op state: %s", exc)
            return None

    def update_git_op_state(
        self, op_id: int | None, status: str, commit_hash: str | None = None, error_message: str | None = None
    ) -> None:
        """Update git operation status in git_ops table."""
        if op_id is None:
            return
        try:
            now = _utcnow_iso()
            self._execute(
                "UPDATE git_ops SET status = ?, commit_hash = ?, error_message = ?, updated_at = ? WHERE id = ?",
                (status, commit_hash, error_message, now, op_id),
            )
        except Exception as exc:
            logger.debug("Error updating git_op state: %s", exc)

    def update_event_git_attribution(self, event_id: int, commit_hash: str | None, git_state: str) -> None:
        """Update commit_hash and git_state on an event under SessionDB transaction."""
        try:
            self._execute(
                "UPDATE events SET commit_hash = ?, git_state = ? WHERE id = ?",
                (commit_hash, git_state, event_id),
            )
        except Exception as exc:
            logger.debug("Could not link commit_hash to burst event %s: %s", event_id, exc)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def checkpoint(self) -> None:
        """Force a WAL checkpoint in TRUNCATE mode."""
        with self._lock:
            if self._conn is None:
                return
            for attempt in range(3):
                try:
                    self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    logger.debug("WAL checkpoint completed for %s", self._path)
                    return
                except sqlite3.OperationalError as exc:
                    if attempt == 2:
                        logger.warning("WAL checkpoint failed on busy: %s", exc)
                    time.sleep(0.05 * (2 ** attempt))
                except sqlite3.Error as exc:
                    logger.warning("WAL checkpoint failed: %s", exc)
                    return

    def close(self) -> None:
        """Checkpoint the WAL, then close the database connection."""
        with self._lock:
            self.checkpoint()
            if hasattr(self._local, "ro_conn") and self._local.ro_conn is not None:
                try:
                    self._local.ro_conn.close()
                except Exception:
                    pass
                self._local.ro_conn = None
            if self._conn is not None:
                self._conn.close()
                self._conn = None
                logger.info("SessionDB closed: %s", self._path)

    def __enter__(self) -> "SessionDB":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

