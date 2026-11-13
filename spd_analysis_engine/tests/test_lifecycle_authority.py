"""
tests/test_lifecycle_authority.py
=================================
Regression test suite verifying the single lifecycle authority invariants:
1. UI startup -> exactly one Supervisor
2. UI shutdown -> Supervisor/reconciler stops
3. Second UI invocation does not create another lifecycle authority
4. Worker remains owned by exactly one controller
5. Stale test/server instances cannot archive a live project
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

# Ensure engine package is importable
ENGINE_ROOT = Path(__file__).resolve().parent.parent
if str(ENGINE_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT.parent))
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from scripts.core.supervisor import Supervisor
from scripts.core.worker_entry import _WorkerEntry
from scripts.core.reaper import ReaperDaemon
from scripts.server.server_daemon import EngineWebServer
from scripts.storage.rotator import StorageRotator, _slug


class TestLifecycleAuthority(unittest.TestCase):
    """Test suite ensuring single lifecycle authority invariant across engine operations."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        (self.root / "current").mkdir(parents=True, exist_ok=True)
        (self.root / "last_run").mkdir(parents=True, exist_ok=True)
        (self.root / "history").mkdir(parents=True, exist_ok=True)
        (self.root / "trash").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        # Clean up any active supervisors for this temp root
        Supervisor.reset_instances()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_01_ui_startup_creates_exactly_one_supervisor_and_reaper(self) -> None:
        """UI startup must create exactly one authoritative Supervisor and exactly one reaper thread."""
        # 1. Authoritative supervisor constructed with enable_reaper=True (as start.py ui does)
        sup = Supervisor(self.root, enable_reaper=True)
        self.assertTrue(sup.is_reaper_running(), "Authoritative supervisor reaper thread must be running in UI mode")

        # 2. EngineWebServer receives sup via dependency injection
        server = EngineWebServer(engine_root=self.root, port=0, supervisor=sup)
        self.assertIs(server.supervisor, sup, "Server must use the injected authoritative supervisor")

        # 3. Check active threads: exactly one 'spd-reaper' thread must exist
        reaper_threads = [t for t in threading.enumerate() if t.name == "spd-reaper" and t.is_alive()]
        self.assertEqual(len(reaper_threads), 1, f"Expected exactly 1 reaper thread, found {len(reaper_threads)}")

        # Clean up
        server.shutdown()

    def test_02_ui_shutdown_stops_supervisor_and_reconciler(self) -> None:
        """UI shutdown path must stop the authoritative controller and its reaper daemon."""
        sup = Supervisor(self.root, enable_reaper=True)
        server = EngineWebServer(engine_root=self.root, port=0, supervisor=sup)
        server.start(background=True)

        self.assertTrue(sup.is_reaper_running())
        reaper_threads_before = [t for t in threading.enumerate() if t.name == "spd-reaper" and t.is_alive()]
        self.assertEqual(len(reaper_threads_before), 1)

        # Execute shutdown
        server.shutdown()

        # Reaper and supervisor must be stopped
        self.assertFalse(sup.is_reaper_running(), "Supervisor reaper must be stopped after server shutdown")
        reaper_threads_after = [t for t in threading.enumerate() if t.name == "spd-reaper" and t.is_alive()]
        self.assertEqual(len(reaper_threads_after), 0, "No spd-reaper threads should remain alive after shutdown")

    def test_03_second_ui_invocation_does_not_create_another_lifecycle_authority(self) -> None:
        """A second UI or server invocation must reuse the existing authoritative controller."""
        sup1 = Supervisor(self.root, enable_reaper=True)
        server1 = EngineWebServer(engine_root=self.root, port=0, supervisor=sup1)

        # Construct a second server instance without passing supervisor (e.g. concurrent handler or secondary caller)
        server2 = EngineWebServer(engine_root=self.root, port=0)

        # Both must point to the identical authoritative supervisor
        self.assertIs(server2.supervisor, sup1, "Second server must resolve to the existing authoritative supervisor")
        self.assertIs(server1.supervisor, server2.supervisor)

        # Reaper threads must NOT be duplicated
        reaper_threads = [t for t in threading.enumerate() if t.name == "spd-reaper" and t.is_alive()]
        self.assertEqual(len(reaper_threads), 1, "Second invocation must not spawn a secondary reaper thread")

        server1.shutdown()

    def test_04_worker_remains_owned_by_exactly_one_controller(self) -> None:
        """A tracked worker must be managed by the single authoritative controller without split registries."""
        sup = Supervisor(self.root, enable_reaper=False)
        slug = "owned_project"
        proj_dir = self.root / "current" / slug
        proj_dir.mkdir(parents=True, exist_ok=True)

        entry = _WorkerEntry(
            name=slug,
            target_path=str(self.root),
            pid=os.getpid(),
            log_path=proj_dir / "worker.log",
            started_at_iso="2026-09-30T00:00:00Z",
        )
        sup._registry[slug] = entry

        # Any secondary Supervisor lookup or server instance accesses the same registry
        server = EngineWebServer(engine_root=self.root, port=0)
        self.assertIn(slug, server.supervisor._registry, "Worker must be visible in server's supervisor registry")
        self.assertIs(server.supervisor._registry[slug], entry, "Worker entry identity must match exactly")

        # Mutating registry through one reference reflects in the other
        server.supervisor._registry.pop(slug)
        self.assertNotIn(slug, sup._registry, "Registry mutations must be coherent across all references")

    def test_05_stale_test_or_server_instances_cannot_archive_live_project(self) -> None:
        """A stale reaper or supervisor instance holding a dead PID must not archive a live replacement project."""
        slug = "live_replacement_proj"
        proj_dir = self.root / "current" / slug
        proj_dir.mkdir(parents=True, exist_ok=True)

        sup = Supervisor(self.root, enable_reaper=False)

        # Active replacement worker PID running on system (e.g. current test process)
        active_pid = os.getpid()
        (proj_dir / "worker.pid").write_text(f"{active_pid}\n2026-09-30T00:00:00Z", encoding="utf-8")

        # In-memory registry holds stale/dead PID
        stale_dead_entry = _WorkerEntry(
            name=slug,
            target_path=str(self.root),
            pid=99999999,
            log_path=proj_dir / "worker.log",
            started_at_iso="2026-09-01T00:00:00Z",
        )
        sup._registry[slug] = stale_dead_entry

        # Trigger reaper reconciliation pass
        reaped = sup.reap_dead_workers()

        # The project must NOT be archived because worker.pid on disk has a live PID
        self.assertNotIn(slug, reaped, "Reaper must not reap project with active replacement worker on disk")
        self.assertTrue((self.root / "current" / slug).exists(), "current/<slug> must remain intact")
        self.assertFalse((self.root / "last_run" / slug).exists(), "last_run/<slug> must not exist")


if __name__ == "__main__":
    unittest.main()
