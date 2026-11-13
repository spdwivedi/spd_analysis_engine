"""
scripts/storage/schema_migration.py
===================================
Versioned, transactional, idempotent, dry-run capable SQLite schema migrations
using PRAGMA user_version.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Callable

def check_fault(name: str) -> None:
    from ..core.faults import check_fault as _cf
    _cf(name)

logger = logging.getLogger(__name__)

CURRENT_SCHEMA_VERSION = 5


@dataclass
class MigrationReport:
    """Report detailing migration execution status and changes."""
    initial_version: int
    target_version: int
    current_version: int
    applied_versions: list[int] = field(default_factory=list)
    dry_run: bool = False
    success: bool = True
    error: str | None = None
    duration_ms: float = 0.0
    changes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "initial_version": self.initial_version,
            "target_version": self.target_version,
            "current_version": self.current_version,
            "applied_versions": self.applied_versions,
            "dry_run": self.dry_run,
            "success": self.success,
            "error": self.error,
            "duration_ms": round(self.duration_ms, 2),
            "changes": self.changes,
        }


def get_user_version(conn: sqlite3.Connection) -> int:
    """Read the current schema version from SQLite PRAGMA user_version."""
    cur = conn.cursor()
    cur.execute("PRAGMA user_version;")
    row = cur.fetchone()
    return int(row[0]) if row else 0


def set_user_version(conn: sqlite3.Connection, version: int) -> None:
    """Set the SQLite PRAGMA user_version header."""
    conn.execute(f"PRAGMA user_version = {int(version)};")


def _get_existing_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    """Return the set of column names currently present in table_name."""
    try:
        cur = conn.cursor()
        cur.execute(f"PRAGMA table_info({table_name});")
        return {row[1] for row in cur.fetchall()}
    except Exception:
        return set()


# ---------------------------------------------------------------------------
# Migration Step 1: Baseline legacy schema
# ---------------------------------------------------------------------------

def _migration_v1_apply(conn: sqlite3.Connection, changes: list[str]) -> None:
    """Ensure baseline sessions, events, patches, git_milestones exist."""
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS sessions (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        project_name TEXT    NOT NULL,
        target_path  TEXT    NOT NULL,
        start_time   TEXT    NOT NULL,
        end_time     TEXT,
        status       TEXT    NOT NULL DEFAULT 'ACTIVE'
    );

    CREATE TABLE IF NOT EXISTS events (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id        INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        event_type        TEXT    NOT NULL,
        timestamp         TEXT    NOT NULL,
        first_file_touched TEXT,
        summary           TEXT,
        ai_summary        TEXT,
        executor          TEXT,
        burst_num         INTEGER,
        commit_hash       TEXT,
        is_rolled_back    INTEGER DEFAULT 0,
        pushed_in_milestone_id INTEGER NULL
    );

    CREATE TABLE IF NOT EXISTS patches (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id     INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
        file_path    TEXT    NOT NULL,
        diff_content TEXT
    );

    CREATE TABLE IF NOT EXISTS git_milestones (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id      INTEGER NOT NULL REFERENCES sessions(id),
        commit_sha      TEXT    NOT NULL,
        commit_title    TEXT    NOT NULL,
        commit_body     TEXT,
        changelog_entry TEXT,
        burst_ids       TEXT    NOT NULL,
        remote_url      TEXT    NOT NULL,
        branch          TEXT    NOT NULL,
        pushed_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Ensure baseline columns exist if events table already existed in a minimal form
    existing_event_cols = _get_existing_columns(conn, "events")
    for col, ctype in [
        ("first_file_touched", "TEXT"),
        ("summary", "TEXT"),
        ("ai_summary", "TEXT"),
        ("executor", "TEXT"),
        ("burst_num", "INTEGER"),
        ("commit_hash", "TEXT"),
        ("is_rolled_back", "INTEGER DEFAULT 0"),
        ("pushed_in_milestone_id", "INTEGER NULL"),
    ]:
        if col not in existing_event_cols:
            conn.execute(f"ALTER TABLE events ADD COLUMN {col} {ctype};")

    conn.executescript("""
    CREATE INDEX IF NOT EXISTS idx_events_session   ON events(session_id);
    CREATE INDEX IF NOT EXISTS idx_patches_event    ON patches(event_id);
    CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_name);
    CREATE INDEX IF NOT EXISTS idx_milestones_session ON git_milestones(session_id);
    CREATE INDEX IF NOT EXISTS idx_events_session_read ON events(session_id, event_type, first_file_touched);
    """)
    changes.append("v1: Ensured baseline tables (sessions, events, patches, git_milestones)")


# ---------------------------------------------------------------------------
# Migration Step 2: Durable project-oriented schema
# ---------------------------------------------------------------------------

def _migration_v2_apply(conn: sqlite3.Connection, changes: list[str]) -> None:
    """
    Add durable project schema fields and indexes:
    - Events: event_id (canonical identity), seq, burst_uid, effective_files
    - Patches: adds, dels, kind, size, hash_before, hash_after, truncated
    - Sessions: end_reason, worker_id, config_json, actor
    Backfill legacy events with stable canonical event_id and sequential seq.
    """
    # 1. Sessions table new fields
    session_cols = _get_existing_columns(conn, "sessions")
    for col, ctype in [
        ("end_reason", "TEXT"),
        ("worker_id", "TEXT"),
        ("config_json", "TEXT"),
        ("actor", "TEXT"),
    ]:
        if col not in session_cols:
            conn.execute(f"ALTER TABLE sessions ADD COLUMN {col} {ctype};")
            changes.append(f"v2: Added column sessions.{col}")

    # 2. Events table new fields
    event_cols = _get_existing_columns(conn, "events")
    for col, ctype in [
        ("event_id", "TEXT"),
        ("seq", "INTEGER"),
        ("burst_uid", "TEXT"),
        ("effective_files", "TEXT"),
    ]:
        if col not in event_cols:
            conn.execute(f"ALTER TABLE events ADD COLUMN {col} {ctype};")
            changes.append(f"v2: Added column events.{col}")

    # 3. Patches table new fields
    patch_cols = _get_existing_columns(conn, "patches")
    for col, ctype in [
        ("adds", "INTEGER DEFAULT 0"),
        ("dels", "INTEGER DEFAULT 0"),
        ("kind", "TEXT DEFAULT 'unified'"),
        ("size", "INTEGER DEFAULT 0"),
        ("hash_before", "TEXT"),
        ("hash_after", "TEXT"),
        ("truncated", "INTEGER DEFAULT 0"),
    ]:
        if col not in patch_cols:
            conn.execute(f"ALTER TABLE patches ADD COLUMN {col} {ctype};")
            changes.append(f"v2: Added column patches.{col}")

    # 4. Backfill canonical event_id for existing events without one
    cur = conn.cursor()
    cur.execute("SELECT id, session_id, event_id FROM events WHERE event_id IS NULL OR event_id = ''")
    missing_evt_ids = cur.fetchall()
    for row in missing_evt_ids:
        row_id = row[0]
        stable_id = f"evt_legacy_{row_id}"
        conn.execute("UPDATE events SET event_id = ? WHERE id = ?", (stable_id, row_id))
    if missing_evt_ids:
        changes.append(f"v2: Backfilled stable canonical event_id for {len(missing_evt_ids)} legacy events")

    # 5. Backfill seq for existing events without seq
    cur.execute("SELECT id, session_id, timestamp FROM events WHERE seq IS NULL ORDER BY session_id, timestamp, id")
    missing_seq = cur.fetchall()
    session_counters: dict[int, int] = {}
    for r_id, s_id, _ in missing_seq:
        session_counters[s_id] = session_counters.get(s_id, 0) + 1
        conn.execute("UPDATE events SET seq = ? WHERE id = ?", (session_counters[s_id], r_id))
    if missing_seq:
        changes.append(f"v2: Backfilled seq order for {len(missing_seq)} legacy events")

    # 6. Backfill patch size if 0 and diff_content exists
    conn.execute("""
    UPDATE patches SET size = length(diff_content)
    WHERE (size IS NULL OR size = 0) AND diff_content IS NOT NULL AND diff_content != ''
    """)

    # 7. Add useful indexes
    conn.executescript("""
    CREATE INDEX IF NOT EXISTS idx_events_canonical_id ON events(event_id);
    CREATE INDEX IF NOT EXISTS idx_events_burst_uid    ON events(burst_uid);
    CREATE INDEX IF NOT EXISTS idx_events_seq          ON events(session_id, seq);
    CREATE INDEX IF NOT EXISTS idx_patches_file        ON patches(file_path);
    CREATE INDEX IF NOT EXISTS idx_sessions_status     ON sessions(status);
    CREATE INDEX IF NOT EXISTS idx_events_session_read ON events(session_id, event_type, first_file_touched);
    """)
    changes.append("v2: Created indexes for canonical event_id, burst_uid, seq, file_path, status")


# ---------------------------------------------------------------------------
# Migration Step 3: Worker-owned data (manifest, git_attribution, capture_metadata)
# ---------------------------------------------------------------------------

def _migration_v3_apply(conn: sqlite3.Connection, changes: list[str]) -> None:
    """
    Worker-owned data:
    - manifest: tracked files, hashes, sizes, modification timestamps, tracking status
    - git_attribution: commit hashes, author/committer metadata, milestone mapping
    - capture_metadata: scan duration, telemetry stats, capture properties
    """
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS manifest (
        file_path       TEXT PRIMARY KEY,
        file_hash       TEXT NOT NULL,
        file_size       INTEGER NOT NULL,
        last_modified   TEXT NOT NULL,
        tracked_status  TEXT NOT NULL DEFAULT 'TRACKED',
        session_id      INTEGER REFERENCES sessions(id) ON DELETE SET NULL
    );
    CREATE INDEX IF NOT EXISTS idx_manifest_session ON manifest(session_id);

    CREATE TABLE IF NOT EXISTS git_attribution (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id      INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        commit_hash     TEXT NOT NULL,
        author_name     TEXT,
        author_email    TEXT,
        committed_at    TEXT,
        milestone_id    INTEGER,
        burst_ids       TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_git_attribution_session ON git_attribution(session_id);
    CREATE INDEX IF NOT EXISTS idx_git_attribution_commit  ON git_attribution(commit_hash);

    CREATE TABLE IF NOT EXISTS capture_metadata (
        key             TEXT PRIMARY KEY,
        value           TEXT NOT NULL,
        updated_at      TEXT NOT NULL,
        session_id      INTEGER REFERENCES sessions(id) ON DELETE SET NULL
    );
    CREATE INDEX IF NOT EXISTS idx_capture_metadata_session ON capture_metadata(session_id);
    """)
    changes.append("v3: Created worker-owned tables (manifest, git_attribution, capture_metadata)")


def _migration_v4_apply(conn: sqlite3.Connection, changes: list[str]) -> None:
    """
    Durable Git state and operations recovery:
    - Events: git_state, git_error
    - git_ops table: tracking per-burst git operations for crash recovery
    """
    event_cols = _get_existing_columns(conn, "events")
    for col, ctype in [
        ("git_state", "TEXT DEFAULT 'pending'"),
        ("git_error", "TEXT"),
    ]:
        if col not in event_cols:
            conn.execute(f"ALTER TABLE events ADD COLUMN {col} {ctype};")
            changes.append(f"v4: Added column events.{col}")

    conn.executescript("""
    CREATE TABLE IF NOT EXISTS git_ops (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id        INTEGER NOT NULL,
        burst_uid       TEXT NOT NULL,
        status          TEXT NOT NULL,
        files_json      TEXT NOT NULL,
        effective_json  TEXT NOT NULL,
        commit_hash     TEXT,
        error_message   TEXT,
        attempts        INTEGER DEFAULT 0,
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_git_ops_status ON git_ops(status);
    CREATE INDEX IF NOT EXISTS idx_git_ops_event  ON git_ops(event_id);
    CREATE INDEX IF NOT EXISTS idx_git_ops_burst  ON git_ops(burst_uid);
    """)
    changes.append("v4: Created git_ops table and events git_state columns for durable recovery")


def _migration_v5_apply(conn: sqlite3.Connection, changes: list[str]) -> None:
    """Non-destructive rollback model and rollbacks history table."""
    event_cols = _get_existing_columns(conn, "events")
    for col, ctype in [
        ("rollback_state", "TEXT DEFAULT 'active'"),
        ("superseded_by", "TEXT"),
    ]:
        if col not in event_cols:
            conn.execute(f"ALTER TABLE events ADD COLUMN {col} {ctype};")
            changes.append(f"v5: Added column events.{col}")

    conn.executescript("""
    CREATE TABLE IF NOT EXISTS rollbacks (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        rollback_id     TEXT NOT NULL UNIQUE,
        target_event_id INTEGER,
        target_commit   TEXT NOT NULL,
        snapshot_commit TEXT NOT NULL,
        status          TEXT NOT NULL DEFAULT 'COMPLETED',
        affected_events TEXT NOT NULL,
        file_plan_json  TEXT NOT NULL,
        created_at      TEXT NOT NULL,
        undone_at       TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_rollbacks_target   ON rollbacks(target_event_id);
    CREATE INDEX IF NOT EXISTS idx_rollbacks_status   ON rollbacks(status);
    CREATE INDEX IF NOT EXISTS idx_events_rollback_st ON events(rollback_state);
    """)
    changes.append("v5: Created rollbacks table and events rollback_state for non-destructive rollback")


MIGRATIONS: dict[int, tuple[str, Callable[[sqlite3.Connection, list[str]], None]]] = {
    1: ("Baseline legacy schema", _migration_v1_apply),
    2: ("Durable project schema with canonical event IDs and metadata", _migration_v2_apply),
    3: ("Worker-owned data tables (manifest, git_attribution, capture_metadata)", _migration_v3_apply),
    4: ("Durable Git state tracking and operations crash recovery", _migration_v4_apply),
    5: ("Non-destructive rollback tracking and rollbacks history", _migration_v5_apply),
}


# ---------------------------------------------------------------------------
# Migration Runner
# ---------------------------------------------------------------------------

def apply_schema_migrations(
    conn: sqlite3.Connection,
    target_version: int = CURRENT_SCHEMA_VERSION,
    dry_run: bool = False,
) -> MigrationReport:
    """
    Execute versioned, transactional, idempotent schema migrations on conn up to target_version.

    Parameters
    ----------
    conn : sqlite3.Connection
        Active SQLite connection to session.db.
    target_version : int
        Desired target PRAGMA user_version (default CURRENT_SCHEMA_VERSION).
    dry_run : bool
        If True, validates and reports pending migration steps without modifying user_version
        or persisting schema alterations.

    Returns
    -------
    MigrationReport
        Structured report describing execution details, applied versions, and changes.
    """
    t0 = time.perf_counter()
    initial_version = get_user_version(conn)

    # Configure INCREMENTAL auto-vacuum on fresh empty databases prior to table creation
    if initial_version == 0 and not dry_run:
        try:
            cur = conn.cursor()
            cur.execute("SELECT count(*) FROM sqlite_master WHERE type='table';")
            if cur.fetchone()[0] == 0:
                conn.execute("PRAGMA auto_vacuum = INCREMENTAL;")
        except Exception:
            pass

    # 1. If user_version is 0, check if legacy tables exist.
    # If legacy tables exist without user_version, treat baseline as v1 so we migrate to v2.
    effective_initial = initial_version
    if initial_version == 0:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='events'")
        if cur.fetchone():
            effective_initial = 1

    report = MigrationReport(
        initial_version=initial_version,
        target_version=target_version,
        current_version=initial_version,
        dry_run=dry_run,
        success=True,
    )

    if effective_initial >= target_version and initial_version == effective_initial:
        report.duration_ms = (time.perf_counter() - t0) * 1000.0
        return report

    pending = [v for v in sorted(MIGRATIONS.keys()) if v > effective_initial and v <= target_version]
    if initial_version == 0 and effective_initial == 1 and 1 not in pending:
        # We need to set user_version to 1 or higher
        pass

    if dry_run:
        for v in pending:
            desc = MIGRATIONS[v][0]
            report.changes.append(f"[dry-run] Would apply v{v}: {desc}")
            report.applied_versions.append(v)
        report.duration_ms = (time.perf_counter() - t0) * 1000.0
        return report

    # Transactional execution
    try:
        # Note: DDL in SQLite commits auto-transaction in standard mode, so we use BEGIN IMMEDIATE
        for v in sorted(MIGRATIONS.keys()):
            if v <= initial_version and initial_version != 0:
                continue
            if v > target_version:
                break

            name, apply_fn = MIGRATIONS[v]
            logger.info("Applying schema migration v%d: %s", v, name)
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE;")
            check_fault("during_migration")
            apply_fn(conn, report.changes)
            set_user_version(conn, v)
            if conn.in_transaction:
                conn.execute("COMMIT;")
            report.applied_versions.append(v)
            report.current_version = v

        # If starting from unversioned v0 but tables already had baseline v1:
        if get_user_version(conn) < target_version:
            set_user_version(conn, target_version)
            report.current_version = target_version

        report.success = True
    except Exception as exc:
        if conn.in_transaction:
            try:
                conn.execute("ROLLBACK;")
            except Exception:
                pass
        report.success = False
        report.error = str(exc)
        logger.error("Schema migration failed: %s", exc)
        raise
    finally:
        report.duration_ms = (time.perf_counter() - t0) * 1000.0

    return report


def execute_incremental_vacuum(conn: sqlite3.Connection, pages: int = 100) -> int:
    """Run PRAGMA incremental_vacuum(pages) to release freelist pages to disk."""
    try:
        cur = conn.cursor()
        cur.execute(f"PRAGMA incremental_vacuum({int(pages)});")
        cur.execute("PRAGMA freelist_count;")
        row = cur.fetchone()
        return int(row[0]) if row else 0
    except Exception as exc:
        logger.debug("incremental_vacuum failed: %s", exc)
        return 0

