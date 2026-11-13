"""
tests/test_v10_physical_rollback_and_selective_trash.py
======================================================
Unit tests for True Physical Workspace Rollback, Watcher Suppression Lock,
and Selective Burst Deletion ID Resolution & AI Cache Pruning.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from scripts.git_shadow import ShadowGit
from scripts.storage_rotator import (
    StorageRotator,
    SessionDB,
    rollback_burst,
    read_project_meta,
    update_project_meta,
)
from scripts.trash_manager import TrashManager, SCOPE_SELECTIVE_BURSTS
from scripts.watcher_fs import _WatchdogEventHandler
from scripts.event_bundler import EventBundler


class TestShadowGitRestore(unittest.TestCase):
    """Test ShadowGit.restore_workspace_to_commit() physically reverts files."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_sg_restore_test_"))
        self.shadow_dir = self.temp_dir / "shadow_git"
        self.target_dir = self.temp_dir / "workspace"
        self.target_dir.mkdir(parents=True, exist_ok=True)
        self.sg = ShadowGit(shadow_dir=self.shadow_dir, target_dir=self.target_dir)
        self.sg.init_repo()

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_restore_workspace_to_commit_reverts_files_and_removes_additions(self) -> None:
        if not self.sg.is_available:
            self.skipTest("Git not available on host")

        # Commit 1: file1.txt = "version 1"
        f1 = self.target_dir / "file1.txt"
        f1.write_text("version 1", encoding="utf-8")
        c1 = self.sg.commit_burst(event_id=1, first_file="file1.txt", summary="Burst 1: initial")
        self.assertIsNotNone(c1)
        assert c1 is not None

        # Commit 2: file1.txt = "version 2", file2.txt = "new file"
        f1.write_text("version 2", encoding="utf-8")
        f2 = self.target_dir / "file2.txt"
        f2.write_text("new file", encoding="utf-8")
        c2 = self.sg.commit_burst(event_id=2, first_file="file1.txt", summary="Burst 2: update")
        self.assertIsNotNone(c2)

        # Confirm state at commit 2
        self.assertEqual(f1.read_text(encoding="utf-8"), "version 2")
        self.assertTrue(f2.exists())

        # Restore workspace to commit 1
        success = self.sg.restore_workspace_to_commit(c1)
        self.assertTrue(success)

        # file1.txt should be reverted to "version 1"
        self.assertEqual(f1.read_text(encoding="utf-8"), "version 1")
        # file2.txt should be deleted from workspace
        self.assertFalse(f2.exists())


class TestPhysicalBurstRollback(unittest.TestCase):
    """Test rollback_burst() physical disk reversion, DB cleanup, and trash migration."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_phys_rollback_test_"))
        self.engine_root = self.temp_dir / "engine"
        self.engine_root.mkdir(parents=True, exist_ok=True)
        self.target_dir = self.temp_dir / "workspace"
        self.target_dir.mkdir(parents=True, exist_ok=True)

        self.rotator = StorageRotator(self.engine_root)
        self.proj_dir = self.rotator.init_project("phys_demo", str(self.target_dir))
        self.db_path = self.proj_dir / "session.db"
        self.db = SessionDB(self.db_path)
        self.sid = self.db.create_session("phys_demo", str(self.target_dir))

        self.shadow_dir = self.proj_dir / "shadow_git"
        self.sg = ShadowGit(shadow_dir=self.shadow_dir, target_dir=self.target_dir)
        self.sg.init_repo()

    def tearDown(self) -> None:
        self.db.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_physical_rollback_reverts_files_and_cleans_db(self) -> None:
        if not self.sg.is_available:
            self.skipTest("Git not available on host")

        # Burst 1
        f1 = self.target_dir / "main.py"
        f1.write_text("print('burst 1')\n", encoding="utf-8")
        c1 = self.sg.commit_burst(event_id=1, first_file="main.py", summary="Burst 1")
        self.assertIsNotNone(c1)
        eid1 = self.db.record_event(
            session_id=self.sid,
            event_type="EDIT",
            first_file="main.py",
            summary="Prompt Burst #1",
            commit_hash=c1,
            burst_num=1,
        )
        self.db.record_patch(eid1, "main.py", "+print('burst 1')\n")

        # Burst 2: modifies main.py and creates helper.py
        f1.write_text("print('burst 2')\n", encoding="utf-8")
        f2 = self.target_dir / "helper.py"
        f2.write_text("def helper(): pass\n", encoding="utf-8")
        c2 = self.sg.commit_burst(event_id=2, first_file="main.py", summary="Burst 2")
        self.assertIsNotNone(c2)
        eid2 = self.db.record_event(
            session_id=self.sid,
            event_type="EDIT",
            first_file="main.py",
            summary="Prompt Burst #2",
            commit_hash=c2,
            burst_num=2,
        )
        self.db.record_patch(eid2, "main.py", "-print('burst 1')\n+print('burst 2')\n")
        self.db.record_patch(eid2, "helper.py", "+def helper(): pass\n")

        # Confirm workspace has burst 2 files
        self.assertEqual(f1.read_text(encoding="utf-8"), "print('burst 2')\n")
        self.assertTrue(f2.exists())

        # Perform rollback of burst 2
        res = rollback_burst(self.engine_root, "phys_demo", eid2, target_dir=self.target_dir)
        self.assertTrue(res.get("success"))
        self.assertTrue(res.get("rolled_back_git"))

        # Verify physical disk state: main.py is burst 1, helper.py is gone
        self.assertEqual(f1.read_text(encoding="utf-8"), "print('burst 1')\n")
        self.assertFalse(f2.exists())

        # Verify .engine_sync.lock is released
        self.assertFalse((self.target_dir / ".engine_sync.lock").exists())

        # Verify DB state: eid2 removed, eid1 remains
        cur = self.db.conn.cursor()
        cur.execute("SELECT id FROM events WHERE id = ?", (eid2,))
        self.assertIsNone(cur.fetchone())
        cur.execute("SELECT id FROM events WHERE id = ?", (eid1,))
        self.assertIsNotNone(cur.fetchone())

        # Verify patches for eid2 removed
        cur.execute("SELECT id FROM patches WHERE event_id = ?", (eid2,))
        self.assertEqual(len(cur.fetchall()), 0)


class TestWatcherSyncLockSuppression(unittest.TestCase):
    """Test that _WatchdogEventHandler suppresses all events when .engine_sync.lock is present."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_lock_test_"))
        self.bundler = EventBundler(callback=lambda b: None, quiet_period=5.0)
        self.handler = _WatchdogEventHandler(target_path=self.temp_dir, bundler=self.bundler)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_engine_sync_lock_suppresses_all_files(self) -> None:
        test_file = self.temp_dir / "code.py"
        # Without lock: not ignored
        self.assertFalse(self.handler._is_ignored(test_file))

        # Create lock file
        lock_file = self.temp_dir / ".engine_sync.lock"
        lock_file.write_text("LOCKED\n", encoding="utf-8")

        # With lock: ignored!
        self.assertTrue(self.handler._is_ignored(test_file))

        # Remove lock file: not ignored again
        lock_file.unlink()
        self.assertFalse(self.handler._is_ignored(test_file))


class TestSelectiveBurstResolutionAndCachePruning(unittest.TestCase):
    """Test resolving bursts by burst_num or id and pruning corresponding AI cache files."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_trash_burst_test_"))
        self.current_dir = self.temp_dir / "current"
        self.current_dir.mkdir(parents=True, exist_ok=True)
        self.trash_mgr = TrashManager(engine_root=self.temp_dir)

        # Setup project
        self.proj_dir = self.current_dir / "burst_res_proj"
        self.proj_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.proj_dir / "session.db"
        self.db = SessionDB(self.db_path)
        self.sid = self.db.create_session("burst_res_proj", str(self.proj_dir))

        # Create bursts with distinct IDs and burst_nums:
        # Event ID 10 -> Burst 1
        # Event ID 20 -> Burst 2
        # Event ID 30 -> Burst 3
        self.db.record_event(self.sid, "EDIT", "a.py", "Burst 1", burst_num=1)
        self.db.conn.execute("UPDATE events SET id = 10 WHERE rowid = 1")
        self.db.conn.commit()

        self.db.record_event(self.sid, "EDIT", "b.py", "Burst 2", burst_num=2)
        self.db.conn.execute("UPDATE events SET id = 20 WHERE summary = 'Burst 2'")
        self.db.conn.commit()

        self.db.record_event(self.sid, "EDIT", "c.py", "Burst 3", burst_num=3)
        self.db.conn.execute("UPDATE events SET id = 30 WHERE summary = 'Burst 3'")
        self.db.conn.commit()

        # Create AI cache files for Burst 2
        cache_dir = self.temp_dir / "ai_data" / "cache" / "burst_res_proj"
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / "burst_overview_1_2_abc123.json").write_text("{}", encoding="utf-8")
        (cache_dir / "event_20.json").write_text("{}", encoding="utf-8")

        files_cache_dir = cache_dir / "files"
        files_cache_dir.mkdir(parents=True, exist_ok=True)
        (files_cache_dir / "file_analysis_1_2_b_py_xyz.json").write_text("{}", encoding="utf-8")

    def tearDown(self) -> None:
        self.db.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_selective_burst_deletion_by_burst_num(self) -> None:
        # Pass burst_ids=[2] which refers to burst_num=2 (event id 20)
        res = self.trash_mgr.move_to_trash("burst_res_proj", scope=SCOPE_SELECTIVE_BURSTS, burst_ids=[2])
        self.assertTrue(res["success"])
        self.assertEqual(res["deleted_bursts_count"], 1)

        # Check remaining events in DB: only 10 and 30 remain
        cur = self.db.conn.cursor()
        cur.execute("SELECT id FROM events ORDER BY id ASC")
        remaining = [r["id"] for r in cur.fetchall()]
        self.assertEqual(remaining, [10, 30])

        # Check cache files for burst 2 were pruned
        cache_dir = self.temp_dir / "ai_data" / "cache" / "burst_res_proj"
        self.assertFalse((cache_dir / "burst_overview_1_2_abc123.json").exists())
        self.assertFalse((cache_dir / "event_20.json").exists())
        self.assertFalse((cache_dir / "files" / "file_analysis_1_2_b_py_xyz.json").exists())


if __name__ == "__main__":
    unittest.main()
