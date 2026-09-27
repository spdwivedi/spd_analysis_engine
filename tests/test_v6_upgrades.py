"""
tests/test_v6_upgrades.py
=========================
Unit tests for the Reliability, Security, and Intelligence upgrades:
1. Hardened .gitignore generation and preservation.
2. Token & credential scrubbing in git_shadow and shell_interceptor.
3. RateLimitManager (sliding window, RPM enforcement, cooldown state).
4. Watchdog suppression for self-triggering files (e.g. CHANGELOG.md).
5. Command string parsing for workspace file read detection.
6. Execution rationale and delta diff prompt generation.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.git_shadow import (
    ensure_hardened_gitignore,
    scrub_tokens as git_scrub_tokens,
    HARDENED_GITIGNORE_TEMPLATE,
    ShadowGit,
)
from scripts.shell_interceptor import (
    scrub_tokens as shell_scrub_tokens,
    ExecutionTracker,
)
from scripts.ai_analyzer import RateLimitManager, AISynthesizer
from scripts.event_bundler import (
    suppress_file_path,
    is_path_suppressed,
    EventBundler,
)


class TestHardenedGitignore(unittest.TestCase):
    """Test automatic hardened .gitignore creation."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_gitignore_test_"))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_creates_gitignore_when_missing(self) -> None:
        created = ensure_hardened_gitignore(self.temp_dir)
        self.assertTrue(created)
        gitignore_path = self.temp_dir / ".gitignore"
        self.assertTrue(gitignore_path.exists())
        content = gitignore_path.read_text(encoding="utf-8")
        self.assertIn(".env", content)
        self.assertIn(".sf/", content)
        self.assertIn("__pycache__/", content)
        self.assertIn("node_modules/", content)
        self.assertIn(".spd/", content)
        self.assertIn("session.db*", content)

    def test_preserves_existing_gitignore(self) -> None:
        gitignore_path = self.temp_dir / ".gitignore"
        existing_content = "# Existing custom gitignore\nbuild/\n*.log\n"
        gitignore_path.write_text(existing_content, encoding="utf-8")

        created = ensure_hardened_gitignore(self.temp_dir)
        self.assertFalse(created)
        self.assertEqual(gitignore_path.read_text(encoding="utf-8"), existing_content)


class TestTokenScrubbing(unittest.TestCase):
    """Test token and credential scrubbing."""

    def test_scrub_tokens_git_shadow(self) -> None:
        # Basic auth URL
        url_with_token = "https://ghp_abc123XYZ45678901234567890123456@github.com/user/repo.git"
        scrubbed = git_scrub_tokens(url_with_token)
        self.assertNotIn("ghp_abc123XYZ45678901234567890123456", scrubbed)
        self.assertIn("github.com/user/repo.git", scrubbed)

        # Standalone GitHub tokens
        raw_ghp = "Exporting token ghp_111122223333444455556666777788889999 for deployment"
        self.assertNotIn("ghp_111122223333444455556666777788889999", git_scrub_tokens(raw_ghp))
        self.assertIn("ghp_***", git_scrub_tokens(raw_ghp))

        # Fine-grained PAT
        raw_pat = "github_pat_11AABBCC_secrettokencontent1234567890abcdef"
        self.assertNotIn("secrettokencontent", git_scrub_tokens(raw_pat))
        self.assertIn("github_pat_***", git_scrub_tokens(raw_pat))

        # Gemini API Key
        raw_gemini = "GEMINI_API_KEY=AIzaSyA1b2C3d4E5f6G7h8I9j0KlMnOpQrStUv"
        self.assertNotIn("AIzaSyA1b2C3d4E5f6G7h8I9j0KlMnOpQrStUv", git_scrub_tokens(raw_gemini))
        self.assertIn("AIza***", git_scrub_tokens(raw_gemini))

    def test_scrub_tokens_shell_interceptor(self) -> None:
        cmd = "curl -H 'Authorization: Bearer ghp_secret123456789012345678901234567890' https://api.github.com"
        scrubbed = shell_scrub_tokens(cmd)
        self.assertNotIn("ghp_secret", scrubbed)
        self.assertIn("ghp_***", scrubbed)


class TestRateLimitManager(unittest.TestCase):
    """Test RateLimitManager sliding window and cooldown."""

    def test_sliding_window_accounting(self) -> None:
        rl = RateLimitManager(max_rpm=5)
        status = rl.get_status()
        self.assertFalse(status["cooling_down"])
        self.assertEqual(status["current_rpm"], 0)
        self.assertEqual(status["max_rpm"], 5)

        # Record 3 requests
        rl.record_request()
        rl.record_request()
        rl.record_request()

        status = rl.get_status()
        self.assertEqual(status["current_rpm"], 3)
        self.assertFalse(status["cooling_down"])

    def test_trigger_cooldown(self) -> None:
        rl = RateLimitManager(max_rpm=10)
        rl.trigger_cooldown(duration_s=2.0)

        status = rl.get_status()
        self.assertTrue(status["cooling_down"])
        self.assertGreater(status["cooldown_remaining_s"], 0)

        # Wait out cooldown
        time.sleep(2.1)
        status = rl.get_status()
        self.assertFalse(status["cooling_down"])
        self.assertEqual(status["cooldown_remaining_s"], 0)

    def test_wait_if_needed_cooldown_callback(self) -> None:
        rl = RateLimitManager(max_rpm=10)
        rl.trigger_cooldown(duration_s=0.5)

        callback_invoked = []
        def on_cd(rem: float) -> None:
            callback_invoked.append(rem)

        rl.wait_if_needed(progress_callback=on_cd)
        self.assertTrue(len(callback_invoked) > 0)
        self.assertFalse(rl.get_status()["cooling_down"])


class TestEventSuppression(unittest.TestCase):
    """Test event suppression for self-triggering files."""

    def test_suppress_file_path(self) -> None:
        suppress_file_path("CHANGELOG.md", duration_s=1.0)
        self.assertTrue(is_path_suppressed("CHANGELOG.md"))
        self.assertTrue(is_path_suppressed("sub/folder/CHANGELOG.md"))
        self.assertFalse(is_path_suppressed("app.py"))

        # Wait for expiration
        time.sleep(1.1)
        self.assertFalse(is_path_suppressed("CHANGELOG.md"))

    def test_event_bundler_drops_suppressed_path(self) -> None:
        bundler = EventBundler(quiet_period=0.5)
        bundler.suppress_path("CHANGELOG.md", duration_s=1.0)

        bundler.add_event("modified", "CHANGELOG.md")
        self.assertEqual(len(bundler._pending_events), 0)


class TestShellReadDetection(unittest.TestCase):
    """Test command parsing for file read detection."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_shell_test_"))
        self.workspace_file = self.temp_dir / "main.py"
        self.workspace_file.write_text("print('hello')", encoding="utf-8")
        self.tracker = ExecutionTracker(target_path=str(self.temp_dir))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_detect_reads(self) -> None:
        # Detect relative path
        reads = self.tracker._detect_file_reads_from_cmd("cat main.py")
        self.assertEqual(reads, ["main.py"])

        # Detect python run
        reads2 = self.tracker._detect_file_reads_from_cmd("python main.py --verbose")
        self.assertEqual(reads2, ["main.py"])

        # Detect non-existent file
        reads3 = self.tracker._detect_file_reads_from_cmd("cat non_existent.txt")
        self.assertEqual(reads3, [])


if __name__ == "__main__":
    unittest.main()
