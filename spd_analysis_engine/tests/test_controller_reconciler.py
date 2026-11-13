"""
tests/test_controller_reconciler.py
====================================
Tests for the authoritative controller reconciler and authenticated control channel.

Covers:
- Authenticated loopback control channel (protocol v1, token verification, commands)
- Stale controller state cannot archive replacement worker (UI restart + resume)
- Force kill transitions to INTERRUPTED without archiving
- Controller restart preserves state
- Worker survives UI exit
- Unknown unverifiable worker -> ORPHANED
- Stale heartbeat -> UNRESPONSIVE (never automatically kill!)
- Heartbeat recovery -> ACTIVE
- Valid unknown worker -> ADOPT
- Startup grace period protection
- Controller stop_project delegates to control channel
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from typing import Any

from spd_analysis_engine.scripts.controller.control_channel import (
    WorkerControlServer,
    send_worker_command,
    PROTOCOL_VERSION,
)
from spd_analysis_engine.scripts.controller.controller import LifecycleController
from spd_analysis_engine.scripts.controller.lifecycle import ProjectState
from spd_analysis_engine.scripts.controller.reconciler import Reconciler, ReconcilerDaemon
from spd_analysis_engine.scripts.controller.process import get_process_create_time, is_process_alive
from spd_analysis_engine.scripts.core.reaper import ReaperDaemon
from spd_analysis_engine.scripts.core.time_utils import utc_now_iso
from spd_analysis_engine.scripts.storage_rotator import StorageRotator


class TestControlChannel(unittest.TestCase):
    """Test suite for authenticated loopback control channel."""

    def setUp(self) -> None:
        self.test_dir = Path(tempfile.mkdtemp(prefix="spd_ctrl_test_"))
        self.secret = "test_secret_token_12345"

    def tearDown(self) -> None:
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_control_channel_ping_and_commands(self) -> None:
        """Verify loopback control channel binds ephemeral port and handles protocol commands."""
        received_commands: list[str] = []

        def dummy_handler(cmd: str, data: dict[str, Any]) -> tuple[bool, str, str | None]:
            received_commands.append(cmd)
            if cmd == "PING":
                return True, "ACTIVE", None
            if cmd == "STOP":
                return True, "STOPPING", None
            if cmd == "SEAL":
                return True, "ACTIVE", None
            return True, "ACTIVE", None

        server = WorkerControlServer(
            secret=self.secret,
            project_dir=self.test_dir,
            project_id="proj_alpha",
            worker_id="w_alpha_01",
            command_handler=dummy_handler,
            get_state_fn=lambda: "ACTIVE",
        )
        server.start()
        try:
            # 1. Verify worker.control.json was created and does not contain secret
            info_file = self.test_dir / "worker.control.json"
            self.assertTrue(info_file.exists())
            info_data = json.loads(info_file.read_text(encoding="utf-8"))
            self.assertEqual(info_data["host"], "127.0.0.1")
            self.assertEqual(info_data["port"], server.port)
            self.assertEqual(info_data["project_id"], "proj_alpha")
            self.assertNotIn("secret", info_data)

            # 2. Authenticated PING
            resp = send_worker_command(
                project_dir=self.test_dir,
                secret=self.secret,
                command="PING",
            )
            self.assertTrue(resp.get("ok"))
            self.assertEqual(resp.get("protocol"), PROTOCOL_VERSION)
            self.assertEqual(resp.get("state"), "ACTIVE")
            self.assertIn("PING", received_commands)

            # 3. Unauthorized request (wrong secret)
            unauth_resp = send_worker_command(
                project_dir=self.test_dir,
                secret="wrong_token",
                command="PING",
            )
            self.assertFalse(unauth_resp.get("ok"))
            self.assertIn("Unauthorized", unauth_resp.get("error", ""))

            # 4. Idempotent STOP
            stop_resp = send_worker_command(
                project_dir=self.test_dir,
                secret=self.secret,
                command="STOP",
            )
            self.assertTrue(stop_resp.get("ok"))
            self.assertEqual(stop_resp.get("state"), "STOPPING")

            stop_repeat = send_worker_command(
                project_dir=self.test_dir,
                secret=self.secret,
                command="STOP",
            )
            self.assertTrue(stop_repeat.get("ok"))

        finally:
            server.stop()
            self.assertFalse(info_file.exists())


class TestControllerReconciler(unittest.TestCase):
    """Test suite for authoritative reconciler, recovery, and replacement safety."""

    def setUp(self) -> None:
        self.test_dir = Path(tempfile.mkdtemp(prefix="spd_reconcile_test_"))
        self.engine_root = self.test_dir / "engine_root"
        self.engine_root.mkdir(parents=True, exist_ok=True)
        self.controller = LifecycleController(self.engine_root)
        self.reconciler = self.controller.reconciler

    def tearDown(self) -> None:
        if getattr(self, "controller", None) and hasattr(self.controller, "stop_reconciler_daemon"):
            self.controller.stop_reconciler_daemon()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _make_active(self, project_id: str) -> None:
        """Legally transition STOPPED -> STARTING -> INDEXING -> ACTIVE."""
        self.controller.registry.transition_project_state(project_id, ProjectState.STARTING.value)
        self.controller.registry.transition_project_state(project_id, ProjectState.INDEXING.value)
        self.controller.registry.transition_project_state(project_id, ProjectState.ACTIVE.value)

    def test_stale_controller_state_cannot_archive_replacement_worker(self) -> None:
        """
        Specifically reproduce:
        1. Start worker A (PID A)
        2. Restart UI / Controller
        3. Restart/resume project -> Replacement Worker B (PID B) runs
        4. Stale controller state (holding dead PID A) CANNOT archive replacement worker!
        """
        slug = "sample_proj"
        proj_dir = self.engine_root / "current" / slug
        proj_dir.mkdir(parents=True, exist_ok=True)
        (proj_dir / "session.db").write_text("dummy sqlite", encoding="utf-8")

        # 1. Register project and initial worker (Worker A, PID 99999991 - dead)
        dead_pid_a = 99999991
        proj = self.controller.register_project(display_name=slug, root_path=str(self.test_dir / slug))
        self._make_active(proj.project_id)
        self.controller.registry.record_worker(
            worker_id="w_worker_a",
            project_id=proj.project_id,
            pid=dead_pid_a,
            process_create_time=time.time() - 100.0,
            canonical_target=proj.root_path,
            engine_root=str(self.engine_root),
            state=ProjectState.ACTIVE.value,
            started_at=utc_now_iso(),
        )

        # 2. Restart UI: instantiate brand-new Controller instance from disk
        ui_restarted_controller = LifecycleController(self.engine_root)

        # 3. Replacement worker B (running under current PID, which is live!) is active on disk
        live_pid_b = os.getpid()
        live_ctime_b = get_process_create_time(live_pid_b)
        pid_file = proj_dir / "worker.pid"
        pid_file.write_text(json.dumps({
            "pid": live_pid_b,
            "worker_id": "w_worker_b_replacement",
            "process_create_time": live_ctime_b,
            "target_path": proj.root_path,
            "display_name": slug,
            "started_at": utc_now_iso(),
        }), encoding="utf-8")

        # 4. Run reconciler pass
        reconcile_results = ui_restarted_controller.reconcile()

        # Replacement worker B MUST be adopted and project MUST remain ACTIVE in current/
        self.assertIn(proj.project_id, reconcile_results["adopted"])
        fresh_proj = ui_restarted_controller.registry.get_project(proj.project_id)
        self.assertIsNotNone(fresh_proj)
        self.assertEqual(fresh_proj.state, ProjectState.ACTIVE.value)

        # current/sample_proj directory must NOT be archived!
        self.assertTrue(proj_dir.exists())
        self.assertFalse((self.engine_root / "last_run" / slug).exists())

        # Also test legacy ReaperDaemon with stale registry holding dead PID A
        rotator = StorageRotator(self.engine_root)
        class DummyEntry:
            pid = dead_pid_a
            exit_code = 1
            def is_alive(self): return False

        reaper = ReaperDaemon(registry={slug: DummyEntry()}, rotator=rotator)
        reaper.reap_dead_workers()

        # Verify replacement worker is STILL in current/ and NOT archived!
        self.assertTrue(proj_dir.exists())
        self.assertFalse((self.engine_root / "last_run" / slug).exists())

    def test_force_kill_transitions_to_interrupted_without_archiving(self) -> None:
        """When worker process exits unexpectedly, state becomes INTERRUPTED without deleting/archiving project."""
        slug = "crashed_proj"
        proj_dir = self.engine_root / "current" / slug
        proj_dir.mkdir(parents=True, exist_ok=True)
        (proj_dir / "session.db").write_text("session data", encoding="utf-8")

        proj = self.controller.register_project(display_name=slug, root_path=str(self.test_dir / slug))
        self._make_active(proj.project_id)
        # Worker PID 99999992 is dead and started 30s ago (outside startup grace period)
        self.controller.registry.record_worker(
            worker_id="w_crashed_01",
            project_id=proj.project_id,
            pid=99999992,
            process_create_time=time.time() - 60.0,
            canonical_target=proj.root_path,
            engine_root=str(self.engine_root),
            state=ProjectState.ACTIVE.value,
            started_at=utc_now_iso(),
        )
        self.reconciler.startup_grace_s = 0.0

        results = self.reconciler.reconcile_once()

        self.assertIn(proj.project_id, results["interrupted"])
        fresh = self.controller.registry.get_project(proj.project_id)
        self.assertEqual(fresh.state, ProjectState.INTERRUPTED.value)

        # Invariant: Project directory in current/ is NEVER archived solely because PID died
        self.assertTrue(proj_dir.exists())
        self.assertTrue((proj_dir / "session.db").exists())
        self.assertFalse((self.engine_root / "last_run" / slug).exists())

    def test_controller_restart_preserves_state(self) -> None:
        """Controller restart preserves metadata and states in engine.db."""
        p1 = self.controller.register_project("Project Alpha", str(self.test_dir / "p1"))
        self._make_active(p1.project_id)

        # Restart controller
        new_controller = LifecycleController(self.engine_root)
        recovered = new_controller.registry.get_project(p1.project_id)
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered.state, ProjectState.ACTIVE.value)
        self.assertEqual(recovered.display_name, "Project Alpha")

    def test_worker_survives_ui_exit(self) -> None:
        """A running worker process survives UI teardown and is recognized when UI restarts."""
        slug = "persistent_worker_proj"
        proj_dir = self.engine_root / "current" / slug
        proj_dir.mkdir(parents=True, exist_ok=True)

        live_pid = os.getpid()
        live_ctime = get_process_create_time(live_pid)
        proj = self.controller.register_project(display_name=slug, root_path=str(self.test_dir / slug))
        self._make_active(proj.project_id)
        self.controller.registry.record_worker(
            worker_id="w_persistent_01",
            project_id=proj.project_id,
            pid=live_pid,
            process_create_time=live_ctime,
            canonical_target=proj.root_path,
            engine_root=str(self.engine_root),
            state=ProjectState.ACTIVE.value,
            started_at=utc_now_iso(),
        )
        (proj_dir / "worker.pid").write_text(json.dumps({
            "pid": live_pid,
            "worker_id": "w_persistent_01",
            "process_create_time": live_ctime,
            "started_at": utc_now_iso(),
        }), encoding="utf-8")

        # Simulate UI exit by deleting controller instance
        del self.controller

        # Restart controller
        restarted_controller = LifecycleController(self.engine_root)
        restarted_controller.reconcile()

        fresh = restarted_controller.registry.get_project(proj.project_id)
        self.assertEqual(fresh.state, ProjectState.ACTIVE.value)
        self.assertTrue(proj_dir.exists())

    def test_orphan_process_unverifiable_metadata(self) -> None:
        """Corrupted/unverifiable PID file causes project to be marked ORPHANED."""
        slug = "corrupted_metadata_proj"
        proj_dir = self.engine_root / "current" / slug
        proj_dir.mkdir(parents=True, exist_ok=True)
        # Write unreadable corrupt content into worker.pid
        (proj_dir / "worker.pid").write_text("{invalid_json_corrupted_format!@#$", encoding="utf-8")

        proj = self.controller.register_project(display_name=slug, root_path=str(self.test_dir / slug))
        results = self.reconciler.reconcile_once()

        self.assertIn(proj.project_id, results["orphaned"])
        fresh = self.controller.registry.get_project(proj.project_id)
        self.assertEqual(fresh.state, ProjectState.ORPHANED.value)

    def test_stale_heartbeat_marks_unresponsive_never_kills(self) -> None:
        """Stale heartbeat transitions state to UNRESPONSIVE without killing worker process."""
        slug = "heartbeat_proj"
        live_pid = os.getpid()
        live_ctime = get_process_create_time(live_pid)

        proj = self.controller.register_project(display_name=slug, root_path=str(self.test_dir / slug))
        self._make_active(proj.project_id)

        # Worker started 60s ago with last heartbeat 30s ago
        from datetime import datetime, timezone, timedelta
        stale_time = (datetime.now(timezone.utc) - timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ")

        worker_rec = self.controller.registry.record_worker(
            worker_id="w_hb_01",
            project_id=proj.project_id,
            pid=live_pid,
            process_create_time=live_ctime,
            canonical_target=proj.root_path,
            engine_root=str(self.engine_root),
            state=ProjectState.ACTIVE.value,
            started_at=stale_time,
        )
        self.controller.registry.update_worker_heartbeat("w_hb_01", stale_time)

        self.reconciler.stale_heartbeat_s = 10.0
        self.reconciler.startup_grace_s = 5.0

        results = self.reconciler.reconcile_once()

        # Marked UNRESPONSIVE
        self.assertIn(proj.project_id, results["unresponsive"])
        fresh = self.controller.registry.get_project(proj.project_id)
        self.assertEqual(fresh.state, ProjectState.UNRESPONSIVE.value)

        # CRITICAL INVARIANT: Worker process is NOT killed!
        self.assertTrue(is_process_alive(live_pid, live_ctime))

        # Test Heartbeat Recovery: update heartbeat to fresh now
        fresh_time = utc_now_iso()
        self.controller.registry.update_worker_heartbeat("w_hb_01", fresh_time)
        self.reconciler.reconcile_once()

        recovered_proj = self.controller.registry.get_project(proj.project_id)
        self.assertEqual(recovered_proj.state, ProjectState.ACTIVE.value)

    def test_adoption_of_valid_unknown_worker(self) -> None:
        """Valid running worker found on disk is adopted into registry as ACTIVE."""
        slug = "adopted_test_proj"
        proj_dir = self.engine_root / "current" / slug
        proj_dir.mkdir(parents=True, exist_ok=True)

        live_pid = os.getpid()
        live_ctime = get_process_create_time(live_pid)
        (proj_dir / "worker.pid").write_text(json.dumps({
            "pid": live_pid,
            "worker_id": "w_adopted_external",
            "process_create_time": live_ctime,
            "target_path": str(self.test_dir / slug),
            "display_name": "Adopted Test Project",
            "started_at": utc_now_iso(),
        }), encoding="utf-8")

        results = self.reconciler.reconcile_once()
        self.assertTrue(len(results["adopted"]) > 0)

        proj = self.controller.resolve_project(slug)
        self.assertIsNotNone(proj)
        self.assertEqual(proj.state, ProjectState.ACTIVE.value)

        active_w = self.controller.registry.get_active_worker(proj.project_id)
        self.assertIsNotNone(active_w)
        self.assertEqual(active_w.pid, live_pid)
        self.assertEqual(active_w.worker_id, "w_adopted_external")

    def test_startup_grace_period_protection(self) -> None:
        """A worker started within startup grace period is never declared dead."""
        slug = "grace_period_proj"
        proj = self.controller.register_project(display_name=slug, root_path=str(self.test_dir / slug))
        self.controller.registry.transition_project_state(proj.project_id, ProjectState.STARTING.value)

        # Worker started 1 second ago (dead PID)
        self.controller.registry.record_worker(
            worker_id="w_grace_01",
            project_id=proj.project_id,
            pid=99999995,
            process_create_time=None,
            canonical_target=proj.root_path,
            engine_root=str(self.engine_root),
            state=ProjectState.STARTING.value,
            started_at=utc_now_iso(),
        )

        self.reconciler.startup_grace_s = 15.0  # 15s grace period

        results = self.reconciler.reconcile_once()

        # Not declared dead or interrupted
        self.assertNotIn(proj.project_id, results["interrupted"])
        fresh = self.controller.registry.get_project(proj.project_id)
        self.assertEqual(fresh.state, ProjectState.STARTING.value)

    def test_controller_stop_project_uses_control_channel(self) -> None:
        """controller.stop_project() sends STOP over loopback control channel."""
        import subprocess, sys
        slug = "stop_ctrl_proj"
        proj_dir = self.engine_root / "current" / slug
        proj_dir.mkdir(parents=True, exist_ok=True)

        commands_seen: list[str] = []
        secret = "secret_for_stop_123"

        # Spawn a dummy subprocess to represent the worker
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        dummy_pid = proc.pid
        dummy_ctime = get_process_create_time(dummy_pid)

        def cmd_handler(cmd: str, data: dict[str, Any]) -> tuple[bool, str, str | None]:
            commands_seen.append(cmd)
            if cmd == "STOP":
                try:
                    proc.terminate()
                except Exception:
                    pass
                return True, "STOPPING", None
            return True, "ACTIVE", None

        # Start loopback control server
        control_server = WorkerControlServer(
            secret=secret,
            project_dir=proj_dir,
            project_id="proj_stop",
            worker_id="w_stop_01",
            command_handler=cmd_handler,
            get_state_fn=lambda: "ACTIVE",
        )
        control_server.start()
        try:
            # Set secret in environment so controller client authenticates
            os.environ["SPD_WORKER_SECRET"] = secret

            proj = self.controller.register_project(display_name=slug, root_path=str(self.test_dir / slug))
            self._make_active(proj.project_id)
            self.controller.registry.record_worker(
                worker_id="w_stop_01",
                project_id=proj.project_id,
                pid=dummy_pid,
                process_create_time=dummy_ctime,
                canonical_target=proj.root_path,
                engine_root=str(self.engine_root),
                state=ProjectState.ACTIVE.value,
                started_at=utc_now_iso(),
            )

            # Stop project via controller
            stopped_proj = self.controller.stop_project(proj.project_id, timeout_s=3.0)
            self.assertEqual(stopped_proj.state, ProjectState.STOPPED.value)
            self.assertIn("STOP", commands_seen)
        finally:
            control_server.stop()
            os.environ.pop("SPD_WORKER_SECRET", None)
            try:
                proc.kill()
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()
