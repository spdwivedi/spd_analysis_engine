"""
tests/test_provider_profiles_and_sanitizer.py
=============================================
Comprehensive unit tests for provider execution profiles, secret sanitizer,
Gemini header authentication, Ollama GPU runtime probing, cache key invalidation,
and per-project cloud consent enforcement.
"""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.ai.controller_db import EngineDB
from scripts.ai.controller_executor import ControllerAIExecutor
from scripts.ai.error_handling import (
    AuthenticationError,
    ConsentRequiredError,
    RateLimitError,
    TimeoutError,
    classify_ai_exception,
    evaluate_retry_plan,
)
from scripts.ai.identity import (
    DEFAULT_SANITIZER_VERSION,
    PROMPT_VERSION,
    AnalysisIdentity,
    AnalysisStatus,
    compute_analysis_cache_key,
    compute_content_hash,
)
from scripts.ai.profiles import (
    ExecutionProfile,
    ProfileConfig,
    get_profile_config,
)
from scripts.ai.provider_gemini import call_gemini, validate_gemini_model
from scripts.ai.provider_ollama import call_ollama, probe_ollama_runtime, test_ollama_connection
from scripts.ai.provider_pool import BoundedProviderPool
from scripts.ai.rate_limiter import RateLimitManager
from scripts.ai.sanitizer import (
    SANITIZER_VERSION,
    is_sensitive_file,
    redact_sensitive_text,
    sanitize_context_dict,
    sanitize_diff_content,
)
from scripts.storage.project_storage import write_project_config


class TestProviderProfilesAndSanitizer(unittest.TestCase):
    """Test suite for execution profiles, secret sanitization, and cloud consent."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_profiles_test_"))
        self.db_path = self.temp_dir / "data" / "engine.db"
        self.db = EngineDB(self.db_path)
        self.pool = BoundedProviderPool({"gemini": 2, "ollama": 1, "heuristic": 3})
        self.executor = ControllerAIExecutor(
            engine_root=self.temp_dir,
            db=self.db,
            provider_pool=self.pool,
            worker_id="test_profiles_worker",
            lease_duration_s=2.0,
            heartbeat_interval_s=0.5,
        )
        write_project_config(self.temp_dir, "consented_proj", {"cloud_ai_enabled": True})
        write_project_config(self.temp_dir, "unconsented_proj", {"cloud_ai_enabled": False})

    def tearDown(self) -> None:
        self.executor.stop(timeout=2.0)

    # -------------------------------------------------------------------------
    # 1. Secret Fixture Never Reaches Mocked Provider
    # -------------------------------------------------------------------------

    def test_secret_fixture_never_reaches_mocked_provider(self) -> None:
        """Verify all secret patterns and sensitive files are redacted before provider call."""
        mock_slack_token = "xoxb-" + "1234567890-123456789012-abcdefghijklmnopqrstuv"
        raw_diff = (
            "--- a/config.py\n"
            "+++ b/config.py\n"
            "@@ -1,5 +1,10 @@\n"
            "+aws_key = 'AKIAIOSFODNN7EXAMPLE'\n"
            "+github_pat = 'ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'\n"
            "+google_key = 'AIzaSyD-1234567890abcdefghijklmnopqrstuv'\n"
            "+openai_key = 'sk-1234567890abcdefghijklmnopqrstuvwx'\n"
            f"+slack_token = '{mock_slack_token}'\n"
            "+jwt = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.doNotLeakThisSignature123'\n"
            "+password = 'SuperSecretPassword123!'\n"
            "+secret = 'my_top_secret_token'\n"
            "+token = 'auth_token_value_99'\n"
            "+api_key = 'custom_secret_key_val'\n"
            "+url = 'https://admin:myPassword123@internal.corp.net/api'\n"
            "+pem = '-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEAz9v...\n-----END RSA PRIVATE KEY-----'\n"
        )
        raw_context = {
            "meta": "token='context_secret_123'",
            "nested": {"key": "password='nested_pass_456'"},
        }

        captured_calls = []

        def mock_call(provider, model, sys_instruction, prompt):
            captured_calls.append({"sys": sys_instruction, "prompt": prompt})
            return {"intent": "clean", "summary": "Sanitized change"}, 10, 10

        jid = self.executor.enqueue_analysis(
            project_slug="consented_proj",
            target_kind="burst",
            target_id="b_secrets",
            diff_text=raw_diff,
            context=raw_context,
            provider="gemini",
        )
        job = self.db.claim_job(self.executor.worker_id)
        with patch.object(self.executor, "_call_provider", side_effect=mock_call):
            self.executor._process_single_job(job)

        self.assertEqual(len(captured_calls), 1)
        full_received = f"{captured_calls[0]['sys']}\n{captured_calls[0]['prompt']}"

        # Assert zero raw secret leaks
        secrets_to_check = [
            "AKIAIOSFODNN7EXAMPLE",
            "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
            "AIzaSyD-1234567890abcdefghijklmnopqrstuv",
            "sk-1234567890abcdefghijklmnopqrstuvwx",
            mock_slack_token,
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
            "SuperSecretPassword123!",
            "my_top_secret_token",
            "auth_token_value_99",
            "custom_secret_key_val",
            "myPassword123@",
            "-----BEGIN RSA PRIVATE KEY-----",
            "context_secret_123",
            "nested_pass_456",
        ]
        for secret in secrets_to_check:
            self.assertNotIn(secret, full_received, f"Raw secret leaked to provider: {secret}")

        # Assert redaction placeholders are present
        self.assertIn("[REDACTED_AWS_KEY]", full_received)
        self.assertIn("[REDACTED_GITHUB_TOKEN]", full_received)
        self.assertIn("[REDACTED_GOOGLE_API_KEY]", full_received)
        self.assertIn("[REDACTED_OPENAI_KEY]", full_received)
        self.assertIn("[REDACTED_SLACK_TOKEN]", full_received)
        self.assertIn("[REDACTED_JWT]", full_received)
        self.assertIn("[REDACTED_PEM_PRIVATE_KEY]", full_received)
        self.assertIn("[REDACTED_USER]:[REDACTED_PASSWORD]@", full_received)

    def test_sensitive_file_replaced_with_redacted_marker(self) -> None:
        """Sensitive files such as .env or id_rsa are replaced with [REDACTED FILE: <path>]."""
        env_diff = "--- a/.env\n+++ b/.env\n@@ -0,0 +1 @@\n+SECRET=xyz\n"
        sanitized = sanitize_diff_content(env_diff, file_path=".env")
        self.assertEqual(sanitized, "[REDACTED FILE: .env]")

        rsa_diff = "--- a/id_rsa\n+++ b/id_rsa\n@@ -0,0 +1 @@\n+key_data\n"
        sanitized_rsa = sanitize_diff_content(rsa_diff, file_path="id_rsa")
        self.assertEqual(sanitized_rsa, "[REDACTED FILE: id_rsa]")

    def test_sensitive_file_diff_redacted_and_unversioned(self) -> None:
        """Verify sensitive files (.env, *.pem) in _handle_event_bundle are redacted and excluded from baseline and staging."""
        from scripts.core.worker_bundle import _handle_event_bundle
        from scripts.core.worker_lifecycle import WorkerState
        from scripts.storage.ignore_policy import IgnorePolicy

        ws = self.temp_dir / "test_ws"
        ws.mkdir(parents=True, exist_ok=True)
        (ws / ".env").write_text("DB_PASSWORD=secret123\n", encoding="utf-8")
        (ws / "main.py").write_text("print('hello')\n", encoding="utf-8")

        mock_db = MagicMock()
        mock_db.record_event.return_value = 1
        recorded_patches = {}
        mock_db.record_patch.side_effect = lambda eid, rel, diff: recorded_patches.update({rel: diff})

        mock_diff_calc = MagicMock()
        mock_diff_calc.compute_diff.return_value = "@@ -0,0 +1 @@\n+print('hello')\n"
        updated_baselines = []
        mock_diff_calc.update_baseline.side_effect = lambda rel, content: updated_baselines.append(rel)

        mock_shadow = MagicMock()
        state = WorkerState(
            project_name="test_ws",
            target_path_str=str(ws),
            session_id=1,
            session_db=mock_db,
            diff_calc=mock_diff_calc,
            shadow_git=mock_shadow,
            patches_dir=self.temp_dir / "patches",
        )
        setattr(state, "ignore_policy", IgnorePolicy(ws))

        bundle = {
            "files_touched": [".env", "main.py"],
            "first_file": ".env",
            "duration_s": 0.1,
            "effective_changes": [".env", "main.py"],
        }
        _handle_event_bundle(bundle, state)

        self.assertIn(".env", recorded_patches)
        self.assertIn("[REDACTED SENSITIVE FILE]", recorded_patches[".env"])
        self.assertNotIn(".env", updated_baselines)
        self.assertIn("main.py", updated_baselines)

        committed_cs = mock_shadow.commit_changeset.call_args[1]["changeset"]
        self.assertNotIn(".env", committed_cs.files)
        self.assertIn("main.py", committed_cs.files)


    # -------------------------------------------------------------------------
    # 2. Model Mismatch (No Silent Substitution)
    # -------------------------------------------------------------------------

    def test_model_mismatch(self) -> None:
        """Verify validate_gemini_model rejects invalid names without silent substitution."""
        # Valid names
        validate_gemini_model("gemini-2.5-flash-lite")
        validate_gemini_model("gemini-3.1-flash-lite")
        validate_gemini_model("gemini-pro")

        for invalid in ("gpt-4o", "claude-3-opus", "gemini/model?query=1", ""):
            with self.assertRaises(ValueError):
                validate_gemini_model(invalid)

    # -------------------------------------------------------------------------
    # 3. Rate Limit (Shared Limiter & Cooldown)
    # -------------------------------------------------------------------------

    def test_rate_limit(self) -> None:
        """Verify RateLimitManager enforces sliding window and triggers cooldown on 429."""
        rl = RateLimitManager(max_rpm=3)
        self.assertFalse(rl.get_status()["cooling_down"])

        # Fill quota
        rl.record_request()
        rl.record_request()
        rl.record_request()
        status = rl.get_status()
        self.assertTrue(status["cooling_down"])
        self.assertEqual(status["current_rpm"], 3)

        # Trigger explicit cooldown
        rl.trigger_cooldown(30.0)
        self.assertTrue(rl.get_status()["cooling_down"])
        self.assertGreater(rl.get_status()["cooldown_remaining_s"], 20.0)

        # Verify retry plan for RateLimitError
        plan = evaluate_retry_plan("RateLimitError", retry_count=0, max_retries=3)
        self.assertTrue(plan.should_retry)
        self.assertEqual(plan.action, "backoff")
        self.assertGreater(plan.delay_s, 0.0)

    # -------------------------------------------------------------------------
    # 4. Timeout (Classification & Smaller Context Retry)
    # -------------------------------------------------------------------------

    def test_timeout(self) -> None:
        """Verify TimeoutError classifies properly and triggers smaller-context retry."""
        err_name, typed_err = classify_ai_exception(TimeoutError("Socket operation timed out"))
        self.assertEqual(err_name, "TimeoutError")
        self.assertIsInstance(typed_err, TimeoutError)

        plan = evaluate_retry_plan("TimeoutError", retry_count=0)
        self.assertTrue(plan.should_retry)
        self.assertEqual(plan.action, "smaller_context")

        # Second timeout failure does not retry further
        plan2 = evaluate_retry_plan("TimeoutError", retry_count=1)
        self.assertFalse(plan2.should_retry)
        self.assertEqual(plan2.action, "fail")

    # -------------------------------------------------------------------------
    # 5. GPU Profile
    # -------------------------------------------------------------------------

    def test_gpu_profile(self) -> None:
        """Verify GPU_AI profile applies full GPU offloading, 8192 context, and 30m keep-alive."""
        cfg = get_profile_config(ExecutionProfile.GPU_AI)
        self.assertEqual(cfg.name, ExecutionProfile.GPU_AI)
        self.assertEqual(cfg.ollama_num_gpu, -1)
        self.assertEqual(cfg.ollama_num_ctx, 8192)
        self.assertEqual(cfg.ollama_keep_alive, "30m")
        self.assertEqual(cfg.timeout_s, 45.0)
        self.assertEqual(cfg.polling_interval_s, 1.0)
        self.assertEqual(cfg.ollama_concurrency, 2)

        pool = BoundedProviderPool()
        pool.apply_profile(cfg)
        self.assertEqual(pool.get_capacity("ollama"), 2)
        self.assertEqual(pool.get_capacity("gemini"), 3)

    # -------------------------------------------------------------------------
    # 6. CPU Profile (ECO)
    # -------------------------------------------------------------------------

    def test_cpu_profile(self) -> None:
        """Verify ECO profile enforces 0 GPU layers, 2048 context, and throttled concurrency."""
        cfg = get_profile_config(ExecutionProfile.ECO)
        self.assertEqual(cfg.name, ExecutionProfile.ECO)
        self.assertEqual(cfg.ollama_num_gpu, 0)
        self.assertEqual(cfg.ollama_num_ctx, 2048)
        self.assertEqual(cfg.ollama_keep_alive, "1m")
        self.assertEqual(cfg.timeout_s, 15.0)
        self.assertEqual(cfg.polling_interval_s, 4.0)
        self.assertEqual(cfg.ollama_concurrency, 1)

        pool = BoundedProviderPool()
        pool.apply_profile(cfg)
        self.assertEqual(pool.get_capacity("ollama"), 1)
        self.assertEqual(pool.get_capacity("gemini"), 1)

    # -------------------------------------------------------------------------
    # 7. Cache Invalidation After Prompt Version Change
    # -------------------------------------------------------------------------

    def test_cache_invalidation_after_prompt_version_change(self) -> None:
        """AI cache key changes when prompt_version changes."""
        key_v1 = compute_analysis_cache_key(
            "burst", "b1", "l1_analysis", "hash_a", "gemini-3.1-flash-lite", "gemini", "v1.0", "v2.0", "ctx_a"
        )
        key_v2 = compute_analysis_cache_key(
            "burst", "b1", "l1_analysis", "hash_a", "gemini-3.1-flash-lite", "gemini", "v1.1", "v2.0", "ctx_a"
        )
        self.assertNotEqual(key_v1, key_v2)

    # -------------------------------------------------------------------------
    # 8. Cache Invalidation After Sanitizer Version Change
    # -------------------------------------------------------------------------

    def test_cache_invalidation_after_sanitizer_version_change(self) -> None:
        """AI cache key changes when sanitizer_version changes."""
        key_san_v2 = compute_analysis_cache_key(
            "burst", "b1", "l1_analysis", "hash_a", "gemini-3.1-flash-lite", "gemini", "v1.0", "v2.0", "ctx_a"
        )
        key_san_v3 = compute_analysis_cache_key(
            "burst", "b1", "l1_analysis", "hash_a", "gemini-3.1-flash-lite", "gemini", "v1.0", "v3.0", "ctx_a"
        )

        self.assertNotEqual(key_san_v2, key_san_v3)

    # -------------------------------------------------------------------------
    # 9. Per-Project Cloud Consent Gate
    # -------------------------------------------------------------------------

    def test_per_project_cloud_consent_gate(self) -> None:
        """Cloud calls fail immediately when project has not granted cloud consent."""
        jid = self.executor.enqueue_analysis(
            project_slug="unconsented_proj",
            target_kind="burst",
            target_id="b_unconsented",
            diff_text="diff",
            provider="gemini",
        )
        job = self.db.claim_job(self.executor.worker_id)
        self.executor._process_single_job(job)

        updated = self.db.get_job(jid)
        self.assertEqual(updated["status"], AnalysisStatus.FAILED)
        self.assertEqual(updated["error_class"], "ConsentRequiredError")

        # Consented project succeeds
        jid_ok = self.executor.enqueue_analysis("consented_proj", "burst", "b_consented", "diff", provider="gemini")
        job_ok = self.db.claim_job(self.executor.worker_id)
        with patch.object(self.executor, "_call_provider", return_value=({"intent": "ok"}, 10, 10)):
            self.executor._process_single_job(job_ok)
        updated_ok = self.db.get_job(jid_ok)
        self.assertEqual(updated_ok["status"], AnalysisStatus.DONE)

        # Legacy current/ tier with cloud_ai_enabled also resolves consent
        curr_dir = self.temp_dir / "current" / "tier_consented_proj"
        curr_dir.mkdir(parents=True, exist_ok=True)
        (curr_dir / "project.config.json").write_text(json.dumps({"cloud_ai_enabled": True}), encoding="utf-8")
        jid_tier = self.executor.enqueue_analysis("tier_consented_proj", "burst", "b_tier", "diff", provider="gemini")
        job_tier = self.db.claim_job(self.executor.worker_id)
        with patch.object(self.executor, "_call_provider", return_value=({"intent": "ok"}, 10, 10)):
            self.executor._process_single_job(job_tier)
        self.assertEqual(self.db.get_job(jid_tier)["status"], AnalysisStatus.DONE)

    def test_offline_profile_blocks_cloud_ai_regardless_of_project(self) -> None:
        """OFFLINE profile strictly blocks cloud calls even if project granted consent."""
        self.executor.set_execution_profile(ExecutionProfile.OFFLINE)
        jid = self.executor.enqueue_analysis(
            project_slug="consented_proj",
            target_kind="burst",
            target_id="b_offline_cloud",
            diff_text="diff",
            provider="gemini",
        )
        job = self.db.claim_job(self.executor.worker_id)
        self.executor._process_single_job(job)

        updated = self.db.get_job(jid)
        self.assertEqual(updated["status"], AnalysisStatus.FAILED)
        self.assertEqual(updated["error_class"], "ConsentRequiredError")

    # -------------------------------------------------------------------------
    # 10. Ollama Runtime GPU Probing
    # -------------------------------------------------------------------------

    def test_ollama_runtime_gpu_probe(self) -> None:
        """Verify probe_ollama_runtime extracts loaded_on_gpu and size_vram."""
        fake_ps = {
            "models": [
                {
                    "name": "qwen2.5-coder:7b",
                    "size_vram": 4294967296,
                    "size": 4294967296,
                    "details": {"format": "gguf"},
                }
            ]
        }
        with patch("urllib.request.urlopen") as mock_url:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(fake_ps).encode("utf-8")
            mock_resp.__enter__.return_value = mock_resp
            mock_url.return_value = mock_resp

            probe = probe_ollama_runtime(model="qwen2.5-coder:7b")
            self.assertTrue(probe["running"])
            self.assertTrue(probe["loaded_on_gpu"])
            self.assertEqual(probe["size_vram_bytes"], 4294967296)

    # -------------------------------------------------------------------------
    # 11. Gemini Header-Only Auth (Never URL Query Parameter)
    # -------------------------------------------------------------------------

    def test_gemini_header_auth_no_url_parameter(self) -> None:
        """Verify Gemini REST fallback transmits API key in header, never in URL."""
        sent_requests = []

        def mock_urlopen(req, timeout=30):
            sent_requests.append(req)
            resp = MagicMock()
            resp.read.return_value = json.dumps({
                "candidates": [{"content": {"parts": [{"text": '{"intent": "test"}'}]}}]
            }).encode("utf-8")
            resp.__enter__.return_value = resp
            return resp

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            # Block google.genai SDK import to test REST fallback
            with patch.dict("sys.modules", {"google": None, "google.genai": None}):
                cfg = {"api_key": "AIzaTestKey_1234567890abcdefghijklmnopqrst", "default_model": "gemini-2.5-flash-lite"}
                res = call_gemini("test prompt", cfg, rate_limiter=None, parse_json_func=json.loads, fast_fail=True)

        self.assertEqual(len(sent_requests), 1)
        req = sent_requests[0]
        # Header must contain key
        self.assertEqual(req.headers.get("X-goog-api-key"), "AIzaTestKey_1234567890abcdefghijklmnopqrst")
        # URL must never contain key
        self.assertNotIn("?key=", req.full_url)
        self.assertNotIn("AIzaTestKey", req.full_url)

    # -------------------------------------------------------------------------
    # 12. Execution Profile Does Not Alter Capture Durability
    # -------------------------------------------------------------------------

    def test_execution_profile_does_not_alter_capture_durability(self) -> None:
        """Switching execution profiles never alters SQLite WAL pragmas or capture durability."""
        from scripts.storage.writer import ProjectWriter

        db_path = self.temp_dir / "data" / "telemetry.db"
        writer = ProjectWriter(db_path, batch_window_s=0.05)
        try:
            # Query writer SQLite connection pragmas under NORMAL profile
            self.executor.set_execution_profile(ExecutionProfile.NORMAL)
            with writer.open_ro_connection() as conn:
                journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
                sync_mode = conn.execute("PRAGMA synchronous;").fetchone()[0]
            self.assertEqual(journal_mode.lower(), "wal")

            # Switch through all profiles and verify journal_mode and sync_mode remain invariant
            for prof in (ExecutionProfile.ECO, ExecutionProfile.GPU_AI, ExecutionProfile.OFFLINE):
                self.executor.set_execution_profile(prof)
                with writer.open_ro_connection() as conn:
                    j_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
                    s_mode = conn.execute("PRAGMA synchronous;").fetchone()[0]
                self.assertEqual(j_mode.lower(), "wal")
                self.assertEqual(s_mode, sync_mode)
        finally:
            writer.stop()


if __name__ == "__main__":
    unittest.main()
