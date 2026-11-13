"""
tests/test_event_bundler_safety.py
==================================
Targeted tests for EventBundler thread-safety, timer race elimination,
hot-reloading, continuous activity bounding, and concurrency serialization.
"""

from __future__ import annotations

import threading
import time
import unittest
from typing import Any

from scripts.core.project_config import ProjectConfig
from scripts.event_bundler import EventBundler


class TestEventBundlerSafety(unittest.TestCase):
    """Safety and race-condition tests for EventBundler."""

    def test_timer_wait_race_new_event_arrives_old_callback_wakes_single_burst(self) -> None:
        """
        Specifically reproduce:
        1. Event arrives -> timer begins (gen 1)
        2. Timer callback begins and waits on _processing_lock
        3. New event arrives while timer callback waits (bumps gen to 2, re-arms timer 2)
        4. Old callback wakes, detects stale generation, and exits without flushing
        5. New timer fires and produces exactly ONE correct burst containing both events.
        """
        emitted_bundles: list[dict[str, Any]] = []
        lock = threading.Lock()

        def on_bundle(bundle: dict[str, Any]) -> None:
            with lock:
                emitted_bundles.append(bundle)

        # Set quiet_period to 0.05s
        bundler = EventBundler(callback=on_bundle, quiet_period=0.05)

        # Step 1: Pre-acquire _processing_lock externally so timer callback will wait
        bundler._processing_lock.acquire()
        try:
            # Add first event; timer 1 starts
            bundler.add_event("modified", "file_a.py")
            gen_after_first = bundler._timer_generation
            self.assertEqual(gen_after_first, 1)

            # Wait long enough for timer 1 (0.05s) to fire and block on _processing_lock
            time.sleep(0.08)

            # Step 2: While timer callback 1 is waiting on _processing_lock, new event arrives
            bundler.add_event("modified", "file_b.py")
            gen_after_second = bundler._timer_generation
            self.assertEqual(gen_after_second, 2)
        finally:
            # Step 3: Release _processing_lock so old callback wakes
            bundler._processing_lock.release()

        # Step 4: Wait for timer 2 to fire (0.05s quiet period + margin)
        time.sleep(0.12)

        # Step 5: Verify that exactly ONE burst was produced containing both files
        with lock:
            self.assertEqual(
                len(emitted_bundles),
                1,
                f"Expected exactly 1 burst, but got {len(emitted_bundles)}",
            )
            burst = emitted_bundles[0]
            self.assertIn("file_a.py", burst["files_touched"])
            self.assertIn("file_b.py", burst["files_touched"])
            self.assertEqual(len(burst["files_touched"]), 2)

        bundler.stop()

    def test_timer_reschedules_if_quiet_period_not_satisfied(self) -> None:
        """
        If a timer callback wakes but quiet period has not elapsed since the latest event,
        it must reschedule rather than flushing prematurely.
        """
        emitted_bundles: list[dict[str, Any]] = []
        bundler = EventBundler(callback=lambda b: emitted_bundles.append(b), quiet_period=0.1)

        # Add initial event
        bundler.add_event("modified", "test1.py")
        current_gen = bundler._timer_generation

        # Simulate waking prematurely while current_gen is valid but quiet period not elapsed
        bundler._on_timer_fired(timer_gen=current_gen)

        # Bundle should not have flushed because quiet_period (0.1s) was not satisfied
        self.assertEqual(len(emitted_bundles), 0)
        self.assertTrue(bundler.is_pending)

        # Wait for full quiet period to elapse
        time.sleep(0.15)
        self.assertEqual(len(emitted_bundles), 1)
        bundler.stop()

    def test_continuous_activity_triggers_max_burst(self) -> None:
        """
        Continuous activity faster than quiet_period must be bounded by max_burst_s
        and force a flush when max_burst_s is reached.
        """
        emitted_bundles: list[dict[str, Any]] = []
        # quiet_period = 0.08s, max_burst_s = 0.20s
        bundler = EventBundler(
            callback=lambda b: emitted_bundles.append(b),
            quiet_period=0.08,
            max_burst_s=0.20,
        )

        start = time.monotonic()
        event_num = 0

        # Stream events every 0.03s for 0.35s (quiet period of 0.08s is NEVER reached)
        while time.monotonic() - start < 0.35:
            bundler.add_event("modified", f"stream_{event_num}.py")
            event_num += 1
            time.sleep(0.03)

        # Since max_burst_s was 0.20s and we ran for 0.35s, at least 1 burst MUST have fired
        self.assertGreaterEqual(
            len(emitted_bundles),
            1,
            "Continuous activity failed to force flush at max_burst_s",
        )

        # Stop and drain remainder
        bundler.stop(flush_pending=True)
        self.assertGreaterEqual(len(emitted_bundles), 1)

    def test_hot_reload_cancels_and_rearms_pending_timer(self) -> None:
        """
        Hot reloading config must cancel existing timer and re-arm with new quiet period immediately.
        """
        emitted_bundles: list[dict[str, Any]] = []
        bundler = EventBundler(
            callback=lambda b: emitted_bundles.append(b),
            quiet_period=10.0,  # Initially very long
        )

        bundler.add_event("modified", "reload_test.py")
        self.assertTrue(bundler.is_pending)

        # Hot reload to 0.05s quiet period
        bundler.update_config(quiet_period=0.05)
        self.assertEqual(bundler.quiet_period, 0.05)

        # Should flush quickly (well before 10.0s)
        time.sleep(0.12)
        self.assertEqual(len(emitted_bundles), 1)
        self.assertEqual(emitted_bundles[0]["files_touched"], ["reload_test.py"])

        # Test hot reload via ProjectConfig object
        config = ProjectConfig(debounce_s=0.5, max_burst_s=1.0)
        bundler.update_config(config=config)
        self.assertEqual(bundler.quiet_period, 0.5)
        self.assertEqual(bundler.max_burst_s, 1.0)

        # Test hot reload to faster quiet_period float to test fast flush
        bundler.update_config(quiet_period=0.04)
        self.assertEqual(bundler.quiet_period, 0.04)

        bundler.add_event("modified", "reload_test_2.py")
        time.sleep(0.12)
        self.assertEqual(len(emitted_bundles), 2)

        bundler.stop()

    def test_hot_reload_via_property_setter(self) -> None:
        """Setting quiet_period property directly must re-arm active timer."""
        emitted_bundles: list[dict[str, Any]] = []
        bundler = EventBundler(
            callback=lambda b: emitted_bundles.append(b),
            quiet_period=10.0,
        )

        bundler.add_event("modified", "prop_test.py")
        bundler.quiet_period = 0.05

        time.sleep(0.12)
        self.assertEqual(len(emitted_bundles), 1)
        bundler.stop()

    def test_stop_during_pending_timer_flushes_pending_work(self) -> None:
        """
        Graceful stop() during pending timer must flush all pending work before returning.
        """
        emitted_bundles: list[dict[str, Any]] = []
        bundler = EventBundler(
            callback=lambda b: emitted_bundles.append(b),
            quiet_period=10.0,
        )

        bundler.add_event("modified", "shutdown_file.py")
        self.assertTrue(bundler.is_pending)

        # Graceful stop flushes pending work
        bundler.stop(flush_pending=True)
        self.assertFalse(bundler.is_pending)
        self.assertEqual(len(emitted_bundles), 1)
        self.assertEqual(emitted_bundles[0]["files_touched"], ["shutdown_file.py"])

        # Further events are dropped
        bundler.add_event("modified", "ignored.py")
        self.assertFalse(bundler.is_pending)
        self.assertEqual(len(emitted_bundles), 1)

    def test_stop_without_flush_drops_pending_work(self) -> None:
        """
        Calling stop(flush_pending=False) must cancel timers and discard pending events.
        """
        emitted_bundles: list[dict[str, Any]] = []
        bundler = EventBundler(
            callback=lambda b: emitted_bundles.append(b),
            quiet_period=10.0,
        )

        bundler.add_event("modified", "drop_file.py")
        bundler.stop(flush_pending=False)

        self.assertEqual(len(emitted_bundles), 0)
        self.assertFalse(bundler.is_pending)

    def test_two_bursts_cannot_process_concurrently(self) -> None:
        """
        _processing_lock must guarantee that multiple threads/callbacks
        cannot process bundles simultaneously.
        """
        active_count = 0
        max_active = 0
        lock = threading.Lock()
        emitted_bundles: list[dict[str, Any]] = []

        def slow_callback(bundle: dict[str, Any]) -> None:
            nonlocal active_count, max_active
            with lock:
                active_count += 1
                if active_count > max_active:
                    max_active = active_count
            time.sleep(0.05)
            with lock:
                active_count -= 1
                emitted_bundles.append(bundle)

        bundler = EventBundler(callback=slow_callback, quiet_period=0.02)

        # Thread 1 adds event and flushes
        def worker_1() -> None:
            bundler.add_event("modified", "thread1.py")
            bundler.flush_burst_now()

        # Thread 2 adds event and flushes
        def worker_2() -> None:
            bundler.add_event("modified", "thread2.py")
            bundler.flush()

        t1 = threading.Thread(target=worker_1)
        t2 = threading.Thread(target=worker_2)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        bundler.stop()

        self.assertEqual(
            max_active,
            1,
            f"Expected max concurrent callbacks <= 1, but observed {max_active}",
        )
        self.assertGreaterEqual(len(emitted_bundles), 1)

    def test_never_process_stale_timer_generation(self) -> None:
        """A timer callback with an outdated timer_gen token must be ignored."""
        emitted_bundles: list[dict[str, Any]] = []
        bundler = EventBundler(callback=lambda b: emitted_bundles.append(b), quiet_period=1.0)

        bundler.add_event("modified", "token_test.py")
        real_gen = bundler._timer_generation

        # Call with obsolete gen (real_gen - 1 or arbitrary mismatched gen)
        bundler._on_timer_fired(timer_gen=real_gen + 99)
        self.assertEqual(len(emitted_bundles), 0)
        self.assertTrue(bundler.is_pending)

        bundler.stop(flush_pending=False)


if __name__ == "__main__":
    unittest.main()
