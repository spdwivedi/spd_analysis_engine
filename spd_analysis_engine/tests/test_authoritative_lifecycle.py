"""
tests/test_authoritative_lifecycle.py
=====================================
Comprehensive tests for the authoritative lifecycle controller:
- 50 concurrent start calls => exactly one worker spawned
- concurrent stop/start => strictly serialized
- idempotent start
- idempotent stop
- invalid state transitions rejected explicitly
- operation lock contention returns 409-equivalent behavior
- versioned schema and minimum required table columns
- orphan recovery and lifecycle restart
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from spd_analysis_engine.scripts.controller import (
    LifecycleController,
    EngineRegistry,
    ProjectState,
    InvalidStateTransitionError,
    EngineLock,
    ProjectOperationLock,
    OperationLockConflictError,
    transition_state,
    is_valid_transition,
    reconcile_orphans,
)


class TestAuthoritativeLifecycle(unittest.TestCase):
    """Test suite for the authoritative lifecycle controller layer."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.engine_root = Path(self.temp_dir.name).resolve()
        self.engine_db_path = self.engine_root / "engine.db"

        # Mock worker spawner tracking process creation
        self.spawn_count = 0
        self.spawn_lock = threading.Lock()

        def mock_spawner(project, worker_id):
            with self.spawn_lock:
                self.spawn_count += 1
                pid = 90000 + self.spawn_count
                return pid, time.time()

        self.mock_spawner = mock_spawner
        self.controller = LifecycleController(
            engine_root=self.engine_root,
            db_path=self.engine_db_path,
            worker_spawner=self.mock_spawner,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    # -------------------------------------------------------------------------
    # 1. 50 concurrent start calls => one worker
    # -------------------------------------------------------------------------

    def test_50_concurrent_start_calls_yields_one_worker(self):
        """50 concurrent start calls for the same project must spawn exactly one worker."""
        project_name = "race_project"
        project_path = str(self.engine_root / "race_ws")
        Path(project_path).mkdir(parents=True, exist_ok=True)

        num_threads = 50
        barrier = threading.Barrier(num_threads)
        results = [None] * num_threads
        exceptions = [None] * num_threads

        def start_worker_task(idx: int):
            try:
                barrier.wait()
                # All threads call start_project simultaneously with lock timeout
                proj = self.controller.start_project(
                    project_name,
                    path=project_path,
                    lock_timeout_s=30.0,
                )
                results[idx] = proj
            except Exception as e:
                exceptions[idx] = e

        threads = [threading.Thread(target=start_worker_task, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=35.0)

        # Ensure no thread encountered an unexpected exception
        for i, exc in enumerate(exceptions):
            self.assertIsNone(exc, f"Thread {i} failed: {exc}")

        # Ensure all 50 threads returned an active project record
        for i, res in enumerate(results):
            self.assertIsNotNone(res, f"Thread {i} returned None")
            self.assertEqual(res.state, ProjectState.ACTIVE.value)

        # INVARIANT: Only 1 worker was spawned by the spawner
        self.assertEqual(
            self.spawn_count,
            1,
            f"Expected exactly 1 worker to be spawned across 50 calls, got {self.spawn_count}",
        )

        # Verify database workers table has exactly one worker for this project
        proj = self.controller.resolve_project(project_name)
        self.assertIsNotNone(proj)
        workers = self.controller.registry.get_workers_for_project(proj.project_id)
        self.assertEqual(len(workers), 1, f"Expected 1 worker in db, found {len(workers)}")
        self.assertEqual(workers[0].state, ProjectState.ACTIVE.value)

    # -------------------------------------------------------------------------
    # 2. Concurrent stop/start => serialized
    # -------------------------------------------------------------------------

    def test_concurrent_stop_start_serialized(self):
        """Concurrent stop and start operations must be serialized via ProjectOperationLock."""
        project_name = "serialize_project"
        project_path = str(self.engine_root / "serialize_ws")
        Path(project_path).mkdir(parents=True, exist_ok=True)

        # Initial start
        proj = self.controller.start_project(project_name, path=project_path)
        self.assertEqual(proj.state, ProjectState.ACTIVE.value)

        barrier = threading.Barrier(2)
        outcomes = {}

        def op_stop():
            barrier.wait()
            res = self.controller.stop_project(project_name, lock_timeout_s=15.0)
            outcomes["stop"] = res.state

        def op_start():
            barrier.wait()
            res = self.controller.start_project(project_name, lock_timeout_s=15.0)
            outcomes["start"] = res.state

        t1 = threading.Thread(target=op_stop)
        t2 = threading.Thread(target=op_start)
        t1.start()
        t2.start()
        t1.join(timeout=20.0)
        t2.join(timeout=20.0)

        # Both operations must complete cleanly without deadlocking
        self.assertIn("stop", outcomes)
        self.assertIn("start", outcomes)

        # The final state must be either ACTIVE or STOPPED (valid states)
        final_proj = self.controller.resolve_project(project_name)
        self.assertIn(final_proj.state, (ProjectState.ACTIVE.value, ProjectState.STOPPED.value))

        # Check operations audit table in engine.db: all completed
        ops = self.controller.registry.list_operations_for_project(final_proj.project_id)
        self.assertGreaterEqual(len(ops), 3)  # Initial start + stop + start
        for op in ops:
            self.assertEqual(op.state, "completed", f"Operation {op.operation_id} did not complete cleanly")

    # -------------------------------------------------------------------------
    # 3. Idempotent start
    # -------------------------------------------------------------------------

    def test_idempotent_start(self):
        """Starting an already active project must return immediately without spawning a new worker."""
        project_name = "idem_start_proj"
        project_path = str(self.engine_root / "idem_start_ws")
        Path(project_path).mkdir(parents=True, exist_ok=True)

        # Start 1
        p1 = self.controller.start_project(project_name, path=project_path)
        self.assertEqual(p1.state, ProjectState.ACTIVE.value)
        self.assertEqual(self.spawn_count, 1)

        # Start 2 (idempotent)
        p2 = self.controller.start_project(project_name, path=project_path)
        self.assertEqual(p2.state, ProjectState.ACTIVE.value)
        self.assertEqual(self.spawn_count, 1, "Second start call should not spawn a new worker")
        self.assertEqual(p1.project_id, p2.project_id)

    # -------------------------------------------------------------------------
    # 4. Idempotent stop
    # -------------------------------------------------------------------------

    def test_idempotent_stop(self):
        """Stopping an already stopped project must succeed cleanly without errors."""
        project_name = "idem_stop_proj"
        project_path = str(self.engine_root / "idem_stop_ws")
        Path(project_path).mkdir(parents=True, exist_ok=True)

        # Register project in STOPPED state
        proj = self.controller.register_project(project_name, project_path)
        self.assertEqual(proj.state, ProjectState.STOPPED.value)

        # Stop 1 (idempotent on STOPPED)
        s1 = self.controller.stop_project(project_name)
        self.assertEqual(s1.state, ProjectState.STOPPED.value)

        # Stop 2 (idempotent)
        s2 = self.controller.stop_project(project_name)
        self.assertEqual(s2.state, ProjectState.STOPPED.value)

    # -------------------------------------------------------------------------
    # 5. Invalid transition rejected
    # -------------------------------------------------------------------------

    def test_invalid_transition_rejected(self):
        """Illegal state transitions must fail explicitly with InvalidStateTransitionError."""
        # Function level validation
        self.assertFalse(is_valid_transition("STOPPED", "ACTIVE"))
        self.assertFalse(is_valid_transition("STOPPED", "STOPPING"))
        self.assertFalse(is_valid_transition("ACTIVE", "STARTING"))
        self.assertFalse(is_valid_transition("ACTIVE", "STOPPED"))  # Must go through STOPPING
        self.assertFalse(is_valid_transition("ACTIVE", "FAILED"))

        # Transition function throws InvalidStateTransitionError
        with self.assertRaises(InvalidStateTransitionError):
            transition_state("STOPPED", "ACTIVE")

        with self.assertRaises(InvalidStateTransitionError):
            transition_state("ACTIVE", "STARTING")

        with self.assertRaises(InvalidStateTransitionError):
            transition_state("STOPPING", "ACTIVE")

        # Database registry enforcement
        proj = self.controller.register_project("invalid_trans_proj", str(self.engine_root / "inv_ws"))
        self.assertEqual(proj.state, ProjectState.STOPPED.value)

        with self.assertRaises(InvalidStateTransitionError):
            self.controller.registry.transition_project_state(proj.project_id, ProjectState.ACTIVE.value)

        # Valid transitions succeed
        proj = self.controller.registry.transition_project_state(proj.project_id, ProjectState.STARTING.value)
        self.assertEqual(proj.state, ProjectState.STARTING.value)
        proj = self.controller.registry.transition_project_state(proj.project_id, ProjectState.INDEXING.value)
        self.assertEqual(proj.state, ProjectState.INDEXING.value)
        proj = self.controller.registry.transition_project_state(proj.project_id, ProjectState.ACTIVE.value)
        self.assertEqual(proj.state, ProjectState.ACTIVE.value)
        proj = self.controller.registry.transition_project_state(proj.project_id, ProjectState.STOPPING.value)
        self.assertEqual(proj.state, ProjectState.STOPPING.value)
        proj = self.controller.registry.transition_project_state(proj.project_id, ProjectState.STOPPED.value)
        self.assertEqual(proj.state, ProjectState.STOPPED.value)

        # any -> ORPHANED is allowed
        proj = self.controller.registry.transition_project_state(proj.project_id, ProjectState.ORPHANED.value)
        self.assertEqual(proj.state, ProjectState.ORPHANED.value)

        # ORPHANED -> STARTING is allowed
        proj = self.controller.registry.transition_project_state(proj.project_id, ProjectState.STARTING.value)
        self.assertEqual(proj.state, ProjectState.STARTING.value)

        # STARTING -> FAILED is allowed
        proj = self.controller.registry.transition_project_state(proj.project_id, ProjectState.FAILED.value)
        self.assertEqual(proj.state, ProjectState.FAILED.value)

        # FAILED -> STARTING is allowed
        proj = self.controller.registry.transition_project_state(proj.project_id, ProjectState.STARTING.value)
        self.assertEqual(proj.state, ProjectState.STARTING.value)

        # STARTING -> INTERRUPTED is allowed
        proj = self.controller.registry.transition_project_state(proj.project_id, ProjectState.INTERRUPTED.value)
        self.assertEqual(proj.state, ProjectState.INTERRUPTED.value)

        # INTERRUPTED -> STARTING is allowed
        proj = self.controller.registry.transition_project_state(proj.project_id, ProjectState.STARTING.value)
        self.assertEqual(proj.state, ProjectState.STARTING.value)

    # -------------------------------------------------------------------------
    # 6. Operation lock contention returns 409-equivalent behavior
    # -------------------------------------------------------------------------

    def test_operation_lock_contention_returns_409(self):
        """Holding a project operation lock must cause non-blocking callers to raise OperationLockConflictError (409)."""
        proj = self.controller.register_project("lock_contention_proj", str(self.engine_root / "lock_ws"))
        lock_a = ProjectOperationLock(self.engine_root, proj.project_id, timeout_s=5.0)
        lock_b = ProjectOperationLock(self.engine_root, proj.project_id, timeout_s=0.0)

        # Thread A acquires lock
        lock_a.acquire()
        try:
            # Thread B attempts non-blocking acquire and must encounter 409 conflict
            with self.assertRaises(OperationLockConflictError) as ctx:
                lock_b.acquire(timeout_s=0.0)

            err = ctx.exception
            self.assertEqual(err.status_code, 409)
            self.assertEqual(err.project_id, proj.project_id)
            self.assertIn("Operation lock conflict", str(err))

            # Attempting controller.start_project with 0 timeout must also raise 409 conflict
            with self.assertRaises(OperationLockConflictError) as ctx_ctrl:
                self.controller.start_project(proj.project_id, lock_timeout_s=0.0)
            self.assertEqual(ctx_ctrl.exception.status_code, 409)

        finally:
            lock_a.release()

        # After Thread A releases lock, acquiring lock_b succeeds
        acquired = lock_b.acquire(timeout_s=1.0)
        self.assertTrue(acquired)
        lock_b.release()

    # -------------------------------------------------------------------------
    # 7. Database schema and minimum required columns
    # -------------------------------------------------------------------------

    def test_versioned_schema_and_minimum_columns(self):
        """engine.db must exist with versioned schema and all required table columns."""
        conn = sqlite3.connect(str(self.engine_db_path))
        conn.row_factory = sqlite3.Row
        try:
            # engine_meta
            meta = dict(conn.execute("SELECT key, value FROM engine_meta").fetchall())
            self.assertEqual(meta.get("schema_version"), "1")
            self.assertIn("created_at", meta)

            # Table schemas
            def get_cols(table: str) -> set[str]:
                return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}

            # projects columns
            project_cols = get_cols("projects")
            expected_proj = {
                "project_id", "slug", "display_name", "root_path",
                "state", "config_json", "created_at", "updated_at",
            }
            self.assertTrue(expected_proj.issubset(project_cols), f"Missing project cols: {expected_proj - project_cols}")

            # workers columns
            worker_cols = get_cols("workers")
            expected_worker = {
                "worker_id", "project_id", "pid", "process_create_time",
                "canonical_target", "engine_root", "state", "started_at",
                "last_heartbeat", "exit_code", "end_reason",
            }
            self.assertTrue(expected_worker.issubset(worker_cols), f"Missing worker cols: {expected_worker - worker_cols}")

            # operations columns
            operation_cols = get_cols("operations")
            expected_op = {
                "operation_id", "project_id", "worker_id", "kind",
                "state", "requested_at", "started_at", "finished_at", "error",
            }
            self.assertTrue(expected_op.issubset(operation_cols), f"Missing op cols: {expected_op - operation_cols}")
        finally:
            conn.close()

    # -------------------------------------------------------------------------
    # 8. Orphan reconciliation
    # -------------------------------------------------------------------------

    def test_reconcile_orphans_marks_dead_workers_orphaned(self):
        """Orphan reconciliation must transition active projects with dead PIDs to ORPHANED."""
        proj = self.controller.register_project("orphan_proj", str(self.engine_root / "orphan_ws"))
        self.controller.registry.transition_project_state(proj.project_id, ProjectState.STARTING.value)
        self.controller.registry.transition_project_state(proj.project_id, ProjectState.INDEXING.value)
        self.controller.registry.transition_project_state(proj.project_id, ProjectState.ACTIVE.value)

        # Record worker with guaranteed non-existent PID
        dead_pid = 99999999
        self.controller.registry.record_worker(
            worker_id="w_dead_1",
            project_id=proj.project_id,
            pid=dead_pid,
            state=ProjectState.ACTIVE.value,
        )

        orphaned = self.controller.recover_orphans()
        self.assertIn(proj.project_id, orphaned)

        fresh = self.controller.registry.get_project(proj.project_id)
        self.assertEqual(fresh.state, ProjectState.ORPHANED.value)

        dead_worker = self.controller.registry.get_worker("w_dead_1")
        self.assertEqual(dead_worker.state, ProjectState.STOPPED.value)
        self.assertEqual(dead_worker.end_reason, "orphaned_on_recovery")

    # -------------------------------------------------------------------------
    # 9. Unified engine.db and Production Controller Wiring (ADR-020)
    # -------------------------------------------------------------------------

    def test_production_wiring_unified_engine_db_and_reconciler_daemon(self):
        """Verify LifecycleController unifies engine.db at data/engine.db and wires ReconcilerDaemon."""
        from spd_analysis_engine.scripts.core.supervisor import Supervisor
        from spd_analysis_engine.scripts.server.server_daemon import EngineWebServer

        test_root = self.engine_root / "prod_wiring_test"
        test_root.mkdir(parents=True, exist_ok=True)

        # Default db_path must resolve to data/engine.db
        ctrl = LifecycleController(test_root)
        self.assertEqual(ctrl.db_path, (test_root / "data" / "engine.db").resolve())

        # Supervisor must bind authoritative controller and rotator
        sup = Supervisor(test_root, enable_reaper=False)
        try:
            self.assertIsNotNone(sup.controller)
            self.assertEqual(sup._rotator.controller, sup.controller)

            # Starting reaper starts reconciler daemon
            self.assertFalse(sup.is_reaper_running())
            sup.start_reaper(interval=0.5)
            self.assertTrue(sup.is_reaper_running())

            # EngineWebServer must share the authoritative controller and unified engine.db
            server = EngineWebServer(engine_root=test_root, port=19876, supervisor=sup)
            self.assertEqual(server.controller, sup.controller)
            self.assertEqual(server.ai_db.db_path, (test_root / "data" / "engine.db").resolve())
        finally:
            if "server" in locals():
                try:
                    server.shutdown()
                except Exception:
                    pass
            sup.shutdown()
            self.assertFalse(sup.is_reaper_running())



if __name__ == "__main__":
    unittest.main()

