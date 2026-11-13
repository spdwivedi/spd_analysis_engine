"""
tests/test_storage_migration.py
===============================
Comprehensive test suite for the durable project storage model,
versioned schema migrations (PRAGMA user_version), and ProjectMigrator.

Covers:
- migration dry run
- real migration
- second migration (no-op)
- corrupted source handling
- legacy event ID mapping
- canonical ID preservation
- migration interruption/restart
- schema migration transactions, idempotence, and field additions
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.storage.db import SessionDB
from scripts.storage.migrator import (
    MIGRATION_MARKER_FILE,
    MigrationResult,
    ProjectMigrator,
)
from scripts.storage.project_storage import (
    ensure_project_layout,
    get_durable_root,
    get_project_dir,
    read_project_config,
    resolve_project_db,
    resolve_project_git,
    write_project_config,
)
from scripts.storage.schema_migration import (
    CURRENT_SCHEMA_VERSION,
    MigrationReport,
    apply_schema_migrations,
    get_user_version,
    set_user_version,
)


class TestSchemaMigrations(unittest.TestCase):
    """Unit tests for the versioned, transactional, idempotent schema migration engine."""

    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="spd_schema_test_"))
        self.db_path = self.tmp_dir / "test_session.db"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_schema_migrations_fresh_db(self) -> None:
        """Fresh database starts at version 0 and migrates to CURRENT_SCHEMA_VERSION."""
        conn = sqlite3.connect(str(self.db_path))
        self.assertEqual(get_user_version(conn), 0)

        report = apply_schema_migrations(conn)
        self.assertTrue(report.success)
        self.assertEqual(report.target_version, CURRENT_SCHEMA_VERSION)
        self.assertEqual(report.current_version, CURRENT_SCHEMA_VERSION)
        self.assertEqual(get_user_version(conn), CURRENT_SCHEMA_VERSION)
        self.assertIn(1, report.applied_versions)
        self.assertIn(2, report.applied_versions)

        # Verify tables and columns exist
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(events)")
        event_cols = {r[1] for r in cur.fetchall()}
        self.assertIn("event_id", event_cols)
        self.assertIn("seq", event_cols)
        self.assertIn("burst_uid", event_cols)
        self.assertIn("effective_files", event_cols)

        cur.execute("PRAGMA table_info(patches)")
        patch_cols = {r[1] for r in cur.fetchall()}
        self.assertIn("adds", patch_cols)
        self.assertIn("dels", patch_cols)
        self.assertIn("kind", patch_cols)
        self.assertIn("size", patch_cols)
        self.assertIn("hash_before", patch_cols)
        self.assertIn("hash_after", patch_cols)
        self.assertIn("truncated", patch_cols)

        cur.execute("PRAGMA table_info(sessions)")
        session_cols = {r[1] for r in cur.fetchall()}
        self.assertIn("end_reason", session_cols)
        self.assertIn("worker_id", session_cols)
        self.assertIn("config_json", session_cols)
        self.assertIn("actor", session_cols)

        conn.close()

    def test_schema_migrations_dry_run(self) -> None:
        """Dry-run migration reports changes without altering user_version or applying DDL."""
        conn = sqlite3.connect(str(self.db_path))
        self.assertEqual(get_user_version(conn), 0)

        report = apply_schema_migrations(conn, dry_run=True)
        self.assertTrue(report.dry_run)
        self.assertTrue(report.success)
        self.assertGreater(len(report.changes), 0)
        self.assertEqual(get_user_version(conn), 0)  # user_version NOT changed

        # Tables were not actually created
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='events'")
        self.assertIsNone(cur.fetchone())
        conn.close()

    def test_schema_migrations_idempotent(self) -> None:
        """Applying migrations a second time is a complete no-op."""
        conn = sqlite3.connect(str(self.db_path))
        rep1 = apply_schema_migrations(conn)
        self.assertEqual(rep1.current_version, CURRENT_SCHEMA_VERSION)

        rep2 = apply_schema_migrations(conn)
        self.assertEqual(len(rep2.applied_versions), 0)
        self.assertEqual(rep2.current_version, CURRENT_SCHEMA_VERSION)
        conn.close()

    def test_schema_migrations_transactional_rollback(self) -> None:
        """Migration failure rolls back transactions cleanly."""
        conn = sqlite3.connect(str(self.db_path))
        # First bring to v1
        apply_schema_migrations(conn, target_version=1)
        self.assertEqual(get_user_version(conn), 1)

        # Inject error into MIGRATIONS temporarily
        from scripts.storage import schema_migration

        def failing_migration(c: sqlite3.Connection, ch: list[str]) -> None:
            c.execute("CREATE TABLE partial_table (id INT);")
            raise RuntimeError("Simulated migration crash")

        orig_m = schema_migration.MIGRATIONS[2]
        try:
            schema_migration.MIGRATIONS[2] = ("Faulty migration", failing_migration)
            with self.assertRaises(RuntimeError):
                apply_schema_migrations(conn, target_version=2)

            # user_version remains 1 and partial table was rolled back
            self.assertEqual(get_user_version(conn), 1)
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='partial_table'")
            self.assertIsNone(cur.fetchone())
        finally:
            schema_migration.MIGRATIONS[2] = orig_m
            conn.close()


class TestStorageMigrationEngine(unittest.TestCase):
    """End-to-end tests for legacy storage migration to durable project-oriented model."""

    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="spd_migration_test_"))
        self.migrator = ProjectMigrator(self.tmp_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _create_legacy_project(
        self,
        slug: str,
        events_count: int = 5,
        with_custom_event_ids: bool = False,
    ) -> Path:
        """Helper to create a legacy project in current/ and history/ tiers."""
        curr_dir = self.tmp_dir / "current" / slug
        curr_dir.mkdir(parents=True, exist_ok=True)

        # Write legacy project.meta
        (curr_dir / "project.meta").write_text(
            f"project_name={slug}\ntarget_path={self.tmp_dir / 'workspace'}\nide_profile=antigravity\n",
            encoding="utf-8",
        )

        # Create session.db with legacy schema
        db_path = curr_dir / "session.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
        CREATE TABLE sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_name TEXT NOT NULL,
            target_path TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT,
            status TEXT NOT NULL DEFAULT 'ACTIVE'
        );
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            first_file_touched TEXT,
            summary TEXT,
            burst_num INTEGER
        );
        CREATE TABLE patches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL,
            file_path TEXT NOT NULL,
            diff_content TEXT
        );
        """)

        conn.execute(
            "INSERT INTO sessions (project_name, target_path, start_time) VALUES (?, ?, ?)",
            (slug, str(self.tmp_dir / "workspace"), "2026-10-01T10:00:00Z"),
        )
        sid = 1

        for i in range(1, events_count + 1):
            conn.execute(
                "INSERT INTO events (session_id, event_type, timestamp, first_file_touched, summary, burst_num) VALUES (?, ?, ?, ?, ?, ?)",
                (sid, "EDIT", f"2026-10-01T10:0{i}:00Z", f"file_{i}.py", f"Summary {i}", i),
            )
            eid = i
            conn.execute(
                "INSERT INTO patches (event_id, file_path, diff_content) VALUES (?, ?, ?)",
                (eid, f"file_{i}.py", f"--- a/file_{i}.py\n+++ b/file_{i}.py\n@@ -1 +1 @@\n-line\n+mod {i}"),
            )

        if with_custom_event_ids:
            # Add event_id column manually and populate with custom ID
            conn.execute("ALTER TABLE events ADD COLUMN event_id TEXT;")
            conn.execute("UPDATE events SET event_id = 'canonical_custom_id_1' WHERE id = 1;")

        conn.commit()
        conn.close()

        # Add mock worker log
        (curr_dir / "worker.log").write_text("[INFO] Telemetry initialized\n", encoding="utf-8")

        # Add mock shadow_git
        git_dir = curr_dir / "shadow_git" / ".git"
        git_dir.mkdir(parents=True, exist_ok=True)
        (git_dir / "config").write_text("[core]\nrepositoryformatversion = 0\n", encoding="utf-8")

        return curr_dir

    def test_migration_dry_run(self) -> None:
        """Dry run discovers legacy project, validates, reports planned target, but does not move or write marker."""
        slug = "proj_dry_run"
        self._create_legacy_project(slug, events_count=3)

        result = self.migrator.migrate_project(slug, dry_run=True)
        self.assertEqual(result.status, "dry_run")
        self.assertTrue(result.success)
        self.assertFalse(result.already_migrated)

        # Target directory and marker must NOT exist
        target_dir = get_project_dir(self.tmp_dir, slug)
        self.assertFalse(target_dir.exists())
        self.assertFalse(self.migrator.is_project_migrated(slug))

        # Legacy current/ directory remains intact
        self.assertTrue((self.tmp_dir / "current" / slug / "session.db").is_file())

    def test_real_migration(self) -> None:
        """Real migration stages, verifies, migrates to data/projects/<slug>/, writes .migrated.json, preserves legacy dirs."""
        slug = "proj_real_migration"
        self._create_legacy_project(slug, events_count=4)

        result = self.migrator.migrate_project(slug, dry_run=False)
        self.assertEqual(result.status, "migrated")
        self.assertTrue(result.success)
        self.assertEqual(result.events_imported, 4)
        self.assertEqual(result.sessions_imported, 1)

        # Durable layout exists
        target_dir = get_project_dir(self.tmp_dir, slug)
        self.assertTrue(target_dir.is_dir())
        self.assertTrue((target_dir / "session.db").is_file())
        self.assertTrue((target_dir / "project.config.json").is_file())
        self.assertTrue((target_dir / "logs" / "worker.log").is_file())
        self.assertTrue((target_dir / "shadow_git" / ".git" / "config").is_file())

        # Migration marker verified
        marker_file = target_dir / MIGRATION_MARKER_FILE
        self.assertTrue(marker_file.is_file())
        marker = json.loads(marker_file.read_text(encoding="utf-8"))
        self.assertTrue(marker["verified"])
        self.assertEqual(marker["events_count"], 4)

        # Target DB is at CURRENT_SCHEMA_VERSION
        conn = sqlite3.connect(str(target_dir / "session.db"))
        self.assertEqual(get_user_version(conn), CURRENT_SCHEMA_VERSION)
        conn.close()

        # Legacy source directories MUST remain intact (not physically deleted)
        legacy_dir = self.tmp_dir / "current" / slug
        self.assertTrue(legacy_dir.is_dir())
        self.assertTrue((legacy_dir / "session.db").is_file())

    def test_second_migration_noop(self) -> None:
        """A second migration run on an already-migrated project is an instant no-op."""
        slug = "proj_double_migrate"
        self._create_legacy_project(slug, events_count=3)

        res1 = self.migrator.migrate_project(slug)
        self.assertEqual(res1.status, "migrated")

        # Second migration
        res2 = self.migrator.migrate_project(slug)
        self.assertEqual(res2.status, "no_op")
        self.assertTrue(res2.success)
        self.assertTrue(res2.already_migrated)

    def test_corrupted_source_handling(self) -> None:
        """Corrupted legacy database fails validation and does not corrupt or create target directory."""
        slug = "proj_corrupted"
        curr_dir = self.tmp_dir / "current" / slug
        curr_dir.mkdir(parents=True, exist_ok=True)

        # Write non-sqlite garbage
        (curr_dir / "session.db").write_bytes(b"NOT A SQLITE FILE RANDOM CORRUPTION 1234567890")

        result = self.migrator.migrate_project(slug)
        self.assertEqual(result.status, "corrupted_source")
        self.assertFalse(result.success)
        self.assertIsNotNone(result.error)

        # Target dir must NOT be created
        target_dir = get_project_dir(self.tmp_dir, slug)
        self.assertFalse(target_dir.exists())

    def test_legacy_event_id_mapping(self) -> None:
        """Migrated events without canonical IDs receive stable evt_legacy_<id> and resolve via get_event_by_id."""
        slug = "proj_id_mapping"
        self._create_legacy_project(slug, events_count=3)
        self.migrator.migrate_project(slug)

        target_db_path = get_project_dir(self.tmp_dir, slug) / "session.db"
        sdb = SessionDB(target_db_path)

        # 1. Resolve by legacy integer ID
        evt1 = sdb.get_event_by_id(1)
        self.assertIsNotNone(evt1)
        self.assertEqual(evt1["id"], 1)
        self.assertEqual(evt1["event_id"], "evt_legacy_1")

        # 2. Resolve by newly assigned canonical ID
        evt_canonical = sdb.get_event_by_id("evt_legacy_1")
        self.assertIsNotNone(evt_canonical)
        self.assertEqual(evt_canonical["id"], 1)

        # 3. Burst numbers are display-only and must NEVER resolve an event
        evt_burst = sdb.get_event_by_id("burst_1")
        self.assertIsNone(evt_burst)

        sdb.close()

    def test_canonical_id_preservation(self) -> None:
        """Events that already possess a canonical event_id preserve it intact through migration."""
        slug = "proj_id_preservation"
        self._create_legacy_project(slug, events_count=2, with_custom_event_ids=True)
        self.migrator.migrate_project(slug)

        target_db_path = get_project_dir(self.tmp_dir, slug) / "session.db"
        sdb = SessionDB(target_db_path)

        # Custom ID was preserved
        evt1 = sdb.get_event_by_id("canonical_custom_id_1")
        self.assertIsNotNone(evt1)
        self.assertEqual(evt1["event_id"], "canonical_custom_id_1")

        sdb.close()

    def test_migration_interruption_and_restart(self) -> None:
        """Interrupted migration leaves staging directory which is safely cleaned up on restart."""
        slug = "proj_restart"
        self._create_legacy_project(slug, events_count=3)

        # Simulate left-over staging directory from previous crash/kill
        durable_root = get_durable_root(self.tmp_dir)
        abandoned_staging = durable_root / f".staging_{slug}_12345678"
        abandoned_staging.mkdir(parents=True, exist_ok=True)
        (abandoned_staging / "partial.tmp").write_text("incomplete", encoding="utf-8")

        self.assertTrue(abandoned_staging.exists())

        # Migrator should clean stale staging and successfully migrate
        result = self.migrator.migrate_project(slug)
        self.assertTrue(result.success)
        self.assertEqual(result.status, "migrated")

        # Old abandoned staging directory was cleaned
        self.assertFalse(abandoned_staging.exists())

        # Target directory is complete and verified
        target_dir = get_project_dir(self.tmp_dir, slug)
        self.assertTrue((target_dir / "session.db").is_file())
        self.assertTrue((target_dir / MIGRATION_MARKER_FILE).is_file())

    def test_incremental_auto_vacuum_and_vacuum_execution(self) -> None:
        """Verify PRAGMA auto_vacuum is set to INCREMENTAL and incremental vacuum runs."""
        from scripts.storage.schema_migration import execute_incremental_vacuum
        db_path = self.tmp_dir / "vacuum_test.db"
        conn = sqlite3.connect(str(db_path))
        apply_schema_migrations(conn)
        cur = conn.cursor()
        cur.execute("PRAGMA auto_vacuum;")
        av = cur.fetchone()[0]
        self.assertEqual(av, 2)
        remaining = execute_incremental_vacuum(conn, pages=50)
        self.assertIsInstance(remaining, int)
        conn.close()

    def test_artifact_size_inspection_and_retention_policy(self) -> None:
        """Verify per-artifact size calculation and retention policy pruning."""
        from scripts.storage.rotator import (
            StorageRotator,
            get_project_artifact_sizes,
            apply_artifact_retention_policy,
        )
        rotator = StorageRotator(self.tmp_dir)
        rotator.init_project("retention_test", str(self.tmp_dir / "target"))

        hist_dir = self.tmp_dir / "history" / "retention_test"
        hist_dir.mkdir(parents=True, exist_ok=True)
        for i in range(15):
            r_dir = hist_dir / f"run_2026100{i:02d}"
            r_dir.mkdir(parents=True, exist_ok=True)
            (r_dir / "data.txt").write_text("historical run data", encoding="utf-8")

        sizes = get_project_artifact_sizes(self.tmp_dir, "retention_test")
        self.assertIn("session_db", sizes)
        self.assertIn("total", sizes)
        self.assertGreater(sizes["history"], 0)

        res = apply_artifact_retention_policy(self.tmp_dir, "retention_test", max_history_runs=5)
        self.assertEqual(res["project"], "retention_test")
        self.assertGreater(res["freed_bytes"], 0)

        remaining_runs = [d for d in hist_dir.iterdir() if d.is_dir() and d.name.startswith("run_")]
        self.assertEqual(len(remaining_runs), 5)


if __name__ == "__main__":
    unittest.main()

