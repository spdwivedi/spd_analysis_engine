"""
tests/test_identity_and_slug_resolution.py
============================================
Comprehensive tests for the SPD Analysis Engine project identity model:
  1. Project names with spaces and casing differences.
  2. Prevention of collision between similarly named projects (pulse, nova_pulse, pulse_dev).
  3. Slug prefix collision resistance in process sweeper and path resolvers.
  4. Backward compatibility with legacy status and PID files.
  5. Uniform presence of canonical identity fields across all storage tiers and API endpoints.
  6. Strict enforcement that display names are never used as internal lifecycle keys.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.core.process_sweeper import sweep_project_processes
from scripts.core.supervisor import Supervisor, _WorkerEntry
from scripts.core.worker_helpers import (
    discover_active_workers,
    format_worker_status_row,
    read_status_file,
)
from scripts.server.request_handler import _EngineRequestHandler
from scripts.server.server_daemon import EngineWebServer
from scripts.storage.rotator import StorageRotator
from scripts.storage.utils import _slug, resolve_project_identity


class TestProjectIdentityModel(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="spd_identity_test_"))
        self.engine_root = self.tmp / "engine"
        self.workspace = self.tmp / "workspace"
        for d in ("current", "last_run", "history", "trash"):
            (self.engine_root / d).mkdir(parents=True, exist_ok=True)
        self.workspace.mkdir(parents=True, exist_ok=True)
        Supervisor.reset_instances()

    def tearDown(self) -> None:
        Supervisor.reset_instances()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -------------------------------------------------------------------------
    # 1. Names with spaces and uppercase/lowercase slug normalization
    # -------------------------------------------------------------------------
    def test_01_names_with_spaces_and_casing(self) -> None:
        """Project names with spaces and mixed casing resolve to identical canonical slugs."""
        ident1 = resolve_project_identity("Nova Pulse", target_path=self.workspace)
        ident2 = resolve_project_identity("nova_pulse", target_path=self.workspace)
        ident3 = resolve_project_identity("  NOVA PULSE  ", target_path=self.workspace)

        self.assertEqual(ident1["slug"], "nova_pulse")
        self.assertEqual(ident2["slug"], "nova_pulse")
        self.assertEqual(ident3["slug"], "nova_pulse")

        self.assertEqual(ident1["display_name"], "Nova Pulse")
        self.assertEqual(ident3["display_name"], "NOVA PULSE")

        self.assertEqual(ident1["project_id"], "proj_nova_pulse")
        self.assertEqual(ident1["root_path"], str(self.workspace.resolve()))

        # Uppercase vs lowercase matching
        self.assertEqual(_slug("PULSE"), "pulse")
        self.assertEqual(_slug("pulse"), "pulse")
        self.assertEqual(_slug("Pulse"), "pulse")

    # -------------------------------------------------------------------------
    # 2. Similarly named projects (pulse, nova_pulse, pulse_dev) never confused
    # -------------------------------------------------------------------------
    def test_02_similarly_named_projects_never_confused(self) -> None:
        """pulse, nova_pulse, and pulse_dev run concurrently without key collisions or cross-talk."""
        sup = Supervisor(self.engine_root)

        path_pulse = self.tmp / "ws_pulse"
        path_nova = self.tmp / "ws_nova_pulse"
        path_dev = self.tmp / "ws_pulse_dev"
        for p in (path_pulse, path_nova, path_dev):
            p.mkdir(parents=True, exist_ok=True)

        entry_pulse = _WorkerEntry("Pulse", str(path_pulse), pid=1001)
        entry_nova = _WorkerEntry("Nova Pulse", str(path_nova), pid=1002)
        entry_dev = _WorkerEntry("Pulse Dev", str(path_dev), pid=1003)

        # Register in supervisor
        sup._registry[entry_pulse.slug] = entry_pulse
        sup._registry[entry_nova.slug] = entry_nova
        sup._registry[entry_dev.slug] = entry_dev

        # Registry should only contain exactly 3 keys (the 3 canonical slugs)
        self.assertEqual(set(sup._registry.keys()), {"pulse", "nova_pulse", "pulse_dev"})

        # get_status by name or slug returns exactly that project
        status_pulse = sup.get_status("pulse")
        self.assertEqual(len(status_pulse), 1)
        self.assertEqual(status_pulse[0]["slug"], "pulse")
        self.assertEqual(status_pulse[0]["pid"], 1001)

        status_nova = sup.get_status("Nova Pulse")
        self.assertEqual(len(status_nova), 1)
        self.assertEqual(status_nova[0]["slug"], "nova_pulse")
        self.assertEqual(status_nova[0]["pid"], 1002)

        status_dev = sup.get_status("pulse_dev")
        self.assertEqual(len(status_dev), 1)
        self.assertEqual(status_dev[0]["slug"], "pulse_dev")
        self.assertEqual(status_dev[0]["pid"], 1003)

        # Sweeper for 'pulse' must NOT match 'nova_pulse' or 'pulse_dev'
        mock_nova_proc = MagicMock()
        mock_nova_proc.info = {
            "pid": 1002,
            "name": "python.exe",
            "cmdline": ["python.exe", "scripts/project_worker.py", "--name", "nova_pulse", "--path", str(path_nova)],
        }
        mock_dev_proc = MagicMock()
        mock_dev_proc.info = {
            "pid": 1003,
            "name": "python.exe",
            "cmdline": ["python.exe", "scripts/project_worker.py", "--name", "pulse_dev", "--path", str(path_dev)],
        }
        with patch("psutil.process_iter", return_value=[mock_nova_proc, mock_dev_proc]):
            matched = sweep_project_processes(slug="pulse", target_path=str(path_pulse), force_kill=False)
            self.assertNotIn(1002, matched)
            self.assertNotIn(1003, matched)
            self.assertEqual(matched, [])

    # -------------------------------------------------------------------------
    # 3. Slug prefix collision resistance in path resolution
    # -------------------------------------------------------------------------
    def test_03_slug_prefix_collisions(self) -> None:
        """Resolver distinguishes between prefix matches like 'app' vs 'app_server'."""
        path_app = self.tmp / "ws_app"
        path_app_server = self.tmp / "ws_app_server"
        path_app.mkdir(parents=True, exist_ok=True)
        path_app_server.mkdir(parents=True, exist_ok=True)

        rotator = StorageRotator(self.engine_root)
        rotator.init_project("app", str(path_app))
        rotator.init_project("app_server", str(path_app_server))

        server = EngineWebServer(engine_root=self.engine_root, port=0)
        try:
            handler = _EngineRequestHandler.__new__(_EngineRequestHandler)
            handler.server = MagicMock()
            handler.server.engine_web_server = server

            res_app = handler._resolve_target_path("app")
            res_server = handler._resolve_target_path("app_server")

            self.assertEqual(res_app, path_app.resolve())
            self.assertEqual(res_server, path_app_server.resolve())
            self.assertNotEqual(res_app, res_server)
        finally:
            server.shutdown()

    # -------------------------------------------------------------------------
    # 4. Old status and PID data compatibility
    # -------------------------------------------------------------------------
    def test_04_old_status_data_compatibility(self) -> None:
        """Legacy status files with missing identity fields are correctly normalized."""
        slug = "legacy_proj"
        proj_dir = self.engine_root / "current" / slug
        proj_dir.mkdir(parents=True, exist_ok=True)

        # Legacy PID file (only had pid, project, target_path)
        pid_file = proj_dir / "worker.pid"
        pid_file.write_text(
            json.dumps({
                "pid": 5544,
                "project": "Legacy Display Project",
                "target_path": str(self.workspace),
            }),
            encoding="utf-8",
        )

        # Legacy status file (missing slug, project_id, state)
        status_file = proj_dir / "worker.status.json"
        status_file.write_text(
            json.dumps({
                "pid": 5544,
                "project": "Legacy Display Project",
                "session_id": 1,
            }),
            encoding="utf-8",
        )

        registry: dict[str, _WorkerEntry] = {}
        with patch("scripts.core.worker_helpers.verify_worker_pid", return_value=True):
            discover_active_workers(self.engine_root, registry, _WorkerEntry)

        # Registry must be keyed strictly by slug
        self.assertIn("legacy_proj", registry)
        self.assertNotIn("Legacy Display Project", registry)

        entry = registry["legacy_proj"]
        self.assertEqual(entry.slug, "legacy_proj")
        self.assertEqual(entry.display_name, "Legacy Display Project")
        self.assertEqual(entry.project_id, "proj_legacy_proj")
        self.assertEqual(entry.root_path, str(self.workspace.resolve()))

        # format_worker_status_row must contain all canonical identity fields
        status_data = read_status_file(proj_dir)
        row = format_worker_status_row(entry, status_data, self.engine_root, verified=True)

        self.assertEqual(row["project_id"], "proj_legacy_proj")
        self.assertEqual(row["slug"], "legacy_proj")
        self.assertEqual(row["project_slug"], "legacy_proj")
        self.assertEqual(row["display_name"], "Legacy Display Project")
        self.assertEqual(row["root_path"], str(self.workspace.resolve()))
        self.assertEqual(row["state"], "ACTIVE")

    # -------------------------------------------------------------------------
    # 5. Uniform identity fields across current, last_run, history tiers in API
    # -------------------------------------------------------------------------
    def test_05_current_last_run_history_compatibility_data(self) -> None:
        """GET /api/projects returns uniform identity fields across all tiers."""
        # 1. active in current/
        ws_curr = self.tmp / "ws_curr"
        ws_curr.mkdir(parents=True, exist_ok=True)
        (self.engine_root / "current" / "p_current").mkdir(parents=True, exist_ok=True)
        (self.engine_root / "current" / "p_current" / "project.meta").write_text(
            f"project_name=Project Current\ntarget_path={ws_curr}\nproject_id=proj_curr_123\n",
            encoding="utf-8",
        )

        # 2. inactive in last_run/
        ws_last = self.tmp / "ws_last"
        ws_last.mkdir(parents=True, exist_ok=True)
        (self.engine_root / "last_run" / "p_last").mkdir(parents=True, exist_ok=True)
        (self.engine_root / "last_run" / "p_last" / "project.meta").write_text(
            f"project_name=Project Last\ntarget_path={ws_last}\n",
            encoding="utf-8",
        )

        # 3. history only
        ws_hist = self.tmp / "ws_hist"
        ws_hist.mkdir(parents=True, exist_ok=True)
        hist_run = self.engine_root / "history" / "p_history" / "run_20260901_000000_123456"
        hist_run.mkdir(parents=True, exist_ok=True)
        (hist_run / "project.meta").write_text(
            f"project_name=Project History\ntarget_path={ws_hist}\n",
            encoding="utf-8",
        )

        server = EngineWebServer(engine_root=self.engine_root, port=0)
        try:
            handler = _EngineRequestHandler.__new__(_EngineRequestHandler)
            handler.server = MagicMock()
            handler.server.engine_web_server = server

            response_data: list[dict[str, Any]] = []
            handler._send_json = lambda d, **kw: response_data.append(d)

            # Mock live supervisor status for p_current
            mock_live = [{
                "project_id": "proj_curr_123",
                "slug": "p_current",
                "project_slug": "p_current",
                "display_name": "Project Current",
                "root_path": str(ws_curr.resolve()),
                "state": "ACTIVE",
                "project": "Project Current",
                "pid": 8877,
                "alive": True,
            }]
            with patch.object(server.supervisor, "get_status", return_value=mock_live):
                handler._handle_api_projects()

            self.assertEqual(len(response_data), 1)
            groups = response_data[0]

            for tier, items in groups.items():
                for item in items:
                    self.assertIn("project_id", item)
                    self.assertIn("slug", item)
                    self.assertIn("display_name", item)
                    self.assertIn("root_path", item)
                    self.assertIn("state", item)
                    self.assertTrue(item["slug"])
                    self.assertTrue(item["display_name"])
                    self.assertTrue(item["root_path"])
                    self.assertIn(item["state"], ("ACTIVE", "STOPPED", "INTERRUPTED"))

            # Verify tier placement
            curr_slugs = [x["slug"] for x in groups["current"]]
            last_slugs = [x["slug"] for x in groups["last_run"]]
            hist_slugs = [x["slug"] for x in groups["history"]]

            self.assertIn("p_current", curr_slugs)
            self.assertIn("p_last", last_slugs)
            self.assertIn("p_history", hist_slugs)
        finally:
            server.shutdown()

    # -------------------------------------------------------------------------
    # 6. Display names never used as lifecycle keys
    # -------------------------------------------------------------------------
    def test_06_display_name_never_used_as_lifecycle_key(self) -> None:
        """Display name with spaces/casing cannot be used as an internal dictionary key."""
        sup = Supervisor(self.engine_root)
        entry = _WorkerEntry("My Custom Application", str(self.workspace), pid=7711)
        sup._registry[entry.slug] = entry

        # 1. Key in _registry MUST be canonical slug, not display name
        self.assertIn("my_custom_application", sup._registry)
        self.assertNotIn("My Custom Application", sup._registry)

        # 2. Looking up or stopping by display name automatically converts to slug
        res = sup.get_status("My Custom Application")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["slug"], "my_custom_application")

        with patch("scripts.core.supervisor.stop_worker_process") as mock_stop, \
             patch("scripts.core.supervisor.sweep_project_processes"):
            mock_stop.return_value = 0
            stop_res = sup.stop_project("My Custom Application")
            self.assertEqual(stop_res["status"], "stopped")
            self.assertNotIn("my_custom_application", sup._registry)


if __name__ == "__main__":
    unittest.main()
