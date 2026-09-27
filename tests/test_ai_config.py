"""
tests/test_ai_config.py
========================
Comprehensive Test Suite for AI Model Configuration, Heuristic Mode,
Batch Analysis, Debounce Telemetry, and Web Server Endpoints.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from unittest.mock import patch, MagicMock

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.ai_analyzer import AISynthesizer
from scripts.event_bundler import EventBundler
from scripts.storage_rotator import SessionDB
from scripts.web_server import EngineWebServer


class TestAIConfigAndHeuristics(unittest.TestCase):
    """Test AI config persistence, masking, testing, and deterministic heuristics."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_ai_test_"))
        self.ai_data_dir = self.temp_dir / "ai_data"
        self.ai_data_dir.mkdir(parents=True, exist_ok=True)
        AISynthesizer.rate_limiter.reset()

    def tearDown(self) -> None:
        AISynthesizer.rate_limiter.reset()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_ai_config_persistence_and_masking(self) -> None:
        """Verify load_config, save_config, and mask_config."""
        cfg = AISynthesizer.load_config(engine_root=self.temp_dir)
        self.assertIn("preferred_provider", cfg)
        self.assertIn("gemini_api_key", cfg)

        # Save customized config
        new_cfg = {
            "preferred_provider": "gemini",
            "ollama_base_url": "http://127.0.0.1:11434",
            "ollama_model": "qwen2.5-coder:14b",
            "gemini_api_key": "AIzaSySecretKeyForTesting1234567890",
            "gemini_model": "gemini-2.5-pro",
        }
        saved = AISynthesizer.save_config(new_cfg, engine_root=self.temp_dir)
        self.assertEqual(saved["preferred_provider"], "gemini")
        self.assertEqual(saved["gemini_api_key"], "AIzaSySecretKeyForTesting1234567890")

        # Masking
        masked = AISynthesizer.mask_config(saved)
        self.assertTrue(masked["gemini_api_key"].startswith("AIza"))
        self.assertIn("****", masked["gemini_api_key"])
        self.assertNotIn("1234567890", masked["gemini_api_key"])

    def test_gemini_key_vault_lifecycle(self) -> None:
        """Verify adding, selecting, deleting, and masking keys in Gemini Key Vault."""
        # 1. Add first key
        saved1 = AISynthesizer.save_config({
            "gemini_api_key": "AIzaSyKeyOne1111111111111111",
            "key_label": "Account 1",
        }, engine_root=self.temp_dir)
        self.assertEqual(len(saved1["saved_gemini_keys"]), 1)
        k1_id = saved1["saved_gemini_keys"][0]["id"]
        self.assertEqual(saved1["gemini_api_key"], "AIzaSyKeyOne1111111111111111")

        # 2. Add second key
        saved2 = AISynthesizer.save_config({
            "gemini_api_key": "AIzaSyKeyTwo2222222222222222",
            "key_label": "Account 2",
        }, engine_root=self.temp_dir)
        self.assertEqual(len(saved2["saved_gemini_keys"]), 2)
        k2_id = saved2["saved_gemini_keys"][0]["id"]
        self.assertEqual(saved2["gemini_api_key"], "AIzaSyKeyTwo2222222222222222")

        # 3. Masking exposes both keys without leaking secrets
        masked = AISynthesizer.mask_config(saved2)
        self.assertEqual(len(masked["saved_gemini_keys"]), 2)
        self.assertTrue(masked["saved_gemini_keys"][0]["active"])
        self.assertFalse(masked["saved_gemini_keys"][1]["active"])
        self.assertNotIn("AIzaSyKeyTwo", masked["saved_gemini_keys"][0]["masked_key"])

        # 4. Select first key by ID
        selected = AISynthesizer.save_config({
            "select_key_id": k1_id,
        }, engine_root=self.temp_dir)
        self.assertEqual(selected["gemini_api_key"], "AIzaSyKeyOne1111111111111111")
        masked_sel = AISynthesizer.mask_config(selected)
        # Find item with k1_id
        item1 = next(item for item in masked_sel["saved_gemini_keys"] if item["id"] == k1_id)
        self.assertTrue(item1["active"])

        # 5. Delete active key
        deleted = AISynthesizer.save_config({
            "delete_key_id": k1_id,
        }, engine_root=self.temp_dir)
        self.assertEqual(len(deleted["saved_gemini_keys"]), 1)
        # Active key should failover to remaining key (k2)
        self.assertEqual(deleted["gemini_api_key"], "AIzaSyKeyTwo2222222222222222")

    def test_ist_changelog_header(self) -> None:
        """Verify Conventional Commit and CHANGELOG entries use IST timestamp headers."""
        syn = AISynthesizer(engine_root=self.temp_dir)
        events = [
            {
                "id": 1,
                "first_file_touched": "main.py",
                "summary": "Initial commit",
                "patches": [{"file_path": "main.py", "diff_content": "+print('hello')"}],
            }
        ]
        res = syn.generate_session_changelog(events, provider="heuristic")
        self.assertIn("### [", res["changelog_entry"])
        self.assertIn("IST]", res["changelog_entry"])

    def test_heuristic_burst_analysis(self) -> None:
        """Verify deterministic rule-based analysis produces structured output."""
        syn = AISynthesizer(engine_root=self.temp_dir)
        event = {
            "id": 1,
            "session_id": "test-session",
            "event_type": "EDIT",
            "timestamp": "2026-09-24T05:00:00Z",
            "first_file_touched": "src/api.py",
            "summary": "Modified 2 files",
        }
        patches = [
            {
                "file_path": "src/api.py",
                "diff_content": "--- a/src/api.py\n+++ b/src/api.py\n@@ -1,3 +1,6 @@\n+def new_route():\n+    return {'status': 'ok'}\n",
            },
            {
                "file_path": "tests/test_api.py",
                "diff_content": "--- a/tests/test_api.py\n+++ b/tests/test_api.py\n@@ -1,2 +1,5 @@\n+def test_new_route():\n+    assert True\n",
            },
        ]

        res = syn.analyze_burst(event, patches, provider="heuristic")
        self.assertEqual(res["provider"], "heuristic")
        self.assertIn("intent", res)
        self.assertIn("summary", res)
        self.assertIn("functionality_gained", res)
        self.assertIsInstance(res["summary"], list)
        self.assertTrue(any("src/api.py" in s for s in res["summary"]))

    def test_heuristic_changelog_synthesis(self) -> None:
        """Verify heuristic changelog generation creates conventional commits."""
        syn = AISynthesizer(engine_root=self.temp_dir)
        events = [
            {
                "id": 1,
                "first_file_touched": "web/index.html",
                "summary": "Updated UI layout",
                "patches": [{"file_path": "web/index.html", "diff_content": "+<h1>New Header</h1>"}],
            },
            {
                "id": 2,
                "first_file_touched": "scripts/server.py",
                "summary": "Added route",
                "patches": [{"file_path": "scripts/server.py", "diff_content": "+def handle_request(): pass"}],
            },
        ]
        res = syn.generate_session_changelog(events, provider="heuristic")
        self.assertEqual(res["provider"], "heuristic")
        self.assertTrue(res["commit_title"].startswith("feat") or res["commit_title"].startswith("refactor"))
        self.assertIn("commit_body", res)
        self.assertIn("changelog_entry", res)
        self.assertIn("web/index.html", res["changelog_entry"])

    @patch("urllib.request.urlopen")
    def test_ollama_connection_test(self, mock_urlopen: MagicMock) -> None:
        """Verify test_ollama_connection parses response correctly."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({
            "models": [{"name": "qwen2.5-coder:7b"}, {"name": "llama3.1:8b"}]
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        res = AISynthesizer.test_ollama_connection(
            base_url="http://127.0.0.1:11434",
            model="qwen2.5-coder:7b",
            engine_root=self.temp_dir,
        )
        self.assertTrue(res["success"])
        self.assertIn("Connected to Ollama", res["message"])
        self.assertIn("qwen2.5-coder:7b", res["installed_models"])

    @patch("urllib.request.urlopen")
    def test_gemini_connection_test(self, mock_urlopen: MagicMock) -> None:
        """Verify test_gemini_connection validates API key."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({
            "models": [{"name": "models/gemini-2.5-flash"}]
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        res = AISynthesizer.test_gemini_connection(
            api_key="AIzaSyValidDummyKey123",
            model="gemini-2.5-flash",
            engine_root=self.temp_dir,
        )
        self.assertTrue(res["success"])
        self.assertIn("Valid Gemini API key", res["message"])

    def test_large_diff_tpm_chunking(self) -> None:
        """Verify diffs exceeding 30,000 characters are safely truncated preserving headers."""
        syn = AISynthesizer(engine_root=self.temp_dir)
        file1 = "diff --git a/big1.py b/big1.py\n--- a/big1.py\n+++ b/big1.py\n@@ -1,1 +1,1 @@\n" + ("+line1 = 1234567890\n" * 1500)
        file2 = "diff --git a/big2.py b/big2.py\n--- a/big2.py\n+++ b/big2.py\n@@ -1,1 +1,1 @@\n" + ("+line2 = 1234567890\n" * 1500)
        massive_diff = file1 + "\n" + file2
        self.assertGreater(len(massive_diff), 40000)

        chunked = syn.chunk_diff(massive_diff, max_chars=30000)
        self.assertLessEqual(len(chunked), 30000)
        self.assertIn("big1.py", chunked)
        self.assertIn("big2.py", chunked)

    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_gemini_rate_limit_backoff(self, mock_urlopen: MagicMock, mock_sleep: MagicMock) -> None:
        """Verify _call_gemini retries up to 3 times on HTTP 429 rate limit."""
        syn = AISynthesizer(engine_root=self.temp_dir)
        syn.save_config({
            "preferred_provider": "gemini",
            "gemini": {
                "enabled": True,
                "api_key": "AIzaSyTestKey123",
                "default_model": "gemini-3.1-flash-lite",
            },
        }, engine_root=self.temp_dir)

        mock_429 = urllib.error.HTTPError("https://api", 429, "Too Many Requests", {}, None)
        mock_success = MagicMock()
        mock_success.__enter__.return_value.read.return_value = json.dumps({
            "candidates": [{
                "content": {"parts": [{"text": json.dumps({
                    "intent": "Backoff recovery test",
                    "summary": ["Handled 429 gracefully"],
                    "functionality_gained": "Retry resiliency"
                })}]}
            }]
        }).encode("utf-8")

        mock_urlopen.side_effect = [mock_429, mock_success]

        res = syn._call_gemini("Test prompt")
        self.assertEqual(res["intent"], "Backoff recovery test")
        self.assertEqual(mock_sleep.call_count, 1)
        mock_sleep.assert_called_with(3.0)


class TestDebounceStatus(unittest.TestCase):
    """Verify EventBundler get_debounce_status reporting."""

    def test_debounce_status_lifecycle(self) -> None:
        bundler = EventBundler(quiet_period=2.0)
        try:
            # Initially idle
            st = bundler.get_debounce_status()
            self.assertFalse(st["active"])
            self.assertEqual(st["remaining_s"], 0.0)
            self.assertEqual(st["files_count"], 0)

            # Add a file event
            bundler.add_event("modified", "src/main.py")
            st_active = bundler.get_debounce_status()
            self.assertTrue(st_active["active"])
            self.assertGreater(st_active["remaining_s"], 0.0)
            self.assertEqual(st_active["files_count"], 1)
            self.assertEqual(st_active["first_file"], "src/main.py")
        finally:
            bundler.stop()


class TestWebServerAIEndpoints(unittest.TestCase):
    """Integration test suite for web server AI endpoints."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = Path(tempfile.mkdtemp(prefix="spd_web_ai_test_"))
        cls.current_dir = cls.temp_dir / "current"
        cls.current_dir.mkdir(parents=True, exist_ok=True)
        cls.ai_data_dir = cls.temp_dir / "ai_data"
        cls.ai_data_dir.mkdir(parents=True, exist_ok=True)

        # Create dummy project
        cls.proj_dir = cls.current_dir / "test_proj"
        cls.proj_dir.mkdir(parents=True, exist_ok=True)
        cls.db_path = cls.proj_dir / "session.db"
        db = SessionDB(cls.db_path)
        sid = db.create_session("test_proj", str(cls.proj_dir))
        # Insert events and patches
        eid1 = db.record_event(sid, "EDIT", "app.py", "Edit app.py")
        db.record_patch(eid1, "app.py", "+print('hello world')\n")
        eid2 = db.record_event(sid, "EDIT", "style.css", "Edit style.css")
        db.record_patch(eid2, "style.css", "+body { background: black; }\n")
        db.close()

        # Start WebServer on random port
        cls.server = EngineWebServer(
            host="127.0.0.1",
            port=0,
            engine_root=cls.temp_dir,
            web_dir=cls.temp_dir,
        )
        cls.port = cls.server.start(background=True)
        cls.base_url = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def test_get_and_post_ai_config(self) -> None:
        """Verify GET /api/ai/config and POST /api/ai/config."""
        req = urllib.request.Request(f"{self.base_url}/api/ai/config")
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(data["success"])
            self.assertIn("config", data)

        # Save updated config
        payload = {
            "preferred_provider": "gemini",
            "gemini_api_key": "AIzaSySuperSecretKey12345",
            "gemini_model": "gemini-2.5-flash",
        }
        req_post = urllib.request.Request(
            f"{self.base_url}/api/ai/config",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req_post) as resp:
            post_data = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(post_data["success"])
            self.assertEqual(post_data["config"]["preferred_provider"], "gemini")
            self.assertIn("****", post_data["config"]["gemini_api_key"])

    def test_post_ai_test_heuristic(self) -> None:
        """Verify POST /api/ai/test with heuristic provider."""
        payload = {"provider": "heuristic"}
        req = urllib.request.Request(
            f"{self.base_url}/api/ai/test",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(data["success"])
            self.assertIn("Heuristic Mode is ready", data["message"])

    def test_batch_analyze(self) -> None:
        """Verify POST /api/project/<name>/analyze-batch starts async batch and status endpoint tracks progress."""
        # Set config to heuristic mode so no external network call is made
        syn = AISynthesizer(engine_root=self.temp_dir)
        syn.save_config({"preferred_provider": "heuristic"}, engine_root=self.temp_dir)
        AISynthesizer.rate_limiter.reset()

        payload = {"force_all": True}
        req = urllib.request.Request(
            f"{self.base_url}/api/project/test_proj/analyze-batch",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(data["success"])
            self.assertEqual(data["total_events"], 2)
            self.assertEqual(data["status"], "running")

        # Poll status endpoint until completed
        import time
        max_wait = 10.0
        start_t = time.time()
        final_status = {}
        while time.time() - start_t < max_wait:
            status_req = urllib.request.Request(f"{self.base_url}/api/project/test_proj/analyze-batch/status")
            with urllib.request.urlopen(status_req) as s_resp:
                final_status = json.loads(s_resp.read().decode("utf-8"))
                if final_status.get("status") == "completed":
                    break
            time.sleep(0.1)

        self.assertEqual(final_status.get("status"), "completed")
        self.assertEqual(final_status.get("analyzed_count"), 2)

        # Verify in DB
        db_p = self.db_path if self.db_path.exists() else (self.temp_dir / "last_run" / "test_proj" / "session.db")
        db = SessionDB(db_p)
        cur = db.conn.cursor()
        cur.execute("SELECT id, ai_summary FROM events")
        rows = cur.fetchall()
        for r in rows:
            self.assertIsNotNone(r[1])
            parsed = json.loads(r[1])
            self.assertIn("intent", parsed)
        db.close()

    def test_active_session_classification_and_burst_count(self) -> None:
        """Verify inactive current/ project is classified in last_run and bursts count correctly."""
        # Create an inactive project in current/
        inactive_dir = self.current_dir / "inactive_proj"
        inactive_dir.mkdir(parents=True, exist_ok=True)
        db_path = inactive_dir / "session.db"
        db = SessionDB(db_path)
        sid = db.create_session("inactive_proj", str(inactive_dir))
        # 2 EDIT events, 1 READ, 1 EXEC
        e1 = db.record_event(sid, "EDIT", "index.py", "Edit 1")
        db.record_patch(e1, "index.py", "+test\n")
        e2 = db.record_event(sid, "EDIT", "index.py", "Edit 2")
        db.record_patch(e2, "index.py", "+test2\n")
        db.record_event(sid, "READ", "data.json", "Inspected data.json")
        db.record_event(sid, "EXEC", "pytest", "Ran pytest")
        db.close()

        req = urllib.request.Request(f"{self.base_url}/api/projects")
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            # inactive_proj has no running worker PID, so it must NOT be in current
            self.assertEqual(len(data["current"]), 0)
            # Find inactive_proj in last_run
            proj = next(p for p in data["last_run"] if p["name"] == "inactive_proj")
            # Despite 4 total events (2 EDIT, 1 READ, 1 EXEC), events_count should only be 2 prompt bursts
            self.assertEqual(proj["events_count"], 2)


if __name__ == "__main__":
    unittest.main()
