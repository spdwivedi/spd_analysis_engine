"""
tests/test_burst_assembler_and_processor.py
============================================
Comprehensive test suite for:
- BurstAssembler (Condition-variable debouncing, zero threading.Timer objects)
- ChangeProcessor (Single ordered consumer per project, serialized non-overlapping bursts)
- Effective-change calculation using fingerprints (detects empty files, deletion, text/binary changes, ignores touches)
- Manifest reconciliation (periodic idle, queue overflow, watcher restart, sleep/wake gap)
"""
from __future__ import annotations

import os
import queue
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any

from scripts.core.burst_assembler import BurstAssembler
from scripts.core.change_processor import ChangeProcessor
from scripts.storage.fingerprint import (
    ChangeResult,
    FileFingerprint,
    compare_fingerprints,
    compute_fingerprint,
)
from scripts.storage.ignore_policy import IgnorePolicy
from scripts.storage.manifest import FileManifest


class TestBurstAssemblerAndProcessor(unittest.TestCase):
    """Test suite for condition-variable BurstAssembler and ChangeProcessor."""

    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="spd_burst_test_"))
        self.manifest = FileManifest(self.tmp_dir)
        self.policy = IgnorePolicy(self.tmp_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_2000_events_bundle_behavior(self) -> None:
        """Verify that 2,000 rapidly submitted events produce the expected ordered bundle."""
        assembler = BurstAssembler(
            quiet_period=0.08,
            max_burst_s=5.0,
            max_queue_size=25000,
        )
        assembler.start()

        try:
            for i in range(2000):
                assembler.add(f"module/file_{i}.py", event_type="modified")

            self.assertTrue(assembler.is_pending)
            self.assertEqual(assembler.pending_count, 2000)

            # Wait for quiet period to elapse and extract downstream bundle
            bundle = assembler.downstream_queue.get(timeout=2.0)
            self.assertIsNotNone(bundle)
            self.assertEqual(len(bundle["files_touched"]), 2000)
            self.assertEqual(bundle["first_file"], "module/file_0.py")
            self.assertFalse(assembler.is_pending)
        finally:
            assembler.stop(drain=False)

    def test_continuous_editing_max_burst_seal(self) -> None:
        """Verify that continuous editing seals at max_burst_s despite never reaching quiet period."""
        assembler = BurstAssembler(
            quiet_period=0.08,  # Quiet period is 80ms
            max_burst_s=0.20,   # Max burst is 200ms
        )
        assembler.start()

        try:
            start_mono = time.monotonic()
            # Continuously submit an event every 20ms for 320ms
            for i in range(16):
                assembler.add(f"rapid_{i}.py")
                time.sleep(0.02)

            # First bundle MUST seal at ~200ms (max_burst) even though edits were continuous
            bundle1 = assembler.downstream_queue.get(timeout=1.0)
            elapsed = time.monotonic() - start_mono

            self.assertGreaterEqual(elapsed, 0.18)
            self.assertLess(elapsed, 0.35)
            self.assertGreater(len(bundle1["files_touched"]), 0)

            # Second bundle collects remaining events after quiet period elapses
            bundle2 = assembler.downstream_queue.get(timeout=1.0)
            self.assertGreater(len(bundle2["files_touched"]), 0)
        finally:
            assembler.stop(drain=False)

    def test_quiet_period_seal(self) -> None:
        """Verify that an idle period satisfying quiet_period seals the bundle promptly."""
        assembler = BurstAssembler(quiet_period=0.05, max_burst_s=5.0)
        assembler.start()

        try:
            t0 = time.monotonic()
            assembler.add("doc1.txt")
            assembler.add("doc2.txt")

            bundle = assembler.downstream_queue.get(timeout=1.0)
            elapsed = time.monotonic() - t0

            self.assertGreaterEqual(elapsed, 0.045)
            self.assertLess(elapsed, 0.20)
            self.assertEqual(set(bundle["files_touched"]), {"doc1.txt", "doc2.txt"})
        finally:
            assembler.stop(drain=False)

    def test_hot_config_reload(self) -> None:
        """Verify that set_policy notifies condition variable and immediately applies new timings."""
        assembler = BurstAssembler(quiet_period=10.0, max_burst_s=30.0)
        assembler.start()

        try:
            assembler.add("reload_test.py")

            # At t=0.02, hot-reload to 0.05s quiet period
            time.sleep(0.02)
            assembler.set_policy(quiet_period=0.05, max_burst_s=0.1)

            t_reload = time.monotonic()
            bundle = assembler.downstream_queue.get(timeout=1.0)
            elapsed = time.monotonic() - t_reload

            # Should seal within ~0.06s of hot-reload, NOT after 10 seconds
            self.assertLess(elapsed, 0.2)
            self.assertEqual(bundle["files_touched"], ["reload_test.py"])
        finally:
            assembler.stop(drain=False)

    def test_timer_race_impossible(self) -> None:
        """
        Verify that no threading.Timer objects exist and concurrent additions/policy changes
        execute safely with zero race conditions.
        """
        assembler = BurstAssembler(quiet_period=0.04, max_burst_s=0.1)
        assembler.start()

        # Confirm no Timer attribute is used
        self.assertFalse(hasattr(assembler, "_timer"))

        # Concurrently spawn 8 threads adding events and modifying policies
        collected_bundles: list[dict[str, Any]] = []
        lock = threading.Lock()

        def submitter(worker_id: int) -> None:
            for j in range(50):
                assembler.add(f"w_{worker_id}_file_{j}.py")
                if j == 25:
                    assembler.set_policy(quiet_period=0.03, max_burst_s=0.08)
                time.sleep(0.001)

        threads = [threading.Thread(target=submitter, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Wait for all bundles to settle
        time.sleep(0.15)
        assembler.stop(drain=True)

        while not assembler.downstream_queue.empty():
            b = assembler.downstream_queue.get_nowait()
            collected_bundles.append(b)

        total_files = sum(len(b["files_touched"]) for b in collected_bundles)
        self.assertEqual(total_files, 400)  # 8 * 50 = 400

    def test_queue_overflow_and_reconciliation(self) -> None:
        """Verify that queue overflow sets needs_reconcile=True and triggers reconciliation."""
        assembler = BurstAssembler(
            quiet_period=0.05,
            max_burst_s=1.0,
            max_queue_size=20,  # Tiny bounded queue
        )

        # Submit 35 files: 20 should succeed, 15 dropped
        accepted = 0
        dropped = 0
        for i in range(35):
            if assembler.add(f"overflow_{i}.py"):
                accepted += 1
            else:
                dropped += 1

        self.assertEqual(accepted, 20)
        self.assertEqual(dropped, 15)
        self.assertTrue(assembler.needs_reconcile)
        self.assertEqual(assembler.drop_counter, 15)

        # ChangeProcessor processing a bundle with needs_reconcile=True triggers reconciliation
        reconciled_reasons: list[str] = []

        class MockProcessor(ChangeProcessor):
            def reconcile(self, reason: str = "manual") -> list[ChangeResult]:
                reconciled_reasons.append(reason)
                return []

        proc = MockProcessor(
            root_path=self.tmp_dir,
            downstream_queue=assembler.downstream_queue,
            manifest=self.manifest,
            ignore_policy=self.policy,
        )

        bundle = assembler.flush_now()
        self.assertIsNotNone(bundle)
        self.assertTrue(bundle.get("needs_reconcile"))

        proc.start()
        time.sleep(0.05)
        proc.stop(drain=True)

        self.assertIn("queue_overflow", reconciled_reasons)

    def test_watcher_restart_recovery(self) -> None:
        """Verify watcher restart triggers immediate manifest reconciliation and catches changes."""
        proc = ChangeProcessor(
            root_path=self.tmp_dir,
            downstream_queue=queue.Queue(),
            manifest=self.manifest,
            ignore_policy=self.policy,
        )

        # Baseline file in manifest
        f1 = self.tmp_dir / "existing.py"
        f1.write_text("v1", encoding="utf-8")
        self.manifest.record_file("existing.py")

        # Simulate events missed while watcher was down
        f1.write_text("v2_modified_while_offline", encoding="utf-8")
        f2 = self.tmp_dir / "created_while_offline.py"
        f2.write_text("new_content", encoding="utf-8")

        # Watcher restart triggers immediate recovery reconciliation
        changes = proc.on_watcher_restarted()
        changed_paths = {c.relative_path: c.change_type for c in changes}

        self.assertEqual(changed_paths.get("existing.py"), "modified")
        self.assertEqual(changed_paths.get("created_while_offline.py"), "created")
        self.assertIn("created_while_offline.py", self.manifest)

    def test_process_shutdown_with_pending_bundle(self) -> None:
        """Verify that stop(drain=True) extracts pending events and flushes them downstream."""
        assembler = BurstAssembler(quiet_period=10.0, max_burst_s=60.0)
        assembler.start()

        assembler.add("shutdown_test1.py")
        assembler.add("shutdown_test2.py")

        # Stop with drain=True
        assembler.stop(drain=True)

        self.assertFalse(assembler.is_pending)
        bundle = assembler.downstream_queue.get_nowait()
        self.assertIsNotNone(bundle)
        self.assertEqual(set(bundle["files_touched"]), {"shutdown_test1.py", "shutdown_test2.py"})

    def test_effective_change_calculation(self) -> None:
        """
        Verify fingerprint effective change detection:
        - empty file creation
        - deletion
        - binary changes
        - textual changes
        - ignores timestamp-only rewrites when content is unchanged
        """
        # 1. Empty file creation
        f_empty = self.tmp_dir / "empty.txt"
        f_empty.touch()
        fp_before_none = None
        fp_empty = compute_fingerprint(f_empty, "empty.txt")
        res_empty = compare_fingerprints(fp_before_none, fp_empty)
        self.assertTrue(res_empty.is_effective)
        self.assertEqual(res_empty.change_type, "created")
        self.assertTrue(res_empty.is_empty)

        # 2. Deletion
        res_del = compare_fingerprints(fp_empty, None, "empty.txt")
        self.assertTrue(res_del.is_effective)
        self.assertEqual(res_del.change_type, "deleted")

        # 3. Textual change
        f_txt = self.tmp_dir / "app.py"
        f_txt.write_text("version 1", encoding="utf-8")
        fp_txt1 = compute_fingerprint(f_txt, "app.py")
        f_txt.write_text("version 2", encoding="utf-8")
        fp_txt2 = compute_fingerprint(f_txt, "app.py")
        res_txt = compare_fingerprints(fp_txt1, fp_txt2)
        self.assertTrue(res_txt.is_effective)
        self.assertEqual(res_txt.change_type, "modified")

        # 4. Binary change
        f_bin = self.tmp_dir / "data.bin"
        f_bin.write_bytes(b"\x00\x01\x02\x03")
        fp_bin1 = compute_fingerprint(f_bin, "data.bin")
        f_bin.write_bytes(b"\x00\x01\x02\x99")
        fp_bin2 = compute_fingerprint(f_bin, "data.bin")
        res_bin = compare_fingerprints(fp_bin1, fp_bin2)
        self.assertTrue(res_bin.is_effective)
        self.assertEqual(res_bin.change_type, "modified")
        self.assertTrue(res_bin.is_binary)

        # 5. Timestamp-only rewrite when content is unchanged
        time.sleep(0.01)
        st = f_txt.stat()
        os.utime(f_txt, (st.st_atime, st.st_mtime + 5.0))
        fp_txt3 = compute_fingerprint(f_txt, "app.py")
        res_touch = compare_fingerprints(fp_txt2, fp_txt3)
        self.assertFalse(res_touch.is_effective, "Timestamp rewrite must NOT be an effective change")
        self.assertEqual(res_touch.change_type, "unchanged")

    def test_change_processor_overflow_bundle_reconcile_no_deadlock(self) -> None:
        """
        B-1 verification: Verify that ChangeProcessor does not self-deadlock
        when processing an overflow bundle with needs_reconcile=True.
        """
        test_file = self.tmp_dir / "overflow_test.py"
        test_file.write_text("print('overflow')", encoding="utf-8")

        downstream_q = queue.Queue()
        proc = ChangeProcessor(
            root_path=self.tmp_dir,
            downstream_queue=downstream_q,
            manifest=self.manifest,
            ignore_policy=self.policy,
            reconcile_interval_s=60.0,
        )
        proc.start()

        try:
            # Enqueue an overflow bundle requiring immediate reconciliation
            overflow_bundle = {
                "files_touched": ["overflow_test.py"],
                "first_file": "overflow_test.py",
                "event_count": 20000,
                "first_event_mono": time.monotonic(),
                "last_event_mono": time.monotonic(),
                "wall_time": time.time(),
                "needs_reconcile": True,
            }
            downstream_q.put(overflow_bundle)

            # Wait for queue to drain without deadlocking
            downstream_q.join()

            self.assertGreaterEqual(proc.burst_counter, 1)
            self.assertGreaterEqual(proc.reconciliation_counter, 1)
            self.assertIn("overflow_test.py", self.manifest)
        finally:
            proc.stop(drain=True, timeout=2.0)


if __name__ == "__main__":
    unittest.main()
