"""
tests/test_v17_sqlite_resilience_and_tier_archiving.py
======================================================
Unit tests for SQLite busy resilience, storage tier archiving, and any-tier project context resolution.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.storage import (
    SessionDB,
    StorageRotator,
    rotate_project_to_history,
    resolve_project_context,
    update_project_meta,
)
from scripts.server.db_queries import _open_ro_db, _read_events_from_db, _read_sessions_from_db


class TestSQLiteResilienceAndTierArchiving(unittest.TestCase):
    """Test suite covering SQLite concurrency pragmas, strict tier archiving, and any-tier resolution."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="spd_test_v17_")
        self.engine_root = Path(self.temp_dir).resolve()
        for tier in ("current", "last_run", "history"):
            (self.engine_root / tier).mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_sqlite_busy_timeout_and_wal_pragmas(self) -> None:
        """Verify SessionDB configures WAL mode, NORMAL synchronous, and 30-second busy timeout."""
        db_path = self.engine_root / "test_session.db"
        db = SessionDB(db_path)
        try:
            assert db.conn is not None
            cur = db.conn.cursor()

            cur.execute("PRAGMA busy_timeout;")
            busy_timeout = cur.fetchone()[0]
            self.assertGreaterEqual(busy_timeout, 30000)

            cur.execute("PRAGMA journal_mode;")
            journal_mode = cur.fetchone()[0]
            self.assertEqual(journal_mode.lower(), "wal")

            cur.execute("PRAGMA synchronous;")
            sync_mode = cur.fetchone()[0]
            # synchronous = 1 corresponds to NORMAL
            self.assertEqual(sync_mode, 1)
        finally:
            db.close()

    def test_02_open_ro_db_applies_busy_timeout(self) -> None:
        """Verify _open_ro_db sets busy timeout and successfully reads database."""
        db_path = self.engine_root / "ro_test.db"
        db = SessionDB(db_path)
        sid = db.create_session("ro_project", str(self.engine_root / "workspace"))
        db.record_event(sid, "EDIT", "file.py", "Test edit")
        db.close()

        ro_conn = _open_ro_db(db_path)
        try:
            cur = ro_conn.cursor()
            cur.execute("PRAGMA busy_timeout;")
            busy_timeout = cur.fetchone()[0]
            self.assertGreaterEqual(busy_timeout, 30000)

            events = _read_events_from_db(db_path)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["first_file_touched"], "file.py")

            sessions = _read_sessions_from_db(db_path)
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0]["project_name"], "ro_project")
        finally:
            ro_conn.close()

    def test_03_rotate_project_to_history(self) -> None:
        """Verify strict tier archiving rotates older runs to history and preserves run artifacts."""
        slug = "my_archive_project"
        curr_dir = self.engine_root / "current" / slug
        curr_dir.mkdir(parents=True, exist_ok=True)
        (curr_dir / "shadow_git").mkdir(parents=True, exist_ok=True)
        (curr_dir / "worker.log").write_text("Worker started run 1\n", encoding="utf-8")

        db1 = SessionDB(curr_dir / "session.db")
        sid1 = db1.create_session(slug, "/test/path1")
        db1.record_event(sid1, "EDIT", "run1.py", "Run 1 edit")
        db1.close()

        update_project_meta(self.engine_root, slug, target_path="/test/path1", status="stopped")

        # First rotation: current -> last_run
        success = rotate_project_to_history(slug, self.engine_root)
        self.assertTrue(success)
        self.assertFalse(curr_dir.exists())

        last_run_dir = self.engine_root / "last_run" / slug
        self.assertTrue(last_run_dir.exists())
        self.assertTrue((last_run_dir / "session.db").exists())
        self.assertTrue((last_run_dir / "project.meta").exists())
        self.assertTrue((last_run_dir / "worker.log").exists())
        self.assertTrue((last_run_dir / "shadow_git").exists())

        # Second run in current
        curr_dir.mkdir(parents=True, exist_ok=True)
        (curr_dir / "shadow_git").mkdir(parents=True, exist_ok=True)
        (curr_dir / "worker.log").write_text("Worker started run 2\n", encoding="utf-8")

        db2 = SessionDB(curr_dir / "session.db")
        sid2 = db2.create_session(slug, "/test/path2")
        db2.record_event(sid2, "EDIT", "run2.py", "Run 2 edit")
        db2.close()

        # Second rotation: previous last_run -> history/run_<ts>, current -> last_run
        success2 = rotate_project_to_history(slug, self.engine_root)
        self.assertTrue(success2)
        self.assertFalse(curr_dir.exists())

        hist_slug_dir = self.engine_root / "history" / slug
        self.assertTrue(hist_slug_dir.exists())
        hist_runs = [d for d in hist_slug_dir.iterdir() if d.is_dir() and d.name.startswith("run_")]
        self.assertEqual(len(hist_runs), 1)

        archived_run = hist_runs[0]
        self.assertTrue((archived_run / "session.db").exists())
        self.assertTrue((archived_run / "project.meta").exists())
        self.assertTrue((archived_run / "worker.log").exists())
        self.assertTrue((archived_run / "shadow_git").exists())

        # Verify active last_run has run 2
        events = _read_events_from_db(last_run_dir / "session.db")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["first_file_touched"], "run2.py")

    def test_04_resolve_project_context_across_all_tiers(self) -> None:
        """Verify resolve_project_context accurately finds target_path and db_path across tiers."""
        target_ws = self.engine_root / "workspace_test"
        target_ws.mkdir(parents=True, exist_ok=True)

        slug = "context_project"

        # Tier 1: history only
        hist_run = self.engine_root / "history" / slug / "run_20260929_000000_000000"
        hist_run.mkdir(parents=True, exist_ok=True)
        db_hist = SessionDB(hist_run / "session.db")
        db_hist.create_session(slug, str(target_ws))
        db_hist.close()
        (hist_run / "project.meta").write_text(f"target_path={target_ws}\nproject_name={slug}\n", encoding="utf-8")

        ctx_hist = resolve_project_context(slug, self.engine_root)
        self.assertEqual(ctx_hist["tier"], "history")
        self.assertEqual(ctx_hist["target_path"], target_ws)
        self.assertTrue(ctx_hist["db_path"].exists())

        # Tier 2: last_run exists
        last_run_dir = self.engine_root / "last_run" / slug
        last_run_dir.mkdir(parents=True, exist_ok=True)
        db_last = SessionDB(last_run_dir / "session.db")
        db_last.create_session(slug, str(target_ws))
        db_last.close()
        (last_run_dir / "project.meta").write_text(f"target_path={target_ws}\nproject_name={slug}\n", encoding="utf-8")

        ctx_last = resolve_project_context(slug, self.engine_root)
        self.assertEqual(ctx_last["tier"], "last_run")
        self.assertEqual(ctx_last["target_path"], target_ws)
        self.assertEqual(ctx_last["db_path"], last_run_dir / "session.db")

        # Tier 3: current exists
        curr_dir = self.engine_root / "current" / slug
        curr_dir.mkdir(parents=True, exist_ok=True)
        db_curr = SessionDB(curr_dir / "session.db")
        db_curr.create_session(slug, str(target_ws))
        db_curr.close()
        (curr_dir / "project.meta").write_text(f"target_path={target_ws}\nproject_name={slug}\n", encoding="utf-8")

        ctx_curr = resolve_project_context(slug, self.engine_root)
        self.assertEqual(ctx_curr["tier"], "current")
        self.assertEqual(ctx_curr["target_path"], target_ws)
        self.assertEqual(ctx_curr["db_path"], curr_dir / "session.db")

    def test_05_micro_modularity_and_line_limits(self) -> None:
        """Verify strict line count limits across the codebase."""
        rotator_file = Path("scripts/storage/rotator.py")
        self.assertTrue(rotator_file.exists())
        rotator_loc = len(rotator_file.read_text(encoding="utf-8").splitlines())
        self.assertGreaterEqual(rotator_loc, 60, "rotator.py must have at least 60 lines")
        self.assertLessEqual(rotator_loc, 200, f"rotator.py must not exceed 200 lines (currently {rotator_loc})")

        for py_path in Path("scripts").rglob("*.py"):
            loc = len(py_path.read_text(encoding="utf-8", errors="replace").splitlines())
            self.assertLessEqual(loc, 500, f"{py_path} exceeds 500 LOC limit (currently {loc})")


if __name__ == "__main__":
    unittest.main()
