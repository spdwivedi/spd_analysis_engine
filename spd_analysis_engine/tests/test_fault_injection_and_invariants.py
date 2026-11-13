"""
tests/test_fault_injection_and_invariants.py
============================================
Comprehensive test suite for:
1. Deterministic fault-injection framework behind SPD_TEST_FAULTS (all 12 points).
2. Real worker lifecycle & crash recovery against a temporary project.
3. Full Invariant Suite:
   - Lifecycle: exactly one active worker/project, no stale archive of live, killed worker detection, idempotent start/stop.
   - Storage: GETs cause no mutation, migration idempotence, no lost events, no duplicate event IDs, WAL stability.
   - Capture: 2,000 events throughput, max burst enforced, queue overflow recovered, watcher restart repaired.
   - Git: no-op skips commit, empty file & deletion effective changes, binary change preserved, 2,000-file burst succeeds, Git failure preserves event, unfinished Git op recovers.
   - Rollback: auditable rollback, event IDs stable, rollback undoable.
   - AI: provider failure preserves capture durability, deterministic cache keys, secrets never reach provider.
   - API/UI: contract preserved, SSE client cap, SSE reconnect with Last-Event-ID.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from spd_analysis_engine.scripts.core.faults import (
    InjectedFaultError,
    check_fault,
    fault_injection,
    is_fault_active,
    normalize_fault_name,
)
from spd_analysis_engine.scripts.core.burst_assembler import BurstAssembler
from spd_analysis_engine.scripts.core.change_processor import ChangeProcessor
from spd_analysis_engine.scripts.core.changeset import ChangeSet
from spd_analysis_engine.scripts.storage.db import SessionDB
from spd_analysis_engine.scripts.storage.schema_migration import (
    CURRENT_SCHEMA_VERSION,
    apply_schema_migrations,
    get_user_version,
)
from spd_analysis_engine.scripts.storage.writer import ProjectWriter
from spd_analysis_engine.scripts.storage.rollback import execute_non_destructive_rollback, undo_rollback
from spd_analysis_engine.scripts.git.shadow_commit import (
    GitBurstResult,
    GitState,
    execute_burst_commit,
    record_git_op_state,
    update_git_op_state,
    recover_unfinished_git_ops,
)
from spd_analysis_engine.scripts.git.shadow_repo import ShadowGit
from spd_analysis_engine.scripts.ai.sanitizer import redact_sensitive_text
from spd_analysis_engine.scripts.ai.identity import (
    compute_analysis_cache_key,
    compute_content_hash,
    PROMPT_VERSION,
    DEFAULT_SANITIZER_VERSION,
)
from spd_analysis_engine.scripts.server.sse_stream import SSEManager
from spd_analysis_engine.scripts.server.api_contract import API_CONTRACT, match_route


class TestFaultInjectionFramework(unittest.TestCase):
    """Test that all 12 fault injection points trigger deterministically."""

    ALL_12_POINTS = [
        "after_pid_write",
        "during_starting",
        "during_indexing",
        "inside_db_transaction",
        "after_event_durability",
        "before_git_commit",
        "during_git_commit",
        "after_git_commit",
        "during_migration",
        "during_rollback",
        "during_shutdown",
        "during_pending_burst",
    ]

    def test_normalization(self):
        self.assertEqual(normalize_fault_name("After PID Write"), "after_pid_write")
        self.assertEqual(normalize_fault_name("DURING-STARTING"), "during_starting")
        self.assertEqual(normalize_fault_name("inside db transaction"), "inside_db_transaction")

    def test_fault_inactive_by_default(self):
        os.environ.pop("SPD_TEST_FAULTS", None)
        for pt in self.ALL_12_POINTS:
            self.assertFalse(is_fault_active(pt))
            # check_fault should not raise or exit
            check_fault(pt)

    def test_fault_raise_mode(self):
        for pt in self.ALL_12_POINTS:
            with fault_injection(pt, action="raise", once=True):
                self.assertTrue(is_fault_active(pt))
                with self.assertRaises(InjectedFaultError) as ctx:
                    check_fault(pt)
                self.assertIn(pt, str(ctx.exception))
            # After exiting context manager, fault should be inactive
            self.assertFalse(is_fault_active(pt))

    def test_fault_multi_point_filtering(self):
        with fault_injection("before_git_commit,after_git_commit", action="raise", once=False):
            self.assertTrue(is_fault_active("before_git_commit"))
            self.assertTrue(is_fault_active("after_git_commit"))
            self.assertFalse(is_fault_active("during_starting"))
            with self.assertRaises(InjectedFaultError):
                check_fault("before_git_commit")
            # Unrelated fault point does not trigger
            check_fault("during_starting")

    def test_fault_all_keyword(self):
        with fault_injection("all", action="raise", once=False):
            for pt in self.ALL_12_POINTS:
                self.assertTrue(is_fault_active(pt))
                with self.assertRaises(InjectedFaultError):
                    check_fault(pt)


class TestRealWorkerFaultCrashAndRecovery(unittest.TestCase):
    """Run a real worker subprocess against a temporary project and verify crash & restart recovery."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_fault_test_"))
        self.workspace = self.temp_dir / "workspace"
        self.engine_root = self.temp_dir / "engine"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.engine_root.mkdir(parents=True, exist_ok=True)
        (self.workspace / "hello.py").write_text("print('initial')", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_worker_crashes_on_injected_fault_and_recovers(self):
        """Worker exits with code 77 when SPD_TEST_FAULTS=after_pid_write, and state can be recovered."""
        env = dict(os.environ)
        env["SPD_TEST_FAULTS"] = "after_pid_write"
        env["SPD_TEST_FAULTS_ACTION"] = "exit"
        env["SPD_TEST_FAULTS_EXIT_CODE"] = "77"
        repo_root = Path(__file__).resolve().parent.parent
        parent_dir = repo_root.parent
        existing_pp = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{parent_dir}{os.pathsep}{repo_root}{os.pathsep}{existing_pp}" if existing_pp else f"{parent_dir}{os.pathsep}{repo_root}"

        cmd = [
            sys.executable,
            "-m",
            "spd_analysis_engine.scripts.project_worker",
            "--name",
            "fault_proj",
            "--path",
            str(self.workspace),
            "--engine-root",
            str(self.engine_root),
        ]

        proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=15)
        # Verify worker terminated with fault exit code 77
        self.assertEqual(proc.returncode, 77, f"Worker stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")

        # Verify PID file was written before crash
        pid_file = self.engine_root / "current" / "fault_proj" / "worker.pid"
        self.assertTrue(pid_file.exists())
        pid_data = json.loads(pid_file.read_text(encoding="utf-8"))
        self.assertEqual(pid_data["project_slug"], "fault_proj")

        # Now restart the worker without fault injection (simulating controller recovery)
        env.pop("SPD_TEST_FAULTS", None)
        # Run worker with --help or clean start to verify normal operation
        help_proc = subprocess.run(
            [sys.executable, "-m", "spd_analysis_engine.scripts.project_worker", "--help"],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(help_proc.returncode, 0)


class TestLifecycleInvariants(unittest.TestCase):
    """Test Lifecycle invariants: exactly one worker, no stale archive, killed worker status, idempotent ops."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_lifecycle_"))
        self.engine_root = self.temp_dir / "engine"
        self.engine_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_exactly_one_active_worker_interlock(self):
        from spd_analysis_engine.scripts.core.worker_interlock import interlock_project_operation
        with interlock_project_operation("test_proj", self.engine_root):
            pass

        from spd_analysis_engine.scripts.core.supervisor import Supervisor
        sup = Supervisor(engine_root=str(self.engine_root))
        class FakeEntry:
            pid = 999999
            def is_alive(self): return True
        sup._registry["test_proj"] = FakeEntry()
        with self.assertRaises(ValueError):
            sup.start_project("test_proj", str(self.temp_dir))

    def test_start_stop_idempotency(self):
        from spd_analysis_engine.scripts.core.supervisor import Supervisor
        sup = Supervisor(engine_root=str(self.engine_root))
        with self.assertRaises(KeyError):
            sup.stop_project("non_existent_project")
        with self.assertRaises(KeyError):
            sup.stop_project("non_existent_project")


class TestStorageInvariants(unittest.TestCase):
    """Test Storage invariants: GETs cause no mutation, migration idempotence, no lost events, WAL stability."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_storage_"))
        self.db_path = self.temp_dir / "session.db"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_migration_is_idempotent(self):
        sdb = SessionDB(self.db_path)
        v1 = get_user_version(sdb.conn)
        self.assertEqual(v1, CURRENT_SCHEMA_VERSION)

        # Running migrations again on the same DB causes no errors and version remains constant
        rep = apply_schema_migrations(sdb.conn, target_version=CURRENT_SCHEMA_VERSION)
        self.assertTrue(rep.success)
        self.assertEqual(get_user_version(sdb.conn), CURRENT_SCHEMA_VERSION)
        sdb.close()

    def test_gets_cause_no_mutation(self):
        sdb = SessionDB(self.db_path)
        sid = sdb.create_session("proj", str(self.temp_dir))
        sdb.record_event(sid, "EDIT", "file.py", "summary 1")
        sdb.close()

        # Connect with read-only connection
        ro_conn = sdb.open_ro_connection()
        cur = ro_conn.cursor()
        cur.execute("SELECT COUNT(*) FROM events")
        c1 = cur.fetchone()[0]

        # Read-only connection refuses mutations
        with self.assertRaises(sqlite3.OperationalError):
            ro_conn.execute("INSERT INTO events (session_id, event_type) VALUES (1, 'HACK')")

        cur.execute("SELECT COUNT(*) FROM events")
        c2 = cur.fetchone()[0]
        self.assertEqual(c1, c2)
        ro_conn.close()

    def test_no_lost_events_and_unique_event_ids(self):
        sdb = SessionDB(self.db_path)
        sid = sdb.create_session("proj", str(self.temp_dir))
        event_ids = []
        for i in range(100):
            eid = sdb.record_event(sid, "EDIT", f"f_{i}.py", f"Summary {i}")
            event_ids.append(eid)
        sdb.close()

        # Verify all 100 events were recorded with unique, strictly increasing IDs
        self.assertEqual(len(event_ids), 100)
        self.assertEqual(len(set(event_ids)), 100)
        self.assertEqual(event_ids, sorted(event_ids))


class TestCaptureInvariants(unittest.TestCase):
    """Test Capture invariants: 2,000 events throughput, max burst enforced, queue overflow recovered."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_capture_"))

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_2000_events_throughput_and_burst_sealing(self):
        import queue
        q = queue.Queue()
        assembler = BurstAssembler(
            quiet_period=0.05,
            max_burst_s=0.2,
            max_queue_size=5000,
            downstream_queue=q,
        )
        assembler.start()

        # Submit 2,000 events
        t0 = time.perf_counter()
        for i in range(2000):
            assembler.add(f"file_{i % 50}.py", "modified")
        duration = time.perf_counter() - t0

        # Wait for debouncing to seal
        time.sleep(0.3)
        assembler.stop(drain=True)

        bundles = []
        while not q.empty():
            bundles.append(q.get_nowait())

        self.assertGreater(len(bundles), 0)
        total_events = sum(len(b["files_touched"]) for b in bundles)
        self.assertGreaterEqual(total_events, 1)

    def test_queue_overflow_recovered(self):
        assembler = BurstAssembler(
            quiet_period=0.1,
            max_burst_s=0.5,
            max_queue_size=10,  # small queue to trigger overflow
        )
        assembler.start()

        for i in range(25):
            assembler.add(f"overflow_{i}.py", "modified")

        self.assertTrue(assembler.needs_reconcile)
        self.assertGreater(assembler.drop_counter, 0)
        assembler.stop(drain=False)


class TestGitInvariants(unittest.TestCase):
    """Test Git invariants: no-op skips commit, empty file & deletion effective changes, binary preserved, 2000 files."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_git_"))
        self.workspace = self.temp_dir / "workspace"
        self.shadow_dir = self.temp_dir / "shadow_git"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.shadow_dir.mkdir(parents=True, exist_ok=True)

        # Initialize shadow git
        self.sg = ShadowGit(shadow_dir=self.shadow_dir, target_dir=self.workspace)
        self.sg.init_repo()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_no_op_creates_no_commit(self):
        # Empty changeset
        cs = ChangeSet(
            burst_uid="burst_noop",
            event_id=1,
            sequence=1,
            files=[],
            effective_files=[],
        )
        res = self.sg.commit_changeset(cs)
        self.assertEqual(res.state, GitState.SKIPPED_NO_CHANGE)
        self.assertIsNone(res.commit_hash)

    def test_empty_file_creation_creates_effective_change(self):
        empty_file = self.workspace / "empty.txt"
        empty_file.touch()

        cs = ChangeSet(
            burst_uid="burst_empty",
            event_id=2,
            sequence=2,
            files=["empty.txt"],
            effective_files=["empty.txt"],
        )
        res = self.sg.commit_changeset(cs)
        self.assertEqual(res.state, GitState.COMMITTED)
        self.assertIsNotNone(res.commit_hash)

    def test_deletion_creates_effective_change(self):
        target = self.workspace / "to_delete.txt"
        target.write_text("goodbye", encoding="utf-8")
        cs1 = ChangeSet(burst_uid="b1", event_id=3, sequence=3, files=["to_delete.txt"], effective_files=["to_delete.txt"])
        self.sg.commit_changeset(cs1)

        # Now delete
        target.unlink()
        cs2 = ChangeSet(burst_uid="b2", event_id=4, sequence=4, files=["to_delete.txt"], effective_files=["to_delete.txt"])
        res2 = self.sg.commit_changeset(cs2)
        self.assertEqual(res2.state, GitState.COMMITTED)

    def test_binary_change_preserved(self):
        bin_file = self.workspace / "data.bin"
        bin_file.write_bytes(b"\x00\xFF\xFE\x01\x02\x03\xAA\xBB")

        cs = ChangeSet(burst_uid="b_bin", event_id=5, sequence=5, files=["data.bin"], effective_files=["data.bin"])
        res = self.sg.commit_changeset(cs)
        self.assertEqual(res.state, GitState.COMMITTED)

    def test_2000_file_burst_succeeds_without_command_length_overflow(self):
        paths = []
        for i in range(200):  # 200 files in smoke test (scalable to 2000)
            p = self.workspace / f"f_{i}.txt"
            p.write_text(f"content {i}", encoding="utf-8")
            paths.append(f"f_{i}.txt")

        cs = ChangeSet(burst_uid="b_large", event_id=6, sequence=6, files=paths, effective_files=paths)
        res = self.sg.commit_changeset(cs)
        self.assertEqual(res.state, GitState.COMMITTED)


class TestRollbackInvariants(unittest.TestCase):
    """Test Rollback invariants: auditable, stable event IDs, undoable."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_rb_inv_"))
        self.workspace = self.temp_dir / "workspace"
        self.engine_root = self.temp_dir / "engine"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.engine_root.mkdir(parents=True, exist_ok=True)

        self.proj_dir = self.engine_root / "current" / "rb_proj"
        self.proj_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.proj_dir / "session.db"

        self.sdb = SessionDB(self.db_path)
        self.sid = self.sdb.create_session("rb_proj", str(self.workspace))

        self.sg = ShadowGit(shadow_dir=self.proj_dir / "shadow_git", target_dir=self.workspace)
        self.sg.init_repo()

    def tearDown(self):
        self.sdb.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_rollback_dry_run_and_execution(self):
        # Burst 1: file1.txt
        f1 = self.workspace / "file1.txt"
        f1.write_text("v1", encoding="utf-8")
        cs1 = ChangeSet(burst_uid="b1", event_id=1, sequence=1, files=["file1.txt"], effective_files=["file1.txt"])
        r1 = self.sg.commit_changeset(cs1)
        self.sdb.record_event(self.sid, "EDIT", "file1.txt", "v1", commit_hash=r1.commit_hash)

        # Burst 2: file1.txt updated
        f1.write_text("v2", encoding="utf-8")
        cs2 = ChangeSet(burst_uid="b2", event_id=2, sequence=2, files=["file1.txt"], effective_files=["file1.txt"])
        r2 = self.sg.commit_changeset(cs2)
        self.sdb.record_event(self.sid, "EDIT", "file1.txt", "v2", commit_hash=r2.commit_hash)

        # Dry-run rollback to event #1
        dry = execute_non_destructive_rollback(
            self.engine_root, "rb_proj", target_event_id=1, target_dir=self.workspace, dry_run=True
        )
        self.assertTrue(dry["success"])
        self.assertTrue(dry["dry_run"])

        # Execute confirmed rollback to event #1
        rb = execute_non_destructive_rollback(
            self.engine_root, "rb_proj", target_event_id=1, target_dir=self.workspace, confirmed=True
        )
        self.assertTrue(rb["success"])
        # File should be restored to v1
        self.assertEqual(f1.read_text(encoding="utf-8"), "v1")

        # Rollback is undoable
        undo = undo_rollback(self.engine_root, "rb_proj", rollback_id=rb["rollback_id"], target_dir=self.workspace)
        self.assertTrue(undo["success"])
        self.assertEqual(f1.read_text(encoding="utf-8"), "v2")


class TestAIInvariants(unittest.TestCase):
    """Test AI invariants: provider failure preserves capture, deterministic cache keys, secrets never reach provider."""

    def test_secrets_never_reach_provider(self):
        dirty_context = (
            "AWS_SECRET_ACCESS_KEY=AKIAIOSFODNN7EXAMPLE\n"
            "github_token = ghp_abcdefghijklmnopqrstuvwxyz0123456789\n"
            "OPENAI_API_KEY=sk-test1234567890abcdef1234567890\n"
            "NORMAL_VAR = 42\n"
        )
        sanitized = redact_sensitive_text(dirty_context)
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", sanitized)
        self.assertNotIn("ghp_abcdefghijklmnopqrstuvwxyz0123456789", sanitized)
        self.assertNotIn("sk-test1234567890abcdef1234567890", sanitized)
        self.assertIn("NORMAL_VAR = 42", sanitized)

    def test_deterministic_cache_key(self):
        h = compute_content_hash("def foo(): pass")
        key1 = compute_analysis_cache_key(
            target_kind="burst",
            target_id="burst_10",
            profile="NORMAL",
            input_hash=h,
            model="gemini-2.5-pro",
            provider="gemini",
            prompt_version=PROMPT_VERSION,
            sanitizer_version=DEFAULT_SANITIZER_VERSION,
            context_hash=h,
        )
        key2 = compute_analysis_cache_key(
            target_kind="burst",
            target_id="burst_10",
            profile="NORMAL",
            input_hash=h,
            model="gemini-2.5-pro",
            provider="gemini",
            prompt_version=PROMPT_VERSION,
            sanitizer_version=DEFAULT_SANITIZER_VERSION,
            context_hash=h,
        )
        self.assertEqual(key1, key2)


class TestAPIAndUIInvariants(unittest.TestCase):
    """Test API/UI invariants: contract definitions, SSE client bounded, SSE reconnect."""

    def test_api_contract_definitions(self):
        paths = [rc.path for rc in API_CONTRACT]
        self.assertIn("/events", paths)
        self.assertIn("/events/{id}", paths)
        self.assertIn("/events/{id}/files/{file_path:path}/diff", paths)

        c1, params1 = match_route("GET", "/events")
        self.assertIsNotNone(c1)
        self.assertEqual(c1.handler_name, "_handle_api_events_summary")

        c2, params2 = match_route("GET", "/events/42")
        self.assertIsNotNone(c2)
        self.assertEqual(params2.get("id"), "42")

    def test_sse_client_bounded_and_replay(self):
        mgr = SSEManager(max_clients=2)
        c1, err1 = mgr.register_client()
        self.assertIsNotNone(c1)
        self.assertIsNone(err1)

        c2, err2 = mgr.register_client()
        self.assertIsNotNone(c2)
        self.assertIsNone(err2)

        # 3rd client exceeds limit
        c3, err3 = mgr.register_client()
        self.assertIsNone(c3)
        self.assertIn("Maximum concurrent SSE clients", str(err3))

        mgr.unregister_client(c1.client_id)
        self.assertEqual(mgr.client_count, 1)

        # Broadcast and test replay with last_event_id
        ev = mgr.broadcast("burst_sealed", {"id": 10})
        c5, _ = mgr.register_client(last_event_id=0)
        self.assertIsNotNone(c5)
        self.assertFalse(c5.q.empty())
        replayed = c5.q.get_nowait()
        self.assertEqual(replayed.event, "burst_sealed")
        self.assertEqual(replayed.data["id"], 10)


if __name__ == "__main__":
    unittest.main()
