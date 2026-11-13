"""
tests/test_v20_process_sweeper_and_hot_reload.py
=================================================
Tests for OS-Level Process Sweeper, Canonical Slug Normalization,
Debounce Hot-Reloading, and Complete Trash Pruning.
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from scripts.core.process_sweeper import sweep_project_processes
from scripts.core.supervisor import Supervisor, _WorkerEntry
from scripts.core.worker_bundle import _run_heartbeat
from scripts.core.worker_lifecycle import WorkerState
from scripts.event_bundler import EventBundler
from scripts.storage.utils import _slug
from scripts.trash.manager import TrashManager
from scripts.trash.constants import SCOPE_LOCAL_SESSION, SCOPE_COMPLETE_ERASE


class TestProcessSweeperAndHotReload(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_v20_test_"))
        self.engine_root = self.temp_dir / "engine"
        self.workspace = self.temp_dir / "workspace"
        self.engine_root.mkdir(parents=True, exist_ok=True)
        self.workspace.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    def test_01_process_sweeper_terminates_matching_process(self):
        """sweep_project_processes terminates matching worker daemons and ignores others."""
        slug = "sweeper_target"
        # Spawn a dummy process that includes 'project_worker.py' and '--name sweeper_target' in cmdline
        script_code = "import sys, time; time.sleep(30)"
        matching_proc = subprocess.Popen(
            [sys.executable, "-c", script_code, "project_worker.py", "--name", slug],
        )
        non_matching_proc = subprocess.Popen(
            [sys.executable, "-c", script_code, "project_worker.py", "--name", "other_slug"],
        )

        try:
            time.sleep(0.3)
            self.assertIsNone(matching_proc.poll())
            self.assertIsNone(non_matching_proc.poll())

            swept_pids = sweep_project_processes(slug)
            self.assertIn(matching_proc.pid, swept_pids)
            self.assertNotIn(non_matching_proc.pid, swept_pids)

            # Wait briefly and verify matching proc is gone, non-matching proc is still alive
            matching_proc.wait(timeout=2.0)
            self.assertIsNotNone(matching_proc.poll())
            self.assertIsNone(non_matching_proc.poll())
        finally:
            for p in (matching_proc, non_matching_proc):
                try:
                    p.terminate()
                    p.wait(timeout=1.0)
                except Exception:
                    pass

    def test_02_supervisor_case_insensitive_slug_reconciliation(self):
        """Supervisor reconciles and matches projects regardless of casing (SPCL vs spcl)."""
        sup = Supervisor(self.engine_root)
        name = "SPCL"
        clean_slug = _slug(name)
        self.assertEqual(clean_slug, "spcl")

        proj_dir = self.engine_root / "current" / clean_slug
        proj_dir.mkdir(parents=True, exist_ok=True)

        entry = _WorkerEntry(
            name=name,
            target_path=str(self.workspace),
            pid=999999,
            log_path=proj_dir / "worker.log",
        )
        sup._registry[clean_slug] = entry
        sup._registry[name] = entry

        # Lookup by uppercase
        res_upper = sup.get_status("SPCL")
        self.assertEqual(len(res_upper), 1)
        self.assertEqual(res_upper[0]["project"], "SPCL")

        # Lookup by lowercase
        res_lower = sup.get_status("spcl")
        self.assertEqual(len(res_lower), 1)
        self.assertEqual(res_lower[0]["project"], "SPCL")

        # Status without project argument deduplicates entries
        all_status = sup.get_status()
        self.assertEqual(len(all_status), 1)

    def test_03_debounce_hot_reloading(self):
        """Updating debounce_window in project.meta updates EventBundler quiet_period dynamically."""
        proj_dir = self.engine_root / "current" / "hot_reload_proj"
        proj_dir.mkdir(parents=True, exist_ok=True)
        pid_file = proj_dir / "worker.pid"
        pid_file.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")

        meta_file = proj_dir / "project.meta"
        meta_file.write_text(json.dumps({"debounce_window": 3.5}), encoding="utf-8")

        bundler = EventBundler(quiet_period=3.5)
        state = WorkerState(
            project_name="hot_reload_proj",
            target_path_str=str(self.workspace),
            engine_root_str=str(self.engine_root),
            pid_file=pid_file,
            event_bundler=bundler,
        )

        self.assertEqual(bundler.quiet_period, 3.5)

        # Update metadata to 1.8s
        time.sleep(0.05)
        meta_file.write_text(json.dumps({"debounce_window": 1.8}), encoding="utf-8")
        os.utime(meta_file, (time.time() + 2, time.time() + 2))

        # Run heartbeat loop for a brief tick
        def _stop_soon():
            time.sleep(0.3)
            state.shutdown_requested = True

        import threading
        t = threading.Thread(target=_stop_soon)
        t.start()
        _run_heartbeat(interval=0.2, state=state)
        t.join(timeout=2.0)

        self.assertAlmostEqual(bundler.quiet_period, 1.8, places=2)
        self.assertAlmostEqual(bundler.configured_quiet_period, 1.8, places=2)

    def test_04_trash_deletion_removes_current_slug_completely(self):
        """Local session and complete erase permanently prune current/<slug> directory."""
        slug = "prune_test_proj"
        cur_dir = self.engine_root / "current" / slug
        cur_dir.mkdir(parents=True, exist_ok=True)
        (cur_dir / "session.db").write_text("sqlite dummy data", encoding="utf-8")
        (cur_dir / "worker.log").write_text("log data", encoding="utf-8")
        (cur_dir / "worker.status.json").write_text("{}", encoding="utf-8")
        (cur_dir / "project.meta").write_text(f"target_path={self.workspace}\n", encoding="utf-8")
        patches_dir = cur_dir / "patches"
        patches_dir.mkdir(parents=True, exist_ok=True)
        (patches_dir / "event_1.patch").write_text("diff 1", encoding="utf-8")

        trash_mgr = TrashManager(self.engine_root)
        self.assertTrue(cur_dir.exists())

        res = trash_mgr.delete_project(slug, scope=SCOPE_LOCAL_SESSION)
        self.assertTrue(res.get("success"))
        # Ensure current/<slug> is completely absent!
        self.assertFalse(cur_dir.exists(), "current/<slug> must be completely absent after session deletion")

        # Verify trash has the moved session files and manifest
        trash_entries = list(trash_mgr.trash_dir.glob(f"*_{slug}_session*"))
        self.assertTrue(len(trash_entries) > 0)
        self.assertTrue((trash_entries[0] / "manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
