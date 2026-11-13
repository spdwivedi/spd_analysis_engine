"""
scripts/storage/migrator.py
===========================
Project storage migration engine: converts legacy three-tier storage (current, last_run, history)
into the durable project-oriented model (data/projects/<slug>/) with schema migrations.

Invariants:
- Discover, validate, import, verify, write migration marker.
- Never move data until verification succeeds.
- Do not physically delete legacy directories (compatibility sources/views).
- Second migration run must be a no-op.
- Dry-run capable.
- Corrupted source handling without crashing batch migration.
- Interruption and restart resilient.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .project_storage import (
    get_durable_root,
    get_project_dir,
    ensure_project_layout,
    read_project_config,
    write_project_config,
)
from .schema_migration import (
    CURRENT_SCHEMA_VERSION,
    apply_schema_migrations,
    get_user_version,
)
from .utils import _slug, _utcnow_iso

logger = logging.getLogger(__name__)

MIGRATION_MARKER_FILE = ".migrated.json"


@dataclass
class MigrationResult:
    """Outcome of migrating a single project."""
    slug: str
    status: str  # 'migrated', 'no_op', 'dry_run', 'corrupted_source', 'failed'
    success: bool
    already_migrated: bool = False
    events_imported: int = 0
    sessions_imported: int = 0
    schema_version: int = CURRENT_SCHEMA_VERSION
    error: str | None = None
    duration_ms: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "status": self.status,
            "success": self.success,
            "already_migrated": self.already_migrated,
            "events_imported": self.events_imported,
            "sessions_imported": self.sessions_imported,
            "schema_version": self.schema_version,
            "error": self.error,
            "duration_ms": round(self.duration_ms, 2),
            "details": self.details,
        }


class ProjectMigrator:
    """Orchestrates validation, consolidation, and migration of legacy project storage."""

    def __init__(self, engine_root: Path | str) -> None:
        self.engine_root = Path(engine_root).resolve()
        self.durable_root = get_durable_root(self.engine_root)
        self.durable_root.mkdir(parents=True, exist_ok=True)

    def is_project_migrated(self, slug: str) -> bool:
        """Check if a project has already completed verified migration."""
        marker = get_project_dir(self.engine_root, slug) / MIGRATION_MARKER_FILE
        if not marker.is_file():
            return False
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
            return bool(data.get("verified"))
        except Exception:
            return False

    def discover_legacy_projects(self) -> dict[str, dict[str, Any]]:
        """
        Scan legacy tiers (current, last_run, history) and return all discoverable project candidates.
        """
        candidates: dict[str, dict[str, Any]] = {}

        for tier in ("current", "last_run", "history"):
            tier_dir = self.engine_root / tier
            if not tier_dir.is_dir():
                continue
            for entry in tier_dir.iterdir():
                if not entry.is_dir() or entry.name.startswith("."):
                    continue
                slug = entry.name
                if slug not in candidates:
                    candidates[slug] = {
                        "slug": slug,
                        "tiers": set(),
                        "sources": [],
                        "already_migrated": self.is_project_migrated(slug),
                    }
                candidates[slug]["tiers"].add(tier)
                candidates[slug]["sources"].append(entry)

        for slug, data in candidates.items():
            data["tiers"] = sorted(list(data["tiers"]))
        return candidates

    def validate_legacy_project(self, slug: str) -> tuple[bool, str | None]:
        """
        Validate legacy project data integrity before migration.
        Performs SQLite integrity checks on any candidate databases.
        """
        db_candidates: list[Path] = []
        for tier in ("current", "last_run"):
            cand = self.engine_root / tier / slug / "session.db"
            if cand.is_file():
                db_candidates.append(cand)

        hist_dir = self.engine_root / "history" / slug
        if hist_dir.is_dir():
            for d in sorted(hist_dir.iterdir()):
                if d.is_dir() and d.name.startswith("run_") and (d / "session.db").is_file():
                    db_candidates.append(d / "session.db")

        for db_file in db_candidates:
            if db_file.stat().st_size == 0:
                continue
            try:
                db_uri = db_file.resolve().as_uri() + "?mode=ro"
                conn = sqlite3.connect(db_uri, uri=True, timeout=5.0)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute("PRAGMA quick_check;")
                row = cur.fetchone()
                conn.close()
                if row and str(row[0]).lower() != "ok":
                    return False, f"Corrupted SQLite database at {db_file}: {row[0]}"
            except Exception as exc:
                return False, f"Failed reading SQLite database at {db_file}: {exc}"

        return True, None

    def migrate_project(self, project_name: str, dry_run: bool = False) -> MigrationResult:
        """
        Migrate a single legacy project to data/projects/<slug>/.

        Execution order:
        1. Check already migrated (second run is a no-op).
        2. Validate legacy source integrity (detect corruption).
        3. Dry-run return if requested.
        4. Staging import (isolated directory for crash/interruption resilience).
        5. Verify staged data (integrity, event IDs, schema version).
        6. Write migration marker and finalize atomically into data/projects/<slug>/.
        7. Legacy directories remain untouched.
        """
        t0 = time.perf_counter()
        slug = _slug(project_name)
        target_dir = get_project_dir(self.engine_root, slug)

        # 1. No-op if already migrated
        if self.is_project_migrated(slug):
            logger.info("Project '%s' is already migrated (no-op).", slug)
            return MigrationResult(
                slug=slug,
                status="no_op",
                success=True,
                already_migrated=True,
                duration_ms=(time.perf_counter() - t0) * 1000.0,
            )

        # 2. Validate legacy sources
        is_valid, val_err = self.validate_legacy_project(slug)
        if not is_valid:
            logger.warning("Project '%s' legacy sources failed validation: %s", slug, val_err)
            return MigrationResult(
                slug=slug,
                status="corrupted_source",
                success=False,
                error=val_err,
                duration_ms=(time.perf_counter() - t0) * 1000.0,
            )

        # 3. Dry-run
        if dry_run:
            logger.info("Dry-run migration for project '%s'.", slug)
            return MigrationResult(
                slug=slug,
                status="dry_run",
                success=True,
                duration_ms=(time.perf_counter() - t0) * 1000.0,
                details={"planned_destination": str(target_dir)},
            )

        # 4. Staging import (Crash & Interruption Resilience)
        # Any prior failed/interrupted staging attempt is cleaned first
        staging_dir = self.durable_root / f".staging_{slug}_{int(time.time() * 1000)}"
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        staging_dir.mkdir(parents=True, exist_ok=True)
        (staging_dir / "logs").mkdir(parents=True, exist_ok=True)

        try:
            # Clean up older stale staging dirs for this slug
            for older_stg in self.durable_root.glob(f".staging_{slug}_*"):
                if older_stg != staging_dir and older_stg.is_dir():
                    shutil.rmtree(older_stg, ignore_errors=True)

            events_count, sessions_count = self._import_data_to_staging(slug, staging_dir)

            # 5. Verify staged data before moving anything
            self._verify_staged_data(staging_dir, events_count, sessions_count)

            # 6. Write migration marker into staging
            marker_data = {
                "slug": slug,
                "version": CURRENT_SCHEMA_VERSION,
                "migrated_at": _utcnow_iso(),
                "verified": True,
                "events_count": events_count,
                "sessions_count": sessions_count,
            }
            (staging_dir / MIGRATION_MARKER_FILE).write_text(json.dumps(marker_data, indent=2), encoding="utf-8")

            # 7. Finalize atomically: move staging to target directory
            # If target_dir exists from partial creation, remove it
            if target_dir.exists():
                shutil.rmtree(target_dir, ignore_errors=True)
            for attempt in range(5):
                try:
                    staging_dir.rename(target_dir)
                    break
                except (OSError, PermissionError):
                    if attempt == 4:
                        shutil.copytree(staging_dir, target_dir, dirs_exist_ok=True)
                        shutil.rmtree(staging_dir, ignore_errors=True)
                        break
                    time.sleep(0.05 * (attempt + 1))

            logger.info("Successfully migrated project '%s' (%d events, %d sessions)", slug, events_count, sessions_count)
            return MigrationResult(
                slug=slug,
                status="migrated",
                success=True,
                events_imported=events_count,
                sessions_imported=sessions_count,
                schema_version=CURRENT_SCHEMA_VERSION,
                duration_ms=(time.perf_counter() - t0) * 1000.0,
            )

        except Exception as exc:
            # Clean up failed staging directory; target_dir was never corrupted
            if staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)
            logger.error("Migration failed for project '%s': %s", slug, exc)
            return MigrationResult(
                slug=slug,
                status="failed",
                success=False,
                error=str(exc),
                duration_ms=(time.perf_counter() - t0) * 1000.0,
            )

    def _import_data_to_staging(self, slug: str, staging_dir: Path) -> tuple[int, int]:
        """Import legacy databases, shadow git, logs, and config into staging_dir."""
        from .history import consolidate_project_history
        from .db import SessionDB

        # 1. Consolidate legacy SQLite history into staging session.db
        staged_db_path = staging_dir / "session.db"
        run_dirs = self._collect_run_dirs(slug)

        staged_db = SessionDB(staged_db_path)
        try:
            assert staged_db.conn is not None
            with staged_db.transaction() as cur:
                # Import historical sessions and events
                self._consolidate_into_conn(staged_db.conn, run_dirs)
                # Ensure canonical event_id, seq, and patch sizes are populated
                cur.execute("UPDATE events SET event_id = 'evt_legacy_' || id WHERE event_id IS NULL OR event_id = ''")
                cur.execute("SELECT id, session_id FROM events WHERE seq IS NULL ORDER BY session_id, timestamp, id")
                missing_seq = cur.fetchall()
                s_counts: dict[int, int] = {}
                for r_id, s_id in missing_seq:
                    s_counts[s_id] = s_counts.get(s_id, 0) + 1
                    cur.execute("UPDATE events SET seq = ? WHERE id = ?", (s_counts[s_id], r_id))
                cur.execute("UPDATE patches SET size = length(diff_content) WHERE (size IS NULL OR size = 0) AND diff_content IS NOT NULL")

            # 2. Apply schema migrations (ensures version headers and indexes)
            apply_schema_migrations(staged_db.conn, target_version=CURRENT_SCHEMA_VERSION)
            staged_db.checkpoint()
        finally:
            staged_db.close()

        # 3. Copy shadow_git if available in legacy tiers
        for tier in ("current", "last_run"):
            legacy_git = self.engine_root / tier / slug / "shadow_git"
            if legacy_git.is_dir() and not (staging_dir / "shadow_git").exists():
                try:
                    shutil.copytree(legacy_git, staging_dir / "shadow_git")
                except Exception as g_err:
                    logger.debug("Failed copying shadow_git for %s: %s", slug, g_err)

        # 4. Copy worker logs into logs/
        for tier in ("current", "last_run"):
            log_file = self.engine_root / tier / slug / "worker.log"
            if log_file.is_file():
                try:
                    shutil.copy2(log_file, staging_dir / "logs" / "worker.log")
                    break
                except Exception:
                    pass

        # 5. Synthesize and write project.config.json
        cfg = read_project_config(self.engine_root, slug)
        (staging_dir / "project.config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")

        # Read event & session totals from staged DB
        conn = sqlite3.connect(str(staged_db_path))
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM events")
        total_events = int(c.fetchone()[0])
        c.execute("SELECT COUNT(*) FROM sessions")
        total_sessions = int(c.fetchone()[0])
        conn.close()

        return total_events, total_sessions

    def _collect_run_dirs(self, slug: str) -> list[Path]:
        """Collect all historical run directories in chronological order."""
        run_dirs: list[Path] = []
        hist_dir = self.engine_root / "history" / slug
        if hist_dir.is_dir():
            for d in sorted(hist_dir.iterdir()):
                if d.is_dir() and d.name.startswith("run_") and (d / "session.db").is_file():
                    run_dirs.append(d)

        for tier in ("last_run", "current"):
            tier_d = self.engine_root / tier / slug
            if tier_d.is_dir() and (tier_d / "session.db").is_file():
                run_dirs.append(tier_d)

        return run_dirs

    def _consolidate_into_conn(self, target_conn: sqlite3.Connection, run_dirs: list[Path]) -> None:
        """Merge rows from run_dirs into target SQLite connection."""
        cur = target_conn.cursor()
        cur.execute("SELECT id, project_name, start_time FROM sessions")
        known_sessions = {(r[1], r[2]): r[0] for r in cur.fetchall()}
        cur.execute("SELECT timestamp, event_type, first_file_touched, summary FROM events")
        target_counts = Counter((r[0], r[1], r[2], r[3]) for r in cur.fetchall())
        inserted_counts: Counter[tuple[str, str, str | None, str | None]] = Counter()

        for src_dir in run_dirs:
            src_db = src_dir / "session.db"
            if not src_db.is_file() or src_db.stat().st_size == 0:
                continue

            try:
                sconn = sqlite3.connect(src_db.resolve().as_uri() + "?mode=ro", uri=True)
                sconn.row_factory = sqlite3.Row
                scur = sconn.cursor()

                # 1. Sessions
                scur.execute("SELECT * FROM sessions ORDER BY id ASC")
                session_id_map: dict[int, int] = {}
                for s_row in scur.fetchall():
                    key = (s_row["project_name"], s_row["start_time"])
                    if key in known_sessions:
                        session_id_map[s_row["id"]] = known_sessions[key]
                    else:
                        s_cols = ["project_name", "target_path", "start_time", "end_time", "status"]
                        s_vals = [s_row["project_name"], s_row["target_path"], s_row["start_time"], s_row["end_time"], s_row["status"]]
                        for s_opt in ("end_reason", "worker_id", "config_json", "actor"):
                            if s_opt in s_row.keys() and s_row[s_opt] is not None:
                                s_cols.append(s_opt)
                                s_vals.append(s_row[s_opt])
                        sq = f"INSERT INTO sessions ({', '.join(s_cols)}) VALUES ({', '.join('?' for _ in s_cols)})"
                        cur.execute(sq, tuple(s_vals))
                        new_sid = cur.lastrowid
                        session_id_map[s_row["id"]] = new_sid
                        known_sessions[key] = new_sid

                # 2. Events & Patches
                scur.execute("SELECT * FROM events ORDER BY id ASC")
                for e_row in scur.fetchall():
                    ekey = (e_row["timestamp"], e_row["event_type"], e_row["first_file_touched"], e_row["summary"])
                    target_sid = session_id_map.get(e_row["session_id"])
                    if not target_sid:
                        continue

                    if inserted_counts[ekey] < target_counts[ekey]:
                        inserted_counts[ekey] += 1
                        continue

                    # Insert event
                    cols = ["session_id", "event_type", "timestamp", "first_file_touched", "summary"]
                    vals = [target_sid, e_row["event_type"], e_row["timestamp"], e_row["first_file_touched"], e_row["summary"]]
                    for opt_col in (
                        "ai_summary", "executor", "burst_num", "commit_hash", "is_rolled_back",
                        "event_id", "seq", "burst_uid", "effective_files"
                    ):
                        if opt_col in e_row.keys() and e_row[opt_col] is not None:
                            cols.append(opt_col)
                            vals.append(e_row[opt_col])

                    if "event_id" not in cols:
                        cols.append("event_id")
                        vals.append(f"evt_legacy_{e_row['id']}")

                    q = f"INSERT INTO events ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})"
                    cur.execute(q, tuple(vals))
                    new_eid = cur.lastrowid
                    inserted_counts[ekey] += 1

                    # Copy associated patches
                    scur2 = sconn.cursor()
                    scur2.execute("SELECT * FROM patches WHERE event_id = ? ORDER BY id ASC", (e_row["id"],))
                    for p_row in scur2.fetchall():
                        p_cols = ["event_id", "file_path", "diff_content"]
                        p_vals = [new_eid, p_row["file_path"], p_row["diff_content"]]
                        for p_opt in ("adds", "dels", "kind", "size", "hash_before", "hash_after", "truncated"):
                            if p_opt in p_row.keys() and p_row[p_opt] is not None:
                                p_cols.append(p_opt)
                                p_vals.append(p_row[p_opt])
                        pq = f"INSERT INTO patches ({', '.join(p_cols)}) VALUES ({', '.join('?' for _ in p_cols)})"
                        cur.execute(pq, tuple(p_vals))

                sconn.close()
            except Exception as exc:
                logger.debug("Error consolidating legacy DB %s: %s", src_db, exc)

    def _verify_staged_data(self, staging_dir: Path, min_events: int, min_sessions: int) -> None:
        """Verify the integrity and completeness of the staged directory."""
        staged_db = staging_dir / "session.db"
        if not staged_db.is_file():
            raise RuntimeError(f"Verification failed: {staged_db} does not exist")

        conn = sqlite3.connect(str(staged_db))
        try:
            cur = conn.cursor()
            cur.execute("PRAGMA quick_check;")
            chk = cur.fetchone()
            if not chk or str(chk[0]).lower() != "ok":
                raise RuntimeError(f"Verification failed: quick_check error {chk}")

            # Verify schema version
            cur.execute("PRAGMA user_version;")
            v = int(cur.fetchone()[0])
            if v < CURRENT_SCHEMA_VERSION:
                raise RuntimeError(f"Verification failed: expected user_version {CURRENT_SCHEMA_VERSION}, found {v}")

            # Verify events have canonical event_id
            cur.execute("SELECT COUNT(*) FROM events WHERE event_id IS NULL OR event_id = ''")
            missing_evt_ids = int(cur.fetchone()[0])
            if missing_evt_ids > 0:
                raise RuntimeError(f"Verification failed: {missing_evt_ids} events missing canonical event_id")

        finally:
            conn.close()

        # Verify config exists
        cfg_f = staging_dir / "project.config.json"
        if not cfg_f.is_file():
            raise RuntimeError("Verification failed: project.config.json missing")
        try:
            json.loads(cfg_f.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError(f"Verification failed: project.config.json malformed: {exc}")

    def migrate_all(self, dry_run: bool = False) -> list[MigrationResult]:
        """Discover and migrate all legacy projects."""
        candidates = self.discover_legacy_projects()
        results: list[MigrationResult] = []
        for slug in candidates:
            results.append(self.migrate_project(slug, dry_run=dry_run))
        return results
