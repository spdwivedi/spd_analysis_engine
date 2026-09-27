"""
tests/test_v9_crash_rollback_and_lap.py
=======================================
Unit tests for Crash & Ungraceful Termination Sentinel, Granular Burst Rollback,
Manual Burst Sealing (Lap Button), Debounce Window expansion, and Native .git
Remote Auto-Discovery in the SPD Analysis Engine.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.event_bundler import EventBundler
from scripts.git_shadow import ShadowGit, discover_native_git_remote
from scripts.storage_rotator import (
    StorageRotator,
    SessionDB,
    read_project_meta,
    update_project_meta,
    rollback_burst,
    _slug,
)
from scripts.trash_manager import TrashManager, SCOPE_SELECTIVE_BURSTS
from scripts.web_server import EngineWebServer, _EngineRequestHandler


class TestEventBundlerLapSealing(unittest.TestCase):
    """Test EventBundler.flush_burst_now() immediate manual sealing."""

    def test_flush_burst_now_emits_pending_bundle(self) -> None:
        emitted_bundles: list[dict] = []

        def on_bundle(b: dict) -> None:
            emitted_bundles.append(b)

        bundler = EventBundler(
            callback=on_bundle,
            quiet_period=30.0,  # Long quiet period so timer does not fire naturally
        )

        # Initially idle
        self.assertFalse(bundler.is_pending)
        self.assertIsNone(bundler.flush_burst_now())

        # Add events
        bundler.add_event("modified", "src/index.js")
        bundler.add_event("created", "src/utils.js")

        self.assertTrue(bundler.is_pending)
        status = bundler.get_debounce_status()
        self.assertTrue(status["active"])
        self.assertEqual(status["files_count"], 2)
        self.assertEqual(status["first_file"], "src/index.js")

        # Manually seal burst (Lap Button)
        bundle = bundler.flush_burst_now()
        self.assertIsNotNone(bundle)
        assert bundle is not None
        self.assertEqual(len(bundle["files_touched"]), 2)
        self.assertEqual(bundle["first_file"], "src/index.js")
        self.assertEqual(len(emitted_bundles), 1)

        # State should be reset to idle
        self.assertFalse(bundler.is_pending)
        status_after = bundler.get_debounce_status()
        self.assertFalse(status_after["active"])

        # Second flush returns None since queue was cleared
        self.assertIsNone(bundler.flush_burst_now())
        bundler.stop()


class TestNativeGitRemoteAutoDiscovery(unittest.TestCase):
    """Test discover_native_git_remote() on git and non-git directories."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_git_remote_test_"))
        self.git_available = shutil.which("git") is not None

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_discover_non_git_dir(self) -> None:
        remote_url, branch = discover_native_git_remote(self.temp_dir)
        self.assertEqual(remote_url, "")
        self.assertEqual(branch, "")

    def test_discover_git_repo_with_remote(self) -> None:
        if not self.git_available:
            self.skipTest("git binary not available on host system")

        repo_dir = self.temp_dir / "my_repo"
        repo_dir.mkdir(parents=True, exist_ok=True)

        # Initialize git repo and add mock origin
        subprocess.run(["git", "init"], cwd=str(repo_dir), check=True, capture_output=True)
        subprocess.run(
            ["git", "remote", "add", "origin", "https://github.com/myorg/awesome-app.git"],
            cwd=str(repo_dir),
            check=True,
            capture_output=True,
        )

        remote_url, branch = discover_native_git_remote(repo_dir)
        self.assertEqual(remote_url, "https://github.com/myorg/awesome-app.git")
        self.assertIn(branch, ["main", "master"])

    def test_read_project_meta_auto_discovers_remote(self) -> None:
        if not self.git_available:
            self.skipTest("git binary not available on host system")

        engine_root = self.temp_dir / "engine"
        repo_dir = self.temp_dir / "target_repo"
        repo_dir.mkdir(parents=True, exist_ok=True)

        subprocess.run(["git", "init"], cwd=str(repo_dir), check=True, capture_output=True)
        subprocess.run(
            ["git", "remote", "add", "origin", "git@github.com:test/repo.git"],
            cwd=str(repo_dir),
            check=True,
            capture_output=True,
        )

        rotator = StorageRotator(engine_root)
        proj_dir = rotator.init_project("test_auto_remote", str(repo_dir))

        meta = read_project_meta(engine_root, "test_auto_remote")
        self.assertEqual(meta.get("remote_url"), "git@github.com:test/repo.git")
        self.assertEqual(meta.get("clean_exit"), "false")
        self.assertEqual(meta.get("status"), "active")


class TestSessionLockAndInterruptedDetection(unittest.TestCase):
    """Test session.lock sentinel, clean stop, and INTERRUPTED status detection."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_crash_test_"))
        self.engine_root = self.temp_dir / "engine"
        self.engine_root.mkdir(parents=True, exist_ok=True)
        self.target_dir = self.temp_dir / "target"
        self.target_dir.mkdir(parents=True, exist_ok=True)

        self.rotator = StorageRotator(self.engine_root)
        self.server = EngineWebServer(engine_root=self.engine_root, port=0)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_interrupted_status_when_dead_worker_has_lock(self) -> None:
        proj_slug = "crash_proj"
        proj_dir = self.rotator.init_project(proj_slug, str(self.target_dir))

        # Simulate worker creating session.lock
        lock_file = proj_dir / "session.lock"
        lock_file.write_text(json.dumps({"pid": 99999, "start_time": "2026-09-26T10:00:00Z"}), encoding="utf-8")

        # Mock supervisor reporting no running workers
        self.server.supervisor.get_status = MagicMock(return_value=[])

        handler = MagicMock(spec=_EngineRequestHandler)
        handler.server_instance = self.server

        sent_json = {}
        def mock_send_json(data: dict) -> None:
            nonlocal sent_json
            sent_json = data

        handler._send_json = mock_send_json

        _EngineRequestHandler._handle_api_projects(handler)

        self.assertIn("last_run", sent_json)
        last_run_items = sent_json["last_run"]
        proj_item = next((p for p in last_run_items if p["name"] == proj_slug), None)
        self.assertIsNotNone(proj_item)
        assert proj_item is not None
        self.assertEqual(proj_item["status"], "INTERRUPTED")
        self.assertTrue(proj_item["is_interrupted"])
        self.assertIn("terminated unexpectedly", proj_item["interrupted_reason"])

    def test_clean_stop_removes_lock_and_marks_stopped(self) -> None:
        proj_slug = "clean_proj"
        proj_dir = self.rotator.init_project(proj_slug, str(self.target_dir))

        lock_file = proj_dir / "session.lock"
        lock_file.write_text(json.dumps({"pid": 99998}), encoding="utf-8")

        handler = MagicMock(spec=_EngineRequestHandler)
        handler.server_instance = self.server

        sent_json = {}
        def mock_send_json(data: dict) -> None:
            nonlocal sent_json
            sent_json = data

        handler._send_json = mock_send_json

        # Call stop project API
        _EngineRequestHandler._handle_api_stop_project(handler, {"project": proj_slug})

        self.assertFalse(lock_file.exists())
        meta = read_project_meta(self.engine_root, proj_slug)
        self.assertEqual(meta.get("clean_exit"), "true")
        self.assertEqual(meta.get("status"), "stopped")

        # Now _handle_api_projects should report STOPPED, not INTERRUPTED
        self.server.supervisor.get_status = MagicMock(return_value=[])
        _EngineRequestHandler._handle_api_projects(handler)

        last_run_items = sent_json["last_run"]
        proj_item = next((p for p in last_run_items if p["name"] == proj_slug), None)
        self.assertIsNotNone(proj_item)
        assert proj_item is not None
        self.assertEqual(proj_item["status"], "STOPPED")
        self.assertFalse(proj_item["is_interrupted"])


class TestGranularBurstRollback(unittest.TestCase):
    """Test rollback_burst() removing events from DB, rolling back shadow git, and moving to trash."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_rollback_test_"))
        self.engine_root = self.temp_dir / "engine"
        self.engine_root.mkdir(parents=True, exist_ok=True)
        self.target_dir = self.temp_dir / "target"
        self.target_dir.mkdir(parents=True, exist_ok=True)

        self.rotator = StorageRotator(self.engine_root)
        self.proj_dir = self.rotator.init_project("rollback_demo", str(self.target_dir))
        self.db_path = self.proj_dir / "session.db"
        self.db = SessionDB(self.db_path)
        self.sid = self.db.create_session("rollback_demo", str(self.target_dir))

        self.server = EngineWebServer(engine_root=self.engine_root, port=0)

    def tearDown(self) -> None:
        self.db.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_rollback_burst_and_move_to_trash(self) -> None:
        # Create an event in DB
        ev_id = self.db.record_event(
            session_id=self.sid,
            event_type="EDIT",
            first_file="main.py",
            summary="Prompt Burst #1: Modified 1 file(s)",
        )
        self.db.record_patch(
            event_id=ev_id,
            file_path="main.py",
            diff_content="--- a/main.py\n+++ b/main.py\n@@ -1 +1 @@\n-old\n+new",
        )

        # Create patch file on disk
        patches_dir = self.proj_dir / "patches"
        patches_dir.mkdir(parents=True, exist_ok=True)
        patch_file = patches_dir / f"event_{ev_id}.patch"
        patch_file.write_text("patch content", encoding="utf-8")

        # Also create associated GIT micro-commit event
        git_ev_id = self.db.record_event(
            session_id=self.sid,
            event_type="GIT",
            first_file=None,
            summary=f"Micro-commit: Burst #{ev_id}: Modified 1 file(s)",
        )

        # Perform rollback
        res = rollback_burst(self.engine_root, "rollback_demo", ev_id)
        self.assertTrue(res.get("success"))
        self.assertEqual(res.get("event_id"), ev_id)

        # Verify event and git event are removed from session.db
        cur = self.db.conn.cursor()
        cur.execute("SELECT id FROM events WHERE id = ?", (ev_id,))
        self.assertIsNone(cur.fetchone())
        cur.execute("SELECT id FROM events WHERE id = ?", (git_ev_id,))
        self.assertIsNone(cur.fetchone())

        # Verify patch rows are deleted
        cur.execute("SELECT id FROM patches WHERE event_id = ?", (ev_id,))
        self.assertEqual(len(cur.fetchall()), 0)

        # Verify moved to trash under selective_bursts
        tm = TrashManager(self.engine_root)
        trash_items = tm.list_trash()
        burst_trash = next((item for item in trash_items if item["scope"] == SCOPE_SELECTIVE_BURSTS), None)
        self.assertIsNotNone(burst_trash)
        assert burst_trash is not None
        self.assertEqual(burst_trash["project_name"], "rollback_demo")
        details = burst_trash.get("details") or burst_trash.get("metadata") or {}
        self.assertIn(ev_id, details.get("burst_ids", []))

    def test_web_server_rollback_endpoint(self) -> None:
        ev_id = self.db.record_event(
            session_id=self.sid,
            event_type="EDIT",
            first_file="config.py",
            summary="Prompt Burst #2: Modified 1 file(s)",
        )

        handler = MagicMock(spec=_EngineRequestHandler)
        handler.server_instance = self.server

        sent_json = {}
        def mock_send_json(data: dict) -> None:
            nonlocal sent_json
            sent_json = data

        handler._send_json = mock_send_json

        _EngineRequestHandler._handle_api_rollback_burst(handler, "rollback_demo", ev_id)
        self.assertTrue(sent_json.get("success"))
        self.assertEqual(sent_json.get("event_id"), ev_id)


class TestSealBurstEndpoint(unittest.TestCase):
    """Test POST /api/project/<name>/seal-burst writing seal_burst.cmd."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_seal_test_"))
        self.engine_root = self.temp_dir / "engine"
        self.engine_root.mkdir(parents=True, exist_ok=True)
        self.target_dir = self.temp_dir / "target"
        self.target_dir.mkdir(parents=True, exist_ok=True)

        self.rotator = StorageRotator(self.engine_root)
        self.proj_dir = self.rotator.init_project("seal_demo", str(self.target_dir))
        self.server = EngineWebServer(engine_root=self.engine_root, port=0)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_handle_api_seal_burst_writes_command_file(self) -> None:
        handler = MagicMock(spec=_EngineRequestHandler)
        handler.server_instance = self.server

        sent_json = {}
        def mock_send_json(data: dict) -> None:
            nonlocal sent_json
            sent_json = data

        handler._send_json = mock_send_json

        _EngineRequestHandler._handle_api_seal_burst(handler, "seal_demo")

        self.assertTrue(sent_json.get("success"))
        self.assertEqual(sent_json.get("project"), "seal_demo")

        cmd_file = self.proj_dir / "seal_burst.cmd"
        # File should have been written (or already consumed)
        # In this mock test without worker active, it remains on disk
        self.assertTrue(cmd_file.exists())
        self.assertEqual(cmd_file.read_text(encoding="utf-8").strip(), "SEAL")


if __name__ == "__main__":
    unittest.main()
