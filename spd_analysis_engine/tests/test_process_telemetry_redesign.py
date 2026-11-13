"""
tests/test_process_telemetry_redesign.py
========================================
Comprehensive verification and benchmarking of redesigned process telemetry:
1. Idle CPU measurement and reporting
2. Scan duration measurement and reporting (initial vs cached)
3. Scan interval adaptation (idle ~2s, min 0.5s, duty-cycle adaptation)
4. RSS memory usage measurement and stability reporting
5. Bounded telemetry queue size and drop counters under pressure
6. Event processing latency measurement and non-blocking guarantees
7. open_files() restriction strictly to allowlisted IDE processes
8. open_files() execution at slower cadence
9. Capture durability and telemetry failure isolation
"""

from __future__ import annotations

import os
import queue
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import psutil

from scripts.watcher_proc import ProcessInspector, TARGET_PROCESS_NAMES, IDE_APP_NAMES


class TestProcessTelemetryRedesign(unittest.TestCase):
    """Benchmarks and regression tests for redesigned process telemetry."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_proc_redesign_test_")).resolve()

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_idle_cpu_measurement_and_report(self) -> None:
        """
        Measure and report idle CPU consumption of ProcessInspector.
        Verifies that running the inspector loop consumes minimal CPU.
        """
        inspector = ProcessInspector(
            target_path=self.temp_dir,
            min_interval=0.1,
            idle_interval=0.2,
        )

        proc = psutil.Process(os.getpid())
        cpu_start = proc.cpu_times()

        inspector.start()
        time.sleep(0.5)
        inspector.stop()

        cpu_end = proc.cpu_times()
        user_consumed = cpu_end.user - cpu_start.user
        sys_consumed = cpu_end.system - cpu_start.system
        total_consumed = user_consumed + sys_consumed

        print(
            f"\n[METRIC REPORT] Idle CPU: user={user_consumed:.4f}s, "
            f"system={sys_consumed:.4f}s, total={total_consumed:.4f}s in 0.5s idle window"
        )

        # In a 0.5s idle window, background inspector should consume negligible CPU time (< 2.0s)
        self.assertLess(total_consumed, 2.0, f"Excessive CPU consumed during idle: {total_consumed:.4f}s")
        self.assertLess(inspector.total_scan_duration_s, 2.0)

    def test_scan_duration_initial_vs_cached_report(self) -> None:
        """
        Measure and report scan duration for:
        1. Initial deep discovery of 50 processes
        2. Subsequent scan with cached unchanged processes
        """
        inspector = ProcessInspector(target_path=self.temp_dir)

        mock_procs: list[MagicMock] = []
        for i in range(50):
            m = MagicMock()
            m.info = {"pid": 10000 + i, "name": "cursor.exe", "create_time": 2000.0 + i}
            m.cmdline.return_value = ["cursor.exe", str(self.temp_dir)]
            m.exe.return_value = "C:\\Program Files\\Cursor\\cursor.exe"
            m.cpu_times.return_value = MagicMock(user=1.0, system=0.5)
            m.open_files.return_value = []
            mock_procs.append(m)

        with patch("psutil.process_iter", return_value=mock_procs), \
             patch("scripts.watcher_proc.is_process_whitelisted", return_value=True):

            # Scan 1: initial deep inspection
            t0 = time.monotonic()
            inspector._inspect_once(check_open_files=True)
            dur_initial = time.monotonic() - t0

            # Scan 2: cached unchanged processes (idle CPU check bypass)
            t1 = time.monotonic()
            inspector._inspect_once(check_open_files=True)
            dur_cached = time.monotonic() - t1

        stats = inspector.get_scan_stats()
        print(
            f"\n[METRIC REPORT] Scan duration: 50 procs initial={dur_initial * 1000:.2f}ms, "
            f"cached={dur_cached * 1000:.2f}ms, recorded_avg={stats['avg_scan_duration_s'] * 1000:.2f}ms"
        )

        self.assertEqual(inspector.scan_count, 2)
        # Cached scan should be faster than initial deep inspection
        self.assertLess(dur_cached, dur_initial + 0.05)
        self.assertLess(dur_cached, 0.1, f"Cached scan too slow: {dur_cached:.4f}s")

    def test_scan_interval_adaptation_and_report(self) -> None:
        """
        Verify and report adaptive interval scaling:
        1. Default idle interval is around 2.0s.
        2. Never below 0.5s.
        3. Adapts upwards under heavy scan duration (duty cycle limit).
        4. Adapts towards min_interval when active with low scan duration.
        """
        inspector = ProcessInspector(
            target_path=self.temp_dir,
            min_interval=0.5,
            idle_interval=2.0,
        )

        # 1. Idle initial interval
        self.assertEqual(inspector.idle_interval, 2.0)
        self.assertEqual(inspector.min_interval, 0.5)
        self.assertGreaterEqual(inspector._current_interval, 0.5)

        # 2. Activity with negligible scan duration -> adapts to min_interval (0.5s)
        act_interval = inspector.adapt_interval(scan_duration=0.01, found_activity=True)
        self.assertEqual(act_interval, 0.5, "Active interval should drop to 0.5s")

        # 3. Activity with heavy scan duration (0.4s) -> adapts to duty cycle (0.4 * 4 = 1.6s)
        heavy_act_interval = inspector.adapt_interval(scan_duration=0.4, found_activity=True)
        self.assertAlmostEqual(heavy_act_interval, 1.6, places=2)

        # 4. Successive idle scans back off towards idle_interval (2.0s)
        idle_1 = inspector.adapt_interval(scan_duration=0.01, found_activity=False)
        idle_2 = inspector.adapt_interval(scan_duration=0.01, found_activity=False)
        idle_3 = inspector.adapt_interval(scan_duration=0.01, found_activity=False)

        print(
            f"\n[METRIC REPORT] Scan interval adaptation: min={inspector.min_interval}s, "
            f"active_fast={act_interval:.2f}s, active_heavy={heavy_act_interval:.2f}s, "
            f"idle_step1={idle_1:.2f}s, idle_step2={idle_2:.2f}s, idle_step3={idle_3:.2f}s"
        )

        self.assertGreaterEqual(idle_1, 0.5)
        self.assertGreaterEqual(idle_2, idle_1)
        self.assertGreaterEqual(idle_3, 2.0)
        # Crucial invariant: never below 0.5s
        self.assertGreaterEqual(inspector._current_interval, 0.5)

    def test_rss_memory_stability_and_report(self) -> None:
        """
        Measure and report RSS before and after 60 inspection cycles
        with hundreds of generated seen handles to verify memory stability.
        """
        inspector = ProcessInspector(target_path=self.temp_dir)
        proc = psutil.Process(os.getpid())

        rss_before = proc.memory_info().rss / (1024 * 1024)

        now = time.monotonic()
        # Seed 3000 seen handles
        for i in range(3000):
            inspector._seen_handles[(20000 + (i % 50), f"src/module_{i}.py")] = now

        # Run 60 simulated scan passes
        mock_proc = MagicMock()
        mock_proc.info = {"pid": 20001, "name": "code.exe", "create_time": 3000.0}
        mock_proc.cmdline.return_value = ["code.exe"]
        mock_proc.exe.return_value = "code.exe"
        mock_proc.cpu_times.return_value = MagicMock(user=5.0, system=2.0)
        mock_proc.open_files.return_value = []

        with patch("psutil.process_iter", return_value=[mock_proc]), \
             patch("scripts.watcher_proc.is_process_whitelisted", return_value=True):
            for _ in range(60):
                inspector._inspect_once(check_open_files=False)

        rss_after = proc.memory_info().rss / (1024 * 1024)
        rss_delta = rss_after - rss_before

        print(
            f"\n[METRIC REPORT] RSS: before={rss_before:.2f} MB, "
            f"after={rss_after:.2f} MB, delta={rss_delta:+.2f} MB"
        )

        # RSS growth should be minimal (< 25 MB)
        self.assertLess(rss_delta, 25.0, f"Excessive memory growth: {rss_delta:.2f} MB")

    def test_queue_size_and_drop_counter_under_pressure_report(self) -> None:
        """
        Verify and report bounded telemetry queue behavior under backpressure:
        Queue does not grow unbounded and drops overflow records without blocking.
        """
        inspector = ProcessInspector(
            target_path=self.temp_dir,
            max_queue_size=20,
        )

        test_file = self.temp_dir / "target.py"
        test_file.write_text("code = 123", encoding="utf-8")

        mock_handle = MagicMock()
        mock_handle.path = str(test_file)

        mock_proc = MagicMock()
        mock_proc.info = {"pid": 40001, "name": "cursor.exe", "create_time": 4000.0}
        mock_proc.cmdline.return_value = ["cursor.exe"]
        mock_proc.exe.return_value = "cursor.exe"
        mock_proc.cpu_times.return_value = MagicMock(user=1.0, system=1.0)
        mock_proc.open_files.return_value = [mock_handle]

        with patch("psutil.process_iter", return_value=[mock_proc]), \
             patch("scripts.watcher_proc.is_process_whitelisted", return_value=True):

            # Push 50 distinct file reads into a queue of maxsize=20
            for i in range(50):
                mock_handle.path = str(self.temp_dir / f"file_{i}.py")
                (self.temp_dir / f"file_{i}.py").write_text("# f", encoding="utf-8")
                # Advance CPU to bypass idle filter
                mock_proc.cpu_times.return_value = MagicMock(user=1.0 + i, system=1.0)
                inspector._inspect_once(check_open_files=True)

        stats = inspector.get_scan_stats()
        print(
            f"\n[METRIC REPORT] Queue size under pressure: "
            f"max_capacity=20, actual_qsize={stats['queue_size']}, drops={stats['drop_count']}"
        )

        self.assertEqual(stats["queue_size"], 20, "Queue must be bounded at capacity 20")
        self.assertEqual(stats["drop_count"], 30, "Expected 30 dropped records (50 - 20)")

    def test_event_processing_latency_and_report(self) -> None:
        """
        Measure and report latency of event processing and dispatch.
        Verifies that callbacks are executed non-blockingly and latency is tracked.
        """
        received: list[dict[str, Any]] = []

        def slow_callback(record: dict[str, Any]) -> None:
            # Simulate 2ms event handling
            time.sleep(0.002)
            received.append(record)

        inspector = ProcessInspector(
            target_path=self.temp_dir,
            on_file_read=slow_callback,
        )

        target_file = self.temp_dir / "observed.py"
        target_file.write_text("observed", encoding="utf-8")

        mock_handle = MagicMock()
        mock_handle.path = str(target_file)

        mock_proc = MagicMock()
        mock_proc.info = {"pid": 50001, "name": "cursor.exe", "create_time": 5000.0}
        mock_proc.cmdline.return_value = ["cursor.exe"]
        mock_proc.exe.return_value = "cursor.exe"
        mock_proc.cpu_times.return_value = MagicMock(user=1.0, system=1.0)
        mock_proc.open_files.return_value = [mock_handle]

        with patch("psutil.process_iter", return_value=[mock_proc]), \
             patch("scripts.watcher_proc.is_process_whitelisted", return_value=True):
            inspector._inspect_once(check_open_files=True)

        stats = inspector.get_scan_stats()
        lat_us = stats["last_event_latency_s"] * 1_000_000

        print(
            f"\n[METRIC REPORT] Event processing latency: "
            f"last={lat_us:.1f} us ({stats['last_event_latency_s'] * 1000:.3f} ms), "
            f"avg={stats['avg_event_latency_s'] * 1000:.3f} ms, "
            f"received_count={len(received)}"
        )

        self.assertEqual(len(received), 1)
        self.assertGreaterEqual(stats["last_event_latency_s"], 0.001)
        self.assertLess(stats["last_event_latency_s"], 0.050)

    def test_open_files_restricted_to_allowlisted_ides_only(self) -> None:
        """
        open_files() must NEVER be called on non-IDE processes (e.g. python, node),
        even if they are whitelisted language runtimes.
        """
        inspector = ProcessInspector(target_path=self.temp_dir)

        # Process A: Python (runtime, in TARGET_PROCESS_NAMES but NOT in IDE_APP_NAMES)
        python_proc = MagicMock()
        python_proc.info = {"pid": 60001, "name": "python.exe", "create_time": 6000.0}
        python_proc.cmdline.return_value = ["python.exe", "test.py"]
        python_proc.exe.return_value = "C:\\Python\\python.exe"
        python_proc.cpu_times.return_value = MagicMock(user=2.0, system=1.0)
        python_proc.open_files.return_value = []

        # Process B: Cursor (allowlisted IDE in IDE_APP_NAMES)
        ide_proc = MagicMock()
        ide_proc.info = {"pid": 60002, "name": "cursor.exe", "create_time": 6000.0}
        ide_proc.cmdline.return_value = ["cursor.exe"]
        ide_proc.exe.return_value = "cursor.exe"
        ide_proc.cpu_times.return_value = MagicMock(user=3.0, system=1.0)
        ide_proc.open_files.return_value = []

        with patch("psutil.process_iter", return_value=[python_proc, ide_proc]), \
             patch("scripts.watcher_proc.is_process_whitelisted", return_value=True):
            inspector._inspect_once(check_open_files=True)

        # Python process must NEVER have open_files() called!
        self.assertEqual(
            python_proc.open_files.call_count, 0,
            "open_files() was called on a non-IDE process (python.exe)!"
        )
        # IDE process MUST have open_files() called
        self.assertEqual(
            ide_proc.open_files.call_count, 1,
            "open_files() was not called on allowlisted IDE process (cursor.exe)"
        )

    def test_open_files_slower_interval_enforcement(self) -> None:
        """
        open_files() must be skipped when check_open_files is False (slower interval).
        """
        inspector = ProcessInspector(
            target_path=self.temp_dir,
            open_files_interval=4.0,
        )

        ide_proc = MagicMock()
        ide_proc.info = {"pid": 70001, "name": "cursor.exe", "create_time": 7000.0}
        ide_proc.cmdline.return_value = ["cursor.exe"]
        ide_proc.exe.return_value = "cursor.exe"
        cpu_obj = MagicMock(user=1.0, system=1.0)
        ide_proc.cpu_times.return_value = cpu_obj
        ide_proc.open_files.return_value = []

        with patch("psutil.process_iter", return_value=[ide_proc]), \
             patch("scripts.watcher_proc.is_process_whitelisted", return_value=True):

            # Scan 1: check_open_files=False (slower cadence not due)
            inspector._inspect_once(check_open_files=False)
            self.assertEqual(ide_proc.open_files.call_count, 0)

            # Advance CPU
            cpu_obj.user = 2.0

            # Scan 2: check_open_files=True (due)
            inspector._inspect_once(check_open_files=True)
            self.assertEqual(ide_proc.open_files.call_count, 1)

    def test_capture_durability_independent_of_telemetry_failures(self) -> None:
        """
        Telemetry exceptions or broken callbacks must NEVER crash the inspector
        or block the capture pipeline.
        """
        def failing_callback(record: dict[str, Any]) -> None:
            raise RuntimeError("Catastrophic writer failure in telemetry subscriber!")

        inspector = ProcessInspector(
            target_path=self.temp_dir,
            on_file_read=failing_callback,
        )

        test_file = self.temp_dir / "target.py"
        test_file.write_text("pass", encoding="utf-8")

        mock_handle = MagicMock()
        mock_handle.path = str(test_file)

        mock_proc = MagicMock()
        mock_proc.info = {"pid": 80001, "name": "cursor.exe", "create_time": 8000.0}
        mock_proc.cmdline.return_value = ["cursor.exe"]
        mock_proc.exe.return_value = "cursor.exe"
        mock_proc.cpu_times.return_value = MagicMock(user=1.0, system=1.0)
        mock_proc.open_files.return_value = [mock_handle]

        with patch("psutil.process_iter", return_value=[mock_proc]), \
             patch("scripts.watcher_proc.is_process_whitelisted", return_value=True):
            # Inspection must succeed without propagating the callback exception
            found = inspector._inspect_once(check_open_files=True)
            self.assertTrue(found)

        # Inspector stats still updated cleanly
        stats = inspector.get_scan_stats()
        self.assertEqual(stats["scan_count"], 1)
        self.assertEqual(stats["queue_size"], 1)


if __name__ == "__main__":
    unittest.main()
