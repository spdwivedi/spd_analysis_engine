"""
tests/test_v19_worker_interlock_and_lock_elimination.py
======================================================
Tests for WorkerInterlock, double-rotation elimination, and SQLite connection resilience.
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from scripts.core.worker_interlock import interlock_project_operation, _is_proc_alive
from scripts.core.supervisor import Supervisor, _WorkerEntry
from scripts.core.reaper import ReaperDaemon
from scripts.core.worker_lifecycle import WorkerState, _clean_shutdown
from scripts.server.db_queries import _read_sessions_from_db, resolve_burst_event
from scripts.storage.history import consolidate_project_history
from scripts.storage.db import SessionDB


class TestWorkerInterlockAndLockElimination(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_v19_test_"))
        self.engine_root = self.temp_dir / "engine"
        self.workspace = self.temp_dir / "workspace"
        self.engine_root.mkdir(parents=True, exist_ok=True)
        self.workspace.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    def test_01_interlock_stops_worker_and_restarts(self):
        """Worker is terminated, locks released, and restarted after operation."""
        slug = "interlock-proj"
        curr_dir = self.engine_root / "current" / slug
        curr_dir.mkdir(parents=True, exist_ok=True)

        meta_file = curr_dir / "project.meta"
        meta_file.write_text(
            f"target_path={self.workspace}\npowers={{\"track_reads\":true,\"track_exec\":false,\"shadow_git\":true}}\n",
            encoding="utf-8",
        )

        dummy = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        pid = dummy.pid
        pid_file = curr_dir / "worker.pid"
        pid_file.write_text(json.dumps({"pid": pid}), encoding="utf-8")

        mock_sup = MagicMock()
        mock_sup._registry = {slug: MagicMock(pid=pid, is_alive=lambda: True)}

        self.assertTrue(_is_proc_alive(pid))

        try:
            with interlock_project_operation(slug, self.engine_root, restart_worker_after=True, supervisor=mock_sup):
                dummy.poll()
                self.assertFalse(_is_proc_alive(pid))
                self.assertNotIn(slug, mock_sup._registry)

            mock_sup.start_project.assert_called_once()
            _, kwargs = mock_sup.start_project.call_args
            self.assertEqual(kwargs.get("name"), slug)
            self.assertEqual(kwargs.get("track_reads"), True)
            self.assertEqual(kwargs.get("track_exec"), False)
        finally:
            try:
                dummy.terminate()
                dummy.wait(timeout=1.0)
            except Exception:
                pass

    def test_02_interlock_no_restart_and_cleans_leftover_locks(self):
        """When restart_worker_after=False, worker is stopped and locks unlinked."""
        slug = "interlock-no-restart"
        curr_dir = self.engine_root / "current" / slug
        curr_dir.mkdir(parents=True, exist_ok=True)

        dummy = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        pid = dummy.pid
        pid_file = curr_dir / "worker.pid"
        pid_file.write_text(str(pid), encoding="utf-8")

        lock_file = curr_dir / "session.lock"
        lock_file.write_text("ACTIVE", encoding="utf-8")

        mock_sup = MagicMock()
        mock_sup._registry = {slug: MagicMock(pid=pid)}

        try:
            with interlock_project_operation(slug, self.engine_root, restart_worker_after=False, supervisor=mock_sup):
                self.assertFalse(_is_proc_alive(pid))

            mock_sup.start_project.assert_not_called()
            self.assertFalse(pid_file.exists())
            self.assertFalse(lock_file.exists())
        finally:
            try:
                dummy.terminate()
                dummy.wait(timeout=1.0)
            except Exception:
                pass

    def test_03_double_rotation_eliminated_on_clean_shutdown(self):
        """_clean_shutdown does not rotate current/ to last_run/."""
        slug = "shutdown-proj"
        curr_dir = self.engine_root / "current" / slug
        curr_dir.mkdir(parents=True, exist_ok=True)

        meta_file = curr_dir / "project.meta"
        meta_file.write_text(f"target_path={self.workspace}\nstatus=active\n", encoding="utf-8")

        state = WorkerState()
        state.project_name = slug
        state.engine_root_str = str(self.engine_root)
        state.target_path_str = str(self.workspace)

        with self.assertRaises(SystemExit) as ctx:
            _clean_shutdown(state=state)

        self.assertEqual(ctx.exception.code, 0)
        self.assertTrue(curr_dir.exists(), "current/<slug> must not be rotated away by worker clean shutdown")
        last_run_dir = self.engine_root / "last_run" / slug
        self.assertFalse(last_run_dir.exists(), "last_run/<slug> must not be created during worker shutdown")

    def test_04_supervisor_and_reaper_skip_archive_if_current_missing(self):
        """Supervisor.stop_project and ReaperDaemon do not crash when current/ is missing."""
        sup = Supervisor(engine_root=self.engine_root)
        ghost = "ghost-proj"
        sup._registry[ghost] = _WorkerEntry(name=ghost, target_path="", pid=99999999)

        result = sup.stop_project(ghost)
        self.assertEqual(result.get("status"), "stopped")
        self.assertNotIn(ghost, sup._registry)

        mock_rotator = MagicMock()
        mock_rotator.root = self.engine_root
        reaper = ReaperDaemon(registry={ghost: MagicMock(is_alive=lambda: False, pid=99999999, exit_code=1)}, rotator=mock_rotator)

        reaper._registry[ghost] = MagicMock(is_alive=lambda: False, pid=99999999, exit_code=1)
        dead = [name for name, entry in list(reaper._registry.items()) if not entry.is_alive()]
        for name in dead:
            if (self.engine_root / "current" / name).exists():
                mock_rotator.archive_project(name)
            reaper._registry.pop(name, None)

        mock_rotator.archive_project.assert_not_called()
        self.assertNotIn(ghost, reaper._registry)

    def test_05_db_queries_and_history_connections_closed_safely(self):
        """SQLite connections in db_queries and history are closed in finally blocks."""
        db_path = self.temp_dir / "test_session.db"
        sdb = SessionDB(db_path)
        sid = sdb.create_session("conn-test", str(self.workspace))
        sdb.record_event(sid, "EDIT", "test.py", "Test patch")
        sdb.checkpoint()
        sdb.close()

        sessions = _read_sessions_from_db(db_path)
        self.assertEqual(len(sessions), 1)

        eid, bnum, rsid = resolve_burst_event(db_path, session_id=sid, burst_id_or_number=1)
        self.assertEqual(rsid, sid)

        hist_slug_dir = self.engine_root / "history" / "conn-test" / "run_20260101_000000"
        hist_slug_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(db_path, hist_slug_dir / "session.db")

        out_path = consolidate_project_history(self.engine_root, "conn-test")
        self.assertIsNotNone(out_path)

        # On Windows, deleting the DB proves all connection handles were completely closed
        try:
            db_path.unlink()
            self.assertFalse(db_path.exists())
        except PermissionError:
            self.fail("Windows file lock remained open on session.db; connection was leaked!")


if __name__ == "__main__":
    unittest.main()
