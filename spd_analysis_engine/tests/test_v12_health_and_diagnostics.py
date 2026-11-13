"""
tests/test_v12_health_and_diagnostics.py
========================================
Unit tests for Engine Diagnostics API (/api/engine/health),
emergency raw worker log tailing (/api/project/<slug>/worker-log-raw),
and CLI pre-flight verification (scripts/core/preflight.py).
"""

import json
import os
import shutil
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from scripts.core.preflight import run_preflight_checks, clean_orphan_lockfiles
from scripts.server.routes_health import HealthRoutesMixin


class TestPreflightDiagnostics(unittest.TestCase):
    """Test CLI pre-flight checks and lockfile cleanup."""

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="spd_test_preflight_"))

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_run_preflight_checks(self):
        res = run_preflight_checks(engine_root=self.test_dir, print_output=False)
        self.assertIsInstance(res, dict)
        self.assertIn("all_passed", res)
        self.assertIn("results", res)
        results = res["results"]
        self.assertTrue(results["python_version"])
        self.assertTrue(results["dependencies"])
        self.assertTrue(results["sqlite_wal"])
        self.assertTrue(results["storage_tiers"])
        self.assertTrue(results["config_validation"])
        self.assertTrue(res["all_passed"])

    def test_clean_orphan_lockfiles(self):
        current_dir = self.test_dir / "current" / "test_proj"
        current_dir.mkdir(parents=True, exist_ok=True)

        lock_file = current_dir / "index.lock"
        lock_file.write_text("lock", encoding="utf-8")

        # Set modification time to 15 minutes ago
        old_time = time.time() - 900.0
        os.utime(lock_file, (old_time, old_time))

        cleaned = clean_orphan_lockfiles(self.test_dir, max_age_seconds=600.0)
        self.assertEqual(len(cleaned), 1)
        self.assertFalse(lock_file.exists())


class TestHealthRoutesMixin(unittest.TestCase):
    """Test HealthRoutesMixin API handlers."""

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="spd_test_health_"))
        for tier in ["current", "last_run", "history", "trash", "ai_data"]:
            (self.test_dir / tier).mkdir(parents=True, exist_ok=True)

        # Create active project with WAL session.db and worker.log
        self.proj_dir = self.test_dir / "current" / "alpha_proj"
        self.proj_dir.mkdir(parents=True, exist_ok=True)

        db_path = self.proj_dir / "session.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("CREATE TABLE test_events (id INTEGER PRIMARY KEY, note TEXT);")
        conn.execute("INSERT INTO test_events (note) VALUES ('test');")
        conn.commit()
        conn.close()

        # Write sample worker.log
        self.log_path = self.proj_dir / "worker.log"
        log_lines = [f"[2026-09-29 00:00:{i:02d}] Log line {i}\n" for i in range(120)]
        self.log_path.write_text("".join(log_lines), encoding="utf-8")

        # Mock handler
        class MockServer:
            engine_root = self.test_dir

            class MockSupervisor:
                def get_status(self):
                    return [
                        {
                            "project_slug": "alpha_proj",
                            "pid": os.getpid(),
                            "alive": True,
                            "last_seen": time.time(),
                        }
                    ]

            supervisor = MockSupervisor()

        class MockHandler(HealthRoutesMixin):
            def __init__(self, srv):
                self.server_instance = srv
                self.last_status = None
                self.last_json = None

            def _send_json(self, data, status=200):
                self.last_json = data
                self.last_status = status

        self.handler = MockHandler(MockServer())

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_handle_api_engine_health(self):
        self.handler._handle_api_engine_health()
        self.assertEqual(self.handler.last_status, 200)

        data = self.handler.last_json
        self.assertIsNotNone(data)
        self.assertIn("timestamp", data)
        self.assertIn("overall_status", data)
        self.assertEqual(data["overall_status"], "healthy")

        pipelines = data.get("pipelines", {})
        self.assertIn("database", pipelines)
        self.assertIn("storage_tiers", pipelines)
        self.assertIn("shadow_git", pipelines)
        self.assertIn("worker_daemon", pipelines)
        self.assertIn("configurations", pipelines)

        # Database assertions
        db_info = pipelines["database"]
        self.assertTrue(db_info["wal_mode"])
        self.assertEqual(db_info["integrity"], "ok")
        self.assertEqual(len(db_info["active_databases"]), 1)

        # Storage tiers assertions
        tiers_info = pipelines["storage_tiers"]["tiers"]
        for tier in ["current", "last_run", "history", "trash"]:
            self.assertTrue(tiers_info[tier]["exists"])
            self.assertTrue(tiers_info[tier]["readable"])
            self.assertTrue(tiers_info[tier]["writable"])

        # Active path manifest
        paths = data.get("active_paths", {})
        self.assertEqual(paths["project_name"], "alpha_proj")
        self.assertTrue(paths["session_db"].endswith("session.db"))
        self.assertTrue(paths["worker_log"].endswith("worker.log"))

        # Health & Observability Metrics Exposures
        self.assertIn("queue_depths", data)
        self.assertIn("writer", data["queue_depths"])
        self.assertIn("burst_assembler", data["queue_depths"])
        self.assertIn("ai_jobs", data["queue_depths"])

        self.assertIn("drop_counters", data)
        self.assertIn("writer", data["drop_counters"])
        self.assertIn("burst_assembler", data["drop_counters"])

        self.assertIn("writer_lag", data)
        self.assertIn("last_batch_duration_ms", data["writer_lag"])
        self.assertIn("status", data["writer_lag"])

        self.assertIn("git_queue_status", data)
        self.assertIn("status", data["git_queue_status"])
        self.assertIn("pending_ops", data["git_queue_status"])

        self.assertIn("ai_queue_status", data)
        self.assertIn("queued", data["ai_queue_status"])
        self.assertIn("running", data["ai_queue_status"])

        self.assertIn("scan_durations", data)
        self.assertIn("proc_watcher_s", data["scan_durations"])
        self.assertIn("manifest_scan_s", data["scan_durations"])

        self.assertIn("active_workers", data)
        self.assertIsInstance(data["active_workers"], list)
        self.assertEqual(len(data["active_workers"]), 1)
        self.assertEqual(data["active_workers"][0]["project"], "alpha_proj")

        self.assertIn("reconciler_health", data)
        self.assertIn("status", data["reconciler_health"])
        self.assertIn("needs_reconcile", data["reconciler_health"])

    def test_handle_api_worker_log_raw(self):
        # 1. Fetch 50 lines
        self.handler._handle_api_worker_log_raw("alpha_proj", {"lines": ["50"]})
        self.assertEqual(self.handler.last_status, 200)
        data = self.handler.last_json
        self.assertTrue(data["success"])
        self.assertTrue(data["exists"])
        self.assertEqual(data["lines_count"], 50)
        self.assertEqual(len(data["lines"]), 50)
        self.assertTrue(data["lines"][-1].endswith("Log line 119\n"))

        # 2. Fetch missing project log
        self.handler._handle_api_worker_log_raw("non_existent_proj", {"lines": ["20"]})
        self.assertEqual(self.handler.last_status, 200)
        miss_data = self.handler.last_json
        self.assertTrue(miss_data["success"])
        self.assertFalse(miss_data["exists"])
        self.assertEqual(miss_data["lines_count"], 0)

    def test_handle_api_purge_zombies(self):
        curr_p = self.test_dir / "current" / "zombie_proj"
        curr_p.mkdir(parents=True, exist_ok=True)
        (curr_p / "worker.pid").write_text("999999", encoding="utf-8")
        (curr_p / "session.lock").write_text("LOCK", encoding="utf-8")
        (curr_p / "session.db").write_text("db dummy", encoding="utf-8")

        self.assertTrue(curr_p.exists())
        self.handler._handle_api_purge_zombies()

        self.assertEqual(self.handler.last_status, 200)
        res = self.handler.last_json
        self.assertTrue(res["success"])
        self.assertGreaterEqual(res["cleaned_dirs"], 1)

        # current/zombie_proj should be moved away
        self.assertFalse(curr_p.exists())
        last_run_p = self.test_dir / "last_run" / "zombie_proj"
        self.assertTrue(last_run_p.exists())
        self.assertFalse((last_run_p / "worker.pid").exists())
        self.assertFalse((last_run_p / "session.lock").exists())


if __name__ == "__main__":
    unittest.main()
