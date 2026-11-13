"""
tests/test_controller_ai_execution.py
======================================
Comprehensive test suite for controller-managed AI execution architecture:
1. Worker never holds provider API keys; worker can safely enqueue analysis jobs.
2. Durable `jobs` and `ai_analyses` in engine.db with WAL mode.
3. AI analysis identity: 9-component unique cache key sensitivity and deduplication.
4. Statuses: queued -> running -> done / failed / superseded.
5. Stored fields: result_json, parent_analysis_id, tokens, latency, error_class; failure never stored as success.
6. Progressive levels: L1 Quick, L2 Detailed, L3 Architecture, L4 Risk/Impact with "expand, don't restate".
7. Bounded provider pools with concurrency limits.
8. Retry rules: 429/503 backoff, auth errors no auto-retry, JSON parse repair retry, timeout smaller-context retry, permanent failure + manual Retry action.
9. Job leases and heartbeats: killed controller job resumes automatically.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from scripts.ai.identity import (
    AnalysisIdentity,
    AnalysisStatus,
    ProgressiveLevel,
    EXPAND_INSTRUCTION,
    compute_analysis_cache_key,
    compute_content_hash,
)
from scripts.ai.error_handling import (
    RateLimitError,
    AuthenticationError,
    JSONParseError,
    TimeoutError,
    PermanentError,
    classify_ai_exception,
    evaluate_retry_plan,
)
from scripts.ai.controller_db import EngineDB
from scripts.ai.provider_pool import BoundedProviderPool
from scripts.ai.progressive_levels import build_progressive_prompt, parse_progressive_result
from scripts.ai.controller_executor import ControllerAIExecutor
from scripts.core.spawner import spawn_detached_worker


class TestControllerAIExecution(unittest.TestCase):
    """Test suite verifying controller-owned AI execution."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_ctrl_ai_test_"))
        self.db_path = self.temp_dir / "data" / "engine.db"
        self.db = EngineDB(self.db_path)
        from scripts.storage.project_storage import write_project_config
        write_project_config(self.temp_dir, "proj", {"cloud_ai_enabled": True})
        self.pool = BoundedProviderPool({"gemini": 2, "ollama": 1, "heuristic": 3})
        self.executor = ControllerAIExecutor(
            engine_root=self.temp_dir,
            db=self.db,
            provider_pool=self.pool,
            worker_id="test_ctrl_worker_1",
            lease_duration_s=2.0,
            heartbeat_interval_s=0.5,
        )

    def tearDown(self) -> None:
        self.executor.stop(timeout=2.0)

    # -------------------------------------------------------------------------
    # 1. Worker Key Isolation & Enqueueing
    # -------------------------------------------------------------------------

    def test_worker_env_scrubs_provider_api_keys(self) -> None:
        """Worker environment scrubs provider keys; workers never hold API keys."""
        with patch.dict(os.environ, {
            "GEMINI_API_KEY": "secret_gemini_key",
            "OPENAI_API_KEY": "secret_openai_key",
            "SOME_API_KEY": "secret_provider_key",
            "SYSTEM_TOKEN": "safe_system_token",
        }):
            with patch("subprocess.Popen") as mock_popen:
                mock_popen.return_value = MagicMock(pid=12345)
                log_file = self.temp_dir / "worker.log"

                spawn_detached_worker(
                    py_exe="python",
                    worker_script=Path("scripts/project_worker.py"),
                    name="test_proj",
                    path=str(self.temp_dir),
                    engine_root=self.temp_dir,
                    log_path=log_file,
                )

                call_kwargs = mock_popen.call_args[1]
                passed_env = call_kwargs.get("env", {})
                self.assertNotIn("GEMINI_API_KEY", passed_env)
                self.assertNotIn("OPENAI_API_KEY", passed_env)
                self.assertNotIn("SOME_API_KEY", passed_env)
                self.assertEqual(passed_env.get("SYSTEM_TOKEN"), "safe_system_token")

    def test_worker_safe_enqueue_without_api_keys(self) -> None:
        """Workers enqueue jobs into controller engine.db without holding provider credentials."""
        job_id = self.executor.enqueue_analysis(
            project_slug="alpha_project",
            target_kind="burst",
            target_id="burst_42",
            diff_text="--- a/mod.py\n+++ b/mod.py\n@@ -1 +1 @@\n-old\n+new",
            level=ProgressiveLevel.L1,
        )
        self.assertTrue(job_id.startswith("job_"))
        job = self.db.get_job(job_id)
        self.assertIsNotNone(job)
        self.assertEqual(job["status"], AnalysisStatus.QUEUED)
        self.assertEqual(job["project_slug"], "alpha_project")
        self.assertEqual(job["level"], "L1")

    # -------------------------------------------------------------------------
    # 2. Durable Jobs and AI Analyses in engine.db
    # -------------------------------------------------------------------------

    def test_durable_jobs_and_analyses_schema(self) -> None:
        """Verify jobs and ai_analyses tables exist in engine.db with indexes and WAL mode."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # Check WAL mode
        cur.execute("PRAGMA journal_mode;")
        self.assertEqual(cur.fetchone()[0].lower(), "wal")

        # Check tables
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {r[0] for r in cur.fetchall()}
        self.assertIn("jobs", tables)
        self.assertIn("ai_analyses", tables)

        # Check jobs columns
        cur.execute("PRAGMA table_info(jobs);")
        job_cols = {r["name"] for r in cur.fetchall()}
        expected_job_cols = {
            "id", "project_slug", "target_kind", "target_id", "level", "profile",
            "analysis_id", "cache_key", "payload_json", "status", "retry_count",
            "max_retries", "error_class", "error_message", "lease_owner",
            "lease_expires_at", "last_heartbeat", "created_at", "updated_at", "completed_at"
        }
        self.assertTrue(expected_job_cols.issubset(job_cols))

        # Check ai_analyses columns
        cur.execute("PRAGMA table_info(ai_analyses);")
        ana_cols = {r["name"] for r in cur.fetchall()}
        expected_ana_cols = {
            "id", "cache_key", "project_slug", "target_kind", "target_id", "profile",
            "level", "input_hash", "model", "provider", "prompt_version",
            "sanitizer_version", "context_hash", "parent_analysis_id", "status",
            "result_json", "prompt_tokens", "completion_tokens", "total_tokens",
            "latency_ms", "error_class", "error_message", "created_at", "completed_at"
        }
        self.assertTrue(expected_ana_cols.issubset(ana_cols))
        conn.close()

    # -------------------------------------------------------------------------
    # 3. AI Analysis Identity & Unique Cache Key
    # -------------------------------------------------------------------------

    def test_unique_cache_key_requires_all_9_components(self) -> None:
        """Altering ANY of the 9 identity components changes the unique cache key."""
        base_params = {
            "target_kind": "file",
            "target_id": "src/app.py",
            "profile": "deep_review",
            "input_hash": "a" * 64,
            "model": "gemini-3.1-flash-lite",
            "provider": "gemini",
            "prompt_version": "v1.2",
            "sanitizer_version": "v1.0",
            "context_hash": "c" * 64,
        }
        base_key = compute_analysis_cache_key(**base_params)

        # Mutate each of the 9 fields individually and assert key differs
        mutations = [
            ("target_kind", "burst"),
            ("target_id", "src/other.py"),
            ("profile", "quick_summary"),
            ("input_hash", "b" * 64),
            ("model", "qwen2.5-coder:7b"),
            ("provider", "ollama"),
            ("prompt_version", "v2.0"),
            ("sanitizer_version", "v1.1"),
            ("context_hash", "d" * 64),
        ]
        for field, new_val in mutations:
            modified_params = dict(base_params)
            modified_params[field] = new_val
            modified_key = compute_analysis_cache_key(**modified_params)
            self.assertNotEqual(
                base_key, modified_key,
                f"Modifying component '{field}' must alter the canonical cache key"
            )

    # -------------------------------------------------------------------------
    # 4. Statuses: queued, running, done, failed, superseded
    # -------------------------------------------------------------------------

    def test_status_transitions_and_supersede(self) -> None:
        """Verify job status transitions and target superseding."""
        jid1 = self.executor.enqueue_analysis("proj", "burst", "burst_1", "diff1")
        jid2 = self.executor.enqueue_analysis("proj", "burst", "burst_1", "diff2")

        # Supersede all prior jobs for burst_1 except jid2
        superseded = self.db.supersede_target_jobs("burst", "burst_1", except_job_id=jid2)
        self.assertEqual(superseded, 1)

        job1 = self.db.get_job(jid1)
        job2 = self.db.get_job(jid2)
        self.assertEqual(job1["status"], AnalysisStatus.SUPERSEDED)
        self.assertEqual(job2["status"], AnalysisStatus.QUEUED)

    # -------------------------------------------------------------------------
    # 5. Stored Fields & Failure Never Stored as Success
    # -------------------------------------------------------------------------

    def test_never_store_ai_failure_as_successful_result(self) -> None:
        """AI failure records error class and message, but NEVER stores result_json as success."""
        cache_key = "test_fail_cache_key_12345"
        ana_id = self.db.record_analysis({
            "cache_key": cache_key,
            "project_slug": "proj",
            "target_kind": "burst",
            "target_id": "b1",
            "profile": "default",
            "level": "L1",
            "input_hash": "h1",
            "model": "gemini",
            "provider": "gemini",
            "status": AnalysisStatus.FAILED,
            "result_json": {"illegal_result": "should_not_be_saved"},
            "error_class": "AuthenticationError",
            "error_message": "Invalid API key provided",
        })

        record = self.db.get_analysis(ana_id)
        self.assertIsNotNone(record)
        self.assertEqual(record["status"], AnalysisStatus.FAILED)
        self.assertIsNone(record["result_json"])
        self.assertEqual(record["error_class"], "AuthenticationError")
        self.assertEqual(record["error_message"], "Invalid API key provided")

    # -------------------------------------------------------------------------
    # 6. Progressive Levels: L1 Quick, L2 Detailed, L3 Architecture, L4 Risk/Impact
    # -------------------------------------------------------------------------

    def test_progressive_levels_expand_dont_restate(self) -> None:
        """Higher progressive levels inject lower-level output with 'expand, don't restate'."""
        diff = "--- a/core.py\n+++ b/core.py\n@@ -10,3 +10,4 @@\n def run():\n+    acquire_lock()\n"

        # L1 prompt
        sys1, user1 = build_progressive_prompt(ProgressiveLevel.L1, diff)
        self.assertIn("L1 Quick", sys1)
        self.assertNotIn(EXPAND_INSTRUCTION, sys1)

        # L2 prompt with L1 output
        l1_res = {"intent": "Added locking to core execution", "summary": "Lock added"}
        sys2, user2 = build_progressive_prompt(ProgressiveLevel.L2, diff, lower_level_output=l1_res)
        self.assertIn(EXPAND_INSTRUCTION, sys2)
        self.assertIn("Added locking to core execution", user2)
        self.assertIn("L2 Detailed", sys2)

        # L3 Architecture with L2 output
        l2_res = {"functional_mechanics": "Mutex acquired before processing"}
        sys3, user3 = build_progressive_prompt(ProgressiveLevel.L3, diff, lower_level_output=l2_res)
        self.assertIn(EXPAND_INSTRUCTION, sys3)
        self.assertIn("L3 Architecture", sys3)

        # L4 Risk/Impact with L3 output
        l3_res = {"architecture_impact": "Lock creates potential deadlock hazard"}
        sys4, user4 = build_progressive_prompt(ProgressiveLevel.L4, diff, lower_level_output=l3_res)
        self.assertIn(EXPAND_INSTRUCTION, sys4)
        self.assertIn("L4 Risk", sys4)

    def test_progressive_analyses_separate_stored_records(self) -> None:
        """L1, L2, L3, L4 are stored as separate analyses with parent links."""
        # Store L1
        k1 = compute_analysis_cache_key("burst", "10", "L1", "ih", "m", "p", "v1", "v1", "c1")
        id1 = self.db.record_analysis({
            "cache_key": k1, "project_slug": "proj", "target_kind": "burst",
            "target_id": "10", "profile": "L1", "level": "L1", "input_hash": "ih",
            "model": "m", "provider": "p", "prompt_version": "v1", "sanitizer_version": "v1",
            "context_hash": "c1", "status": AnalysisStatus.DONE, "result_json": {"summary": "L1 quick"},
        })

        # Store L2 linking to L1
        k2 = compute_analysis_cache_key("burst", "10", "L2", "ih", "m", "p", "v1", "v1", "c2")
        id2 = self.db.record_analysis({
            "cache_key": k2, "project_slug": "proj", "target_kind": "burst",
            "target_id": "10", "profile": "L2", "level": "L2", "input_hash": "ih",
            "model": "m", "provider": "p", "prompt_version": "v1", "sanitizer_version": "v1",
            "context_hash": "c2", "parent_analysis_id": id1, "status": AnalysisStatus.DONE,
            "result_json": {"details": "L2 mechanics"},
        })

        rec1 = self.db.get_analysis(id1)
        rec2 = self.db.get_analysis(id2)
        self.assertEqual(rec1["level"], "L1")
        self.assertEqual(rec2["level"], "L2")
        self.assertEqual(rec2["parent_analysis_id"], id1)

        # Query all analyses for target
        target_analyses = self.db.get_analyses_for_target("burst", "10")
        self.assertEqual(len(target_analyses), 2)

    # -------------------------------------------------------------------------
    # 7. Bounded Provider Pools
    # -------------------------------------------------------------------------

    def test_bounded_provider_pool_limits(self) -> None:
        """BoundedProviderPool enforces max concurrency per provider."""
        pool = BoundedProviderPool({"ollama": 1})
        self.assertEqual(pool.get_capacity("ollama"), 1)

        with pool.acquire("ollama"):
            self.assertEqual(pool.get_active_count("ollama"), 1)
            # A second concurrent acquire must time out
            with self.assertRaises(TimeoutError):
                with pool.acquire("ollama", timeout=0.1):
                    pass

        self.assertEqual(pool.get_active_count("ollama"), 0)

    # -------------------------------------------------------------------------
    # 8. Retry Rules
    # -------------------------------------------------------------------------

    def test_retry_rules_classification_and_decisions(self) -> None:
        """Verify strict retry rules: 429/503 backoff, auth fail, JSON repair, timeout smaller context."""
        # 1. 429/503 -> retry with backoff
        p_429 = evaluate_retry_plan("RateLimitError", retry_count=0, max_retries=3)
        self.assertTrue(p_429.should_retry)
        self.assertEqual(p_429.action, "backoff")
        self.assertGreater(p_429.delay_s, 0.0)

        # 2. Auth errors -> NO automatic retry
        p_auth = evaluate_retry_plan("AuthenticationError", retry_count=0)
        self.assertFalse(p_auth.should_retry)
        self.assertEqual(p_auth.action, "fail")

        # 3. JSON parse error -> exactly ONE repair retry
        p_json1 = evaluate_retry_plan("JSONParseError", retry_count=0)
        self.assertTrue(p_json1.should_retry)
        self.assertEqual(p_json1.action, "repair_json")
        p_json2 = evaluate_retry_plan("JSONParseError", retry_count=1)
        self.assertFalse(p_json2.should_retry)

        # 4. Timeout -> exactly ONE smaller-context retry
        p_to1 = evaluate_retry_plan("TimeoutError", retry_count=0)
        self.assertTrue(p_to1.should_retry)
        self.assertEqual(p_to1.action, "smaller_context")
        p_to2 = evaluate_retry_plan("TimeoutError", retry_count=1)
        self.assertFalse(p_to2.should_retry)

    def test_manual_retry_action_for_failed_job(self) -> None:
        """Permanent failure transitions to failed state, but manual Retry action re-queues it."""
        jid = self.executor.enqueue_analysis("proj", "burst", "burst_99", "diff")
        self.db.fail_job(jid, "PermanentError", "Model not reachable", retry_count=3)

        job = self.db.get_job(jid)
        self.assertEqual(job["status"], AnalysisStatus.FAILED)
        self.assertEqual(job["retry_count"], 3)

        # Trigger Retry action
        retried = self.db.retry_job(jid)
        self.assertTrue(retried)

        updated_job = self.db.get_job(jid)
        self.assertEqual(updated_job["status"], AnalysisStatus.QUEUED)
        self.assertEqual(updated_job["retry_count"], 0)
        self.assertIsNone(updated_job["error_class"])

    # -------------------------------------------------------------------------
    # 9. Job Leases and Heartbeats: Killed Job Resumes
    # -------------------------------------------------------------------------

    def test_killed_controller_job_resumes_after_lease_expiry(self) -> None:
        """When a controller is killed mid-job, the expired lease is reclaimed and resumes."""
        jid = self.executor.enqueue_analysis("proj", "burst", "burst_crash", "diff")

        # Worker 1 claims job with a 0.2s short lease
        claimed = self.db.claim_job("killed_worker_pid_999", lease_duration_s=0.2)
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed["id"], jid)
        self.assertEqual(claimed["status"], AnalysisStatus.RUNNING)
        self.assertEqual(claimed["lease_owner"], "killed_worker_pid_999")

        # Wait for lease to expire (simulating controller process crash/kill)
        time.sleep(0.3)

        # Reclaim expired leases
        reclaimed_count = self.db.reclaim_expired_leases()
        self.assertEqual(reclaimed_count, 1)

        job_reclaimed = self.db.get_job(jid)
        self.assertEqual(job_reclaimed["status"], AnalysisStatus.QUEUED)
        self.assertIsNone(job_reclaimed["lease_owner"])

        # New worker claims and executes the resumed job
        worker2_claimed = self.db.claim_job("new_worker_pid_1000", lease_duration_s=5.0)
        self.assertIsNotNone(worker2_claimed)
        self.assertEqual(worker2_claimed["id"], jid)
        self.assertEqual(worker2_claimed["lease_owner"], "new_worker_pid_1000")

    # -------------------------------------------------------------------------
    # 10. HTTP Controller API Integration
    # -------------------------------------------------------------------------

    def test_http_api_endpoints_enqueue_get_retry(self) -> None:
        """Verify HTTP REST endpoints for AI jobs, analyses, and manual retry action."""
        import http.client
        from scripts.web_server import EngineWebServer

        server = EngineWebServer(
            engine_root=self.temp_dir,
            host="127.0.0.1",
            port=0,
            web_dir=self.temp_dir / "web",
            require_token=False,
        )
        port = server.start(background=True)
        try:
            # 1. POST /api/ai/jobs/enqueue
            conn = http.client.HTTPConnection("127.0.0.1", port)
            payload = json.dumps({
                "project_slug": "http_test_proj",
                "target_kind": "burst",
                "target_id": "42",
                "diff_text": "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-1\n+2",
                "level": "L1",
            })
            conn.request("POST", "/api/ai/jobs/enqueue", body=payload, headers={"Content-Type": "application/json"})
            res = conn.getresponse()
            self.assertEqual(res.status, 200)
            data = json.loads(res.read().decode("utf-8"))
            self.assertTrue(data["success"])
            job_id = data["job_id"]

            # 2. GET /api/ai/jobs/{id}
            conn.request("GET", f"/api/ai/jobs/{job_id}")
            res2 = conn.getresponse()
            self.assertEqual(res2.status, 200)
            data2 = json.loads(res2.read().decode("utf-8"))
            self.assertEqual(data2["job"]["id"], job_id)

            # 3. Simulate failure then POST /api/ai/jobs/{id}/retry
            server.ai_db.fail_job(job_id, "RateLimitError", "Simulated quota hit", 3)
            conn.request("POST", f"/api/ai/jobs/{job_id}/retry")
            res3 = conn.getresponse()
            self.assertEqual(res3.status, 200)
            data3 = json.loads(res3.read().decode("utf-8"))
            self.assertTrue(data3["success"])
            self.assertEqual(data3["status"], "queued")

            conn.close()
        finally:
            server.shutdown()

    # -------------------------------------------------------------------------
    # 11. End-to-End Progressive Chain Execution
    # -------------------------------------------------------------------------

    def test_progressive_levels_chain_execution(self) -> None:
        """Controller executes L1 -> L2 -> L3 -> L4 forming an authoritative progressive chain."""
        diff = "--- a/api.py\n+++ b/api.py\n@@ -1,3 +1,4 @@\n-def old(): pass\n+def new(): pass\n"

        # 1. Enqueue L1
        j1 = self.executor.enqueue_analysis("chain_proj", "burst", "chain_1", diff, level=ProgressiveLevel.L1, provider="heuristic")
        job1 = self.db.claim_job(self.executor.worker_id)
        self.executor._process_single_job(job1)
        res1 = self.db.get_job(j1)
        self.assertEqual(res1["status"], AnalysisStatus.DONE)
        ana1 = self.db.get_analysis(res1["analysis_id"])
        self.assertEqual(ana1["level"], "L1")
        self.assertIsNone(ana1["parent_analysis_id"])

        # 2. Enqueue L2
        j2 = self.executor.enqueue_analysis("chain_proj", "burst", "chain_1", diff, level=ProgressiveLevel.L2, provider="heuristic")
        job2 = self.db.claim_job(self.executor.worker_id)
        self.executor._process_single_job(job2)
        res2 = self.db.get_job(j2)
        self.assertEqual(res2["status"], AnalysisStatus.DONE)
        ana2 = self.db.get_analysis(res2["analysis_id"])
        self.assertEqual(ana2["level"], "L2")
        self.assertEqual(ana2["parent_analysis_id"], ana1["id"])

        # 3. Enqueue L3
        j3 = self.executor.enqueue_analysis("chain_proj", "burst", "chain_1", diff, level=ProgressiveLevel.L3, provider="heuristic")
        job3 = self.db.claim_job(self.executor.worker_id)
        self.executor._process_single_job(job3)
        res3 = self.db.get_job(j3)
        self.assertEqual(res3["status"], AnalysisStatus.DONE)
        ana3 = self.db.get_analysis(res3["analysis_id"])
        self.assertEqual(ana3["level"], "L3")
        self.assertEqual(ana3["parent_analysis_id"], ana2["id"])

        # 4. Enqueue L4
        j4 = self.executor.enqueue_analysis("chain_proj", "burst", "chain_1", diff, level=ProgressiveLevel.L4, provider="heuristic")
        job4 = self.db.claim_job(self.executor.worker_id)
        self.executor._process_single_job(job4)
        res4 = self.db.get_job(j4)
        self.assertEqual(res4["status"], AnalysisStatus.DONE)
        ana4 = self.db.get_analysis(res4["analysis_id"])
        self.assertEqual(ana4["level"], "L4")
        self.assertEqual(ana4["parent_analysis_id"], ana3["id"])

        # Verify all 4 are distinct stored records in ai_analyses
        all_target_analyses = self.db.get_analyses_for_target("burst", "chain_1")
        self.assertEqual(len(all_target_analyses), 4)
        levels_present = [a["level"] for a in all_target_analyses]
        self.assertEqual(levels_present, ["L1", "L2", "L3", "L4"])

    # -------------------------------------------------------------------------
    # 12. Executor Retry Execution on Specific Errors
    # -------------------------------------------------------------------------

    def test_executor_auth_error_no_auto_retry(self) -> None:
        """Authentication error immediately marks job as failed with zero auto-retries."""
        jid = self.executor.enqueue_analysis("proj", "burst", "b_auth", "diff", provider="gemini")
        job = self.db.claim_job(self.executor.worker_id)

        with patch.object(self.executor, "_call_provider", side_effect=AuthenticationError("401 Unauthorized")):
            self.executor._process_single_job(job)

        updated = self.db.get_job(jid)
        self.assertEqual(updated["status"], AnalysisStatus.FAILED)
        self.assertEqual(updated["error_class"], "AuthenticationError")
        self.assertEqual(updated["retry_count"], 1)

    def test_executor_rate_limit_requeues_with_backoff(self) -> None:
        """429 RateLimitError increments retry count and requeues for backoff."""
        jid = self.executor.enqueue_analysis("proj", "burst", "b_rate", "diff", provider="gemini")
        job = self.db.claim_job(self.executor.worker_id)

        with patch("time.sleep"):  # don't actually sleep in unit tests
            with patch.object(self.executor, "_call_provider", side_effect=RateLimitError("429 Resource exhausted")):
                self.executor._process_single_job(job)

        updated = self.db.get_job(jid)
        self.assertEqual(updated["status"], AnalysisStatus.QUEUED)
        self.assertEqual(updated["error_class"], "RateLimitError")
        self.assertEqual(updated["retry_count"], 1)

    def test_executor_timeout_halves_diff_context(self) -> None:
        """TimeoutError triggers smaller-context retry (halves diff_text in payload)."""
        long_diff = "A" * 1000
        jid = self.executor.enqueue_analysis("proj", "burst", "b_to", long_diff, provider="gemini")
        job = self.db.claim_job(self.executor.worker_id)

        with patch("time.sleep"):
            with patch.object(self.executor, "_call_provider", side_effect=TimeoutError("Request timed out")):
                self.executor._process_single_job(job)

        updated = self.db.get_job(jid)
        self.assertEqual(updated["status"], AnalysisStatus.QUEUED)
        self.assertEqual(updated["error_class"], "TimeoutError")
        payload = json.loads(updated["payload_json"])
        self.assertEqual(len(payload["diff_text"]), 500)


if __name__ == "__main__":
    unittest.main()
