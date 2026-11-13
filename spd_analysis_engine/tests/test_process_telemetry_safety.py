"""
tests/test_process_telemetry_safety.py
======================================
Tests verifying process telemetry improvements:
1. Idle CPU measurement
2. Scan duration recording & measurement
3. Scan frequency & adaptive intervals (avoids indefinite 0.18s polling)
4. Avoids repeated cwd()/cmdline() calls for unchanged processes
5. Avoids unnecessary open_files() on idle processes
6. TTL-based seen sets (avoids clearing entire set at arbitrary size)
7. Explicit observation source on READ telemetry (open_handle, cmd_ref, other)
8. 1-project activity vs 100-process environment scaling
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import psutil

from scripts.shell.tracker import ExecutionTracker
from scripts.watcher_proc import ProcessInspector


class TestProcessTelemetrySafety(unittest.TestCase):
    """Targeted regression tests for safe process telemetry."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_telemetry_test_")).resolve()

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_idle_cpu_measurement(self) -> None:
        """
        Measure idle CPU usage of ExecutionTracker and ProcessInspector.
        Verifies that background threads consume negligible CPU when idle.
        """
        tracker = ExecutionTracker(target_dir=self.temp_dir, idle_interval=0.2)
        inspector = ProcessInspector(target_path=self.temp_dir, poll_interval=0.2)

        proc = psutil.Process(os.getpid())
        cpu_start = proc.cpu_times()

        tracker.start()
        inspector.start()

        time.sleep(0.5)

        tracker.stop()
        inspector.stop()

        cpu_end = proc.cpu_times()
        cpu_consumed = (cpu_end.user - cpu_start.user) + (cpu_end.system - cpu_start.system)

        # In 0.5s idle window, CPU consumed should be bounded
        self.assertLess(cpu_consumed, 2.0, f"Excessive idle CPU consumed: {cpu_consumed:.3f}s")
        # And scanner total durations themselves must be reasonably bounded
        self.assertLess(tracker.total_scan_duration_s, 2.0)
        self.assertLess(inspector.total_scan_duration_s, 2.0)

    def test_scan_duration_recorded(self) -> None:
        """Verify that scan duration is measured and recorded in stats."""
        tracker = ExecutionTracker(target_dir=self.temp_dir)
        inspector = ProcessInspector(target_path=self.temp_dir)

        # Run single scan
        tracker._scan_processes_once()
        inspector._inspect_once()

        self.assertGreater(tracker.scan_count, 0)
        self.assertGreaterEqual(tracker.last_scan_duration_s, 0.0)
        tracker_stats = tracker.get_scan_stats()
        self.assertIn("last_scan_duration_s", tracker_stats)
        self.assertIn("avg_scan_duration_s", tracker_stats)
        self.assertEqual(tracker_stats["scan_count"], 1)

        self.assertGreater(inspector.scan_count, 0)
        self.assertGreaterEqual(inspector.last_scan_duration_s, 0.0)
        inspector_stats = inspector.get_scan_stats()
        self.assertIn("last_scan_duration_s", inspector_stats)
        self.assertIn("avg_scan_duration_s", inspector_stats)
        self.assertEqual(inspector_stats["scan_count"], 1)

    def test_scan_frequency_and_adaptive_intervals(self) -> None:
        """
        Verify that ExecutionTracker:
        1. Uses idle_interval when idle (not 0.18s).
        2. When fast polling is triggered, adaptively backs off towards idle_interval
           if consecutive scans detect no new activity (never stays at 0.18s indefinitely).
        """
        tracker = ExecutionTracker(
            target_dir=self.temp_dir,
            idle_interval=1.2,
            fast_interval=0.18,
        )

        # Idle state
        self.assertFalse(tracker.is_fast_polling)
        self.assertEqual(tracker._current_interval, 1.2)

        # Trigger fast poll
        tracker.trigger_fast_poll(10.0)
        self.assertTrue(tracker.is_fast_polling)

        # Simulate loop adaptive step with no activity:
        # Starting interval should be fast_interval
        tracker._current_interval = tracker.fast_interval
        self.assertEqual(tracker._current_interval, 0.18)

        # Consecutive idle scans during active fast poll should adaptively scale interval up
        intervals: list[float] = []
        consecutive_idle = 0
        for _ in range(6):
            consecutive_idle += 1
            backoff = tracker.fast_interval * (1.3 ** min(consecutive_idle, 8))
            tracker._current_interval = min(tracker.idle_interval, max(tracker.fast_interval, backoff))
            intervals.append(tracker._current_interval)

        # Verify that interval adaptively backed off from 0.18s
        self.assertGreater(intervals[-1], 0.18)
        self.assertLessEqual(intervals[-1], 1.2)
        # Monotonically increasing backoff
        for i in range(len(intervals) - 1):
            self.assertGreaterEqual(intervals[i + 1], intervals[i])

    def test_avoids_repeated_cwd_and_cmdline_for_unchanged_procs(self) -> None:
        """
        ExecutionTracker must cache (pid, create_time) metadata and avoid repeated
        expensive cwd() and cmdline() calls on unchanged processes.
        """
        tracker = ExecutionTracker(target_dir=self.temp_dir)

        # Mock 3 processes: 2 outside target, 1 inside target
        mock_procs: list[MagicMock] = []
        for i in range(3):
            m = MagicMock()
            m.info = {"pid": 20000 + i, "name": f"proc_{i}.exe", "create_time": 1000.0 + i}
            if i == 0:
                # Inside target
                m.cwd.return_value = str(self.temp_dir)
                m.cmdline.return_value = [f"proc_{i}.exe", "arg1"]
            else:
                # Outside target
                m.cwd.return_value = "C:\\Windows\\System32"
                m.cmdline.return_value = [f"proc_{i}.exe"]
            m.exe.return_value = f"C:\\bin\\proc_{i}.exe"
            mock_procs.append(m)

        with patch("psutil.process_iter", return_value=mock_procs), \
             patch("scripts.shell.tracker.is_process_whitelisted", return_value=True):

            # Scan 1: initial discovery populates cache
            tracker._scan_processes_once()
            initial_cwd_calls = [m.cwd.call_count for m in mock_procs]
            self.assertEqual(initial_cwd_calls, [1, 1, 1])

            # Scan 2: unchanged processes must NOT invoke cwd() again
            tracker._scan_processes_once()
            second_cwd_calls = [m.cwd.call_count for m in mock_procs]
            self.assertEqual(second_cwd_calls, [1, 1, 1], "cwd() was called again on unchanged processes")

            # Scan 3: unchanged processes must NOT invoke cwd() again
            tracker._scan_processes_once()
            third_cwd_calls = [m.cwd.call_count for m in mock_procs]
            self.assertEqual(third_cwd_calls, [1, 1, 1], "cwd() was repeatedly called across scans")

    def test_avoids_unnecessary_open_files(self) -> None:
        """
        ProcessInspector must skip calling open_files() on processes whose CPU times
        have not advanced since the last scan (idle processes).
        """
        inspector = ProcessInspector(target_path=self.temp_dir)

        mock_proc = MagicMock()
        mock_proc.info = {"pid": 30001, "name": "code.exe", "create_time": 5000.0}
        mock_proc.cmdline.return_value = ["code.exe", str(self.temp_dir)]

        # Initial CPU: 10.0 user, 5.0 system
        cpu_obj = MagicMock()
        cpu_obj.user = 10.0
        cpu_obj.system = 5.0
        mock_proc.cpu_times.return_value = cpu_obj
        mock_proc.open_files.return_value = []

        with patch("psutil.process_iter", return_value=[mock_proc]), \
             patch("scripts.watcher_proc.is_process_whitelisted", return_value=True):

            # Scan 1: first check queries open_files()
            inspector._inspect_once()
            self.assertEqual(mock_proc.open_files.call_count, 1)

            # Scan 2: process CPU time is unchanged -> skip open_files()!
            inspector._inspect_once()
            self.assertEqual(mock_proc.open_files.call_count, 1, "open_files() was called on an idle process")

            # Scan 3: process CPU time still unchanged -> skip open_files()!
            inspector._inspect_once()
            self.assertEqual(mock_proc.open_files.call_count, 1, "open_files() was called on an idle process")

            # Process executes instructions: CPU advances to 10.2
            cpu_obj.user = 10.2
            inspector._inspect_once()
            self.assertEqual(mock_proc.open_files.call_count, 2, "open_files() was not called when process had CPU activity")

    def test_ttl_based_seen_set(self) -> None:
        """
        Verifies that seen sets use TTL-based aging rather than wiping the entire set
        at an arbitrary size limit.
        """
        # Test ExecutionTracker seen PIDs
        tracker = ExecutionTracker(target_dir=self.temp_dir)
        tracker.seen_ttl_s = 1.0

        now = time.monotonic()
        # Add 2500 entries (exceeding previous 2000 arbitrary threshold)
        for pid in range(2500):
            tracker._seen_pids[pid] = now

        # Must NOT wipe everything clean
        self.assertEqual(len(tracker._seen_pids), 2500)

        # Age half the entries past TTL
        old_time = now - 2.0
        for pid in range(1000):
            tracker._seen_pids[pid] = old_time

        # Trigger prune
        tracker._scan_processes_once()

        # Exactly the 1000 expired entries are pruned; the 1500 recent entries remain
        self.assertLessEqual(len(tracker._seen_pids), 1500)
        self.assertGreaterEqual(len(tracker._seen_pids), 1400)

        # Test ProcessInspector seen handles
        inspector = ProcessInspector(target_path=self.temp_dir)
        inspector.seen_ttl_s = 1.0

        for idx in range(3500):
            inspector._seen_handles[(100, f"file_{idx}.py")] = now

        # Exceeds previous 3000 arbitrary threshold -> must retain entries!
        self.assertEqual(len(inspector._seen_handles), 3500)

    def test_read_telemetry_observation_source(self) -> None:
        """
        READ telemetry records must explicitly identify their observation source:
        'open_handle', 'cmd_ref', or 'other'.
        """
        read_records: list[dict[str, Any]] = []

        # 1. ProcessInspector emits 'open_handle'
        inspector = ProcessInspector(
            target_path=self.temp_dir,
            on_file_read=lambda r: read_records.append(r),
        )

        test_file = self.temp_dir / "target.py"
        test_file.write_text("print('hello')", encoding="utf-8")

        mock_handle = MagicMock()
        mock_handle.path = str(test_file)

        mock_proc = MagicMock()
        mock_proc.info = {"pid": 40001, "name": "code.exe", "create_time": 6000.0}
        mock_proc.cmdline.return_value = ["code.exe"]
        mock_proc.cpu_times.return_value = MagicMock(user=1.0, system=1.0)
        mock_proc.open_files.return_value = [mock_handle]

        with patch("psutil.process_iter", return_value=[mock_proc]), \
             patch("scripts.watcher_proc.is_process_whitelisted", return_value=True):
            inspector._inspect_once()

        self.assertEqual(len(read_records), 1)
        self.assertEqual(read_records[0]["event_type"], "READ")
        self.assertEqual(read_records[0]["source"], "open_handle")
        self.assertIn("open_handle", read_records[0]["summary"])

        # 2. ExecutionTracker command detection emits 'cmd_ref'
        read_records.clear()
        tracker = ExecutionTracker(
            target_dir=self.temp_dir,
            on_read=lambda r: read_records.append(r),
        )

        # Execute command referencing target.py
        tracker.record_execution(command=f"python {test_file.name}", duration_s=0.1)

        self.assertEqual(len(read_records), 1)
        self.assertEqual(read_records[0]["event_type"], "READ")
        self.assertEqual(read_records[0]["source"], "cmd_ref")
        self.assertIn("cmd_ref", read_records[0]["summary"])

    def test_1_project_vs_100_process_environment(self) -> None:
        """
        Measure and compare scan duration in:
        1. 1-project active environment
        2. 100-process environment with process caching
        """
        tracker = ExecutionTracker(target_dir=self.temp_dir)

        # 1-project activity
        active_proc = MagicMock()
        active_proc.info = {"pid": 50001, "name": "python.exe", "create_time": 7000.0}
        active_proc.cwd.return_value = str(self.temp_dir)
        active_proc.cmdline.return_value = ["python.exe", "run.py"]
        active_proc.exe.return_value = "C:\\Python\\python.exe"

        with patch("psutil.process_iter", return_value=[active_proc]), \
             patch("scripts.shell.tracker.is_process_whitelisted", return_value=True):
            t0 = time.monotonic()
            tracker._scan_processes_once()
            dur_1_project = time.monotonic() - t0

        self.assertLess(dur_1_project, 0.25)

        # 100-process environment
        mock_100: list[MagicMock] = []
        for i in range(100):
            m = MagicMock()
            m.info = {"pid": 60000 + i, "name": f"worker_{i}.exe", "create_time": 8000.0 + i}
            m.cwd.return_value = f"C:\\OtherProject\\proc_{i}"
            m.cmdline.return_value = [f"worker_{i}.exe"]
            m.exe.return_value = f"C:\\OtherProject\\worker_{i}.exe"
            mock_100.append(m)

        with patch("psutil.process_iter", return_value=mock_100), \
             patch("scripts.shell.tracker.is_process_whitelisted", return_value=True):
            # Scan 1: initial population
            tracker._scan_processes_once()

            # Scan 2: cached hits for 100 processes
            t0 = time.monotonic()
            tracker._scan_processes_once()
            dur_100_cached = time.monotonic() - t0

        # Scan 2 with 100 cached processes should be fast (< 50ms)
        self.assertLess(dur_100_cached, 0.1, f"100 cached processes took too long: {dur_100_cached:.4f}s")


if __name__ == "__main__":
    unittest.main()
