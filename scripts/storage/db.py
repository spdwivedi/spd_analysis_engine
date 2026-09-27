"""
scripts/storage/db.py
=====================
SessionDB class: SQLite WAL-mode database manager for the SPD Analysis Engine.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

try:
    from .utils import _utcnow_iso
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.storage.utils import _utcnow_iso
    except (ImportError, ModuleNotFoundError):
        from scripts.storage.utils import _utcnow_iso

logger = logging.getLogger(__name__)


class SessionDB:
    """
    Manages a SQLite database for a single project session.

    The database lives at ``<project_current_dir>/session.db`` and is opened
    in WAL journal mode so concurrent readers and a single writer can coexist
    without blocking each other, and so that an abrupt process death cannot
    corrupt already-committed data.

    Schema
    ------
    sessions  – one row per monitoring session (start → stop lifecycle)
    events    – file-system / git events captured during a session
    patches   – raw diff content associated with EDIT events

    Parameters
    ----------
    db_path : Path | str
        Absolute path to the ``session.db`` file that will be created or
        opened.  Parent directories must already exist.

    Usage
    -----
    ::

        db = SessionDB(Path("current/my_proj/session.db"))
        sid = db.create_session("my_proj", "/workspace/my_proj")
        eid = db.record_event(sid, "EDIT", "main.py", "Modified main()")
        db.record_patch(eid, "main.py", "- old\\n+ new")
        db.close_session(sid)
        db.close()
    """

    # DDL statements executed once on first open
    _SCHEMA_SQL = """
    PRAGMA journal_mode = WAL;
    PRAGMA synchronous   = NORMAL;
    PRAGMA foreign_keys  = ON;

    CREATE TABLE IF NOT EXISTS sessions (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        project_name TEXT    NOT NULL,
        target_path  TEXT    NOT NULL,
        start_time   TEXT    NOT NULL,
        end_time     TEXT,
        status       TEXT    NOT NULL DEFAULT 'ACTIVE'
        -- status ∈ {ACTIVE, STOPPED, CRASHED}
    );

    CREATE TABLE IF NOT EXISTS events (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id        INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        event_type        TEXT    NOT NULL,
        -- event_type ∈ {EDIT, READ, EXEC, GIT}
        timestamp         TEXT    NOT NULL,
        first_file_touched TEXT,
        summary           TEXT,
        ai_summary        TEXT,
        executor          TEXT,
        burst_num         INTEGER,
        commit_hash       TEXT,
        is_rolled_back    INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS patches (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id     INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
        file_path    TEXT    NOT NULL,
        diff_content TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_events_session   ON events(session_id);
    CREATE INDEX IF NOT EXISTS idx_patches_event    ON patches(event_id);
    CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_name);
    """

    def __init__(self, db_path: Path | str) -> None:
        self._path = Path(db_path)
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
        """Open (or create) the SQLite database and apply the schema."""
        logger.debug("Opening session DB at %s", self._path)
        self._conn = sqlite3.connect(
            str(self._path),
            check_same_thread=False,
            isolation_level=None,   # autocommit; we manage transactions manually
            timeout=10.0,
        )
        self._conn.row_factory = sqlite3.Row
        # Apply schema (idempotent – IF NOT EXISTS guards all DDL)
        self._conn.executescript(self._SCHEMA_SQL)
        # Idempotent column migration for existing databases
        for col_name, col_type in [
            ("ai_summary", "TEXT"),
            ("executor", "TEXT"),
            ("burst_num", "INTEGER"),
            ("commit_hash", "TEXT"),
            ("is_rolled_back", "INTEGER DEFAULT 0"),
        ]:
            try:
                self._conn.execute(f"ALTER TABLE events ADD COLUMN {col_name} {col_type}")
            except sqlite3.OperationalError:
                pass
        logger.info("SessionDB ready: %s", self._path)

    def _execute(
        self,
        sql: str,
        params: tuple[Any, ...] = (),
        *,
        commit: bool = True,
    ) -> sqlite3.Cursor:
        """
        Execute a single statement inside an explicit transaction.

        Parameters
        ----------
        sql     : SQL string with ``?`` placeholders.
        params  : Tuple of bind values.
        commit  : If ``True`` (default), ``COMMIT`` immediately after.
                  Pass ``False`` when batching multiple statements.
        """
        assert self._conn is not None, "Database is closed"
        cur = self._conn.cursor()
        if commit:
            cur.execute("BEGIN IMMEDIATE")
        try:
            cur.execute(sql, params)
            if commit:
                self._conn.execute("COMMIT")
        except Exception:
            if commit:
                self._conn.execute("ROLLBACK")
            raise
        return cur

    # ------------------------------------------------------------------
    # Public API – sessions
    # ------------------------------------------------------------------

    def create_session(self, project_name: str, target_path: str) -> int:
        """
        Insert a new ``ACTIVE`` session row and return its primary key.

        Parameters
        ----------
        project_name : Human-readable project label.
        target_path  : Absolute path being monitored.

        Returns
        -------
        int – The ``sessions.id`` of the newly created row.
        """
        cur = self._execute(
            "INSERT INTO sessions (project_name, target_path, start_time, status) "
            "VALUES (?, ?, ?, 'ACTIVE')",
            (project_name, target_path, _utcnow_iso()),
        )
        sid = cur.lastrowid
        logger.info("Created session id=%s for project '%s'", sid, project_name)
        return sid  # type: ignore[return-value]

    def close_session(self, session_id: int, status: str = "STOPPED") -> None:
        """
        Mark a session as finished by setting ``end_time`` and ``status``.

        Parameters
        ----------
        session_id : Row ID returned by :meth:`create_session`.
        status     : One of ``STOPPED`` (clean exit) or ``CRASHED``.
        """
        self._execute(
            "UPDATE sessions SET end_time = ?, status = ? WHERE id = ?",
            (_utcnow_iso(), status, session_id),
        )
        logger.info("Closed session id=%s with status=%s", session_id, status)

    def get_session(self, session_id: int) -> sqlite3.Row | None:
        """Return the row for *session_id*, or ``None`` if not found."""
        assert self._conn is not None
        cur = self._conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        )
        return cur.fetchone()

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
    ) -> int:
        """
        Insert a new event row and return its primary key.

        Parameters
        ----------
        session_id  : Foreign key into ``sessions``.
        event_type  : One of ``EDIT``, ``READ``, ``EXEC``, ``GIT``.
        first_file  : Path of the first file touched (may be ``None``).
        summary     : Free-text description of what happened.
        executor    : Execution initiator ('AI_AGENT' or 'HUMAN_DEV' for EXEC).
        commit_hash : Associated ShadowGit commit hash.
        is_rolled_back : 1 if rolled back, 0 otherwise.
        burst_num   : Sequential burst index within session.

        Returns
        -------
        int – The ``events.id`` of the new row.
        """
        valid_types = {"EDIT", "READ", "EXEC", "GIT"}
        if event_type not in valid_types:
            raise ValueError(f"event_type must be one of {valid_types}, got {event_type!r}")

        cur = self._execute(
            "INSERT INTO events (session_id, event_type, timestamp, first_file_touched, summary, executor, commit_hash, is_rolled_back, burst_num) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, event_type, _utcnow_iso(), first_file, summary, executor, commit_hash, is_rolled_back, burst_num),
        )
        eid = cur.lastrowid
        logger.debug("Recorded event id=%s type=%s file=%s executor=%s commit=%s", eid, event_type, first_file, executor, commit_hash)
        return eid  # type: ignore[return-value]

    def get_events(self, session_id: int) -> list[sqlite3.Row]:
        """Return all events for the given session, ordered by timestamp."""
        assert self._conn is not None
        cur = self._conn.execute(
            "SELECT * FROM events WHERE session_id = ? ORDER BY timestamp",
            (session_id,),
        )
        return cur.fetchall()

    def update_event_ai_summary(self, event_id: int, ai_summary: str) -> None:
        """
        Update the AI-generated intent and functional explanation for an event.

        Parameters
        ----------
        event_id   : Primary key into ``events``.
        ai_summary : JSON string or structured summary text.
        """
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
    ) -> int:
        """
        Store a raw diff associated with an EDIT event.

        Parameters
        ----------
        event_id     : Foreign key into ``events``.
        file_path    : The file whose diff is being stored.
        diff_content : Unified diff text (may be ``None`` for binary files).

        Returns
        -------
        int – The ``patches.id`` of the new row.
        """
        cur = self._execute(
            "INSERT INTO patches (event_id, file_path, diff_content) VALUES (?, ?, ?)",
            (event_id, file_path, diff_content),
        )
        pid = cur.lastrowid
        logger.debug("Recorded patch id=%s for event id=%s", pid, event_id)
        return pid  # type: ignore[return-value]

    def get_patches(self, event_id: int) -> list[sqlite3.Row]:
        """Return all patches associated with *event_id*."""
        assert self._conn is not None
        cur = self._conn.execute(
            "SELECT * FROM patches WHERE event_id = ? ORDER BY id",
            (event_id,),
        )
        return cur.fetchall()

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
            try:
                from spd_analysis_engine.scripts.shell_interceptor import is_ignored_command
            except (ImportError, ModuleNotFoundError):
                from scripts.shell_interceptor import is_ignored_command
        except Exception:
            return 0

        assert self._conn is not None
        cur = self._conn.execute("SELECT id, first_file_touched, summary FROM events WHERE event_type = 'EXEC'")
        rows = cur.fetchall()
        to_delete = []
        for r in rows:
            if is_ignored_command(r["first_file_touched"] or "") or is_ignored_command(r["summary"] or ""):
                to_delete.append(r["id"])
        if to_delete:
            q = f"DELETE FROM events WHERE id IN ({','.join('?' for _ in to_delete)})"
            self._execute(q, tuple(to_delete))
            logger.info("Purged %d internal IDE EXEC noise rows from session.db", len(to_delete))
        return len(to_delete)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def checkpoint(self) -> None:
        """
        Force a WAL checkpoint in ``TRUNCATE`` mode.

        Calling this before process exit ensures all WAL frames are written
        back to the main database file, and the WAL file is reset to zero
        length.  This protects against data loss on abrupt termination of
        the *next* run (SQLite can still recover from an intact WAL, but
        truncating it keeps the on-disk state cleaner).
        """
        if self._conn is None:
            return
        try:
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            logger.debug("WAL checkpoint completed for %s", self._path)
        except sqlite3.Error as exc:
            logger.warning("WAL checkpoint failed: %s", exc)

    def close(self) -> None:
        """Checkpoint the WAL, then close the database connection."""
        self.checkpoint()
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            logger.info("SessionDB closed: %s", self._path)

    def __enter__(self) -> "SessionDB":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
