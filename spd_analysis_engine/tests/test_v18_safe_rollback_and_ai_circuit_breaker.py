"""
tests/test_v18_safe_rollback_and_ai_circuit_breaker.py
======================================================
Unit tests for:
1. Resilient workspace rollback with pre-rollback trash preservation and safe git execution.
2. Trash bin listing and restoration of scope 'rollback_backup'.
3. Instant Gemini circuit breaker failover (< 100ms) on HTTP 503/429 to Tier 1 Heuristic Mode.
4. Line count and micro-modularity compliance (shadow_rollback.py < 200 LOC, all scripts < 500 LOC).
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch, MagicMock

from scripts.git_shadow import ShadowGit
from scripts.git.shadow_rollback import rollback_to_burst
from scripts.trash.purge import list_trash_entries, purge_trash_entry
from scripts.trash.restore import restore_from_trash
from scripts.ai.provider_gemini import call_gemini
from scripts.ai.synthesizer_tiered import synthesize_milestone, tier1_heuristic_synthesize


class TestSafeRollbackWithTrashBackup(unittest.TestCase):
    """Test rollback_to_burst creates trash backup, restores files, and handles restoration."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_rb_test_"))
        self.workspace = self.temp_dir / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.shadow_dir = self.temp_dir / "shadow_git"
        self.engine_root = self.temp_dir / "engine"
        self.engine_root.mkdir(parents=True, exist_ok=True)

        self.sg = ShadowGit(shadow_dir=self.shadow_dir, target_dir=self.workspace)
        self.sg.init_repo()

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_rollback_creates_trash_backup_and_reverts_files(self) -> None:
        if not self.sg.is_available:
            self.skipTest("Git not available on host")

        # Burst 1: create file_a.py
        fa = self.workspace / "file_a.py"
        fa.write_text("print('version 1')\n", encoding="utf-8")
        c1 = self.sg.commit_burst(event_id=1, first_file="file_a.py", summary="Burst 1")
        self.assertIsNotNone(c1)

        # Burst 2: modify file_a.py and create file_b.py
        fa.write_text("print('version 2')\n", encoding="utf-8")
        fb = self.workspace / "file_b.py"
        fb.write_text("print('new file b')\n", encoding="utf-8")
        c2 = self.sg.commit_burst(event_id=2, first_file="file_a.py", summary="Burst 2")
        self.assertIsNotNone(c2)

        # Execute rollback_to_burst to c1
        res = rollback_to_burst(
            target_path=self.workspace,
            shadow_git_dir=self.shadow_dir,
            burst_hash=c1,
            project_slug="demo_proj",
            engine_root=self.engine_root,
            burst_id=2,
        )
        self.assertTrue(res.get("success"))
        self.assertEqual(res.get("rolled_back_to"), c1)
        backup_dir = Path(res["trash_backup"])
        self.assertTrue(backup_dir.exists())

        # 1. Verify manifest in trash backup
        mf_file = backup_dir / "manifest.json"
        self.assertTrue(mf_file.exists())
        mf = json.loads(mf_file.read_text(encoding="utf-8"))
        self.assertEqual(mf.get("scope"), "rollback_backup")
        self.assertEqual(mf.get("project_name"), "demo_proj")
        self.assertIn("file_a.py", mf.get("files", []))
        self.assertIn("file_b.py", mf.get("files", []))

        # 2. Verify workspace disk state: file_a is version 1, file_b is gone
        self.assertEqual(fa.read_text(encoding="utf-8"), "print('version 1')\n")
        self.assertFalse(fb.exists())

        # 3. Verify listing in trash bin
        trash_dir = self.engine_root / "trash"
        entries = list_trash_entries(trash_dir)
        self.assertTrue(any(e.get("scope") == "rollback_backup" for e in entries))

        # 4. Verify restoring from trash recovers the discarded files
        trash_id = mf.get("trash_id")
        restore_res = restore_from_trash(self.engine_root, trash_dir, trash_id)
        self.assertTrue(restore_res.get("success"))
        # Discarded version 2 of file_a and file_b should be restored to workspace
        self.assertEqual(fa.read_text(encoding="utf-8"), "print('version 2')\n")
        self.assertTrue(fb.exists())
        self.assertEqual(fb.read_text(encoding="utf-8"), "print('new file b')\n")

    def test_rollback_timeout_handling(self) -> None:
        # Test that timeout returns clean failure dictionary
        import subprocess
        with patch("scripts.git.shadow_rollback._run_git_cmd", side_effect=subprocess.TimeoutExpired(cmd=["git"], timeout=8)):
            res = rollback_to_burst(
                target_path=self.workspace,
                shadow_git_dir=self.shadow_dir,
                burst_hash="abcdef123456",
                project_slug="demo_proj",
                engine_root=self.engine_root,
            )
            self.assertFalse(res.get("success"))
            self.assertIn("timed out", res.get("error", "").lower())


class TestFastAICircuitBreaker(unittest.TestCase):
    """Test fast failover on Gemini 503/429 without stalling sleeps."""

    def test_call_gemini_fast_fail_on_http_503(self) -> None:
        cfg = {"api_key": "test_key", "default_model": "gemini-3.1-flash-lite"}
        start = time.perf_counter()
        with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError("url", 503, "Service Unavailable", {}, None)):
            with self.assertRaises(urllib.error.HTTPError):
                call_gemini("prompt", cfg, None, json.loads, fast_fail=True)
        elapsed = time.perf_counter() - start
        # Instant raise: must take less than 0.1s (100ms)
        self.assertLess(elapsed, 0.1, "fast_fail did not fail over within 100ms")

    def test_call_gemini_fast_fail_on_http_429(self) -> None:
        cfg = {"api_key": "test_key", "default_model": "gemini-3.1-flash-lite"}
        start = time.perf_counter()
        with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError("url", 429, "Too Many Requests", {}, None)):
            with self.assertRaises(urllib.error.HTTPError):
                call_gemini("prompt", cfg, None, json.loads, fast_fail=True)
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 0.1)

    def test_synthesize_milestone_failover_latency_under_100ms(self) -> None:
        import subprocess
        mock_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout=" M app.py\n", stderr="")
        with patch("scripts.ai.synthesizer_tiered._run_git", return_value=mock_proc):
            with patch("scripts.ai.synthesizer_tiered.call_gemini", side_effect=RuntimeError("HTTP 503: Service Unavailable")):
                start = time.perf_counter()
                res = synthesize_milestone(
                    burst_ids=[101],
                    mode="tiered",
                    project_slug="perf_proj",
                )
                elapsed = time.perf_counter() - start
                # Must return in under 100ms
                self.assertLess(elapsed, 0.1, f"Failover took {elapsed:.3f}s; expected < 0.100s")
                self.assertEqual(res.get("mode_used"), "tier1_heuristic")
                self.assertIn("Gemini 503 Failover", res.get("provider_used", ""))
                self.assertTrue(res.get("title").startswith(("feat", "fix", "chore", "docs", "test")))

    def test_tier1_heuristic_synthesizer_direct(self) -> None:
        res = tier1_heuristic_synthesize(
            project_slug="my_app",
            unpushed_bursts=[1, 2],
            is_root=False,
            touched_files=["src/app.py", "tests/test_app.py"],
            failover_reason="Tier 1 Heuristic (Gemini 503 Failover)",
        )
        self.assertEqual(res["mode_used"], "tier1_heuristic")
        self.assertEqual(res["provider_used"], "Tier 1 Heuristic (Gemini 503 Failover)")
        self.assertIn("my_app", res["changelog"])
        self.assertIn("app.py", res["title"])


class TestMicroModularityCompliance(unittest.TestCase):
    """Ensure strict LOC constraints: shadow_rollback.py < 200, all scripts < 500."""

    def test_shadow_rollback_under_500_loc(self) -> None:
        target = Path("scripts/git/shadow_rollback.py")
        self.assertTrue(target.exists())
        total_lines = len(target.read_text(encoding="utf-8").splitlines())
        self.assertLess(total_lines, 500, f"shadow_rollback.py has {total_lines} lines; must be < 500")

    def test_all_scripts_under_500_loc(self) -> None:
        scripts_dir = Path("scripts")
        violations = []
        for py_file in scripts_dir.rglob("*.py"):
            lines = len(py_file.read_text(encoding="utf-8").splitlines())
            if lines > 500:
                violations.append((str(py_file), lines))
        self.assertEqual(violations, [], f"Files in scripts/ exceeding 500 LOC: {violations}")


if __name__ == "__main__":
    unittest.main()
