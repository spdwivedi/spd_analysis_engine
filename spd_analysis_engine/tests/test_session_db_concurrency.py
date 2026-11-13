"""
tests/test_session_db_concurrency.py
====================================
Concurrency and writer ownership regression suite for SessionDB:
  - 8 threads x 5,000 writes stress test (no lost rows)
  - Concurrent read/write with read-only connections
  - Rollback isolation across threads and nested savepoints
  - Duplicate READ event detection and stable event identity
  - ExecutionTracker single ownership for command-referenced file reads
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

# Ensure engine package is importable
ENGINE_ROOT = Path(__file__).resolve().parent.parent
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from scripts.storage.db import SessionDB
from scripts.shell.tracker import ExecutionTracker


class TestSessionDBConcurrencyAndOwnership(unittest.TestCase):
    """Test multi-threaded concurrency, rollback isolation, and READ deduplication."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_sdb_concurrency_"))
        self.db_path = self.temp_dir / "session.db"
        self.sdb = SessionDB(self.db_path)
        self.sid = self.sdb.create_session("concurrency_proj", str(self.temp_dir))

    def tearDown(self) -> None:
        try:
            self.sdb.close()
        except Exception:
            pass
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_8_threads_5000_writes_no_lost_rows(self) -> None:
        """8 threads executing 5,000 writes each must commit 40,000 distinct events with zero lost rows."""
        num_threads = 8
        writes_per_thread = 5000
        expected_total = num_threads * writes_per_thread

        errors: list[Exception] = []
        recorded_ids: list[list[int]] = [[] for _ in range(num_threads)]

        def _worker(thread_idx: int) -> None:
            try:
                for i in range(writes_per_thread):
                    eid = self.sdb.record_event(
                        session_id=self.sid,
                        event_type="EDIT",
                        first_file=f"file_t{thread_idx}_{i}.py",
                        summary=f"Edit {i} by thread {thread_idx}",
                    )
                    recorded_ids[thread_idx].append(eid)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_worker, args=(t,)) for t in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Concurrent writes encountered errors: {errors}")

        all_ids: list[int] = []
        for t_ids in recorded_ids:
            all_ids.extend(t_ids)

        self.assertEqual(len(all_ids), expected_total, f"Expected {expected_total} IDs, got {len(all_ids)}")
        self.assertEqual(len(set(all_ids)), expected_total, "Duplicate event IDs generated during concurrent writes")

        # Verify on-disk row count
        cur = self.sdb.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM events WHERE session_id = ?", (self.sid,))
        actual_count = cur.fetchone()[0]
        self.assertEqual(
            actual_count,
            expected_total,
            f"Lost rows detected: expected {expected_total} rows, found {actual_count} in DB",
        )

    def test_concurrent_read_write(self) -> None:
        """Concurrent readers using read-only connections must not collide or crash with active writers."""
        stop_event = threading.Event()
        errors: list[Exception] = []
        read_counts: list[int] = [0, 0, 0, 0]

        def _writer(writer_idx: int) -> None:
            try:
                i = 0
                while not stop_event.is_set():
                    self.sdb.record_event(
                        session_id=self.sid,
                        event_type="EDIT",
                        first_file=f"w_{writer_idx}_{i}.py",
                        summary=f"write {i}",
                    )
                    i += 1
                    time.sleep(0.001)
            except Exception as exc:
                errors.append(exc)

        def _reader(reader_idx: int) -> None:
            try:
                ro_conn = self.sdb.open_ro_connection()
                while not stop_event.is_set():
                    # Read via independent read-only connection
                    cur = ro_conn.execute("SELECT COUNT(*) FROM events WHERE session_id = ?", (self.sid,))
                    _ = cur.fetchone()[0]
                    # Also read via public SessionDB get_events
                    evts = self.sdb.get_events(self.sid)
                    self.assertIsInstance(evts, list)
                    read_counts[reader_idx] += 1
                    time.sleep(0.002)
                ro_conn.close()
            except Exception as exc:
                errors.append(exc)

        writers = [threading.Thread(target=_writer, args=(w,)) for w in range(3)]
        readers = [threading.Thread(target=_reader, args=(r,)) for r in range(3)]

        for t in readers + writers:
            t.start()

        # Let them race concurrently
        time.sleep(1.0)
        stop_event.set()

        for t in readers + writers:
            t.join()

        self.assertEqual(len(errors), 0, f"Concurrent read/write encountered errors: {errors}")
        for idx, count in enumerate(read_counts[:3]):
            self.assertGreater(count, 0, f"Reader {idx} completed zero reads")

    def test_rollback_isolation(self) -> None:
        """One thread's rollback must not roll back or corrupt another thread's concurrent transaction."""
        errors: list[Exception] = []
        thread_b_committed_id: int | None = None

        def _failing_thread_a() -> None:
            try:
                with self.sdb.transaction() as cur:
                    cur.execute(
                        "INSERT INTO events (session_id, event_type, timestamp, summary) VALUES (?, ?, ?, ?)",
                        (self.sid, "EDIT", "2026-10-02T00:00:00Z", "Thread A doomed event"),
                    )
                    raise RuntimeError("Simulated failure in Thread A")
            except RuntimeError:
                pass
            except Exception as exc:
                errors.append(exc)

        def _successful_thread_b() -> None:
            nonlocal thread_b_committed_id
            try:
                time.sleep(0.01)
                with self.sdb.transaction() as cur:
                    cur.execute(
                        "INSERT INTO events (session_id, event_type, timestamp, summary) VALUES (?, ?, ?, ?)",
                        (self.sid, "EDIT", "2026-10-02T00:00:01Z", "Thread B committed event"),
                    )
                    thread_b_committed_id = cur.lastrowid
            except Exception as exc:
                errors.append(exc)

        ta = threading.Thread(target=_failing_thread_a)
        tb = threading.Thread(target=_successful_thread_b)

        ta.start()
        tb.start()
        ta.join()
        tb.join()

        self.assertEqual(len(errors), 0, f"Unexpected errors during rollback isolation test: {errors}")
        self.assertIsNotNone(thread_b_committed_id)

        cur = self.sdb.conn.cursor()
        cur.execute("SELECT summary FROM events WHERE session_id = ?", (self.sid,))
        summaries = [row[0] for row in cur.fetchall()]

        self.assertNotIn("Thread A doomed event", summaries, "Thread A event was committed despite rollback!")
        self.assertIn("Thread B committed event", summaries, "Thread B event was lost due to Thread A's rollback!")

    def test_savepoint_nested_rollback_isolation(self) -> None:
        """Nested savepoint transactions must allow partial rollback without destroying the outer transaction."""
        with self.sdb.transaction() as outer_cur:
            outer_cur.execute(
                "INSERT INTO events (session_id, event_type, timestamp, summary) VALUES (?, ?, ?, ?)",
                (self.sid, "EDIT", "2026-10-02T00:00:00Z", "Outer Step 1"),
            )

            # Inner failing transaction using savepoint
            try:
                with self.sdb.transaction() as inner_cur:
                    inner_cur.execute(
                        "INSERT INTO events (session_id, event_type, timestamp, summary) VALUES (?, ?, ?, ?)",
                        (self.sid, "EDIT", "2026-10-02T00:00:01Z", "Inner Step 2 (Doomed)"),
                    )
                    raise ValueError("Inner failure")
            except ValueError:
                pass  # Inner failure handled

            outer_cur.execute(
                "INSERT INTO events (session_id, event_type, timestamp, summary) VALUES (?, ?, ?, ?)",
                (self.sid, "EDIT", "2026-10-02T00:00:02Z", "Outer Step 3"),
            )

        cur = self.sdb.conn.cursor()
        cur.execute("SELECT summary FROM events WHERE session_id = ? ORDER BY id", (self.sid,))
        rows = [r[0] for r in cur.fetchall()]

        self.assertEqual(rows, ["Outer Step 1", "Outer Step 3"])

    def test_duplicate_read_detection_and_identity(self) -> None:
        """Multiple identical READ events for the same file in a session must deduplicate and return stable ID."""
        file_path = "src/components/App.tsx"
        read_ids: list[int] = []
        errors: list[Exception] = []

        def _recorder(idx: int) -> None:
            try:
                eid = self.sdb.record_event(
                    session_id=self.sid,
                    event_type="READ",
                    first_file=file_path,
                    summary=f"Inspected by thread {idx}",
                )
                read_ids.append(eid)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_recorder, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Errors during concurrent READ recording: {errors}")
        self.assertEqual(len(read_ids), 20)

        # All 20 calls must return the identical canonical event ID
        canonical_id = read_ids[0]
        self.assertTrue(all(rid == canonical_id for rid in read_ids), "Duplicate READ generated divergent event IDs")

        # Exactly 1 row in the database
        cur = self.sdb.conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM events WHERE session_id = ? AND event_type = 'READ' AND first_file_touched = ?",
            (self.sid, file_path),
        )
        self.assertEqual(cur.fetchone()[0], 1, "Database contains multiple rows for deduplicated READ event")

        # Distinct file must receive its own distinct event ID
        other_id = self.sdb.record_event(
            session_id=self.sid,
            event_type="READ",
            first_file="src/utils/math.ts",
            summary="Inspected other file",
        )
        self.assertNotEqual(canonical_id, other_id)

        # An EDIT event on the same file is not suppressed as a READ
        edit_id = self.sdb.record_event(
            session_id=self.sid,
            event_type="EDIT",
            first_file=file_path,
            summary="Modified App.tsx",
        )
        self.assertNotEqual(canonical_id, edit_id)

    def test_execution_tracker_read_single_ownership(self) -> None:
        """ExecutionTracker.record_execution must delegate READs to on_read and not insert duplicate READ events."""
        received_reads: list[dict] = []

        def _mock_on_read(read_info: dict) -> None:
            received_reads.append(read_info)
            # Simulate worker_lifecycle._on_file_read behavior: single owner inserts into DB
            self.sdb.record_event(
                session_id=self.sid,
                event_type="READ",
                first_file=read_info.get("file_path"),
                summary=f"File inspected via callback: {read_info.get('file_path')}",
            )

        # Create a dummy workspace file
        src_file = self.temp_dir / "calc.py"
        src_file.write_text("print(42)\n", encoding="utf-8")

        tracker = ExecutionTracker(
            target_dir=self.temp_dir,
            db=self.sdb,
            session_id=self.sid,
            on_read=_mock_on_read,
        )

        # Execute command referencing calc.py
        tracker.record_execution(command="python calc.py", duration_s=0.1, exit_code=0)

        # Verify on_read received exactly 1 callback
        self.assertEqual(len(received_reads), 1)
        self.assertEqual(received_reads[0]["file_path"], "calc.py")

        # Verify database has exactly 1 READ row for calc.py
        cur = self.sdb.conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM events WHERE session_id = ? AND event_type = 'READ' AND first_file_touched = 'calc.py'",
            (self.sid,),
        )
        read_count = cur.fetchone()[0]
        self.assertEqual(
            read_count,
            1,
            f"Expected exactly 1 READ row in DB, found {read_count} (duplicate insert occurred)",
        )


if __name__ == "__main__":
    unittest.main()
