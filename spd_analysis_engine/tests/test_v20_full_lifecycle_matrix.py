"""
tests/test_v20_full_lifecycle_matrix.py
=========================================
Full Lifecycle Project Mock Integration Test Matrix.
Verifies process collision resistance, zombie sweeping, dynamic hot-reloading,
safe physical rollback under active workers, clean session trash pruning,
and any-tier project context resolution.
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
from unittest.mock import MagicMock

from scripts.core.process_sweeper import sweep_project_processes
from scripts.core.supervisor import Supervisor, _WorkerEntry
from scripts.core.worker_bundle import _run_heartbeat
from scripts.core.worker_lifecycle import WorkerState
from scripts.event_bundler import EventBundler
from scripts.git_shadow import ShadowGit
from scripts.git.shadow_rollback import rollback_to_burst
from scripts.storage.db import SessionDB
from scripts.storage.meta import resolve_project_context, read_project_meta, update_project_meta
from scripts.storage.utils import _slug
from scripts.trash.manager import TrashManager
from scripts.trash.constants import SCOPE_LOCAL_SESSION


class TestFullLifecycleMatrix(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_matrix_test_"))
        self.engine_root = self.temp_dir / "engine"
        self.workspace = self.temp_dir / "workspace"

        for d in ("current", "last_run", "history", "trash"):
            (self.engine_root / d).mkdir(parents=True, exist_ok=True)
        self.workspace.mkdir(parents=True, exist_ok=True)

        # Populate realistic workspace files
        (self.workspace / "index.js").write_text("console.log('init');\n", encoding="utf-8")
        (self.workspace / "README.md").write_text("# Test Project\n", encoding="utf-8")
        src_dir = self.workspace / "src"
        src_dir.mkdir(parents=True, exist_ok=True)
        (src_dir / "app.py").write_text("def run(): pass\n", encoding="utf-8")

    def tearDown(self) -> None:
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    def test_01_casing_collision_and_zombie_sweeper(self) -> None:
        """Attempting to launch SPCL and spcl resolves to the same entity and sweeps rogue daemons."""
        sup = Supervisor(self.engine_root)
        name_upper = "SPCL"
        name_lower = "spcl"
        clean_slug = _slug(name_upper)
        self.assertEqual(clean_slug, "spcl")

        # Spawn a simulated rogue daemon process for SPCL
        dummy_code = "import sys, time; time.sleep(30)"
        rogue_proc = subprocess.Popen(
            [sys.executable, "-c", dummy_code, "project_worker.py", "--name", name_upper],
        )

        try:
            time.sleep(0.3)
            self.assertIsNone(rogue_proc.poll(), "Rogue worker daemon should be running")

            # Register SPCL as active worker in Supervisor
            entry = _WorkerEntry(
                name=name_upper,
                target_path=str(self.workspace),
                pid=rogue_proc.pid,
                log_path=self.engine_root / "current" / clean_slug / "worker.log",
            )
            sup._registry[clean_slug] = entry
            sup._registry[name_upper] = entry

            # Attempting to start lower-case 'spcl' must detect running worker and reject duplicate
            with self.assertRaises(ValueError) as ctx:
                sup.start_project(name=name_lower, path=str(self.workspace))
            self.assertIn("already running", str(ctx.exception).lower())

            # Status query returns unified entity whether queried with SPCL or spcl
            st_upper = sup.get_status(name_upper)
            st_lower = sup.get_status(name_lower)
            self.assertEqual(len(st_upper), 1)
            self.assertEqual(len(st_lower), 1)
            self.assertEqual(st_upper[0]["pid"], rogue_proc.pid)
            self.assertEqual(st_lower[0]["pid"], rogue_proc.pid)

            # Sweep processes targeting this slug and workspace
            swept = sweep_project_processes(name_lower, str(self.workspace))
            self.assertIn(rogue_proc.pid, swept)

            rogue_proc.wait(timeout=2.0)
            self.assertIsNotNone(rogue_proc.poll())
        finally:
            try:
                rogue_proc.terminate()
                rogue_proc.wait(timeout=1.0)
            except Exception:
                pass

    def test_02_dynamic_debounce_hot_reload(self) -> None:
        """Modifying debounce_window in project.meta updates EventBundler quiet_period live."""
        slug = "matrix_debounce_proj"
        cur_dir = self.engine_root / "current" / slug
        cur_dir.mkdir(parents=True, exist_ok=True)
        pid_file = cur_dir / "worker.pid"
        pid_file.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")

        meta_file = cur_dir / "project.meta"
        meta_file.write_text(json.dumps({"debounce_window": 3.5}), encoding="utf-8")

        bundler = EventBundler(quiet_period=3.5)
        state = WorkerState(
            project_name=slug,
            target_path_str=str(self.workspace),
            engine_root_str=str(self.engine_root),
            pid_file=pid_file,
            event_bundler=bundler,
        )

        self.assertEqual(bundler.quiet_period, 3.5)

        # Update metadata to 1.5s
        time.sleep(0.05)
        meta_file.write_text(json.dumps({"debounce_window": 1.5}), encoding="utf-8")
        os.utime(meta_file, (time.time() + 2, time.time() + 2))

        # Advance heartbeat loop
        def _stop_heartbeat():
            time.sleep(0.25)
            state.shutdown_requested = True

        t = threading.Thread(target=_stop_heartbeat)
        t.start()
        _run_heartbeat(interval=0.1, state=state)
        t.join(timeout=2.0)

        # Assert quiet_period updated without restarting worker
        self.assertAlmostEqual(bundler.quiet_period, 1.5, places=2)
        self.assertAlmostEqual(bundler.configured_quiet_period, 1.5, places=2)

    def test_03_burst_rollback_under_active_worker(self) -> None:
        """Physical rollback under active worker completes in < 2.0s and creates trash manifest."""
        slug = "matrix_rollback_proj"
        cur_dir = self.engine_root / "current" / slug
        cur_dir.mkdir(parents=True, exist_ok=True)
        shadow_dir = cur_dir / "shadow_git"

        sg = ShadowGit(shadow_dir=shadow_dir, target_dir=self.workspace)
        if not sg.is_available:
            self.skipTest("Git not available on host")
        sg.init_repo()

        # Step 1: Generate 10 sample files and commit Burst #1
        for i in range(10):
            (self.workspace / f"module_{i}.txt").write_text(f"v1_content_{i}\n", encoding="utf-8")
        c1 = sg.commit_burst(event_id=1, first_file="module_0.txt", summary="Burst #1: Baseline")
        self.assertIsNotNone(c1)

        # Step 2: Modify 10 files and add new file for Burst #2
        for i in range(10):
            (self.workspace / f"module_{i}.txt").write_text(f"v2_modified_{i}\n", encoding="utf-8")
        (self.workspace / "module_new.txt").write_text("v2_new_file\n", encoding="utf-8")
        c2 = sg.commit_burst(event_id=2, first_file="module_0.txt", summary="Burst #2: Mutated")
        self.assertIsNotNone(c2)

        # Verify mutated workspace state
        self.assertEqual((self.workspace / "module_0.txt").read_text(encoding="utf-8"), "v2_modified_0\n")
        self.assertTrue((self.workspace / "module_new.txt").exists())

        # Step 3: Trigger rollback_to_burst to Burst #1
        start_time = time.monotonic()
        res = rollback_to_burst(
            target_path=self.workspace,
            shadow_git_dir=shadow_dir,
            burst_hash=c1,
            project_slug=slug,
            engine_root=self.engine_root,
            burst_id=1,
        )
        elapsed = time.monotonic() - start_time

        self.assertTrue(res.get("success"), f"Rollback failed: {res}")
        self.assertLess(elapsed, 2.0, f"Rollback took {elapsed:.2f}s, exceeding 2.0s threshold")

        # Step 4: Verify workspace files physically restored to Burst #1
        self.assertEqual((self.workspace / "module_0.txt").read_text(encoding="utf-8"), "v1_content_0\n")
        self.assertEqual((self.workspace / "module_9.txt").read_text(encoding="utf-8"), "v1_content_9\n")
        self.assertFalse((self.workspace / "module_new.txt").exists(), "Added files must be removed on rollback")

        # Step 5: Verify pre-rollback changes preserved in trash with manifest.json
        trash_backups = list((self.engine_root / "trash" / slug).glob("rollback_*"))
        self.assertTrue(len(trash_backups) > 0, "Pre-rollback trash backup directory must exist")
        manifest_file = trash_backups[0] / "manifest.json"
        self.assertTrue(manifest_file.exists(), "Restore manifest.json must exist in trash backup")
        manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
        self.assertEqual(manifest_data.get("scope"), "rollback_backup")
        self.assertEqual(manifest_data.get("burst_id"), 1)

    def test_04_session_clean_and_zero_resurrection(self) -> None:
        """Stopping worker and SCOPE_LOCAL_SESSION deletion prunes current/<slug> with zero resurrect."""
        slug = "matrix_prune_proj"
        cur_dir = self.engine_root / "current" / slug
        cur_dir.mkdir(parents=True, exist_ok=True)

        db_path = cur_dir / "session.db"
        sdb = SessionDB(db_path)
        sid = sdb.create_session(slug, str(self.workspace))
        sdb.record_event(sid, "EDIT", "index.js", "Initial edit")
        sdb.checkpoint()
        sdb.close()

        (cur_dir / "worker.log").write_text("worker active\n", encoding="utf-8")
        (cur_dir / "worker.pid").write_text(json.dumps({"pid": 999999}), encoding="utf-8")
        (cur_dir / "worker.status.json").write_text(json.dumps({"status": "ACTIVE"}), encoding="utf-8")
        (cur_dir / "project.meta").write_text(f"target_path={self.workspace}\n", encoding="utf-8")
        patches_dir = cur_dir / "patches"
        patches_dir.mkdir(parents=True, exist_ok=True)
        (patches_dir / "event_1.patch").write_text("patch diff\n", encoding="utf-8")

        self.assertTrue(cur_dir.exists())

        # Perform soft deletion with SCOPE_LOCAL_SESSION
        trash_mgr = TrashManager(self.engine_root)
        del_res = trash_mgr.delete_project(slug, scope=SCOPE_LOCAL_SESSION)
        self.assertTrue(del_res.get("success"))

        # Assert current/<slug> is completely removed from disk
        self.assertFalse(cur_dir.exists(), "current/<slug> must be completely removed from disk")

        # Supervisor status and project listing must show 0 active workers and no resurrection
        sup = Supervisor(self.engine_root)
        projects = sup.list_projects()
        self.assertEqual(len(projects["running"]), 0)
        self.assertFalse(cur_dir.exists(), "current/<slug> must not be resurrected after list_projects")

        # Verify moved session and manifest in trash
        trash_entries = list(trash_mgr.trash_dir.glob(f"*_{slug}_session*"))
        self.assertTrue(len(trash_entries) > 0)
        self.assertTrue((trash_entries[0] / "manifest.json").exists())

    def test_05_any_tier_git_push_resolution(self) -> None:
        """resolve_project_context resolves unpushed bursts and metadata seamlessly from history/."""
        slug = "matrix_history_proj"
        hist_run_dir = self.engine_root / "history" / slug / "run_20260929_060000"
        hist_run_dir.mkdir(parents=True, exist_ok=True)

        hist_db_path = hist_run_dir / "session.db"
        sdb = SessionDB(hist_db_path)
        sid = sdb.create_session(slug, str(self.workspace))
        ev_id = sdb.record_event(sid, "EDIT", "src/app.py", "feat: core app module")
        sdb.checkpoint()
        sdb.close()

        (hist_run_dir / "project.meta").write_text(
            f"target_path={self.workspace}\nremote_url=https://github.com/test/repo.git\nremote_branch=main\n",
            encoding="utf-8",
        )

        # Resolve project context across tiers
        ctx = resolve_project_context(slug, self.engine_root)
        self.assertEqual(ctx["tier"], "history")
        self.assertEqual(Path(ctx["target_path"]).resolve(), self.workspace.resolve())
        self.assertIsNotNone(ctx["db_path"])
        self.assertTrue(Path(ctx["db_path"]).exists())

        # Verify unpushed burst scoping against historical database
        conn = sqlite3.connect(ctx["db_path"])
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT id, first_file_touched, summary FROM events WHERE pushed_in_milestone_id IS NULL")
        rows = cur.fetchall()
        conn.close()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], ev_id)
        self.assertEqual(rows[0]["first_file_touched"], "src/app.py")


if __name__ == "__main__":
    unittest.main()
