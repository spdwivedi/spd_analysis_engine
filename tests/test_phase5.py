"""
tests/test_phase5.py
====================
Comprehensive Test Suite for Phase 5 of the SPD Analysis Engine:
1. AISynthesizer: SHA-256 diff caching, smart chunking, prompt generation, and fallback.
2. GitHub Remote Sync: ShadowGit.push_to_remote staging, committing, and upstream pushing.
3. Web Server API:
   - POST /api/project/<name>/analyze-event/<event_id> (AI analysis & SQLite persistence)
   - POST /api/project/<name>/git-push (Primary repo remote sync)
   - GET /api/project/<name>/events (Returning ai_summary)
   - Static asset verification (style.css modal scaling rules)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.ai_analyzer import AISynthesizer
from scripts.git_shadow import ShadowGit, push_to_remote
from scripts.storage_rotator import SessionDB, StorageRotator
from scripts.web_server import EngineWebServer


class TestAISynthesizer(unittest.TestCase):
    """Unit tests for the on-demand AI Intelligence Layer."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_ai_test_"))
        self.ai_data_dir = self.temp_dir / "ai_data"
        self.ai_data_dir.mkdir(parents=True, exist_ok=True)
        # Create minimal config
        cfg = {
            "default_provider": "gemini" if os.environ.get("GEMINI_API_KEY") else "ollama",
            "ollama": {"enabled": True, "base_url": "http://localhost:11434", "default_model": "qwen2.5-coder:7b"},
            "gemini": {"enabled": True, "api_key": os.environ.get("GEMINI_API_KEY", ""), "default_model": "gemini-3.8-flash"},
            "analysis": {"max_diff_lines_for_summary": 20},
        }
        (self.ai_data_dir / "models_config.json").write_text(json.dumps(cfg), encoding="utf-8")
        self.syn = AISynthesizer(engine_root=self.temp_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_diff_hash_and_cache(self) -> None:
        diff_text = "--- a/test.py\n+++ b/test.py\n@@ -1 +1 @@\n-print('hello')\n+print('world')"
        dhash = self.syn.compute_diff_hash(diff_text)
        self.assertTrue(len(dhash) == 64, "SHA-256 hash must be 64 characters long")

        # Initially cache should miss
        cached = self.syn.get_cached_analysis(dhash)
        self.assertIsNone(cached)

        # Save to cache
        dummy_analysis = {
            "intent": "Update greeting message",
            "summary": ["Changed hello to world"],
            "functionality_gained": "Updated output string",
            "provider": "test-mock",
        }
        self.syn.save_cached_analysis(dhash, dummy_analysis)

        # Cache should now hit
        hit = self.syn.get_cached_analysis(dhash)
        self.assertIsNotNone(hit)
        self.assertEqual(hit["intent"], "Update greeting message")
        self.assertTrue(hit.get("cached"))

    def test_smart_diff_chunking(self) -> None:
        lines = [f"+line {i}" for i in range(100)]
        long_diff = "\n".join(lines)
        chunked = self.syn.chunk_diff(long_diff, max_lines=20)
        self.assertIn("lines of unified diff omitted for analysis", chunked)
        self.assertTrue(len(chunked.splitlines()) < 30)

    def test_fallback_analysis_structure(self) -> None:
        fallback = self.syn._fallback_analysis(
            files=["main.py", "utils.py"],
            first_file="main.py",
            summary="Refactor entrypoint",
            reason="Offline test",
        )
        self.assertIn("intent", fallback)
        self.assertIn("summary", fallback)
        self.assertIn("functionality_gained", fallback)
        self.assertEqual(fallback["provider"], "offline-fallback")
        self.assertIsInstance(fallback["summary"], list)


class TestGitRemoteSync(unittest.TestCase):
    """Unit tests for primary workspace GitHub remote push/sync."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_git_push_test_"))
        self.upstream_dir = self.temp_dir / "upstream.git"
        self.workspace_dir = self.temp_dir / "workspace"
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

        self.git_bin = shutil.which("git")
        if not self.git_bin:
            self.skipTest("git is not installed on this system")

        # Create bare remote repository
        subprocess.run([self.git_bin, "init", "--bare", str(self.upstream_dir)], check=True, capture_output=True)

        # Initialize workspace repo
        subprocess.run([self.git_bin, "init"], cwd=str(self.workspace_dir), check=True, capture_output=True)
        subprocess.run([self.git_bin, "config", "user.name", "SPD Tester"], cwd=str(self.workspace_dir), check=True)
        subprocess.run([self.git_bin, "config", "user.email", "tester@spd.local"], cwd=str(self.workspace_dir), check=True)
        subprocess.run([self.git_bin, "remote", "add", "origin", str(self.upstream_dir)], cwd=str(self.workspace_dir), check=True)

        # Initial commit
        (self.workspace_dir / "init.txt").write_text("initial commit", encoding="utf-8")
        subprocess.run([self.git_bin, "add", "-A"], cwd=str(self.workspace_dir), check=True)
        subprocess.run([self.git_bin, "commit", "-m", "Initial commit"], cwd=str(self.workspace_dir), check=True)
        # Push initial commit to main
        subprocess.run([self.git_bin, "branch", "-M", "main"], cwd=str(self.workspace_dir), check=True)
        subprocess.run([self.git_bin, "push", "-u", "origin", "main"], cwd=str(self.workspace_dir), check=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_push_to_remote_success(self) -> None:
        # Create uncommitted modifications
        (self.workspace_dir / "feature.py").write_text("def hello(): return 'world'", encoding="utf-8")

        result = push_to_remote(self.workspace_dir, remote_name="origin", branch="main")
        self.assertTrue(result["success"], f"push_to_remote failed: {result}")
        self.assertEqual(result["remote"], "origin")
        self.assertEqual(result["branch"], "main")
        self.assertIsNotNone(result["commit"])

        # Verify upstream received the commit
        log_res = subprocess.run(
            [self.git_bin, "log", "-n1", "--format=%s"],
            cwd=str(self.upstream_dir),
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("SPD Session Sync", log_res.stdout)

    def test_push_to_nonexistent_remote(self) -> None:
        result = push_to_remote(self.workspace_dir, remote_name="nonexistent_remote", branch="main")
        self.assertFalse(result["success"])
        self.assertIn("No remote named 'nonexistent_remote'", result["message"])


class TestPhase5EndToEndAPI(unittest.TestCase):
    """End-to-End API verification for Phase 5 web server & AI analysis integration."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.engine_root = PROJECT_ROOT
        cls.port = 8795
        cls.server = EngineWebServer(host="127.0.0.1", port=cls.port, engine_root=cls.engine_root)
        cls.port = cls.server.start(background=True)
        cls.base_url = f"http://127.0.0.1:{cls.port}"

        cls.project_name = "phase5_e2e_test"
        cls.target_dir = cls.engine_root / "mock_workspace" / cls.project_name
        cls.target_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls) -> None:
        # Stop worker if active
        try:
            req = urllib.request.Request(
                f"{cls.base_url}/api/projects/stop",
                data=json.dumps({"project": cls.project_name}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass

        cls.server.shutdown()
        time.sleep(1.5)
        # Clean up temporary test artifacts across all tiers
        from scripts.storage_rotator import purge_test_artifacts
        purge_test_artifacts(cls.engine_root)


    def _post(self, path: str, payload: dict) -> dict:
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _get(self, path: str) -> dict:
        url = f"{self.base_url}{path}"
        with urllib.request.urlopen(url, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def test_01_static_assets_modal_scaling(self) -> None:
        """Verify CSS contains max-height, dark scrollbar, and AI button rules."""
        css_url = f"{self.base_url}/style.css"
        with urllib.request.urlopen(css_url, timeout=5) as resp:
            css_text = resp.read().decode("utf-8")
            self.assertIn("max-height: 88vh", css_text)
            self.assertIn(".btn-ai", css_text)
            self.assertIn(".ai-analysis-card", css_text)
            self.assertIn("#btn-header-push-git", css_text)

        # Verify index.html contains push button
        html_url = f"{self.base_url}/"
        with urllib.request.urlopen(html_url, timeout=5) as resp:
            html_text = resp.read().decode("utf-8")
            self.assertIn('id="btn-header-push-git"', html_text)
            self.assertIn('id="new-session-modal"', html_text)

    def test_02_worker_lifecycle_and_burst(self) -> None:
        """Start worker, write file, and wait for debounce event."""
        res = self._post("/api/projects/start", {
            "project": self.project_name,
            "path": str(self.target_dir),
            "scaffold": True,
            "debounce": 1.5,
            "track_reads": False,
            "track_exec": True,
            "shadow_git": True,
            "git_init_primary": True,
        })
        self.assertEqual(res.get("status"), "started")
        time.sleep(1.0)

        # Write test code
        test_file = self.target_dir / "calculator.py"
        test_file.write_text(
            "def add(a: int, b: int) -> int:\n    \"\"\"Return sum of two numbers.\"\"\"\n    return a + b\n",
            encoding="utf-8",
        )

        # Wait for debounce window (1.5s + quiet margin)
        time.sleep(2.5)

        # Verify event recorded
        events_resp = self._get(f"/api/project/{self.project_name}/events")
        events = events_resp.get("events", [])
        edit_events = [e for e in events if e.get("event_type") == "EDIT"]
        self.assertTrue(len(edit_events) >= 1, "At least one EDIT event should be recorded")

    def test_03_analyze_event_api(self) -> None:
        """Verify POST /api/project/<name>/analyze-event/<event_id> runs AI synthesis and stores ai_summary."""
        events_resp = self._get(f"/api/project/{self.project_name}/events")
        events = events_resp.get("events", [])
        edit_events = [e for e in events if e.get("event_type") == "EDIT"]
        self.assertTrue(len(edit_events) >= 1)
        event_id = edit_events[0]["id"]

        # Call analyze-event API
        analysis_resp = self._post(f"/api/project/{self.project_name}/analyze-event/{event_id}", {})
        self.assertTrue(analysis_resp.get("success"), f"Analysis failed: {analysis_resp}")
        ai_summary = analysis_resp.get("ai_summary", {})
        self.assertIn("intent", ai_summary)
        self.assertIn("summary", ai_summary)
        self.assertIn("functionality_gained", ai_summary)

        # Verify that querying /events now returns ai_summary in the database row
        refreshed_resp = self._get(f"/api/project/{self.project_name}/events")
        refreshed_events = refreshed_resp.get("events", [])
        matched = [e for e in refreshed_events if e["id"] == event_id][0]
        self.assertIsNotNone(matched.get("ai_summary"))
        self.assertEqual(matched["ai_summary"]["intent"], ai_summary["intent"])

    def test_04_git_push_api_structure(self) -> None:
        """Verify POST /api/project/<name>/git-push endpoint response."""
        push_resp = self._post(f"/api/project/{self.project_name}/git-push", {
            "remote": "origin",
            "branch": "main",
        })
        # Since mock workspace has no remote origin configured, success will be False with clean message
        self.assertIn("success", push_resp)
        self.assertIn("message", push_resp)
        self.assertFalse(push_resp["success"])
        self.assertIn("No remote named 'origin'", push_resp["message"])


if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("  SPD ANALYSIS ENGINE — PHASE 5 FULL INTEGRATION TEST SUITE")
    print("=" * 70 + "\n")
    unittest.main(verbosity=2)
