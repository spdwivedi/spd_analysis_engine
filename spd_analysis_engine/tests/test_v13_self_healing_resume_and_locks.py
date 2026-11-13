"""
tests/test_v13_self_healing_resume_and_locks.py
================================================
Unit tests for Self-Healing Project Resumption and Windows File Lock Resilience.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from scripts.core.worker_helpers import safe_write_status_json, read_status_file
from scripts.storage.meta import synthesize_project_meta, read_project_meta
from scripts.server.routes_projects_resume import ProjectResumeRoutesMixin


class TestSynthesizeProjectMeta(unittest.TestCase):
    """Test automatic metadata reconstruction from session.db, history, and status files."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_test_v13_meta_"))
        for tier in ("current", "last_run", "history", "trash"):
            (self.temp_dir / tier).mkdir(parents=True, exist_ok=True)
        self.workspace = self.temp_dir / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_synthesize_from_current_session_db(self) -> None:
        proj_dir = self.temp_dir / "current" / "meta_heal_proj"
        proj_dir.mkdir(parents=True, exist_ok=True)
        db_path = proj_dir / "session.db"

        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """
            CREATE TABLE sessions (
                id INTEGER PRIMARY KEY,
                started_at TEXT,
                target_path TEXT,
                debounce_window REAL,
                powers TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO sessions (started_at, target_path, debounce_window, powers)
            VALUES (?, ?, ?, ?)
            """,
            (
                "2026-09-29T00:00:00Z",
                str(self.workspace),
                2.5,
                json.dumps({"track_edits": True, "track_reads": False, "track_exec": True, "shadow_git": True, "ide_profile": "cursor"}),
            ),
        )
        conn.commit()
        conn.close()

        meta = synthesize_project_meta(self.temp_dir, "meta_heal_proj")
        self.assertIsNotNone(meta)
        self.assertEqual(meta["target_path"], str(self.workspace))
        self.assertEqual(meta["debounce_window"], 2.5)
        self.assertEqual(meta["ide_profile"], "cursor")

        # Verify project.meta was written to disk
        meta_file = proj_dir / "project.meta"
        self.assertTrue(meta_file.exists())
        read_back = read_project_meta(self.temp_dir, "meta_heal_proj")
        self.assertEqual(read_back["target_path"], str(self.workspace))
        self.assertEqual(read_back["debounce_window"], "2.5")

    def test_synthesize_from_history_run(self) -> None:
        hist_run = self.temp_dir / "history" / "hist_proj" / "run_001"
        hist_run.mkdir(parents=True, exist_ok=True)
        db_path = hist_run / "session.db"

        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE sessions (id INTEGER PRIMARY KEY, target_path TEXT, debounce_window REAL, powers TEXT)")
        conn.execute(
            "INSERT INTO sessions (target_path, debounce_window, powers) VALUES (?, ?, ?)",
            (str(self.workspace), 4.0, json.dumps({"shadow_git": False})),
        )
        conn.commit()
        conn.close()

        meta = synthesize_project_meta(self.temp_dir, "hist_proj")
        self.assertIsNotNone(meta)
        self.assertEqual(meta["target_path"], str(self.workspace))
        self.assertEqual(meta["debounce_window"], 4.0)

    def test_synthesize_from_status_json_fallback(self) -> None:
        proj_dir = self.temp_dir / "last_run" / "status_proj"
        proj_dir.mkdir(parents=True, exist_ok=True)
        status_file = proj_dir / "worker.status.json"
        status_file.write_text(
            json.dumps({
                "target_path": str(self.workspace),
                "debounce": {"quiet_period": 3.0},
                "powers": {"ide_profile": "windsurf"},
            }),
            encoding="utf-8",
        )

        meta = synthesize_project_meta(self.temp_dir, "status_proj")
        self.assertIsNotNone(meta)
        self.assertEqual(meta["target_path"], str(self.workspace))
        self.assertEqual(meta["debounce_window"], 3.0)
        self.assertEqual(meta["ide_profile"], "windsurf")

    def test_synthesize_returns_none_when_nonexistent(self) -> None:
        meta = synthesize_project_meta(self.temp_dir, "ghost_project")
        self.assertIsNone(meta)


class TestProjectResumeSelfHealing(unittest.TestCase):
    """Test project resumption self-healing when project.meta is missing."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_test_v13_resume_"))
        for tier in ("current", "last_run", "history", "trash"):
            (self.temp_dir / tier).mkdir(parents=True, exist_ok=True)
        self.workspace = self.temp_dir / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)

        class MockSupervisor:
            def __init__(self):
                self.started = []

            def get_status(self):
                return []

            def start_project(self, **kwargs):
                self.started.append(kwargs)
                return {"started": True, **kwargs}

        class MockServer:
            engine_root = self.temp_dir
            supervisor = MockSupervisor()

        class MockHandler(ProjectResumeRoutesMixin):
            def __init__(self, srv):
                self.server_instance = srv
                self.last_json = None
                self.last_status = None
                self.last_error = None

            def _resolve_target_path(self, slug):
                return None

            def _send_json(self, data, status=200):
                self.last_json = data
                self.last_status = status

            def _send_error(self, message, status=500):
                self.last_error = message
                self.last_status = status

        self.server = MockServer()
        self.handler = MockHandler(self.server)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_resume_self_heals_missing_meta(self) -> None:
        proj_dir = self.temp_dir / "current" / "unmeta_proj"
        proj_dir.mkdir(parents=True, exist_ok=True)
        db_path = proj_dir / "session.db"

        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE sessions (id INTEGER PRIMARY KEY, target_path TEXT, debounce_window REAL, powers TEXT)")
        conn.execute(
            "INSERT INTO sessions (target_path, debounce_window, powers) VALUES (?, ?, ?)",
            (str(self.workspace), 1.5, json.dumps({"track_edits": True, "shadow_git": True})),
        )
        conn.commit()
        conn.close()

        # No project.meta exists yet
        meta_file = proj_dir / "project.meta"
        self.assertFalse(meta_file.exists())

        self.handler._handle_api_resume_project("unmeta_proj", body={})
        self.assertEqual(self.handler.last_status, 200)
        self.assertTrue(self.handler.last_json["success"])
        self.assertEqual(len(self.server.supervisor.started), 1)
        self.assertEqual(self.server.supervisor.started[0]["name"], "unmeta_proj")
        self.assertEqual(self.server.supervisor.started[0]["path"], str(self.workspace))
        self.assertTrue(meta_file.exists())

    def test_resume_fails_404_when_no_records_exist(self) -> None:
        self.handler._handle_api_resume_project("completely_unknown_proj")
        self.assertEqual(self.handler.last_status, 404)
        self.assertIn("project.meta not found", self.handler.last_error)


class TestWindowsFileLockResilience(unittest.TestCase):
    """Test safe status JSON read and atomic replace under simulated file locks."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_test_v13_locks_"))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_safe_write_status_json_success(self) -> None:
        target = self.temp_dir / "worker.status.json"
        success = safe_write_status_json(target, json.dumps({"state": "running"}))
        self.assertTrue(success)
        self.assertTrue(target.exists())
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"state": "running"})

    def test_safe_write_status_json_retry_on_permission_error(self) -> None:
        target = self.temp_dir / "worker.status.json"
        call_count = [0]
        real_replace = os.replace

        def mock_replace(src, dst):
            call_count[0] += 1
            if call_count[0] < 2:
                raise PermissionError(13, "Access is denied (simulated WinError 32)")
            return real_replace(src, dst)

        with patch("os.replace", side_effect=mock_replace):
            success = safe_write_status_json(target, json.dumps({"state": "retried"}), max_retries=3, delay=0.01)

        self.assertTrue(success)
        self.assertGreater(call_count[0], 1)
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"state": "retried"})

    def test_safe_write_status_json_cleanup_on_total_failure(self) -> None:
        target = self.temp_dir / "worker.status.json"

        with patch("os.replace", side_effect=PermissionError(13, "Permanent Lock")):
            success = safe_write_status_json(target, json.dumps({"state": "fail"}), max_retries=2, delay=0.01)

        self.assertFalse(success)
        # Ensure temporary file is cleaned up
        tmp_file = target.with_suffix(".json.tmp")
        self.assertFalse(tmp_file.exists())

    def test_read_status_file_shared_and_resilient(self) -> None:
        proj_dir = self.temp_dir / "active_proj"
        proj_dir.mkdir(parents=True, exist_ok=True)
        status_file = proj_dir / "worker.status.json"
        status_file.write_text(json.dumps({"status": "active", "burst_count": 42}), encoding="utf-8")

        # Test normal read
        data = read_status_file(proj_dir)
        self.assertEqual(data.get("status"), "active")
        self.assertEqual(data.get("burst_count"), 42)

        # Test missing file
        empty_dir = self.temp_dir / "empty_proj"
        empty_dir.mkdir(parents=True, exist_ok=True)
        self.assertEqual(read_status_file(empty_dir), {})

        # Test corrupted JSON
        bad_dir = self.temp_dir / "bad_proj"
        bad_dir.mkdir(parents=True, exist_ok=True)
        (bad_dir / "worker.status.json").write_text("{corrupt json ...", encoding="utf-8")
        self.assertEqual(read_status_file(bad_dir), {})


if __name__ == "__main__":
    unittest.main()
