"""
tests/test_changeset_and_shadow_git.py
======================================
Comprehensive test suite for:
- ChangeSet domain object and size policy enforcement.
- Hardened ShadowGit repository configuration (gc.auto, core.longpaths, etc.).
- 4-step Git workflow (git add -> git diff --cached --quiet -> git commit -> git diff-tree).
- Mandatory scenarios:
  1. 1,000 files
  2. 2,000 files
  3. long paths (>260 chars)
  4. binary changes
  5. empty-file changes
  6. no-op burst
  7. Git failure (preserves SessionDB events)
  8. Git timeout (preserves SessionDB events)
  9. worker crash during Git commit
  10. recovery after restart
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.core.changeset import (
    ChangeSet,
    check_file_size_policy,
    truncate_patch_content,
    DEFAULT_MAX_TEXT_BYTES,
    DEFAULT_MAX_PATCH_BYTES,
)
from scripts.git.shadow_commit import (
    GitState,
    GitBurstResult,
    get_shadow_git_env,
    configure_shadow_repository,
    stage_paths_nul,
    execute_burst_commit,
    record_git_op_state,
    update_git_op_state,
    recover_unfinished_git_ops,
)
from scripts.git.shadow_repo import ShadowGit
from scripts.storage.db import SessionDB
from scripts.storage.fingerprint import FileFingerprint


class TestChangeSetDomain(unittest.TestCase):
    """Test ChangeSet domain object properties, serialization, and size policies."""

    def test_changeset_domain_properties_and_serialization(self) -> None:
        fp_before = FileFingerprint(relative_path="a.py", exists=True, size=10, mtime_ns=1000, sha256="abc")
        fp_after = FileFingerprint(relative_path="a.py", exists=True, size=20, mtime_ns=2000, sha256="def")

        cs = ChangeSet(
            burst_uid="burst_test_01",
            event_id=101,
            sequence=1,
            files=["a.py", "b.py"],
            effective_files=["a.py"],
            renames=[("old_name.py", "b.py")],
            before_fingerprints={"a.py": fp_before},
            after_fingerprints={"a.py": fp_after},
            metadata={"source": "test"},
        )

        self.assertTrue(cs.is_effective)
        self.assertFalse(cs.is_empty)

        # Serialization round-trip
        data = cs.to_dict()
        self.assertEqual(data["burst_uid"], "burst_test_01")
        self.assertEqual(data["event_id"], 101)
        self.assertEqual(data["effective_files"], ["a.py"])
        self.assertEqual(data["renames"], [["old_name.py", "b.py"]])

        restored = ChangeSet.from_dict(data)
        self.assertEqual(restored.burst_uid, cs.burst_uid)
        self.assertEqual(restored.event_id, cs.event_id)
        self.assertEqual(restored.effective_files, cs.effective_files)
        self.assertEqual(restored.renames, cs.renames)
        self.assertIsInstance(restored.before_fingerprints["a.py"], FileFingerprint)
        self.assertEqual(restored.before_fingerprints["a.py"].sha256, "abc")
        self.assertEqual(restored.after_fingerprints["a.py"].sha256, "def")

    def test_size_policy_streaming_and_patch_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "large.bin"
            # Write 200 KB file
            p.write_bytes(b"A" * 200_000)

            # Check with limit 100 KB
            exceeds, size, sha = check_file_size_policy(p, max_text_bytes=100_000)
            self.assertTrue(exceeds)
            self.assertEqual(size, 200_000)
            self.assertIsNotNone(sha)

            # Check with limit 300 KB
            exceeds, size, sha = check_file_size_policy(p, max_text_bytes=300_000)
            self.assertFalse(exceeds)
            self.assertEqual(size, 200_000)
            self.assertIsNone(sha)

        # Patch truncation
        short_patch = "diff --git a/f b/f\n+hello\n"
        res, is_trunc, orig_sz = truncate_patch_content(short_patch, max_patch_bytes=100)
        self.assertFalse(is_trunc)
        self.assertEqual(res, short_patch)

        long_patch = "x" * 1000
        res, is_trunc, orig_sz = truncate_patch_content(long_patch, max_patch_bytes=100)
        self.assertTrue(is_trunc)
        self.assertEqual(orig_sz, 1000)
        self.assertTrue(res.startswith("x" * 100))
        self.assertIn("[PATCH TRUNCATED", res)


class TestShadowGitWorkflows(unittest.TestCase):
    """Test ShadowGit repository workflows and 10 mandatory test scenarios."""

    def setUp(self) -> None:
        self.test_dir = Path(tempfile.mkdtemp(prefix="spd_cs_test_"))
        self.target_dir = self.test_dir / "target"
        self.shadow_dir = self.test_dir / "shadow_git"
        self.target_dir.mkdir(parents=True, exist_ok=True)
        self.sg = ShadowGit(shadow_dir=self.shadow_dir, target_dir=self.target_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _get_git_config(self, key: str) -> str:
        res = self.sg._run_git("config", "--get", key)
        return res.stdout.strip()

    # -----------------------------------------------------------------------
    # Hardened Configuration
    # -----------------------------------------------------------------------
    def test_shadow_git_hardened_configuration(self) -> None:
        """Shadow repo must enforce gc.auto=0, core.longpaths=true, etc."""
        if not self.sg.is_available:
            self.skipTest("Git not available")

        self.assertTrue(self.sg.init_repo())
        self.assertEqual(self._get_git_config("gc.auto"), "0")
        self.assertEqual(self._get_git_config("core.longpaths"), "true")
        self.assertEqual(self._get_git_config("core.quotepath"), "false")
        self.assertEqual(self._get_git_config("core.autocrlf"), "false")
        self.assertEqual(self._get_git_config("core.fsmonitor"), "false")

    # -----------------------------------------------------------------------
    # 1. 1,000 files
    # -----------------------------------------------------------------------
    def test_01_one_thousand_files_burst_commit(self) -> None:
        """Stage and commit 1,000 files via NUL pathspec; recursive diff-tree patch extracted."""
        if not self.sg.is_available:
            self.skipTest("Git not available")

        self.sg.init_repo()
        files = []
        for i in range(1000):
            rel = f"bulk1k/file_{i:04d}.txt"
            p = self.target_dir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"v1 {i}\n", encoding="utf-8")
            files.append(rel)

        cs = ChangeSet(burst_uid="b_1000", event_id=1, files=files, effective_files=files)
        res = self.sg.commit_changeset(cs, summary="1,000 files test")

        self.assertEqual(res.state, GitState.COMMITTED)
        self.assertIsNotNone(res.commit_hash)
        self.assertIsNotNone(res.patch)
        self.assertIn("bulk1k/file_0000.txt", res.patch)

    # -----------------------------------------------------------------------
    # 2. 2,000 files
    # -----------------------------------------------------------------------
    def test_02_two_thousand_files_burst_commit(self) -> None:
        """Stage and commit 2,000 files without Windows command line length overflow."""
        if not self.sg.is_available:
            self.skipTest("Git not available")

        self.sg.init_repo()
        files = []
        for i in range(2000):
            rel = f"bulk2k/file_{i:04d}.txt"
            p = self.target_dir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"v1 {i}\n", encoding="utf-8")
            files.append(rel)

        cs = ChangeSet(burst_uid="b_2000", event_id=2, files=files, effective_files=files)
        res = self.sg.commit_changeset(cs, summary="2,000 files test")

        self.assertEqual(res.state, GitState.COMMITTED)
        self.assertIsNotNone(res.commit_hash)

    # -----------------------------------------------------------------------
    # 3. Long paths (>260 characters)
    # -----------------------------------------------------------------------
    def test_03_long_paths_commit(self) -> None:
        """Deep path exceeding 260 characters staged and committed cleanly."""
        if not self.sg.is_available:
            self.skipTest("Git not available")

        self.sg.init_repo()
        deep_dir = self.target_dir / ("dir_" + "x" * 60) / ("dir_" + "y" * 60) / ("dir_" + "z" * 60)
        deep_dir.mkdir(parents=True, exist_ok=True)
        long_file = deep_dir / "nested_deep_file.txt"
        long_file.write_text("deep path content\n", encoding="utf-8")

        rel_path = str(long_file.relative_to(self.target_dir)).replace("\\", "/")
        self.assertGreater(len(str(long_file.resolve())), 260)

        cs = ChangeSet(burst_uid="b_long", event_id=3, files=[rel_path], effective_files=[rel_path])
        res = self.sg.commit_changeset(cs, summary="Long path test")

        self.assertEqual(res.state, GitState.COMMITTED)
        self.assertIsNotNone(res.commit_hash)
        self.assertIn("nested_deep_file.txt", res.patch or "")

    # -----------------------------------------------------------------------
    # 4. Binary changes
    # -----------------------------------------------------------------------
    def test_04_binary_changes_commit(self) -> None:
        """Binary files committed and diff-tree reports binary difference."""
        if not self.sg.is_available:
            self.skipTest("Git not available")

        self.sg.init_repo()
        bin_p = self.target_dir / "sample.bin"
        bin_p.write_bytes(b"\x00\x01\x02\xff\xfe\xaa")

        cs1 = ChangeSet(burst_uid="b_bin_1", event_id=4, files=["sample.bin"], effective_files=["sample.bin"])
        res1 = self.sg.commit_changeset(cs1, summary="Initial binary")
        self.assertEqual(res1.state, GitState.COMMITTED)

        # Mutate binary
        bin_p.write_bytes(b"\xff\xfe\x00\x01\x99\x88")
        cs2 = ChangeSet(burst_uid="b_bin_2", event_id=5, files=["sample.bin"], effective_files=["sample.bin"])
        res2 = self.sg.commit_changeset(cs2, summary="Update binary")
        self.assertEqual(res2.state, GitState.COMMITTED)
        self.assertIn("Binary files", res2.patch or "")

    # -----------------------------------------------------------------------
    # 5. Empty-file changes
    # -----------------------------------------------------------------------
    def test_05_empty_file_changes(self) -> None:
        """0-byte file committed without --allow-empty."""
        if not self.sg.is_available:
            self.skipTest("Git not available")

        self.sg.init_repo()
        empty_p = self.target_dir / "empty.txt"
        empty_p.write_text("", encoding="utf-8")

        cs = ChangeSet(burst_uid="b_empty", event_id=6, files=["empty.txt"], effective_files=["empty.txt"])
        res = self.sg.commit_changeset(cs, summary="Empty file")

        self.assertEqual(res.state, GitState.COMMITTED)
        self.assertIsNotNone(res.commit_hash)

    # -----------------------------------------------------------------------
    # 6. No-op burst
    # -----------------------------------------------------------------------
    def test_06_no_op_burst(self) -> None:
        """Burst with no staged changes returns skipped_no_change without commit."""
        if not self.sg.is_available:
            self.skipTest("Git not available")

        self.sg.init_repo()
        f = self.target_dir / "stable.txt"
        f.write_text("stable content\n", encoding="utf-8")

        cs1 = ChangeSet(burst_uid="b_stable_1", event_id=7, files=["stable.txt"], effective_files=["stable.txt"])
        res1 = self.sg.commit_changeset(cs1, summary="First commit")
        self.assertEqual(res1.state, GitState.COMMITTED)

        # Re-commit without modifying file
        cs2 = ChangeSet(burst_uid="b_stable_2", event_id=8, files=["stable.txt"], effective_files=["stable.txt"])
        res2 = self.sg.commit_changeset(cs2, summary="No-op commit")

        self.assertEqual(res2.state, GitState.SKIPPED_NO_CHANGE)
        self.assertIsNone(res2.commit_hash)

    # -----------------------------------------------------------------------
    # 7. Git failure preserves event
    # -----------------------------------------------------------------------
    def test_07_git_failure_preserves_event(self) -> None:
        """Git failure marks state failed but does not lose already-persisted SessionDB event."""
        db_path = self.test_dir / "session.db"
        sdb = SessionDB(db_path)
        sid = sdb.create_session("test_proj", str(self.target_dir))
        ev_id = sdb.record_event(session_id=sid, event_type="EDIT", first_file="fail.py", summary="Failure test")

        op_id = record_git_op_state(
            conn=sdb.conn,
            event_id=ev_id,
            burst_uid="b_fail",
            status=GitState.RUNNING,
            files=["fail.py"],
            effective_files=["fail.py"],
        )

        cs = ChangeSet(burst_uid="b_fail", event_id=ev_id, files=["fail.py"], effective_files=["fail.py"])

        # Force Git failure by pointing git_cmd to an invalid binary
        bad_sg = ShadowGit(shadow_dir=self.shadow_dir, target_dir=self.target_dir)
        bad_sg.git_cmd = "nonexistent_git_binary"

        res = bad_sg.commit_changeset(cs, summary="Should fail")
        self.assertEqual(res.state, GitState.FAILED)

        update_git_op_state(
            conn=sdb.conn,
            op_id=op_id,
            status=res.state,
            error_message=res.error,
        )

        # Verify event in SessionDB remains completely intact
        cur = sdb.conn.cursor()
        cur.execute("SELECT id, session_id, event_type, summary FROM events WHERE id = ?", (ev_id,))
        ev_row = cur.fetchone()
        self.assertIsNotNone(ev_row)
        self.assertEqual(ev_row[2], "EDIT")

        # Verify git_ops recorded failed status
        cur.execute("SELECT id, status, error_message FROM git_ops WHERE id = ?", (op_id,))
        op_row = cur.fetchone()
        self.assertEqual(op_row[1], GitState.FAILED)
        sdb.close()

    # -----------------------------------------------------------------------
    # 8. Git timeout preserves event
    # -----------------------------------------------------------------------
    def test_08_git_timeout_preserves_event(self) -> None:
        """Git timeout marks state failed but preserves SessionDB event."""
        if not self.sg.is_available:
            self.skipTest("Git not available")

        self.sg.init_repo()
        f = self.target_dir / "timeout.txt"
        f.write_text("timeout test\n", encoding="utf-8")

        db_path = self.test_dir / "session.db"
        sdb = SessionDB(db_path)
        sid = sdb.create_session("timeout_proj", str(self.target_dir))
        ev_id = sdb.record_event(session_id=sid, event_type="EDIT", first_file="timeout.txt", summary="Timeout test")

        op_id = record_git_op_state(
            conn=sdb.conn,
            event_id=ev_id,
            burst_uid="b_timeout",
            status=GitState.RUNNING,
            files=["timeout.txt"],
            effective_files=["timeout.txt"],
        )

        cs = ChangeSet(burst_uid="b_timeout", event_id=ev_id, files=["timeout.txt"], effective_files=["timeout.txt"])

        # Execute with an impossible timeout (1 microsecond)
        res = self.sg.commit_changeset(cs, summary="Timeout burst", timeout=0.000001)
        self.assertEqual(res.state, GitState.FAILED)
        self.assertIn("timed out", (res.error or "").lower())

        update_git_op_state(conn=sdb.conn, op_id=op_id, status=res.state, error_message=res.error)

        # Verify event still exists
        cur = sdb.conn.cursor()
        cur.execute("SELECT id, event_type FROM events WHERE id = ?", (ev_id,))
        self.assertIsNotNone(cur.fetchone())
        sdb.close()

    # -----------------------------------------------------------------------
    # 9. Worker crash during Git commit
    # -----------------------------------------------------------------------
    def test_09_worker_crash_during_git_commit(self) -> None:
        """Simulate unexpected worker crash leaving git_ops in running state."""
        db_path = self.test_dir / "session.db"
        sdb = SessionDB(db_path)
        sid = sdb.create_session("crash_proj", str(self.target_dir))
        ev_id = sdb.record_event(session_id=sid, event_type="EDIT", first_file="in_flight.txt", summary="Crash in flight")

        f = self.target_dir / "in_flight.txt"
        f.write_text("in-flight content\n", encoding="utf-8")

        # Worker records running state right before crash
        op_id = record_git_op_state(
            conn=sdb.conn, event_id=ev_id, burst_uid="b_crash_inflight",
            status=GitState.RUNNING, files=["in_flight.txt"], effective_files=["in_flight.txt"],
        )

        # Worker process terminates without update_git_op_state
        sdb.close()

        # Reopen and inspect durable table
        sdb2 = SessionDB(db_path)
        cur = sdb2.conn.cursor()
        cur.execute("SELECT id, event_id, burst_uid, status FROM git_ops WHERE id = ?", (op_id,))
        row = cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[1], ev_id)
        self.assertEqual(row[3], GitState.RUNNING)
        sdb2.close()

    # -----------------------------------------------------------------------
    # 10. Recovery after restart
    # -----------------------------------------------------------------------
    def test_10_recovery_after_restart(self) -> None:
        """Worker restart discovers unfinished git_ops and commits them successfully."""
        if not self.sg.is_available:
            self.skipTest("Git not available")

        self.sg.init_repo()
        recovered_file = self.target_dir / "recovered.py"
        recovered_file.write_text("def recovered(): pass\n", encoding="utf-8")

        db_path = self.test_dir / "session.db"
        sdb = SessionDB(db_path)
        sid = sdb.create_session("recovery_proj", str(self.target_dir))
        ev_id = sdb.record_event(session_id=sid, event_type="EDIT", first_file="recovered.py", summary="Pending recovery")

        op_id = record_git_op_state(
            conn=sdb.conn, event_id=ev_id, burst_uid="b_recovery_01",
            status=GitState.RUNNING, files=["recovered.py"], effective_files=["recovered.py"],
        )

        # On worker startup:
        recovered = self.sg.recover_pending_ops(sdb.conn)
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0]["op_id"], op_id)
        self.assertEqual(recovered[0]["state"], GitState.COMMITTED)
        self.assertIsNotNone(recovered[0]["commit_hash"])

        cur = sdb.conn.cursor()
        cur.execute("SELECT status, commit_hash FROM git_ops WHERE id = ?", (op_id,))
        row = cur.fetchone()
        self.assertEqual(row[0], GitState.COMMITTED)
        self.assertEqual(row[1], recovered[0]["commit_hash"])
        sdb.close()

    def test_11_stale_index_lock_recovery(self) -> None:
        """Stale index.lock older than threshold is unlinked, unblocking burst commits."""
        if not self.sg.is_available:
            self.skipTest("Git not available")

        self.sg.init_repo()
        lock_file = self.sg.shadow_dir / "index.lock"
        lock_file.write_text("simulated stale lock", encoding="utf-8")

        # Active/fresh lock should not be removed with max_age_s=60
        self.assertFalse(self.sg.recover_stale_index_lock(max_age_s=60.0))
        self.assertTrue(lock_file.exists())

        # Age the lock artificially and verify recovery
        self.assertTrue(self.sg.recover_stale_index_lock(max_age_s=0.0))
        self.assertFalse(lock_file.exists())

        # Mid-session crash simulation: aged lock recovered automatically by commit_changeset
        lock_file.write_text("abandoned lock", encoding="utf-8")
        past_time = time.time() - 120.0
        os.utime(lock_file, (past_time, past_time))

        f = self.target_dir / "test_lock.py"
        f.write_text("val = 1\n", encoding="utf-8")
        cs = ChangeSet(burst_uid="b_lock_1", files=["test_lock.py"], effective_files=["test_lock.py"])
        res = self.sg.commit_changeset(cs, summary="Test lock recovery")
        self.assertEqual(res.state, GitState.COMMITTED)
        self.assertFalse(lock_file.exists())

    def test_12_shadow_git_maintenance(self) -> None:
        """run_maintenance executes repack and prune while preserving milestone references."""
        if not self.sg.is_available:
            self.skipTest("Git not available")

        self.sg.init_repo()
        f = self.target_dir / "maint.py"
        f.write_text("x = 1\n", encoding="utf-8")
        cs = ChangeSet(burst_uid="b_maint_1", files=["maint.py"], effective_files=["maint.py"])
        res = self.sg.commit_changeset(cs, summary="Pre-maintenance")
        self.assertEqual(res.state, GitState.COMMITTED)

        maint_res = self.sg.run_maintenance(keep_refs=[f"milestone_1:{res.commit_hash}"])
        self.assertTrue(maint_res["success"])
        self.assertTrue(maint_res["repack_success"])
        self.assertTrue(maint_res["prune_success"])


if __name__ == "__main__":
    unittest.main()
