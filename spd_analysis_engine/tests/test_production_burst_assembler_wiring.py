"""
tests/test_production_burst_assembler_wiring.py
===============================================
Comprehensive test suite verifying production worker BurstAssembler wiring:
1. Production worker constructs BurstAssembler.
2. Production worker does not construct EventBundler.
3. No threading.Timer is created by the production worker.
4. 2,000 events within the quiet window become one burst.
5. Continuous activity eventually seals at max_burst_s.
6. Two separated activity windows create two bursts.
7. Hot-reloaded debounce is respected.
8. Events remain ordered.
9. Shutdown flushes pending events correctly.
10. Worker restart does not lose the persisted completed bursts.
"""
from __future__ import annotations

import argparse
import os
import queue
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from scripts.core.burst_assembler import BurstAssembler
from scripts.core.change_processor import ChangeProcessor
from scripts.core.worker_lifecycle import WorkerState, wire_worker, _clean_shutdown
from scripts.core.worker_bundle import _handle_event_bundle
from scripts.storage import SessionDB


class TestProductionBurstAssemblerWiring(unittest.TestCase):
    """Verifies that production worker is strictly wired to BurstAssembler with zero Timer objects."""

    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="spd_worker_wiring_test_"))
        self.engine_root = self.tmp_dir / "engine"
        self.target_dir = self.tmp_dir / "target_project"
        self.engine_root.mkdir(parents=True, exist_ok=True)
        self.target_dir.mkdir(parents=True, exist_ok=True)

        (self.target_dir / "main.py").write_text("print('hello')", encoding="utf-8")

        self.args = argparse.Namespace(
            name="test_proj",
            path=str(self.target_dir),
            engine_root=str(self.engine_root),
            scaffold=False,
            debounce=0.05,
            track_reads=False,
            track_exec=False,
            shadow_git=False,
            git_init_primary=False,
            ide_profile="antigravity",
            ide_custom_marker=None,
            is_new_project=False,
            worker_id="test-worker-1234",
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_production_worker_constructs_burst_assembler(self) -> None:
        """1. Verify that production worker constructs BurstAssembler."""
        state = WorkerState(_testing=True)
        try:
            wire_worker(self.args, state=state)
            self.assertIsNotNone(state.burst_assembler)
            self.assertIsInstance(state.burst_assembler, BurstAssembler)
            self.assertIsInstance(state.change_processor, ChangeProcessor)
        finally:
            _clean_shutdown(state=state)

    def test_02_production_worker_does_not_construct_event_bundler(self) -> None:
        """2. Verify that production worker does NOT construct EventBundler."""
        state = WorkerState(_testing=True)

        # Patch EventBundler.__init__ to raise an exception if it is ever instantiated
        with patch("scripts.event_bundler.EventBundler.__init__", side_effect=AssertionError("EventBundler was instantiated!")):
            wire_worker(self.args, state=state)

        try:
            # Confirm state.event_bundler alias points to BurstAssembler, not EventBundler
            self.assertIsInstance(state.event_bundler, BurstAssembler)
            self.assertEqual(type(state.event_bundler).__name__, "BurstAssembler")
        finally:
            _clean_shutdown(state=state)

    def test_03_no_threading_timer_created_by_production_worker(self) -> None:
        """3. Verify that zero threading.Timer instances are created by the production worker."""
        state = WorkerState(_testing=True)

        timer_created_count = 0

        original_timer_init = threading.Timer

        def spy_timer(*args: Any, **kwargs: Any) -> threading.Timer:
            nonlocal timer_created_count
            timer_created_count += 1
            return original_timer_init(*args, **kwargs)

        with patch("threading.Timer", side_effect=spy_timer):
            wire_worker(self.args, state=state)

            # Submit events directly to burst assembler
            state.burst_assembler.add("module_a.py", event_type="modified")
            state.burst_assembler.add("module_b.py", event_type="modified")

            time.sleep(0.08)

            _clean_shutdown(state=state)

        self.assertEqual(
            timer_created_count,
            0,
            f"Expected 0 threading.Timer objects created by production worker, found {timer_created_count}",
        )

    def test_04_2000_events_within_quiet_window_become_one_burst(self) -> None:
        """4. Verify that 2,000 events within quiet window form exactly one burst."""
        assembler = BurstAssembler(
            quiet_period=0.08,
            max_burst_s=5.0,
            max_queue_size=25000,
        )
        assembler.start()

        try:
            for i in range(2000):
                assembler.add(f"file_{i}.txt", event_type="modified")

            self.assertTrue(assembler.is_pending)
            self.assertEqual(assembler.pending_count, 2000)

            # Wait for quiet period to seal bundle
            bundle = assembler.downstream_queue.get(timeout=2.0)
            self.assertIsNotNone(bundle)
            self.assertEqual(len(bundle["files_touched"]), 2000)
            self.assertEqual(bundle["first_file"], "file_0.txt")
            self.assertFalse(assembler.is_pending)

            # Ensure downstream queue has no other duplicate bundles
            self.assertTrue(assembler.downstream_queue.empty())
        finally:
            assembler.stop(drain=False)

    def test_05_continuous_activity_eventually_seals_at_max_burst(self) -> None:
        """5. Verify that continuous activity seals at max_burst_s without quiet period."""
        assembler = BurstAssembler(
            quiet_period=0.08,  # 80ms quiet period
            max_burst_s=0.20,   # 200ms max burst ceiling
        )
        assembler.start()

        try:
            t0 = time.monotonic()
            # Feed an event every 20ms for 320ms: never reaches 80ms quiet period
            for i in range(16):
                assembler.add(f"cont_{i}.txt")
                time.sleep(0.02)

            # Must seal first bundle at ~200ms
            bundle1 = assembler.downstream_queue.get(timeout=1.0)
            elapsed = time.monotonic() - t0

            self.assertGreaterEqual(elapsed, 0.18)
            self.assertLess(elapsed, 0.35)
            self.assertGreater(len(bundle1["files_touched"]), 0)

            # Remaining events seal in a second bundle once feeding stops
            bundle2 = assembler.downstream_queue.get(timeout=1.0)
            self.assertGreater(len(bundle2["files_touched"]), 0)
        finally:
            assembler.stop(drain=False)

    def test_06_two_separated_activity_windows_create_two_bursts(self) -> None:
        """6. Verify that two separated activity windows create two independent bursts."""
        assembler = BurstAssembler(
            quiet_period=0.05,
            max_burst_s=2.0,
        )
        assembler.start()

        try:
            # Window 1
            assembler.add("window1_a.py")
            assembler.add("window1_b.py")

            bundle1 = assembler.downstream_queue.get(timeout=1.0)
            self.assertEqual(set(bundle1["files_touched"]), {"window1_a.py", "window1_b.py"})

            # Separation gap > quiet period
            time.sleep(0.06)

            # Window 2
            assembler.add("window2_c.py")

            bundle2 = assembler.downstream_queue.get(timeout=1.0)
            self.assertEqual(set(bundle2["files_touched"]), {"window2_c.py"})

            self.assertTrue(assembler.downstream_queue.empty())
        finally:
            assembler.stop(drain=False)

    def test_07_hot_reloaded_debounce_is_respected(self) -> None:
        """7. Verify that hot-reloaded debounce quiet_period takes immediate effect."""
        assembler = BurstAssembler(
            quiet_period=10.0,  # Initially huge quiet period
            max_burst_s=30.0,
        )
        assembler.start()

        try:
            assembler.add("hot_reload.py")

            # At t=0.02s, hot-reload debounce to 0.04s
            time.sleep(0.02)
            t_reload = time.monotonic()
            assembler.quiet_period = 0.04

            bundle = assembler.downstream_queue.get(timeout=1.0)
            elapsed = time.monotonic() - t_reload

            # Should seal in ~0.04s-0.10s, NOT in 10.0 seconds
            self.assertLess(elapsed, 0.25)
            self.assertEqual(bundle["files_touched"], ["hot_reload.py"])
        finally:
            assembler.stop(drain=False)

    def test_08_events_remain_ordered(self) -> None:
        """8. Verify that submitted file events preserve exact FIFO sequence order."""
        assembler = BurstAssembler(
            quiet_period=0.06,
            max_burst_s=5.0,
        )
        assembler.start()

        expected_order = [f"sequence_file_{i:03d}.py" for i in range(100)]

        try:
            for f in expected_order:
                assembler.add(f)

            bundle = assembler.downstream_queue.get(timeout=2.0)
            self.assertEqual(bundle["files_touched"], expected_order)
            self.assertEqual(bundle["first_file"], expected_order[0])
        finally:
            assembler.stop(drain=False)

    def test_09_shutdown_flushes_pending_events_correctly(self) -> None:
        """9. Verify that shutdown flushes pending events cleanly downstream."""
        assembler = BurstAssembler(
            quiet_period=10.0,  # Long quiet period
            max_burst_s=60.0,
        )
        assembler.start()

        assembler.add("pending_file_1.txt")
        assembler.add("pending_file_2.txt")
        self.assertTrue(assembler.is_pending)

        # Stop with drain=True flushes immediately
        assembler.stop(drain=True)

        self.assertFalse(assembler.is_pending)
        bundle = assembler.downstream_queue.get_nowait()
        self.assertIsNotNone(bundle)
        self.assertEqual(set(bundle["files_touched"]), {"pending_file_1.txt", "pending_file_2.txt"})

    def test_10_worker_restart_does_not_lose_persisted_completed_bursts(self) -> None:
        """10. Verify that worker restart preserves completed bursts in SQLite SessionDB."""
        state1 = WorkerState(_testing=True)
        wire_worker(self.args, state=state1)

        # Simulate first burst edit
        bundle1 = {
            "event_type": "EDIT",
            "files_touched": ["main.py"],
            "first_file": "main.py",
            "duration_s": 0.05,
            "events": [{"file_path": "main.py", "event_type": "modified", "seq": 1}],
        }
        _handle_event_bundle(bundle1, state=state1)

        # Verify event recorded in DB
        cur1 = state1.session_db.conn.cursor()
        cur1.execute("SELECT COUNT(*) FROM events WHERE event_type = 'EDIT'")
        count1 = cur1.fetchone()[0]
        self.assertEqual(count1, 1)

        # Clean shutdown worker 1
        _clean_shutdown(state=state1)

        # Worker restart (run 2)
        state2 = WorkerState(_testing=True)
        wire_worker(self.args, state=state2)

        # Verify previous event is still present in the persisted DB
        cur2 = state2.session_db.conn.cursor()
        cur2.execute("SELECT COUNT(*) FROM events WHERE event_type = 'EDIT'")
        count_persisted = cur2.fetchone()[0]
        self.assertEqual(count_persisted, 1)

        # Record a second burst
        bundle2 = {
            "event_type": "EDIT",
            "files_touched": ["second.py"],
            "first_file": "second.py",
            "duration_s": 0.05,
            "events": [{"file_path": "second.py", "event_type": "modified", "seq": 2}],
        }
        _handle_event_bundle(bundle2, state=state2)

        cur2.execute("SELECT COUNT(*) FROM events WHERE event_type = 'EDIT'")
        count2 = cur2.fetchone()[0]
        self.assertEqual(count2, 2)

        _clean_shutdown(state=state2)


if __name__ == "__main__":
    unittest.main()
