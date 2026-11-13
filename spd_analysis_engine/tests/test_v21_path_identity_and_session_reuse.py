"""
tests/test_v21_path_identity_and_session_reuse.py
=================================================
Unit & integration tests for:
  1. Path-Based Project Identity: Target path match overrides name to existing project slug.
  2. Empty Session Reuse: Reusing active session ID when last session has 0 events.
  3. Instant New-Project Baseline Ingestion (Burst #1): Only triggered when is_new_project is True.
  4. Sweeper on Supervisor Shutdown: Terminates background workers across all on-disk projects.
  5. Graceful Resume Error Handler: Responding with success when ValueError("already running") is raised.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.core.supervisor import Supervisor, _WorkerEntry
from scripts.storage.db import SessionDB
from scripts.storage.rotator import StorageRotator, _slug
from scripts.core.worker_bundle import trigger_initial_ingestion
from scripts.core.worker_lifecycle import WorkerState


class TestV21PathIdentityAndSessionReuse(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="spd_v21_test_"))
        self.engine_root = self.tmp / "engine"
        self.workspace = self.tmp / "workspace"
        for d in ("current", "last_run", "history", "trash"):
            (self.engine_root / d).mkdir(parents=True, exist_ok=True)
        self.workspace.mkdir(parents=True, exist_ok=True)

        (self.workspace / "app.py").write_text("print('hello')\n", encoding="utf-8")
        (self.workspace / "README.md").write_text("# Readme\n", encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_01_path_based_project_identity_override(self) -> None:
        """If a project is started with a target path already monitored, override the name."""
        rotator = StorageRotator(self.engine_root)
        orig_slug = "original_slug"
        proj_dir = rotator.init_project(orig_slug, str(self.workspace))

        sup = Supervisor(self.engine_root)

        with patch("scripts.core.supervisor.spawn_detached_worker") as mock_spawn:
            mock_proc = MagicMock()
            mock_proc.pid = 9911
            mock_spawn.return_value = (mock_proc, 9911)

            res = sup.start_project(name="Different_Name", path=str(self.workspace))

            # The project name should be overridden to original_slug
            self.assertEqual(res["project"], orig_slug)
            mock_spawn.assert_called_once()
            _, kwargs = mock_spawn.call_args
            self.assertEqual(kwargs.get("name"), orig_slug)
            self.assertFalse(kwargs.get("is_new_project"))

    def test_02_empty_session_reuse(self) -> None:
        """If the latest session has 0 events, reuse its session ID."""
        db_path = self.tmp / "test_session.db"
        sdb = SessionDB(db_path)

        # Create first session with 0 events
        sid1 = sdb.create_session("my_proj", str(self.workspace))
        self.assertEqual(sid1, 1)

        # Calling create_session again without recording events should reuse sid1
        sid2 = sdb.create_session("my_proj", str(self.workspace))
        self.assertEqual(sid2, sid1)

        # Now record an event in sid2
        sdb.record_event(session_id=sid2, event_type="EDIT", first_file="app.py", summary="Edit 1")

        # Now creating a new session must allocate a new session ID
        sid3 = sdb.create_session("my_proj", str(self.workspace))
        self.assertNotEqual(sid3, sid2)
        self.assertEqual(sid3, 2)
        sdb.close()

    def test_03_instant_baseline_ingestion_new_project(self) -> None:
        """trigger_initial_ingestion creates Burst #1 for tracked files in diff_calc."""
        from scripts.diff_calc import DiffCalculator
        from scripts.event_bundler import EventBundler

        baseline_dir = self.tmp / "baseline"
        baseline_dir.mkdir(parents=True, exist_ok=True)
        diff_calc = DiffCalculator(baseline_dir=baseline_dir, target_dir=self.workspace)
        diff_calc.capture_baseline(self.workspace)

        db_path = self.tmp / "ingest_session.db"
        sdb = SessionDB(db_path)
        sid = sdb.create_session("ingest_proj", str(self.workspace))

        bundler = EventBundler(quiet_period=10.0, is_initial_ingestion=True)
        state = WorkerState(
            session_db=sdb,
            session_id=sid,
            diff_calc=diff_calc,
            event_bundler=bundler,
            project_name="ingest_proj",
            target_path_str=str(self.workspace),
            engine_root_str=str(self.engine_root),
        )

        trigger_initial_ingestion(state=state)

        # Verify an event was recorded in SessionDB
        events = sdb.get_events(sid)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "EDIT")
        self.assertIn("Burst edit", events[0]["summary"])

        # Verify patches were recorded for the 2 files
        patches = sdb.get_patches(events[0]["id"])
        self.assertEqual(len(patches), 2)
        patch_files = {p["file_path"] for p in patches}
        self.assertIn("app.py", patch_files)
        self.assertIn("README.md", patch_files)

        sdb.close()
        bundler.stop()

    def test_04_supervisor_shutdown_sweeps_workers(self) -> None:
        """Supervisor.shutdown() invokes sweep_project_processes for all projects."""
        sup = Supervisor(self.engine_root)
        rotator = StorageRotator(self.engine_root)
        rotator.init_project("proj_alpha", str(self.workspace))
        rotator.init_project("proj_beta", str(self.workspace))

        with patch("scripts.core.supervisor.sweep_project_processes") as mock_sweep:
            sup.shutdown()
            # Must have swept proj_alpha and proj_beta
            swept_slugs = {call.args[0] for call in mock_sweep.call_args_list}
            self.assertIn("proj_alpha", swept_slugs)
            self.assertIn("proj_beta", swept_slugs)

    def test_05_routes_projects_resume_already_running(self) -> None:
        """Resume route handles ValueError already running gracefully."""
        from scripts.server.routes_projects_resume import ProjectResumeRoutesMixin

        class DummyHandler(ProjectResumeRoutesMixin):
            def __init__(self, engine_root: Path):
                self.server_instance = MagicMock()
                self.server_instance.engine_root = engine_root
                self.server_instance.supervisor = MagicMock()
                self.server_instance.supervisor.get_status.return_value = []
                self.server_instance.supervisor.start_project.side_effect = ValueError(
                    "A worker for project 'active_proj' is already running (PID 1234)."
                )
                self.json_sent = None
                self.status_sent = 200

            def _resolve_target_path(self, slug):
                return None

            def _send_json(self, payload, status=200):
                self.json_sent = payload
                self.status_sent = status

            def _send_error(self, msg, status=500):
                self.json_sent = {"error": msg}
                self.status_sent = status

        # Create meta for active_proj
        meta_dir = self.engine_root / "current" / "active_proj"
        meta_dir.mkdir(parents=True, exist_ok=True)
        (meta_dir / "project.meta").write_text(f"target_path={self.workspace}\n", encoding="utf-8")

        handler = DummyHandler(self.engine_root)
        handler._handle_api_resume_project("active_proj")

        self.assertIsNotNone(handler.json_sent)
        self.assertTrue(handler.json_sent.get("success"))
        self.assertIn("already running", handler.json_sent.get("message", "").lower())
        self.assertEqual(handler.status_sent, 200)


if __name__ == "__main__":
    unittest.main()
