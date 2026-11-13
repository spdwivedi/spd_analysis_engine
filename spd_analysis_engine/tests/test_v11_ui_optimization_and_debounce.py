"""
tests/test_v11_ui_optimization_and_debounce.py
=============================================
Unit tests verifying UI optimizations, unified settings modal,
touched files scrolling/filtering, smart initial baseline debounce,
and rolling IDE churn detection.
"""

from __future__ import annotations

import unittest
from pathlib import Path
import time

from scripts.event_bundler import EventBundler

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestV11DebounceAndChurn(unittest.TestCase):
    """Test smart initial baseline ingestion and IDE churn detection."""

    def test_initial_ingestion_debounce_clamping_and_reversion(self) -> None:
        """Verify quiet_period is clamped to 3.5s on initial ingestion and restored after Burst #1."""
        emitted_bundles: list[dict] = []
        bundler = EventBundler(
            callback=lambda b: emitted_bundles.append(b),
            quiet_period=600.0,
            is_initial_ingestion=True,
        )

        self.assertEqual(bundler.configured_quiet_period, 600.0)
        self.assertEqual(bundler.quiet_period, 3.5)
        self.assertTrue(bundler.is_initial_ingestion)

        # Record file events for initial burst
        bundler.add_event("created", "main.py")
        bundler.add_event("created", "utils.py")

        status = bundler.get_debounce_status()
        self.assertTrue(status["active"])
        self.assertEqual(status["quiet_period"], 3.5)

        # Seal Burst #1
        burst = bundler.flush_burst_now()
        self.assertIsNotNone(burst)
        self.assertEqual(len(burst["files_touched"]), 2)

        # Verify quiet period is restored to configured duration (600s)
        self.assertFalse(bundler.is_initial_ingestion)
        self.assertEqual(bundler.quiet_period, 600.0)

        # Next event uses full 600s quiet period
        bundler.add_event("modified", "main.py")
        status_after = bundler.get_debounce_status()
        self.assertTrue(status_after["active"])
        self.assertEqual(status_after["quiet_period"], 600.0)
        bundler.stop()

    def test_ide_churn_detection_rolling_window(self) -> None:
        """Verify > 8 touches on a file in 10s sets ide_churn=True and clears upon sealing."""
        bundler = EventBundler(quiet_period=30.0)
        self.assertFalse(bundler.has_ide_churn())

        # Simulate 9 rapid edits on active file in IDE
        for i in range(9):
            bundler.add_event("modified", "active_doc.py")

        self.assertTrue(bundler.has_ide_churn())
        status = bundler.get_debounce_status()
        self.assertTrue(status["active"])
        self.assertTrue(status["ide_churn"])

        # Manual snapshot / burst flush immediately clears churn state
        burst = bundler.flush_burst_now()
        self.assertIsNotNone(burst)
        self.assertFalse(bundler.has_ide_churn())
        status_after = bundler.get_debounce_status()
        self.assertFalse(status_after["active"])
        self.assertFalse(status_after["ide_churn"])
        bundler.stop()


class TestV11UIOptimizationAndModals(unittest.TestCase):
    """Test frontend markup, Eco Mode CSS, and settings modal consolidation."""

    def setUp(self) -> None:
        self.index_html = (PROJECT_ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.eco_css = (PROJECT_ROOT / "web" / "css" / "eco_mode.css").read_text(encoding="utf-8")
        self.diff_css = (PROJECT_ROOT / "web" / "css" / "diff.css").read_text(encoding="utf-8")

    def test_eco_mode_css_rules(self) -> None:
        """Verify eco_mode.css defines .eco-mode with backdrop-filter none and paused animations."""
        self.assertIn(".eco-mode", self.eco_css)
        self.assertIn("backdrop-filter: none !important", self.eco_css)
        self.assertIn("box-shadow: none !important", self.eco_css)
        self.assertIn("animation-play-state: paused !important", self.eco_css)

    def test_header_reorganization(self) -> None:
        """Verify header cluster contains Eco Mode, Settings, and Push to GitHub."""
        self.assertIn('id="btn-header-eco-mode"', self.index_html)
        self.assertIn('id="btn-header-settings"', self.index_html)
        self.assertIn('id="btn-header-push-git"', self.index_html)
        self.assertIn('id="btn-header-stop"', self.index_html)
        self.assertIn('id="btn-header-resume"', self.index_html)

    def test_unified_settings_modal_tabs(self) -> None:
        """Verify unified settings modal contains all 4 tabs and preserved forms."""
        self.assertIn('id="modal-unified-settings"', self.index_html)
        self.assertIn('data-tab="ai"', self.index_html)
        self.assertIn('data-tab="git"', self.index_html)
        self.assertIn('data-tab="trash"', self.index_html)
        self.assertIn('data-tab="engine"', self.index_html)

        # Preserved internal form and element IDs
        self.assertIn('id="form-ai-settings"', self.index_html)
        self.assertIn('id="form-git-settings"', self.index_html)
        self.assertIn('id="trash-bin-items-container"', self.index_html)
        self.assertIn('id="del-modal-project-name"', self.index_html)
        self.assertIn('id="sel-default-debounce"', self.index_html)
        self.assertIn('id="chk-eco-mode-setting"', self.index_html)
        self.assertIn('id="btn-save-engine-settings"', self.index_html)

    def test_touched_files_scrolling_and_filtering(self) -> None:
        """Verify touched files list has filter input and scroll container styles."""
        self.assertIn('id="filter-touched-files"', self.index_html)
        self.assertIn("touched-files-list", self.index_html)
        self.assertIn(".touched-files-list", self.diff_css)
        self.assertIn("max-height: calc(100vh - 220px)", self.diff_css)
        self.assertIn("scrollbar-width: thin", self.diff_css)


if __name__ == "__main__":
    unittest.main()
