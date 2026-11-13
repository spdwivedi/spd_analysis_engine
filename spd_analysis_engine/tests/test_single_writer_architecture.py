"""
tests/test_single_writer_architecture.py
=========================================
Comprehensive test suite for Phase 10: Authoritative Single-Writer Architecture.

Covers:
1. 8 x 5,000 concurrent logical writes (40,000 total writes across threads).
2. Zero lost durable rows (verifying all 40,000 rows committed and intact).
3. No transaction cross-contamination (failed/rolled-back operations in a batch
   do not corrupt or cancel subsequent batches).
4. GET does not touch WAL (read-only SQLite connections query the DB without
   modifying the WAL file size or timestamp).
5. Worker restart drains and reconciles pending writer operations (stopping
   with drain=True commits all pending writes before exit; restart resumes cleanly).
6. Write queue backpressure is observable (queue depth, dropped counter on best-effort
   writes under backpressure, metrics inspection).
7. Worker-owned vs Controller-owned data separation (session.db vs engine.db).
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import os
import shutil
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from scripts.controller.jobs import (
    init_controller_tables,
    create_job,
    update_job_state,
    get_job,
    record_ai_analysis,
    get_ai_analysis,
    set_controller_flag,
    get_controller_flag,
)
from scripts.storage.schema_migration import CURRENT_SCHEMA_VERSION, get_user_version
from scripts.storage.writer import ProjectWriter
from scripts.storage.writer_ops import WriteOperation, WriterMetrics


class TestSingleWriterArchitecture(unittest.TestCase):
    """Test suite verifying single-writer concurrency, durability, and isolation."""

    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="spd_writer_test_"))
        self.db_path = self.tmp_dir / "session.db"
        self.engine_db_path = self.tmp_dir / "engine.db"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # 1 & 2: 8 x 5,000 Concurrent Logical Writes & Zero Lost Durable Rows
    # -------------------------------------------------------------------------

    def test_8_x_5000_concurrent_writes_and_zero_lost_durable_rows(self) -> None:
        """
        Verify that 8 concurrent threads submitting 5,000 operations each
        (40,000 total writes) execute cleanly with zero lost durable rows.
        """
        writer = ProjectWriter(
            self.db_path,
            batch_window_s=0.05,
            max_batch_size=500,
            max_queue_size=50000,
        )

        try:
            session_id = writer.create_session("benchmark_proj", str(self.tmp_dir))
            self.assertGreater(session_id, 0)

            def submitter_worker(thread_idx: int) -> list[concurrent.futures.Future]:
                futures = []
                for i in range(5000):
                    logical_seq = (thread_idx * 5000) + i
                    fut = writer.submit_critical(
                        "event",
                        {
                            "session_id": session_id,
                            "event_type": "EDIT",
                            "first_file": f"src/module_{thread_idx}/file_{i}.py",
                            "summary": f"Thread {thread_idx} write #{i}",
                            "seq": logical_seq,
                            "burst_uid": f"burst-{thread_idx}-{i // 10}",
                        },
                    )
                    futures.append(fut)
                return futures

            start_t = time.perf_counter()
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                thread_future_lists = list(executor.map(submitter_worker, range(8)))

            # Wait on all 40,000 futures to verify resolution and no exceptions
            total_futures = 0
            for flist in thread_future_lists:
                for fut in flist:
                    row_id = fut.result(timeout=15.0)
                    self.assertIsInstance(row_id, int)
                    self.assertGreater(row_id, 0)
                    total_futures += 1

            self.assertEqual(total_futures, 40000)
            elapsed = time.perf_counter() - start_t

            # Verify writer state and metrics
            metrics = writer.get_metrics()
            self.assertEqual(metrics.total_submitted, 40001)  # 1 session + 40000 events
            self.assertEqual(metrics.total_committed, 40001)
            self.assertEqual(metrics.total_dropped, 0)
            self.assertEqual(metrics.queue_depth, 0)
        finally:
            writer.stop(drain=True)

        # Connect directly to database to verify row count and integrity
        direct_conn = sqlite3.connect(str(self.db_path))
        try:
            cursor = direct_conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM events WHERE session_id = ?", (session_id,))
            event_count = cursor.fetchone()[0]
            self.assertEqual(
                event_count,
                40000,
                f"Expected exactly 40,000 durable rows, but found {event_count}",
            )

            # Spot-check distinct sequence values
            cursor.execute("SELECT COUNT(DISTINCT seq) FROM events WHERE session_id = ?", (session_id,))
            distinct_seq_count = cursor.fetchone()[0]
            self.assertEqual(distinct_seq_count, 40000)
        finally:
            direct_conn.close()

    # -------------------------------------------------------------------------
    # 3: No Transaction Cross-Contamination
    # -------------------------------------------------------------------------

    def test_no_transaction_cross_contamination(self) -> None:
        """
        Verify that a failed operation causes that batch to roll back and raise,
        while subsequent batches commit cleanly without transaction corruption.
        """
        writer = ProjectWriter(self.db_path, batch_window_s=0.01, max_batch_size=10)
        try:
            session_id = writer.create_session("isolation_proj", str(self.tmp_dir))

            # Batch 1: Valid operation
            f_valid_1 = writer.submit_critical(
                "event",
                {"session_id": session_id, "event_type": "CREATE", "first_file": "clean_1.py"},
            )
            ev1_id = f_valid_1.result(timeout=5.0)
            self.assertGreater(ev1_id, 0)

            # Batch 2: Faulty operation (invalid SQL / missing table)
            f_faulty = writer.submit_critical(
                "sql",
                {"sql": "INSERT INTO table_that_does_not_exist (id) VALUES (999)"},
            )
            with self.assertRaises(sqlite3.OperationalError):
                f_faulty.result(timeout=5.0)

            # Batch 3: Valid operations following the failure
            f_valid_2 = writer.submit_critical(
                "event",
                {"session_id": session_id, "event_type": "EDIT", "first_file": "clean_2.py"},
            )
            f_valid_3 = writer.submit_critical(
                "patch",
                {"event_id": ev1_id, "file_path": "clean_1.py", "diff_content": "+line1\n"},
            )

            ev2_id = f_valid_2.result(timeout=5.0)
            patch_id = f_valid_3.result(timeout=5.0)
            self.assertGreater(ev2_id, 0)
            self.assertGreater(patch_id, 0)

            # Verify that only the 2 valid events exist in the database
            metrics = writer.get_metrics()
            self.assertGreaterEqual(metrics.total_committed, 3)
        finally:
            writer.stop(drain=True)

        # Verify database state
        direct_conn = sqlite3.connect(str(self.db_path))
        try:
            cursor = direct_conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM events")
            self.assertEqual(cursor.fetchone()[0], 2)
            cursor.execute("SELECT COUNT(*) FROM patches")
            self.assertEqual(cursor.fetchone()[0], 1)
        finally:
            direct_conn.close()

    # -------------------------------------------------------------------------
    # 4: GET Does Not Touch WAL
    # -------------------------------------------------------------------------

    def test_get_does_not_touch_wal(self) -> None:
        """
        Verify that readers using read-only connections (?mode=ro, PRAGMA query_only = ON)
        do not modify the WAL file size, timestamp, or acquire write locks.
        """
        writer = ProjectWriter(self.db_path, batch_window_s=0.02)
        try:
            session_id = writer.create_session("wal_test_proj", str(self.tmp_dir))
            for i in range(25):
                writer.record_event(session_id, "EDIT", f"file_{i}.py")

            wal_path = Path(str(self.db_path) + "-wal")
            self.assertTrue(wal_path.exists(), "WAL file should exist after active writes")

            # Snapshot WAL file properties
            st_before = wal_path.stat()
            size_before = st_before.st_size
            mtime_before = st_before.st_mtime_ns

            with open(wal_path, "rb") as f:
                hash_before = hashlib.sha256(f.read()).hexdigest()

            # Execute 200 read-only queries (simulating GET requests)
            ro_conn = writer.open_ro_connection()
            try:
                for _ in range(200):
                    cursor = ro_conn.cursor()
                    cursor.execute("SELECT COUNT(*), MAX(id) FROM events")
                    cnt, max_id = cursor.fetchone()
                    self.assertEqual(cnt, 25)
                    self.assertGreater(max_id, 0)

                    # Verify PRAGMA query_only is respected
                    with self.assertRaises(sqlite3.OperationalError):
                        cursor.execute("INSERT INTO events (session_id, event_type) VALUES (1, 'FAIL')")
            finally:
                ro_conn.close()

            # Verify WAL properties after all GET reads
            st_after = wal_path.stat()
            size_after = st_after.st_size
            mtime_after = st_after.st_mtime_ns

            with open(wal_path, "rb") as f:
                hash_after = hashlib.sha256(f.read()).hexdigest()

            self.assertEqual(size_before, size_after, "WAL size must remain strictly unchanged by GET queries")
            self.assertEqual(mtime_before, mtime_after, "WAL mtime must remain strictly unchanged by GET queries")
            self.assertEqual(hash_before, hash_after, "WAL content must remain bit-for-bit identical")
        finally:
            writer.stop(drain=True)

    # -------------------------------------------------------------------------
    # 5: Worker Restart Drains and Reconciles Pending Writer Operations
    # -------------------------------------------------------------------------

    def test_worker_restart_drains_and_reconciles(self) -> None:
        """
        Verify that stopping the writer with drain=True commits all pending writes,
        and restarting a new ProjectWriter on the same database resumes cleanly
        without data loss or corruption.
        """
        writer1 = ProjectWriter(self.db_path, batch_window_s=0.05, max_batch_size=200)
        session_id = writer1.create_session("restart_proj", str(self.tmp_dir), worker_id="worker-old")

        # Submit 100 critical writes and 100 best-effort writes
        futures = []
        for i in range(100):
            fut = writer1.submit_critical(
                "event",
                {"session_id": session_id, "event_type": "EDIT", "first_file": f"w1_crit_{i}.py"},
            )
            futures.append(fut)
            writer1.submit_best_effort(
                "event",
                {"session_id": session_id, "event_type": "READ", "first_file": f"w1_be_{i}.py"},
            )

        # Graceful worker shutdown: drains all queued writes
        writer1.stop(drain=True)
        self.assertFalse(writer1.is_running)

        # Start replacement worker with new ProjectWriter on the same db
        writer2 = ProjectWriter(self.db_path, batch_window_s=0.05, max_batch_size=200)
        try:
            self.assertTrue(writer2.is_running)

            # Close old session and start new session
            writer2.close_session(session_id, status="STOPPED", end_reason="worker_restart")
            session_id_2 = writer2.create_session("restart_proj", str(self.tmp_dir), worker_id="worker-new")

            # Submit writes under new worker
            for i in range(50):
                writer2.record_event(session_id_2, "EDIT", f"w2_file_{i}.py")
        finally:
            writer2.stop(drain=True)

        # Verify all operations from both worker lifecycles are persisted
        direct_conn = sqlite3.connect(str(self.db_path))
        try:
            cursor = direct_conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM sessions")
            self.assertEqual(cursor.fetchone()[0], 2)

            cursor.execute("SELECT status, end_reason FROM sessions WHERE id = ?", (session_id,))
            s1_row = cursor.fetchone()
            self.assertEqual(s1_row[0], "STOPPED")
            self.assertEqual(s1_row[1], "worker_restart")

            cursor.execute("SELECT COUNT(*) FROM events WHERE session_id = ?", (session_id,))
            s1_events = cursor.fetchone()[0]
            self.assertGreaterEqual(s1_events, 100)

            cursor.execute("SELECT COUNT(*) FROM events WHERE session_id = ?", (session_id_2,))
            s2_events = cursor.fetchone()[0]
            self.assertEqual(s2_events, 50)
        finally:
            direct_conn.close()

    # -------------------------------------------------------------------------
    # 6: Write Queue Backpressure is Observable
    # -------------------------------------------------------------------------

    def test_write_queue_backpressure_is_observable(self) -> None:
        """
        Verify backpressure behavior:
        - When queue is full, best-effort submissions drop and increment drop_counter.
        - Queue depth, dropped items, and backpressure_active flag are visible in metrics.
        """
        # Create writer with tiny queue and large batch window so queue fills up quickly
        writer = ProjectWriter(
            self.db_path,
            batch_window_s=0.5,
            max_batch_size=5,
            max_queue_size=10,
        )
        try:
            session_id = writer.create_session("backpressure_proj", str(self.tmp_dir))

            dropped_count = 0
            accepted_count = 0
            for i in range(40):
                ok = writer.submit_best_effort(
                    "event",
                    {"session_id": session_id, "event_type": "READ", "first_file": f"telemetry_{i}.py"},
                )
                if ok:
                    accepted_count += 1
                else:
                    dropped_count += 1

            # Dropped counter must be greater than zero due to queue capacity
            self.assertGreater(dropped_count, 0)
            self.assertEqual(writer.drop_counter, dropped_count)

            metrics = writer.get_metrics()
            self.assertEqual(metrics.total_dropped, dropped_count)
            self.assertGreaterEqual(metrics.max_queue_size, 10)
            self.assertIsInstance(metrics.backpressure_active, bool)
            self.assertIsInstance(metrics.last_batch_duration_ms, float)
        finally:
            writer.stop(drain=True)

    # -------------------------------------------------------------------------
    # 7: Worker-Owned Data vs Controller-Owned Data Separation
    # -------------------------------------------------------------------------

    def test_worker_and_controller_data_separation(self) -> None:
        """
        Verify worker-owned tables in session.db (events, patches, sessions, manifest,
        git_attribution, capture_metadata) and controller-owned tables in engine.db
        (jobs, ai_analyses, controller_flags).
        """
        # 1. Test worker-owned tables via ProjectWriter
        writer = ProjectWriter(self.db_path)
        try:
            sess_id = writer.create_session("separation_test", str(self.tmp_dir), actor="test_actor")
            ev_id = writer.record_event(sess_id, "EDIT", "main.py", summary="edit main")
            p_id = writer.record_patch(ev_id, "main.py", diff_content="@@ -1 +1 @@", adds=1, dels=1)
            man_key = writer.record_manifest("main.py", "hash123", 1024, "2026-10-02T12:00:00Z")
            git_id = writer.record_git_attribution(sess_id, "a1b2c3d", author_name="Dev", author_email="dev@example.com")
            meta_key = writer.record_capture_metadata("daemon_version", "1.0.0", session_id=sess_id)

            self.assertGreater(sess_id, 0)
            self.assertGreater(ev_id, 0)
            self.assertGreater(p_id, 0)
            self.assertEqual(man_key, "main.py")
            self.assertGreater(git_id, 0)
            self.assertEqual(meta_key, "daemon_version")
        finally:
            writer.stop(drain=True)

        # Verify session.db schema version is at least 3
        conn_session = sqlite3.connect(str(self.db_path))
        try:
            self.assertGreaterEqual(get_user_version(conn_session), 3)
            cursor = conn_session.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {row[0] for row in cursor.fetchall()}
            self.assertTrue({"sessions", "events", "patches", "manifest", "git_attribution", "capture_metadata"}.issubset(tables))
            # Controller tables MUST NOT be in session.db
            self.assertNotIn("jobs", tables)
            self.assertNotIn("ai_analyses", tables)
            self.assertNotIn("controller_flags", tables)
        finally:
            conn_session.close()

        # 2. Test controller-owned tables in engine.db
        conn_engine = sqlite3.connect(str(self.engine_db_path))
        conn_engine.row_factory = sqlite3.Row
        try:
            # Need projects table for foreign key reference
            conn_engine.execute("""
                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    slug TEXT UNIQUE NOT NULL,
                    display_name TEXT NOT NULL,
                    root_path TEXT NOT NULL,
                    state TEXT NOT NULL,
                    config_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)
            conn_engine.execute("""
                INSERT INTO projects VALUES ('proj-1', 'test-slug', 'Test', '/path', 'ACTIVE', '{}', '2026-10-02', '2026-10-02');
            """)
            init_controller_tables(conn_engine)

            # Test Jobs
            job = create_job(conn_engine, "job-101", "proj-1", "repair", payload={"check": True})
            self.assertEqual(job.state, "PENDING")
            update_job_state(conn_engine, "job-101", "COMPLETED", result={"status": "repaired"})
            job_updated = get_job(conn_engine, "job-101")
            self.assertIsNotNone(job_updated)
            self.assertEqual(job_updated.state, "COMPLETED")

            # Test AI Analyses
            ai_rec = record_ai_analysis(
                conn_engine, "ai-201", "proj-1", "burst", "burst-1",
                prompt="Summarize diffs", response="Clean changes", model="gemini-flash"
            )
            self.assertEqual(ai_rec.analysis_id, "ai-201")
            fetched_ai = get_ai_analysis(conn_engine, "ai-201")
            self.assertIsNotNone(fetched_ai)
            self.assertEqual(fetched_ai.response, "Clean changes")

            # Test Controller Flags
            set_controller_flag(conn_engine, "enable_shadow_git", True, description="Enable shadow git")
            self.assertTrue(get_controller_flag(conn_engine, "enable_shadow_git"))
            set_controller_flag(conn_engine, "enable_shadow_git", False)
            self.assertFalse(get_controller_flag(conn_engine, "enable_shadow_git"))

            # Session tables MUST NOT be in engine.db
            cursor = conn_engine.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            engine_tables = {row[0] for row in cursor.fetchall()}
            self.assertTrue({"jobs", "ai_analyses", "controller_flags"}.issubset(engine_tables))
            self.assertNotIn("patches", engine_tables)
            self.assertNotIn("manifest", engine_tables)
        finally:
            conn_engine.close()


if __name__ == "__main__":
    unittest.main()
