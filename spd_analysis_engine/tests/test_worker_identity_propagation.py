"""
tests/test_worker_identity_propagation.py
=========================================
Comprehensive test suite validating worker identity preservation and propagation:
1. Spawned command contains --worker-id.
2. Worker receives exactly the generated UUID.
3. Supervisor accepts the correct worker.
4. Supervisor rejects a mismatched worker ID.
5. A stale PID with a different worker_id is not adopted.
6. Restart generates a new worker_id.
7. Worker status reports the correct worker_id.
8. 50 start/stop cycles leave no identity confusion.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.core.spawner import (
    SpawnResult,
    build_worker_cmd,
    spawn_detached_worker,
)
from scripts.core.supervisor import Supervisor, _WorkerEntry
from scripts.core.worker_helpers import (
    StopResult,
    discover_active_workers,
    validate_worker_process,
    verify_worker_pid,
    wait_for_worker_pid,
)
from scripts.core.worker_lifecycle import (
    WorkerState,
    _clean_shutdown,
    wire_worker,
    worker_state,
)
from scripts.project_worker import _build_arg_parser
from scripts.storage_rotator import _slug


class TestWorkerIdentityPropagation(unittest.TestCase):
    """Test suite proving strict worker identity propagation and verification."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_test_ident_")).resolve()
        self.engine_root = self.temp_dir / "engine_root"
        self.engine_root.mkdir(parents=True, exist_ok=True)
        self.workspace = self.temp_dir / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)

        for tier in ("current", "last_run", "history", "trash"):
            (self.engine_root / tier).mkdir(parents=True, exist_ok=True)

        worker_state._testing = True
        Supervisor.reset_instances()

    def tearDown(self) -> None:
        worker_state._testing = True
        Supervisor.reset_instances()
        _clean_shutdown(state=worker_state)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # 1. Spawned command contains --worker-id
    # -------------------------------------------------------------------------
    def test_01_spawned_command_contains_worker_id(self) -> None:
        """Spawned command must contain --worker-id and exact UUID."""
        custom_id = str(uuid.uuid4())
        dummy_script = self.temp_dir / "project_worker.py"
        dummy_script.touch()

        # 1a. Direct build_worker_cmd call
        cmd = build_worker_cmd(
            py_exe="python",
            worker_script=dummy_script,
            name="test_proj",
            path=self.workspace,
            engine_root=self.engine_root,
            worker_id=custom_id,
        )
        self.assertIn("--worker-id", cmd)
        idx = cmd.index("--worker-id")
        self.assertEqual(cmd[idx + 1], custom_id)

        # 1b. spawn_detached_worker with explicit worker_id
        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 4321
            mock_popen.return_value = mock_proc

            res = spawn_detached_worker(
                py_exe="python",
                worker_script=dummy_script,
                name="test_proj",
                path=self.workspace,
                engine_root=self.engine_root,
                log_path=self.engine_root / "current" / "test_proj" / "worker.log",
                worker_id=custom_id,
            )
            self.assertEqual(res.worker_id, custom_id)
            self.assertEqual(res.pid, 4321)

            popen_cmd = mock_popen.call_args[0][0]
            self.assertIn("--worker-id", popen_cmd)
            p_idx = popen_cmd.index("--worker-id")
            self.assertEqual(popen_cmd[p_idx + 1], custom_id)

        # 1c. spawn_detached_worker with worker_id=None generates valid UUID and propagates it
        with patch("subprocess.Popen") as mock_popen2:
            mock_proc2 = MagicMock()
            mock_proc2.pid = 4322
            mock_popen2.return_value = mock_proc2

            res2 = spawn_detached_worker(
                py_exe="python",
                worker_script=dummy_script,
                name="test_proj_2",
                path=self.workspace,
                engine_root=self.engine_root,
                log_path=self.engine_root / "current" / "test_proj_2" / "worker.log",
                worker_id=None,
            )
            self.assertTrue(bool(res2.worker_id))
            parsed_uuid = uuid.UUID(res2.worker_id)
            self.assertIsNotNone(parsed_uuid)

            popen_cmd2 = mock_popen2.call_args[0][0]
            self.assertIn("--worker-id", popen_cmd2)
            p2_idx = popen_cmd2.index("--worker-id")
            self.assertEqual(popen_cmd2[p2_idx + 1], res2.worker_id)

    # -------------------------------------------------------------------------
    # 2. Worker receives exactly the generated UUID
    # -------------------------------------------------------------------------
    def test_02_worker_receives_exactly_generated_uuid(self) -> None:
        """Worker must receive and persist the exact UUID without regenerating."""
        explicit_uuid = str(uuid.uuid4())
        test_state = WorkerState()
        test_state._testing = True

        parser = _build_arg_parser()
        args = parser.parse_args([
            "--name", "identity_proj",
            "--path", str(self.workspace),
            "--engine-root", str(self.engine_root),
            "--worker-id", explicit_uuid,
        ])

        self.assertEqual(args.worker_id, explicit_uuid)

        wire_worker(args, state=test_state)

        # Worker state adopts exact UUID
        self.assertEqual(test_state.worker_id, explicit_uuid)

        # worker.pid file contains exact UUID
        pid_file = self.engine_root / "current" / "identity_proj" / "worker.pid"
        self.assertTrue(pid_file.exists())
        pid_data = json.loads(pid_file.read_text(encoding="utf-8"))
        self.assertEqual(pid_data.get("worker_id"), explicit_uuid)

        # worker.status.json contains exact UUID
        status_file = self.engine_root / "current" / "identity_proj" / "worker.status.json"
        self.assertTrue(status_file.exists())
        status_data = json.loads(status_file.read_text(encoding="utf-8"))
        self.assertEqual(status_data.get("worker_id"), explicit_uuid)

        _clean_shutdown(state=test_state)

    # -------------------------------------------------------------------------
    # 3. Supervisor accepts the correct worker
    # -------------------------------------------------------------------------
    def test_03_supervisor_accepts_correct_worker(self) -> None:
        """Supervisor accepts and reconciles a worker with matching identity."""
        slug = "accepted_proj"
        proj_dir = self.engine_root / "current" / slug
        proj_dir.mkdir(parents=True, exist_ok=True)
        worker_uuid = str(uuid.uuid4())
        sim_pid = 7788

        pid_file = proj_dir / "worker.pid"
        pid_file.write_text(json.dumps({
            "pid": sim_pid,
            "worker_id": worker_uuid,
            "project": "Accepted Project",
            "slug": slug,
            "project_id": f"proj_{slug}",
            "target_path": str(self.workspace),
            "canonical_target": str(self.workspace),
            "canonical_project_root": str(self.workspace),
            "engine_root": str(self.engine_root),
            "process_create_time": 1000.0,
            "started_at": "2026-10-05T12:00:00Z",
            "state": "ACTIVE",
        }), encoding="utf-8")

        status_file = proj_dir / "worker.status.json"
        status_file.write_text(json.dumps({
            "pid": sim_pid,
            "worker_id": worker_uuid,
            "project": "Accepted Project",
            "session_id": 42,
            "state": "ACTIVE",
        }), encoding="utf-8")

        registry: dict[str, _WorkerEntry] = {}

        # Mock verify_worker_pid to return True (worker identity verified)
        with patch("scripts.core.worker_helpers.verify_worker_pid", return_value=True):
            discover_active_workers(self.engine_root, registry, _WorkerEntry)

        self.assertIn(slug, registry)
        entry = registry[slug]
        self.assertEqual(entry.pid, sim_pid)
        self.assertEqual(entry.worker_id, worker_uuid)
        self.assertEqual(entry.session_id, 42)

    # -------------------------------------------------------------------------
    # 4. Supervisor rejects a mismatched worker ID
    # -------------------------------------------------------------------------
    def test_04_supervisor_rejects_mismatched_worker_id(self) -> None:
        """Supervisor strictly rejects a process whose worker_id does not match."""
        expected_uuid = str(uuid.uuid4())
        actual_uuid = str(uuid.uuid4())
        sim_pid = 8899

        # Simulate process running with actual_uuid instead of expected_uuid
        fake_tokens = [
            "python", "project_worker.py",
            "--name", "mismatch_proj",
            "--path", str(self.workspace.resolve()),
            "--engine-root", str(self.engine_root.resolve()),
            "--worker-id", actual_uuid,
        ]

        mock_proc = MagicMock()
        mock_proc.is_running.return_value = True
        mock_proc.status.return_value = "running"
        mock_proc.create_time.return_value = 1000.0
        mock_proc.exe.return_value = "C:\\Python\\python.exe"
        mock_proc.cmdline.return_value = fake_tokens

        with patch("scripts.core.worker_helpers.psutil.pid_exists", return_value=True), \
             patch("scripts.core.worker_helpers.psutil.Process", return_value=mock_proc):

            # Validation against expected_uuid MUST FAIL
            is_valid = validate_worker_process(
                pid=sim_pid,
                name="mismatch_proj",
                expected_worker_id=expected_uuid,
                expected_project_root=self.workspace,
                expected_engine_root=self.engine_root,
            )
            self.assertFalse(is_valid)

            # When discover_active_workers runs with mismatched PID file, reject and mark stale
            slug = "mismatch_proj"
            proj_dir = self.engine_root / "current" / slug
            proj_dir.mkdir(parents=True, exist_ok=True)
            pid_file = proj_dir / "worker.pid"
            pid_file.write_text(json.dumps({
                "pid": sim_pid,
                "worker_id": expected_uuid,
                "project": slug,
                "canonical_project_root": str(self.workspace),
                "engine_root": str(self.engine_root),
            }), encoding="utf-8")

            registry: dict[str, _WorkerEntry] = {}
            discover_active_workers(self.engine_root, registry, _WorkerEntry)

            self.assertNotIn(slug, registry)
            self.assertFalse(pid_file.exists())
            self.assertTrue((proj_dir / "worker.pid.stale").exists())

    # -------------------------------------------------------------------------
    # 5. A stale PID with a different worker_id is not adopted
    # -------------------------------------------------------------------------
    def test_05_stale_pid_with_different_worker_id_not_adopted(self) -> None:
        """A stale PID on disk with a different worker_id is not adopted."""
        slug = "stale_proj"
        proj_dir = self.engine_root / "current" / slug
        proj_dir.mkdir(parents=True, exist_ok=True)
        old_uuid = str(uuid.uuid4())
        new_uuid = str(uuid.uuid4())

        # Write stale PID file
        pid_file = proj_dir / "worker.pid"
        pid_file.write_text(json.dumps({
            "pid": 99999,
            "worker_id": old_uuid,
            "project": slug,
        }), encoding="utf-8")

        # 5a. wait_for_worker_pid with expected_worker_id rejects old file
        found_pid = wait_for_worker_pid(
            pid_file=pid_file,
            proc=None,
            deadline_s=0.1,
            expected_worker_id=new_uuid,
        )
        self.assertEqual(found_pid, 0)

        # 5b. discover_active_workers marks it stale and does not adopt
        with patch("scripts.core.worker_helpers.psutil.pid_exists", return_value=False):
            registry: dict[str, _WorkerEntry] = {}
            discover_active_workers(self.engine_root, registry, _WorkerEntry)
            self.assertNotIn(slug, registry)
            self.assertFalse(pid_file.exists())
            self.assertTrue((proj_dir / "worker.pid.stale").exists())

    # -------------------------------------------------------------------------
    # 6. Restart generates a new worker_id
    # -------------------------------------------------------------------------
    def test_06_restart_generates_new_worker_id(self) -> None:
        """Restarting a project worker generates a distinct, valid new worker_id."""
        supervisor = Supervisor(self.engine_root)
        spawned_ids: list[str] = []

        def mock_spawn(**kwargs):
            wid = kwargs.get("worker_id")
            spawned_ids.append(wid)
            mock_proc = MagicMock()
            mock_proc.pid = 5000 + len(spawned_ids)
            mock_proc.poll.return_value = None

            # Simulate worker writing its pid file
            proj_dir = self.engine_root / "current" / _slug(kwargs.get("name"))
            proj_dir.mkdir(parents=True, exist_ok=True)
            (proj_dir / "worker.pid").write_text(json.dumps({
                "pid": mock_proc.pid,
                "worker_id": wid,
                "project": kwargs.get("name"),
            }), encoding="utf-8")

            return SpawnResult(mock_proc, mock_proc.pid, strategy="mock", worker_id=wid)

        mock_clean_stop = StopResult(exit_code=0, escalated=False, clean=True, method="mock_stop")
        with patch("scripts.core.supervisor.spawn_detached_worker", side_effect=mock_spawn), \
             patch("scripts.core.supervisor.stop_worker_process", return_value=mock_clean_stop):

            # Start 1
            res1 = supervisor.start_project("cycle_proj", str(self.workspace))
            wid1 = res1["worker_id"]

            # Stop 1
            supervisor.stop_project("cycle_proj")

            # Start 2
            res2 = supervisor.start_project("cycle_proj", str(self.workspace))
            wid2 = res2["worker_id"]

            self.assertNotEqual(wid1, wid2)
            self.assertEqual(len(spawned_ids), 2)
            self.assertEqual(spawned_ids[0], wid1)
            self.assertEqual(spawned_ids[1], wid2)
            self.assertIsNotNone(uuid.UUID(wid1))
            self.assertIsNotNone(uuid.UUID(wid2))

    # -------------------------------------------------------------------------
    # 7. Worker status reports the correct worker_id
    # -------------------------------------------------------------------------
    def test_07_worker_status_reports_correct_worker_id(self) -> None:
        """Worker status query reports the exact active worker_id."""
        supervisor = Supervisor(self.engine_root)
        target_uuid = str(uuid.uuid4())
        sim_pid = 6655
        slug = "status_proj"

        proj_dir = self.engine_root / "current" / slug
        proj_dir.mkdir(parents=True, exist_ok=True)

        (proj_dir / "worker.pid").write_text(json.dumps({
            "pid": sim_pid,
            "worker_id": target_uuid,
            "project": "Status Project",
            "slug": slug,
            "target_path": str(self.workspace),
            "canonical_target": str(self.workspace),
            "canonical_project_root": str(self.workspace),
            "engine_root": str(self.engine_root),
        }), encoding="utf-8")

        (proj_dir / "worker.status.json").write_text(json.dumps({
            "pid": sim_pid,
            "worker_id": target_uuid,
            "project": "Status Project",
            "session_id": 10,
            "state": "ACTIVE",
        }), encoding="utf-8")

        with patch("scripts.core.worker_helpers.verify_worker_pid", return_value=True):
            status_list = supervisor.get_status(name=slug)

        self.assertEqual(len(status_list), 1)
        row = status_list[0]
        self.assertEqual(row["worker_id"], target_uuid)
        self.assertEqual(row["pid"], sim_pid)
        self.assertEqual(row["slug"], slug)

    # -------------------------------------------------------------------------
    # 8. 50 start/stop cycles leave no identity confusion
    # -------------------------------------------------------------------------
    def test_08_50_start_stop_cycles_leave_no_identity_confusion(self) -> None:
        """50 sequential start/stop cycles must produce 50 unique IDs with zero bleed."""
        supervisor = Supervisor(self.engine_root)
        observed_ids: list[str] = []
        pids: list[int] = []

        def mock_spawn_cycle(**kwargs):
            wid = kwargs.get("worker_id")
            cycle_idx = len(observed_ids) + 1
            curr_pid = 10000 + cycle_idx

            mock_proc = MagicMock()
            mock_proc.pid = curr_pid
            mock_proc.poll.return_value = None

            proj_dir = self.engine_root / "current" / _slug(kwargs.get("name"))
            proj_dir.mkdir(parents=True, exist_ok=True)
            (proj_dir / "worker.pid").write_text(json.dumps({
                "pid": curr_pid,
                "worker_id": wid,
                "project": kwargs.get("name"),
                "slug": _slug(kwargs.get("name")),
                "canonical_target": str(self.workspace),
                "engine_root": str(self.engine_root),
            }), encoding="utf-8")

            return SpawnResult(mock_proc, curr_pid, strategy="cycle_mock", worker_id=wid)

        mock_clean_stop = StopResult(exit_code=0, escalated=False, clean=True, method="mock_stop")
        with patch("scripts.core.supervisor.spawn_detached_worker", side_effect=mock_spawn_cycle), \
             patch("scripts.core.supervisor.stop_worker_process", return_value=mock_clean_stop):

            for i in range(50):
                start_res = supervisor.start_project("cycle_stress", str(self.workspace))
                curr_wid = start_res["worker_id"]
                curr_pid = start_res["pid"]

                self.assertNotIn(curr_wid, observed_ids, f"Cycle {i} produced duplicate worker_id")
                observed_ids.append(curr_wid)
                pids.append(curr_pid)

                # Registry has active entry with exact ID
                slug = "cycle_stress"
                self.assertIn(slug, supervisor._registry)
                self.assertEqual(supervisor._registry[slug].worker_id, curr_wid)
                self.assertEqual(supervisor._registry[slug].pid, curr_pid)

                stop_res = supervisor.stop_project("cycle_stress")
                self.assertEqual(stop_res["status"], "stopped")
                self.assertNotIn(slug, supervisor._registry)

        # All 50 IDs must be strictly unique
        self.assertEqual(len(observed_ids), 50)
        self.assertEqual(len(set(observed_ids)), 50)
        self.assertEqual(len(supervisor._registry), 0)


if __name__ == "__main__":
    unittest.main()
